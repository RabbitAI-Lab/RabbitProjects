/** 安全设置页（C.11 · AUTH-004 §3.3）。
 *  修改密码卡（旧/新/确认 三组 label+input+👁 + 「确认修改」）
 *  密码强度指示器复用注册页（AUTH-001 §3.4）同一组件（弱/中/强三档 + 规则常驻清单）；
 *  本处采用极简内联实现，避免组件跨页耦合，未来如需彻底复用再抽。
 *  修改成功：表单上方 Alert「密码已修改。其他设备已需要重新登录」（非 toast）；表单清空；焦点移至 Alert
 *  活跃会话区块：灰置 +「即将上线」角标（§3.3） */
import { useEffect, useMemo, useRef, useState } from "react";
import { SettingsShell } from "../components/SettingsShell";
import { ProfileAPI } from "../services/api";

interface PwRule { label: string; ok: boolean }

function evaluatePassword(pw: string): { level: "weak" | "medium" | "strong"; rules: PwRule[] } {
  const rules: PwRule[] = [
    { label: "至少 8 个字符", ok: pw.length >= 8 },
    { label: "包含字母", ok: /[A-Za-z]/.test(pw) },
    { label: "包含数字或符号", ok: /[0-9!@#$%^&*()_+\-=[\]{};':"\\|,.<>/?`~]/.test(pw) },
  ];
  const score = rules.filter((r) => r.ok).length + (pw.length >= 12 ? 1 : 0);
  const level: "weak" | "medium" | "strong" = score >= 4 ? "strong" : score >= 2 ? "medium" : "weak";
  return { level, rules };
}

export default function SettingsSecurityPage() {
  const [show, setShow] = useState<{ old: boolean; np: boolean; cf: boolean }>({ old: false, np: false, cf: false });
  const [oldPwd, setOldPwd] = useState("");
  const [newPwd, setNewPwd] = useState("");
  const [cfPwd, setCfPwd] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [success, setSuccess] = useState(false);
  const [errMsg, setErrMsg] = useState("");
  const alertRef = useRef<HTMLDivElement | null>(null);

  const evalResult = useMemo(() => evaluatePassword(newPwd), [newPwd]);
  const allOk = newPwd.length >= 8 && newPwd === cfPwd && evalResult.level !== "weak";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;
    setErrMsg("");
    if (!oldPwd || !newPwd || !cfPwd) { setErrMsg("三项密码均为必填"); return; }
    if (newPwd !== cfPwd) { setErrMsg("两次新密码输入不一致"); return; }
    setSubmitting(true);
    try {
      await ProfileAPI.changePassword({
        old_password: oldPwd,
        new_password: newPwd,
        new_password_confirm: cfPwd,
      });
      // 成功：表单清空 + Alert「密码已修改」+ 焦点移至 Alert（§3.9）
      setOldPwd(""); setNewPwd(""); setCfPwd("");
      setSuccess(true);
      setTimeout(() => alertRef.current?.focus(), 0);
    } catch (err) {
      setErrMsg(err instanceof Error ? err.message : "密码修改失败");
    } finally {
      setSubmitting(false);
    }
  }

  // 改密成功后聚焦保持一次；用户离开再回来不打扰
  useEffect(() => {
    if (success) {
      const id = setTimeout(() => setSuccess(false), 6000);
      return () => clearTimeout(id);
    }
  }, [success]);

  const meterColor =
    evalResult.level === "strong" ? "bg-emerald-500" : evalResult.level === "medium" ? "bg-amber-500" : "bg-red-500";

  return (
    <SettingsShell active="security">
      {success && (
        <div ref={alertRef} tabIndex={-1} role="alert"
             className="mb-4 flex items-start gap-2 bg-emerald-50 border border-emerald-200 text-emerald-700 rounded-md px-3 py-2 text-[13px]">
          <svg className="mt-0.5 shrink-0" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>
          密码已修改。其他设备已需要重新登录。
        </div>
      )}

      <h2 className="text-[15px] font-semibold text-neutral-900 mb-3">修改密码</h2>
      <form onSubmit={submit} className="space-y-4 max-w-[420px]">
        <Field
          label="当前密码" id="sec-old" value={oldPwd} onChange={setOldPwd}
          visible={show.old} onToggle={() => setShow({ ...show, old: !show.old })}
          autoComplete="current-password"
        />
        <Field
          label="新密码" id="sec-np" value={newPwd} onChange={setNewPwd}
          visible={show.np} onToggle={() => setShow({ ...show, np: !show.np })}
          autoComplete="new-password"
        />
        {/* 强度指示器 + 规则常驻清单 */}
        <div>
          <div className="h-1.5 w-full bg-neutral-100 rounded-full overflow-hidden" role="meter"
               aria-label="密码强度" aria-valuemin={0} aria-valuemax={100}
               aria-valuenow={evalResult.level === "strong" ? 100 : evalResult.level === "medium" ? 60 : 30}>
            <div className={`h-full ${meterColor} transition-all`}
                 style={{ width: evalResult.level === "strong" ? "100%" : evalResult.level === "medium" ? "60%" : "30%" }} />
          </div>
          <ul className="mt-2 space-y-1 text-[12px]">
            {evalResult.rules.map((r) => (
              <li key={r.label} className={`flex items-center gap-1.5 ${r.ok ? "text-emerald-700" : "text-neutral-400"}`}>
                <span className={`w-3.5 h-3.5 inline-flex items-center justify-center rounded-full text-[10px] ${r.ok ? "bg-emerald-500 text-white" : "bg-neutral-200 text-neutral-400"}`}>
                  {r.ok ? "✓" : ""}
                </span>
                {r.label}
              </li>
            ))}
          </ul>
        </div>
        <Field
          label="确认新密码" id="sec-cf" value={cfPwd} onChange={setCfPwd}
          visible={show.cf} onToggle={() => setShow({ ...show, cf: !show.cf })}
          autoComplete="new-password"
        />
        {errMsg && <p className="text-[12px] text-red-600" role="alert">{errMsg}</p>}
        <button type="submit"
                className="h-9 px-4 bg-brand-500 hover:bg-brand-600 disabled:opacity-40 text-white rounded-md text-[13px] font-medium"
                disabled={!allOk || submitting}>
          {submitting ? "提交中…" : "确认修改"}
        </button>
      </form>

      {/* 活跃会话区块：灰置（§3.3） */}
      <h2 className="mt-10 mb-3 text-[15px] font-semibold text-neutral-400">活跃会话</h2>
      <div className="border border-neutral-200 rounded-lg px-4 py-5 bg-neutral-50 text-[13px] text-neutral-400 relative">
        <span className="absolute top-3 right-3 text-[10px] bg-neutral-200 text-neutral-500 px-1.5 py-0.5 rounded">即将上线</span>
        管理各设备的登录状态（P2 交付）。
      </div>
    </SettingsShell>
  );
}

function Field({ label, id, value, onChange, visible, onToggle, autoComplete }:
  { label: string; id: string; value: string; onChange: (v: string) => void;
    visible: boolean; onToggle: () => void; autoComplete: string }) {
  return (
    <div>
      <label className="block text-[13px] font-medium text-neutral-700 mb-1.5" htmlFor={id}>{label}</label>
      <div className="relative">
        <input id={id} type={visible ? "text" : "password"} value={value}
               autoComplete={autoComplete}
               onChange={(e) => onChange(e.target.value)}
               className="w-full h-9 pl-3 pr-9 border border-neutral-300 rounded-md text-[13px] bg-white" />
        <button type="button" aria-label={visible ? "隐藏密码" : "显示密码"} onClick={onToggle}
                className="absolute right-2 top-2 text-neutral-400 hover:text-neutral-700">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            {visible
              ? <><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7Z"/><circle cx="12" cy="12" r="3"/></>
              : <><path d="M3 3l18 18"/><path d="M10.7 6.2A11 11 0 0 1 12 6c6 0 10 6 10 6a17 17 0 0 1-3.3 4M6.6 6.6A17 17 0 0 0 2 12s4 6 10 6a11 11 0 0 0 4-.6"/></>}
          </svg>
        </button>
      </div>
    </div>
  );
}
