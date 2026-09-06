/**
 * GanttStore（GANTT-001 §4.4.1 + GANTT-002 §4.4）：甘特视窗状态机。
 *
 *  - 视窗取数：横向像素平移（pan，纯 transform 零请求）与数据视窗（dataViewport）
 *    解耦——时间范围越界才 150ms 防抖预取（§2.2 时序，绝不全量拉取）；
 *  - 增量合并：rowsById 按 id 去重 + rowOrder 追加/替换两态（平移=换窗替换、
 *    滚动=游标追加）；连线仅对视窗内行批量求值（relation_count>0 才发，§2.1）；
 *  - 拖拽会话：previewDrag/commitDrag/rollbackDrag（GANTT-002 §4.4 三手势状态机）；
 *  - 键盘改期 300ms 合并提交（§3.4）；未排期入轨默认 3 天（BR-07）；
 *  - 实时：issue.updated → 单实体增量拉取补日期（BR-14，不做全量刷新）。
 *
 * 说明：规格 §4.4.1 将 GanttStore 画在 packages/shared-state——本仓 shared-state
 * 纪律是「不发起 HTTP，数据由 app 层 services 注入」（见其 index.ts 头注与
 * BoardStore 先例），且 linked 包变更需重启 dev server（CLAUDE.md 坑 19），
 * 故落在 apps/web/app/stores/（与 SessionStore/BoardStore 同层）——偏差登记 ADR-0022。
 */
import { makeObservable, observable, runInAction } from "mobx";
import {
  GanttAPI,
  IssueAPI,
  type GanttEdge,
  type GanttGranularity,
  type GanttOverdueSummary,
  type GanttRow,
  type GanttUnscheduledRow,
} from "../services/api";
import { toast } from "../components/Toast";

/* ───────────────────────── 域常量（§1.4 / §2.5 / 原型 O3） ───────────────────────── */

/** px/天。原型 O3 冻结值 day 36 / week 13 / month 9；GANTT-001 §1.4 表为 32/8/2——
 *  按「视觉/交互验收基准（唯一）= 冻结原型」取原型值（勘误口径，ADR-0022 登记）。 */
export const DAY_WIDTH: Record<GanttGranularity, number> = { day: 36, week: 13, month: 9 };
/** 行高固定 36px（BR-09）。 */
export const ROW_H = 36;
/** 行分页默认 60（BR-09 / §2.5）。 */
export const ROW_PAGE = 60;
/** 滚动至 80% 预取下一页（§2.1）。 */
export const PREFETCH_RATIO = 0.8;
/** 平移/缩放防抖 150ms（§2.2 时序）。 */
export const DEBOUNCE_MS = 150;
/** 键盘改期键击合并窗口（GANTT-002 §3.4）。 */
export const KB_MERGE_MS = 300;
/** 未排期入轨默认工期 3 天（BR-07）。 */
export const UNSCHED_SPAN_DAYS = 3;
/** 连线批量一次 ≤ 60 issue（§2.5，超限分批）。 */
export const BULK_MAX = 60;
/** 乐观更新失败回滚动画（GANTT-002 §3.1：300ms ease-back）。 */
export const ROLLBACK_MS = 300;
/** 缩放提示阈值：day 视窗拖出 6 个月 → 建议切 week（§1.4）。 */
export const SUGGEST_GRAN_DAYS = 180;

/** 中国法定节假日配置表（§3.1：10% 底纹 + 列头节假日名；P2 内置 2026 年表）。 */
export const HOLIDAYS: Record<string, string> = {
  "2026-01-01": "元旦",
  "2026-02-16": "除夕",
  "2026-02-17": "春节",
  "2026-04-05": "清明",
  "2026-05-01": "劳动节",
  "2026-06-19": "端午",
  "2026-09-25": "中秋",
  "2026-10-01": "国庆",
  "2026-10-02": "国庆",
  "2026-10-03": "国庆",
  "2026-10-04": "国庆",
  "2026-10-05": "国庆",
  "2026-10-06": "国庆",
  "2026-10-07": "国庆",
};

/* ───────────────────────── 纯日期工具（UTC 日界，无 DST 歧义） ───────────────────────── */

const DAY_MS = 86_400_000;

