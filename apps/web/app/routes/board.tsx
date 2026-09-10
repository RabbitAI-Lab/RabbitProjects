import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { IssueDrawer as SharedDrawer } from "../components/IssueDrawer";
import { PeekPopover, usePeekHover } from "../components/PeekPopover";
import { NewTaskModal } from "../components/NewTaskModal";
import { IssueAPI, ProjectAPI, RelationAPI, unwrap, type RelationRow } from "../services/api";
import { LabelsAdminModal } from "./labels-admin";
import { useStores } from "../stores";
import { ViewSwitchBar } from "../components/views/ViewSwitchBar";
import { FilterOpenButton, ViewChipsRow } from "../components/views/ViewFilterBar";
import { GroupedBoard } from "../components/views/GroupedBoard";
import { SwimlaneMatrix } from "../components/views/SwimlaneMatrix";
import { useViewPage } from "../components/views/useViewPage";
import { BulkOperations, TruncationStrip, useBulkSelection, useMarquee } from "../components/views/BulkOperations";
import { usePermissionSync } from "../components/PermissionGate";

/** Sprint-3 Phase 3-A：BOARD-003 §3.1/§3.2 分组看板泛化 + 视图切换器工具条（C.64~C.67）。
 *  Sprint-1/2 冻结表面（C.29/C.30/C.44/C.45/C.51）在泛化看板上保留：
 *  hover Peek、⛔ 角标与 tooltip、列内快速创建、完成被拦 M-BLOCKED、标签管理入口。 */
