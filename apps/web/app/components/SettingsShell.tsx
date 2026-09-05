/** 个人设置壳（C.10 · AUTH-004 §3.1）：左侧固定导航（个人资料 / 安全 / 通知偏好[灰置]）
 *  + 右侧内容区 max-width 720px。二级页，不挂工作空间 Topbar/Sidebar。 */
import type { ReactNode } from "react";
import { Link } from "react-router";
import { Logo } from "./Logo";

const NAV: Array<{ key: "profile" | "security"; label: string; to: string }> = [
  { key: "profile", label: "个人资料", to: "/settings/profile" },
  { key: "security", label: "安全", to: "/settings/security" },
];

export function SettingsShell({ active, children }: { active: "profile" | "security"; children: ReactNode }) {
  return (
    <div className="min-h-screen bg-neutral-50">
      <header className="h-12 border-b border-neutral-200 bg-white flex items-center px-4">
        <div className="flex items-center gap-2 text-neutral-800">
          <Logo />
          <span className="text-[14px] font-semibold">RabbitProjects</span>
        </div>
        <span className="ml-3 text-[12px] text-neutral-400">个人设置</span>
      </header>
      <div className="mx-auto max-w-[1000px] px-6 py-8 flex gap-10">
        <nav aria-label="个人设置" className="w-[240px] shrink-0">
          <ul>
            {NAV.map((n) => (
              <li key={n.key} className="mb-0.5">
                <Link
                  to={n.to}
                  aria-current={active === n.key ? "page" : undefined}
                  className={`block px-3 h-9 leading-9 rounded-md text-[13px] ${
                    active === n.key
                      ? "bg-brand-50 text-brand-600 font-medium"
                      : "text-neutral-600 hover:bg-neutral-100"
                  }`}
                >
                  {n.label}
                </Link>
              </li>
            ))}
            {/* ADR-0011 #8：三项导航，通知偏好灰置占位不隐藏 */}
            <li>
              <span className="block px-3 h-9 leading-9 rounded-md text-[13px] text-neutral-300 cursor-not-allowed"
                    aria-disabled="true" title="即将上线">
                通知偏好<span className="ml-1.5 text-[11px]">即将上线</span>
              </span>
            </li>
          </ul>
        </nav>
        <div className="flex-1 min-w-0 max-w-[720px]">{children}</div>
      </div>
    </div>
  );
}
