import { NavLink, useParams } from "react-router";
import { useStores } from "../stores";

/** Sprint-3（BOARD-003 §3.2 布局段）：表格视图图标（list/kanban 同族 lucide 路径）。 */
const tableIcon = (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/></svg>
);

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
      <NavLink to={`${base}/table`} className={({ isActive }) => item(isActive)}>
        {tableIcon}
        表格
      </NavLink>
      <NavLink to={`${base}/board`} className={({ isActive }) => item(isActive)}>
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M9 3v18M15 3v18"/></svg>
        看板
      </NavLink>
      {/* Sprint-3 Phase 3-C（COLLAB-003 §3.1 / 原型 O5）：动态流页入口（pulse 图标） */}
      <NavLink to={`${base}/activity`} className={({ isActive }) => item(isActive)}>
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 12h4l3-9 4 18 3-9h4"/></svg>
        动态
      </NavLink>
      <div className="text-[11px] font-semibold text-neutral-400 uppercase tracking-wider px-2.5 py-2 mt-1">管理</div>
      <NavLink to={`${base}/settings`} className={({ isActive }) => item(isActive)}>
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="3"/><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/></svg>
        项目设置
      </NavLink>
      {/* Sprint-2（TASK-008 §3.1 / C.52）：字段管理（项目设置 → 字段） */}
      <NavLink to={`${base}/settings/fields`} className={({ isActive }) => item(isActive)}>
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42z"/><circle cx="7.5" cy="7.5" r=".5" fill="currentColor"/></svg>
        字段管理
      </NavLink>
      {/* Sprint-2（TASK-010 §3.2 / C.62）：admin 死信补偿入口（系统级页面） */}
      <NavLink to="/admin/dead-letters" className={({ isActive }) => item(isActive)}>
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="M12 8v4M12 16h.01"/></svg>
        死信补偿（admin）
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