export function isoToUtc(iso: string): number {
  return Date.parse(`${iso}T00:00:00Z`);
}
export function utcToIso(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}
export function addDaysIso(iso: string, n: number): string {
  return utcToIso(isoToUtc(iso) + n * DAY_MS);
}
export function diffDays(aIso: string, bIso: string): number {
  return Math.round((isoToUtc(bIso) - isoToUtc(aIso)) / DAY_MS);
}
/** 本地时区今日（YYYY-MM-DD；meta.today 优先，此处是客户端兜底）。 */
export function localTodayIso(): string {
  return new Date().toLocaleDateString("sv-SE");
}
export function fmtCn(iso: string): string {
  const [, m, d] = iso.split("-");
  return `${Number(m)}月${Number(d)}日`;
}

/* ───────────────────────── 状态形状 ───────────────────────── */

export type DragMode = "move" | "resize-start" | "resize-end";

/** 拖拽会话预览态（GANTT-002 §3.1：占位几何 + Δ徽标 + 钳制/冲突标记）。 */
export interface GanttDragState {
  issueId: string;
  mode: DragMode;
  /** 预览起/止（已钳制，start ≤ target）。 */
  start: string | null;
  target: string | null;
  deltaDays: number;
  clamped: boolean;
  conflict: boolean;
  /** 冲突前置任务编号（确认弹层文案用，§3.1）。 */
  conflictWith: string | null;
  /** 徽标文本（aria-live=polite 播报，§3.4）。 */
  badge: string;
}

/** 实时事件版本门（ADR-0021：数值化 Date.parse 比较，防乱序盲写）。 */
function versionTs(v: string | undefined): number {
  if (!v) return 0;
  const t = Date.parse(v);
  return Number.isNaN(t) ? 0 : t;
}

export interface GanttStoreCtx {
  slug: string;
  projectId: string;
  /** 与甘特行集同源的筛选参数（view_id / filters——TASK-011 三源恒 AND）。 */
  listParams: { view_id?: string; filters?: string };
  /** 折叠持久化（BR-10：display_props.collapsed；无视图时为 undefined 仅本地态）。 */
  onCollapseChange?: (ids: string[]) => void;
  /** 拖拽入口闸（BR-05：VIEWER/COMMENTER/完成态/<1024px 全禁）。 */
  canDrag: () => boolean;
}

export class GanttStore {
  /* 视窗（§4.4.1） */
  granularity: GanttGranularity = "day";
  pan = 0;
  viewportW = 1000;
  /** 时间轴边界（固定 3 年域：Home/End 锚点 + 今日线/开放端条的稳定坐标）。 */
  readonly tlStart: string;
  readonly tlEnd: string;
  /** 数据视窗（已取数的时间范围；像素平移不触发，越界才防抖预取）。 */
  dataViewport: { start: string; end: string } = { start: "", end: "" };

  /* 行集（增量合并） */
  rowsById = new Map<string, GanttRow>();
  rowOrder: string[] = [];
  edges: GanttEdge[] = [];
  collapsed = new Set<string>();
  nextCursor: string | null = null;
  totalCount = 0;
  today = localTodayIso();

  /* 取数状态 */
  loading = true; // 首屏骨架（表头 + 12 行条形骨架，§3.5）
  prefetching = false; // 列头右侧 spinner（§3.4 平移行）
  prefetchFailed = false; // 黄条「实时更新暂停 · 重试」（§2.4 / §3.5）
  suggestGranularity: "week" | null = null; // 平移过远建议切粒度（§1.4）

  /* 未排期折叠区（§3.1 / §3.4） */
  unscheduledCount = 0;
  unscheduledRows: GanttUnscheduledRow[] = [];
  unscheduledOpen = false;

  /* 延期概览（GANTT-002 §3.2） */
  overdue: GanttOverdueSummary | null = null;
  overdueOpen = false;
  overdueThrottled = false; // 429 处理：概览限流 10/min（§4.2.1 要点 4）

  /* 交互态 */
  selectedRowId: string | null = null;
  drag: GanttDragState | null = null;
  /** 回滚动画标记（300ms ease-back 后清除）。 */
  rollbackId: string | null = null;
  /** 键盘改期合并缓冲（§3.4：300ms 内键击合并为一次 PATCH）。 */
  kbAnnounce = "";

  private ctx: GanttStoreCtx;
  private debounceTimer: ReturnType<typeof setTimeout> | null = null;
  private kbTimer: ReturnType<typeof setTimeout> | null = null;
  /** 键盘改期合并缓冲（§3.4：300ms 内键击合并为一次 PATCH）。orig = 会话起点
   *  快照（patchDates 差分基准——行本体在每次键击时乐观写回）。 */
  private kbPending: { id: string; start: string | null; target: string | null; orig: { start: string | null; target: string | null } } | null = null;
  private versionGate = new Map<string, number>();
  private reqSeq = 0;
  /** 在途行取数计数（单飞真源；详见取数管线注释）。 */
  private reqInflight = 0;
  /** 飞行期间登记的 ensure（落定后重估一次）。 */
  private pendingEnsure = false;

