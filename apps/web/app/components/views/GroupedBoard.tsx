import { useEffect, useMemo, useRef, useState } from "react";
import type { FilterLogicNode, IssueViewData } from "@rp/shared-state";
import type { Issue } from "@rp/types";
import { AssigneeAPI, IssueAPI } from "../../services/api";
import type { ApiError } from "../../services/axios";
import { toast } from "../Toast";
import { AvatarStack, BlockedCompleteDialog, blockersFromError, type BlockerItem } from "../issue-dialogs";
import { usePeekHover, type PeekIssue } from "../PeekPopover";
import { useBoardLiveSync } from "../../realtime/useBoardLiveSync";
import type { ViewPage } from "./useViewPage";
import { PRIORITY_COLUMNS } from "./view-dsl";

/** BOARD-003 §3.2 分组看板泛化（C.66/C.67）：五维分组列（前端配置源渲染——响应不内嵌
 *  列元数据 BR-16）、__none__ 哨兵列、空列恒在/折叠、跨维度拖拽 dropToWrite（§2.4 写路径
 *  表）、BR-14 哨兵拦截、BR-15 多值替换确认、失败回滚弹回、120ms 渐隐重排（§3.5）。 */

const BOARD_CSS = `
@keyframes viewfade{from{opacity:0}to{opacity:1}}
.bcard-vf{animation:viewfade .12s ease}
@keyframes boardbounce{0%{transform:scale(.95)}60%{transform:translateX(8px)}100%{transform:translateX(0)}}
article.bcard.bounce-back{animation:boardbounce .3s cubic-bezier(.2,.9,.3,1.1)}
@keyframes boardshake{0%,100%{transform:translateX(0)}20%{transform:translateX(-4px)}40%{transform:translateX(4px)}60%{transform:translateX(-3px)}80%{transform:translateX(3px)}}
section.bcol.shaking{animation:boardshake .4s ease}
/* COLLAB-004 §3.2：远端卡片 300ms 淡入 + 列计数 bump */
@keyframes rpremotein{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}
article.bcard.rp-remote-in{animation:rpremotein .3s ease}
@keyframes rpcountbump{0%{transform:scale(1)}40%{transform:scale(1.25)}100%{transform:scale(1)}}
.rp-count-bump{animation:rpcountbump .6s ease}
`;

const DIM_NAMES: Record<string, string> = {
  state_id: "状态", priority: "优先级", assignee_id: "负责人", label_id: "标签",
};

const todayISO = () => new Date().toISOString().slice(0, 10);

interface GroupCol {
  key: string;
  name: string;
  color?: string | null | undefined;
  avatarName?: string | undefined;
  none?: boolean;
  cfNone?: boolean;
  issues: Issue[];
  total: number;
}

/** BR-15 多值替换确认弹层（执行人/标签通用）。 */
function ReplaceConfirm({ kind, oldNames, newName, onCancel, onOk }: {
  kind: string; oldNames: string[]; newName: string; onCancel: () => void; onOk: () => void;
}) {
  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center p-4 z-[80]" data-sb-scope="board-replace-confirm">
      <div className="bg-white rounded-xl shadow-lg w-[400px] p-5 border-l-4 border-amber-400" role="alertdialog" aria-modal="true" aria-label={`替换${kind}确认`}>
        <div className="flex items-center gap-2 text-base font-semibold mb-3">⚠ 替换{kind}确认</div>
        <p className="text-[13.5px] text-neutral-700">该任务有多个{kind}：<b>{oldNames.join("、")}</b>。跨列拖拽将以 <b>{newName}</b> 全量替换（PUT 语义 · B003 BR-15）。</p>
        <div className="flex justify-end gap-2.5 mt-5">
          <button type="button" onClick={onCancel} className="h-[34px] px-3.5 border border-neutral-300 rounded-md">取消</button>
          <button type="button" data-sb-scope="board-replace-ok" onClick={onOk} className="h-[34px] px-3.5 bg-brand-500 text-white rounded-md hover:bg-brand-600">替换</button>
        </div>
      </div>
    </div>
  );
}

