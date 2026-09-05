/** Hover Peek 浮层（C.30 · BOARD-002 §3.4）。
 *  - 看板卡片 hover ≥400ms 触发（触摸设备无 hover → 不触发，点按直接开详情，无信息损失）
 *  - 内容：标题 + 描述摘要（description_stripped 前 200 字）+ 标签 + 子任务进度 + 附件数
 *  - Portal 到 body，随锚元素定位；锚滚出视口或 mouseleave 即关
 *  仅桌面 pointer 启用（C.30 触摸分支）。 */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export interface PeekIssue {
  id: string;
  name: string;
  issue_key: string;
  description_stripped?: string | null;
  labels?: Array<{ id: string; name: string; color: string }>;
  sub_issues_count?: number;
  completed_sub_issues_count?: number;
  attachment_count?: number;
}

export function usePeekHover(delayMs = 400) {
  const [peek, setPeek] = useState<{ issue: PeekIssue; anchor: HTMLElement } | null>(null);
  const timerRef = useRef<number | null>(null);

  const bind = (issue: PeekIssue) => ({
    onMouseEnter: (e: React.MouseEvent) => {
      // 触摸设备不触发 peek（C.30）：pointerType 可得时判定，退化环境按 mouse 走
      const pt = (e.nativeEvent as PointerEvent).pointerType;
      if (pt === "touch" || pt === "pen") return;
      const el = e.currentTarget as HTMLElement;
      timerRef.current = window.setTimeout(() => setPeek({ issue, anchor: el }), delayMs);
    },
    onMouseLeave: () => {
      if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null; }
      setPeek(null);
    },
    // 拖拽中不弹（且清理待触发计时）
    onDragStart: () => {
      if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null; }
      setPeek(null);
    },
  });

  useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current); }, []);
  return { peek, bind, close: () => setPeek(null) };
}

export function PeekPopover({ peek, onClose }: { peek: { issue: PeekIssue; anchor: HTMLElement } | null; onClose: () => void }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  useLayoutEffect(() => {
    if (!peek || !ref.current) return;
    const a = peek.anchor.getBoundingClientRect();
    const w = 340, gap = 8;
    // 优先锚右侧；放不下改左侧；上下夹在视口内
    let left = a.right + gap;
    if (left + w > window.innerWidth - 8) left = Math.max(8, a.left - w - gap);
    const top = Math.min(Math.max(8, a.top), window.innerHeight - 240);
    setPos({ top, left });
  }, [peek]);

  useEffect(() => {
    if (!peek) return;
    const onScroll = () => onClose();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("scroll", onScroll, true); window.removeEventListener("keydown", onKey); };
  }, [peek, onClose]);

  if (!peek || !pos) return null;
  const it = peek.issue;
  const subTotal = it.sub_issues_count ?? 0;
  const subDone = it.completed_sub_issues_count ?? 0;
  const pct = subTotal > 0 ? Math.round((subDone / subTotal) * 100) : null;

  return createPortal(
    <div
      ref={ref}
      data-testid="peek-popover"
      role="tooltip"
      onMouseLeave={onClose}
      className="fixed z-40 w-[340px] bg-white border border-neutral-200 rounded-lg shadow-lg p-3.5 text-left"
      style={{ top: pos.top, left: pos.left }}
    >
      <div className="flex items-center gap-2 text-[11px] text-neutral-400">
        <span className="font-mono">{it.issue_key}</span>
      </div>
      <div className="mt-1 text-[14px] font-medium text-neutral-900 line-clamp-2">{it.name}</div>
      {it.description_stripped ? (
        <p className="mt-1.5 text-[12px] text-neutral-500 line-clamp-4 leading-relaxed">
          {it.description_stripped.slice(0, 200)}{(it.description_stripped.length ?? 0) > 200 ? "…" : ""}
        </p>
      ) : (
        <p className="mt-1.5 text-[12px] text-neutral-300">无描述</p>
      )}
      {it.labels && it.labels.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {it.labels.map((l) => (
            <span key={l.id} className="px-1.5 h-[18px] inline-flex items-center rounded text-[10px] text-white" style={{ background: l.color }}>
              {l.name}
            </span>
          ))}
        </div>
      )}
      <div className="mt-2.5 flex items-center gap-3 text-[11px] text-neutral-400">
        {subTotal > 0 && (
          <span className="flex items-center gap-1">
            ☑ {subDone}/{subTotal}
            {pct !== null && (
              <span className="inline-block w-8 h-[3px] bg-neutral-200 rounded overflow-hidden align-middle">
                <span className="block h-full bg-emerald-500" style={{ width: `${pct}%` }} />
              </span>
            )}
          </span>
        )}
        {(it.attachment_count ?? 0) > 0 && <span>📎 {it.attachment_count}</span>}
      </div>
    </div>,
    document.body,
  );
}