  constructor(ctx: GanttStoreCtx) {
    this.ctx = ctx;
    this.today = localTodayIso();
    this.tlStart = addDaysIso(this.today, -365);
    this.tlEnd = addDaysIso(this.today, 730);
    makeObservable(this, {
      granularity: observable,
      pan: observable,
      viewportW: observable,
      dataViewport: observable,
      rowsById: observable,
      rowOrder: observable,
      edges: observable,
      collapsed: observable,
      nextCursor: observable,
      totalCount: observable,
      today: observable,
      loading: observable,
      prefetching: observable,
      prefetchFailed: observable,
      suggestGranularity: observable,
      unscheduledCount: observable,
      unscheduledRows: observable,
      unscheduledOpen: observable,
      overdue: observable,
      overdueOpen: observable,
      overdueThrottled: observable,
      selectedRowId: observable,
      drag: observable,
      rollbackId: observable,
      kbAnnounce: observable,
    });
  }

  /* ───────────── 坐标系（dX/xD 与原型 O3 同构） ───────────── */

  get dayWidth(): number {
    return DAY_WIDTH[this.granularity];
  }
  get totalPx(): number {
    return (diffDays(this.tlStart, this.tlEnd) + 1) * this.dayWidth;
  }
  xOf(iso: string): number {
    return diffDays(this.tlStart, iso) * this.dayWidth;
  }
  dateAt(px: number): string {
    return addDaysIso(this.tlStart, Math.round(px / this.dayWidth));
  }
  clampPan(p: number): number {
    return Math.max(0, Math.min(p, this.totalPx - this.viewportW));
  }
  /** 当前可视日期窗（含缓冲后送 store.ensureDataWindow 判定）。 */
  visibleDates(): { start: string; end: string } {
    return { start: this.dateAt(this.pan), end: this.dateAt(this.pan + this.viewportW) };
  }
  viewportCenterDate(): string {
    return this.dateAt(this.pan + this.viewportW / 2);
  }

  /* ───────────── 视窗动作（平移/缩放/今天；纯前端，取数走 ensureDataWindow） ───────────── */

  setViewportW(w: number) {
    this.viewportW = Math.max(w, 50);
  }
  setPan(px: number) {
    this.pan = this.clampPan(px);
    this.maybeSuggestGranularity();
    this.scheduleWindowFetch();
  }
  panBy(deltaPx: number) {
    this.setPan(this.pan + deltaPx);
  }
  /** 平移到指定日期居中（今天导航 300ms 动画的落点；粒度切换锚点同款）。 */
  panToCenter(iso: string) {
    this.setPan(this.xOf(iso) - this.viewportW / 2);
  }
  /** 粒度切换：中心日期锚定不变（§1.4 缩放锚点；150ms 列宽过渡由 CSS 承担）。 */
  setGranularity(g: GanttGranularity, anchor?: string) {
    const center = anchor ?? this.viewportCenterDate();
    const spanDays = diffDays(this.visibleDates().start, this.visibleDates().end);
    this.granularity = g;
    this.panToCenter(center);
    // §1.4：day 视窗拖出 6 个月 → 提示切更优粒度（一次性，可消隐）
    if (g === "day" && spanDays > SUGGEST_GRAN_DAYS) this.suggestGranularity = "week";
    else this.suggestGranularity = null;
    this.scheduleWindowFetch();
  }  /** 缩放（Ctrl+滚轮/⤢）：以光标处日期为锚步进粒度（§3.1 缩放行）。 */
  zoom(atPx: number, dir: 1 | -1) {
    const order: GanttGranularity[] = ["day", "week", "month"];
    const anchor = this.dateAt(atPx);
    const idx = order.indexOf(this.granularity);
    const next = order[Math.max(0, Math.min(idx + dir, order.length - 1))];
    if (next && next !== this.granularity) {
      this.granularity = next;
      // 锚点保持：锚日期相对视口的像素比例不变
      const ratio = (atPx - this.pan) / this.viewportW;
      this.pan = this.clampPan(this.xOf(anchor) - ratio * this.viewportW);
      this.scheduleWindowFetch();
    }
  }
  resetZoom() {
    this.setGranularity("day", this.today);
    this.panToCenter(this.today);
  }
  private maybeSuggestGranularity() {
    const span = diffDays(this.visibleDates().start, this.visibleDates().end);
    this.suggestGranularity =
      this.granularity === "day" && span > SUGGEST_GRAN_DAYS ? "week" : null;
  }

