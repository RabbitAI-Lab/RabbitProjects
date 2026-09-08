import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { IssueDrawer as SharedDrawer } from "../components/IssueDrawer";
import { NewTaskModal } from "../components/NewTaskModal";
import { ProjectAPI } from "../services/api";
import type { Issue } from "@rp/types";
import { ViewSwitchBar } from "../components/views/ViewSwitchBar";
import { FilterOpenButton, ViewChipsRow } from "../components/views/ViewFilterBar";
import { useViewPage } from "../components/views/useViewPage";
import { TABLE_COL_NAMES } from "../components/views/view-dsl";
import { renderTableCell } from "../components/views/TableCell";
import { BulkOperations, TruncationStrip, useBulkSelection, useMarquee } from "../components/views/BulkOperations";
import { useStores } from "../stores";

/** BOARD-003 §1.2/§3.3 + 原型 O3：表格布局（C.75）——紧凑行高 + 斑马纹 + 列配置全生效。
 *  与列表布局同构（同一 issues 数据源，view_id/filters 三源恒 AND）；列集合 =
 *  display_props.columns（「-」前缀 = 隐藏，C.68 列配置区控制）。 */
export default function Table() {
  const { workspaceSlug, projectId } = useParams<{ workspaceSlug: string; projectId: string }>();
  const vp = useViewPage({ workspaceSlug, projectId, layout: "table" });
  const [issues, setIssues] = useState<Issue[]>([]);
  const [loading, setLoading] = useState(true);
  const [projName, setProjName] = useState("…");
  const [projIdentifier, setProjIdentifier] = useState("");
  const [showTaskModal, setShowTaskModal] = useState(false);

  // ── Sprint-3 Phase 3-B（BOARD-004 §3.1）：表格行多选 + 批量工具条（C.78~C.83）──
  const stores = useStores();
  const myRole = stores.permission.effectiveProjectRole(projectId, workspaceSlug);
  // 快照未 hydrate 时 fail-open（写边界在后端；同 board.tsx S3V-3 回归口径）
  const canEdit = stores.permission.snapshot === null || myRole >= 15;
  const bulkSel = useBulkSelection({ projectId, canEdit });
  const tableWrapRef = useRef<HTMLDivElement | null>(null);
  useMarquee(tableWrapRef, {
    enabled: canEdit,
    onHit: (ids, additive) => {
      if (!additive) bulkSel.sel.clear();
      bulkSel.sel.addMany(ids);
    },
  });

  useEffect(() => {
    if (!workspaceSlug || !projectId) return;
    ProjectAPI.detail(workspaceSlug, projectId)
      .then((r) => {
        const d = (r as unknown as { data: { name?: string; identifier?: string } }).data;
        setProjName(d?.name ?? "…");
        setProjIdentifier(d?.identifier ?? "");
      })
      .catch(() => {});
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceSlug, projectId]);

  /** 表格数据拉取（视图/临时筛选变化与批量动作成功后共用）。 */
  const reload = () => {
    setLoading(true);
    const t = setTimeout(() => {
      void vp.fetchFlat({ per_page: 100 }).then((r) => {
        setIssues((r?.data as Issue[]) ?? []);
        setLoading(false);
      });
    }, 0);
    return () => clearTimeout(t);
  };
  useEffect(() => {
    if (!workspaceSlug || !projectId) return;
    return reload();
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [vp.viewIdParam, vp.filtersParam, workspaceSlug, projectId]);

  // ?peekIssue URL 同步（四布局共用，BOARD-003 §1.2）
  const [sp, setSp] = useSearchParams();
  const peekId = sp.get("peekIssue");
  function openPeek(id: string) { setSp((prev) => { const n = new URLSearchParams(prev); n.set("peekIssue", id); return n; }, { preventScrollReset: true }); }
  function closePeek() { setSp((prev) => { const n = new URLSearchParams(prev); n.delete("peekIssue"); return n; }, { preventScrollReset: true }); }

  /** 列配置：display_props.columns 的可见列（按序，「-」前缀隐藏）。 */
  const visibleCols = useMemo(() => {
    const cols = vp.effDisplay.columns ?? Object.keys(TABLE_COL_NAMES);
    return cols.filter((c) => !c.startsWith("-"));
  }, [vp.effDisplay.columns]);

  const nameOf = (uid: string) => vp.members.find((m) => m.id === uid)?.name ?? "…";
  const today = new Date().toISOString().slice(0, 10);

  /** 共用渲染：表格/列表同构（ADR-0030：列表补齐 display_props.columns 消费）。 */
  const renderCell = (it: Issue, col: string) => renderTableCell(it, col, { nameOf, today, labels: vp.labels });

  return (
    <div className="flex flex-col h-screen">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <ProjectSidebar projectName={projName} identifier={projIdentifier} />
        <main className="flex-1 min-w-0 flex flex-col overflow-hidden">
          <div className="h-[56px] border-b border-neutral-200 flex items-center gap-2 px-5 bg-white shrink-0">
            <span className="text-[15px] font-semibold">表格</span>
            <span className="text-[13px] text-neutral-500 ml-1">{issues.length} 个任务</span>
            <button onClick={() => setShowTaskModal(true)} className="ml-auto inline-flex h-[34px] items-center gap-1.5 px-3.5 bg-brand-500 text-white rounded-md font-medium">+ 创建任务</button>
          </div>
          <ViewSwitchBar vp={vp} />
          <div className="h-[52px] border-b border-neutral-200 bg-white flex items-center gap-3 px-5 shrink-0" data-sb-scope="table-filterbar">
            <FilterOpenButton vp={vp} />
            <div className="ml-auto text-[12px] text-neutral-400">紧凑斑马纹 · 列配置在 ⚙ 显示</div>
          </div>
          <ViewChipsRow vp={vp} totalCount={issues.length} />
          <div className="flex-1 overflow-y-auto px-5 pb-24" data-sb-scope="table-wrap" ref={tableWrapRef}>
            {/* ⌘A 截断黄条（C.79） */}
            <TruncationStrip />
            {loading ? (
              <div className="py-8 space-y-2">
                {[0, 1, 2, 3, 4].map((i) => <div key={i} className="h-9 rounded bg-neutral-100 animate-pulse" />)}
              </div>
            ) : issues.length === 0 ? (
              <div className="flex flex-col items-center gap-2 py-16 text-neutral-500">
                <div className="text-[15px] font-semibold text-neutral-700">无匹配卡片</div>
                <div className="text-[13px]">当前视图筛选下没有任务，尝试调整或清空筛选</div>
              </div>
            ) : (
              <table className="w-full border-collapse" role="listbox" aria-label="任务表格" data-sb-scope="table-el">
                <thead>
                  <tr>
                    {/* C.78 表格行首复选列（BOARD-004 §3.1） */}
                    <th className="w-[34px] pr-0" aria-hidden="true" />
                    {visibleCols.map((c) => (
                      <th key={c} data-col={c}
                        className={`text-left px-2.5 py-2 border-b border-neutral-200 text-[11px] font-semibold text-neutral-400 uppercase tracking-wider ${c === "title" ? "min-w-[260px]" : "whitespace-nowrap"}`}>
                        {TABLE_COL_NAMES[c] ?? c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {issues.map((it) => {
                    const selected = bulkSel.isSelected(it.id);
                    return (
                      <tr key={it.id} data-sb-scope="table-row" data-id={it.id} data-sel-id={it.id} tabIndex={0} role="option" aria-selected={selected}
                        onClick={(e) => {
                          if (bulkSel.onModifierClick(it.id, e, issues.map((i) => i.id))) return;
                          openPeek(it.id);
                        }}
                        onKeyDown={(e) => { if (e.key === "Enter") openPeek(it.id); }}
                        className={`group cursor-pointer ${selected ? "[&>td]:bg-brand-50" : "[&>tr:nth-child(even)>td]:bg-[#fbfbfb] [&>tr:hover>td]:bg-neutral-50 odd:[&>td]:bg-[#fbfbfb] hover:[&>td]:bg-neutral-50"}`}>
                        <td className="border-b border-neutral-100 w-[34px] pr-0">
                          <input type="checkbox" data-sb-scope="table-row-cb" aria-label={`选择 ${it.name}`} checked={selected}
                            onChange={() => bulkSel.onCheckboxClick(it.id)}
                            onClick={(e) => e.stopPropagation()}
                            className={`w-[15px] h-[15px] accent-brand-500 cursor-pointer transition-opacity ${selected || bulkSel.anySelected ? "opacity-100" : "opacity-0 group-hover:opacity-100"}`} />
                        </td>
                        {visibleCols.map((c) => (
                          <td key={c} className={`px-2.5 py-1.5 border-b border-neutral-100 text-[13px] ${c === "title" ? "min-w-[260px]" : "whitespace-nowrap"}`}>
                            {renderCell(it, c)}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
          {/* 批量工具条 + 浮层/确认/失败定位（C.80~C.83） */}
          <BulkOperations workspaceSlug={workspaceSlug} projectId={projectId} canEdit={canEdit}
            visibleIds={issues.map((i) => i.id)} states={vp.states} members={vp.members} labels={vp.labels}
            onOpenIssue={openPeek} onMutated={reload} />
          {peekId && <SharedDrawer issueId={peekId} slug={workspaceSlug!} projectId={projectId!} onClose={closePeek} onChanged={() => {}} />}
          {showTaskModal && <NewTaskModal slug={workspaceSlug!} projectId={projectId!} projectName={projName} onClose={() => setShowTaskModal(false)} onCreated={() => {}} />}
        </main>
      </div>
    </div>
  );
}
