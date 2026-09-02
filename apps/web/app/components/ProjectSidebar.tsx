import { NavLink, useParams } from "react-router";
import { useStores } from "../stores";

/** 项目侧栏 220px（高保真 PROJ-001 §3.3）：项目身份区 + 视图组 + 管理组 + 返回列表 */
export function ProjectSidebar({ projectName, identifier }: { projectName: string; identifier: string }) {
  const { workspaceSlug, projectId } = useParams<{ workspaceSlug: string; projectId: string }>();
  const base = `/${workspaceSlug}/projects/${projectId}`;
  const color = ["#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899"][(projectId?.charCodeAt(0) ?? 48) % 5];
  const item = (active: boolean) =>
    `h-[34px] px-2.5 rounded-md flex items-center gap-2 text-sm ${active ? "bg-brand-50 text-brand-600 font-medium" : "text-neutral-700 hover:bg-neutral-50"}`;
  return (
    <nav className="w-[220px] border-r border-neutral-200 bg-white flex flex-col gap-0.5 p-3 shrink-0">
      <div className="flex items-center gap-2.5 px-2.5 pt-1.5 pb-3">
        <span className="w-6 h-6 rounded-md text-white text-xs font-semibold flex items-center justify-center" style={{ background: color }}>
          {projectName.slice(0, 1)}
        </span>
        <div className="min-w-0"><div className="text-sm font-semibold truncate">{projectName}</div></div>
        <span className="badge-id">{identifier}</span>
      </div>
      <div className="text-[11px] font-semibold text-neutral-400 uppercase tracking-wider px-2.5 py-2">视图</div>
      <NavLink to={`${base}/issues`} end className={({ isActive }) => item(isActive)}>
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg>
        任务列表
      </NavLink>
      <NavLink to={`${base}/board`} className={({ isActive }) => item(isActive)}>
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M9 3v18M15 3v18"/></svg>
        看板
      </NavLink>
      <div className="text-[11px] font-semibold text-neutral-400 uppercase tracking-wider px-2.5 py-2 mt-1">管理</div>
      <NavLink to={`${base}/settings`} className={({ isActive }) => item(isActive)}>
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="3"/><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/></svg>
        项目设置
      </NavLink>
      <div className="mt-auto">
        <NavLink to={`/${workspaceSlug}/projects`} className="h-[34px] px-2.5 rounded-md flex items-center gap-2 text-sm text-neutral-700 hover:bg-neutral-50">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
          返回项目列表
        </NavLink>
      </div>
    </nav>
  );
}