  /* ───────────── 取数管线（视窗行 + 连线 + 未排期 + 概览） ─────────────
   *  单飞纪律：reqInflight 计数器是真源（loading/prefetching 是派生 UI 态，会被
   *  迟到丢弃路径改写——曾因此出现「±120 首屏在途 + ±14 预取抢跑窄化数据窗」的
   *  交错，进而连累粒度切换二次取数与乐观写回读）；ensure 在飞行中只登记
   *  pendingEnsure，落定后重估一次（余量 ≥ 缓冲即停机）。 */

  /** 平移防抖 150ms 后判界预取（§2.2 时序图第 3~5 步）。 */
  scheduleWindowFetch() {
    if (this.debounceTimer) clearTimeout(this.debounceTimer);
    this.debounceTimer = setTimeout(() => {
      void this.ensureDataWindow();
    }, DEBOUNCE_MS);
  }

  /** 可视范围越出已取视窗（数据窗边缘距可视边缘 <7 天缓冲）才发请求——像素平移
   *  与数据视窗解耦；取数窗 = 可视 ±14d（取后边缘余量 14 ≥ 7，天然停机不循环）。 */
  async ensureDataWindow(): Promise<void> {
    if (this.loading || this.reqInflight > 0) {
      this.pendingEnsure = true;
      return;
    }
    const vis = this.visibleDates();
    const buf = 7;
    // 左/右边缘余量（diffDays(a,b)=b−a）：左 = vis.start − dv.start，右 = dv.end − vis.end；
    // 两侧均 ≥ buf（数据窗足够罩住可视 ±缓冲）才免取
    const need =
      !this.dataViewport.start ||
      diffDays(this.dataViewport.start, vis.start) < buf ||
      diffDays(vis.end, this.dataViewport.end) < buf;
    if (!need) return;
    const start = addDaysIso(vis.start, -14);
    const end = addDaysIso(vis.end, 14);
    await this.fetchRows(start, end, { replace: true });
  }

  /** 上下文变更（路由/视图/筛选）→ 整体重载。 */
  setCtx(ctx: GanttStoreCtx, reload: boolean) {
    this.ctx = ctx;
    if (reload) void this.reload();
  }

  async reload() {
    this.loading = true;
    this.nextCursor = null;
    const vis = this.visibleDates();
    const start = !this.dataViewport.start || diffDays(vis.start, this.dataViewport.start) > 0
      ? addDaysIso(vis.start, -120)
      : this.dataViewport.start;
    const end = !this.dataViewport.end || diffDays(this.dataViewport.end, vis.end) > 0
      ? addDaysIso(vis.end, 120)
      : this.dataViewport.end;
    // 初始视窗 today±120d：三粒度默认列宽下切换均不触发二次取数（§1.4 数据不变）
    await this.fetchRows(start, end, { replace: true });
    void this.fetchOverdue();
  }

  /** 视窗行取数（GET …/gantt/）+ 连线批量（relation_count>0 才发，§2.1）。
   *  迟到响应（seq 落后）整包丢弃——被丢弃的定窗请求不窄化 dataViewport。 */
  async fetchRows(viewportStart: string, viewportEnd: string, opts: { replace: boolean; cursor?: string; append?: boolean }) {
    const seq = ++this.reqSeq;
    this.reqInflight += 1;
    const first = !this.dataViewport.start;
    if (!opts.append) this.prefetching = true;
    try {
      const r = await GanttAPI.rows(this.ctx.slug, this.ctx.projectId, {
        ...this.ctx.listParams,
        granularity: this.granularity,
        viewport_start: viewportStart,
        viewport_end: viewportEnd,
        per_page: ROW_PAGE,
        ...(opts.cursor ? { cursor: opts.cursor } : {}),
      });
      const env = r as unknown as {
        data: { rows: GanttRow[]; unscheduled_count: number };
        meta: {
          next_cursor: string | null; total_count: number; today: string;
          granularity: string;
          viewport: { start: string; end: string };
        };
      };
      if (seq !== this.reqSeq) return; // 迟到响应（本地乐观写 bump 过 reqSeq）
      runInAction(() => {
        const rows = env.data.rows ?? [];
        for (const row of rows) this.rowsById.set(row.id, row);
        if (opts.append) {
          const seen = new Set(this.rowOrder);
          this.rowOrder = [...this.rowOrder, ...rows.map((x) => x.id).filter((id) => !seen.has(id))];
        } else {
          this.rowOrder = rows.map((x) => x.id);
        }
        this.unscheduledCount = env.data.unscheduled_count ?? 0;
        this.totalCount = env.meta?.total_count ?? rows.length;
        if (env.meta?.today) this.today = env.meta.today;
        this.nextCursor = env.meta?.next_cursor ?? null;
        this.dataViewport = { start: viewportStart, end: viewportEnd };
        this.prefetching = false;
        this.prefetchFailed = false;
        this.loading = false;
        // 初始折叠回显（BR-10：视图保存的 collapsed 随行下发）
        if (first) {
          for (const row of rows) if (row.collapsed) this.collapsed.add(row.id);
        }
      });
      void this.fetchEdges();
    } catch {
      if (seq === this.reqSeq) {
        runInAction(() => {
          this.prefetching = false;
          this.loading = false;
          if (this.dataViewport.start) this.prefetchFailed = true; // 预取失败黄条（§2.4）
        });
      }
    } finally {
      this.reqInflight -= 1;
      // 飞行期间登记过的 ensure 重估一次（余量足即停机）
      if (this.pendingEnsure && this.reqInflight === 0) {
        this.pendingEnsure = false;
        void this.ensureDataWindow();
      }
    }
  }

