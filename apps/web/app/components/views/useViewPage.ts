import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import {
  DEFAULT_DISPLAY_PROPS,
  mergeTrees,
  type FilterCondition,
  type FilterLogicNode,
  type IssueViewData,
  type ViewDisplayProps,
  type ViewLayout,
} from "@rp/shared-state";
import { IssueAPI, LabelAPI, ProjectAPI, ProjectMemberAPI, FieldAPI, UserSettingsAPI, ViewAPI, unwrap } from "../../services/api";
import { useStores } from "../../stores";
import { toast } from "../Toast";
import type { CustomFieldDef } from "../../services/api";
import { BUILTIN_GROUPBYS, GROUP_KEY_ALIAS, PRIORITY_COLUMNS } from "./view-dsl";

/** 列头配置源数据（BOARD-003 §3.2 / §2.3 五维分组语义表的「分组列来源」列）。 */
export interface BoardColumnMeta {
  key: string;
  name: string;
  color?: string | null | undefined;
  avatarName?: string | undefined;
  none?: boolean;
  /** cf_* 维度的 __none__ 允许拖入 = 删键（BR-14） */
  cfNone?: boolean;
}

export interface ViewStateRow {
  id: string;
  name: string;
  group: string;
  color: string;
}

export interface MemberRow {
  id: string;
  name: string;
}

export interface LabelRow {
  id: string;
  name: string;
  color: string;
  is_active?: boolean;
}

/** useViewPage：BOARD-003 §4.4 ViewStore + TASK-011 §4.4.1 FilterTreeStore 的 web 编排层。
 *  职责：视图列表/偏好/Schema 拉取与喂 store；?view_id 与 ?filters= URL 双向绑定
 *  （R2 裁决）；dirty 黄条判定；保存/另存/设默认/删除动作。 */
