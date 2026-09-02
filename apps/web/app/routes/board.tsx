import { useEffect, useState } from "react";
import { useParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { Sidebar } from "../components/Sidebar";
import { StateBadge } from "../components/StateBadge";
import { IssueAPI, ProjectAPI } from "../services/api";
import type { Issue } from "@rp/types";

interface Col { id: string | null; name: string; group: string; issues: Issue[]; }

const COL_NAMES = [
  { group: "unstarted", label: "待办" },
  { group: "started", label: "进行中" },
  { group: "completed", label: "已完成" },
];

export default function Board() {
  const { workspaceSlug, projectId } = useParams<{ workspaceSlug: string; projectId: string }>();
  const [cols, setCols] = useState<Col[]>([]);
  const [loading, setLoading] = useState(true);
  const [dragId, setDragId] = useState<string | null>(null);
  const [openIssueId, setOpenIssueId] = useState<string | null>(null);
  const [showTaskModal, setShowTaskModal] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const [sRes, iRes] = await Promise.all([
        ProjectAPI.states(workspaceSlug!, projectId!),
        IssueAPI.list(workspaceSlug!, projectId!, { ordering: "sort_order", per_page: 100 }),
      ]);
      const states = (sRes as any).data as Array<{ id: string; name: string; group: string }>;
      const issues = (iRes as any).data as Issue[];
      const map = new Map<string | null, Col>();
      for (const n of COL_NAMES) map.set(n.label, { id: null, name: n.label, group: n.group, issues: [] });
      // 占位状态：使用服务端返回的第一个匹配 group 的 state id
      for (const s of states) {
        const col = [...map.values()].find((c) => c.group === s.group);
        if (col && !col.id) col.id = s.id;
      }
      for (const it of issues) {
        const s = states.find((x) => x.id === it.state_id);
        if (!s) continue;
        const col = [...map.values()].find((c) => c.group === s.group);
        if (col) col.issues.push(it);
      }
      setCols([...map.values()]);
    } finally { setLoading(false); }
  }

  useEffect(() => { load(); }, [workspaceSlug, projectId]);

  async function move(id: string, targetGroup: string) {
    const col = cols.find((c) => c.group === targetGroup);
    if (!col?.id) return;
    const last = col.issues[col.issues.length - 1];
    const sort = last ? last.sort_order + 65535 : 65535;
    try {
      await IssueAPI.patch(workspaceSlug!, projectId!, id, { state_id: col.id, sort_order: sort });
      await load();
    } catch (e) { console.error(e); await load(); }
  }

  return (
    <div className="flex flex-col h-screen">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <Sidebar workspaceSlug={workspaceSlug!} />
        <main className="flex-1 min-w-0 flex flex-col">
          <div className="h-[56px] border-b border-neutral-200 flex items-center gap-2 px-5 bg-white">
            <span className="text-[15px] font-semibold">看板</span>
            <button onClick={() => setShowTaskModal(true)} className="ml-auto inline-flex h-[34px] items-center gap-1.5 px-3.5 bg-brand-500 text-white rounded-md font-medium">+ 创建任务</button>
          </div>
          <div className="flex-1 flex gap-4 overflow-x-auto p-4 min-h-0">
            {COL_NAMES.map((n) => {
              const col = cols.find((c) => c.group === n.group);
              return (
                <section key={n.group} className={`w-[280px] shrink-0 flex flex-col bg-neutral-100 rounded-lg max-h-full transition ${dragId && col?.issues.some((i) => i.id === dragId) ? "" : ""}`}
                  onDragOver={(e) => { e.preventDefault(); }}
                  onDrop={async (e) => { e.preventDefault(); if (dragId) { await move(dragId, n.group); setDragId(null); } }}>
                  <div className="h-11 flex items-center gap-2 px-3 shrink-0">
                    <span className="dot w-1.5 h-1.5 rounded-full" style={{ background: { unstarted: "#9ca3af", started: "#3b82f6", completed: "#10b981" }[n.group] }} />
                    <span className="text-[13px] font-medium">{n.label}</span>
                    <span className="ml-auto font-mono text-xs text-neutral-400">{col?.issues.length ?? 0}</span>
                  </div>
                  <div className="flex-1 overflow-y-auto p-2 flex flex-col gap-2 min-h-[56px]">
                    {col?.issues.length ? col.issues.map((it) => (
                      <article key={it.id} draggable tabIndex={0} className={`bg-white border border-neutral-200 rounded-md p-2.5 pl-3.5 shadow-sm cursor-grab relative hover:shadow-md hover:border-neutral-300 transition ${dragId === it.id ? "opacity-40 scale-[0.98]" : ""}`}
                        onDragStart={(e) => { setDragId(it.id); e.dataTransfer.setData("text/plain", it.id); }}
                        onDragEnd={() => setDragId(null)}
                        onClick={() => setOpenIssueId(it.id)}>
                        <div className="absolute left-0 top-2 bottom-2 w-[3px] rounded" style={{ background: { unstarted: "#9ca3af", started: "#3b82f6", completed: "#10b981" }[it.state_group ?? "unstarted"] }} />
                        <div className="text-[13px] text-neutral-900 line-clamp-3">{it.name}</div>
                        <div className="flex items-center justify-between mt-2 text-xs">
                          <span className="font-mono text-neutral-400">{it.issue_key}</span>
                          <span className="flex items-center gap-1.5 text-neutral-500">
                            {it.assignee && <span className="w-5 h-5 rounded-full text-white text-[10px] font-semibold flex items-center justify-center" style={{ background: ["#3b82f6","#10b981","#f59e0b"][(it.assignee.name?.charCodeAt(0) ?? 0) % 3] }}>{it.assignee.name.slice(0, 1)}</span>}
                            {it.target_date && <span className="font-mono text-[11px]">{new Date(it.target_date).getMonth() + 1}-{new Date(it.target_date).getDate()}</span>}
                          </span>
                        </div>
                      </article>
                    )) : <div className="border-2 border-dashed border-neutral-300 rounded-md py-4 text-center text-xs text-neutral-400">将任务拖拽到这里</div>}
                  </div>
                </section>
              );
            })}
          </div>
          {openIssueId && <IssueDrawer issueId={openIssueId} slug={workspaceSlug!} projectId={projectId!} onClose={() => { setOpenIssueId(null); load(); }} />}
          {showTaskModal && <NewTaskModal slug={workspaceSlug!} projectId={projectId!} onClose={() => { setShowTaskModal(false); load(); }} />}
        </main>
      </div>
    </div>
  );
}

