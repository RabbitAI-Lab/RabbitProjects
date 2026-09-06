import { action, computed, makeObservable, observable } from "mobx";
import type { Issue, ProjectSummary, UUID, WorkspaceSummary } from "@rp/types";

/** 本包不发起 HTTP 请求（monorepo-structure.md §4）：数据由 app 层 services/ 注入。 */

export class SessionStore {
  currentUser: { name: string; email: string } | null = null;
  isBootstrapped = false;

  constructor() {
    makeObservable(this, {
      currentUser: observable,
      isBootstrapped: observable,
      setUser: action,
    });
  }

  setUser(user: { name: string; email: string } | null): void {
    this.currentUser = user;
    this.isBootstrapped = true;
  }
}

export class WorkspaceStore {
  list: WorkspaceSummary[] = [];
  currentSlug: string | null = null;
  projects: ProjectSummary[] = [];

  constructor() {
    makeObservable(this, {
      list: observable,
      currentSlug: observable,
      projects: observable,
      current: computed,
      hydrate: action,
      switchWorkspace: action,
      setProjects: action,
    });
  }

  get current(): WorkspaceSummary | undefined {
    return this.list.find((w) => w.slug === this.currentSlug) ?? this.list[0];
  }

  hydrate(list: WorkspaceSummary[], slug: string | null): void {
    this.list = list;
    this.currentSlug = slug;
  }

  switchWorkspace(slug: string): void {
    this.currentSlug = slug;
    this.projects = [];
  }

  setProjects(projects: ProjectSummary[]): void {
    this.projects = projects;
  }
}

/** 看板视图状态：分组渲染与排序求值可脱离 UI 单测（monorepo-structure.md 改进点 2）。 */
export class BoardStore {
  issuesByColumn = new Map<string, Issue[]>();

  constructor() {
    makeObservable(this, {
      issuesByColumn: observable.shallow,
      setColumn: action,
    });
  }

  setColumn(stateGroup: string, issues: Issue[]): void {
    this.issuesByColumn.set(stateGroup, issues);
  }

  columnIssues(stateGroup: string): Issue[] {
    return this.issuesByColumn.get(stateGroup) ?? [];
  }
}

/* ═══════════════ Sprint-3 Phase 3-A（BOARD-003 §4.4 / TASK-011 §4.4.1）═══════════════ */

/** BOARD-003 §4.1.1 IssueView.Layout 白名单（BR-04）。 */
export type ViewLayout = "list" | "kanban" | "gantt" | "table";

/** display_props（§4.1.1 help_text 同构；columns 隐藏用「-」前缀——原型 O3 列配置）。 */
export interface ViewDisplayProps {
  icon?: string;
  group_by?: string | null;
  order_by?: string | null;
  /** 列序数组（key/title/state/assignees/due/priority/labels）；「-key」= 隐藏 */
  columns?: string[];
  /** 卡片开关：固定 7 项 + cf_*（BR-09 键域） */
  card_fields?: Record<string, boolean>;
  show_empty_groups?: boolean;
}

/** 条件节点（TASK-011 §1.2 DSL；value 形态按操作符：多值=数组、标量=单值）。 */
export interface FilterCondition {
  field: string;
  operator: string;
  value: unknown[];
}
/** 逻辑节点（op ∈ AND/OR；conditions 混排条件与嵌套组；深度 ≤3、条件 ≤20）。 */
export interface FilterLogicNode {
  op: "AND" | "OR";
  conditions: Array<FilterCondition | FilterLogicNode>;
}

/** BOARD-003 §4.2-1 GET …/views/ 行（IssueViewSerializer 同构）。 */
export interface IssueViewData {
  id: string;
  project_id: string;
  workspace_id: string;
  name: string;
  description?: string;
  access: "personal" | "shared";
  layout: ViewLayout;
  owner_id: string;
  is_system: boolean;
  is_locked?: boolean;
  filters: Partial<FilterLogicNode> | Record<string, never>;
  display_props: ViewDisplayProps;
  sort_order: number;
}

export const NONE_KEY = "__none__";
/** BOARD-003 §3.4 八枚 emoji 预设（view_service.ICON_POOL 同源）。 */
export const VIEW_ICON_POOL = ["✨", "📦", "🐛", "👤", "📅", "🧪", "🔥", "🚒"] as const;
/** BOARD-003 §3.3 卡片开关固定 7 项（BR-09 同一集合）。 */
export const CARD_FIELD_KEYS = [
  "labels",
  "sub_issues",
  "attachments",
  "estimate",
  "priority",
  "timer",
  "target_date",
] as const;
/** 表格/列表列配置键域（原型 O3 / COL_NAMES）。 */
export const TABLE_COLUMN_KEYS = ["key", "title", "state", "assignees", "due", "priority", "labels"] as const;

