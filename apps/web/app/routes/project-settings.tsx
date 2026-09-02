import { useState } from "react";
import { useParams, useNavigate } from "react-router";
import { Topbar } from "../components/Topbar";
import { Sidebar } from "../components/Sidebar";
import { ProjectAPI } from "../services/api";

export default function ProjectSettings() {
  const { workspaceSlug, projectId } = useParams<{ workspaceSlug: string; projectId: string }>();
  const nav = useNavigate();
  const [name, setName] = useState("");
  const [identifier, setIdentifier] = useState("");
  const [description, setDescription] = useState("");
  const [confirm, setConfirm] = useState("");
  const [deleting, setDeleting] = useState(false);
  return (
    <div className="flex flex-col h-screen">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <Sidebar workspaceSlug={workspaceSlug!} />
        <main className="flex-1 overflow-y-auto p-4">
          <div className="max-w-[640px]">
            <div className="bg-white border border-neutral-200 rounded-lg p-5 mb-4">
              <div className="text-[15px] font-semibold mb-3.5">基本信息</div>
              <div className="flex items-center gap-3 mb-3.5"><label className="w-21 text-[13px] text-neutral-500">项目名称</label><input className="flex-1 h-9 border border-neutral-300 rounded-md px-2.5" value={name} onChange={(e) => setName(e.target.value)} /></div>
              <div className="flex items-center gap-3 mb-3.5"><label className="w-21 text-[13px] text-neutral-500">项目标识符</label><input className="w-[130px] h-9 border border-neutral-300 rounded-md px-2.5 font-mono" disabled value={identifier} onChange={(e) => setIdentifier(e.target.value)} /><span className="text-xs text-neutral-500 inline-flex items-center gap-1">🔒 创建后不可修改</span></div>
              <div className="flex items-center gap-3 mb-3.5"><label className="w-21 text-[13px] text-neutral-500">项目描述</label><textarea className="flex-1 border border-neutral-300 rounded-md p-2 text-sm" rows={3} value={description} onChange={(e) => setDescription(e.target.value)} /></div>
              <div className="text-right"><button onClick={async () => { await ProjectAPI.patch(workspaceSlug!, projectId!, { name, description }); }} className="h-[34px] px-3.5 bg-brand-500 text-white rounded-md hover:bg-brand-600">保存更改</button></div>
            </div>
            <div className="bg-white border border-red-300 rounded-lg p-5">
              <div className="text-[15px] font-semibold mb-3.5 text-red-700">⚠️ 危险区域</div>
              <div className="text-xs text-neutral-500 mb-3">删除项目将同时删除其下全部任务，此操作不可在界面恢复。</div>
              <div className="text-right"><button onClick={() => setDeleting(true)} className="h-[34px] px-3.5 bg-red-500 text-white rounded-md hover:bg-red-600">删除项目</button></div>
            </div>
            {deleting && (
              <div className="fixed inset-0 bg-black/30 flex items-center justify-center p-4 z-50">
                <div className="bg-white rounded-xl shadow-lg w-[480px] p-6">
                  <div className="flex items-center justify-between mb-[18px]"><div className="text-base font-semibold">删除项目</div><button onClick={() => setDeleting(false)}>✕</button></div>
                  <div className="flex items-center gap-2 text-[13px] px-3 py-2 bg-red-50 text-red-700 rounded-md mb-3">⚠ 此操作不可恢复。输入项目名称 <b>{name || "项目"}</b> 以确认。</div>
                  <input className="w-full h-9 border border-neutral-300 rounded-md px-2.5 mb-3" placeholder={name || "项目"} value={confirm} onChange={(e) => setConfirm(e.target.value)} />
                  <div className="flex justify-end gap-2.5"><button onClick={() => setDeleting(false)} className="h-[34px] px-3.5 bg-white border border-neutral-300 rounded-md">取消</button>
                  <button disabled={confirm !== name} onClick={async () => { await ProjectAPI.delete(workspaceSlug!, projectId!); nav(`/${workspaceSlug}/projects`); }} className="h-[34px] px-3.5 bg-red-500 text-white rounded-md disabled:opacity-50">删除项目</button></div>
                </div>
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
