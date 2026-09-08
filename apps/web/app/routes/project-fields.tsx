import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { FieldAPI, IssueAPI, IssueTypeAPI, ProjectAPI, unwrap, type CustomFieldDef, type FieldOption } from "../services/api";
import type { ApiError } from "../services/axios";
import { useStores } from "../stores";
import { toast } from "../components/Toast";

/** 字段管理页（C.52 / TASK-008 §3.1）：项目设置 → 字段。
 *  - 列表列：拖拽把手 / 名称 / 类型中文 / 作用域（适用类型 chips）/ 必填·默认值标记 / 索引标记 / 操作 ⋯
 *  - 顶部提示条：x/50（≥45 变 amber）+ 「Workspace 全局字段 N 个 · 项目私有字段 M 个」
 *  - 继承折叠区：全局字段只读 +「在 Workspace 设置中管理」提示条
 *  - ⋯ 菜单：编辑 / 停用（数据保留）·启用 / 删除 / 上移 / 下移（键盘替代，C.52 拖拽排序行）
 *  - M-FIELD 新建/编辑弹层（C.53：12 类型宫格）；M-DELFIELD 三段式删除确认（C.54：202 受理）。 */

/** C.53 12 类型宫格（P2_ALLOWED_TYPES 同源；图标 = 原型 FIELD_TYPES）。 */
const FIELD_TYPES: Array<{ t: string; n: string; icon: string }> = [
  { t: "text", n: "单行文本", icon: "T" },
  { t: "textarea", n: "多行文本", icon: "≡" },
  { t: "select", n: "单选下拉", icon: "▣" },
  { t: "multi_select", n: "多选下拉", icon: "☑" },
  { t: "number", n: "数字", icon: "#" },
  { t: "currency", n: "金额", icon: "¥" },
  { t: "date", n: "日期", icon: "📅" },
  { t: "checkbox", n: "复选框", icon: "☑︎" },
  { t: "member", n: "成员", icon: "👤" },
  { t: "member_multi", n: "人员多选", icon: "👥" },
  { t: "url", n: "链接", icon: "🔗" },
  { t: "auto_increment", n: "自增编号", icon: "№" },
];
// ADR-0015 A-8 勘误：P2 白名单 12 类型为 member_multi/auto_increment（非原型宫格的
// email/phone——后端 P2_ALLOWED_TYPES 拒绝 email/phone，原型/旧清单照抄系笔误）
/** 选项区仅下拉类型显示（C.53：选定后表单下段按类型变形）。 */
const OPTION_TYPES = ["select", "multi_select"];
const AUTO_INC = "auto_increment"; // 选项区隐藏 + 必填强制关（TASK-008 §4.4.2）
const PRESET_COLORS = ["#DC2626", "#F59E0B", "#3B82F6", "#10B981", "#8B5CF6", "#EC4899", "#14B8A6", "#F97316", "#6B7280", "#0EA5E9", "#84CC16", "#A855F7"];
const MAX_FIELDS = 50;

/** 字段标识按名称生成（cf_ 前缀 snake_case；CJK 无拼音库时退化为 field+短哈希，可改）。 */
function keyFromName(name: string): string {
  const ascii = name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  if (ascii) return `cf_${ascii}`;
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return `cf_field_${h.toString(36)}`;
}
const KEY_RE = /^cf_[a-z][a-z0-9_]*$/;

type IssueTypeRow = { id: string; name: string; color: string; is_active: boolean };

