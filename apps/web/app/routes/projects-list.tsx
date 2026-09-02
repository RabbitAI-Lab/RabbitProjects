import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { Sidebar } from "../components/Sidebar";
import { ProjectAPI, WorkspaceAPI } from "../services/api";
import type { ProjectSummary, WorkspaceSummary } from "@rp/types";

export default function ProjectsList() {
  const { workspaceSlug } = useParams<{ workspaceSlug: string }>();
  const [ws, setWs] = useState<WorkspaceSummary | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [showNew, setShowNew] = useState(false);

  useEffect(() => {
    WorkspaceAPI.list().then((r) => {
      const list = (r as any).data as WorkspaceSummary[];
      setWs(list.find((w) => w.slug === workspaceSlug) ?? null);
    });
    ProjectAPI.listByWs(workspaceSlug!).then((r) => setProjects((r as any).data));
  }, [workspaceSlug]);

  return (
    <div className="flex flex-col h-screen">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <Sidebar workspaceSlug={workspaceSlug!} />
        <main className="flex-1 min-w-0 overflow-y-auto px-6 py-5">
          {!ws && <div className="text-sm text-neutral-500">加载中…</div>}
          {ws && (
            <>
              <div className="flex items-center gap-3 mb-5">
                <div><div className="text-lg font-semibold">项目</div><div className="text-[13px] text-neutral-500">{ws.name} · {projects.length} 个项目</div></div>
                <button onClick={() => setShowNew(true)} className="ml-auto inline-flex h-[34px] items-center gap-1.5 px-3.5 bg-brand-500 text-white rounded-md font-medium hover:bg-brand-600">+ 创建项目</button>
              </div>
              {projects.length === 0 ? (
                <div className="flex flex-col items-center gap-2 py-16 text-neutral-500">
                  <svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="#d4d4d4" strokeWidth="2"><path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/></svg>
                  <div className="text-[15px] font-semibold text-neutral-700">还没有项目</div>
                  <div className="text-[13px]">点击创建第一个项目，开始管理你的工作</div>
                  <button onClick={() => setShowNew(true)} className="mt-2 inline-flex h-[34px] items-center gap-1.5 px-3.5 bg-brand-500 text-white rounded-md font-medium">+ 创建项目</button>
                </div>
              ) : (
                <div className="grid gap-4 grid-cols-[repeat(auto-fill,minmax(265px,1fr))]">
                  {projects.map((p) => (
                    <Link key={p.id} to={`/${workspaceSlug}/projects/${p.id}/board`} className="block bg-white border border-neutral-200 rounded-lg p-4 hover:border-brand-100 hover:shadow-md transition">
                      <div className="flex items-start gap-2.5">
                        <span className="w-6 h-6 rounded-md text-white text-xs font-semibold flex items-center justify-center" style={{ background: ["#3b82f6","#10b981","#f59e0b","#8b5cf6","#ec4899"][(p.id?.charCodeAt(0) ?? 0) % 5] }}>{p.name.slice(0, 1)}</span>
                        <div className="min-w-0"><div className="text-[15px] font-medium truncate">{p.name}</div><div className="flex items-center gap-1.5 mt-1 text-xs text-neutral-600"><span className="font-mono px-1.5 py-0.5 rounded bg-neutral-100 text-neutral-600">{(p as any).identifier ?? ""}</span><span className="inline-flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full" style={{ background: "#10b981" }} />进行中</span></div></div>
                      </div>
                      <div className="text-[13px] text-neutral-500 mt-2 line-clamp-2">企业级项目管理系统</div>
                      <div className="flex items-center justify-between mt-3.5"><span className="text-xs text-neutral-400">0 个任务</span></div>
                    </Link>
                  ))}
                </div>
              )}
            </>
          )}
          {showNew && <NewProjectModal slug={workspaceSlug!} onClose={() => setShowNew(false)} />}
        </main>
      </div>
    </div>
  );
}

function NewProjectModal({ slug, onClose }: { slug: string; onClose: () => void }) {
  const [name, setName] = useState("");
  const [identifier, setIdentifier] = useState("");
  const [identifierDirty, setIdentifierDirty] = useState(false);
  const [description, setDescription] = useState("");
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-center justify-center p-4 z-50">
      <div className="bg-white rounded-xl shadow-lg w-[520px] p-6">
        <div className="flex items-center justify-between mb-[18px]"><div className="text-base font-semibold">创建项目</div><button onClick={onClose}>✕</button></div>
        {err && <div className="mb-3.5 px-3 py-2 bg-red-50 text-red-700 rounded-md text-[13px]">{err}</div>}
        <form onSubmit={async (e) => {
          e.preventDefault(); setErr(null);
          if (!name.trim() || !/^[A-Z]{2,5}$/.test(identifier)) { setErr("请填写项目名称和 2-5 个大写字母的标识符"); return; }
          try {
            const r = await ProjectAPI.create(slug, { name, identifier, description });
            location.href = `/${slug}/projects/${(r as any).data.id}/board`;
          } catch (e: any) { setErr(e?.message ?? "创建失败"); }
        }}>
          <div className="mb-4"><label className="block text-[13px] font-medium text-neutral-700 mb-1.5">项目名称 *</label><input className="w-full h-9 border border-neutral-300 rounded-md px-2.5" value={name} onChange={(e) => {
            setName(e.target.value);
            if (!identifierDirty) {
              const sug = e.target.value.trim().split(/[\s一-龥]+/).filter((w: string) => /^[a-zA-Z]/.test(w)).map((w: string) => w[0]).join("").toUpperCase().slice(0, 5);
              setIdentifier(sug);
            }
          }} /></div>
          <div className="mb-4">
            <label className="block text-[13px] font-medium text-neutral-700 mb-1.5">项目标识符 *</label>
            <div className="flex gap-2.5 items-center">
              <input className="w-[150px] h-9 border border-neutral-300 rounded-md px-2.5 font-mono uppercase" maxLength={5} value={identifier} onChange={(e) => { setIdentifierDirty(true); setIdentifier(e.target.value.replace(/[^a-zA-Z]/g, "").toUpperCase()); }} />
              <span className="font-mono px-2.5 py-1 rounded bg-neutral-100 text-neutral-600 text-[13px]">{identifier || "…"} -1</span>
            </div>
            <div className="text-xs text-neutral-500 mt-1.5">2-5 个大写字母，用于生成任务编号，<b>创建后不可修改</b></div>
          </div>
          <div className="mb-4"><label className="block text-[13px] font-medium text-neutral-700 mb-1.5">项目描述</label><textarea className="w-full border border-neutral-300 rounded-md p-2 text-sm" rows={3} maxLength={2000} value={description} onChange={(e) => setDescription(e.target.value)} /><div className="text-right text-xs text-neutral-400">{description.length} / 2000</div></div>
          <div className="flex justify-end gap-2.5 mt-5">
            <button type="button" onClick={onClose} className="h-[34px] px-3.5 bg-white text-neutral-700 border border-neutral-300 rounded-md hover:bg-neutral-50">取消</button>
            <button type="submit" className="h-[34px] px-3.5 bg-brand-500 text-white rounded-md hover:bg-brand-600">创建项目</button>
          </div>
        </form>
      </div>
    </div>
  );
}
