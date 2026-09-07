import { NavLink, Outlet } from "react-router";

/** 运维台布局：四区导航（限流 / 备份 / 发布门禁+checklist）——C.134。 */
export default function OpsLayout() {
  const link = ({ isActive }: { isActive: boolean }) =>
    `px-3 h-9 flex items-center rounded-md text-[13px] no-underline ${isActive ? "bg-brand-500 text-white" : "text-neutral-600 hover:bg-neutral-100"}`;
  return (
    <div className="flex min-h-screen bg-neutral-50" data-sb-scope="ops">
      <aside className="w-52 shrink-0 border-r border-neutral-200 bg-white p-3 flex flex-col gap-1">
        <div className="px-2 py-3 text-[15px] font-semibold">🐰 God Mode</div>
        <div className="px-2 pb-1 text-[11px] text-neutral-400">实例运维（INFRA-005 / QA-001）</div>
        <NavLink to="/ops" end className={link}>限流监控</NavLink>
        <NavLink to="/ops/backups" className={link}>备份管理</NavLink>
        <NavLink to="/ops/release" className={link}>发布门禁</NavLink>
        <NavLink to="/" className={`${link} mt-auto`}>← 首页</NavLink>
      </aside>
      <main className="flex-1 min-w-0 p-6 overflow-x-auto">
        <Outlet />
      </main>
    </div>
  );
}
