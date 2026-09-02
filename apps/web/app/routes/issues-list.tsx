import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { IssueDrawer } from "../components/IssueDrawer";
import { NewTaskModal } from "../components/NewTaskModal";
import { StateBadge } from "../components/StateBadge";
import { IssueAPI, ProjectAPI } from "../services/api";
import { toast } from "../components/Toast";
import type { Issue } from "@rp/types";

const today = () => new Date().toISOString().slice(0, 10);

export default function IssuesList() {
  const { workspaceSlug, projectId } = useParams<{ workspaceSlug: string; projectId: string }>();
  const [issues, setIssues] = useState<Issue[]>([]);
  const [project, setProject] = useState<{ name: string; identifier: string } | null>(null);
  const [quick, setQuick] = useState("");
  const [creating, setCreating] = useState(false);
  const [openIssueId, setOpenIssueId] = useState<string | null>(null);
  const [showTaskModal, setShowTaskModal] = useState(false);

  async function load() {
    try {
      const [iRes, pRes] = await Promise.all([
        IssueAPI.list(workspaceSlug!, projectId!, { ordering: "sort_order" }),
        ProjectAPI.detail(workspaceSlug!, projectId!),
      ]);
      setIssues((iRes as any).data);
      setProject((pRes as any).data);
    } catch { /* 鉴权失败由 axios 拦截器统一跳转；此处吃掉 unhandled rejection */ }
  }
  useEffect(() => { load(); }, [workspaceSlug, projectId]);
  const [sp, setSp] = useSearchParams();
  const peekId = sp.get("peekIssue");
  function openPeek(id: string) { setSp((prev) => { const n = new URLSearchParams(prev); n.set("peekIssue", id); return n; }, { preventScrollReset: true }); }
  function closePeek() { setSp((prev) => { const n = new URLSearchParams(prev); n.delete("peekIssue"); return n; }, { preventScrollReset: true }); }

  async function quickCreate() {
    if (!quick.trim() || creating) return;
    const text = quick;
    setCreating(true); setQuick("");
    try {
      await IssueAPI.create(workspaceSlug!, projectId!, { name: text });
      await load();
    } catch (e: any) {
      setQuick(text); // 失败恢复输入（TASK-001 §3.2.1）
      toast(e?.message ?? "创建失败", "error");
    } finally {
      setCreating(false);
      document.getElementById("tq-input")?.focus();
    }
  }

  return (
    <div className="flex flex-col h-screen">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <ProjectSidebar projectName={project?.name ?? "…"} identifier={project?.identifier ?? ""} />
        <main className="flex-1 min-w-0 flex flex-col overflow-hidden">
          <div className="h-[53px] border-b border-neutral-200 flex items-center gap-2 px-5 bg-white shrink-0">
            <div><span className="text-[15px] font-semibold">任务列表</span>
              <span className="text-[13px] text-neutral-500 ml-2">{issues.length} 个任务</span></div>
            <button onClick={() => setShowTaskModal(true)} className="ml-auto inline-flex h-[34px] items-center gap-1.5 px-3.5 bg-brand-500 text-white rounded-md font-medium hover:bg-brand-600">+ 创建任务</button>
          </div>
          <div className="flex-1 overflow-y-auto p-5">
            <div className="flex items-center gap-1.5 border border-dashed border-neutral-300 h-[38px] px-3 rounded-md mb-3.5 text-neutral-500 focus-within:border-brand-500 focus-within:bg-white">
              <span>+</span>
              <input id="tq-input" className="flex-1 bg-transparent outline-none text-[13px]" placeholder="输入任务标题后按回车快速创建…"
                value={quick} onChange={(e) => setQuick(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); quickCreate(); } if (e.key === "Escape") { setQuick(""); (e.target as HTMLInputElement).blur(); } }} />
              <span className="text-xs">Enter 创建</span>
            </div>
            {issues.length === 0 ? (
              <div className="flex flex-col items-center gap-2 py-12 text-neutral-500">
                <svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="#d4d4d4" strokeWidth="2"><rect width="8" height="4" x="8" y="2" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4M12 16h4M8 11h.01M8 16h.01"/></svg>
                <div className="text-[15px] font-semibold text-neutral-700">暂无任务</div>
                <div className="text-[13px]">创建第一个任务开始工作</div>
                <button onClick={() => setShowTaskModal(true)} className="mt-2 inline-flex h-[34px] items-center gap-1.5 px-3.5 bg-brand-500 text-white rounded-md font-medium">+ 创建任务</button>
              </div>
            ) : (
              <table className="w-full border-collapse">
                <thead><tr>
                  {["编号", "标题", "状态", "负责人", "截止时间"].map((h, i) => (
                    <th key={h} className={`text-left px-3 py-2 border-b border-neutral-200 text-[11px] font-semibold text-neutral-400 uppercase tracking-wider ${["w-24", "", "w-[120px]", "w-[110px]", "w-[130px]"][i]}`}>{h}</th>
                  ))}
                </tr></thead>
                <tbody>
                  {issues.map((it) => {
                    const overdue = it.target_date && it.target_date < today() && it.state_group !== "completed";
                    return (
                      <tr key={it.id} className="group cursor-pointer hover:bg-neutral-50" onClick={() => openPeek(it.id)}>
                        <td className="px-3 py-2.5 border-b border-neutral-100">
                          <button className="font-mono text-xs text-neutral-500 hover:text-brand-600"
                            onClick={(e) => { e.stopPropagation(); navigator.clipboard?.writeText(it.issue_key); }}
                            title="点击复制编号">{it.issue_key}</button>
                        </td>
                        <td className="px-3 py-2.5 border-b border-neutral-100 text-[13px]">
                          <span className="inline-flex items-center gap-1.5">{it.name}
                            <span className="opacity-0 group-hover:opacity-100 text-neutral-300">⋯</span>
                          </span>
                        </td>
                        <td className="px-3 py-2.5 border-b border-neutral-100"><StateBadge group={it.state_group ?? "unstarted"} name={it.state_name ?? "—"} /></td>
                        <td className="px-3 py-2.5 border-b border-neutral-100 text-[13px]">{it.assignee ? it.assignee.name : <span className="text-neutral-400">—</span>}</td>
                        <td className="px-3 py-2.5 border-b border-neutral-100 text-[13px]">
                          {it.target_date ? (
                            overdue
                              ? <span className="text-red-500 inline-flex items-center gap-1"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>{it.target_date}</span>
                              : <span className="font-mono text-xs text-neutral-500">{it.target_date}</span>
                          ) : <span className="text-neutral-400">—</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </main>
      </div>
      {peekId && <IssueDrawer issueId={peekId} slug={workspaceSlug!} projectId={projectId!} onClose={() => { closePeek(); load(); }} onChanged={() => load()} />}
      {showTaskModal && <NewTaskModal slug={workspaceSlug!} projectId={projectId!} projectName={project?.name ?? ""} onClose={() => setShowTaskModal(false)} onCreated={() => load()} />}
    </div>
  );
}