export const DEFAULT_CARD_FIELDS: Record<string, boolean> = {
  labels: true,
  sub_issues: true,
  attachments: true,
  estimate: true,
  priority: false,
  timer: false,
  target_date: true,
};
export const DEFAULT_DISPLAY_PROPS: ViewDisplayProps = {
  group_by: "state_id",
  order_by: "sort_order",
  columns: [...TABLE_COLUMN_KEYS],
  card_fields: { ...DEFAULT_CARD_FIELDS },
  show_empty_groups: true,
};

/** —— DSL 树纯函数（TASK-011 §1.2 前后端同构）—— */
export function isLogicNode(n: FilterCondition | FilterLogicNode): n is FilterLogicNode {
  return "op" in n;
}
export function countConditions(tree: FilterLogicNode | null | undefined): number {
  if (!tree) return 0;
  return tree.conditions.reduce((s, c) => s + (isLogicNode(c) ? countConditions(c) : 1), 0);
}
export function treeDepth(tree: FilterLogicNode | null | undefined): number {
  if (!tree) return 0;
  return 1 + Math.max(0, ...tree.conditions.filter(isLogicNode).map((c) => treeDepth(c)));
}
export function cloneTree<T>(tree: T): T {
  return JSON.parse(JSON.stringify(tree)) as T;
}
export function emptyTree(): FilterLogicNode {
  return { op: "AND", conditions: [] };
}
/** 同层扁平化查找/删除（原型 findGroup/removeNode 同款，按引用操作）。 */
export function findGroup(tree: FilterLogicNode, id: unknown): FilterLogicNode | null {
  if (tree === id) return tree;
  for (const c of tree.conditions) {
    if (isLogicNode(c)) {
      const hit = findGroup(c, id);
      if (hit) return hit;
    }
  }
  return null;
}
export function removeNode(tree: FilterLogicNode, node: FilterCondition | FilterLogicNode): void {
  tree.conditions = tree.conditions.filter((c) => c !== node);
  for (const c of tree.conditions) if (isLogicNode(c)) removeNode(c, node);
}
/** 另存 = 合并树：视图 filters AND 临时树（TASK-011 §4.4.1 mergeTrees）。 */
export function mergeTrees(view: Partial<FilterLogicNode> | null, temp: FilterLogicNode | null): FilterLogicNode {
  const viewTree = view && (view as FilterLogicNode).conditions?.length ? (view as FilterLogicNode) : null;
  if (!viewTree && !temp?.conditions.length) return emptyTree();
  if (!temp?.conditions.length) return cloneTree(viewTree!);
  if (!viewTree) return cloneTree(temp);
  return { op: "AND", conditions: [cloneTree(viewTree), cloneTree(temp)] };
}

/**
 * FilterTreeStore（TASK-011 §4.4.1）：③ 临时层（URL ?filters= 同源）。
 * 本包不发起 HTTP（monorepo-structure.md §4）：命中数预估由 app 层注入
 * preview 回调（500ms 防抖在其内实现），store 只持有结果。
 */
export class FilterTreeStore {
  /** ③ 临时层编辑树（应用前草稿；applied 才入 URL） */
  tree: FilterLogicNode = emptyTree();
  /** 已应用的临时层（与 URL 同源；null = 无） */
  applied: FilterLogicNode | null = null;
  hitCount: number | null = null;
  previewing = false;

  constructor() {
    makeObservable(this, {
      tree: observable,
      applied: observable,
      hitCount: observable,
      previewing: observable,
      quota: computed,
      setTree: action,
      apply: action,
      reset: action,
      clear: action,
      setHit: action,
      hydrateApplied: action,
    });
  }

  get quota(): { conditions: number; depth: number } {
    return { conditions: countConditions(this.tree), depth: treeDepth(this.tree) };
  }

  setTree(t: FilterLogicNode): void {
    this.tree = t;
  }

  /** 初始化：从 URL ?filters= 反序列化（app 层解析 JSON 后喂入；TASK-011 §4.4.2）。 */
  hydrateApplied(tree: FilterLogicNode | null): void {
    this.applied = tree ? cloneTree(tree) : null;
    this.tree = tree ? cloneTree(tree) : emptyTree();
  }

  /** [应用]：树入 applied（URL 层）；空树 → 清空临时条件。 */
  apply(): void {
    this.applied = countConditions(this.tree) > 0 ? cloneTree(this.tree) : null;
  }

  /** 打开面板时以已应用树（或空树）为草稿。 */
  reset(): void {
    this.tree = this.applied ? cloneTree(this.applied) : emptyTree();
    this.hitCount = null;
  }

  clear(): void {
    this.tree = emptyTree();
    this.applied = null;
    this.hitCount = null;
  }

  setHit(n: number | null): void {
    this.hitCount = n;
  }
}

