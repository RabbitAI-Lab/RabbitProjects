/** AUTH-005 §3.3 403 路由页 `/403?required=<permission_key>`。
 *
 *  清单行（附录 C.14）：
 *  - 403 页 /403：shield-off 96px 图标 +「没有访问该页面的权限」+ 副文案「当前角色不满足『{权限点中文名}』所需的最低角色要求。」
 *    + 按钮「返回工作台」「切换账号」+ 底注「认为这是误判？请联系空间管理员检查你的角色。」
 *    required 权限点经 URL 参数带入并渲染为中文名（PERMISSION_LABELS），不裸露英文 key；不提供「申请权限」动线。
 *  - 403 页 a11y：主标题 role="alert"、焦点自动移至标题、「返回工作台」为默认焦点按钮
 *  - 403 两套落点分工：直达无权 URL → 本路由守卫页（保留具体缺哪个权限）；
 *    页内请求失败的 403 → INFRA-004 §3.4 请求级空态（按 error.code 分支渲染）（ADR-0011 #10 定稿）
 *
 *  挂在 public layout（route-groups/public-extra.ts）下，**不**渲染工作空间 Topbar/Sidebar：
 *  这是匿名可达页——未登录用户直达 /403 时不应再被工作空间壳包裹。
 */
import { useEffect, useRef } from "react";
import { useSearchParams } from "react-router";
import { Logo } from "../components/Logo";
import { PERMISSION_LABELS } from "../stores/permission";

/** shield-off 96px 内联 SVG（与原型 icon('shieldOff', 96) 同形；原型取自 lucide）。 */
function ShieldOffIcon() {
  return (
    <svg
      width="96"
      height="96"
      viewBox="0 0 24 24"
      fill="none"
      stroke="#d4d4d4"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      data-testid="e403-shield-off"
    >
      <path d="M19.7 14a6.9 6.9 0 0 0 .3-2V5l-8-3-3.16 1.18" />
      <path d="M4.73 4.73 4 5v7c0 6 8 10 8 10a20.29 20.29 0 0 0 5.62-4.38" />
      <path d="M2 2 22 22" />
    </svg>
  );
}

export default function Forbidden() {
  const [params] = useSearchParams();
  // §3.3：URL 参数 required=<permission_key> 带入，渲染为中文名；未登记 key 兜底「访问该页面」
  const rawKey = params.get("required") ?? "";
  const label = (rawKey && PERMISSION_LABELS[rawKey]) || "访问该页面";
  const titleRef = useRef<HTMLHeadingElement | null>(null);
  const backBtnRef = useRef<HTMLAnchorElement | null>(null);

  // §3.6 a11y：主标题 role="alert"；焦点自动移至标题；「返回工作台」为默认焦点按钮
  useEffect(() => {
    const t = setTimeout(() => {
      titleRef.current?.focus();
      backBtnRef.current?.focus();
    }, 0);
    return () => clearTimeout(t);
  }, []);

  return (
    <div className="min-h-screen bg-neutral-50 flex flex-col items-center pt-[14vh] px-4" data-testid="e403-page">
      <div className="flex items-center gap-2 text-neutral-800 mb-6">
        <Logo />
        <span className="text-[15px] font-semibold">RabbitProjects</span>
      </div>

      <div
        className="flex flex-col items-center gap-3.5 px-6 py-10 text-center w-full max-w-[520px] bg-white border border-neutral-200 rounded-xl shadow-sm"
      >
        <ShieldOffIcon />
        <h1
          ref={titleRef}
          id="e403-title"
          role="alert"
          tabIndex={-1}
          className="text-[20px] font-semibold text-neutral-900 outline-none mt-2"
        >
          没有访问该页面的权限
        </h1>
        <div className="text-[14px] text-neutral-500 max-w-[480px] leading-[1.6]" data-testid="e403-sub">
          当前角色不满足『<span className="text-neutral-700 font-medium">{label}</span>』所需的最低角色要求。
        </div>
        <div className="mt-3 flex items-center gap-2.5" data-testid="e403-actions">
          <a
            ref={backBtnRef}
            href="/"
            data-testid="e403-back"
            className="inline-flex h-[36px] items-center px-4 bg-brand-500 text-white rounded-md font-medium hover:bg-brand-600"
          >
            返回工作台
          </a>
          <button
            type="button"
            data-testid="e403-switch"
            onClick={() => { location.href = "/login?ts=" + Date.now(); }}
            className="inline-flex h-[36px] items-center px-4 bg-white border border-neutral-300 text-neutral-700 rounded-md hover:bg-neutral-50"
          >
            切换账号
          </button>
        </div>
        <div className="mt-3.5 text-[13px] text-neutral-500" data-testid="e403-footnote">
          认为这是误判？请联系空间管理员检查你的角色。
        </div>
      </div>
    </div>
  );
}