  /** 行滚动 80% 预取下一页（§2.1 H 步）。 */
  async maybePrefetchRows(renderedEndRatio: number) {
    if (renderedEndRatio < PREFETCH_RATIO) return;
    if (!this.nextCursor || this.reqInflight > 0 || this.loading) return;
    await this.fetchRows(this.dataViewport.start, this.dataViewport.end, {
      replace: false, append: true, cursor: this.nextCursor,
    });
  }

  /** 连线批量：仅对视窗内 relation_count>0 的行、60/批（§2.1/§2.5）。 */
  async fetchEdges() {
    const ids = this.rowOrder
      .map((id) => this.rowsById.get(id))
      .filter((r): r is GanttRow => !!r && r.relation_count > 0)
      .map((r) => r.id);
    if (ids.length === 0) {
      runInAction(() => { this.edges = []; });
      return;
    }
    const collected: GanttEdge[] = [];
    for (let i = 0; i < ids.length; i += BULK_MAX) {
      try {
        const r = await GanttAPI.relationsBulk(this.ctx.slug, this.ctx.projectId, ids.slice(i, i + BULK_MAX));
        const env = r as unknown as { data: { edges: GanttEdge[] } };
        collected.push(...(env.data.edges ?? []));
      } catch { /* 连线失败不阻塞渲染（行集已就绪） */ }
    }
    const seen = new Set(this.edges.map((e) => `${e.from_issue_id}|${e.to_issue_id}|${e.relation_type}`));
    for (const e of collected) seen.add(`${e.from_issue_id}|${e.to_issue_id}|${e.relation_type}`);
    runInAction(() => {
      const merged = [...this.edges];
      const have = new Set(merged.map((e) => `${e.from_issue_id}|${e.to_issue_id}|${e.relation_type}`));
      for (const e of collected) {
        const k = `${e.from_issue_id}|${e.to_issue_id}|${e.relation_type}`;
        if (!have.has(k)) merged.push(e);
      }
      // 修剪两端都已不在行集的陈旧边（BR-07 只画可见边）
      this.edges = merged.filter((e) => this.rowsById.has(e.from_issue_id) && this.rowsById.has(e.to_issue_id));
    });
  }

  async retryPrefetch() {
    this.prefetchFailed = false;
    await this.ensureDataWindow();
  }

  /* ───────────── 未排期区（§3.1/§3.4 + GANTT-002 入轨） ───────────── */

  async openUnscheduled(open: boolean) {
    this.unscheduledOpen = open;
    if (open && this.unscheduledRows.length === 0 && this.unscheduledCount > 0) {
      await this.fetchUnscheduled();
    }
  }

  async fetchUnscheduled() {
    try {
      const r = await GanttAPI.unscheduled(this.ctx.slug, this.ctx.projectId, {
        ...this.ctx.listParams, per_page: 50,
      });
      const env = r as unknown as { data: GanttUnscheduledRow[]; meta: { total_count: number } };
      runInAction(() => {
        this.unscheduledRows = env.data ?? [];
        this.unscheduledCount = env.meta?.total_count ?? this.unscheduledRows.length;
      });
    } catch { /* 折叠区失败保留计数 */ }
  }