export function useViewPage(opts: { workspaceSlug?: string | undefined; projectId?: string | undefined; layout: "list" | "kanban" | "table" | "gantt" }) {
  const { workspaceSlug, projectId, layout } = opts;
  const stores = useStores();
  const viewStore = stores.views;
  const filterStore = stores.filterTree;
  const [sp, setSp] = useSearchParams();
  /** store 变更后的强制重渲染（页面不使用 observer HOC，动作后 bump 保证派生值重读）。 */
  const [rev, setRev] = useState(0);
  const bump = useCallback(() => setRev((n) => n + 1), []);

  // ── 配置源（列头渲染 / 字段选择器值域共用）──
  const [states, setStates] = useState<ViewStateRow[]>([]);
  const [members, setMembers] = useState<MemberRow[]>([]);
  const [labels, setLabels] = useState<LabelRow[]>([]);
  const [cfDefs, setCfDefs] = useState<CustomFieldDef[]>([]);
  const [meName, setMeName] = useState(stores.session.user?.display_name ?? "我");
  /** meta.degraded（分组字段停用回退提示，BOARD-003 §3.6）。 */
  const [degraded, setDegraded] = useState<string | null>(null);
  /** 视图已被删除/不可见（他人视图 404）→ 回退默认 + 黄条（§3.6 / BR-12）。 */
  const [viewGone, setViewGone] = useState<string | null>(null);
  const hydratedRef = useRef<string>("");

  const viewIdParam = sp.get("view_id");
  const filtersParam = sp.get("filters");

  // ── 拉取：视图 + 偏好 + Schema + 成员/标签/状态（进项目一次）──
  useEffect(() => {
    if (!workspaceSlug || !projectId) return;
    const key = `${workspaceSlug}/${projectId}/${stores.session.user?.id ?? "anon"}`;
    if (hydratedRef.current === key) return;
    hydratedRef.current = key;
    setDegraded(null);
    setViewGone(null);
    (async () => {
      viewStore.setLoading(true);
      const [viewsRes, settingsRes] = await Promise.all([
        ViewAPI.list(workspaceSlug, projectId).catch(() => null),
        UserSettingsAPI.get().catch(() => null),
      ]);
      const views = viewsRes ? (unwrap<IssueViewData[]>(viewsRes) ?? []) : [];
      viewStore.hydrate(projectId, views);
      const prefs = settingsRes ? (unwrap<Record<string, unknown>>(settingsRes) ?? {}) : {};
      const dv = (prefs["board.default_view_id"] ?? {}) as Record<string, string>;
      for (const [pid, vid] of Object.entries(dv)) viewStore.setDefault(pid, vid);
      viewStore.setLoading(false);
      bump();
      // URL 无 ?view_id 时直达默认视图（BR-10；无默认退「全部」）
      const spViewId = new URLSearchParams(window.location.search).get("view_id");
      if (!spViewId) {
        const def = viewStore.defaultViewByProject.get(projectId);
        if (def && views.some((v) => v.id === def)) {
          setSp((prev) => { const n = new URLSearchParams(prev); n.set("view_id", def); return n; }, { replace: true });
        }
      }
    })();
    ProjectAPI.states(workspaceSlug, projectId, { include_cancelled: "1" })
      .then((r) => setStates((unwrap<ViewStateRow[]>(r) ?? []).map((s) => ({ ...s, color: s.color || "#9ca3af" }))))
      .catch(() => {});
    ProjectMemberAPI.list(workspaceSlug, projectId, { per_page: 100 })
      .then((r) => setMembers((unwrap<Array<{ user: { id: string; display_name: string } }>>(r) ?? []).map((m) => ({ id: m.user.id, name: m.user.display_name }))))
      .catch(() => {});
    LabelAPI.list(workspaceSlug, projectId)
      .then((r) => setLabels((unwrap<LabelRow[]>(r) ?? []).filter((l) => l.is_active !== false).map((l) => ({ ...l }))))
      .catch(() => {});
    FieldAPI.schema(workspaceSlug, projectId)
      .then((r) => setCfDefs(unwrap<{ custom: CustomFieldDef[] }>(r)?.custom ?? []))
      .catch(() => setCfDefs([]));
    setMeName(stores.session.user?.display_name ?? "我");
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceSlug, projectId]);

  // ── ?view_id 双向绑定：URL → store（含默认视图解析后）──
  useEffect(() => {
    if (!projectId) return;
    const list = viewStore.views(projectId);
    if (viewIdParam && list.some((v) => v.id === viewIdParam)) {
      viewStore.setCurrent(viewIdParam);
      setViewGone(null);
    } else if (viewIdParam && list.length > 0) {
      // 他人视图 404 / 已删除（存在性隐藏，§2.6）→ 回退默认 + 黄条
      viewStore.setCurrent(null);
      setViewGone(viewIdParam);
      setSp((prev) => { const n = new URLSearchParams(prev); n.delete("view_id"); return n; }, { replace: true });
    } else {
      viewStore.setCurrent(null);
    }
    bump();
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [viewIdParam, projectId, viewStore.viewsByProject.get(projectId ?? "")?.length]);

  // ── ?filters= 反序列化（损坏 JSON → 静默回无条件 + Toast，TASK-011 §2.5）──
  useEffect(() => {
    if (!filtersParam) {
      filterStore.hydrateApplied(null);
      bump();
      return;
    }
    try {
      const tree = JSON.parse(filtersParam) as FilterLogicNode;
      if (tree && typeof tree === "object" && Array.isArray(tree.conditions)) {
        filterStore.hydrateApplied(tree);
      }
    } catch {
      toast("filters 参数不是合法 JSON，已回无条件态", "warning");
      setSp((prev) => { const n = new URLSearchParams(prev); n.delete("filters"); return n; }, { replace: true });
    }
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [filtersParam]);

  // oxlint-disable-next-line react-hooks/exhaustive-deps
  const views = useMemo(() => viewStore.views(projectId ?? ""), [projectId, viewStore.viewsByProject, rev]);
  const currentView = viewStore.currentView(projectId ?? "");
  const effDisplay: ViewDisplayProps = viewStore.effectiveDisplayOf(projectId ?? "");
  const dirty = viewStore.dirty;
  const filterApplied = filterStore.applied;

  /** 生效分组维度（别名归一 BR-05；cf 停用由 meta.degraded 提示）。 */
  const groupBy = useMemo(() => {
    const raw = effDisplay.group_by || "state_id";
    return GROUP_KEY_ALIAS[raw] ?? raw;
  }, [effDisplay.group_by]);

  const filtersQs = useMemo(
    () => (filterApplied ? JSON.stringify(filterApplied) : undefined),
    [filterApplied],
  );

  /** 列表/分组请求共用的查询参数（view_id 与 filters 三源恒 AND，BR-12）。 */
  const listParams = useCallback((extra: Record<string, unknown> = {}) => ({
    ...(viewIdParam ? { view_id: viewIdParam } : {}),
    ...(filtersQs ? { filters: filtersQs } : {}),
    ...extra,
  }), [viewIdParam, filtersQs]);

  /** 分组端点（BOARD-003 §4.2-6）。返回 [envelope, error]。 */
  async function fetchGrouped(dimension: string) {
    if (!workspaceSlug || !projectId) return null;
    try {
      const r = await IssueAPI.list(workspaceSlug, projectId, {
        ...listParams({ group_by: dimension, group_per_page: 100 }),
      });
      const env = (r as unknown as { data: unknown; meta: unknown }) as unknown as {
        data: Record<string, { results: never[]; total_results: number; unfiltered_total_results: number }>;
        meta: { grouped_by: string; total_count: number; degraded?: Record<string, string> | null };
      };
      setDegraded(env.meta?.degraded?.group_by ?? null);
      return env;
    } catch {
      return null;
    }
  }

  /** 平铺列表（list/table 布局与命中数预估共用）。 */
  async function fetchFlat(extra: Record<string, unknown> = {}) {
    if (!workspaceSlug || !projectId) return null;
    try {
      const r = await IssueAPI.list(workspaceSlug, projectId, listParams(extra));
      return r as unknown as { data: unknown[]; meta: { total_count: number } };
    } catch {
      return null;
    }
  }

  // ── 动作 ──
  function setUrlView(id: string | null) {
    setSp((prev) => {
      const n = new URLSearchParams(prev);
      if (id) n.set("view_id", id); else n.delete("view_id");
      return n;
    }, { preventScrollReset: true });
  }

  /** 切视图：清空临时层（TASK-011 §4.4.2 ViewTabs：选中即 viewId + 清空临时层）。 */
  function switchView(id: string | null) {
    filterStore.clear();
    bump();
    setSp((prev) => {
      const n = new URLSearchParams(prev);
      if (id) n.set("view_id", id); else n.delete("view_id");
      n.delete("filters");
      return n;
    }, { preventScrollReset: true });
  }

  /** [应用]：临时树入 URL ?filters=（TASK-011 §3.3：列表刷新 + URL 同步，替换不入栈）。 */
  function applyFilterTree() {
    filterStore.apply();
    const applied = filterStore.applied;
    bump();
    setSp((prev) => {
      const n = new URLSearchParams(prev);
      if (applied) n.set("filters", JSON.stringify(applied));
      else n.delete("filters");
      return n;
    }, { preventScrollReset: true, replace: true });
  }

  function clearTempFilters() {
    filterStore.clear();
    bump();
    setSp((prev) => { const n = new URLSearchParams(prev); n.delete("filters"); return n; }, { preventScrollReset: true });
  }

  /** 分组/显示/列等覆盖 → 黄条（BOARD-003 §3.1）。 */
  function patchDisplay(patch: Partial<ViewDisplayProps>) {
    viewStore.patchOverride({ displayProps: patch });
    bump();
  }

  function discardChanges() {
    viewStore.discard();
    bump();
  }

  /** 黄条 [保存]：就地 PATCH display_props（+layout 若切换过）；内置视图允许（BR-03，filters 锁定）。 */
  async function saveInPlace(): Promise<boolean> {
    if (!workspaceSlug || !projectId || !currentView) return false;
    const ov = viewStore.overrides.get(currentView.id);
    if (!ov) return false;
    const displayProps = { ...currentView.display_props, ...(ov.displayProps ?? {}) };
    const payload: { display_props: unknown; layout?: ViewLayout } = { display_props: displayProps };
    if (ov.layout) payload.layout = ov.layout;
    try {
      const r = await ViewAPI.patch(workspaceSlug, projectId, currentView.id, payload);
      const saved = unwrap<IssueViewData>(r);
      viewStore.markSaved(saved);
      bump();
      toast(currentView.is_system
        ? "内置视图：显示配置已保存（filters 锁定 · BR-03）"
        : "视图已更新（display_props）", "ok");
      return true;
    } catch (e) {
      toast(e instanceof Error ? e.message : "保存失败", "error");
      return false;
    }
  }

  /** M-SAVE [创建视图]：另存为 = 合并树（视图 filters AND 临时树，TASK-011 §4.4.1）。 */
  async function createView(input: { name: string; icon: string; layout: ViewLayout }): Promise<IssueViewData | null> {
    if (!workspaceSlug || !projectId) return null;
    const baseFilters = currentView?.filters as FilterLogicNode | undefined;
    const merged = mergeTrees(baseFilters ?? null, filterStore.applied);
    const displayProps: ViewDisplayProps = {
      ...DEFAULT_DISPLAY_PROPS,
      ...(currentView?.display_props ?? {}),
      ...(viewStore.overrides.get(viewStore.overrideKey)?.displayProps ?? {}),
      icon: input.icon,
    };
    try {
      const r = await ViewAPI.create(workspaceSlug, projectId, {
        name: input.name,
        layout: input.layout,
        filters: merged.conditions.length ? merged : { op: "AND", conditions: [] },
        display_props: displayProps,
      });
      const created = unwrap<IssueViewData>(r);
      viewStore.upsert(created);
      viewStore.discard();
      filterStore.clear();
      bump();
      setUrlView(created.id);
      toast(`视图「${created.name}」已创建并选中（URL ?view_id= 可分享直达）`, "ok");
      return created;
    } catch (e) {
      toast(e instanceof Error ? e.message : "创建视图失败", "error");
      return null;
    }
  }

  async function renameView(id: string, name: string): Promise<boolean> {
    if (!workspaceSlug || !projectId) return false;
    try {
      const r = await ViewAPI.patch(workspaceSlug, projectId, id, { name });
      viewStore.upsert(unwrap<IssueViewData>(r));
      bump();
      toast("视图已重命名", "ok");
      return true;
    } catch (e) {
      toast(e instanceof Error ? e.message : "重命名失败", "error");
      return false;
    }
  }

  async function deleteView(id: string): Promise<boolean> {
    if (!workspaceSlug || !projectId) return false;
    try {
      await ViewAPI.del(workspaceSlug, projectId, id);
      viewStore.remove(projectId, id);
      bump();
      if (viewIdParam === id) setUrlView(null);
      toast("视图已删除（软删）", "ok");
      return true;
    } catch (e) {
      toast(e instanceof Error ? e.message : "删除失败", "error");
      return false;
    }
  }

  /** ★ 设默认：PATCH /users/me/settings/ 偏好键 board.default_view_id（BR-10）。 */
  async function setDefaultView(id: string | null): Promise<void> {
    if (!projectId) return;
    try {
      const r = await UserSettingsAPI.patch({
        "board.default_view_id": id ? { [projectId]: id } : { [projectId]: null },
      });
      const prefs = unwrap<Record<string, unknown>>(r) ?? {};
      const dv = (prefs["board.default_view_id"] ?? {}) as Record<string, string>;
      viewStore.defaultViewByProject.clear();
      for (const [pid, vid] of Object.entries(dv)) viewStore.setDefault(pid, vid);
      bump();
      toast(id ? "已设为默认视图（PATCH users/me/settings/ board.default_view_id）" : "已取消默认视图", "ok");
    } catch (e) {
      toast(e instanceof Error ? e.message : "设置默认视图失败", "error");
    }
  }

  /** 切布局：路由段变化 + PATCH 视图 layout 单字段（BR-04/BR-13；「全部」裸态仅本地态）。 */
  async function switchLayout(next: ViewLayout, navigate: (path: string) => void) {
    const seg = next === "kanban" ? "board" : next === "table" ? "table" : next === "gantt" ? "gantt" : "issues";
    const path = `/${workspaceSlug}/projects/${projectId}/${seg}${window.location.search}`;
    if (currentView) {
      try {
        const r = await ViewAPI.patch(workspaceSlug!, projectId!, currentView.id, { layout: next });
        viewStore.upsert(unwrap<IssueViewData>(r));
      } catch {
        viewStore.patchOverride({ layout: next });
      }
      bump();
    } else if (viewStore.overrides.get("__all__")?.layout !== next) {
      // 「全部」无视图存档：仅本地态（TASK-011 §4.4.2 LayoutSwitcher 注）
      viewStore.patchOverride({ layout: next });
    }
    navigate(path);
  }

  // ── 列头配置源（五维，BOARD-003 §2.3「分组列来源」；响应不内嵌列元数据 BR-16）──
  const columnMetas = useCallback((dimension: string): BoardColumnMeta[] => {
    if (dimension === "state_id") {
      return states.map((s) => ({ key: s.id, name: s.name, color: s.color }));
    }
    if (dimension === "priority") {
      // PRIORITY_COLUMNS 五档固定表（枚举固定表，BR-06；none 为合法值列非哨兵）
      return PRIORITY_COLUMNS.map((p) => ({ ...p }));
    }
    if (dimension === "assignee_id") {
      return [
        ...members.map((m) => ({ key: m.id, name: m.name, avatarName: m.name })),
        { key: "__none__", name: "未指派", color: "#9CA3AF", none: true },
      ];
    }
    if (dimension === "label_id") {
      return [
        ...labels.map((l) => ({ key: l.id, name: l.name, color: l.color as string })),
        { key: "__none__", name: "无标签", color: "#9CA3AF", none: true },
      ];
    }
    const def = cfDefs.find((d) => d.key === dimension && d.is_active !== false);
    if (!def) return [];
    return [
      ...def.options.map((o) => ({ key: o.value, name: o.label, color: o.color ?? "#999" })),
      { key: "__none__", name: "未填值", color: "#9CA3AF", none: true, cfNone: true },
    ];
  }, [states, members, labels, cfDefs]);

  /** 分组维度候选（BUILTIN 置顶 + groupable cf_*，BOARD-003 §3.2 分组切换器）。 */
  const groupCandidates = useMemo(() => {
    const cf = cfDefs
      .filter((d) => d.is_active !== false && d.groupable)
      .map((d) => ({ key: d.key, name: `按${d.name}（自定义）` }));
    return [...BUILTIN_GROUPBYS, ...cf];
  }, [cfDefs]);

  /** 默认视图 id（board.default_view_id 镜像读取；★ 星标判定用）。 */
  const defaultViewIdOf = useCallback(
    (pid: string) => viewStore.defaultViewByProject.get(pid) ?? null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [viewStore.defaultViewByProject, rev],
  );

  return {
    // 数据
    views, currentView, effDisplay, dirty, viewIdParam, filtersParam,
    states, members, labels, cfDefs, meName,
    degraded, viewGone, defaultViewIdOf,
    groupBy, groupCandidates, columnMetas,
    listParams, fetchGrouped, fetchFlat,
    filterStore,
    // 动作
    switchView, applyFilterTree, clearTempFilters,
    patchDisplay, discardChanges, saveInPlace, createView, renameView, deleteView, setDefaultView, switchLayout,
    layout,
  };
}

export type ViewPage = ReturnType<typeof useViewPage>;
export type { FilterCondition, FilterLogicNode, IssueViewData };
