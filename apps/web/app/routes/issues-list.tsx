import { useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { IssueDrawer } from "../components/IssueDrawer";
import { NewTaskModal } from "../components/NewTaskModal";
import { StateBadge } from "../components/StateBadge";
import { IssueAPI, ProjectAPI } from "../services/api";
import { toast } from "../components/Toast";
import { LabelsAdminModal } from "./labels-admin";
import type { Issue } from "@rp/types";

const today = () => new Date().toISOString().slice(0, 10);

interface Chip { k: string; v: string; label: string }

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

  async function load() {
    try {
      const [iRes, pRes] = await Promise.all([
        IssueAPI.list(workspaceSlug!, projectId!, { ordering: "sort_order" }),
        ProjectAPI.detail(workspaceSlug!, projectId!),
      ]);
      setIssues(((iRes as unknown as { data: Issue[] }).data));
      setProject(((pRes as unknown as { data: typeof project }).data));
    } catch { /* 鉴权失败由 axios 拦截器统一跳转；此处吃掉 unhandled rejection */ }
  }
  useEffect(() => {
    const handle = setTimeout(() => { void load(); }, 0);
    return () => clearTimeout(handle);
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceSlug, projectId]);
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
    } catch (e: unknown) {
      setQuick(text); // 失败恢复输入（TASK-001 §3.2.1）
      const msg = e instanceof Error ? e.message : "创建失败";
      toast(msg, "error");
    } finally {
      setCreating(false);
      document.getElementById("tq-input")?.focus();
    }
  }

  // 服务端筛选参数（占位 — 当前接口未全量接；保留 UI 入口，未来由 IssueAPI.list 透传）
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q && chips.length === 0) return issues;
    return issues.filter((it) => {
      if (q && !(it.name.toLowerCase().includes(q) || it.issue_key.toLowerCase().includes(q))) return false;
      for (const c of chips) {
        if (c.k === "state" && it.state_name !== c.v) return false;
      }
      return true;
    });
  }, [issues, search, chips]);

  const total = issues.length;

  return (
    <div className="flex flex-col h-screen">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <ProjectSidebar projectName={project?.name ?? "…"} identifier={project?.identifier ?? ""} />
        <main className="flex-1 min-w-0 flex flex-col overflow-hidden">
          <div className="h-[53px] border-b border-neutral-200 flex items-center gap-2 px-5 bg-white shrink-0">
            <div><span className="text-[15px] font-semibold">任务列表</span>
              <span className="text-[13px] text-neutral-500 ml-2">{total} 个任务</span></div>
            <button onClick={() => setShowTaskModal(true)} className="ml-auto inline-flex h-[34px] items-center gap-1.5 px-3.5 bg-brand-500 text-white rounded-md font-medium hover:bg-brand-600">+ 创建任务</button>
          </div>
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
              <div className="flex flex-col items-center gap-2 py-12 text-neutral-500">
                <svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="#d4d4d4" strokeWidth="2"><rect width="8" height="4" x="8" y="2" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4M12 16h4M8 11h.01M8 16h.01"/></svg>
                <div className="text-[15px] font-semibold text-neutral-700">{total === 0 ? "暂无任务" : "没有符合当前筛选的任务"}</div>
                <div className="text-[13px]">{total === 0 ? "创建第一个任务开始工作" : "尝试调整或清空筛选"}</div>
                <button onClick={() => { if (total === 0) setShowTaskModal(true); else { setSearch(""); setChips([]); } }} className="mt-2 inline-flex h-[34px] items-center gap-1.5 px-3.5 bg-brand-500 text-white rounded-md font-medium">
                  {total === 0 ? "+ 创建任务" : "清空全部"}
                </button>
              </div>
            ) : (
              <table className="w-full border-collapse">
                <thead><tr>
                    {["编号", "标题", "状态", "负责人", "截止时间"].map((h, i) => (
                      <th key={h} className={`text-left px-3 py-2 border-b border-neutral-200 text-[11px] font-semibold text-neutral-400 uppercase tracking-wider ${["w-24", "", "w-[120px]", "w-[110px]", "w-[130px]"][i]}`}>{h}</th>
                    ))}
                  </tr></thead>
                <tbody>
                  {filtered.map((it) => {
                    const overdue = it.target_date && it.target_date < today() && it.state_group !== "completed";
                    return (
                      <tr key={it.id} className="group cursor-pointer hover:bg-neutral-50" onClick={() => openPeek(it.id)}>
                        <td className="px-3 py-2.5 border-b border-neutral-100">
                          <button className="font-mono text-xs text-neutral-500 hover:text-brand-600"
                            onClick={(e) => {
                              e.stopPropagation();
                              // 原型 copyText：复制成功/失败都给反馈（headless 无剪贴板权限时静默失败是缺陷）
                              navigator.clipboard?.writeText(it.issue_key)
                                .then(() => toast(`已复制 ${it.issue_key}`))
                                .catch(() => toast(`复制失败：${it.issue_key}`, "error"));
                            }}
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
      {showLabelsAdmin && workspaceSlug && projectId && (
        <LabelsAdminModal workspaceSlug={workspaceSlug} projectId={projectId} onClose={() => setShowLabelsAdmin(false)} />
      )}
    </div>
  );
}