  /** 未排期入轨（GANTT-002 §1.2 表末行）：start=落点、target=start+2d（3 天默认工期），
   *  乐观成条 + 失败回滚（计数复原）。写通道 = Issue PATCH（BR-01）。 */
  async dropUnscheduled(row: GanttUnscheduledRow, startIso: string): Promise<boolean> {
    const target = addDaysIso(startIso, UNSCHED_SPAN_DAYS - 1);
    runInAction(() => {
      this.unscheduledRows = this.unscheduledRows.filter((u) => u.id !== row.id);
      this.unscheduledCount = Math.max(0, this.unscheduledCount - 1);
      // 乐观成条（服务端字段随后台刷新收敛；progress 0/unstarted 与 §1.2 口径一致）
      this.rowsById.set(row.id, {
        id: row.id, issue_key: row.issue_key, name: row.name, depth: 0,
        has_children: false, collapsed: false, start_date: startIso, target_date: target,
        progress: 0, progress_source: "state", state_group: row.state_group,
        state_color: row.state_color, is_overdue: false, assignee_ids: row.assignee_ids,
        is_aggregated: false, relation_count: 0, estimate_minutes: null, spent_minutes: 0,
      });
      this.rowOrder = [...this.rowOrder, row.id];
      this.totalCount += 1;
    });
    const ok = await this.patchDates(row.id, startIso, target, { start: null, target: null });
    if (ok) {
      void this.fetchUnscheduled();
    } else {
      runInAction(() => {
        this.unscheduledRows = [row, ...this.unscheduledRows];
        this.unscheduledCount += 1;
        this.rowsById.delete(row.id);
        this.rowOrder = this.rowOrder.filter((id) => id !== row.id);
        this.totalCount -= 1;
      });
    }
    return ok;
  }

  /* ───────────── 延期概览（GANTT-002 §3.2；429 处理见 §4.2.1 要点 4） ───────────── */

  async fetchOverdue() {
    try {
      const r = await GanttAPI.overdueSummary(this.ctx.slug, this.ctx.projectId, { ...this.ctx.listParams });
      const env = r as unknown as { data: GanttOverdueSummary };
      runInAction(() => {
        this.overdue = env.data ?? null;
        this.overdueThrottled = false;
      });
    } catch (err) {
      const code = (err as { code?: string }).code;
      if (code === "RATE_LIMIT_EXCEEDED") {
        // 10/min 限流：概览是加速器不是阻断器——保留旧值 + 角标提示（axios 已有全局 toast）
        runInAction(() => { this.overdueThrottled = true; });
      }
    }
  }

  /* ───────────── 行树（折叠/可见行/几何） ───────────── */

  toggleCollapse(id: string) {
    if (this.collapsed.has(id)) this.collapsed.delete(id);
    else this.collapsed.add(id);
    this.ctx.onCollapseChange?.([...this.collapsed]);
  }

  /** 折叠过滤：收起的父行吞掉后续更深层行（原型 visibleRows 同构）。 */
  visibleRows(): GanttRow[] {
    const out: GanttRow[] = [];
    let skipDepth = -1;
    for (const id of this.rowOrder) {
      const row = this.rowsById.get(id);
      if (!row) continue;
      if (skipDepth >= 0) {
        if (row.depth > skipDepth) continue;
        skipDepth = -1;
      }
      out.push(row);
      if (row.has_children && this.collapsed.has(id)) skipDepth = row.depth;
    }
    return out;
  }

  /** 条几何（§4.4.1 barGeometry）：BR-02 开放端 = NULL 端贴时间轴边界 + 渐隐。 */
  barGeometry(row: GanttRow): { left: number; width: number; openStart: boolean; openEnd: boolean } {
    const dw = this.dayWidth;
    const sRaw = row.start_date ?? this.tlStart;
    const eRaw = row.target_date ?? this.tlEnd;
    const s = Math.max(this.xOf(sRaw), 0);
    const e = Math.min(this.xOf(eRaw) + dw, this.totalPx);
    return {
      left: s,
      width: Math.max(4, e - s),
      openStart: row.start_date === null,
      openEnd: row.target_date === null,
    };
  }

  selectRow(id: string | null) {
    this.selectedRowId = id;
  }

  /* ───────────── 拖拽会话（GANTT-002 §4.4：preview/commit/rollback） ───────────── */

