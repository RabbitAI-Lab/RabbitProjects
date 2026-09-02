import { NavLink } from "react-router";

const items = [
  { to: "home", label: "首页", enabled: false, hint: "Sprint 1+ 交付" },
  { to: "projects", label: "项目", enabled: true },
  { to: "my-tasks", label: "我的任务", enabled: false, hint: "RPT-001 交付" },
  { to: "settings", label: "团队设置", enabled: false, hint: "TEAM-002 起交付" },
];

export function Sidebar({ workspaceSlug }: { workspaceSlug: string }) {
  return (
    <nav className="w-60 border-r border-neutral-200 bg-white flex flex-col p-3 gap-0.5">
      <div className="text-[11px] font-semibold text-neutral-400 uppercase tracking-wider px-2.5 py-2">工作区</div>
      {items.map((it) => {
        const href = it.to === "projects" ? `/${workspaceSlug}/projects` : `/${workspaceSlug}`;
        if (!it.enabled) {
          return (
            <span key={it.to} title={`${it.hint} · 即将上线`} className="h-[34px] px-2.5 rounded-md flex items-center gap-2 text-sm text-neutral-400 cursor-not-allowed">
              {it.label}
            </span>
          );
        }
        return (
          <NavLink key={it.to} to={href} className={({ isActive }) => `h-[34px] px-2.5 rounded-md flex items-center gap-2 text-sm ${isActive ? "bg-brand-50 text-brand-600 font-medium" : "text-neutral-700 hover:bg-neutral-50"}`}>
            {it.label}
          </NavLink>
        );
      })}
    </nav>
  );
}