function IssueDrawer({ issueId, slug, projectId, onClose }: { issueId: string; slug: string; projectId: string; onClose: () => void }) {
  const [issue, setIssue] = useState<Issue | null>(null);
  useEffect(() => { IssueAPI.detail(slug, projectId, issueId).then((r) => setIssue((r as any).data)); }, [issueId]);
  if (!issue) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/25" onClick={onClose} />
      <aside className="relative w-[720px] max-w-[calc(100vw-64px)] bg-white border-l border-neutral-200 shadow-lg flex flex-col">
        <div className="flex items-center gap-2 px-5 py-3 border-b border-neutral-200">
          <button className="font-mono text-[13px] text-neutral-500 hover:text-brand-600" onClick={() => navigator.clipboard?.writeText(issue.issue_key)}>{issue.issue_key}</button>
          <button onClick={onClose} className="ml-auto w-7 h-7 flex items-center justify-center text-neutral-500 hover:text-neutral-900 ml-auto">✕</button>
        </div>
        <div className="flex-1 overflow-y-auto p-5">
          <h2 className="text-lg font-semibold mb-3">{issue.name}</h2>
          <div className="border border-neutral-200 rounded-lg">
            <div className="flex gap-0.5 px-2 py-1.5 border-b border-neutral-200 bg-neutral-50 rounded-t-lg text-[13px] text-neutral-500">B I U ≡ ☰ ⌗ &lt;/&gt; 🔗</div>
            <div className="min-h-[110px] p-2.5 text-[13px] leading-relaxed" dangerouslySetInnerHTML={{ __html: issue.description_html || "<p class='text-neutral-400'>添加描述…</p>" }} />
          </div>
          <div className="mt-4 border-t border-neutral-200 pt-4 grid grid-cols-[76px_1fr] gap-x-3.5 gap-y-4 items-center">
            <label className="text-[13px] text-neutral-500">状态</label><div><StateBadge group={issue.state_group ?? "unstarted"} name={issue.state_name ?? "—"} /></div>
            <label className="text-[13px] text-neutral-500">负责人</label><div className="text-[13px]">{issue.assignee?.name ?? "—"}</div>
            <label className="text-[13px] text-neutral-500">截止时间</label><div className="text-[13px]">{issue.target_date || "—"}</div>
          </div>
        </div>
      </aside>
    </div>
  );
}

function NewTaskModal({ slug, projectId, onClose }: { slug: string; projectId: string; onClose: () => void }) {
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-center justify-center p-4 z-50">
      <div className="bg-white rounded-xl shadow-lg w-[640px] p-6">
        <div className="flex items-center justify-between mb-[18px]"><div className="text-base font-semibold">创建任务</div><button onClick={onClose}>✕</button></div>
        {err && <div className="mb-3.5 px-3 py-2 bg-red-50 text-red-700 rounded-md text-[13px]">{err}</div>}
        <form onSubmit={async (e) => {
          e.preventDefault(); setErr(null);
          if (!name.trim()) { setErr("请填写任务标题"); return; }
          try { await IssueAPI.create(slug, projectId, { name }); onClose(); } catch (er: any) { setErr(er?.message ?? "创建失败"); }
        }}>
          <input className="w-full h-10 text-[17px] font-medium border-0 border-b-2 border-transparent focus:border-brand-500 focus:outline-none bg-transparent px-0 mb-3" placeholder="任务标题" autoFocus value={name} onChange={(e) => setName(e.target.value)} />
          <div className="flex justify-end gap-2.5 mt-5"><button type="button" onClick={onClose} className="h-[34px] px-3.5 bg-white border border-neutral-300 rounded-md">取消</button><button type="submit" className="h-[34px] px-3.5 bg-brand-500 text-white rounded-md hover:bg-brand-600">创建任务</button></div>
        </form>
      </div>
    </div>
  );
}
