import { NavLink } from "react-router";

const items = [
  // 首页挂 /:ws 根路径，必须精确匹配（end）——NavLink 默认前缀匹配会让它在
  // /projects、/settings 等一切工作区子路由上同时高亮（与「项目」双选中）
  { to: "home", label: "首页", enabled: true, end: true },
  { to: "projects", label: "项目", enabled: true },
  // WF-002 §3.1（Sprint-7）：审批中心一级入口（C.140）
  { to: "approvals", label: "审批", enabled: true },
  // WF-005 §3（Sprint-7 / C.145）：工作流模板库一级入口（预设四套 + 两步下发）——2026-09-09 入口补口
  { to: "workflow-templates", label: "模板库", enabled: true },
  // AUTH-007 §3.1（C.150，Sprint-8 R6）：组织管理一级入口
  { to: "org", label: "组织", enabled: true },
  // AUTH-010 §3.1（C.152，Sprint-8 R6）：全站审计日志一级入口
  { to: "audit-logs", label: "审计", enabled: true },
  { to: "my-tasks", label: "我的任务", enabled: false, hint: "RPT-001 交付" },
  // ADR-0011 #18：工作区侧栏「团队设置」由置灰点亮为成员管理入口（TEAM-002 §3.1 / C.15）
  { to: "settings/members", label: "团队设置", enabled: true },
  // AUTH-009 §3.1（C.155，Sprint-8 R6）：SSO 配置（WS_OWNER）
  { to: "settings/sso", label: "SSO 登录", enabled: true },
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
            : it.to === "approvals"
              ? `/${workspaceSlug}/approvals`
              : it.to === "workflow-templates"
                ? `/${workspaceSlug}/workflow-templates`
                : it.to === "org"
                  ? `/${workspaceSlug}/org`
                  : it.to === "audit-logs"
                    ? `/${workspaceSlug}/audit-logs`
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