export default function ProjectFields() {
  const { workspaceSlug, projectId } = useParams<{ workspaceSlug: string; projectId: string }>();
  const stores = useStores();
  const isAdmin = stores.permission.effectiveProjectRole(projectId, workspaceSlug) >= 20;

  const [project, setProject] = useState<{ name: string; identifier: string } | null>(null);
  const [defs, setDefs] = useState<CustomFieldDef[]>([]);
  const [types, setTypes] = useState<IssueTypeRow[]>([]);
  const [wsFoldOpen, setWsFoldOpen] = useState(false);
  /** 行 ⋯ 菜单 */
  const [rowMenuFor, setRowMenuFor] = useState<string | null>(null);
  /** M-FIELD 弹层（新建 = null；编辑 = 定义） */
  const [editModal, setEditModal] = useState<CustomFieldDef | null | undefined>(undefined);
  /** M-DELFIELD 三段式 */
  const [delModal, setDelModal] = useState<CustomFieldDef | null>(null);
  /** TASK-012 §4.4（补口轮）：权限矩阵弹层 */
  const [permModal, setPermModal] = useState<CustomFieldDef | null>(null);
  /** 202 删除受理后的顶部黄条（C.54：后台任务进度链接占位） */
  const [delAccepted, setDelAccepted] = useState<{ name: string; affected: number; taskUrl: string } | null>(null);

  async function load() {
    try {
      const [pRes, dRes, tRes] = await Promise.all([
        ProjectAPI.detail(workspaceSlug!, projectId!),
        FieldAPI.list(workspaceSlug!, projectId!, { scope: "all" }),
        IssueTypeAPI.list(workspaceSlug!, projectId!),
      ]);
      const p = (pRes as unknown as { data: { name: string; identifier: string } }).data;
      setProject(p ?? null);
      setDefs(unwrap<CustomFieldDef[]>(dRes) ?? []);
      setTypes(unwrap<IssueTypeRow[]>(tRes) ?? []);
    } catch (e: unknown) {
      const err = e as ApiError;
      toast(err?.message ?? "加载失败", "error");
    }
  }
  useEffect(() => {
    const t = setTimeout(() => { void load(); }, 0);
    return () => clearTimeout(t);
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceSlug, projectId]);

  // 行菜单外点击关闭（教训 #4）
  useEffect(() => {
    if (rowMenuFor == null) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (!t?.closest('[data-sb-scope="field-row-menu-wrap"]')) setRowMenuFor(null);
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [rowMenuFor]);

  const projDefs = useMemo(() => defs.filter((d) => d.scope === "project"), [defs]);
  const wsDefs = useMemo(() => defs.filter((d) => d.scope === "global"), [defs]);
  const totalActive = defs.filter((d) => d.is_active !== false).length;

  /** C.52 停用/启用（数据保留；启用一键恢复）。 */
  async function toggleActive(d: CustomFieldDef) {
    try {
      await FieldAPI.patch(workspaceSlug!, projectId!, d.id, { is_active: d.is_active === false });
      toast(d.is_active === false ? "字段已启用" : "字段已停用 · 值保留（表单与列表不再渲染）", d.is_active === false ? "ok" : "warning");
      await load();
    } catch (e: unknown) {
      const err = e as ApiError;
      toast(err?.details?.[0]?.message ?? err?.message ?? "操作失败", "error");
    }
  }

  /** C.52 键盘替代排序：行菜单上移/下移（PATCH sort-order prev_id/next_id 浮点插值）。
   *  上移 = 落到 siblings[idx-1] 之前（锚点 prev=idx-2, next=idx-1）；
   *  下移 = 落到 siblings[idx+1] 之后（锚点 prev=idx+1, next=idx+2）。 */
  async function moveField(d: CustomFieldDef, dir: -1 | 1) {
    const siblings = projDefs.filter((x) => x.is_active !== false);
    const idx = siblings.findIndex((x) => x.id === d.id);
    if (idx < 0) return;
    const at = (i: number) => siblings[i] ?? undefined;
    const prev = dir === -1 ? at(idx - 2) : at(idx + 1);
    const next = dir === -1 ? at(idx - 1) : at(idx + 2);
    const inRange = dir === -1 ? idx - 1 >= 0 : idx + 1 < siblings.length;
    if (!inRange) return;
    try {
      await FieldAPI.sortOrder(workspaceSlug!, projectId!, d.id, {
        prev_id: prev?.id ?? null,
        next_id: next?.id ?? null,
      });
      await load();
    } catch (e: unknown) {
      const err = e as ApiError;
      toast(err?.details?.[0]?.message ?? err?.message ?? "排序失败", "error");
    }
  }

  /** C.54 三段式删除：202 → 行消失 + 顶部黄条（后台任务进度链接 INFRA-004 §13.1）。 */
  async function doDelete(d: CustomFieldDef) {
    try {
      const r = await FieldAPI.del(workspaceSlug!, projectId!, d.id);
      const res = unwrap<{ affected_issues: number; status_url: string; task_id: string }>(r);
      setDelModal(null);
      setDelAccepted({ name: d.name, affected: res?.affected_issues ?? 0, taskUrl: res?.status_url ?? "" });
      toast("删除请求已受理（202）· 后台清除任务进行中", "warning", { ttl: 8000 });
      await load();
    } catch (e: unknown) {
      const err = e as ApiError;
      toast(err?.details?.[0]?.message ?? err?.message ?? "删除失败", "error");
    }
  }

  function Row({ d, readonly }: { d: CustomFieldDef; readonly: boolean }) {
    const t = FIELD_TYPES.find((x) => x.t === d.type) ?? { t: d.type, n: d.type, icon: "T" };
    const applicable = d.applicable_types.length
      ? d.applicable_types.map((tid) => types.find((x) => x.id === tid)?.name ?? tid)
      : null;
    return (
      <tr className={`hover:bg-neutral-50 ${d.is_active === false ? "opacity-50" : ""}`} data-sb-scope="field-row" data-field-key={d.key}>
        <td className="px-3 py-2.5 border-b border-neutral-100 text-neutral-300 select-none" aria-hidden="true">⋮⋮</td>
        <td className="px-3 py-2.5 border-b border-neutral-100 text-[13px] font-medium">{d.name}</td>
        <td className="px-3 py-2.5 border-b border-neutral-100"><span className="text-[12px] text-neutral-600 bg-neutral-100 rounded px-2 py-0.5" data-sb-scope="field-type-chip">{t.icon} {t.n}</span></td>
        <td className="px-3 py-2.5 border-b border-neutral-100">
          {applicable
            ? applicable.map((n) => <span key={n} className="text-[11px] text-neutral-400 bg-neutral-100 rounded px-1.5 py-0.5 mr-1">{n}</span>)
            : <span className="text-[11px] text-neutral-400 bg-neutral-100 rounded px-1.5 py-0.5">全部类型</span>}
        </td>
        <td className="px-3 py-2.5 border-b border-neutral-100">
          <span className={`text-[12px] ${d.required || d.default_value != null ? "text-neutral-600" : "text-neutral-300"}`}>
            {d.required ? "已必填" : d.default_value != null ? "有默认值" : "—"}
          </span>
        </td>
        <td className="px-3 py-2.5 border-b border-neutral-100">
          {d.indexed
            ? <span className="inline-flex items-center gap-1 text-[12px] text-neutral-600"><span className="w-2 h-2 rounded-full bg-brand-500" title="已建立表达式索引（≤10/工作空间）" />索引优化</span>
            : <span className="text-[12px] text-neutral-300">—</span>}
        </td>
        <td className="px-3 py-2.5 border-b border-neutral-100">{d.is_active === false && <span className="text-[12px] text-neutral-400" data-sb-scope="field-inactive-mark">灰显 · 数据保留</span>}</td>
        <td className="px-3 py-2.5 border-b border-neutral-100 text-right">
          <span className="relative inline-block" data-sb-scope="field-row-menu-wrap">
            <button aria-label={`字段操作 ${d.name}`} data-sb-scope="field-row-menu"
              className="w-7 h-7 inline-flex items-center justify-center text-neutral-400 hover:bg-neutral-100 rounded"
              onClick={() => setRowMenuFor(rowMenuFor === d.id ? null : d.id)}>⋯</button>
            {rowMenuFor === d.id && (
              <div role="menu" className="absolute right-0 top-7 z-10 w-[170px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1">
                <button role="menuitem" data-sb-scope="field-menu-edit" disabled={readonly}
                  onClick={() => { setRowMenuFor(null); setEditModal(d); }}
                  className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50 disabled:opacity-40">编辑</button>
                {/* TASK-012 §4.4（补口轮）：权限矩阵网格入口 */}
                <button role="menuitem" data-sb-scope="field-menu-perm" disabled={readonly}
                  onClick={() => { setRowMenuFor(null); setPermModal(d); }}
                  className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50 disabled:opacity-40">字段权限…</button>
                <button role="menuitem" data-sb-scope="field-menu-toggle" disabled={readonly}
                  onClick={() => { setRowMenuFor(null); void toggleActive(d); }}
                  className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50 disabled:opacity-40">
                  {d.is_active === false ? "启用" : "停用（数据保留）"}
                </button>
                <button role="menuitem" data-sb-scope="field-menu-del" disabled={readonly}
                  onClick={() => { setRowMenuFor(null); setDelModal(d); }}
                  className="w-full text-left px-3 h-8 text-[13px] text-red-600 hover:bg-red-50 disabled:opacity-40">删除</button>
                <div className="h-px bg-neutral-100 my-1" />
                <button role="menuitem" data-sb-scope="field-menu-up" disabled={readonly}
                  onClick={() => { setRowMenuFor(null); void moveField(d, -1); }}
                  className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50 disabled:opacity-40">↑ 上移</button>
                <button role="menuitem" data-sb-scope="field-menu-down" disabled={readonly}
                  onClick={() => { setRowMenuFor(null); void moveField(d, 1); }}
                  className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50 disabled:opacity-40">↓ 下移</button>
              </div>
            )}
          </span>
        </td>
      </tr>
    );
  }

  return (
    <div className="flex flex-col h-screen">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <ProjectSidebar projectName={project?.name ?? "…"} identifier={project?.identifier ?? ""} />
        <main className="flex-1 min-w-0 flex flex-col overflow-hidden">
          {/* C.52 视图条：字段管理 + 新建字段 */}
          <div className="h-[56px] border-b border-neutral-200 flex items-center gap-2 px-5 bg-white shrink-0">
            <span className="text-[15px] font-semibold">字段管理</span>
            <span className="text-[13px] text-neutral-400">项目设置 → 字段</span>
            <button onClick={() => setEditModal(null)} data-sb-scope="fields-new"
              className="ml-auto inline-flex h-[34px] items-center gap-1.5 px-3.5 bg-brand-500 text-white rounded-md font-medium hover:bg-brand-600">＋ 新建字段</button>
          </div>

          <div className="flex-1 overflow-y-auto p-5">
            {/* C.52 权限负向：需要项目管理员权限 */}
            {!isAdmin ? (
              <div className="flex flex-col items-center gap-2 py-16 text-neutral-500">
                <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#d4d4d4" strokeWidth="2"><rect width="18" height="11" x="3" y="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
                <div className="text-[15px] font-semibold text-neutral-700">需要项目管理员权限</div>
                <div className="text-[13px]">字段管理仅项目管理员可用</div>
              </div>
            ) : (
              <>
                {/* C.52 202 删除受理黄条（后台任务进度） */}
                {delAccepted && (
                  <div className="flex items-center gap-2 mb-3 rounded-lg border border-amber-200 bg-amber-50 text-amber-800 px-3.5 py-2 text-[13px]" role="status" data-sb-scope="fields-del-accepted">
                    <span aria-hidden="true">⚠</span>
                    「{delAccepted.name}」删除请求已受理：{delAccepted.affected} 个任务的字段值后台清除中（不可恢复）。
                  </div>
                )}
                {/* C.52 顶部提示条：x/50（≥45 amber）+ 全局/私有计数 */}
                <div className="flex items-center gap-2 text-[12px] text-neutral-400 mb-3" data-sb-scope="fields-cap">
                  <span className={`tabular-nums ${totalActive >= 45 ? "text-amber-700 font-semibold" : ""}`}>{totalActive}/{MAX_FIELDS}</span>
                  个字段{totalActive >= 45 ? "（接近上限）" : ""}
                  <span className="ml-auto">Workspace 全局字段 {wsDefs.length} 个 · 项目私有字段 {projDefs.length} 个</span>
                </div>

                {/* C.52 列表 */}
                <table className="w-full border-collapse bg-white border border-neutral-200 rounded-lg overflow-hidden">
                  <thead><tr>
                    {["", "名称", "类型", "作用域", "必填/默认", "索引", "", ""].map((h, i) => (
                      <th key={i} className="text-left px-3 py-2.5 border-b border-neutral-200 text-[11px] font-semibold text-neutral-400 uppercase tracking-wider bg-white">{h}</th>
                    ))}
                  </tr></thead>
                  <tbody>
                    {/* C.52 空态：创建第一个字段 + 3 个场景模板按钮 */}
                    {projDefs.length === 0 ? (
                      <tr><td colSpan={8} className="py-10 text-center">
                        <div className="flex flex-col items-center gap-2 text-neutral-500">
                          <div className="text-[15px] font-semibold text-neutral-700">还没有字段</div>
                          <div className="text-[13px]">为任务补充业务属性（严重等级 / 需求来源 / 影响版本…）</div>
                          <div className="flex gap-2 mt-1">
                            {[["严重等级", "select"], ["需求来源", "select"], ["影响版本", "multi_select"]].map(([n]) => (
                              <button key={n} data-sb-scope="fields-empty-tpl"
                                onClick={() => setEditModal(null)}
                                className="h-[30px] px-3 border border-neutral-300 rounded-md text-[13px] hover:border-brand-400 hover:text-brand-600">一键创建：{n}</button>
                            ))}
                          </div>
                        </div>
                      </td></tr>
                    ) : projDefs.map((d) => <Row key={d.id} d={d} readonly={false} />)}
                  </tbody>
                </table>

                {/* C.52 继承折叠区：全局字段只读 +「在 Workspace 设置中管理」 */}
                <div className="mt-5">
                  <button className="flex items-center gap-2 w-full text-left py-1.5" onClick={() => setWsFoldOpen((v) => !v)} data-sb-scope="fields-ws-fold">
                    <span className="text-neutral-400" aria-hidden="true">{wsFoldOpen ? "▾" : "▸"}</span>
                    继承自 Workspace <span className="text-[12px] text-neutral-400">{wsDefs.length} 个</span>
                    <span className="ml-auto text-[12px] text-neutral-400">只读 · 在 Workspace 设置中管理</span>
                  </button>
                  {wsFoldOpen && (
                    <div data-sb-scope="fields-ws-body">
                      <table className="w-full border-collapse bg-white border border-neutral-200 rounded-lg overflow-hidden">
                        <tbody>{wsDefs.map((d) => <Row key={d.id} d={d} readonly />)}</tbody>
                      </table>
                      <div className="flex items-center gap-2 mt-2.5 text-[12.5px] text-neutral-400 bg-neutral-100 rounded-lg px-2.5 py-1.5">
                        <span aria-hidden="true">⚠</span>
                        全局字段对本项目只读；如需停用请在 Workspace 设置操作（变更将即时对本项目生效）。
                      </div>
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </main>
      </div>

      {/* M-FIELD 新建/编辑字段弹层（C.53） */}
      {editModal !== undefined && (
        <FieldModal
          slug={workspaceSlug!} projectId={projectId!} types={types} editing={editModal}
          onClose={() => setEditModal(undefined)}
          onSaved={() => { setEditModal(undefined); void load(); }}
        />
      )}

      {/* M-DELFIELD 三段式删除确认（C.54） */}
      {delModal && (
        <DeleteFieldModal
          slug={workspaceSlug!} projectId={projectId!} field={delModal}
          onCancel={() => setDelModal(null)}
          onConfirm={() => void doDelete(delModal)}
        />
      )}

      {/* TASK-012 §4.4（补口轮）：权限矩阵网格 */}
      {permModal && (
        <PermissionMatrixModal
          slug={workspaceSlug!} projectId={projectId!} field={permModal}
          onClose={() => setPermModal(null)}
          onSaved={() => { void load(); }}
        />
      )}
    </div>
  );
}

/* ═══════════ TASK-012 §4.4 权限矩阵网格（补口轮）═══════════ */

const PERM_ROLES = [
  { token: "role:proj_admin", label: "管理员" },
  { token: "role:proj_contributor", label: "成员" },
  { token: "role:proj_commenter", label: "评论者" },
  { token: "role:proj_viewer", label: "只读" },
];
type PermSets = { read: Set<string>; write: Set<string>; required_for: Set<string> };

function PermissionMatrixModal({ slug, projectId, field, onClose, onSaved }: {
  slug: string; projectId: string; field: CustomFieldDef; onClose: () => void; onSaved: () => void;
}) {
  const cfg = field.permission_config ?? {};
  const [sets, setSets] = useState<PermSets>({
    read: new Set(cfg.read ?? []),
    write: new Set(cfg.write ?? []),
    required_for: new Set(cfg.required_for ?? []),
  });
  const [busy, setBusy] = useState(false);

  function toggle(key: keyof PermSets, token: string) {
    setSets((s) => {
      const n = new Set(s[key]);
      if (n.has(token)) n.delete(token); else n.add(token);
      return { ...s, [key]: n };
    });
  }

  async function save() {
    setBusy(true);
    try {
      const payload = {
        read: [...sets.read], write: [...sets.write], required_for: [...sets.required_for],
      };
      await FieldAPI.patch(slug, projectId, field.id, { permission_config: payload });
      toast("字段权限已保存（access 缓存 60s 内收敛）", "ok");
      onSaved();
      onClose();
    } catch (e) {
      const err = e as ApiError;
      toast(err?.details?.[0]?.message ?? err?.message ?? "保存失败（BR-08/BR-17 校验）", "error");
    } finally {
      setBusy(false);
    }
  }

  const cell = (key: keyof PermSets, token: string) => (
    // 静态位（每行固定三格）不带 key——三列同 token 会撞兄弟 key（React 警告）
    <td className="px-3 py-2 border-b border-neutral-100 text-center">
      <input type="checkbox" checked={sets[key].has(token)}
        data-sb-scope="perm-cell" data-set={key} data-token={token}
        onChange={() => toggle(key, token)} />
    </td>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/25" data-sb-scope="perm-matrix">
      <div className="bg-white rounded-xl shadow-2xl w-[560px] max-h-[85vh] overflow-auto p-5" role="dialog" aria-modal="true" aria-label="字段权限矩阵">
        <h2 className="text-sm font-semibold">字段权限 · {field.name}</h2>
        <p className="text-[12px] text-neutral-400 mt-1 mb-3">
          留空 = 全员；read 限可读（未列角色 <b>hidden</b>）、write 限可写（只读角色 <b>readonly</b>）、required_for 按角色必填
        </p>
        <table className="w-full text-sm border border-neutral-200 rounded-lg">
          <thead>
            <tr className="text-left text-[11px] uppercase tracking-wider text-neutral-400 border-b border-neutral-200">
              <th className="px-3 py-2 font-semibold">角色</th>
              <th className="px-3 py-2 font-semibold text-center">可读</th>
              <th className="px-3 py-2 font-semibold text-center">可写</th>
              <th className="px-3 py-2 font-semibold text-center">必填</th>
            </tr>
          </thead>
          <tbody>
            {PERM_ROLES.map((r) => (
              <tr key={r.token}>
                <td className="px-3 py-2 border-b border-neutral-100 text-neutral-700">{r.label}</td>
                {cell("read", r.token)}
                {cell("write", r.token)}
                {cell("required_for", r.token)}
              </tr>
            ))}
          </tbody>
        </table>
        <p className="text-[12px] text-neutral-400 mt-3">
          当前字段全局必填={String(field.required)}——全局必填字段不得把可读角色落入只读（BR-17）。
        </p>
        <div className="flex justify-end gap-2 mt-4">
          <button type="button" onClick={onClose} className="h-8 px-3 rounded-md border border-neutral-200 text-sm">取消</button>
          <button type="button" data-sb-scope="perm-save-btn" disabled={busy} onClick={() => void save()}
            className="h-8 px-3 rounded-md bg-brand-600 text-white text-sm disabled:opacity-50">保存权限</button>
        </div>
      </div>
    </div>
  );
}

/* ═══════════ M-FIELD 新建/编辑字段弹层（C.53 / TASK-008 §3.2）═══════════ */

function useEscClose(onClose: () => void) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
}

function FieldModal({ slug, projectId, types, editing, onClose, onSaved }: {
  slug: string; projectId: string; types: IssueTypeRow[];
  editing: CustomFieldDef | null;
  onClose: () => void; onSaved: () => void;
}) {
  useEscClose(onClose);
  const [name, setName] = useState(editing?.name ?? "");
  const [manualKey, setManualKey] = useState(editing?.key ?? "");
  const [keyTouched, setKeyTouched] = useState(Boolean(editing));
  const [fieldType, setFieldType] = useState<string | null>(editing?.type ?? null);
  const [applicable, setApplicable] = useState<string[]>(editing?.applicable_types ?? []);
  const [required, setRequired] = useState(editing?.required ?? false);
  const [indexed, setIndexed] = useState(editing?.indexed ?? false);
  const [desc, setDesc] = useState(editing?.description ?? "");
  const [options, setOptions] = useState<FieldOption[]>(
    editing?.options?.length ? editing.options.map((o) => ({ ...o })) : [],
  );
  const [saving, setSaving] = useState(false);
  const nameRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => { nameRef.current?.focus(); }, []);
  // 字段标识 = 按名称生成（未手改）；手改后以手改值为准（派生值，不在 effect 里 setState）
  const fieldKey = keyTouched ? manualKey : (name.trim() ? keyFromName(name) : "");

  const showOptions = fieldType != null && OPTION_TYPES.includes(fieldType);
  const keyValid = KEY_RE.test(fieldKey);
  const canSave = Boolean(name.trim()) && fieldType != null && keyValid && (!showOptions || options.length > 0);

  function upsertOption(i: number, patch: Partial<FieldOption>) {
    setOptions((cur) => cur.map((o, j) => (j === i ? { ...o, ...patch } : o)));
  }

  async function save() {
    if (!canSave || saving) return;
    setSaving(true);
    try {
      const normOpts = options.map((o, i) => {
        const out: { label: string; value: string; sort_order: number; color?: string } = { label: o.label, value: o.value, sort_order: i + 1 };
        if (o.color) out.color = o.color;
        return out;
      });
      if (editing) {
        const patch: Parameters<typeof FieldAPI.patch>[3] = {
          name: name.trim(), description: desc, is_required: required, is_indexed: indexed,
        };
        if (showOptions) patch.options = normOpts;
        await FieldAPI.patch(slug, projectId, editing.id, patch);
        toast("字段已更新", "ok");
      } else {
        const create: Parameters<typeof FieldAPI.create>[2] = {
          name: name.trim(), field_key: fieldKey, field_type: fieldType!,
          is_required: required, is_indexed: indexed, description: desc,
          applicable_types: applicable,
        };
        if (showOptions) create.options = normOpts;
        await FieldAPI.create(slug, projectId, create);
        toast(`已创建字段 ${name.trim()}`, "ok");
      }
      onSaved();
    } catch (e: unknown) {
      const err = e as ApiError;
      toast(err?.details?.[0]?.message ?? err?.message ?? "保存失败", "error");
    } finally { setSaving(false); }
  }

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center p-4 z-[90]">
      <div className="bg-white rounded-xl shadow-lg w-[640px] max-w-full max-h-[88vh] overflow-y-auto p-6" role="dialog" aria-modal="true" aria-label={editing ? "编辑字段" : "新建字段"}>
        <div className="flex items-center justify-between mb-4">
          <div className="text-base font-semibold" data-sb-scope="field-modal-title">{editing ? "编辑字段" : "新建字段"}</div>
          <button aria-label="关闭" onClick={onClose} className="w-7 h-7 flex items-center justify-center text-neutral-500 hover:text-neutral-900">✕</button>
        </div>

        {/* C.53 字段名称 + 字段标识（cf_ 前缀按名称生成，可改；创建后不可改） */}
        <div className="mb-3.5">
          <span className="block text-[13px] font-medium text-neutral-700 mb-1.5">字段名称</span>
          <input ref={nameRef} className="w-full h-9 border border-neutral-300 rounded-md px-2.5 text-[13px]" placeholder="严重等级"
            aria-label="字段名称" data-sb-scope="field-name" value={name}
            onChange={(e) => setName(e.target.value)} />
          <div className="text-[12px] text-neutral-400 mt-1" data-sb-scope="field-key-hint">
            字段标识 <b className="font-mono text-neutral-600">{fieldKey || "cf_…"}</b>（系统按名称生成，{editing ?<>创建后不可改</>:<>可修改</>}）
            {!keyValid && fieldKey && <span className="text-red-500 ml-1">键名必须为 cf_ 前缀的 snake_case</span>}
          </div>
          {!editing && (
            <input className="w-[240px] h-8 border border-neutral-200 rounded-md px-2 text-[12px] font-mono mt-1"
              aria-label="字段标识（可修改）" data-sb-scope="field-key-input" value={fieldKey}
              onChange={(e) => { setKeyTouched(true); setManualKey(e.target.value); }} />
          )}
        </div>

        {/* C.53 12 类型宫格（选定后表单下段按类型变形；编辑态类型置灰不可改） */}
        <div className="mb-3.5">
          <span className="block text-[13px] font-medium text-neutral-700 mb-1.5">字段类型</span>
          <div className="grid grid-cols-4 gap-2" role="radiogroup" aria-label="字段类型" data-sb-scope="field-type-grid">
            {FIELD_TYPES.map((t) => (
              <button key={t.t} type="button" role="radio" aria-checked={fieldType === t.t} data-sb-scope="field-type-cell" data-field-type={t.t}
                disabled={Boolean(editing)}
                onClick={() => { setFieldType(t.t); if (!OPTION_TYPES.includes(t.t)) setOptions([]); else if (options.length === 0) setOptions([{ label: "致命", value: "critical", color: PRESET_COLORS[0]! }, { label: "严重", value: "major", color: PRESET_COLORS[1]! }, { label: "一般", value: "minor", color: PRESET_COLORS[2]! }]); }}
                className={`border rounded-lg py-2 flex flex-col items-center gap-1 text-[12px] transition ${fieldType === t.t ? "border-brand-500 bg-brand-50 text-brand-600 font-semibold" : "border-neutral-300 text-neutral-600 hover:border-brand-400 hover:bg-brand-50/50"} ${editing ? "opacity-50 cursor-not-allowed" : ""}`}>
                <span aria-hidden="true">{t.icon}</span>{t.n}
              </button>
            ))}
          </div>
          {editing && <div className="text-[12px] text-neutral-400 mt-1.5">类型创建后不可变</div>}
        </div>

        {/* C.53 适用任务类型（全部默认勾选） */}
        <div className="mb-3.5">
          <span className="block text-[13px] font-medium text-neutral-700 mb-1.5">适用任务类型</span>
          <div className="flex gap-3 flex-wrap text-[13px] text-neutral-600">
            <label className="inline-flex items-center gap-1.5"><input type="checkbox" className="accent-brand-500" checked disabled /><span>全部</span></label>
            {types.filter((t) => t.is_active).map((t) => (
              <label key={t.id} className="inline-flex items-center gap-1.5" data-sb-scope="field-applicable-type">
                <input type="checkbox" className="accent-brand-500" checked={applicable.includes(t.id)}
                  onChange={(e) => setApplicable((cur) => (e.target.checked ? [...cur, t.id] : cur.filter((x) => x !== t.id)))} />
                <span>{t.name}</span>
              </label>
            ))}
          </div>
        </div>

        {/* C.53 选项行（色块 + 显示名 + 存储值 + 删除 + 添加选项） */}
        {showOptions && (
          <div className="mb-3.5">
            <span className="block text-[13px] font-medium text-neutral-700 mb-1.5">选项（至少 1 项）</span>
            <div data-sb-scope="field-options">
              {options.map((o, i) => (
                <div key={i} className="flex items-center gap-2 border border-neutral-200 rounded-lg px-2.5 py-1.5 mb-1.5 bg-white">
                  <span className="w-3.5 h-3.5 rounded shrink-0" style={{ background: o.color ?? "#999" }} title="选项颜色（对比度 ≥4.5:1）" role="img" aria-label="选项颜色" data-sb-scope="field-opt-color" />
                  <input className="flex-1 h-[30px] border border-neutral-300 rounded px-2 text-[13px]" placeholder="显示名" aria-label="选项显示名"
                    value={o.label} onChange={(e) => upsertOption(i, { label: e.target.value })} />
                  <input className="w-[110px] h-[30px] border border-neutral-300 rounded px-2 text-[13px] font-mono" placeholder="存储值" aria-label="选项存储值"
                    value={o.value} onChange={(e) => upsertOption(i, { value: e.target.value })} />
                  <button aria-label="删除选项" onClick={() => setOptions((cur) => cur.filter((_, j) => j !== i))}
                    className="w-6 h-6 inline-flex items-center justify-center text-neutral-400 hover:text-red-600">✕</button>
                </div>
              ))}
            </div>
            <button className="text-brand-600 hover:text-brand-700 text-[13px]" data-sb-scope="field-add-opt"
              onClick={() => setOptions((cur) => [...cur, { label: `新选项${cur.length + 1}`, value: `opt_${cur.length + 1}`, color: PRESET_COLORS[cur.length % PRESET_COLORS.length]! }])}
            >＋ 添加选项</button>
            <span className="text-[12px] text-neutral-400 ml-2.5">ⓘ 存储值创建后不可改，显示名可改</span>
          </div>
        )}

        {/* C.53 必填 / 索引（帮助气泡） / 帮助说明 */}
        <div className="flex gap-4 items-center my-3.5 text-[13px] text-neutral-600">
          <label className="inline-flex items-center gap-1.5" data-sb-scope="field-required">
            <input type="checkbox" className="accent-brand-500" checked={fieldType === AUTO_INC ? false : required}
                   disabled={fieldType === AUTO_INC} onChange={(e) => setRequired(e.target.checked)} />必填
          </label>
          <label className="inline-flex items-center gap-1.5" data-sb-scope="field-indexed">
            <input type="checkbox" className="accent-brand-500" checked={indexed} onChange={(e) => setIndexed(e.target.checked)} />建立索引优化
          </label>
          <span className="text-neutral-400" title="加速该字段的排序与范围查询；每个工作空间最多 10 个">ⓘ 上限说明</span>
        </div>
        <div className="mb-4">
          <span className="block text-[13px] font-medium text-neutral-700 mb-1.5">帮助说明</span>
          <input className="w-full h-9 border border-neutral-300 rounded-md px-2.5 text-[13px]" placeholder="critical 需 2 小时内响应"
            aria-label="帮助说明" data-sb-scope="field-desc" value={desc} onChange={(e) => setDesc(e.target.value)} />
        </div>

        <div className="flex justify-end gap-2.5">
          <button onClick={onClose} className="h-[34px] px-3.5 border border-neutral-300 rounded-md text-[13px]">取消</button>
          <button onClick={() => void save()} disabled={!canSave || saving} data-sb-scope="field-save"
            className="h-[34px] px-3.5 bg-brand-500 text-white rounded-md text-[13px] hover:bg-brand-600 disabled:opacity-50 disabled:cursor-not-allowed"
          >{editing ? "保存修改" : "创建字段"}</button>
        </div>
      </div>
    </div>
  );
}

/* ═══════════ M-DELFIELD 删除字段三段式确认（C.54 / TASK-008 §3.3）═══════════ */

function DeleteFieldModal({ slug, projectId, field, onCancel, onConfirm }: {
  slug: string; projectId: string;
  field: CustomFieldDef; onCancel: () => void; onConfirm: () => void;
}) {
  useEscClose(onCancel);
  const [confirmName, setConfirmName] = useState("");
  /** C.54 影响统计（服务端口径换算：填写数 = 任务总数 − `?property.<id>=null` 空值数）。 */
  const [usage, setUsage] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    const totalP = IssueAPI.list(slug, projectId, { per_page: 1 }) as unknown as Promise<{ data: unknown } & { meta?: { total_count?: number } }>;
    const emptyParams: Record<string, string | number> = { per_page: 1, [`property.${field.id}`]: "null" };
    const emptyP = IssueAPI.list(slug, projectId, emptyParams as unknown as { per_page?: number }) as unknown as Promise<{ data: unknown } & { meta?: { total_count?: number } }>;
    Promise.allSettled([totalP, emptyP]).then(([t, e]) => {
      if (cancelled) return;
      const total = t.status === "fulfilled" ? (t.value.meta?.total_count ?? null) : null;
      const empty = e.status === "fulfilled" ? (e.value.meta?.total_count ?? null) : null;
      if (total != null && empty != null) setUsage(Math.max(0, total - empty));
    });
    return () => { cancelled = true; };
  }, [slug, projectId, field.id]);

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center p-4 z-[95]">
      <div className="bg-white rounded-xl shadow-lg w-[480px] max-w-full p-6" role="alertdialog" aria-modal="true" aria-label={`删除字段 ${field.name}`}>
        <div className="flex items-center justify-between mb-4">
          <div className="text-base font-semibold text-red-700" data-sb-scope="delfield-title">⚠ 删除字段「{field.name}」？</div>
          <button aria-label="关闭" onClick={onCancel} className="w-7 h-7 flex items-center justify-center text-neutral-500 hover:text-neutral-900">✕</button>
        </div>
        {/* C.54 影响统计 + 删除后果 */}
        <div className="rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-[13px] text-red-700 my-2.5" data-sb-scope="delfield-stats">
          <b>该字段当前：</b>
          <ul className="list-disc ml-5 mt-1 text-neutral-700">
            <li>被 <b data-sb-scope="delfield-affected">{usage == null ? "…" : usage.toLocaleString("zh-CN")}</b> 个任务填写</li>
            <li>被 3 个视图引用（TASK-011 上线后）</li>
          </ul>
          <b className="block mt-1.5">删除后：</b>
          <ul className="list-disc ml-5 text-neutral-700">
            <li>全部任务的该字段值将被<b>异步清除（不可恢复）</b></li>
            <li>引用它的视图将自动移除该条件</li>
          </ul>
        </div>
        {/* C.54 输入字段名确认：输入 == 字段名才激活 [确认删除] */}
        <span className="block text-[13px] font-medium text-neutral-700 mb-1.5">请输入字段名确认：</span>
        <input className="w-full h-9 border border-neutral-300 rounded-md px-2.5 text-[13px]" placeholder={field.name}
          aria-label="输入字段名确认" data-sb-scope="delfield-confirm-input" value={confirmName}
          onChange={(e) => setConfirmName(e.target.value)} />
        <div className="flex justify-end gap-2.5 mt-5">
          <button onClick={onCancel} className="h-[34px] px-3.5 border border-neutral-300 rounded-md text-[13px]">取消</button>
          <button onClick={onConfirm} disabled={confirmName.trim() !== field.name} data-sb-scope="delfield-go"
            className="h-[34px] px-3.5 bg-red-500 text-white rounded-md text-[13px] hover:bg-red-600 disabled:opacity-45 disabled:cursor-not-allowed"
          >确认删除</button>
        </div>
      </div>
    </div>
  );
}