export default function Board() {
  const { workspaceSlug, projectId } = useParams<{ workspaceSlug: string; projectId: string }>();
  usePermissionSync(); // 权限快照晚到重渲染（fail-closed 竞态，见 PermissionGate.tsx）
  const vp = useViewPage({ workspaceSlug, projectId, layout: "kanban" });
  // BOARD-005（C.154）：?sub_group_by= 二维泳道（行分组维度；清空回一维）
  const [spParams] = useSearchParams();
  const subGroupBy = spParams.get("sub_group_by") ?? "";
  const { peek, close: closeHoverPeek } = usePeekHover();
  const [showTaskModal, setShowTaskModal] = useState(false);
  const [projName, setProjName] = useState("…");
  const [projIdentifier, setProjIdentifier] = useState("");
  const [search, setSearch] = useState("");
  const [showLabelsAdmin, setShowLabelsAdmin] = useState(false);
  const stores = useStores();
  // 快照未 hydrate（刷新竞态）时 fail-open：写边界在后端（403 拦截），
  // 避免权限快照晚到把 CONTRIBUTOR+ 误判为只读（S3V-3 实测回归）。
  const myRole = stores.permission.effectiveProjectRole(projectId, workspaceSlug);
  const canEdit = stores.permission.snapshot === null || myRole >= 15;

  /** C.45 被阻塞集合（?blocked=true 服务端口径；⛔ 角标与 tooltip 共用）。 */
  const [blockedIds, setBlockedIds] = useState<Set<string>>(new Set());
  const [blockedTip, setBlockedTip] = useState<Record<string, string>>({});
  /** 外部变更信号（创建任务/抽屉编辑 → 分组看板重拉，旧 board.load() 的接线位） */
  const [reloadKey, setReloadKey] = useState(0);
  const bumpReload = () => setReloadKey((n) => n + 1);
  /** 视图/临时筛选命中总数（meta.total_count；C.74 总计行）。 */
  const [totalCount, setTotalCount] = useState<number | null>(null);

  // ── Sprint-3 Phase 3-B（BOARD-004 §3.1/§4.4）：看板多选 + 批量工具条（C.77~C.83）──
  const bulkSel = useBulkSelection({ projectId, canEdit });
  /** C.79 ⌘A 作用域：已载入卡片 id（GroupedBoard onCardsLoaded 上报）。 */
  const [boardIds, setBoardIds] = useState<string[]>([]);
  const boardIdsRef = useRef<string[]>([]);
  boardIdsRef.current = boardIds;
  const boardWrapRef = useRef<HTMLDivElement | null>(null);
  useMarquee(boardWrapRef, {
    enabled: canEdit,
    onHit: (ids, additive) => {
      if (!additive) bulkSel.sel.clear();
      bulkSel.sel.addMany(ids);
    },
  });
  const onCardsLoaded = useCallback((ids: string[]) => setBoardIds(ids), []);
  const bumpReloadForBulk = () => setReloadKey((n) => n + 1);

  useEffect(() => {
    if (!workspaceSlug || !projectId) return;
    ProjectAPI.detail(workspaceSlug, projectId)
      .then((r) => {
        const d = (r as unknown as { data: { name?: string; identifier?: string } }).data;
        setProjName(d?.name ?? "…");
        setProjIdentifier(d?.identifier ?? "");
      })
      .catch(() => {});
    IssueAPI.list(workspaceSlug, projectId, { blocked: true, per_page: 100 })
      .then((r) => setBlockedIds(new Set((((r as unknown as { data: { id: string }[] }).data) ?? []).map((i) => i.id))))
      .catch(() => {});
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceSlug, projectId]);

  // 命中总数（仅视图/筛选激活时查询；「全部」裸态不查）
  useEffect(() => {
    if (!workspaceSlug || !projectId) return;
    if (!vp.viewIdParam && !vp.filtersParam) { setTotalCount(null); return; }
    let cancel = false;
    const t = setTimeout(() => {
      void vp.fetchFlat({ per_page: 1 }).then((r) => {
        if (!cancel && r) setTotalCount(r.meta?.total_count ?? null);
      });
    }, 50);
    return () => { cancel = true; clearTimeout(t); };
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [vp.viewIdParam, vp.filtersParam, workspaceSlug, projectId]);

  // ?peekIssue URL 同步（TASK-001 §3.3）
  const [sp, setSp] = useSearchParams();
  const peekId = sp.get("peekIssue");
  function openPeek(id: string) { setSp((prev) => { const n = new URLSearchParams(prev); n.set("peekIssue", id); return n; }, { preventScrollReset: true }); }
  function closePeek() { setSp((prev) => { const n = new URLSearchParams(prev); n.delete("peekIssue"); return n; }, { preventScrollReset: true }); }

  /** ⛔ 角标 tooltip：hover 按需拉 relations（列阻塞项前 3 +「等 N 项」，C.45）。 */
  async function loadBlockedTip(issueId: string) {
    if (blockedTip[issueId] || !workspaceSlug || !projectId) return;
    try {
      const r = await RelationAPI.list(workspaceSlug, projectId, issueId);
      const rows = (unwrap<RelationRow[]>(r) ?? [])
        .filter((x) => x.relation_type === "is_blocked_by"
          && x.related_issue.state_group !== "completed" && x.related_issue.state_group !== "cancelled");
      const keys = rows.map((x) => x.related_issue.issue_key);
      setBlockedTip((cur) => ({ ...cur, [issueId]: keys.length > 3 ? `${keys.slice(0, 3).join("、")} 等 ${keys.length} 项` : keys.join("、") || "被未完成前置任务阻塞" }));
    } catch { /* tooltip 降级为通用文案 */ }
  }

  return (
    <div className="flex flex-col h-screen">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <ProjectSidebar projectName={projName} identifier={projIdentifier} />
        <main className="flex-1 min-w-0 flex flex-col">
          {/* 视图条（C.29 视图名 + 创建任务按钮） */}
          <div className="h-[56px] border-b border-neutral-200 flex items-center gap-2 px-5 bg-white shrink-0">
            <span className="text-[15px] font-semibold">看板</span>
            <span className="text-[12px] text-neutral-500 ml-1">{projIdentifier}</span>
            <button onClick={() => setShowTaskModal(true)} className="ml-auto inline-flex h-[34px] items-center gap-1.5 px-3.5 bg-brand-500 text-white rounded-md font-medium">+ 创建任务</button>
          </div>
          {/* 视图切换器工具条（C.64/C.65：四段器/Tabs/右键/黄条/⚙显示/分组切换器） */}
          <ViewSwitchBar vp={vp} />
          {/* 筛选工具条（C.29 基线 + O2 ⊞ 筛选入口） */}
          <div className="h-[52px] border-b border-neutral-200 sticky top-0 z-20 bg-white/95 backdrop-blur flex items-center gap-3 px-5 shrink-0" data-sb-scope="board-filterbar">
            <div className="flex items-center gap-1.5 h-8 border border-neutral-300 rounded-md px-2.5 bg-white focus-within:border-brand-500 w-[240px]">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-neutral-400"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
              <input aria-label="搜索任务" placeholder="搜索任务…" value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="flex-1 h-7 outline-none text-[13px] bg-transparent" />
            </div>
            <FilterOpenButton vp={vp} />
            <button onClick={() => setShowLabelsAdmin(true)} data-sb-scope="board-open-labels"
              className="h-8 px-2.5 inline-flex items-center gap-1.5 border border-neutral-300 rounded-md text-[13px] hover:bg-neutral-50">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M20.59 13.41 13.41 20.59a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><path d="M7 7h.01"/></svg>
              管理标签
            </button>
            <div className="ml-auto text-[12px] text-neutral-400">
              {totalCount != null ? `${totalCount} 个任务 · 视图 ${vp.currentView?.name ?? "全部"}` : "分组列 280px"}
            </div>
          </div>
          {/* 视图条件 chips 行（C.74） */}
          <ViewChipsRow vp={vp} totalCount={totalCount} />
          {/* ⌘A 截断黄条（C.79：已选前 100 / 共 N） */}
          <div className="px-4 pt-2"><TruncationStrip /></div>
          {/* 分组看板（五维泛化 C.66/C.67）+ 多选接线（C.77） */}
          <div ref={boardWrapRef} className="flex-1 flex flex-col min-h-0">
            {subGroupBy ? (
              <SwimlaneMatrix vp={vp} workspaceSlug={workspaceSlug} projectId={projectId}
                subGroupBy={subGroupBy} reloadKey={reloadKey} onCount={setTotalCount} />
            ) : (
              <GroupedBoard vp={vp} workspaceSlug={workspaceSlug} projectId={projectId} canEdit={canEdit} blockedIds={blockedIds} blockedTipOf={(id) => blockedTip[id] ?? "被未完成前置任务阻塞"} onLoadBlockedTip={(id) => void loadBlockedTip(id)}
                onOpenIssue={openPeek} search={search} reloadKey={reloadKey} onCardsLoaded={onCardsLoaded}
                bulk={{
                  isSelected: bulkSel.isSelected,
                  anySelected: bulkSel.anySelected,
                  onCheck: bulkSel.onCheckboxClick,
                  // ⌘/Shift 作用域 = 当前看板可见卡片（列内 100 上限内的已载入集）
                  onModifierClick: (id, ev) => bulkSel.onModifierClick(id, ev, boardIdsRef.current),
                }} />
            )}
          </div>
          {/* 批量工具条 + 浮层/确认/失败定位（C.80~C.83） */}
          <BulkOperations workspaceSlug={workspaceSlug} projectId={projectId} canEdit={canEdit}
            visibleIds={boardIds} states={vp.states} members={vp.members} labels={vp.labels}
            onOpenIssue={openPeek} onMutated={bumpReloadForBulk} />
          <PeekPopover peek={peek} onClose={closeHoverPeek} />
          {peekId && <SharedDrawer issueId={peekId} slug={workspaceSlug!} projectId={projectId!} onClose={() => { closePeek(); bumpReload(); }} onChanged={bumpReload} />}
          {showTaskModal && <NewTaskModal slug={workspaceSlug!} projectId={projectId!} projectName={projName} onClose={() => setShowTaskModal(false)} onCreated={bumpReload} />}
          {showLabelsAdmin && workspaceSlug && projectId && (
            <LabelsAdminModal workspaceSlug={workspaceSlug} projectId={projectId} onClose={() => setShowLabelsAdmin(false)} />
          )}
        </main>
      </div>
    </div>
  );
}
