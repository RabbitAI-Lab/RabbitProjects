import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { IssueDrawer } from "../components/IssueDrawer";
import { NewTaskModal } from "../components/NewTaskModal";
import { StateBadge } from "../components/StateBadge";
import { AvatarStack, fmtMinutes } from "../components/issue-dialogs";
import {
  AssigneeAPI,
  FieldAPI,
  IssueAPI,
  ProjectAPI,
  ProjectMemberAPI,
  RelationAPI,
  unwrap,
  type CustomFieldDef,
  type RelationRow,
} from "../services/api";
import type { ApiError } from "../services/axios";
import { useStores } from "../stores";
import { toast } from "../components/Toast";
import { LabelsAdminModal } from "./labels-admin";
import { ViewSwitchBar } from "../components/views/ViewSwitchBar";
import { FilterOpenButton, ViewChipsRow } from "../components/views/ViewFilterBar";
import { useViewPage } from "../components/views/useViewPage";
import type { Issue, SubtreeData } from "@rp/types";

const today = () => new Date().toISOString().slice(0, 10);

/** TASK-004 §1.3：业务层级上限（根=1）。第 5 层行不渲染悬浮「＋」（前端预判，API 409 兜底）。 */
const MAX_ISSUE_DEPTH = 5;
/** C.39 / §4.4.2 DROP_EDGE_RATIO：目标行上沿 25% = 排序区，其余 75% = 成为子任务区。 */
const DROP_EDGE_RATIO = 0.25;

interface Chip { k: string; v: string; label: string }

/** 树形行专用动效/拖拽落点样式（keyframes 无法用 Tailwind 内联，注入一次性 style） */
const TREE_CSS = `
@keyframes treecycleflash{0%,60%{background:#fef2f2}100%{background:transparent}}
tr.cycle-flash td{animation:treecycleflash 2s ease}
tr.row-drop-nest td{background:#eef4ff!important}
tr.row-drop-reorder td{box-shadow:inset 0 2px 0 #6366f1}
tr.row-dragging td{opacity:.5}
`;

type ExpandPhase = "loading" | "loaded" | "error";

/** C.51 快速指派浮层：单人快速选择（CONTRIBUTOR+ 可选；COMMENTER/VIEWER 灰显标注）。 */
function QuickAssignPop({ members, onPick }: {
  members: Array<{ id: string; user: { id: string; display_name: string; email: string }; role: number }>;
  onPick: (userId: string) => void;
}) {
  const assignable = members.filter((m) => m.role >= 15);
  return (
    <div role="menu" data-sb-scope="list-quick-assign-pop" aria-label="快速指派候选"
      className="absolute left-0 top-6 z-30 w-[240px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1 max-h-[240px] overflow-y-auto">
      {assignable.length === 0 ? (
        <div className="px-3 py-2 text-[13px] text-neutral-400">无可指派成员</div>
      ) : assignable.map((m) => (
        <button key={m.id} role="menuitem" data-sb-scope="list-quick-assign-item"
          onClick={(e) => { e.stopPropagation(); onPick(m.user.id); }}
          className="w-full text-left px-3 h-9 text-[13px] hover:bg-neutral-50 flex items-center gap-2">
          <span className="w-5 h-5 rounded-full text-white text-[10px] font-semibold flex items-center justify-center shrink-0"
            style={{ background: "#3b82f6" }} aria-hidden="true">{Array.from(m.user.display_name)[0]}</span>
          <span className="flex-1 truncate">{m.user.display_name}</span>
          <span className="text-[11px] text-neutral-400 font-mono">{m.user.email}</span>
        </button>
      ))}
    </div>
  );
}

/** C.56 动态列值渲染（select=色块文本 / member=头像 / date=yyyy-MM-dd / currency=¥ / multi=chips 前 2+N）。 */
function CfCell({ field, value, nameOf }: {
  field: CustomFieldDef | undefined;
  value: unknown;
  nameOf: (uid: string) => string;
}) {
  if (value == null || value === false || value === "") return <span className="text-neutral-400">—</span>;
  if (!field) return <span className="text-[12px] text-neutral-600 truncate">{String(value)}</span>;
  if (field.type === "select") {
    const opt = field.options.find((o) => o.value === value);
    return (
      <span className="inline-flex items-center gap-1.5 text-[12px] text-neutral-600">
        <span className="w-2 h-2 rounded-sm shrink-0" style={{ background: opt?.color ?? "#999" }} />
        {opt?.label ?? String(value)}
      </span>
    );
  }
  if (field.type === "multi_select") {
    const vals = Array.isArray(value) ? value : [value];
    return (
      <span className="inline-flex items-center gap-1.5">
        {vals.slice(0, 2).map((v) => {
          const opt = field.options.find((o) => o.value === v);
          return (
            <span key={String(v)} className="inline-flex items-center gap-1 text-[12px] text-neutral-600">
              <span className="w-2 h-2 rounded-sm shrink-0" style={{ background: opt?.color ?? "#999" }} />{opt?.label ?? String(v)}
            </span>
          );
        })}
        {vals.length > 2 && <span className="text-neutral-400">+{vals.length - 2}</span>}
      </span>
    );
  }
  if (field.type === "member") {
    const name = nameOf(String(value));
    return (
      <span className="inline-flex items-center gap-1 text-[12px]">
        <span className="w-5 h-5 rounded-full bg-neutral-200 text-neutral-700 text-[10px] font-semibold flex items-center justify-center" aria-hidden="true">{Array.from(name)[0]}</span>
      </span>
    );
  }
  if (field.type === "currency") {
    return <span className="font-mono text-[12px]">¥{Number(value).toLocaleString("zh-CN", { minimumFractionDigits: 2 })}</span>;
  }
  if (field.type === "date") return <span className="font-mono text-[12px]">{String(value)}</span>;
  if (field.type === "checkbox") {
    return <span className={value ? "text-emerald-600" : "text-neutral-400"}>{value ? "开" : "关"}</span>;
  }
  return <span className="text-[12px] text-neutral-600 truncate">{String(value)}</span>;
}