/** 临时层/视图层的本地未保存覆盖（黄条 dirty 依据，BOARD-003 §3.1）。 */
export interface ViewOverrides {
  displayProps?: Partial<ViewDisplayProps>;
  layout?: ViewLayout | undefined;
}

/**
 * ViewStore（BOARD-003 §4.4）：viewsByProject（SWR key project:{id}:views）、
 * currentViewId（null = 「全部」前端固定入口）、dirty 派生（当前显示配置 vs
 * 存档 diff）、默认视图偏好（board.default_view_id 镜像；读写由 app 层注入）。
 * 本包不发起 HTTP：hydrate 由 app 层 services 调用后喂数。
 */
export class ViewStore {
  /** project:{id}:views —— SWR 缓存键与 Map 键一致（§4.4）。 */
  viewsByProject = new Map<string, IssueViewData[]>();
  /** 当前选中视图（null = 「全部」固定首项，不入库）。 */
  currentViewId: string | null = null;
  /** board.default_view_id 偏好镜像（{project_id: view_id}）。 */
  defaultViewByProject = new Map<string, string>();
  /** 未保存覆盖：key = viewId ?? "__all__"（会话级；放弃=清空）。 */
  overrides = new Map<string, ViewOverrides>();
  loading = false;

  constructor() {
    makeObservable(this, {
      viewsByProject: observable.shallow,
      currentViewId: observable,
      defaultViewByProject: observable.shallow,
      overrides: observable.shallow,
      loading: observable,
      hydrate: action,
      setLoading: action,
      setCurrent: action,
      upsert: action,
      remove: action,
      setDefault: action,
      patchOverride: action,
      discard: action,
      markSaved: action,
    });
  }

  views(projectId: string): IssueViewData[] {
    return this.viewsByProject.get(projectId) ?? [];
  }

  currentView(projectId: string): IssueViewData | null {
    if (!this.currentViewId) return null;
    return this.views(projectId).find((v) => v.id === this.currentViewId) ?? null;
  }

  get overrideKey(): string {
    return this.currentViewId ?? "__all__";
  }

  /** 黄条依据：存在任一未保存覆盖（display_props / layout）。 */
  get dirty(): boolean {
    const ov = this.overrides.get(this.overrideKey);
    if (!ov) return false;
    return Object.keys(ov.displayProps ?? {}).length > 0 || ov.layout !== undefined;
  }

  /** 生效显示配置 = 存档 display_props ⊕ 覆盖（「全部」= 默认值 ⊕ 覆盖）。 */
  effectiveDisplayOf(projectId: string): ViewDisplayProps {
    const base = this.currentView(projectId)?.display_props ?? DEFAULT_DISPLAY_PROPS;
    const ov = this.overrides.get(this.overrideKey);
    return { ...DEFAULT_DISPLAY_PROPS, ...base, ...(ov?.displayProps ?? {}) };
  }

  hydrate(projectId: string, views: IssueViewData[]): void {
    this.viewsByProject.set(projectId, views);
  }

  setLoading(v: boolean): void {
    this.loading = v;
  }

  setCurrent(viewId: string | null): void {
    this.currentViewId = viewId;
  }

  upsert(view: IssueViewData): void {
    const list = [...(this.viewsByProject.get(view.project_id) ?? [])];
    const i = list.findIndex((v) => v.id === view.id);
    if (i >= 0) list[i] = view;
    else list.push(view);
    list.sort((a, b) => a.sort_order - b.sort_order);
    this.viewsByProject.set(view.project_id, list);
  }

  remove(projectId: string, viewId: string): void {
    this.viewsByProject.set(
      projectId,
      (this.viewsByProject.get(projectId) ?? []).filter((v) => v.id !== viewId),
    );
    if (this.currentViewId === viewId) this.currentViewId = null;
    this.overrides.delete(viewId);
    if (this.defaultViewByProject.get(projectId) === viewId) this.defaultViewByProject.delete(projectId);
  }

  setDefault(projectId: string, viewId: string | null): void {
    if (viewId) this.defaultViewByProject.set(projectId, viewId);
    else this.defaultViewByProject.delete(projectId);
  }

  patchOverride(patch: Partial<ViewOverrides>): void {
    const key = this.overrideKey;
    const cur = this.overrides.get(key) ?? {};
    this.overrides.set(key, {
      displayProps: { ...(cur.displayProps ?? {}), ...(patch.displayProps ?? {}) },
      layout: patch.layout ?? cur.layout,
    });
  }

  discard(): void {
    this.overrides.delete(this.overrideKey);
  }

  /** 保存成功：覆盖并入存档并清草稿。 */
  markSaved(view: IssueViewData): void {
    this.upsert(view);
    this.overrides.delete(view.id);
  }
}

