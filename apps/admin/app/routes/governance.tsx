import { NavLink, Outlet } from "react-router";

/** 租户治理布局（AUTH-012 §3.1 平台侧——冻结原型 V-TENANTS 侧栏 IA：
 * 身份块 +「平台治理」组三项，边界报告台账 R1+ 置灰）。 */
export default function GovernanceLayout() {
  const link = ({ isActive }: { isActive: boolean }) =>
    `px-3 h-9 flex items-center rounded-md text-[13px] no-underline ${isActive ? "bg-brand-500 text-white" : "text-neutral-600 hover:bg-neutral-100"}`;
  return (
    <div className="flex min-h-screen bg-neutral-50" data-sb-scope="governance">
      <aside className="w-52 shrink-0 border-r border-neutral-200 bg-white p-3 flex flex-col gap-1">
        <div className="px-2 py-3 flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-neutral-800 text-white text-[11px] font-semibold flex items-center justify-center">RP</div>
          <div>
            <div className="text-[13.5px] font-semibold leading-tight">平台运营后台</div>
            <div className="text-[11px] text-neutral-400 leading-tight">tenant_ops · SaaS 治理</div>
          </div>
        </div>
        <div className="px-2 pt-2 pb-1 text-[11px] text-neutral-400">平台治理</div>
        <NavLink to="/governance" end className={link}>▦ 租户总览</NavLink>
        <NavLink to="/governance/risk-events" className={link}>⚠ 风控事件中心</NavLink>
        <button disabled title="后续轮次交付"
          className="px-3 h-9 flex items-center rounded-md text-[13px] text-neutral-300 cursor-not-allowed">
          🧾 边界报告台账 <span className="ml-auto text-[10px]">R1+</span>
        </button>
        <NavLink to="/ops" className={`${link} mt-auto`}>← 实例运维</NavLink>
      </aside>
      <main className="flex-1 min-w-0 p-6 overflow-x-auto">
        <Outlet />
      </main>
    </div>
  );
}
