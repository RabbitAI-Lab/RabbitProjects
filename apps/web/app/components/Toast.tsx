import { useEffect, useState } from "react";

/** 最小 toast 系统（AUTH-001 §3.5：429/5xx 与全局提示用 toast；字段级错误不用）。
 *  右上角、5s 自动消失、同文案去重（AUTH-002 §3.3 BR-13）。 */
type ToastItem = { id: number; text: string; kind: "info" | "error" };
let items: ToastItem[] = [];
let seq = 0;
const subs = new Set<() => void>();
const lastByKind = new Map<string, number>();

function emit() { subs.forEach((f) => f()); }

export function toast(text: string, kind: "info" | "error" = "info") {
  const now = Date.now();
  const key = kind + ":" + text;
  if (now - (lastByKind.get(key) ?? 0) < 4000) return; // 同文案 4s 去重
  lastByKind.set(key, now);
  const item = { id: ++seq, text, kind };
  items = [...items, item];
  emit();
  setTimeout(() => { items = items.filter((x) => x.id !== item.id); emit(); }, 5000);
}

export function Toaster() {
  const [, force] = useState(0);
  useEffect(() => {
    const f = () => force((n) => n + 1);
    subs.add(f);
    return () => { subs.delete(f); };
  }, []);
  return (
    <div className="fixed top-4 right-4 z-[200] flex flex-col gap-2 w-[320px]" role="status" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={`rounded-lg shadow-md px-3.5 py-2.5 text-[13px] flex items-start gap-2 ${t.kind === "error" ? "bg-red-500 text-white" : "bg-neutral-800 text-white"}`}>
          {t.kind === "error" ? "⚠" : "ⓘ"}{t.text}
        </div>
      ))}
    </div>
  );
}