  /** 拖拽预览（钳制 + Δ徽标 + 冲突实时检测，§3.1 反馈表）。 */
  previewDrag(issueId: string, mode: DragMode, next: { start: string | null; target: string | null }, deltaDays: number, prevBadge: GanttDragState | null) {
    const row = this.rowsById.get(issueId);
    if (!row) return;
    let start = next.start;
    let target = next.target;
    let clamped = false;
    // BR-03 钳制：start ≤ target-1d / target ≥ start+1d（DB chk 双保险）
    if (start && target && start > target) {
      clamped = true;
      if (mode === "resize-start") start = addDaysIso(target, -1);
      else if (mode === "resize-end") target = addDaysIso(start, 1);
      else start = addDaysIso(target, -(diffDays(start, target)));
    }
    if (mode === "resize-start" && !start && target) start = target;
    if (mode === "resize-end" && !target && start) target = start;
    const conflict = this.conflictFor(issueId, start, target);
    const origSpan = row.start_date && row.target_date ? diffDays(row.start_date, row.target_date) + 1 : 0;
    const span = start && target ? diffDays(start, target) + 1 : 0;
    let badge = "";
    if (mode === "move") badge = `Δ ${deltaDays >= 0 ? "+" : ""}${deltaDays}d`;
    else badge = `工期 ${origSpan}d → ${span}d`;
    if (clamped) badge += "（已钳制）";
    if (conflict) badge += " ⚠ 依赖冲突";
    this.drag = {
      issueId, mode, start, target, deltaDays, clamped,
      conflict: !!conflict, conflictWith: conflict ?? null, badge,
    };
    if (prevBadge?.badge !== this.drag.badge) this.kbAnnounce = this.drag.badge;
  }

  clearDrag() {
    this.drag = null;
  }

  /** 冲突弹层「取消」：预览态弹回原位（300ms ease-back，§3.1）。 */
  cancelDrag() {
    const id = this.drag?.issueId ?? null;
    this.drag = null;
    if (id) {
      this.rollbackId = id;
      setTimeout(() => runInAction(() => { if (this.rollbackId === id) this.rollbackId = null; }), ROLLBACK_MS);
    }
  }

  /** 依赖冲突检测（§4.4 冲突实时检测）：被拖条为被阻塞方且新起 < 阻塞方终，或反向。
   *  返回冲突前置任务编号（确认弹层文案），无冲突 null。 */
  conflictFor(issueId: string, start: string | null, target: string | null): string | null {
    for (const e of this.edges) {
      if (e.relation_type !== "blocks") continue;
      if (e.to_issue_id === issueId && start && e.from.target_date && start < e.from.target_date) {
        return e.from.issue_key;
      }
      if (e.from_issue_id === issueId && target) {
        const toRow = this.rowsById.get(e.to_issue_id);
        const toStart = toRow?.start_date ?? e.to.start_date;
        if (toStart && toStart < target) return e.to.issue_key;
      }
    }
    return null;
  }

  /** 松手提交（§2.1 K→O）：乐观更新 → PATCH（仅 start/target，BR-01）→ 失败回滚。 */
  async commitDrag(): Promise<boolean> {
    const d = this.drag;
    this.drag = null;
    if (!d) return false;
    return this.patchDates(d.issueId, d.start, d.target);
  }

  /** Issue PATCH 唯一写通道（GANTT-002 BR-01：三手势/键盘/入轨全收敛于此）。
   *  仅发送与 orig 快照不同的字段（调终点手势只写 target_date）；orig 缺省取
   *  当前行（乐观写回前的原值）。无变更（钳制回原值）不发请求。 */
  async patchDates(
    issueId: string, start: string | null, target: string | null,
    orig?: { start: string | null; target: string | null },
  ): Promise<boolean> {
    const row = this.rowsById.get(issueId);
    const base = orig ?? (row ? { start: row.start_date, target: row.target_date } : null);
    const payload: { start_date?: string; target_date?: string } = {};
    if (start && (!base || base.start !== start)) payload.start_date = start;
    if (target && (!base || base.target !== target)) payload.target_date = target;
    if (!payload.start_date && !payload.target_date) return true;
    // 本地变更后丢弃一切在途视窗响应（其快照早于本次乐观写——曾把拖拽就位的条
    // 悄悄弹回旧日期：seq 门在 fetchRows 侧按 reqSeq 丢弃）
    this.reqSeq += 1;
    const snapshot = row ? { start: row.start_date, target: row.target_date } : null;
    if (row) {
      runInAction(() => {
        this.rowsById.set(issueId, {
          ...row,
          ...(payload.start_date ? { start_date: payload.start_date } : {}),
          ...(payload.target_date ? { target_date: payload.target_date } : {}),
        });
      });
    }
    try {
      await IssueAPI.patch(this.ctx.slug, this.ctx.projectId, issueId, payload);
      this.recomputeViolations(issueId);
      return true;
    } catch (err) {
      const e = err as { request_id?: string };
      runInAction(() => {
        if (snapshot && row) {
          this.rowsById.set(issueId, {
            ...row,
            start_date: snapshot.start,
            target_date: snapshot.target,
          });
        }
        this.rollbackId = issueId; // 300ms ease-back 弹回（§3.1）
      });
      setTimeout(() => runInAction(() => { if (this.rollbackId === issueId) this.rollbackId = null; }), ROLLBACK_MS);
      toast(`改期失败已回滚${e.request_id ? `（${e.request_id}）` : ""}`, "error");
      return false;
    }
  }

