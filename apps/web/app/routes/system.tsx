/**
 * 系统管理枢纽页（2026-09-12 用户走查定稿）——头像下拉「系统管理」入口的
 * 落地页：租户级配置与治理族（组织/审计/模板库/SSO/身份源/安全中心）的
 * 一屏导览。WS_ADMIN+（后端各页面守卫兜底）。
 */
import { observer } from "mobx-react-lite";
import { Link, useParams } from "react-router";

import { useStores } from "../stores";
import { WorkspaceRole } from "../stores/permission";
import { Topbar } from "../components/Topbar";
import { SystemSidebar } from "../components/SystemSidebar";

const CARDS = [
  {
    to: "org", icon: "🏗", title: "组织",
    desc: "部门树 · 岗位 · 按部门授权（AUTH-007）",
  },
  {
    to: "audit-logs", icon: "📜", title: "审计",
    desc: "全站敏感操作留痕 · 哈希链完整性 · 180 天留存（AUTH-010）",
  },
  {
    to: "workflow-templates", icon: "🧩", title: "模板库",
    desc: "工作流模板浏览与两步下发（WF-005）",
  },
  {
    to: "settings/sso", icon: "🔑", title: "SSO 登录",
    desc: "SAML/OIDC 身份提供方 · 强制 SSO（AUTH-009 · 仅所有者）",
    minRole: WorkspaceRole.OWNER,
  },
  {
    to: "settings/directory", icon: "🗂", title: "身份源",
    desc: "LDAP/SCIM 目录同步 · 离职自动禁用（AUTH-011）",
  },
  {
    to: "settings/security", icon: "🛡", title: "安全中心",
    desc: "租户风控事件 · 阈值调紧 · L2 工单（AUTH-012）",
  },
] satisfies ReadonlyArray<{
  to: string; icon: string; title: string; desc: string;
  minRole?: number;
}>;

export default observer(function SystemPage() {
  const { workspaceSlug: ws } = useParams<{ workspaceSlug: string }>();
  const { permission } = useStores();
  const role = permission.snapshot === null
    ? WorkspaceRole.OWNER
    : permission.workspaceRole(undefined, ws ?? "");

  return (
    <div className="flex h-screen flex-col">
      <Topbar />
      <div className="flex flex-1 overflow-hidden">
        <SystemSidebar workspaceSlug={ws ?? ""} />
        <main className="flex-1 overflow-auto bg-neutral-50" data-sb-scope="system-home">
          <div className="mx-auto max-w-[1000px] px-6 py-6">
            <div className="mb-5">
              <div className="text-[17px] font-semibold">系统管理</div>
              <div className="text-[12.5px] text-neutral-400">
                租户级配置与治理——身份、审计与安全族（WS_ADMIN+）
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3.5">
              {CARDS.map((c) => {
                if (role < (c.minRole ?? WorkspaceRole.ADMIN)) return null;
                return (
                  <Link key={c.to} to={`/${ws}/${c.to}`}
                    className="rounded-lg border border-neutral-200 bg-white p-4 no-underline hover:border-brand-400 hover:shadow-sm transition-colors"
                    data-sb-scope="system-card">
                    <div className="flex items-center gap-2.5">
                      <span className="w-8 h-8 rounded-lg bg-neutral-50 border border-neutral-200 flex items-center justify-center text-[16px]">{c.icon}</span>
                      <div className="text-[14px] font-semibold text-neutral-800">{c.title}</div>
                    </div>
                    <div className="mt-2 text-[12.5px] text-neutral-500 leading-relaxed">{c.desc}</div>
                  </Link>
                );
              })}
            </div>
          </div>
        </main>
      </div>
    </div>
  );
});