export interface RootStore {
  session: SessionStore;
  workspace: WorkspaceStore;
  board: BoardStore;
  views: ViewStore;
  filterTree: FilterTreeStore;
}

/* ═══════════════ Sprint-3 Phase 3-B（BOARD-004 §4.4）═══════════════ */

/**
 * SelectionStore（BOARD-004 §4.4）：全局批量选中池 —— 跨页/切视图保留、
 * 切项目清空（§2.3 状态机注）、上限 100 阻止追加（§2.6 边界表）、dispatching
 * 冻结（BR-13：请求期点选/区间无效）。本包不发起 HTTP：dispatching 由 app 层
 * 批量动作处置，失败定位弹层消费 details[].field 解析 issue_ids[<index>]。
 */
export class SelectionStore {
  static readonly LIMIT = 100;

  selectedIds: Set<string> = new Set();
  projectId: string | null = null;
  /** BR-13：请求期冻结（防中途改选造成响应错配）。 */
  dispatching = false;
  /** ⌘A/全选截断黄条（C.79）：>100 时由 selectViewResults 置位，清空/知道了归零。 */
  truncatedInfo: { selected: number; total: number } | null = null;

  constructor() {
    makeObservable(this, {
      selectedIds: observable.ref,
      projectId: observable,
      dispatching: observable,
      truncatedInfo: observable.ref,
      count: computed,
      bindProject: action,
      toggle: action,
      addMany: action,
      range: action,
      selectViewResults: action,
      setDispatching: action,
      clearTruncation: action,
      clear: action,
      removeMany: action,
    });
  }

  get count(): number {
    return this.selectedIds.size;
  }

  has(id: string): boolean {
    return this.selectedIds.has(id);
  }

  /** 进入项目时重置作用域（防止跨项目批量误伤，§4.4）。 */
  bindProject(projectId: string | undefined | null): void {
    if (this.projectId !== projectId) {
      this.projectId = projectId ?? null;
      this.selectedIds = new Set();
      this.truncatedInfo = null;
    }
  }

  toggle(id: string): void {
    if (this.dispatching) return; // BR-13：冻结期间点选无效
    const next = new Set(this.selectedIds);
    if (next.has(id)) {
      next.delete(id);
    } else {
      if (next.size >= SelectionStore.LIMIT) return; // 上限阻止追加（已选项仍可移除）
      next.add(id);
    }
    this.selectedIds = next;
  }

  /** 框选命中集（Shift = 在既有选中上追加）；达上限截断。 */
  addMany(ids: string[]): void {
    if (this.dispatching || ids.length === 0) return;
    const next = new Set(this.selectedIds);
    for (const id of ids) {
      if (next.size >= SelectionStore.LIMIT) break;
      next.add(id);
    }
    this.selectedIds = next;
  }

  /** Shift 区间选择：visibleIds 为当前视图可见顺序（§4.4 代码块同签名语义）。 */
  range(fromId: string, toId: string, visibleIds: string[]): void {
    if (this.dispatching) return; // BR-13
    const a = visibleIds.indexOf(fromId);
    const b = visibleIds.indexOf(toId);
    if (a === -1 || b === -1) return;
    const [lo, hi] = a < b ? [a, b] : [b, a];
    this.addMany(visibleIds.slice(lo, hi + 1));
  }

  /** 全选视图结果集（前端显式展开，BR-08——服务端不隐式展开视图）。 */
  selectViewResults(viewIds: string[]): { truncated: boolean; total: number } {
    if (this.dispatching) return { truncated: false, total: this.selectedIds.size };
    const total = viewIds.length;
    const truncated = total > SelectionStore.LIMIT;
    this.selectedIds = new Set(viewIds.slice(0, SelectionStore.LIMIT));
    this.truncatedInfo = truncated ? { selected: SelectionStore.LIMIT, total } : null;
    return { truncated, total };
  }

  setDispatching(v: boolean): void {
    this.dispatching = v;
  }

  clearTruncation(): void {
    this.truncatedInfo = null;
  }

  clear(): void {
    this.selectedIds = new Set();
    this.truncatedInfo = null;
  }

  /** 「移除失败项重试」（§3.4 一键重试）。 */
  removeMany(ids: string[]): void {
    const drop = new Set(ids);
    this.selectedIds = new Set([...this.selectedIds].filter((i) => !drop.has(i)));
  }
}

export function createRootStore(): RootStore {
  return {
    session: new SessionStore(),
    workspace: new WorkspaceStore(),
    board: new BoardStore(),
    views: new ViewStore(),
    filterTree: new FilterTreeStore(),
  };
}

export type { Issue, ProjectSummary, UUID };

/* ═══════════════ Sprint-3 Phase 3-C（COLLAB-004 §4.4 实时层）═══════════════ */

export * from "./realtime";