  /** 改期后本地重算相关 blocks 边 violation（红点持续提示，BR-06 提示非拦截）。 */
  recomputeViolations(issueId: string) {
    for (const e of this.edges) {
      if (e.relation_type !== "blocks") continue;
      if (e.to_issue_id !== issueId && e.from_issue_id !== issueId) continue;
      const fromRow = this.rowsById.get(e.from_issue_id);
      const toRow = this.rowsById.get(e.to_issue_id);
      const fromTarget = fromRow?.target_date ?? e.from.target_date;
      const toStart = toRow?.start_date ?? e.to.start_date;
      e.to.violation = !!(fromTarget && toStart && toStart < fromTarget);
    }
  }

  /* ───────────── 键盘改期（§3.4：300ms 合并提交） ───────────── */

  /** Shift+←/→ 平移、Alt+←/→ 调起、Alt+Shift+←/→ 调止（各 ±1 天）。 */
  kbAdjust(issueId: string, mode: "move" | "start" | "target", dir: 1 | -1) {
    const row = this.rowsById.get(issueId);
    if (!row) return;
    const pending = this.kbPending && this.kbPending.id === issueId ? this.kbPending : null;
    const base = pending
      ? { start: pending.start, target: pending.target }
      : { start: row.start_date, target: row.target_date };
    const orig = pending ? pending.orig : { start: row.start_date, target: row.target_date };
    let { start, target } = base;
    if (mode === "move") {
      if (start) start = addDaysIso(start, dir);
      if (target) target = addDaysIso(target, dir);
    } else if (mode === "start" && target) {
      start = addDaysIso(start ?? target, dir);
      if (start > target) start = target; // BR-03 钳制
    } else if (mode === "target" && start) {
      target = addDaysIso(target ?? start, dir);
      if (target < start) target = start;
    }
    this.kbPending = { id: issueId, start, target, orig };
    const movedFrom = base.start ?? base.target ?? null;
    const movedTo = start ?? target;
    runInAction(() => {
      this.rowsById.set(issueId, { ...row, start_date: start, target_date: target });
      if (movedFrom && movedTo && mode === "move") {
        this.kbAnnounce = `向后移动 ${Math.abs(diffDays(movedFrom, movedTo))} 天`;
      }
    });
    if (this.kbTimer) clearTimeout(this.kbTimer);
    this.kbTimer = setTimeout(() => {
      const p = this.kbPending;
      this.kbPending = null;
      if (p && (p.start !== p.orig.start || p.target !== p.orig.target)) {
        void this.patchDates(p.id, p.start, p.target, p.orig);
      }
    }, KB_MERGE_MS);
  }

  /* ───────────── 实时（BR-14：单行 upsert + 单实体增量拉取，不全量刷新） ───────────── */

  /** issue.updated 事件到达：version 门（ADR-0021 数值化比较）→ 单实体拉取补日期。 */
  async onIssueUpdated(issueId: string, version: string | undefined) {
    const ts = versionTs(version);
    const seen = this.versionGate.get(issueId) ?? 0;
    if (ts <= seen) return; // 旧于等于本地已应用版本 → 忽略（COLLAB-004 BR-07）
    this.versionGate.set(issueId, ts);
    if (!this.rowsById.has(issueId)) return;
    try {
      const r = await IssueAPI.detail(this.ctx.slug, this.ctx.projectId, issueId);
      const env = r as unknown as { data: Record<string, unknown> };
      const d = env.data ?? {};
      const cur = this.rowsById.get(issueId);
      if (!cur) return;
      const serverTs = versionTs(d.updated_at as string | undefined);
      if (serverTs && serverTs < ts - 1) return; // 拉到的实体早于事件 → 等下一事件
      runInAction(() => {
        this.rowsById.set(issueId, {
          ...cur,
          name: (d.name as string) ?? cur.name,
          start_date: (d.start_date as string | null) ?? null,
          target_date: (d.target_date as string | null) ?? null,
          state_group: (d.state_group as string) ?? cur.state_group,
        });
      });
      this.recomputeViolations(issueId);
    } catch { /* 单实体拉取失败：保持本地，下一事件再收敛 */ }
  }
}
