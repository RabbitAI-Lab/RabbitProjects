/**
 * 甘特图表区（GANTT-001 §3.1~§3.6 渲染层 + GANTT-002 §3.1~§3.4 交互层）。
 *
 *  分层（§4.4.2）：左栏行树（role=tree，240~480px 可拖宽）｜时间表头（窗口化列）｜
 *  行区（@tanstack/react-virtual 虚拟滚动 36px 行高——滚动 500 行 DOM 节点数恒定，
 *  §7.2-1）｜任务条（七态样式矩阵 §3.2）｜连线 SVG（四型 + 冲突红点脉冲 §3.3）｜
 *  今日线/底纹。
 *
 *  交互：三手势拖拽（阈值激活/钳制/Δ徽标/冲突脉冲/确认弹层/失败回滚）、未排期
 *  拖入（3 天默认）、键盘全套（↑↓←→/Home/End/Enter/1/2/3/T/Shift+←→/Alt 组合，
 *  300ms 合并提交）、PNG 导出（⋯/⌘E）。
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { observer } from "mobx-react-lite";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  addDaysIso, diffDays, fmtCn, HOLIDAYS, isoToUtc, ROW_H, UNSCHED_SPAN_DAYS,
  type DragMode, type GanttStore,
} from "../../stores/gantt";
import type { GanttRow, GanttUnscheduledRow } from "../../services/api";
import { exportGanttPng } from "./exportPng";
import { toast } from "../Toast";

const STATE_CN: Record<string, string> = {
  completed: "已完成", started: "进行中", unstarted: "未开始", backlog: "未开始", cancelled: "已取消",
};

function weekday(iso: string): number {
  return new Date(isoToUtc(iso)).getUTCDay();
}
/** 周一为周首（§1.4 对齐）。 */
function weekStart(iso: string): string {
  const wd = weekday(iso);
  return addDaysIso(iso, wd === 0 ? -6 : 1 - wd);
}
function monthStart(iso: string): string {
  return `${iso.slice(0, 7)}-01`;
}

/** 行摘要 aria-label（§3.6：屏幕阅读器可线性消费）。 */
const EMPTY_SET = new Set<string>();

function rowAria(r: GanttRow): string {
  return `${r.issue_key} ${r.name}，${r.start_date ? fmtCn(r.start_date) : "未设置开始"}至${r.target_date ? fmtCn(r.target_date) : "未设置截止"}，进度 ${r.progress}%，${STATE_CN[r.state_group] ?? r.state_group}`;
}

export interface GanttChartProps {
  store: GanttStore;
  memberName: (uid: string) => string;
  openPeek: (id: string) => void;
  onOpenList: () => void;
  projectName: string;
  viewName: string;
  userName: string;
  canEdit: boolean;
  /** 导出入口（工具条 ⋯ 注入；键盘 ⌘E 走组件内同一 doExport）。 */
  exportFnRef: React.MutableRefObject<() => void>;
  /** GANTT-003 §3.1：关键路径高亮——关键任务集合与开关（空集=无 CPM 数据）。 */
  criticalIds?: Set<string>;
  criticalOn?: boolean;
}

