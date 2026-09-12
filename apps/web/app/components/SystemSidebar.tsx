import { NavLink } from "react-router";
import { observer } from "mobx-react-lite";

import { useStores } from "../stores";
import { WorkspaceRole } from "../stores/permission";

/**
 * 系统管理侧栏（2026-09-12 用户走查定稿：治理/身份/安全族从空间侧栏
 * 迁入头像下拉「系统管理」入口的本控制台）。
 *
 * 两组：
 *   管理与治理 —— 组织 / 审计 / 模板库 / SSO 登录（SSO 项 OWNER 专属——
 *                含全体成员认证入口；升租户级后改挂租户配置，AUTH-009 回改）
 *   设置       —— 身份源（目录同步）/ 安全中心（租户安全）
 *
 * 整台控制台 WS_ADMIN+（入口可见性在 Topbar 下拉同口径判定；页面后端
 * 守卫兜底）。角色经 PermissionStore.workspaceRole，快照未到达 fail-open。
 */
const GROUPS = [
  {
    key: "governance",
    label: "管理与治理",
    items: [
      { to: "org", label: "组织", minRole: WorkspaceRole.ADMIN },
      { to: "audit-logs", label: "审计", minRole: WorkspaceRole.ADMIN },
      { to: "workflow-templates", label: "模板库", minRole: WorkspaceRole.ADMIN },
      { to: "settings/sso", label: "SSO 登录", minRole: WorkspaceRole.OWNER },
    ],
  },
  {
    key: "settings",
    label: "设置",
    items: [
      { to: "settings/directory", label: "身份源", minRole: WorkspaceRole.ADMIN },
      { to: "settings/security", label: "安全中心", minRole: WorkspaceRole.ADMIN },
    ],
  },
] as const;

export const SystemSidebar = observer(function SystemSidebar({ workspaceSlug }: {
  workspaceSlug: string;
}) {
  const { permission } = useStores();
  const role = permission.snapshot === null
    ? WorkspaceRole.OWNER
    : permission.workspaceRole(undefined, workspaceSlug);

  return (
    <nav className="w-60 border-r border-neutral-200 bg-white flex flex-col p-3 gap-0.5 overflow-y-auto" data-sb-scope="system-sidebar">
      <div className="px-2.5 py-2 flex items-center gap-2">
        <div className="w-6 h-6 rounded-md bg-neutral-800 text-white text-[11px] font-semibold flex items-center justify-center">⚙</div>
        <div>
          <div className="text-[13px] font-semibold leading-tight">系统管理</div>
          <div className="text-[11px] text-neutral-400 leading-tight">租户配置与治理</div>
        </div>
      </div>
      {GROUPS.map((group) => (
        <div key={group.key} className={group.key === "governance" ? "mt-2" : "mt-3"}>
          <div className="text-[11px] font-semibold text-neutral-400 uppercase tracking-wider px-2.5 py-1.5">
            {group.label}
          </div>
          {group.items.map((it) => (
            <NavLink key={it.to} to={`/${workspaceSlug}/${it.to}`}
              className={({ isActive }) =>
                `h-[34px] px-2.5 rounded-md flex items-center gap-2 text-sm ${isActive
                  ? "bg-brand-50 text-brand-600 font-medium"
                  : "text-neutral-700 hover:bg-neutral-50"}`}>
              {it.label}
            </NavLink>
          ))}
        </div>
      ))}
    </nav>
  );
});
