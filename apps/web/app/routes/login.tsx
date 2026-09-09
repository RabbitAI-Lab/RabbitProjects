import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { Logo } from "../components/Logo";
import { useStores } from "../stores";
import { AuthAPI } from "../services/api";

export default function Login() {
  const { session } = useStores();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [email, setEmail] = useState(params.get("email") ?? "");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [show, setShow] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  /** AUTH-009 §3.2：邮箱路由发现（sso/password 分流，§2.4 前置） */
  const [route, setRoute] = useState<{ mode: "sso" | "password"; sso_login_url?: string } | null>(null);
  const [routeBusy, setRouteBusy] = useState(false);
  const next = params.get("next");

  async function discoverRoute(v?: string) {
    const em = v ?? email;
    if (!em.includes("@")) return;
    setRouteBusy(true);
    try {
      const { SSOAPI } = await import("../services/api");
      const r = await SSOAPI.route(em);
      setRoute((r as { data?: { mode: "sso" | "password"; sso_login_url?: string } }).data ?? null);
    } catch { setRoute(null); }  // 发现失败不打扰——默认密码路径
    finally { setRouteBusy(false); }
  }
  // 账号被禁用（AUTH-002 §3.3）：常驻 Alert，改邮箱前禁用提交
  const disabledAlert = params.get("disabled") === "1";
  const [disabledOk, setDisabledOk] = useState(!disabledAlert);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!disabledOk) return;
    setErr(null); setLoading(true);
    try {
      await session.signIn(email, password, remember);
      nav(next ?? `/${session.currentWsSlug ?? ""}/projects`);
    } catch (e: any) {
      setErr(e?.message ?? "登录失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="w-[420px] max-w-full bg-white border border-neutral-200 rounded-xl shadow-sm px-9 pt-8 pb-6">
      <div className="flex flex-col items-center gap-2.5 mb-[18px]">
        <Logo />
        <h1 className="text-2xl font-semibold text-center">登录 RabbitProjects</h1>
        {next && <p className="text-[13px] text-neutral-500 text-center">登录后将返回你原本访问的页面</p>}
      </div>
      {disabledAlert && !disabledOk && (
        <div className="mb-3.5 flex items-center gap-2 text-[13px] px-3 py-2 rounded-md bg-red-500 text-white" role="alert">
          ⚠ 账号已被禁用，请联系管理员（修改邮箱后可重试）
        </div>
      )}
      {err && <div className={`mb-3.5 flex items-center gap-2 text-[13px] px-3 py-2 rounded-md ${err.includes("已被禁用") ? "bg-red-500 text-white" : "bg-red-50 text-red-700"}`}>{err}</div>}
      {route?.mode === "sso" && (
        <div className="mb-3.5 flex items-center gap-2 text-[13px] px-3 py-2 rounded-md bg-blue-50 text-blue-700 border border-blue-200" role="status" data-sb-scope="login-sso-route">
          🔗 该邮箱属于已启用 SSO 的组织
          <button type="button" onClick={() => { window.location.href = route.sso_login_url ?? "/login"; }}
                  className="ml-auto underline underline-offset-2" data-sb-scope="login-sso-jump">前往 SSO 登录</button>
        </div>
      )}
      {routeBusy && <div className="mb-2 text-xs text-neutral-400">检查登录方式…</div>}
      <form onSubmit={submit}>
        <div className="mb-4">
          <label className="block text-[13px] font-medium text-neutral-700 mb-1.5" htmlFor="email">邮箱</label>
          <input id="email" className="w-full h-9 border border-neutral-300 rounded-md px-2.5 bg-white focus:outline-none focus:border-brand-500 focus:ring-[3px] focus:ring-brand-50" type="email" autoFocus autoComplete="email" placeholder="you@company.com" value={email} onChange={(e) => { setEmail(e.target.value); if (disabledAlert) setDisabledOk(true); setRoute(null); }} onBlur={(e) => discoverRoute(e.target.value)} data-sb-scope="login-email" />
        </div>
        <div className="mb-4">
          <label className="block text-[13px] font-medium text-neutral-700 mb-1.5" htmlFor="pw">密码</label>
          <div className="relative">
            <input id="pw" className="w-full h-9 border border-neutral-300 rounded-md px-2.5 pr-9 bg-white focus:outline-none focus:border-brand-500 focus:ring-[3px] focus:ring-brand-50" type={show ? "text" : "password"} autoComplete="current-password" placeholder="••••••••" value={password} onChange={(e) => setPassword(e.target.value)} />
            <button type="button" className="absolute right-0.5 top-0.5 w-8 h-[34px] flex items-center justify-center text-neutral-500 rounded-md hover:text-neutral-900" onClick={() => setShow(!show)} aria-label="显示密码">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2.06 12.35a1 1 0 0 1 0-.7 10.75 10.75 0 0 1 19.88 0 1 1 0 0 1 0 .7 10.75 10.75 0 0 1-19.88 0"/><circle cx="12" cy="12" r="3"/></svg>
            </button>
          </div>
        </div>
        <div className="mb-4 flex items-center justify-between">
          <label className="flex items-center gap-2 text-[13px] text-neutral-700 cursor-pointer">
            <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} className="accent-brand-500" />记住我（30 天）
          </label>
          <span className="text-neutral-400 cursor-not-allowed" title="即将上线">忘记密码？</span>
        </div>
        <button type="submit" disabled={loading || !disabledOk} className="w-full h-[34px] bg-brand-500 text-white rounded-md font-medium hover:bg-brand-600 disabled:opacity-50">
          {loading ? "登录中…" : "登录"}
        </button>
      </form>
      <button type="button" onClick={async () => {
        try { await session.signIn("zhangsan@rabbit.dev", "Rabbit123"); nav(`/${session.currentWsSlug}/projects`); } catch (e: any) { setErr(e?.message); }
      }} className="w-full h-[34px] mt-2.5 bg-white text-neutral-700 border border-neutral-300 rounded-md font-medium hover:bg-neutral-50">一键进入演示账号（张三）</button>
      <div className="border-t border-neutral-200 mt-5 pt-4 text-[13px] text-neutral-500 text-center">没有账号？ <Link className="text-brand-600" to={`/register${email.trim() ? `?email=${encodeURIComponent(email.trim())}` : ""}`}>立即注册</Link></div>
    </div>
  );
}
