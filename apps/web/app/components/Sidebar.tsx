import { NavLink } from "react-router";

const items = [
  // 首页挂 /:ws 根路径，必须精确匹配（end）——NavLink 默认前缀匹配会让它在
  // /projects、/settings 等一切工作区子路由上同时高亮（与「项目」双选中）
  { to: "home", label: "首页", enabled: true, end: true },
  { to: "projects", label: "项目", enabled: true },
  { to: "my-tasks", label: "我的任务", enabled: false, hint: "RPT-001 交付" },
  // ADR-0011 #18：工作区侧栏「团队设置」由置灰点亮为成员管理入口（TEAM-002 §3.1 / C.15）
  { to: "settings/members", label: "团队设置", enabled: true },
];

export function Sidebar({ workspaceSlug }: { workspaceSlug: string }) {
  return (
    <nav className="w-60 border-r border-neutral-200 bg-white flex flex-col p-3 gap-0.5">
      <div className="text-[11px] font-semibold text-neutral-400 uppercase tracking-wider px-2.5 py-2">工作区</div>
      {items.map((it) => {
        const href = it.to === "projects"
          ? `/${workspaceSlug}/projects`
          : it.to === "settings/members"
            ? `/${workspaceSlug}/settings/members`
            : `/${workspaceSlug}`;
        if (!it.enabled) {
          return (
            <span key={it.to} title={`${it.hint} · 即将上线`} className="h-[34px] px-2.5 rounded-md flex items-center gap-2 text-sm text-neutral-400 cursor-not-allowed">
              {it.label}
            </span>
          );
        }
        return (
          <NavLink key={it.to} to={href} end={it.end === true} className={({ isActive }) => `h-[34px] px-2.5 rounded-md flex items-center gap-2 text-sm ${isActive ? "bg-brand-50 text-brand-600 font-medium" : "text-neutral-700 hover:bg-neutral-50"}`}>
            {it.label}
          </NavLink>
        );
      })}
    </nav>
  );
}
