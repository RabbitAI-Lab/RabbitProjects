import { useEffect, useState } from "react";

/** 最小 toast 系统（AUTH-001 §3.5：429/5xx 与全局提示用 toast；字段级错误不用）。
 *  右上角、5s 自动消失、同文案去重（AUTH-002 §3.3 BR-13）。
 *  Sprint-2 扩展（TASK-009 §3.2 / C.58）：kind 增加 warning/ok；可选 action 按钮 +
 *  倒计时可见文本（撤销 10s）——旧调用点（text, kind）签名不变。 */
type ToastKind = "info" | "error" | "warning" | "ok";
type ToastItem = {
  id: number;
  text: string;
  kind: ToastKind;
  action?: { label: string; onAction: () => void };
  ttl?: number;
  countdown?: boolean;
};
export interface ToastOptions {
  action?: { label: string; onAction: () => void };
  /** 存活毫秒数（默认 5000） */
  ttl?: number;
  /** 显示倒计时可见文本「(Ns)」（C.58：10s 内一键恢复 + 倒计时可见） */
  countdown?: boolean;
}
let items: ToastItem[] = [];
let seq = 0;
const subs = new Set<() => void>();
const lastByKind = new Map<string, number>();

function emit() { subs.forEach((f) => f()); }

export function toast(text: string, kind: ToastKind = "info", opt?: ToastOptions) {
  const now = Date.now();
  if (!opt?.action) { // 带 action 的操作型 toast 不去重（撤销窗口逐条独立）
    const key = kind + ":" + text;
    if (now - (lastByKind.get(key) ?? 0) < 4000) return; // 同文案 4s 去重
    lastByKind.set(key, now);
  }
  const item: ToastItem = { id: ++seq, text, kind, ...opt };
  items = [...items, item];
  emit();
  setTimeout(() => { items = items.filter((x) => x.id !== item.id); emit(); }, opt?.ttl ?? 5000);
}

const KIND_CLS: Record<ToastKind, string> = {
  error: "bg-red-500 text-white",
  warning: "bg-amber-600 text-white",
  ok: "bg-emerald-600 text-white",
  info: "bg-neutral-800 text-white",
};
const KIND_ICON: Record<ToastKind, string> = { error: "⚠", warning: "⚠", ok: "✓", info: "ⓘ" };

export function Toaster() {
  const [, force] = useState(0);
  useEffect(() => {
    const f = () => force((n) => n + 1);
    subs.add(f);
    return () => { subs.delete(f); };
  }, []);
  return (
    <div className="fixed top-4 right-4 z-[200] flex flex-col gap-2 w-[360px]" role="status" aria-live="polite">
      {items.map((t) => (
        <ToastItemView key={t.id} item={t} />
      ))}
    </div>
  );
}

function ToastItemView({ item }: { item: ToastItem }) {
  // 倒计时秒数（state：渲染期不读 ref，React Compiler 友好）
  const [remainS, setRemainS] = useState(() => Math.ceil((item.ttl ?? 5000) / 1000));
  useEffect(() => {
    if (!item.countdown) return;
    const t = setInterval(() => setRemainS((n) => Math.max(0, n - 1)), 1000);
    return () => clearInterval(t);
  }, [item.countdown]);
  return (
    <div className={`rounded-lg shadow-md px-3.5 py-2.5 text-[13px] flex items-start gap-2 ${KIND_CLS[item.kind]}`}>
      <span aria-hidden="true">{KIND_ICON[item.kind]}</span>
      <span className="flex-1 min-w-0" data-sb-scope="toast-text">{item.text}</span>
      {item.countdown && <span className="tabular-nums opacity-90" data-sb-scope="toast-count">({remainS}s)</span>}
      {item.action && (
        <button
          data-sb-scope="toast-action"
          className="shrink-0 rounded border border-white/40 bg-white/15 px-2 py-0.5 text-[12px] hover:bg-white/25"
          onClick={() => { items = items.filter((x) => x.id !== item.id); emit(); item.action!.onAction(); }}
        >{item.action.label}</button>
      )}
    </div>
  );
}
