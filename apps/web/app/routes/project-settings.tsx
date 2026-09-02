import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { ProjectAPI } from "../services/api";

export default function ProjectSettings() {
  const { workspaceSlug, projectId } = useParams<{ workspaceSlug: string; projectId: string }>();
  const nav = useNavigate();
  const [loaded, setLoaded] = useState(false);
  const [name, setName] = useState("");
  const [identifier, setIdentifier] = useState("");
  const [description, setDescription] = useState("");
  const [confirm, setConfirm] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [saved, setSaved] = useState(false);

  // 加载项目详情回显（修复：原版不拉数据，空字段 + PATCH 空 name 会清空项目名）
  useEffect(() => {
    ProjectAPI.detail(workspaceSlug!, projectId!).then((r) => {
      const p = (r as any).data;
      setName(p.name ?? ""); setIdentifier(p.identifier ?? ""); setDescription(p.description ?? "");
      setLoaded(true);
    });
  }, [workspaceSlug, projectId]);

  async function save() {
    await ProjectAPI.patch(workspaceSlug!, projectId!, { name, description });
    setSaved(true); setTimeout(() => setSaved(false), 1800);
  }

  async function doDelete() {
    await ProjectAPI.delete(workspaceSlug!, projectId!);
    nav(`/${workspaceSlug}/projects`);
  }

  return (
    <div className="flex flex-col h-screen">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <ProjectSidebar projectName={name || "…"} identifier={identifier} />
        <main className="flex-1 min-w-0 overflow-y-auto p-5">
          {!loaded ? <div className="text-sm text-neutral-500">加载中…</div> : (
          <div className="max-w-[640px]">
            <div className="bg-white border border-neutral-200 rounded-lg p-5 mb-4">
              <div className="text-[15px] font-semibold mb-3.5">基本信息</div>
              <div className="flex items-center gap-3 mb-3.5">
                <label htmlFor="st-name" className="w-20 text-[13px] text-neutral-500 shrink-0">项目名称</label>
                <input id="st-name" className="flex-1 h-9 border border-neutral-300 rounded-md px-2.5" value={name} onChange={(e) => setName(e.target.value)} />
              </div>
              <div className="flex items-center gap-3 mb-3.5">
                <label htmlFor="st-id" className="w-20 text-[13px] text-neutral-500 shrink-0">项目标识符</label>
                <input id="st-id" className="w-[130px] h-9 border border-neutral-300 rounded-md px-2.5 font-mono shrink-0" disabled value={identifier} />
                <span className="text-xs text-neutral-500 inline-flex items-center gap-1">🔒 创建后不可修改</span>
              </div>
              <div className="flex items-center gap-3 mb-3.5">
                <label htmlFor="st-desc" className="w-20 text-[13px] text-neutral-500 shrink-0">项目描述</label>
                <textarea id="st-desc" className="flex-1 border border-neutral-300 rounded-md p-2 text-sm" rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
              </div>
              <div className="text-right flex items-center justify-end gap-2">
                {saved && <span className="text-xs text-emerald-600 inline-flex items-center gap-1">✓ 已保存</span>}
                <button onClick={save} disabled={!name.trim()} className="h-[34px] px-3.5 bg-brand-500 text-white rounded-md hover:bg-brand-600 disabled:opacity-50">保存更改</button>
              </div>
            </div>
            <div className="bg-white border border-red-300 rounded-lg p-5">
              <div className="text-[15px] font-semibold mb-3.5 text-red-700">⚠️ 危险区域</div>
              <div className="text-xs text-neutral-500 mb-3">删除项目将同时删除其下全部任务，此操作不可在界面恢复。</div>
              <div className="text-right"><button onClick={() => setDeleting(true)} className="h-[34px] px-3.5 bg-red-500 text-white rounded-md hover:bg-red-600">删除项目</button></div>
            </div>
            {deleting && (
              <div className="fixed inset-0 bg-black/30 flex items-center justify-center p-4 z-50">
                <div className="bg-white rounded-xl shadow-lg w-[480px] p-6">
                  <div className="flex items-center justify-between mb-[18px]"><div className="text-base font-semibold">删除项目</div><button onClick={() => { setDeleting(false); setConfirm(""); }}>✕</button></div>
                  <div className="flex items-center gap-2 text-[13px] px-3 py-2 bg-red-50 text-red-700 rounded-md mb-3">⚠ 此操作不可恢复。输入项目名称 <b>{name}</b> 以确认。</div>
                  <input className="w-full h-9 border border-neutral-300 rounded-md px-2.5 mb-3" placeholder={name} value={confirm} onChange={(e) => setConfirm(e.target.value)} />
                  <div className="flex justify-end gap-2.5">
                    <button onClick={() => { setDeleting(false); setConfirm(""); }} className="h-[34px] px-3.5 bg-white border border-neutral-300 rounded-md">取消</button>
                    <button disabled={confirm !== name} onClick={doDelete} className="h-[34px] px-3.5 bg-red-500 text-white rounded-md disabled:opacity-50">删除项目</button>
                  </div>
                </div>
              </div>
            )}
          </div>
          )}
        </main>
      </div>
    </div>
  );
}
