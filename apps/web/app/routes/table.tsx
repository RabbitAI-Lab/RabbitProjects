import { useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { IssueDrawer as SharedDrawer } from "../components/IssueDrawer";
import { NewTaskModal } from "../components/NewTaskModal";
import { StateBadge } from "../components/StateBadge";
import { AvatarStack } from "../components/issue-dialogs";
import { ProjectAPI } from "../services/api";
import type { Issue } from "@rp/types";
import { ViewSwitchBar } from "../components/views/ViewSwitchBar";
import { FilterOpenButton, ViewChipsRow } from "../components/views/ViewFilterBar";
import { useViewPage } from "../components/views/useViewPage";
import { PRIORITY_COLUMNS, TABLE_COL_NAMES } from "../components/views/view-dsl";

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

  useEffect(() => {
    if (!workspaceSlug || !projectId) return;
    setLoading(true);
    const t = setTimeout(() => {
      void vp.fetchFlat({ per_page: 100 }).then((r) => {
        setIssues((r?.data as Issue[]) ?? []);
        setLoading(false);
      });
    }, 0);
    return () => clearTimeout(t);
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

  function cell(it: Issue, col: string) {
    switch (col) {
      case "key":
        return <span className="font-mono text-xs text-neutral-500 whitespace-nowrap">{it.issue_key}</span>;
      case "title":
        return <span className="text-[13px] text-neutral-900 truncate">{it.name}</span>;
      case "state":
        return <StateBadge group={it.state_group ?? "unstarted"} name={it.state_name ?? "—"} />;
      case "assignees":
        return it.assignee_ids?.length
          ? <AvatarStack names={it.assignee_ids.map(nameOf)} size={20} />
          : <span className="inline-flex items-center rounded-full bg-neutral-100 px-2 py-0.5 text-[12px] text-neutral-500">未指派</span>;
      case "due": {
        if (!it.target_date) return <span className="text-neutral-400">—</span>;
        const od = it.target_date < today && it.state_group !== "completed" && it.state_group !== "cancelled";
        return <span className={`font-mono text-xs tabular-nums ${od ? "text-red-500 font-semibold" : "text-neutral-500"}`}>{it.target_date.slice(5)}</span>;
      }
      case "priority": {
        const p = PRIORITY_COLUMNS.find((x) => x.key === it.priority);
        return p ? (
          <span className="inline-flex items-center gap-1.5 text-[13px] text-neutral-600 whitespace-nowrap">
            <span className="w-2 h-2 rounded-full" style={{ background: p.color }} />{p.name}
          </span>
        ) : <span className="text-neutral-400">—</span>;
      }
      case "labels":
        return it.label_ids?.length ? (
          <span className="inline-flex gap-1">
            {it.label_ids.map((id) => {
              const l = vp.labels.find((x) => x.id === id);
              return l ? <span key={id} className="text-[11px] px-1.5 rounded text-white h-[18px] inline-flex items-center" style={{ background: l.color }}>{l.name}</span> : null;
            })}
          </span>
        ) : <span className="text-neutral-400">—</span>;
      default:
        return <span className="text-neutral-400">—</span>;
    }
  }

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
          <div className="flex-1 overflow-y-auto px-5 pb-10" data-sb-scope="table-wrap">
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
                    {visibleCols.map((c) => (
                      <th key={c} data-col={c}
                        className={`text-left px-2.5 py-2 border-b border-neutral-200 text-[11px] font-semibold text-neutral-400 uppercase tracking-wider ${c === "title" ? "min-w-[260px]" : "whitespace-nowrap"}`}>
                        {TABLE_COL_NAMES[c] ?? c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="[&>tr:nth-child(even)>td]:bg-[#fbfbfb] [&>tr:hover>td]:bg-neutral-50">
                  {issues.map((it) => (
                    <tr key={it.id} data-sb-scope="table-row" data-id={it.id} tabIndex={0} role="option" aria-selected="false"
                      onClick={() => openPeek(it.id)}
                      onKeyDown={(e) => { if (e.key === "Enter") openPeek(it.id); }}
                      className="cursor-pointer">
                      {visibleCols.map((c) => (
                        <td key={c} className={`px-2.5 py-1.5 border-b border-neutral-100 text-[13px] ${c === "title" ? "min-w-[260px]" : "whitespace-nowrap"}`}>
                          {cell(it, c)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          {peekId && <SharedDrawer issueId={peekId} slug={workspaceSlug!} projectId={projectId!} onClose={closePeek} onChanged={() => {}} />}
          {showTaskModal && <NewTaskModal slug={workspaceSlug!} projectId={projectId!} projectName={projName} onClose={() => setShowTaskModal(false)} onCreated={() => {}} />}
        </main>
      </div>
    </div>
  );
}
