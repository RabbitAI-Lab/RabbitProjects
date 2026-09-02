import { useState } from "react";
import { useNavigate } from "react-router";
import { Logo } from "../components/Logo";
import { useStores } from "../stores";

function pwScore(p: string) {
  const okLen = p.length >= 8, okUp = /[A-Z]/.test(p), okLo = /[a-z]/.test(p), okDi = /\d/.test(p);
  const hard = okLen && okUp && okLo && okDi, strong = p.length >= 12 && /[^A-Za-z0-9]/.test(p) && hard;
  return { okLen, okUp, okLo, okDi, hard, strong, lvl: strong ? "强" : hard ? "中" : p ? "弱" : "" };
}

export default function Register() {
  const { session } = useStores();
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [show, setShow] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const s = pwScore(pw);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null); setLoading(true);
    if (!s.hard) { setErr("密码不满足规则"); setLoading(false); return; }
    if (pw !== pw2) { setErr("两次输入的密码不一致"); setLoading(false); return; }
    try {
      await session.signUp(email, pw);
      nav(`/${session.currentWsSlug}/projects`);
    } catch (e: any) {
      setErr(e?.message ?? "注册失败");
    } finally { setLoading(false); }
  }

  return (
    <div className="w-[420px] max-w-full bg-white border border-neutral-200 rounded-xl shadow-sm px-9 pt-8 pb-6">
      <div className="flex flex-col items-center gap-2.5 mb-[18px]"><Logo /><h1 className="text-2xl font-semibold text-center">创建你的账号</h1></div>
      <div className="flex justify-center gap-1.5 text-[13px] text-neutral-500 mb-3.5">已有账号？<button className="text-brand-600" onClick={() => nav("/login")}>登录</button></div>
      {err && <div className="mb-3.5 px-3 py-2 bg-red-50 text-red-700 rounded-md text-[13px]">{err}</div>}
      <form onSubmit={submit}>
        <div className="mb-4"><label className="block text-[13px] font-medium text-neutral-700 mb-1.5">邮箱</label><input className="w-full h-9 border border-neutral-300 rounded-md px-2.5" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} /></div>
        <div className="mb-4">
          <label className="block text-[13px] font-medium text-neutral-700 mb-1.5">密码</label>
          <div className="relative">
            <input className="w-full h-9 border border-neutral-300 rounded-md px-2.5 pr-9" type={show ? "text" : "password"} autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} />
            <button type="button" className="absolute right-0.5 top-0.5 w-8 h-[34px]" onClick={() => setShow(!show)} aria-label="显示密码">👁</button>
          </div>
          <div className="h-1.5 rounded-sm bg-neutral-100 mt-2 overflow-hidden"><div className="h-full transition-all" style={{ width: s.lvl === "强" ? "100%" : s.lvl === "中" ? "66%" : pw ? "33%" : "0", background: s.lvl === "强" ? "#10b981" : s.lvl === "中" ? "#f59e0b" : "#ef4444" }} /></div>
          <div className="flex justify-between text-xs text-neutral-500 mt-1.5"><span>密码强度</span><span>{s.lvl ? "强度：" + s.lvl : ""}</span></div>
          <div className="grid grid-cols-2 gap-1 text-xs text-neutral-500 mt-2.5">
            <span className={s.okLen ? "text-emerald-600" : ""}>{s.okLen ? "✓" : "○"} 至少 8 位</span>
            <span className={s.okUp ? "text-emerald-600" : ""}>{s.okUp ? "✓" : "○"} 含大写字母</span>
            <span className={s.okLo ? "text-emerald-600" : ""}>{s.okLo ? "✓" : "○"} 含小写字母</span>
            <span className={s.okDi ? "text-emerald-600" : ""}>{s.okDi ? "✓" : "○"} 含数字</span>
          </div>
        </div>
        <div className="mb-4">
          <label className="block text-[13px] font-medium text-neutral-700 mb-1.5">确认密码</label>
          <div className="relative">
            <input className="w-full h-9 border border-neutral-300 rounded-md px-2.5 pr-9" type={show ? "text" : "password"} autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)} />
            <button type="button" className="absolute right-0.5 top-0.5 w-8 h-[34px]" onClick={() => setShow(!show)} aria-label="显示密码">👁</button>
          </div>
        </div>
        <button type="submit" disabled={loading} className="w-full h-[34px] bg-brand-500 text-white rounded-md font-medium hover:bg-brand-600 disabled:opacity-50">{loading ? "创建中…" : "创建账号"}</button>
      </form>
      <div className="border-t border-neutral-200 mt-5 pt-4 text-[13px] text-neutral-500 text-center">已有账号？ <button className="text-brand-600" onClick={() => nav("/login")}>登录</button></div>
    </div>
  );
}