export default function IssuesList() {
  const { workspaceSlug, projectId } = useParams<{ workspaceSlug: string; projectId: string }>();
  const [issues, setIssues] = useState<Issue[]>([]);
  const [project, setProject] = useState<{ name: string; identifier: string } | null>(null);
  const [quick, setQuick] = useState("");
  const [creating, setCreating] = useState(false);
  const [showTaskModal, setShowTaskModal] = useState(false);
  const [search, setSearch] = useState("");
  const [chips, setChips] = useState<Chip[]>([]);
  const [showLabelsAdmin, setShowLabelsAdmin] = useState(false);

  // ── 树形状态（TASK-004 §3.1 / §4.4.1 IssueTreeStore 语义：懒加载状态机 + 子级缓存 + 折叠记忆） ──
  /** 子级缓存：parentId -> Issue[]（顺序即 sort_order；懒加载 ?parent_id= 结果） */
  const [childrenByParent, setChildrenByParent] = useState<Record<string, Issue[]>>({});
  /** 展开状态机：idle（未加载）不落 key，loading/loaded/error */
  const [expandState, setExpandState] = useState<Record<string, ExpandPhase>>({});
  /** 折叠记忆（C.37 / E2E-06）：`issue-tree:collapsed:{projectId}` 存
   *  `{collapsed:[显式折叠过的节点], open:[用户展开过的节点]}`——默认全折叠（§4.4.1），
   *  open 集合用于刷新后逐层还原展开（懒加载世界无法从 collapsed 单集反推）。 */
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [openSet, setOpenSet] = useState<Set<string>>(new Set());
  /** 行内快速加子任务：目标行 + 输入草稿（C.38） */
  const [quickSubOf, setQuickSubOf] = useState<string | null>(null);
  const [quickSubDraft, setQuickSubDraft] = useState("");
  const quickSubInputRef = useRef<HTMLInputElement | null>(null);
  /** 「⊕ 展开 ▾」下拉（C.37） */
  const [expandMenuOpen, setExpandMenuOpen] = useState(false);
  /** 拖拽移动（C.39）：当前被拖行 + 落点高亮（落点样式用命令式 DOM，避免每像素 setState） */
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const dragIdRef = useRef<string | null>(null);
  const lastDropRowRef = useRef<HTMLTableRowElement | null>(null);
  /** 409 环：环上节点红高亮 2s（C.39 成环反馈） */
  const [cycleIds, setCycleIds] = useState<string[]>([]);
  /** 移动确认弹层（C.39）：拖拽与「移动到…」弹窗共用同一条 PATCH 链路 */
  const [confirmMove, setConfirmMove] = useState<{ fromId: string; toId: string; desc: number; loadingDesc: boolean } | null>(null);
  /** <768px 行菜单「移动到…」弹窗（C.63：触屏禁拖拽的替代路径） */
  const [rowMenuFor, setRowMenuFor] = useState<string | null>(null);
  const [moveModalFor, setMoveModalFor] = useState<string | null>(null);
  const [moveSearch, setMoveSearch] = useState("");
  /** 响应式断点（§3.7：<768px 拖拽禁用，改行菜单） */
  const [isMobile, setIsMobile] = useState(() => typeof window !== "undefined" && window.innerWidth < 768);

  // ── Sprint-3 Phase 3-A：视图切换器工具条 + 筛选入口（BOARD-003 §3.1 / TASK-011 §3.2）──
  const vp = useViewPage({ workspaceSlug, projectId, layout: "list" });

  // ── Sprint-2 列表新增（TASK-005/006/007/008/009 §3.3/§3.4/§3.5）──
  const stores = useStores();
  const myRole = stores.permission.effectiveProjectRole(projectId, workspaceSlug);
  const canWrite = myRole >= 15;
  /** C.51 成员表（头像堆叠姓名解析 + 快速指派候选） */
  const [members, setMembers] = useState<Array<{ id: string; user: { id: string; display_name: string; email: string }; role: number }>>([]);
  /** C.56 动态列：Schema 自定义字段 + 已选列（localStorage 持久化） */
  const [cfDefs, setCfDefs] = useState<CustomFieldDef[]>([]);
  const [cfCols, setCfCols] = useState<string[]>([]);
  /** C.48 工时列开关（默认开启，列选择器内切换） */
  const [wlColumn, setWlColumn] = useState(true);
  const [colMenuOpen, setColMenuOpen] = useState(false);
  /** C.45 依赖图标数据：id -> { blocked, count }（可见行 relations 聚合） */
  const [relInfo, setRelInfo] = useState<Record<string, { blocked: boolean; count: number }>>({});
  /** C.45 ?blocked 只看被阻塞（Chip 回显） */
  const [blockedFilter, setBlockedFilter] = useState(false);
  /** C.59 归档视图开关（?archived=true 反向查） */
  const [archivedView, setArchivedView] = useState(false);
  /** C.51 快速指派浮层（行悬浮头像区 → 单人快速选择） */
  const [quickAssignFor, setQuickAssignFor] = useState<string | null>(null);

  useEffect(() => {
    const mq = window.matchMedia("(max-width: 767px)");
    const onChange = () => setIsMobile(mq.matches);
    onChange();
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, []);

  const collapsedKey = `issue-tree:collapsed:${projectId}`;
  function persistTree(nextCollapsed: Set<string>, nextOpen: Set<string>) {
    try { localStorage.setItem(collapsedKey, JSON.stringify({ collapsed: [...nextCollapsed], open: [...nextOpen] })); } catch { /* 私有模式等 */ }
  }
  function setTreeOpen(id: string) {
    const c = new Set(collapsed); c.delete(id);
    const o = new Set(openSet); o.add(id);
    setCollapsed(c); setOpenSet(o); persistTree(c, o);
  }
  function setTreeCollapsed(id: string) {
    const c = new Set(collapsed); c.add(id);
    const o = new Set(openSet); o.delete(id);
    setCollapsed(c); setOpenSet(o); persistTree(c, o);
  }
  // 折叠记忆还原（刷新还原，C.37）
  useEffect(() => {
    try {
      const raw = localStorage.getItem(collapsedKey);
      const parsed = raw ? (JSON.parse(raw) as unknown) : null;
      if (Array.isArray(parsed)) { // 兼容仅折叠集的旧形态
        setCollapsed(new Set(parsed as string[]));
        setOpenSet(new Set());
      } else if (parsed && typeof parsed === "object") {
        const p = parsed as { collapsed?: string[]; open?: string[] };
        setCollapsed(new Set(p.collapsed ?? []));
        setOpenSet(new Set(p.open ?? []));
      } else {
        setCollapsed(new Set());
        setOpenSet(new Set());
      }
    } catch {
      setCollapsed(new Set());
      setOpenSet(new Set());
    }
    setChildrenByParent({});
    setExpandState({});
    setQuickSubOf(null);
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  /** 瞬态网络错误重试一次（dev 代理 ECONNRESET / runserver keep-alive 竞态实测存在）：
   *  静默吞掉错误会让列表页永久停留空态——重试一次仍失败则维持原静默语义（鉴权跳转由拦截器负责）。 */
  const loadRetriedRef = useRef(false);
  async function load() {
    try {
      const [iRes, pRes] = await Promise.all([
        // 排序参数名是 order_by（issue_query.apply_order 白名单）；树形模式下仅同层兄弟有序。
        // C.45 ?blocked / C.59 ?archived 为服务端筛选白名单参数（TASK-005 §4.2.5 / TASK-009 §4.2.4）。
        IssueAPI.list(workspaceSlug!, projectId!, {
          order_by: "sort_order",
          ...(blockedFilter ? { blocked: true } : {}),
          ...(archivedView ? { archived: true } : {}),
          // ②③ 视图层 + 临时层（BOARD-003 §4.2-6 / TASK-011 BR-12：三源恒 AND）
          ...(vp.viewIdParam ? { view_id: vp.viewIdParam } : {}),
          ...(vp.filtersParam ? { filters: vp.filtersParam } : {}),
        }),
        ProjectAPI.detail(workspaceSlug!, projectId!),
      ]);
      setIssues(((iRes as unknown as { data: Issue[] }).data));
      setProject(((pRes as unknown as { data: typeof project }).data));
      loadRetriedRef.current = false;
    } catch {
      if (!loadRetriedRef.current) {
        loadRetriedRef.current = true;
        setTimeout(() => { void load(); }, 600);
      }
      /* 鉴权失败由 axios 拦截器统一跳转；此处吃掉 unhandled rejection */
    }
  }
  useEffect(() => {
    const handle = setTimeout(() => { void load(); }, 0);
    return () => clearTimeout(handle);
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceSlug, projectId, blockedFilter, archivedView, vp.viewIdParam, vp.filtersParam]);

  // 成员表 + 字段 Schema（C.51 / C.56）：进入页面取一次
  useEffect(() => {
    ProjectMemberAPI.list(workspaceSlug!, projectId!, { per_page: 100 })
      .then((r) => setMembers(unwrap<typeof members>(r) ?? []))
      .catch(() => {});
    FieldAPI.schema(workspaceSlug!, projectId!)
      .then((r) => setCfDefs(unwrap<{ custom: CustomFieldDef[] }>(r)?.custom ?? []))
      .catch(() => setCfDefs([]));
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceSlug, projectId]);
  // 列选择持久化（C.56：工时列 + 自定义列）
  useEffect(() => {
    try {
      const raw = localStorage.getItem(`issue-list:cols:${projectId}`);
      if (raw) {
        const parsed = JSON.parse(raw) as { wl?: boolean; cf?: string[] };
        if (typeof parsed.wl === "boolean") setWlColumn(parsed.wl);
        if (Array.isArray(parsed.cf)) setCfCols(parsed.cf.filter((k) => k.startsWith("cf_")));
      }
    } catch { /* 私有模式 */ }
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);
  useEffect(() => {
    try { localStorage.setItem(`issue-list:cols:${projectId}`, JSON.stringify({ wl: wlColumn, cf: cfCols })); } catch { /* ignore */ }
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [wlColumn, cfCols, projectId]);

  /** C.45 依赖图标：可见行 relations 聚合（bounded：前 60 行；50/任务上限天然有界）。
   *  blocked = 存在未完成前置（is_blocked_by 且目标未完成/取消）；count = 任意关联数。 */
  const relTickRef = useRef(0);
  function loadRelationInfo(ids: string[]) {
    const bounded = ids.filter((id) => !id.startsWith("temp-")).slice(0, 60);
    if (!bounded.length) return;
    const tick = ++relTickRef.current;
    Promise.allSettled(bounded.map((id) => RelationAPI.list(workspaceSlug!, projectId!, id).then((r) => ({ id, rows: unwrap<RelationRow[]>(r) ?? [] }))))
      .then((results) => {
        if (tick !== relTickRef.current) return; // 过期响应丢弃
        const next: Record<string, { blocked: boolean; count: number }> = {};
        for (const res of results) {
          if (res.status !== "fulfilled") continue;
          const rows = res.value.rows;
          next[res.value.id] = {
            blocked: rows.some((x) => x.relation_type === "is_blocked_by"
              && x.related_issue.state_group !== "completed" && x.related_issue.state_group !== "cancelled"),
            count: rows.length,
          };
        }
        setRelInfo(next);
      });
  }
  const nameOf = (uid: string) => members.find((m) => m.user.id === uid)?.user.display_name ?? "…";
  /** C.51 认领（空集合才可；点击即 POST 无需确认） */
  async function claimRow(id: string) {
    try {
      await AssigneeAPI.claim(workspaceSlug!, projectId!, id);
      toast("已认领该任务", "ok");
      await load();
    } catch (e: unknown) {
      const err = e as ApiError;
      toast(err?.details?.[0]?.message ?? err?.message ?? "认领失败", "error");
    }
  }
  /** C.51 快速指派（单人快速选择 → PUT 全量集合追加） */
  async function quickAssign(issueId: string, userId: string) {
    setQuickAssignFor(null);
    const cur = (issueById.get(issueId)?.assignee_ids ?? []);
    if (cur.includes(userId)) return;
    try {
      await AssigneeAPI.put(workspaceSlug!, projectId!, issueId, { assignee_ids: [...cur, userId] });
      toast("执行人已更新", "ok");
      await load();
    } catch (e: unknown) {
      const err = e as ApiError;
      toast(err?.details?.[0]?.message ?? err?.message ?? "指派失败", "error");
    }
  }
  const [sp, setSp] = useSearchParams();
  const peekId = sp.get("peekIssue");
  function openPeek(id: string) { if (id.startsWith("temp-")) return; setSp((prev) => { const n = new URLSearchParams(prev); n.set("peekIssue", id); return n; }, { preventScrollReset: true }); }
  function closePeek() { setSp((prev) => { const n = new URLSearchParams(prev); n.delete("peekIssue"); return n; }, { preventScrollReset: true }); }

  // ── 派生：id 索引 / 根列表 / 可见行（DFS） ──
  const issueById = useMemo(() => {
    const m = new Map<string, Issue>();
    issues.forEach((i) => m.set(i.id, i));
    Object.values(childrenByParent).flat().forEach((i) => m.set(i.id, i));
    return m;
  }, [issues, childrenByParent]);
  /** C.59 归档视图：后端 ?archived=true 为「含归档」超集（T9-12 契约），UI 按原型口径只显示归档行。 */
  const listedIssues = useMemo(
    () => (archivedView ? issues.filter((i) => i.archived_at) : issues),
    [issues, archivedView],
  );
  const roots = useMemo(() => listedIssues.filter((i) => !i.parent_id), [listedIssues]);
  const hasKids = (it: Issue) => (it.sub_issues_count ?? 0) > 0 || (childrenByParent[it.id]?.length ?? 0) > 0;
  const isExpanded = (it: Issue) => Boolean(childrenByParent[it.id]?.length) && !collapsed.has(it.id);

  type VRow = { it: Issue; depth: number };
  const visibleRows = useMemo(() => {
    const out: VRow[] = [];
    const walk = (list: Issue[], depth: number) => {
      for (const it of list) {
        out.push({ it, depth });
        const kids = childrenByParent[it.id];
        if (kids?.length && !collapsed.has(it.id)) walk(kids, depth + 1);
      }
    };
    walk(roots, 1);
    return out;
  }, [roots, childrenByParent, collapsed]);


  /** 已加载子树内的后代集合（移动候选排除自身与后代，C.63；后端仍有 CTE 兜底） */
  function descendantsLoaded(id: string): Set<string> {
    const out = new Set<string>();
    const walk = (pid: string) => {
      for (const k of childrenByParent[pid] ?? []) { if (!out.has(k.id)) { out.add(k.id); walk(k.id); } }
    };
    walk(id);
    return out;
  }

  // ── 展开懒加载（§3.1：?parent_id=&order_by=sort_order&per_page=50） ──
  async function fetchChildren(parentId: string): Promise<Issue[]> {
    const r = await IssueAPI.list(workspaceSlug!, projectId!, { parent_id: parentId, order_by: "sort_order", per_page: 50 });
    return ((r as unknown as { data: Issue[] }).data) ?? [];
  }
  async function loadChildren(parentId: string, force = false) {
    if (!force && childrenByParent[parentId]) return;
    setExpandState((s) => ({ ...s, [parentId]: "loading" }));
    try {
      const kids = await fetchChildren(parentId);
      setChildrenByParent((prev) => ({ ...prev, [parentId]: kids }));
      setExpandState((s) => ({ ...s, [parentId]: "loaded" }));
    } catch {
      setExpandState((s) => ({ ...s, [parentId]: "error" }));
    }
  }
  function collapseRow(it: Issue) {
    setTreeCollapsed(it.id);
  }
  async function expandRow(it: Issue) {
    setTreeOpen(it.id);
    if (!childrenByParent[it.id]) await loadChildren(it.id); // 请求期间箭头转圈
  }
  async function toggleRow(it: Issue) {
    if (isExpanded(it)) collapseRow(it);
    else await expandRow(it);
  }

  /** 「全部展开（≤500 节点）」：DFS 逐层懒加载并展开（C.37） */
  async function expandAll() {
    setExpandMenuOpen(false);
    let budget = 500;
    const patch: Record<string, Issue[]> = {};
    const nextCollapsed = new Set(collapsed);
    const nextOpen = new Set(openSet);
    const queue: Issue[] = [...roots];
    while (queue.length && budget > 0) {
      const it = queue.shift()!;
      budget--;
      if ((it.sub_issues_count ?? 0) <= 0 && !(patch[it.id] ?? childrenByParent[it.id])?.length) continue;
      nextCollapsed.delete(it.id);
      nextOpen.add(it.id);
      let kids = patch[it.id] ?? childrenByParent[it.id];
      if (!kids) {
        try { kids = await fetchChildren(it.id); } catch { continue; }
        patch[it.id] = kids;
      }
      queue.push(...kids);
    }
    setChildrenByParent((prev) => ({ ...prev, ...patch }));
    setCollapsed(nextCollapsed);
    setOpenSet(nextOpen);
    persistTree(nextCollapsed, nextOpen);
  }

  /** 「收起到第 N 层」：浅层确保展开、已知深层（含缓存里的）全部折叠（原型语义：S.expanded={} 清空） */
  async function collapseToLevel(level: number) {
    setExpandMenuOpen(false);
    const nextCollapsed = new Set<string>();
    const nextOpen = new Set<string>();
    const patch: Record<string, Issue[]> = {};
    // < level 的层：确保已加载并标记展开
    const ensure = async (list: Issue[], depth: number) => {
      for (const it of list) {
        if (depth >= level) continue;
        let k = patch[it.id] ?? childrenByParent[it.id];
        if (!k && (it.sub_issues_count ?? 0) > 0) {
          try { k = await fetchChildren(it.id); } catch { k = undefined; }
          if (k) patch[it.id] = k;
        }
        if ((it.sub_issues_count ?? 0) > 0 || (k?.length ?? 0) > 0) nextOpen.add(it.id);
        if (k) await ensure(k, depth + 1);
      }
    };
    await ensure(roots, 1);
    // 已知有子级的节点：depth >= level 一律进折叠集（沿缓存下钻，不漏深层）
    const markCollapsed = (list: Issue[], depth: number) => {
      for (const it of list) {
        const k = patch[it.id] ?? childrenByParent[it.id];
        if ((it.sub_issues_count ?? 0) > 0 || (k?.length ?? 0) > 0) {
          if (depth >= level) nextCollapsed.add(it.id);
          if (k) markCollapsed(k, depth + 1);
        }
      }
    };
    markCollapsed(roots, 1);
    setChildrenByParent((prev) => ({ ...prev, ...patch }));
    setCollapsed(nextCollapsed);
    setOpenSet(nextOpen);
    persistTree(nextCollapsed, nextOpen);
  }

  async function quickCreate() {
    if (!quick.trim() || creating) return;
    const text = quick;
    setCreating(true); setQuick("");
    try {
      await IssueAPI.create(workspaceSlug!, projectId!, { name: text });
      await load();
    } catch (e: unknown) {
      setQuick(text); // 失败恢复输入（TASK-001 §3.2.1）
      const msg = e instanceof Error ? e.message : "创建失败";
      toast(msg, "error");
    } finally {
      setCreating(false);
      document.getElementById("tq-input")?.focus();
    }
  }

  // ── 行内快速加子任务（C.38 / §3.2）：乐观临时行 → 成功替换 / 失败回填 ──
  async function submitQuickSub(parentId: string) {
    const name = quickSubDraft.trim();
    if (!name) return;
    const tempId = `temp-${Date.now()}`;
    // 乐观临时行（opacity-60、编号 …）：渲染只读少数字段，构造最小 Issue
    const temp = {
      id: tempId, issue_key: "…", name, parent_id: parentId,
      state_group: "unstarted", state_name: "…", state_id: "",
      sub_issues_count: 0, completed_sub_issues_count: 0,
      sort_order: Number.MAX_SAFE_INTEGER, target_date: null,
    } as unknown as Issue;
    setChildrenByParent((prev) => ({ ...prev, [parentId]: [...(prev[parentId] ?? []), temp] }));
    setTreeOpen(parentId);
    setQuickSubDraft("");
    try {
      const r = await IssueAPI.createSubIssue(workspaceSlug!, projectId!, parentId, { name });
      const real = unwrap<Issue>(r);
      setChildrenByParent((prev) => ({ ...prev, [parentId]: (prev[parentId] ?? []).map((k) => (k.id === tempId ? real : k)) }));
      // 父徽标乐观 +1（列表行 sub_issues_count 本地递增；x/y 唯一来源是 annotate，此处即其镜像）
      setIssues((prev) => prev.map((i) => (i.id === parentId ? { ...i, sub_issues_count: (i.sub_issues_count ?? 0) + 1 } : i)));
      toast(`已创建子任务 ${real.issue_key}`);
    } catch (e: unknown) {
      // 失败：移除临时行 + 内容恢复输入框 + Toast error.message（C.38）
      setChildrenByParent((prev) => ({ ...prev, [parentId]: (prev[parentId] ?? []).filter((k) => k.id !== tempId) }));
      setQuickSubDraft(name);
      setQuickSubOf(parentId);
      toast(e instanceof Error ? e.message : "创建失败", "error");
    }
  }

  // ── 拖拽移动子树（C.39 / §4.4.2 双区判定） ──
  function clearDropClasses(keep?: HTMLTableRowElement) {
    if (lastDropRowRef.current && lastDropRowRef.current !== keep) {
      lastDropRowRef.current.classList.remove("row-drop-nest", "row-drop-reorder");
    }
    if (!keep) lastDropRowRef.current = null;
  }
  function onRowDragOver(e: React.DragEvent<HTMLTableRowElement>, it: Issue) {
    if (!dragIdRef.current || dragIdRef.current === it.id) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    const rect = e.currentTarget.getBoundingClientRect();
    const nest = (e.clientY - rect.top) / rect.height > DROP_EDGE_RATIO;
    e.currentTarget.classList.toggle("row-drop-nest", nest);
    e.currentTarget.classList.toggle("row-drop-reorder", !nest);
    clearDropClasses(e.currentTarget);
    lastDropRowRef.current = e.currentTarget;
  }
  function onRowDrop(e: React.DragEvent<HTMLTableRowElement>, it: Issue) {
    e.preventDefault();
    const from = dragIdRef.current;
    clearDropClasses();
    if (!from || from === it.id) return; // 自拖自 = 无操作（前端判定）
    const rect = e.currentTarget.getBoundingClientRect();
    const nest = (e.clientY - rect.top) / rect.height > DROP_EDGE_RATIO;
    if (nest) void openConfirmMove(from, it.id);
    else void doReorder(from, it);
  }

  /** 上沿 25% 排序区：同级排序（复用 sort_order 插值；跨父时随行携带 parent_id 变更） */
  async function doReorder(fromId: string, target: Issue) {
    const parentId = target.parent_id ?? "";
    const siblings = parentId === "" ? roots : (childrenByParent[parentId] ?? []);
    const idx = siblings.findIndex((s) => s.id === target.id);
    if (idx < 0) return;
    const prev = siblings[idx - 1];
    const prevSort = prev ? prev.sort_order : target.sort_order - 65535;
    const newSort = (prevSort + target.sort_order) / 2;
    const from = issueById.get(fromId);
    const payload: Parameters<typeof IssueAPI.patch>[3] = { sort_order: newSort };
    if (from && (from.parent_id ?? "") !== parentId) payload.parent_id = parentId === "" ? null : parentId;
    const oldParent = from?.parent_id ?? null;
    try {
      await IssueAPI.patch(workspaceSlug!, projectId!, fromId, payload);
      toast("已调整顺序");
      await load();
      // 目标层与旧父缓存都刷新：跨父移动时旧行残留会导致 React duplicate key
      if (childrenByParent[parentId] || parentId === "") await loadChildren(parentId, true);
      if (oldParent && oldParent !== (parentId === "" ? null : parentId) && childrenByParent[oldParent]) {
        await loadChildren(oldParent, true).catch(() => {});
      }
    } catch (e: unknown) {
      toast(e instanceof Error ? e.message : "调整顺序失败", "error");
    }
  }

  /** 确认弹层：先取 subtree stats 的后代数（含 0；与后端口径同源），再弹「将 “X”（含 N 个后代）移动到 “Y” 之下？」 */
  async function openConfirmMove(fromId: string, toId: string) {
    setConfirmMove({ fromId, toId, desc: 0, loadingDesc: true });
    try {
      const r = await IssueAPI.subtree(workspaceSlug!, projectId!, fromId);
      const st = unwrap<SubtreeData>(r);
      setConfirmMove((c) => (c && c.fromId === fromId ? { ...c, desc: Math.max(0, (st.stats?.total ?? 1) - 1), loadingDesc: false } : c));
    } catch {
      setConfirmMove((c) => (c ? { ...c, desc: descendantsLoaded(fromId).size, loadingDesc: false } : c));
    }
  }

  async function executeMove() {
    const cm = confirmMove;
    if (!cm) return;
    setConfirmMove(null);
    const from = issueById.get(cm.fromId);
    const to = issueById.get(cm.toId);
    const oldParentId = from?.parent_id ?? null;
    try {
      await IssueAPI.patch(workspaceSlug!, projectId!, cm.fromId, { parent_id: cm.toId });
      // 依响应重排树：从旧父移除、加入新父、展开新父（乐观隐藏原位置在确认阶段已完成）
      setChildrenByParent((prev) => {
        const next: Record<string, Issue[]> = {};
        for (const [pid, kids] of Object.entries(prev)) next[pid] = kids.filter((k) => k.id !== cm.fromId);
        if (from) next[cm.toId] = [...(next[cm.toId] ?? []), from];
        return next;
      });
      setTreeOpen(cm.toId);
      toast(`已移动到「${to?.name ?? "…"}」之下`);
      await load();
      // 原父/新父计数与行数据 revalidate：被移行的旧父、旧父的父（旧父行自身的 x/y 藏在其父的
      // children 缓存里）、新父——三处缓存都要重取，否则旧父行徽标停留旧值
      const refresh = new Set<string>([cm.toId]);
      if (oldParentId) {
        refresh.add(oldParentId);
        const grand = issueById.get(oldParentId)?.parent_id;
        if (grand) refresh.add(grand);
      }
      for (const pid of refresh) {
        await loadChildren(pid, true).catch(() => {});
      }
    } catch (e: unknown) {
      const err = e as ApiError;
      // 409 CYCLE：details[0].message 直出环路径（服务端已渲染人类可读路径）+ 环上节点红高亮 2s
      const cycleDetail = err?.details?.find?.((d) => d?.code === "CYCLE")?.message;
      toast(cycleDetail || err?.message || (e instanceof Error ? e.message : "移动失败"), "error");
      if (err?.code === "RESOURCE_CIRCULAR_DEPENDENCY") {
        setCycleIds([cm.fromId, cm.toId]);
        setTimeout(() => setCycleIds([]), 2000);
      }
    }
  }

  // 键盘导航（§3.7：← 折叠 / → 展开 / ↑↓ 移动 / Home/End 首末 / Enter 打开详情）
  function onRowKeyDown(e: React.KeyboardEvent<HTMLTableRowElement>) {
    const keys = ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "Enter"];
    if (!keys.includes(e.key)) return;
    const row = e.currentTarget;
    const id = row.dataset.id ?? "";
    const it = issueById.get(id);
    if (e.key === "Enter") { e.preventDefault(); openPeek(id); return; }
    if (e.key === "ArrowRight" && it && hasKids(it) && !isExpanded(it)) { e.preventDefault(); void expandRow(it); return; }
    if (e.key === "ArrowLeft" && it && isExpanded(it)) { e.preventDefault(); collapseRow(it); return; }
    const rows = Array.from(document.querySelectorAll<HTMLTableRowElement>("tr[data-tree-row]"));
    const idx = rows.indexOf(row);
    if (e.key === "ArrowUp" && idx > 0) { e.preventDefault(); rows[idx - 1]?.focus(); }
    if (e.key === "ArrowDown" && idx >= 0 && idx < rows.length - 1) { e.preventDefault(); rows[idx + 1]?.focus(); }
    if (e.key === "Home" && rows.length) { e.preventDefault(); rows[0]?.focus(); }
    if (e.key === "End" && rows.length) { e.preventDefault(); rows[rows.length - 1]?.focus(); }
  }

  /** 刷新还原（E2E-06 / C.37 折叠记忆）：open 集合里的节点重新懒加载并逐层下钻 */
  useEffect(() => {
    const tryRestore = (id: string, count: number) => {
      if (count > 0 && openSet.has(id) && !collapsed.has(id) && !childrenByParent[id] && expandState[id] === undefined) {
        void loadChildren(id);
      }
    };
    roots.forEach((r) => tryRestore(r.id, r.sub_issues_count ?? 0));
    for (const [pid, kids] of Object.entries(childrenByParent)) {
      if (collapsed.has(pid)) continue;
      kids.forEach((k) => tryRestore(k.id, k.sub_issues_count ?? 0));
    }
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [roots, childrenByParent, openSet, collapsed, expandState]);

  // 弹层/菜单外点击关闭（CLAUDE.md 教训 #4：mousedown 阶段 + closest 判 scope）
  useEffect(() => {    if (!expandMenuOpen && rowMenuFor == null && !colMenuOpen && quickAssignFor == null) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (expandMenuOpen && !t?.closest('[data-sb-scope="tree-expand-dd"]')) setExpandMenuOpen(false);
      if (rowMenuFor != null && !t?.closest('[data-sb-scope="tree-row-menu-wrap"]')) setRowMenuFor(null);
      if (colMenuOpen && !t?.closest('[data-sb-scope="list-cols-dd"]')) setColMenuOpen(false);
      if (quickAssignFor != null && !t?.closest('[data-sb-scope="list-quick-assign-pop"]') && !t?.closest('[data-sb-scope="list-quick-assign"]') && !t?.closest('[data-sb-scope="list-unassigned"]')) setQuickAssignFor(null);
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [expandMenuOpen, rowMenuFor, colMenuOpen, quickAssignFor]);

  // 确认弹层 Esc 取消（C.39：聚焦陷阱，Esc 取消并还原）
  useEffect(() => {
    if (!confirmMove) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setConfirmMove(null); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [confirmMove]);

  // 服务端筛选参数（占位 — 当前接口未全量接；保留 UI 入口，未来由 IssueAPI.list 透传）
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q && chips.length === 0) return listedIssues;
    return listedIssues.filter((it) => {
      if (q && !(it.name.toLowerCase().includes(q) || it.issue_key.toLowerCase().includes(q))) return false;
      for (const c of chips) {
        if (c.k === "state" && it.state_name !== c.v) return false;
      }
      return true;
    });
  }, [listedIssues, search, chips]);
  /** 筛选态走平铺（全局视角）；清空筛选回到树形（§3.1 排序语义：树形 order_by 仅同层兄弟） */
  const filterActive = Boolean(search.trim() || chips.length);

  /** C.45 依赖图标：可见行变化时拉取 relations 聚合（过期响应丢弃）。 */
  useEffect(() => {
    const ids = filterActive ? filtered.map((i) => i.id) : visibleRows.map((r) => r.it.id);
    loadRelationInfo(ids);
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleRows, filtered, filterActive]);

  const total = issues.length;

  /** 「移动到…」弹窗候选（C.63：排除自身与后代；显示第 N 层） */
  const moveCandidates = useMemo(() => {
    if (!moveModalFor) return [];
    const forbidden = descendantsLoaded(moveModalFor);
    forbidden.add(moveModalFor);
    const q = moveSearch.trim().toLowerCase();
    const pool = [...issueById.values()];
    return pool
      .filter((i) => !forbidden.has(i.id) && !i.id.startsWith("temp-"))
      .filter((i) => !q || i.name.toLowerCase().includes(q) || i.issue_key.toLowerCase().includes(q))
      .slice(0, 8);
  }, [moveModalFor, moveSearch, issueById, childrenByParent]);

  /** 节点在已加载树中的深度（根=1）——「移动到…」候选行「第 N 层」与「＋」预判共用 */
  function depthLoaded(id: string): number {
    const walk = (list: Issue[], d: number): number => {
      for (const it of list) {
        if (it.id === id) return d;
        const kids = childrenByParent[it.id];
        if (kids?.length && !collapsed.has(it.id)) {
          const found = walk(kids, d + 1);
          if (found > 0) return found;
        }
      }
      return -1;
    };
    const hit = walk(roots, 1);
    if (hit > 0) return hit;
    // 折叠分支里也要能算（walk 不下钻折叠节点）：沿 parent 链上溯
    let cur = issueById.get(id);
    let d = 1;
    while (cur?.parent_id) { cur = issueById.get(cur.parent_id); d++; }
    return d;
  }

  // 行渲染（树形/平铺共用；tree=false 时无折叠箭头与悬浮操作位）
  function renderRow(it: Issue, depth: number, tree: boolean) {
    const kids = tree ? hasKids(it) : false;
    const exp = tree ? isExpanded(it) : false;
    const loading = tree && expandState[it.id] === "loading";
    const errored = tree && expandState[it.id] === "error";
    const isTemp = it.id.startsWith("temp-");
    const overdue = it.target_date && it.target_date < today() && it.state_group !== "completed";
    const y = it.sub_issues_count ?? 0;
    const x = it.completed_sub_issues_count ?? 0;
    const full = y > 0 && x === y;
    return (
      <Fragment key={it.id}>
        <tr
          data-sb-scope="tree-row"
          data-tree-row=""
          data-id={it.id}
          role="treeitem"
          aria-level={depth}
          aria-expanded={kids ? exp : undefined}
          tabIndex={0}
          className={`group cursor-pointer hover:bg-neutral-50 ${isTemp ? "opacity-60" : ""} ${it.archived_at ? "opacity-60 hover:opacity-85" : ""} ${cycleIds.includes(it.id) ? "cycle-flash" : ""} ${draggingId === it.id ? "row-dragging" : ""}`}
          onClick={() => openPeek(it.id)}
          onKeyDown={onRowKeyDown}
          onDragOver={tree && !isMobile ? (e) => onRowDragOver(e, it) : undefined}
          onDrop={tree && !isMobile ? (e) => onRowDrop(e, it) : undefined}
        >
          <td className="px-3 py-2.5 border-b border-neutral-100 whitespace-nowrap">
            <span className="inline-block align-middle" style={{ width: tree ? (depth - 1) * 20 : 0 }} aria-hidden="true" />
            {tree && (kids ? (
              <button
                data-sb-scope="tree-toggle"
                aria-label={exp ? "折叠子任务" : "展开子任务"}
                aria-expanded={exp}
                onClick={(e) => { e.stopPropagation(); void toggleRow(it); }}
                className="inline-flex w-4 h-4 items-center justify-center text-neutral-400 mr-1 align-middle hover:text-neutral-700"
              >
                {loading ? (
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="animate-spin"><path d="M21 12a9 9 0 1 1-6.22-8.56"/></svg>
                ) : (
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="transition-transform duration-150 ease-out" style={{ transform: exp ? "rotate(90deg)" : undefined }}><path d="m9 18 6-6-6-6"/></svg>
                )}
              </button>
            ) : (
              <span className="inline-block w-4 mr-1" aria-hidden="true" />
            ))}
            <button className="font-mono text-xs text-neutral-500 hover:text-brand-600"
              onClick={(e) => {
                e.stopPropagation();
                navigator.clipboard?.writeText(it.issue_key)
                  .then(() => toast(`已复制 ${it.issue_key}`))
                  .catch(() => toast(`复制失败：${it.issue_key}`, "error"));
              }}
              title="点击复制编号">{it.issue_key}</button>
          </td>
          <td className={`px-3 py-2.5 border-b border-neutral-100 text-[13px] relative ${tree && depth > 1 ? "border-l border-neutral-200" : ""}`}>
            <span className="flex items-center gap-1 min-w-0">
              {/* C.45 列表行依赖图标：标题左侧 12px link/alert 图标（存在任意关联时；被阻塞 amber） */}
              {(relInfo[it.id]?.count ?? 0) > 0 && (
                <span
                  className={relInfo[it.id]?.blocked ? "text-amber-500" : "text-neutral-400"}
                  title={relInfo[it.id]?.blocked ? "被未完成前置阻塞" : "存在关联"}
                  aria-label={relInfo[it.id]?.blocked ? "被未完成前置阻塞" : "存在关联"}
                  data-sb-scope="list-rel-icon"
                >
                  {!isTemp && (relInfo[it.id]?.blocked
                    ? <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4M12 17h.01"/></svg>
                    : <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>)}
                </span>
              )}
              {/* C.59 归档行：archive 图标（带文本 tooltip） */}
              {it.archived_at && (
                <span className="text-neutral-400" title="已归档" aria-label="已归档" data-sb-scope="list-arch-icon">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect width="20" height="5" x="2" y="3" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8M10 12h4"/></svg>
                </span>
              )}
              <span className={`truncate ${tree && depth === 1 ? "font-medium" : ""}`}>{it.name}</span>
              {isTemp && <span className="ml-1 font-mono text-[11px] text-neutral-400">…</span>}
              {/* 行悬浮「＋」（C.37/C.38）：第 5 层不渲染（前端预判，E2E-04） */}
              {tree && depth < MAX_ISSUE_DEPTH && !isTemp && (
                <button
                  data-sb-scope="tree-quick-add"
                  aria-label="在下方添加子任务"
                  title="添加子任务"
                  onClick={(e) => { e.stopPropagation(); setQuickSubOf(it.id); setQuickSubDraft(""); }}
                  className="opacity-0 group-hover:opacity-100 transition-opacity ml-1 w-[22px] h-[22px] inline-flex items-center justify-center rounded text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700"
                >＋</button>
              )}
              {/* 拖拽把手（C.39：≥768px；<768px 拖拽禁用改行菜单「移动到…」） */}
              {tree && !isMobile && !isTemp && (
                <span
                  data-sb-scope="tree-grip"
                  draggable
                  title="拖拽移动子树"
                  aria-label="拖拽移动子树"
                  onDragStart={(e) => {
                    dragIdRef.current = it.id;
                    setDraggingId(it.id);
                    e.dataTransfer.setData("text/plain", `row:${it.id}`);
                    e.dataTransfer.effectAllowed = "move";
                  }}
                  onDragEnd={() => { dragIdRef.current = null; setDraggingId(null); clearDropClasses(); }}
                  className="opacity-0 group-hover:opacity-100 transition-opacity ml-1 w-[20px] h-[22px] inline-flex items-center justify-center cursor-grab active:cursor-grabbing select-none text-neutral-300"
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M5 8h14M5 16h14"/></svg>
                </span>
              )}
              {/* <768px 行菜单（C.63 触发条件） */}
              {tree && isMobile && !isTemp && (
                <span className="relative ml-auto" data-sb-scope="tree-row-menu-wrap">
                  <button
                    data-sb-scope="tree-row-menu"
                    aria-label="行操作"
                    onClick={(e) => { e.stopPropagation(); setRowMenuFor(rowMenuFor === it.id ? null : it.id); }}
                    className="w-7 h-7 inline-flex items-center justify-center text-neutral-500"
                  >⋯</button>
                  {rowMenuFor === it.id && (
                    <div role="menu" className="absolute right-0 top-7 z-20 w-[150px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1">
                      <button role="menuitem" data-sb-scope="tree-row-move"
                        onClick={(e) => { e.stopPropagation(); setRowMenuFor(null); setMoveSearch(""); setMoveModalFor(it.id); }}
                        className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50">移动到…</button>
                    </div>
                  )}
                </span>
              )}
            </span>
            {/* 展开加载失败：该层行内「加载失败 · 重试」（§3.6） */}
            {tree && errored && (
              <div className="mt-1 text-[12px] text-red-500" data-sb-scope="tree-expand-error">
                加载失败 · <button className="underline" onClick={(e) => { e.stopPropagation(); void loadChildren(it.id, true); }}>重试</button>
              </div>
            )}
          </td>
          <td className="px-3 py-2.5 border-b border-neutral-100"><StateBadge group={it.state_group ?? "unstarted"} name={it.state_name ?? "—"} /></td>
          {/* C.51 列表负责人列：20px 头像堆叠；空显示「未指派」neutral 徽标（可点开快速指派）；悬浮「🖐」认领 */}
          <td className="px-3 py-2.5 border-b border-neutral-100">
            {it.assignee_ids && it.assignee_ids.length > 0 ? (
              <span className="inline-flex items-center gap-1 group/av flex-nowrap whitespace-nowrap">
                <AvatarStack names={it.assignee_ids.map(nameOf)} size={20} />
                {canWrite && !it.archived_at && (
                  <span className="relative">
                    <button
                      data-sb-scope="list-quick-assign"
                      aria-label={`快速指派 ${it.name}`}
                      className="opacity-0 group-hover/av:opacity-100 transition-opacity w-5 h-5 rounded inline-flex items-center justify-center text-neutral-400 hover:bg-brand-50 hover:text-brand-600 text-[12px] shrink-0"
                      onClick={(e) => { e.stopPropagation(); setQuickAssignFor(quickAssignFor === it.id ? null : it.id); }}
                    >＋</button>
                    {quickAssignFor === it.id && (
                      <QuickAssignPop members={members} onPick={(uid) => void quickAssign(it.id, uid)} />
                    )}
                  </span>
                )}
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 group/av flex-nowrap whitespace-nowrap">
                <button
                  className="inline-flex items-center gap-1 rounded-full bg-neutral-100 px-2 py-0.5 text-[12px] text-neutral-500 hover:bg-neutral-200 shrink-0"
                  aria-label="未指派"
                  data-sb-scope="list-unassigned"
                  onClick={(e) => { e.stopPropagation(); if (canWrite && !it.archived_at) setQuickAssignFor(quickAssignFor === it.id ? null : it.id); }}
                >未指派</button>
                {canWrite && !it.archived_at && !isTemp && (
                  <>
                    <button
                      data-sb-scope="list-claim"
                      aria-label="认领该任务"
                      title="认领该任务"
                      className="opacity-0 group-hover/av:opacity-100 transition-opacity w-6 h-6 rounded inline-flex items-center justify-center text-neutral-400 hover:bg-brand-50 hover:text-brand-600 shrink-0"
                      onClick={(e) => { e.stopPropagation(); void claimRow(it.id); }}
                    >🖐</button>
                    <span className="relative">
                      <button
                        data-sb-scope="list-quick-assign"
                        aria-label={`快速指派 ${it.name}`}
                        className="opacity-0 group-hover/av:opacity-100 transition-opacity w-5 h-5 rounded inline-flex items-center justify-center text-neutral-400 hover:bg-brand-50 hover:text-brand-600 text-[12px] shrink-0"
                        onClick={(e) => { e.stopPropagation(); setQuickAssignFor(quickAssignFor === it.id ? null : it.id); }}
                      >＋</button>
                      {quickAssignFor === it.id && (
                        <QuickAssignPop members={members} onPick={(uid) => void quickAssign(it.id, uid)} />
                      )}
                    </span>
                  </>
                )}
              </span>
            )}
          </td>
          <td className="px-3 py-2.5 border-b border-neutral-100 text-[13px]">
            {it.target_date ? (
              overdue
                ? <span className="text-red-500 inline-flex items-center gap-1"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>{it.target_date}</span>
                : <span className="font-mono text-xs text-neutral-500">{it.target_date}</span>
            ) : <span className="text-neutral-400">—</span>}
          </td>
          {/* C.48 工时列：⏱ 5.5/8h；超耗红；无估算仅 ⏱ 5.5h；无记录灰显 — */}
          {wlColumn && (
            <td className="px-3 py-2.5 border-b border-neutral-100 w-[96px]" data-sb-scope="list-wl-cell">
              {(() => {
                const spent = it.spent_minutes ?? 0;
                const est = it.estimate_minutes ?? null;
                if (!spent && est == null) return <span className="text-neutral-400">—</span>;
                const over = est != null && spent > est;
                return (
                  <span className={`inline-flex items-center gap-1 text-[12px] tabular-nums ${over ? "text-red-600" : "text-neutral-600"}`}
                    title={est != null ? `估算 ${fmtMinutes(est)}` : undefined}>
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="14" r="8"/><path d="M12 14l3-3M10 2h4"/></svg>
                    {est != null ? `${fmtMinutes(spent)}/${fmtMinutes(est)}` : fmtMinutes(spent)}
                  </span>
                );
              })()}
            </td>
          )}
          {/* C.56 动态列值渲染：select=色块文本 / member=头像 / date=yyyy-MM-dd / currency=¥ / multi=chips 前 2+N */}
          {cfCols.map((key) => {
            const f = cfDefs.find((d) => d.key === key);
            const v = ((it as unknown as { custom_fields?: Record<string, unknown> }).custom_fields ?? {})[key];
            return (
              <td key={key} className="px-3 py-2.5 border-b border-neutral-100 w-[110px]" data-sb-scope="list-cf-cell">
                <CfCell field={f} value={v} nameOf={nameOf} />
              </td>
            );
          })}
          {/* 子任务进度列（C.37：独立列 88px；直接子级口径 annotate；无子级 —） */}
          <td className="px-3 py-2.5 border-b border-neutral-100 w-[88px]">
            {y > 0 ? (
              <span className="inline-flex items-center gap-1.5 text-[12px] text-neutral-600 tabular-nums" role="img"
                aria-label={`子任务 ${y} 个，已完成 ${x} 个`} data-sb-scope="tree-sub-progress">
                <span className="w-4 h-4 rounded-full inline-block shrink-0"
                  style={full
                    ? { border: "2px solid #10b981", background: "#10b981" }
                    : { border: "2px solid #e5e5e5", borderTopColor: x > 0 ? "#10b981" : "#9ca3af" }} />
                {x}/{y}
              </span>
            ) : <span className="text-neutral-400">—</span>}
          </td>
        </tr>
        {/* 行内快速加子任务输入行（C.38：目标行下方插入，缩进对齐目标行子级） */}
        {tree && quickSubOf === it.id && (
          <tr data-sb-scope="tree-quick-sub-row">
            <td colSpan={6 + (wlColumn ? 1 : 0) + cfCols.length} className="border-b border-neutral-100 py-1">
              <div className="flex items-center gap-2 h-[34px] px-2.5 my-1 border border-dashed border-neutral-300 rounded-md text-neutral-500 focus-within:border-brand-500 focus-within:bg-white transition-colors"
                style={{ marginLeft: depth * 20 }}>
                <span className="text-[13px]">＋</span>
                <input
                  ref={quickSubInputRef}
                  data-sb-scope="tree-quick-input"
                  aria-label="子任务标题"
                  className="flex-1 bg-transparent outline-none text-[13px] text-neutral-900"
                  placeholder="输入子任务标题后按回车…"
                  value={quickSubDraft}
                  onChange={(e) => setQuickSubDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") { e.preventDefault(); void submitQuickSub(it.id); }
                    if (e.key === "Escape") { e.preventDefault(); setQuickSubOf(null); }
                  }}
                />
                <span className="text-[12px] text-neutral-400 shrink-0">Enter 保存 · Esc 取消</span>
              </div>
            </td>
          </tr>
        )}
      </Fragment>
    );
  }

  // 快速行打开时聚焦（连续录入）
  useEffect(() => {
    if (quickSubOf) quickSubInputRef.current?.focus();
  }, [quickSubOf]);

  return (
    <div className="flex flex-col h-screen">
      <style>{TREE_CSS}</style>
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <ProjectSidebar projectName={project?.name ?? "…"} identifier={project?.identifier ?? ""} />
        <main className="flex-1 min-w-0 flex flex-col overflow-hidden">
          <div className="h-[53px] border-b border-neutral-200 flex items-center gap-2 px-5 bg-white shrink-0">
            <div><span className="text-[15px] font-semibold">任务列表</span>
              <span className="text-[13px] text-neutral-500 ml-2">{total} 个任务</span></div>
            {/* C.59 归档视图入口：列表工具条「显示已归档」→ ?archived=true（URL 同源由 load 参数承载） */}
            <button
              onClick={() => setArchivedView((v) => !v)}
              data-sb-scope="list-archived-toggle"
              aria-pressed={archivedView}
              className={`ml-auto h-[34px] px-2.5 inline-flex items-center gap-1.5 border rounded-md text-[13px] ${archivedView ? "border-brand-500 text-brand-600 bg-brand-50" : "border-neutral-300 text-neutral-700 hover:bg-neutral-50"}`}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect width="20" height="5" x="2" y="3" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8M10 12h4"/></svg>
              显示已归档
            </button>
            {/* C.45 ?blocked 筛选：只看被阻塞任务（Chip 回显） */}
            <button
              onClick={() => setBlockedFilter((v) => !v)}
              data-sb-scope="list-blocked-toggle"
              aria-pressed={blockedFilter}
              className={`h-[34px] px-2.5 inline-flex items-center gap-1.5 border rounded-md text-[13px] ${blockedFilter ? "border-amber-400 text-amber-700 bg-amber-50" : "border-neutral-300 text-neutral-700 hover:bg-neutral-50"}`}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4M12 17h.01"/></svg>
              只看被阻塞
            </button>
            {/* C.56 列选择器：「列」菜单（工时开关 + 全部自定义字段） */}
            <div className="relative" data-sb-scope="list-cols-dd">
              <button
                aria-haspopup="menu" aria-label="列选择器"
                onClick={() => setColMenuOpen((v) => !v)}
                data-sb-scope="list-cols-toggle"
                className="h-[34px] px-2.5 inline-flex items-center gap-1.5 border border-neutral-300 rounded-md text-[13px] text-neutral-700 hover:bg-neutral-50"
              >列 ▾</button>
              {colMenuOpen && (
                <div role="menu" data-sb-scope="list-cols-menu" aria-label="列选择"
                  className="absolute top-[38px] right-0 z-20 w-[220px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1 max-h-[300px] overflow-y-auto">
                  <button role="menuitem" onClick={() => setWlColumn((v) => !v)} data-sb-scope="list-cols-wl"
                    className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50 flex items-center gap-2">
                    <span className={`w-3.5 h-3.5 rounded border inline-flex items-center justify-center text-[10px] ${wlColumn ? "bg-brand-500 border-brand-500 text-white" : "border-neutral-300"}`}>{wlColumn ? "✓" : ""}</span>
                    工时
                  </button>
                  <div className="h-px bg-neutral-100 my-1" />
                  {cfDefs.filter((d) => d.is_active !== false).map((d) => {
                    const on = cfCols.includes(d.key);
                    return (
                      <button key={d.id} role="menuitem" data-sb-scope="list-cols-cf"
                        onClick={() => setCfCols((cur) => (on ? cur.filter((k) => k !== d.key) : [...cur, d.key]))}
                        className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50 flex items-center gap-2">
                        <span className={`w-3.5 h-3.5 rounded border inline-flex items-center justify-center text-[10px] ${on ? "bg-brand-500 border-brand-500 text-white" : "border-neutral-300"}`}>{on ? "✓" : ""}</span>
                        {d.name}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
            {/* 「⊕ 展开 ▾」下拉（C.37：三项） */}
            <div className="relative" data-sb-scope="tree-expand-dd">
              <button
                aria-haspopup="menu"
                aria-label="展开控制"
                onClick={() => setExpandMenuOpen((v) => !v)}
                className="h-[34px] px-2.5 inline-flex items-center gap-1.5 border border-neutral-300 rounded-md text-[13px] text-neutral-700 hover:bg-neutral-50"
              >⊕ 展开 ▾</button>
              {expandMenuOpen && (
                <div role="menu" data-sb-scope="tree-expand-menu" aria-label="展开控制"
                  className={isMobile
                    // <768px：main overflow-hidden 会裁掉绝对定位菜单，改 fixed 挂载（§3.7 响应式）
                    ? "fixed right-3 top-[56px] z-30 w-[190px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1"
                    : "absolute top-[38px] right-0 z-20 w-[190px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1"}>
                  <button role="menuitem" onClick={() => void expandAll()} className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50">全部展开（≤500 节点）</button>
                  <button role="menuitem" onClick={() => void collapseToLevel(1)} className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50">收起到第 1 层</button>
                  <button role="menuitem" onClick={() => void collapseToLevel(2)} className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50">收起到第 2 层</button>
                </div>
              )}
            </div>
            <button onClick={() => setShowTaskModal(true)} className="inline-flex h-[34px] items-center gap-1.5 px-3.5 bg-brand-500 text-white rounded-md font-medium hover:bg-brand-600">+ 创建任务</button>
          </div>
          {/* 视图切换器工具条（BOARD-003 §3.1 / TASK-011 §3.2；C.64/C.65/C.76） */}
          <ViewSwitchBar vp={vp} />
          {/* 筛选工具条（C.28）：搜索框 + 标签管理 + Chips 行 */}
          <div className="border-b border-neutral-200 bg-white shrink-0">
            <div className="flex items-center gap-2 px-5 py-2.5">
              <div className="flex items-center gap-1.5 h-8 border border-neutral-300 rounded-md px-2.5 bg-white focus-within:border-brand-500 w-[260px]">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-neutral-400"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
                <input
                  aria-label="搜索任务"
                  type="search"
                  placeholder="搜索任务…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="flex-1 h-7 outline-none text-[13px] bg-transparent"
                />
              </div>
              <button
                onClick={() => setShowLabelsAdmin(true)}
                data-sb-scope="list-open-labels"
                className="h-8 px-2.5 inline-flex items-center gap-1.5 border border-neutral-300 rounded-md text-[13px] hover:bg-neutral-50"
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M20.59 13.41 13.41 20.59a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><path d="M7 7h.01"/></svg>
                管理标签
              </button>
              {/* ⊞ 筛选入口（TASK-011 §3.1 + O2；C.74） */}
              <FilterOpenButton vp={vp} />
              <span className="ml-auto text-[12px] text-neutral-500">命中 {filtered.length} / {total}</span>
            </div>
            {/* Chips 行（C.28 已选 Chips + 清空全部） */}
            {chips.length > 0 && (
              <div className="flex items-center flex-wrap gap-1.5 px-5 pb-2" aria-label="已选筛选条件">
                {chips.map((c, i) => (
                  <span key={i} className="inline-flex items-center gap-1 rounded-full bg-neutral-100 px-2 py-0.5 text-[12px]">
                    {c.label}
                    <button
                      onClick={() => setChips(chips.filter((_, idx) => idx !== i))}
                      aria-label={`移除筛选：${c.label}`}
                      className="text-neutral-500 hover:text-red-500"
                    >✕</button>
                  </span>
                ))}
                <button onClick={() => setChips([])} className="text-[12px] text-neutral-500 hover:text-brand-600">清空全部</button>
              </div>
            )}
            {/* 视图条件 chips 行（C.74：视图只读 chips + 叠加 chip + 清空） */}
            <ViewChipsRow vp={vp} totalCount={(vp.viewIdParam || vp.filtersParam) ? total : null} />
            {/* C.45/C.59 筛选 Chip 行：被阻塞 = true / 已归档视图 + 命中计数 */}
            {(blockedFilter || archivedView) && (
              <div className="flex items-center flex-wrap gap-1.5 px-5 pb-2 text-[12px]" aria-label="已选筛选条件" data-sb-scope="list-chiprow">
                {blockedFilter && (
                  <span className="inline-flex items-center gap-1 rounded-full bg-neutral-100 px-2.5 py-0.5">
                    被阻塞 = true
                    <button aria-label="移除筛选：被阻塞" onClick={() => setBlockedFilter(false)} className="text-neutral-500 hover:text-red-500">✕</button>
                  </span>
                )}
                {archivedView && (
                  <span className="inline-flex items-center gap-1 rounded-full bg-neutral-100 px-2.5 py-0.5">
                    🗄 已归档视图
                    <button aria-label="移除筛选：已归档" onClick={() => setArchivedView(false)} className="text-neutral-500 hover:text-red-500">✕</button>
                  </span>
                )}
                <span className="ml-auto text-neutral-400">{filtered.length} / {total} 个任务</span>
              </div>
            )}
          </div>
          <div className="flex-1 overflow-y-auto p-5">
            <div className="flex items-center gap-1.5 border border-dashed border-neutral-300 h-[38px] px-3 rounded-md mb-3.5 text-neutral-500 focus-within:border-brand-500 focus-within:bg-white">
              <span>+</span>
              <input id="tq-input" className="flex-1 bg-transparent outline-none text-[13px]" placeholder="输入任务标题后按回车快速创建…"
                value={quick} onChange={(e) => setQuick(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); quickCreate(); } if (e.key === "Escape") { setQuick(""); (e.target as HTMLInputElement).blur(); } }} />
              <span className="text-xs">Enter 创建</span>
            </div>
            {filtered.length === 0 ? (
              /* C.59 归档视图空态：「没有已归档的任务」（归档的任务会出现在这里） */
              <div className="flex flex-col items-center gap-2 py-12 text-neutral-500">
                <svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="#d4d4d4" strokeWidth="2"><rect width="8" height="4" x="8" y="2" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4M12 16h4M8 11h.01M8 16h.01"/></svg>
                <div className="text-[15px] font-semibold text-neutral-700" data-sb-scope="list-empty-title">
                  {archivedView && total === 0 ? "没有已归档的任务" : total === 0 ? "暂无任务" : "没有符合当前筛选的任务"}
                </div>
                <div className="text-[13px]">
                  {archivedView && total === 0 ? "归档的任务会出现在这里" : total === 0 ? "创建第一个任务开始工作" : "尝试调整或清空筛选"}
                </div>
                {!archivedView && (
                  <button onClick={() => { if (total === 0) setShowTaskModal(true); else { setSearch(""); setChips([]); } }} className="mt-2 inline-flex h-[34px] items-center gap-1.5 px-3.5 bg-brand-500 text-white rounded-md font-medium">
                    {total === 0 ? "+ 创建任务" : "清空全部"}
                  </button>
                )}
              </div>
            ) : (
              <table className="w-full border-collapse" role="tree" aria-label="任务树">
                <thead><tr>
                    {[
                      ["编号", "w-24"], ["标题", ""], ["状态", "w-[120px]"], ["负责人", "w-[110px]"], ["截止时间", "w-[130px]"],
                      ...(wlColumn ? [["工时", "w-[96px]"]] : []),
                      ...cfCols.map((key) => {
                        const f = cfDefs.find((d) => d.key === key);
                        return [f?.name ?? key, "w-[110px]"] as [string, string];
                      }),
                      ["子任务", "w-[88px]"],
                    ].map(([h, w]) => (
                      <th key={h} data-sb-scope={h === "工时" ? "list-wl-th" : undefined}
                        className={`text-left px-3 py-2 border-b border-neutral-200 text-[11px] font-semibold text-neutral-400 uppercase tracking-wider ${w}`}>{h}</th>
                    ))}
                  </tr></thead>
                <tbody>
                  {filterActive
                    ? filtered.map((it) => renderRow(it, 1, false))
                    : visibleRows.map(({ it, depth }) => renderRow(it, depth, true))}
                </tbody>
              </table>
            )}
          </div>
        </main>
      </div>

      {/* 移动确认弹层（C.39）：「将 “X”（含 N 个后代）移动到 “Y” 之下？」；Esc 取消 */}
      {confirmMove && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center p-4 z-[80]" data-sb-scope="tree-confirm-move">
          <div className="bg-white rounded-xl shadow-lg w-[420px] p-6" role="dialog" aria-modal="true" aria-label="移动子树">
            <div className="flex items-center gap-2 text-base font-semibold mb-3">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M5 9l-3 3 3 3M9 5l3-3 3 3M15 19l-3 3-3-3M19 9l3 3-3 3M2 12h20M12 2v20"/></svg>
              移动子树
            </div>
            <div className="text-[13.5px] text-neutral-700" data-sb-scope="tree-confirm-move-text">
              将 “<b>{issueById.get(confirmMove.fromId)?.name ?? "…"}</b>”（含 {confirmMove.desc} 个后代）移动到 “<b>{issueById.get(confirmMove.toId)?.name ?? "…"}</b>”之下？
            </div>
            <div className="flex justify-end gap-2.5 mt-5">
              <button autoFocus onClick={() => setConfirmMove(null)} className="h-[34px] px-3.5 border border-neutral-300 rounded-md">取消</button>
              <button data-sb-scope="tree-confirm-move-go" onClick={() => void executeMove()} className="h-[34px] px-3.5 bg-brand-500 text-white rounded-md hover:bg-brand-600">移动</button>
            </div>
          </div>
        </div>
      )}

      {/* 「移动到…」弹窗（C.63：<768px 行菜单触发；父任务搜索选择器 → 同一确认链路） */}
      {moveModalFor && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center p-4 z-[70]" data-sb-scope="tree-move-modal">
          <div className="bg-white rounded-xl shadow-lg w-[480px] max-w-full p-5" role="dialog" aria-modal="true" aria-label="移动任务">
            <div className="flex items-start gap-2 mb-3">
              <div className="text-base font-semibold flex-1">
                移动到… · {issueById.get(moveModalFor)?.issue_key} {issueById.get(moveModalFor)?.name}
              </div>
              <button aria-label="关闭" onClick={() => setMoveModalFor(null)} className="w-7 h-7 flex items-center justify-center text-neutral-500 hover:text-neutral-900">✕</button>
            </div>
            <label className="block text-[13px] font-medium text-neutral-700 mb-1.5">选择新的父任务（留空 = 摘出为顶层）</label>
            <input
              className="w-full h-9 border border-neutral-300 rounded-md px-2.5 text-[13px] focus:outline-none focus:border-brand-500"
              placeholder="🔍 搜索父任务…"
              aria-label="搜索父任务"
              value={moveSearch}
              onChange={(e) => setMoveSearch(e.target.value)}
            />
            <div className="mt-2 border border-neutral-200 rounded-lg max-h-[240px] overflow-y-auto" role="listbox" aria-label="父任务候选">
              {moveCandidates.length === 0 ? (
                <div className="px-3 py-6 text-center text-[13px] text-neutral-400">未找到匹配任务，换个关键词</div>
              ) : moveCandidates.map((c) => (
                <div key={c.id} role="option" aria-selected="false" data-sb-scope="tree-move-candidate" data-candidate-id={c.id}
                  onClick={() => { const from = moveModalFor; setMoveModalFor(null); if (from) void openConfirmMove(from, c.id); }}
                  className="flex items-center gap-2 px-3 py-2 text-[13px] cursor-pointer hover:bg-neutral-50">
                  <span className="font-mono text-[12px] text-neutral-400">{c.issue_key}</span>
                  <span className="flex-1 truncate">{c.name}</span>
                  <span className="text-[12px] text-neutral-400">第 {depthLoaded(c.id)} 层</span>
                </div>
              ))}
            </div>
            <div className="mt-3 text-[12.5px] text-neutral-400">
              移动后保持自身 sort_order；深度 ≤5 校验与环检测由服务端兜底（409 将高亮环路径）。
            </div>
            <div className="flex justify-end mt-4">
              <button onClick={() => setMoveModalFor(null)} className="h-[34px] px-3.5 border border-neutral-300 rounded-md">取消</button>
            </div>
          </div>
        </div>
      )}

      {peekId && <IssueDrawer issueId={peekId} slug={workspaceSlug!} projectId={projectId!} onClose={() => { closePeek(); load(); }} onChanged={() => load()} />}
      {showTaskModal && <NewTaskModal slug={workspaceSlug!} projectId={projectId!} projectName={project?.name ?? ""} onClose={() => setShowTaskModal(false)} onCreated={() => load()} />}
      {showLabelsAdmin && workspaceSlug && projectId && (
        <LabelsAdminModal workspaceSlug={workspaceSlug} projectId={projectId} onClose={() => setShowLabelsAdmin(false)} />
      )}
    </div>
  );
}
