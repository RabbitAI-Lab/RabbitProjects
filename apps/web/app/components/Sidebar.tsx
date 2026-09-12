import { NavLink } from "react-router";
import { observer } from "mobx-react-lite";

import { useStores } from "../stores";
import { WorkspaceRole } from "../stores/permission";

/**
 * 工作空间侧栏（菜单 IA 重排 2026-09-12，用户走查定稿）。
 *
 * 三组心智分组 + 角色可见性（2026-09-12 二次定稿：治理/身份/安全族迁
 * 头像下拉「系统管理」控制台——SystemSidebar/system.tsx）：
 *   工作台 —— 空间资产与日常工作流（全员）
 *   个人   —— 跟「我」走的事务（全员）
 *   设置   —— 仅成员与角色（WS_ADMIN+；用户裁定保留于空间设置）
 *
 * 角色来源：PermissionStore.workspaceRole（快照未到达 fail-open 显示，
 * 与 PermissionGate 竞态口径一致——后端权限守卫兜底）。
 *
 * href 显式映射（走查修复 2026-09-10）：映射表漏配即 key 缺失，TypeScript
 * 编译期报错（不再有静默兜底）。
 */
const HREF: Record<string, string> = {
  home: "",
  projects: "projects",
  portfolios: "portfolios",
  "wiki-search": "wiki-search",
  approvals: "approvals",
  "my-tasks": "my-tasks",
  "settings/members": "settings/members",
};

type Item = {
  to: keyof typeof HREF & string;
  label: string;
  enabled: boolean;
  /** 该项最低空间角色（缺省 GUEST=全员） */
  minRole?: number;
  end?: boolean;
  hint?: string;
};
type Group = { key: string; label: string; minRole?: number; items: Item[] };

const GROUPS: Group[] = [
  {
    key: "work",
    label: "工作台",
    items: [
      // 首页挂 /:ws 根路径，必须精确匹配（end）——NavLink 默认前缀匹配会让它在
      // /projects、/settings 等一切工作区子路由上同时高亮（与「项目」双选中）
      { to: "home", label: "首页", enabled: true, end: true },
      { to: "projects", label: "项目", enabled: true },
      // PROJ-004 §3.1（Sprint-9）：项目集一级入口
      { to: "portfolios", label: "项目集", enabled: true },
      // FILE-005 §3.3（Sprint-9）：知识检索（Wiki 专属）
      { to: "wiki-search", label: "知识检索", enabled: true },
    ],
  },
  {
    key: "personal",
    label: "个人",
    items: [
      // WF-002 §3.1（Sprint-7 / C.140）：审批中心——个人事务归「个人」组
      { to: "approvals", label: "审批", enabled: true },
      { to: "my-tasks", label: "我的任务", enabled: false, hint: "RPT-001 交付" },
    ],
  },
  {
    key: "settings",
    label: "设置",
    minRole: WorkspaceRole.ADMIN,
    items: [
      // ADR-0011 #18 / TEAM-002 §3.1（C.15）：成员与角色（用户裁定保留于
      // 空间设置；治理/身份/安全族已迁头像下拉「系统管理」控制台）
      { to: "settings/members", label: "成员与角色", enabled: true },
    ],
  },
];

export const Sidebar = observer(function Sidebar({ workspaceSlug }: {
  workspaceSlug: string;
}) {
  const { permission } = useStores();
  // 快照未到达 → fail-open 显示管理组（角色未知按可见处理；后端守卫兜底）
  const role = permission.snapshot === null
    ? WorkspaceRole.OWNER
    : permission.workspaceRole(undefined, workspaceSlug);

  return (
    <nav className="w-60 border-r border-neutral-200 bg-white flex flex-col p-3 gap-0.5 overflow-y-auto">
      {GROUPS.map((group) => {
        const groupVisible = role >= (group.minRole ?? WorkspaceRole.GUEST);
        if (!groupVisible) return null;
        const visible = group.items.filter(
          (it) => role >= (it.minRole ?? WorkspaceRole.GUEST));
        return (
          <div key={group.key} className={group.key === "work" ? "" : "mt-3"}>
            <div className="text-[11px] font-semibold text-neutral-400 uppercase tracking-wider px-2.5 py-1.5">
              {group.label}
            </div>
            {visible.map((it) => {
              const tail = HREF[it.to];
              const href = tail === ""
                ? `/${workspaceSlug}`
                : `/${workspaceSlug}/${tail}`;
              if (!it.enabled) {
                return (
                  <span key={it.to}
                    title={`${"hint" in it ? it.hint : ""} · 即将上线`}
                    className="h-[34px] px-2.5 rounded-md flex items-center gap-2 text-sm text-neutral-400 cursor-not-allowed">
                    {it.label}
                  </span>
                );
              }
              return (
                <NavLink key={it.to} to={href} end={it.end === true}
                  className={({ isActive }) =>
                    `h-[34px] px-2.5 rounded-md flex items-center gap-2 text-sm ${isActive
                      ? "bg-brand-50 text-brand-600 font-medium"
                      : "text-neutral-700 hover:bg-neutral-50"}`}>
                  {it.label}
                </NavLink>
              );
            })}
          </div>
        );
      })}
    </nav>
  );
});