export const GanttChart = observer(function GanttChart(props: GanttChartProps) {
  const { store, memberName, openPeek, onOpenList, projectName, viewName, userName, canEdit, exportFnRef,
          criticalIds = EMPTY_SET, criticalOn = false } = props;
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const leftBodyRef = useRef<HTMLDivElement | null>(null);
  const headRef = useRef<HTMLDivElement | null>(null);
  const exportRef = useRef<HTMLDivElement | null>(null);
  const badgeRef = useRef<HTMLDivElement | null>(null);
  const initializedRef = useRef(false);
  const [leftW, setLeftW] = useState(320);
  const [granAnim, setGranAnim] = useState(false);
  const [tooltip, setTooltip] = useState<{ row: GanttRow; x: number; y: number } | null>(null);
  const [hlEdge, setHlEdge] = useState<string | null>(null);
  const [confirmDrag, setConfirmDrag] = useState<{ issueId: string; issueKey: string; blocker: string } | null>(null);
  const [exportAsk, setExportAsk] = useState<number | null>(null);
  const [dropHint, setDropHint] = useState<{ left: number } | null>(null);
  const [smallScreen, setSmallScreen] = useState(false);

  const visRows = store.visibleRows();
  const rowIndexOf = new Map(visRows.map((r, i) => [r.id, i]));

  /* ── 虚拟滚动（§4.4.2：36px 行高、overScan 10——DOM 节点数恒定） ── */
  const virtualizer = useVirtualizer({
    count: visRows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_H,
    overscan: 10,
  });
  const totalSize = Math.max(virtualizer.getTotalSize(), 120);

  /* ── 挂载：量视口 → 定位今天 → 首屏取数（§2.1 首屏加载流程） ── */
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (el) {
      store.setViewportW(el.clientWidth);
      store.panToCenter(store.today);
    }
    if (!initializedRef.current) {
      initializedRef.current = true;
      void store.reload();
    }
    const ro = new ResizeObserver(() => {
      if (scrollRef.current) store.setViewportW(scrollRef.current.clientWidth);
    });
    if (el) ro.observe(el);
    const mq = window.matchMedia("(max-width: 1023px)");
    const onMq = () => setSmallScreen(mq.matches);
    onMq();
    mq.addEventListener("change", onMq);
    return () => { ro.disconnect(); mq.removeEventListener("change", onMq); };
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [store]);

  /* ── 粒度切换 150ms 列宽过渡（§1.4；平移无过渡——像素跟手） ── */
  const granRef = useRef(store.granularity);
  useEffect(() => {
    if (granRef.current !== store.granularity) {
      granRef.current = store.granularity;
      setGranAnim(true);
      const t = setTimeout(() => setGranAnim(false), 160);
      return () => clearTimeout(t);
    }
  }, [store.granularity]);

  /* ── 导出（GANTT-002 §3.3/§4.3.3） ── */
  const runExport = useCallback(async () => {
    const container = exportRef.current;
    if (!container) return;
    toast("正在渲染…", "info", { ttl: 1500 });
    await exportGanttPng(container, { projectName, viewName, userName });
  }, [projectName, viewName, userName]);

  const doExport = useCallback(() => {
    if (visRows.length > 200) { setExportAsk(visRows.length); return; } // §3.3 范围提示
    void runExport();
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [runExport, visRows.length]);

  /** 工具条 ⋯/⌘E 经页面的 exportFnRef 桥接到此（容器 ref 与确认弹层在本组件）。 */
  useEffect(() => {
    exportFnRef.current = doExport;
    return () => { exportFnRef.current = () => {}; };
  }, [doExport, exportFnRef]);

  /* ── 全局指针交互（平移 / 三手势拖拽 / 未排期入轨；窗口级监听挂一次） ── */
  const panRef = useRef<{ x: number; pan: number } | null>(null);
  const pendingRef = useRef<{ issueId: string; mode: DragMode; x0: number; s0: string | null; e0: string | null } | null>(null);
  const dragRef = useRef<{ issueId: string; mode: DragMode; x0: number; s0: string | null; e0: string | null; moved: boolean } | null>(null);
  const unschedRef = useRef<{ row: GanttUnscheduledRow; over: boolean; date: string } | null>(null);
  const resizeColRef = useRef(false);

  const setBadge = (text: string | null, x: number, y: number, bad = false) => {
    const b = badgeRef.current;
    if (!b) return;
    if (text === null) { b.style.display = "none"; return; }
    b.style.display = "block";
    b.textContent = text;
    b.style.left = `${x + 14}px`;
    b.style.top = `${y - 30}px`;
    b.classList.toggle("bad", bad);
  };

  /** 阈值激活（原型：>4px 才升级为拖拽——纯单击不被吞）。 */
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (panRef.current) {
        store.setPan(panRef.current.pan - (e.clientX - panRef.current.x));
      }
      if (resizeColRef.current) {
        setLeftW((w) => Math.max(240, Math.min(480, w + e.movementX)));
      }
      if (!dragRef.current && pendingRef.current && Math.abs(e.clientX - pendingRef.current.x0) > 4) {
        dragRef.current = { ...pendingRef.current, moved: true };
        pendingRef.current = null;
      }
      if (dragRef.current) {
        const ctx = dragRef.current;
        ctx.moved = true;
        const dDays = Math.round((e.clientX - ctx.x0) / store.dayWidth);
        let start = ctx.s0;
        let target = ctx.e0;
        if (ctx.mode === "move") {
          if (start) start = addDaysIso(start, dDays);
          if (target) target = addDaysIso(target, dDays);
        } else if (ctx.mode === "resize-start") {
          if (ctx.s0) start = addDaysIso(ctx.s0, dDays);
          else if (ctx.e0) start = addDaysIso(ctx.e0, dDays);
        } else {
          if (ctx.e0) target = addDaysIso(ctx.e0, dDays);
          else if (ctx.s0) target = addDaysIso(ctx.s0, dDays);
        }
        store.previewDrag(ctx.issueId, ctx.mode, { start, target }, dDays, store.drag);
        setBadge(store.drag?.badge ?? "", e.clientX, e.clientY, !!store.drag?.clamped);
      }
      if (unschedRef.current) {
        const body = scrollRef.current;
        if (body) {
          const r = body.getBoundingClientRect();
          const over = e.clientX > r.left && e.clientX < r.right && e.clientY > r.top && e.clientY < r.bottom;
          const x = e.clientX - r.left + store.pan;
          const d = store.dateAt(Math.max(0, x));
          unschedRef.current.over = over;
          unschedRef.current.date = d;
          setDropHint(over ? { left: store.xOf(d) } : null);
          setBadge(over ? `排期至 ${fmtCn(d)}（${UNSCHED_SPAN_DAYS} 天）` : "拖入时间轴排期", e.clientX, e.clientY);
        }
      }
    };
    const onUp = () => {
      document.querySelector(".rp-g-body")?.classList.remove("panning");
      if (panRef.current) panRef.current = null;
      if (resizeColRef.current) resizeColRef.current = false;
      if (unschedRef.current) {
        const u = unschedRef.current;
        unschedRef.current = null;
        setDropHint(null);
        setBadge(null, 0, 0);
        if (u.over) void store.dropUnscheduled(u.row, u.date);
        return;
      }
      if (dragRef.current) {
        const ctx = dragRef.current;
        dragRef.current = null;
        setBadge(null, 0, 0);
        const drag = store.drag;
        if (!ctx.moved || !drag) { store.clearDrag(); return; }
        if (drag.conflict && drag.conflictWith) {
          // §2.1 J：连线红点脉冲 + 松手确认弹层（「仍按此排期」/ 取消）
          setConfirmDrag({ issueId: ctx.issueId, issueKey: store.rowsById.get(ctx.issueId)?.issue_key ?? "", blocker: drag.conflictWith });
          return; // store.drag 保留（条停在落点，弹层裁决）
        }
        void store.commitDrag();
        return;
      }
      if (pendingRef.current) {
        // 纯单击（未过阈值）：选中 + 打开详情 Drawer（§3.4 条点击行，URL ?peekIssue=）
        const p = pendingRef.current;
        pendingRef.current = null;
        store.selectRow(p.issueId);
        openPeek(p.issueId);
        return;
      }
      pendingRef.current = null;
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [store, openPeek]);

  /* ── 键盘（§3.6 全套 + GANTT-002 §3.4 键盘改期）——行集经 ref 读最新值（每渲染重建的
   *  派生结构不进依赖数组，订阅恒一次） ── */
  const rowsRef = useRef<{ rows: GanttRow[]; indexOf: Map<string, number> }>({ rows: [], indexOf: new Map() });
  rowsRef.current = { rows: visRows, indexOf: rowIndexOf };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const tag = t?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t?.isContentEditable) return;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "e") { e.preventDefault(); exportFnRef.current(); return; }
      if (e.key === "Escape") {
        if (confirmDrag) { store.cancelDrag(); setConfirmDrag(null); }
        else if (exportAsk != null) setExportAsk(null);
        return;
      }
      const { rows, indexOf } = rowsRef.current;
      const selIdx = store.selectedRowId ? indexOf.get(store.selectedRowId) ?? -1 : -1;
      const selRow = selIdx >= 0 ? rows[selIdx] : undefined;
      if (e.key === "1" || e.key === "2" || e.key === "3") {
        const g = (["day", "week", "month"] as const)[Number(e.key) - 1];
        if (g) store.setGranularity(g);
      } else if (e.key === "t" || e.key === "T") {
        store.panToCenter(store.today);
      } else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
        e.preventDefault();
        if (!selRow) { if (rows[0]) store.selectRow(rows[0].id); return; }
        const ni = e.key === "ArrowDown" ? Math.min(selIdx + 1, rows.length - 1) : Math.max(selIdx - 1, 0);
        const nextRow = rows[ni];
        if (nextRow) store.selectRow(nextRow.id);
      } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        const dir = e.key === "ArrowRight" ? 1 : -1;
        if (e.altKey && e.shiftKey && selRow?.start_date) {
          e.preventDefault();
          store.kbAdjust(selRow.id, "target", dir); // Alt+Shift+←/→ 调终点
        } else if (e.altKey && selRow?.target_date) {
          e.preventDefault();
          store.kbAdjust(selRow.id, "start", dir); // Alt+←/→ 调起点
        } else if (e.shiftKey && selRow && (selRow.start_date || selRow.target_date)) {
          e.preventDefault();
          store.kbAdjust(selRow.id, "move", dir); // Shift+←/→ 平移一天（300ms 合并）
        } else {
          // ←→ 平移一天（周/月粒度平移一列，§3.6）
          const colPan = store.granularity === "day" ? store.dayWidth : store.granularity === "week" ? 7 * store.dayWidth : 30 * store.dayWidth;
          store.panBy(dir * colPan);
        }
      } else if (e.key === "Home") {
        store.setPan(0);
      } else if (e.key === "End") {
        store.setPan(Number.MAX_SAFE_INTEGER);
      } else if (e.key === "Enter" && store.selectedRowId) {
        openPeek(store.selectedRowId);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [store, openPeek, exportFnRef, confirmDrag, exportAsk]);

  /* ── 列头/底纹（窗口化：仅渲染可视 ±缓冲，DOM 有界） ── */
  const dStart = store.dateAt(store.pan - 60);
  const dEnd = store.dateAt(store.pan + store.viewportW + 60);
  const days: string[] = [];
  for (let d = dStart; isoToUtc(d) <= isoToUtc(dEnd); d = addDaysIso(d, 1)) days.push(d);

  const headerCols: Array<{ left: number; w: number; label: string; cls?: string; title?: string | undefined }> = [];
  if (store.granularity === "day") {
    for (const d of days) {
      const wd = weekday(d);
      const hol = HOLIDAYS[d];
      headerCols.push({
        left: store.xOf(d), w: store.dayWidth, label: `${Number(d.slice(8))}`,
        cls: wd === 0 || wd === 6 ? "wknd" : hol ? "hol" : "", title: hol,
      });
    }
  } else if (store.granularity === "week") {
    let w = weekStart(dStart);
    while (isoToUtc(w) <= isoToUtc(dEnd)) {
      const we = addDaysIso(w, 6);
      const width = (diffDays(w, we) + 1) * store.dayWidth;
      headerCols.push({ left: store.xOf(w), w: width, label: `${fmtCn(w)} – ${fmtCn(we)}` });
      w = addDaysIso(we, 1);
    }
  } else {
    let m = monthStart(dStart);
    while (isoToUtc(m) <= isoToUtc(dEnd)) {
      const nextM = `${Number(m.slice(0, 4)) + Math.floor(Number(m.slice(5, 7)) / 12)}-${String((Number(m.slice(5, 7)) % 12) + 1).padStart(2, "0")}-01`;
      const width = (diffDays(m, addDaysIso(nextM, -1)) + 1) * store.dayWidth;
      headerCols.push({ left: store.xOf(m), w: width, label: `${m.slice(0, 4)} 年 ${Number(m.slice(5, 7))} 月` });
      m = nextM;
    }
  }

  /* ── 连线（§3.3 四型 + 冲突红点；BR-07 仅两端行均可见） ── */
  const edgesSvg: React.ReactNode[] = [];
  const dragId = store.drag?.issueId;
  for (const edge of store.edges) {
    const a = rowIndexOf.get(edge.from_issue_id);
    const b = rowIndexOf.get(edge.to_issue_id);
    if (a === undefined || b === undefined) continue;
    const rowA = visRows[a];
    const rowB = visRows[b];
    if (!rowA || !rowB) continue;
    const gA = store.barGeometry(rowA);
    const gB = store.barGeometry(rowB);
    // 起点挂 A 条右缘（BR-08：无日期端锚定今日）；终点挂 B 条左缘
    const fromTarget = dragId === edge.from_issue_id ? store.drag?.target : rowA.target_date;
    const toStart = dragId === edge.to_issue_id ? store.drag?.start : rowB.start_date;
    const x1 = rowA.target_date === null && dragId !== edge.from_issue_id ? store.xOf(store.today) : gA.left + gA.width;
    const y1 = a * ROW_H + 18;
    const x2 = rowB.start_date === null && dragId !== edge.to_issue_id ? store.xOf(store.today) : gB.left;
    const y2 = b * ROW_H + 18;
    const mx = (x1 + x2) / 2;
    const d = `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`;
    const key = `${edge.from_issue_id}|${edge.to_issue_id}`;
    const hl = hlEdge === key || rowA.id === dragId || rowB.id === dragId;
    // 拖动中实时重算 violation（§4.4 冲突实时检测）——预览日期代入两边
    const violationLive =
      edge.relation_type === "blocks" &&
      !!fromTarget && !!toStart && toStart < fromTarget;
    const isBlocks = edge.relation_type === "blocks";
    let dots: React.ReactNode = null;
    if (isBlocks) {
      dots = (
        <>
          <circle cx={x1} cy={y1} r="3" fill="#3f76ff" />
          <path d={`M ${x2 - 5} ${y2 - 4} l 5 4 l -5 4`} fill="none" stroke="#3f76ff" strokeWidth="1.5" />
          {(edge.to.violation || violationLive) && (
            <circle className={`conflict-dot${dragId ? " pulse" : ""}`} data-cdot="1" cx={mx} cy={(y1 + y2) / 2} r="4" />
          )}
        </>
      );
    } else if (edge.relation_type === "duplicates") {
      dots = <rect x={x2 - 4} y={y2 - 4} width="8" height="8" transform={`rotate(45 ${x2} ${y2})`} fill="none" stroke="#9ca3af" />;
    } else {
      dots = (
        <>
          <circle cx={x1} cy={y1} r="2.5" fill="#9ca3af" />
          <circle cx={x2} cy={y2} r="2.5" fill="#9ca3af" />
        </>
      );
    }
    edgesSvg.push(
      <g key={key} data-rel={key}>
        <path d={d} className={`rel-${edge.relation_type}${hl ? " hl" : ""}`} data-rel={key}>
          <title>{`${edge.from.issue_key} → ${edge.to.issue_key}（${edge.relation_type === "blocks" ? "完成前者后才可完成后者" : edge.relation_type === "duplicates" ? "重复于" : "相关"}）`}</title>
        </path>
        {dots}
      </g>,
    );
  }

  /* ── 行渲染（左树 + 条；drag 预览几何覆盖） ── */
  const renderBar = (row: GanttRow) => {
    const isDrag = dragId === row.id;
    const dragStart = isDrag ? store.drag?.start ?? null : row.start_date;
    const dragTarget = isDrag ? store.drag?.target ?? null : row.target_date;
    const sPx = Math.max(store.xOf(dragStart ?? store.tlStart), 0);
    const ePx = Math.min(store.xOf(dragTarget ?? store.tlEnd) + store.dayWidth, store.totalPx);
    const geom = {
      left: sPx,
      width: Math.max(4, ePx - sPx),
      openStart: dragStart === null,
      openEnd: dragTarget === null,
    };
    const stCls = row.state_group === "backlog" ? "st-unstarted" : `st-${row.state_group}`;
    const immutable = row.state_group === "completed" || row.state_group === "cancelled";
    const dragBlocked = !canEdit || smallScreen || immutable;
    const blockedTip = !canEdit
      ? "访客只读：拖拽改期需 CONTRIBUTOR+（PATCH 403）"
      : smallScreen ? "小屏只读：拖拽改期仅桌面端（≥1024px）"
      : immutable ? `${STATE_CN[row.state_group]}任务不可拖拽` : undefined;
    const isCrit = criticalOn && criticalIds.has(row.id);
    const cls = [
      "rp-gbar", row.is_aggregated ? "agg" : stCls,
      criticalOn ? (isCrit ? "crit" : "cp-dim") : "",
      row.is_overdue ? "overdue" : "",
      geom.openStart ? "open-start" : "",
      geom.openEnd ? "open-end" : "",
      row.start_date && row.target_date && row.start_date <= store.today && row.target_date >= store.today ? "cross-today" : "",
      isDrag ? "dragging" : "",
      store.rollbackId === row.id ? "easeback" : "",
      dragBlocked ? "nodrag" : "",
    ].filter(Boolean).join(" ");
    const fill = row.is_aggregated ? row.progress : row.state_group === "completed" ? 100 : row.progress;
    return (
      <div key={row.id} className={cls} data-bar={row.id} data-row-issue={row.id} role="button" tabIndex={0}
        aria-label={rowAria(row)}
        style={{ left: geom.left, width: geom.width }}
        title={blockedTip}
        onMouseEnter={(e) => {
          const rel = store.edges.find((ed) => ed.from_issue_id === row.id || ed.to_issue_id === row.id);
          setHlEdge(rel ? `${rel.from_issue_id}|${rel.to_issue_id}` : null);
          const r = e.currentTarget.getBoundingClientRect();
          setTooltip({ row, x: r.left, y: r.bottom + 8 });
        }}
        onMouseLeave={() => { setTooltip(null); setHlEdge(null); }}
        onMouseDown={(e) => {
          if (e.button !== 0) return;
          e.preventDefault();
          if (dragBlocked) {
            if (blockedTip) toast(blockedTip, "warning", { ttl: 1800 });
            return;
          }
          if (!row.start_date && !row.target_date) return;
          pendingRef.current = { issueId: row.id, mode: "move", x0: e.clientX, s0: row.start_date, e0: row.target_date };
        }}
        onKeyDown={(e) => { if (e.key === "Enter") { e.stopPropagation(); store.selectRow(row.id); openPeek(row.id); } }}>
        <div className="fill" style={{ width: `${fill}%` }} />
        {geom.width >= 80 && <span className="txt">{row.issue_key} {row.name}</span>}
        {row.is_overdue && <span className="warn-ico" aria-label="逾期">⚠</span>}
        {!dragBlocked && (
          <>
            <div className="rz l" data-rz="l" aria-hidden="true"
              onMouseDown={(e) => {
                e.stopPropagation(); e.preventDefault();
                if (e.button !== 0 || !row.target_date) return;
                pendingRef.current = { issueId: row.id, mode: "resize-start", x0: e.clientX, s0: row.start_date, e0: row.target_date };
              }} />
            <div className="rz r" data-rz="r" aria-hidden="true"
              onMouseDown={(e) => {
                e.stopPropagation(); e.preventDefault();
                if (e.button !== 0 || !row.start_date) return;
                pendingRef.current = { issueId: row.id, mode: "resize-end", x0: e.clientX, s0: row.start_date, e0: row.target_date };
              }} />
          </>
        )}
      </div>
    );
  };

  const virtualItems = virtualizer.getVirtualItems();
  const rowNodes = virtualItems.map((vi) => {
    const row = visRows[vi.index];
    if (!row) return null;
    const ghostGeom = dragId === row.id ? store.barGeometry(row) : null;
    return (
      <div key={row.id} style={{ position: "absolute", top: 0, left: 0, width: "100%", height: ROW_H, transform: `translateY(${vi.start}px)`, borderBottom: "1px solid #f0f0f0" }}
        data-vrow={row.id}>
        {renderBar(row)}
        {/* 拖拽原位虚线占位（§3.1 拖起态） */}
        {ghostGeom && (
          <div className="rp-gbar ghosted" style={{ left: ghostGeom.left, width: ghostGeom.width }} aria-hidden="true" />
        )}
      </div>
    );
  });

  const leftNodes = virtualItems.map((vi) => {
    const row = visRows[vi.index];
    if (!row) return null;
    return (
      <div key={row.id}
        className={`rp-grow${store.selectedRowId === row.id ? " selected" : ""}`}
        style={{ transform: `translateY(${vi.start}px)` }}
        role="treeitem" aria-expanded={row.has_children ? !store.collapsed.has(row.id) : undefined}
        aria-label={`${row.issue_key} ${row.name}`} data-grow={row.id} data-sb-scope="gantt-row"
        onClick={() => store.selectRow(row.id)}>
        {row.has_children ? (
          <button type="button" className="caret" data-sb-scope="gantt-caret"
            aria-label={`${store.collapsed.has(row.id) ? "展开" : "折叠"} ${row.issue_key}`}
            onClick={(e) => { e.stopPropagation(); store.toggleCollapse(row.id); }}>
            {store.collapsed.has(row.id) ? "▸" : "▾"}
          </button>
        ) : (
          <span className="caret" style={{ visibility: "hidden" }} aria-hidden="true">·</span>
        )}
        <span style={{ width: row.depth * 16, flexShrink: 0 }} aria-hidden="true" />
        <span className="badge-id">{row.issue_key}</span>
        <span className="ttl" title={row.name}>{row.name}</span>
        {row.estimate_minutes != null && (
          <span className="text-[11px] text-neutral-400 font-mono">{Math.floor(row.spent_minutes / 60)}h/{Math.floor(row.estimate_minutes / 60)}h</span>
        )}
      </div>
    );
  });

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    if (leftBodyRef.current) leftBodyRef.current.scrollTop = el.scrollTop;
    if (totalSize > 0) void store.maybePrefetchRows((el.scrollTop + el.clientHeight) / totalSize);
  };

  const tx = `translateX(${-store.pan}px)`;
  const allUnscheduled = !store.loading && store.totalCount === 0 && store.unscheduledCount > 0;
  const noTasks = !store.loading && store.totalCount === 0 && store.unscheduledCount === 0;

  return (
    <div className="flex flex-col flex-1 min-h-0" data-sb-scope="gantt-chart">
      {/* <1024px 降级提示（§3.6 断点表；拖拽仅桌面） */}
      {smallScreen && (
        <div className="px-5 py-1.5 bg-neutral-100 border-b border-neutral-200 text-[12.5px] text-neutral-500" data-sb-scope="gantt-small-hint">
          建议在桌面端使用——小屏为只读简化时间轴（行上限 200，无拖拽改期）
        </div>
      )}
      {/* 预取失败黄条（§2.4/§3.5：保持既有渲染 + 重试） */}
      {store.prefetchFailed && (
        <div className="flex items-center gap-3 px-5 py-1.5 bg-amber-50 border-b border-amber-200 text-amber-800 text-[12.5px]" role="alert" data-sb-scope="gantt-prefetch-failed">
          实时更新暂停
          <button type="button" className="text-brand-600 hover:underline" onClick={() => void store.retryPrefetch()}>重试</button>
        </div>
      )}
      {noTasks ? (
        <div className="flex flex-col items-center gap-2 py-20 text-neutral-500 flex-1" data-sb-scope="gantt-empty">
          <div className="text-[40px]" aria-hidden="true">📊</div>
          <div className="text-[15px] font-semibold text-neutral-700">创建或导入任务后，排期将在此展示</div>
          <div className="text-[13px]">左侧未排期区可拖入时间轴开始排期</div>
        </div>
      ) : (
        <div className="flex flex-1 min-h-0 overflow-hidden bg-white" data-gantt-wrap data-sb-scope="gantt-wrap">
          {/* ── 左栏行树（C.99；role=tree，240~480px 可拖宽） ── */}
          <div className="relative flex flex-col shrink-0 border-r border-neutral-200 bg-white" style={{ width: leftW }}
            role="tree" aria-label="任务行树" data-sb-scope="gantt-left">
            <div className="rp-g-left-head">
              任务 ({store.totalCount})
              <span className="text-[11px] text-neutral-400 ml-auto">行高 36px</span>
            </div>
            <div className="flex-1 min-h-0 overflow-hidden relative" ref={leftBodyRef}>
              <div style={{ height: totalSize, position: "relative" }}>
                {leftNodes}
                {/* 全未排期引导（§3.5：置顶展开 + 去列表设置日期） */}
                {allUnscheduled && (
                  <div className="absolute left-0 right-0 top-0 bottom-0 flex items-center justify-center text-[13px] text-neutral-400" style={{ height: totalSize }}>
                    全部任务未排期——<span className="text-brand-600">去列表设置日期</span>后回到甘特即时出现
                  </div>
                )}
              </div>
            </div>
            {/* 栏宽拖拽（240~480，C.99） */}
            <div className="rp-g-resizer" role="separator" aria-orientation="vertical" aria-label="调整栏宽（240~480px）" data-sb-scope="gantt-resizer"
              onMouseDown={(e) => { e.preventDefault(); resizeColRef.current = true; }} />
            {/* 未排期折叠区（C.103） */}
            <div className="rp-g-unsched">
              {store.unscheduledCount > 0 && (
                <>
                  <button type="button" className="rp-g-unsched-head" aria-expanded={store.unscheduledOpen} data-sb-scope="gantt-unsched-head"
                    onClick={() => void store.openUnscheduled(!store.unscheduledOpen)}>
                    📥 未排期 ({store.unscheduledCount})
                    <span className="text-[11px] text-neutral-400 ml-auto">{store.unscheduledOpen ? "收起" : "展开"}</span>
                  </button>
                  {store.unscheduledOpen && (
                    <div className="rp-g-unsched-list" data-sb-scope="gantt-unsched-list">
                      {store.unscheduledRows.map((u) => (
                        <div key={u.id} className="rp-g-unsched-row" data-unsched={u.id}
                          title="拖入时间轴排期（默认 3 天工期）；点击跳转列表设置日期"
                          onClick={() => { if (!unschedRef.current) onOpenList(); }}
                          onMouseDown={(e) => {
                            if (e.button !== 0 || !canEdit || smallScreen) return;
                            e.preventDefault();
                            unschedRef.current = { row: u, over: false, date: store.today };
                            setBadge("拖入时间轴排期", e.clientX, e.clientY);
                          }}>
                          <span className="badge-id">{u.issue_key}</span>
                          <span className="ttl" style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{u.name}</span>
                          <span className="text-[11px] text-neutral-400">{u.assignee_ids.map(memberName).join("、")}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>

          {/* ── 时间轴（表头 + 行区；导出根 = 表头/可见行/连线/今日线，BR-11） ── */}
          <div className="flex-1 min-w-0 flex flex-col relative overflow-hidden" ref={exportRef} data-sb-scope="gantt-right">
            <div className="h-10 border-b border-neutral-200 overflow-hidden relative shrink-0 bg-white" ref={headRef}>
              <div style={{ transform: tx, width: store.totalPx, height: 40, position: "absolute", top: 0 }} className={granAnim ? "rp-g-anim" : ""}>
                {headerCols.map((c, i) => (
                  <div key={`${store.granularity}-${i}`} className={`rp-g-col ${c.cls ?? ""}`} style={{ left: c.left, width: c.w }} title={c.title}>
                    {c.label}
                    {c.title && <span className="sub">{c.title}</span>}
                  </div>
                ))}
              </div>
              {store.prefetching && (
                <span className="absolute right-2 top-1/2 -translate-y-1/2 text-[12px] text-neutral-400 animate-pulse" role="status" data-sb-scope="gantt-prefetch-spinner">⟳</span>
              )}
            </div>
            <div ref={scrollRef} className="rp-g-body" role="application" aria-roledescription="甘特图" tabIndex={0}
              aria-label="时间轴：拖拽平移，任务条可拖拽改期" data-sb-scope="gantt-body"
              onScroll={onScroll}
              onMouseDown={(e) => {
                const el = e.target as HTMLElement;
                if (el.closest(".rp-gbar") || e.button !== 0) return;
                panRef.current = { x: e.clientX, pan: store.pan };
                e.currentTarget.classList.add("panning");
              }}
              onWheel={(e) => {
                if (e.ctrlKey) {
                  e.preventDefault();
                  const r = e.currentTarget.getBoundingClientRect();
                  store.zoom(e.clientX - r.left + store.pan, e.deltaY > 0 ? -1 : 1);
                } else if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
                  e.preventDefault();
                  store.panBy(e.deltaX);
                }
              }}>
              <div className={`rp-g-content${granAnim ? " rp-g-anim" : ""}`} style={{ transform: tx, width: store.totalPx, height: totalSize }}>
                {/* 首屏骨架（§3.5：表头骨架 + 12 行条形骨架，占位宽度定长防 CLS） */}
                {store.loading && store.rowOrder.length === 0
                  ? [86, 64, 73, 52, 80, 45, 68, 58, 76, 62, 70, 55].map((w, i) => (
                      <div key={i} className="absolute h-5 rounded bg-neutral-100 animate-pulse" data-sb-scope="gantt-skel"
                        style={{ left: 120 + (i % 4) * 90, width: (w / 100) * (store.viewportW * 0.4), top: i * ROW_H + 8 }} />
                    ))
                  : (
                    <>
                      {/* 周末/节假日底纹（6%/10% 灰，§3.1） */}
                      {days.map((d) => {
                        const wd = weekday(d);
                        const hol = HOLIDAYS[d];
                        if (!hol && wd !== 0 && wd !== 6) return null;
                        return <div key={d} className={hol ? "rp-g-cell-hol" : "rp-g-cell-wknd"} style={{ left: store.xOf(d), width: store.dayWidth }} aria-hidden="true" />;
                      })}
                      {rowNodes}
                      <svg className="rp-glines" width={store.totalPx} height={totalSize} aria-hidden="true">
                        {edgesSvg}
                      </svg>
                      {/* 今日线（2px 红 + 顶部角标「今天」） */}
                      <div className="rp-today-line" style={{ left: store.xOf(store.today) }} data-sb-scope="gantt-today-line">
                        <div className="rp-today-tag">今天</div>
                      </div>
                      {/* 未排期拖入落点高亮（O6） */}
                      {dropHint && (
                        <div className="rp-g-drop-hint" style={{ left: dropHint.left, width: store.dayWidth * UNSCHED_SPAN_DAYS }} aria-hidden="true" />
                      )}
                    </>
                  )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Δ徽标（§3.1：aria-live=polite 播报；导出过滤） */}
      <div ref={badgeRef} className="rp-drag-badge" role="status" aria-live="polite" data-sb-scope="gantt-drag-badge" data-export-hidden="1" style={{ display: "none" }} />
      {/* 键盘改期播报（C.111） */}
      <div className="sr-only" role="status" aria-live="polite">{store.kbAnnounce}</div>

      {/* 条悬浮 tooltip（§3.2：编号/起止/进度/执行人/工时对照——estimate 空不显示该位） */}
      {tooltip && (
        <div className="rp-gtip" data-sb-scope="gantt-bar-tip" data-export-hidden="1"
          style={{ left: Math.min(tooltip.x, window.innerWidth - 280), top: tooltip.y }}>
          <b>{tooltip.row.issue_key} {tooltip.row.name}</b><br />
          <span className="k">起止</span> {tooltip.row.start_date ? fmtCn(tooltip.row.start_date) : "未设置"} – {tooltip.row.target_date ? fmtCn(tooltip.row.target_date) : "未设置"}
          {(tooltip.row.start_date === null || tooltip.row.target_date === null) && `（${tooltip.row.start_date === null ? "未设置开始" : "未设置截止"}日期）`}<br />
          <span className="k">进度</span> {tooltip.row.progress}% · {STATE_CN[tooltip.row.state_group]}
          {tooltip.row.is_overdue && ` · 逾期 ${diffDays(tooltip.row.target_date ?? store.today, store.today)} 天`}
          <br />
          <span className="k">执行</span> {tooltip.row.assignee_ids.map(memberName).join("、") || "未指派"}
          {tooltip.row.estimate_minutes != null && (
            <>
              　<span className="k">工时</span> {Math.floor(tooltip.row.spent_minutes / 60)}/{Math.floor(tooltip.row.estimate_minutes / 60)}h
              {tooltip.row.spent_minutes > tooltip.row.estimate_minutes && <span style={{ color: "#fca5a5" }}>超耗</span>}
            </>
          )}
        </div>
      )}

      {/* 依赖冲突确认弹层（C.107：role=alertdialog + 「仍按此排期」） */}
      {confirmDrag && (
        <div className="fixed inset-0 bg-black/30 backdrop-blur-[2px] z-[90] flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-2xl w-[480px] max-w-full p-6" role="alertdialog" aria-label="仍按此排期？" data-sb-scope="gantt-conflict-dialog">
            <div className="text-[16px] font-semibold mb-4">仍按此排期？</div>
            <div className="text-[13.5px] text-neutral-600 leading-7">
              新排期使该任务早于其前置 <b>{confirmDrag.blocker}</b> 的完成日。仍按此排期？
              <div className="text-[12px] text-neutral-400 mt-1">P2 连线只读不自动顺延——确认后将保存并保留连线红点提示。</div>
            </div>
            <div className="flex justify-end gap-2.5 mt-6">
              <button type="button" className="btn-ghost h-9 px-4 rounded-md border border-neutral-300 text-[13px]" data-sb-scope="gantt-conflict-cancel"
                onClick={() => { store.cancelDrag(); setConfirmDrag(null); }}>取消</button>
              <button type="button" className="h-9 px-4 rounded-md bg-brand-500 text-white text-[13px] font-medium" data-sb-scope="gantt-conflict-ok"
                onClick={() => { setConfirmDrag(null); void store.commitDrag(); }}>仍按此排期</button>
            </div>
          </div>
        </div>
      )}

      {/* 导出范围提示（C.110：可见行 > 200 确认） */}
      {exportAsk != null && (
        <div className="fixed inset-0 bg-black/30 backdrop-blur-[2px] z-[90] flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-2xl w-[440px] max-w-full p-6" role="dialog" aria-label="导出范围提示" data-sb-scope="gantt-export-ask">
            <div className="text-[16px] font-semibold mb-4">导出范围提示</div>
            <div className="text-[13.5px] text-neutral-600">当前可见行 {exportAsk} 行 &gt; 200 行，导出仅包含当前滚动视窗的 200 行。</div>
            <div className="flex justify-end gap-2.5 mt-6">
              <button type="button" className="h-9 px-4 rounded-md border border-neutral-300 text-[13px]" onClick={() => setExportAsk(null)}>取消</button>
              <button type="button" className="h-9 px-4 rounded-md bg-brand-500 text-white text-[13px] font-medium" data-sb-scope="gantt-export-go"
                onClick={() => { setExportAsk(null); void runExport(); }}>继续导出</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
});
