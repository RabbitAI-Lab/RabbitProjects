/** SSO 认领页（AUTH-009 §3.2 认领页——C.155，Sprint-8 R6）。
 *
 *  IdP 回调判定「邮箱已存在且设过密码」→ 302 本页：密码验证一次即完成
 *  SSO 绑定 + 建会话（sso_txn Cookie 承载事务，10 分钟有效，消费即删）。 */
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { api } from "../services/axios";
import { toast } from "../components/Toast";

export default function SSOClaimPage() {
  const [sp] = useSearchParams();
  const next = sp.get("next") || "/";
  const nav = useNavigate();
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  async function claim() {
    setBusy(true);
    try {
      const r = await api.post("auth/sso/claim/", { password });
      toast("绑定完成，欢迎回来");
      const d = (r as unknown as { data?: { next?: string } }).data;
      nav(d?.next ?? next);
    } catch (e) {
      toast((e as { message?: string })?.message ?? "密码错误或事务已过期（请重新发起 SSO 登录）", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-neutral-50" data-sb-scope="sso-claim">
      <div className="w-[400px] rounded-xl border bg-white p-6 shadow-sm">
        <div className="mb-1 text-lg font-semibold">完成账号关联</div>
        <p className="mb-4 text-[13px] text-neutral-500">
          你的 SSO 身份对应一个已存在的本地账号。输入该账号的密码完成绑定
          （仅需验证一次；此后直接 SSO 登录）。
        </p>
        <input type="password" value={password} autoFocus
               onChange={(e) => setPassword(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && claim()}
               placeholder="本地账号密码" aria-label="本地账号密码"
               className="mb-3 w-full rounded-md border px-3 py-2 text-sm" data-sb-scope="sso-claim-password" />
        <button onClick={claim} disabled={busy || !password}
                className="w-full rounded-md bg-brand-500 py-2 text-sm text-white disabled:opacity-50"
                data-sb-scope="sso-claim-submit">
          {busy ? "验证中…" : "验证并绑定"}
        </button>
        <p className="mt-3 text-center text-xs text-neutral-400">
          事务 10 分钟内有效；超时请从 IdP 重新发起登录
        </p>
      </div>
    </div>
  );
}