export function GroupedBoard({ vp, workspaceSlug, projectId, canEdit, blockedIds, blockedTipOf, onLoadBlockedTip, onOpenIssue, search, reloadKey = 0, bulk, onCardsLoaded }: {
  vp: ViewPage;
  workspaceSlug?: string | undefined;
  projectId?: string | undefined;
  canEdit: boolean;
  blockedIds: Set<string>;
  blockedTipOf: (issueId: string) => string;
  onLoadBlockedTip: (issueId: string) => void;
  onOpenIssue: (id: string) => void;
  search: string;
  /** 外部数据变更信号（创建任务/抽屉编辑后 +1 → 分组数据重拉） */
  reloadKey?: number;
  /** BOARD-004 §3.1 多选接线（C.77）：⌘/Shift 点选 + 左上角标复选框 + 选中 ring。
   *  由路由层 useBulkSelection 注入；未传时看板保持纯浏览态（零行为变化）。 */
  bulk?: {
    isSelected: (id: string) => boolean;
    anySelected: boolean;
    onCheck: (id: string) => void;
    /** modifier 点击（⌘/Shift）——返回 true 表示已消费（不打开详情）。 */
    onModifierClick: (id: string, ev: { metaKey: boolean; ctrlKey: boolean; shiftKey: boolean }) => boolean;
  };
  /** C.79 ⌘A 作用域上报：已载入卡片 id（列加载/reload 后回调；路由层喂 BulkOperations）。 */
  onCardsLoaded?: (ids: string[]) => void;
}) {
  const { groupBy, effDisplay } = vp;
  const [cols, setCols] = useState<GroupCol[]>([]);
  const [loading, setLoading] = useState(true);
  const [dragId, setDragId] = useState<string | null>(null);
  const [quickCol, setQuickCol] = useState<string | null>(null);
  /** BR-15 替换确认（挂起写操作）。 */
  const [replaceAsk, setReplaceAsk] = useState<null | { kind: string; oldNames: string[]; newName: string; run: () => Promise<void> }>(null);
  /** C.44 M-BLOCKED（state 维度 409 拦截）。 */
  const [blockedDlg, setBlockedDlg] = useState<{ issueName: string; blockers: BlockerItem[]; stateId: string; issueId: string } | null>(null);
  const loadTick = useRef(0);
  const { bind } = usePeekHover();

  // ── Sprint-3 Phase 3-C（COLLAB-004 §3.2/§4.4.2）：远端事件定向更新 + 拖拽保护（最小触点）──
  const { flush: flushLiveQueue } = useBoardLiveSync({
    groupBy,
    cols,
    colGroupOf: (key) => vp.states.find((s) => s.id === key)?.group,
    fetchGrouped: () => vp.fetchGrouped(groupBy),
    setCols: setCols,
    dragging: dragId != null,
    memberName: (uid) => (uid ? vp.members.find((m) => m.id === uid)?.name ?? "成员" : "成员"),
  });

  const showEmpty = effDisplay.show_empty_groups !== false;
  const cardFields = { labels: true, sub_issues: true, attachments: true, estimate: true, priority: false, timer: false, target_date: true, ...(effDisplay.card_fields ?? {}) };
  const columnMetas = vp.columnMetas(groupBy);

  /** 分组数据（§3.5：切换视图/分组数据重拉——SWR key 变化）。 */
  useEffect(() => {
    if (!workspaceSlug || !projectId || columnMetas.length === 0) return;
    const tick = ++loadTick.current;
    setLoading(true);
    void (async () => {
      const env = await vp.fetchGrouped(groupBy);
      if (tick !== loadTick.current) return;
      if (!env) { setCols([]); setLoading(false); return; }
      const next: GroupCol[] = columnMetas.map((meta) => {
        const bucket = env.data?.[meta.key] ?? { results: [] as Issue[], total_results: 0 };
        return { ...meta, issues: (bucket.results as Issue[]) ?? [], total: bucket.total_results ?? 0 };
      });
      setCols(next);
      setLoading(false);
      onCardsLoaded?.(next.flatMap((c) => c.issues.map((i) => i.id))); // C.79 ⌘A 作用域
    })();
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceSlug, projectId, groupBy, vp.viewIdParam, vp.filtersParam, reloadKey, JSON.stringify(columnMetas.map((c) => c.key))]);

  const issueById = useMemo(() => {
    const m = new Map<string, Issue>();
    cols.forEach((c) => c.issues.forEach((i) => m.set(i.id, i)));
    return m;
  }, [cols]);

  const nameOf = (uid: string) => vp.members.find((m) => m.id === uid)?.name ?? "…";

  function bounceBack(issueId: string, colKey?: string) {
    const card = document.querySelector(`article[data-card-id="${issueId}"]`);
    card?.classList.add("bounce-back");
    setTimeout(() => card?.classList.remove("bounce-back"), 320);
    if (colKey) {
      const col = document.querySelector(`section[data-col="${colKey}"]`);
      col?.classList.remove("shaking");
      if (col) void (col as HTMLElement).offsetWidth;
      col?.classList.add("shaking");
      setTimeout(() => col?.classList.remove("shaking"), 450);
    }
  }

  /** BR-14 哨兵拦截演出：列头红 outline + toast + 弹回。 */
  function denyDrop(col: GroupCol, reason: string) {
    toast(`🚫 不能拖入「${col.name}」列 —— ${reason}`, "warning");
    const el = document.querySelector(`section[data-col="${col.key}"]`);
    el?.classList.add("ring-2", "ring-red-400");
    setTimeout(() => el?.classList.remove("ring-2", "ring-red-400"), 600);
  }

  /** §2.4 跨维度拖拽写路径：始终映射该维度的正规写端点。 */
  async function dropToWrite(issue: Issue, col: GroupCol) {
    if (!workspaceSlug || !projectId) return;
    if (!canEdit) { toast("当前角色无编辑权限", "warning"); bounceBack(issue.id, col.key); return; }
    const last = col.issues[col.issues.length - 1];
    const sort = last ? last.sort_order + 65535 : 65535;
    const run = async () => {
      try {
        if (groupBy === "state_id") {
          await IssueAPI.patch(workspaceSlug, projectId, issue.id, { state_id: col.key, sort_order: sort });
          if (issue.state_group === "unstarted" || issue.state_group === "started") {
            // 拖入已取消列的恢复提示（C.29 语义沿维度保留）
            const targetGroup = vp.states.find((s) => s.id === col.key)?.group;
            if (targetGroup === "cancelled") toast("已取消，可拖回其他列恢复");
          }
        } else if (groupBy === "priority") {
          await IssueAPI.patch(workspaceSlug, projectId, issue.id, { priority: col.key, sort_order: sort });
        } else if (groupBy === "assignee_id") {
          await AssigneeAPI.put(workspaceSlug, projectId, issue.id, { assignee_ids: [col.key] });
        } else if (groupBy === "label_id") {
          await IssueAPI.setLabels(workspaceSlug, projectId, issue.id, [col.key]);
        } else {
          // cf_*：拖入 __none__ = 删该键传 null（BR-14 允许；正常写路径非拦截）
          await IssueAPI.patch(workspaceSlug, projectId, issue.id, {
            custom_fields: { [groupBy]: col.key === "__none__" ? null : col.key },
          });
          if (col.key === "__none__") toast(`已清空「${vp.cfDefs.find((d) => d.key === groupBy)?.name ?? groupBy}」（拖入未填值 = 删键）`, "ok");
        }
        await reload();
      } catch (e) {
        const err = e as ApiError;
        if (err?.code === "RESOURCE_TRANSITION_BLOCKED" && groupBy === "state_id") {
          bounceBack(issue.id, col.key);
          setBlockedDlg({ issueName: issue.name, blockers: blockersFromError(err), stateId: col.key, issueId: issue.id });
          await reload();
          return;
        }
        toast(err?.details?.[0]?.message ?? err?.message ?? "写入失败，已回滚到原位置", "error");
        bounceBack(issue.id, col.key);
        await reload();
      }
    };
    // BR-14：assignee/label 维 __none__ 可拖出不可拖入（cf_* 维允许 = 删键）
    if (col.none && !col.cfNone && (groupBy === "assignee_id" || groupBy === "label_id")) {
      denyDrop(col, `${col.name}列可拖出不可拖入（BR-14）`);
      bounceBack(issue.id, col.key);
      return;
    }
    // BR-15：多执行人/多标签替换确认
    if (groupBy === "assignee_id" && (issue.assignee_ids?.length ?? 0) > 1) {
      setReplaceAsk({ kind: "执行人", oldNames: (issue.assignee_ids ?? []).map(nameOf), newName: col.name, run });
      return;
    }
    if (groupBy === "label_id" && (issue.label_ids?.length ?? 0) > 1) {
      setReplaceAsk({ kind: "标签", oldNames: (issue.label_ids ?? []).map((id) => vp.labels.find((l) => l.id === id)?.name ?? id), newName: col.name, run });
      return;
    }
    await run();
  }

  async function reload() {
    const env = await vp.fetchGrouped(groupBy);
    if (!env) return;
    const next = cols.map((c) => {
      const bucket = env.data?.[c.key] ?? { results: [] as Issue[], total_results: 0 };
      return { ...c, issues: (bucket.results as Issue[]) ?? [], total: bucket.total_results ?? 0 };
    });
    setCols(next);
    onCardsLoaded?.(next.flatMap((c) => c.issues.map((i) => i.id)));
  }

  function cardMeta(it: Issue) {
    const meta: React.ReactNode[] = [];
    if (cardFields.labels && it.label_ids?.length) {
      meta.push(
        <span key="labels" className="inline-flex gap-1">
          {it.label_ids.slice(0, 2).map((id) => {
            const l = vp.labels.find((x) => x.id === id);
            return l ? <span key={id} className="text-[11px] px-1.5 rounded text-white h-[18px] inline-flex items-center" style={{ background: l.color }}>{l.name}</span> : null;
          })}
        </span>,
      );
    }
    if (cardFields.sub_issues && (it.sub_issues_count ?? 0) > 0) {
      meta.push(<span key="sub" className="font-mono text-neutral-500">◯ {it.completed_sub_issues_count ?? 0}/{it.sub_issues_count ?? 0}</span>);
    }
    if (cardFields.attachments && (it.attachment_count ?? 0) > 0) {
      meta.push(<span key="att" className="text-neutral-500">📎 {it.attachment_count}</span>);
    }
    if (cardFields.estimate && it.estimate_minutes) {
      meta.push(<span key="est" className="text-neutral-500">⏱ {it.estimate_minutes / 60}h</span>);
    }
    if (cardFields.priority && it.priority) {
      const p = PRIORITY_COLUMNS.find((x) => x.key === it.priority);
      if (p) meta.push(<span key="pri" className="inline-flex items-center gap-1 text-neutral-500"><span className="w-1.5 h-1.5 rounded-full" style={{ background: p.color }} />{p.name}</span>);
    }
    if (cardFields.timer && (it.spent_minutes ?? 0) > 0) {
      meta.push(<span key="timer" className="text-neutral-500 tabular-nums">⏱ {Math.round(it.spent_minutes! / 60 * 10) / 10}h{it.estimate_minutes ? `/${it.estimate_minutes / 60}h` : ""}</span>);
    }
    if (cardFields.target_date && it.target_date) {
      const od = it.target_date < todayISO() && it.state_group !== "completed" && it.state_group !== "cancelled";
      meta.push(<span key="due" className={od ? "text-red-500 font-semibold" : "text-neutral-500"}>📅 {it.target_date.slice(5)}</span>);
    }
    for (const [k, on] of Object.entries(cardFields)) {
      if (!k.startsWith("cf_") || !on) continue;
      const def = vp.cfDefs.find((d) => d.key === k);
      const v = (it.custom_fields ?? {})[k];
      if (v == null || v === false || def == null) continue;
      if (def.type === "select") {
        const opt = def.options.find((o) => o.value === v);
        meta.push(<span key={k} className="inline-flex items-center gap-1 text-neutral-500"><span className="w-2 h-2 rounded-sm" style={{ background: opt?.color ?? "#999" }} />{opt?.label ?? String(v)}</span>);
      } else if (def.type === "checkbox" && v === true) {
        meta.push(<span key={k} className="text-neutral-500">☑ {def.name}</span>);
      } else if (Array.isArray(v)) {
        meta.push(<span key={k} className="text-neutral-500">{def.name} ×{v.length}</span>);
      } else {
        meta.push(<span key={k} className="text-neutral-500">{String(v)}</span>);
      }
    }
    return meta;
  }

  const dimName = DIM_NAMES[groupBy] ?? vp.cfDefs.find((d) => d.key === groupBy)?.name ?? groupBy;
  const q = search.trim().toLowerCase();

  return (
    <div className="flex-1 min-h-0 relative">
      <style>{BOARD_CSS}</style>
      {replaceAsk && (
        <ReplaceConfirm kind={replaceAsk.kind} oldNames={replaceAsk.oldNames} newName={replaceAsk.newName}
          onCancel={() => setReplaceAsk(null)}
          onOk={() => { const r = replaceAsk; setReplaceAsk(null); void r.run(); }} />
      )}
      {blockedDlg && (
        <BlockedCompleteDialog
          issueName={blockedDlg.issueName}
          blockers={blockedDlg.blockers}
          isAdmin={canEdit}
          onClose={() => setBlockedDlg(null)}
          onForce={async (comment) => {
            try {
              await IssueAPI.patch(workspaceSlug!, projectId!, blockedDlg.issueId, { state_id: blockedDlg.stateId, force: true, comment });
              toast("已强制完成（管理员通道）· 已记录说明", "warning");
              setBlockedDlg(null);
              await reload();
            } catch (e: unknown) {
              const err = e as ApiError;
              toast(err?.details?.[0]?.message ?? err?.message ?? "强制完成失败", "error");
            }
          }}
          onJump={() => {}}
        />
      )}
      {/* 120ms 渐隐重排：视图/分组/筛选变化 → 容器 remount，卡片 viewfade 进入（§3.5） */}
      <div key={`${groupBy}|${vp.viewIdParam ?? ""}|${vp.filtersParam ?? ""}`} className="flex-1 flex gap-4 overflow-x-auto p-4 min-h-0 h-full" role="listbox" aria-label="分组看板" data-sb-scope="grouped-board">
        {loading && columnMetas.length === 0 ? (
          <div className="flex gap-4 w-full">
            {[0, 1, 2, 3].map((i) => <div key={i} className="w-[280px] shrink-0 h-40 rounded-lg bg-neutral-100 animate-pulse" />)}
          </div>
        ) : cols.map((col) => {
          const visible = q
            ? col.issues.filter((i) => i.name.toLowerCase().includes(q) || i.issue_key.toLowerCase().includes(q))
            : col.issues;
          const hiddenCount = col.total - col.issues.length;
          const visibleIssues = visible;
          /** 空列折叠胶囊（show_empty_groups=false，§3.2）。 */
          if (!showEmpty && col.total === 0) {
            return (
              <div key={col.key} data-col={col.key} data-sb-scope="bcol-collapsed"
                className="shrink-0 self-start border-[1.5px] border-dashed border-neutral-300 rounded-full px-3.5 py-2 bg-neutral-100 flex items-center gap-2 text-[13px] text-neutral-500">
                {col.avatarName ? <span className="w-5 h-5 rounded-full bg-neutral-300 text-white text-[10px] inline-flex items-center justify-center">{col.avatarName[0]}</span>
                  : col.color ? <span className="w-1.5 h-1.5 rounded-full" style={{ background: col.color }} /> : null}
                {col.name} <span className="font-mono text-xs text-neutral-400">0</span>
              </div>
            );
          }
          const stateGroup = groupBy === "state_id" ? vp.states.find((st) => st.id === col.key)?.group : undefined;
          return (
            <section key={col.key} data-col={stateGroup ?? col.key} data-key={col.key} data-sb-scope="bcol" data-none={col.none ? "1" : "0"}
              aria-label={`${dimName} ${col.name}，${col.total} 个任务`}
              className="w-[280px] shrink-0 flex flex-col bg-neutral-100 rounded-lg max-h-full"
              onDragOver={(e) => { e.preventDefault(); }}
              onDrop={async (e) => {
                e.preventDefault();
                const id = dragId ?? e.dataTransfer.getData("text/plain");
                setDragId(null);
                const it = issueById.get(id);
                if (it) await dropToWrite(it, col);
              }}>
              <div className={`h-11 flex items-center gap-2 px-3 shrink-0 ${col.none ? "rounded-t-lg" : ""}`}>
                {col.avatarName ? (
                  <span className="w-6 h-6 rounded-full bg-brand-500 text-white text-[11px] font-semibold inline-flex items-center justify-center" aria-hidden="true">{col.avatarName[0]}</span>
                ) : col.color ? (
                  <span className="w-2 h-2 rounded-full shrink-0" style={{ background: col.color }} />
                ) : null}
                <span className="text-[13px] font-medium inline-flex items-center gap-1.5">
                  {col.none ? <span className="border-b-2 border-dashed border-neutral-400 pb-px">{col.name}</span> : col.name}
                  {col.none && <span className="text-[11px] text-neutral-400 font-normal">{col.cfNone ? "可拖入=删值" : "可拖出 · 不可拖入"}</span>}
                </span>
                <span className="ml-auto font-mono text-xs text-neutral-400 bg-neutral-200 rounded-full px-2 tabular-nums" data-count={col.key}>{col.total}</span>
              </div>
              {groupBy === "state_id" && stateGroup === "started" && (() => {
                const blockedN = visibleIssues.filter((i) => blockedIds.has(i.id)).length;
                return blockedN > 0 ? (
                  <div className="px-3 pb-1.5 text-[11px] text-amber-700 shrink-0" data-sb-scope="board-blocked-sub">
                    {visibleIssues.length} 个任务（{blockedN} 被阻塞）
                  </div>
                ) : null;
              })()}
              <div className="flex-1 overflow-y-auto p-2 pt-0 flex flex-col gap-2 min-h-[56px]">
                {quickCol === col.key && (
                  <input autoFocus id={`qi-${groupBy === "state_id" ? (vp.states.find((st) => st.id === col.key)?.group ?? col.key) : col.key}`} aria-label="任务标题" placeholder="输入任务标题…"
                    onKeyDown={async (e) => {
                      if (e.key === "Enter") {
                        const v = (e.target as HTMLInputElement).value.trim();
                        if (!v) return;
                        setQuickCol(null);
                        try {
                          await IssueAPI.create(workspaceSlug!, projectId!, { name: v, ...(groupBy === "state_id" ? { state_id: col.key } : {}) });
                          await reload();
                        } catch (err) {
                          toast(err instanceof Error ? err.message : "创建失败", "error");
                        }
                      }
                      if (e.key === "Escape") setQuickCol(null);
                    }}
                    onBlur={(e) => { if (!e.target.value.trim()) setQuickCol(null); }}
                    className="h-[34px] border border-brand-500 rounded-md px-2.5 text-[13px] focus:outline-none focus:ring-[3px] focus:ring-brand-50 bg-white" />
                )}
                {visible.length ? visible.map((it) => {
                  const blocked = blockedIds.has(it.id);
                  const tip = blockedTipOf(it.id);
                  const state = vp.states.find((s) => s.id === it.state_id);
                  const assignees = (it.assignee_ids ?? []).map(nameOf);
                  // C.77 看板多选态（BOARD-004 §3.1）：ring-2 + 左上角标复选框 + aria-selected
                  const selected = bulk ? bulk.isSelected(it.id) : false;
                  return (
                    <article key={it.id} data-card-id={it.id} data-sb-scope="board-card" draggable tabIndex={0}
                      data-sel-id={bulk ? it.id : undefined}
                      role="option" aria-selected={selected} aria-label={`${it.name}，${state?.name ?? ""}`}
                      className={`bcard bcard-vf group/card relative bg-white border rounded-md p-2.5 pl-3.5 shadow-sm cursor-grab hover:shadow-md hover:border-neutral-300 transition ${selected ? "border-brand-500 ring-2 ring-brand-100 shadow-[0_0_0_2px_#3f76ff_inset]" : "border-neutral-200"} ${dragId === it.id ? "opacity-40 scale-[0.98]" : ""} ${it.state_group === "cancelled" ? "opacity-60" : ""}`}
                      style={{ ["--bar" as string]: state?.color ?? "#9ca3af" }}
                      onClick={(e) => {
                        if (bulk && bulk.onModifierClick(it.id, e)) return; // ⌘/Shift = 多选（C.77）
                        onOpenIssue(it.id);
                      }}
                      {...bind(it as unknown as PeekIssue)}
                      onMouseEnter={(e) => { if (blocked) onLoadBlockedTip(it.id); bind(it as unknown as PeekIssue).onMouseEnter?.(e); }}
                      onDragStart={(e) => { setDragId(it.id); e.dataTransfer.setData("text/plain", it.id); e.dataTransfer.effectAllowed = "move"; }}
                      onDragEnd={() => { setDragId(null); flushLiveQueue(); }}>
                      <span className="absolute left-0 top-2 bottom-2 w-[3px] rounded" style={{ background: state?.color ?? "#9ca3af" }} aria-hidden="true" />
                      {bulk && (
                        <input type="checkbox" data-sb-scope="board-card-cb" aria-label={`选择 ${it.name}`} checked={selected}
                          onChange={() => bulk.onCheck(it.id)}
                          onClick={(e) => e.stopPropagation()}
                          className={`absolute top-1.5 left-1.5 w-[18px] h-[18px] accent-brand-500 z-[2] cursor-pointer transition-opacity ${selected || bulk.anySelected ? "opacity-100" : "opacity-0 group-hover/card:opacity-100"}`} />
                      )}
                      {blocked && <span className="absolute top-1.5 right-1.5 text-amber-500 text-[13px]" role="img" title={tip} aria-label={`被未完成前置任务阻塞：${tip}`} data-sb-scope="board-blocked-badge">⛔</span>}
                      <div className="text-[13px] text-neutral-900 flex gap-1.5 items-start pl-1 pr-4">
                        <span className="font-mono text-[11px] px-1.5 py-px bg-neutral-100 rounded text-neutral-500 shrink-0">{it.issue_key}</span>
                        <span className="line-clamp-3">{it.name}</span>
                      </div>
                      {cardMeta(it).length > 0 && (
                        <div className="flex items-center gap-2 mt-2 text-[12px] text-neutral-400 flex-wrap pl-1">{cardMeta(it)}</div>
                      )}
                      <span className="absolute right-2 bottom-2">
                        {assignees.length > 0
                          ? <AvatarStack names={assignees} size={20} />
                          : <span className="inline-flex text-neutral-300 border border-dashed border-neutral-300 rounded-full w-5 h-5 items-center justify-center" title="未指派" aria-label="未指派" data-sb-scope="board-unassigned">
                              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
                            </span>}
                      </span>
                    </article>
                  );
                }) : (
                  <div className="border-2 border-dashed border-neutral-300 rounded-md py-4 text-center text-xs text-neutral-400" data-sb-scope="bcol-empty">
                    {col.total === 0 ? "空分组（恒在展示 · 债务可见）· 将任务拖拽到这里" : "无匹配卡片"}
                  </div>
                )}
                {hiddenCount > 0 && (
                  <div className="text-center text-[11px] text-neutral-400 py-1">…另有 {hiddenCount} 项未载入（组内 100 上限）</div>
                )}
                <button type="button" onClick={() => setQuickCol(col.key)} disabled={!canEdit}
                  className="h-8 flex items-center gap-1.5 px-2.5 rounded-md text-[13px] text-neutral-400 hover:bg-neutral-200/60 disabled:opacity-40 w-full shrink-0">＋ 添加任务</button>
              </div>
            </section>
          );
        })}
        {cols.length === 0 && !loading && (
          <div className="flex-1 flex flex-col items-center justify-center gap-2 text-neutral-500 py-16">
            <div className="text-[15px] font-semibold text-neutral-700">无匹配卡片</div>
            <div className="text-[13px]">当前视图筛选下没有任务，尝试调整或清空筛选</div>
          </div>
        )}
      </div>
    </div>
  );
}

export type { IssueViewData, FilterLogicNode };
