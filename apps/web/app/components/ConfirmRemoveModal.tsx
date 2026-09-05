/** 移除确认弹窗（TEAM-002 §3.4 / §3.6 · 400px）。
 *  通用骨架：被 team-members 与 project-members 两处复用。
 *  - 标题：移除成员 / 移除项目成员
 *  - 列明级联影响；默认焦点在「取消」（危险操作范式）
 *  - role="alertdialog"
 *  - 弹窗打开期间：mousedown 阶段 + target.closest 判范围（CLAUDE.md 教训 #4 dropdown 纪律） */
import { useEffect, useRef } from "react";

export interface ConfirmRemoveModalProps {
  /** 移除对象的人类可读标识（昵称 + 邮箱） */
  subject: { name: string; email?: string };
  /** 工作空间名 / 项目名（用于"移出 XXX"） */
  workspaceName: string;
  /** 受影响项目数（C.18 · TEAM-002：3 个项目；PROJ-002：名下 N 个任务指派） */
  cascadeText: string;
  /** "owner" 角色不可移除（红字提示）；移除项目成员无此场景 */
  isOwnerTarget?: boolean;
  /** 「owner 不可移除」副文案 */
  ownerHint?: string;
  /** 确认中态 */
  loading?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

export function ConfirmRemoveModal({
  subject,
  workspaceName,
  cascadeText,
  isOwnerTarget = false,
  ownerHint = "所有者不可移除，请先转让所有权",
  loading = false,
  onConfirm,
  onClose,
}: ConfirmRemoveModalProps) {
  const cancelRef = useRef<HTMLButtonElement | null>(null);

  // Esc 关闭 + 默认焦点在「取消」（§3.6 危险操作范式）
  useEffect(() => {
    cancelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !loading) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [loading, onClose]);

  return (
    <div
      data-sb-scope="modal-root"
      className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-center justify-center p-4 z-50"
      onMouseDown={(e) => {
        if (loading) return;
        // 点击遮罩关闭（mousedown 阶段 + 排除弹窗本体）
        const t = e.target as HTMLElement | null;
        if (t?.closest('[data-sb-scope="modal-body"]')) return;
        onClose();
      }}
    >
      <div
        data-sb-scope="modal-body"
        role="alertdialog"
        aria-modal="true"
        aria-label="移除成员确认"
        className="bg-white rounded-xl shadow-lg w-[400px] max-w-full p-6"
      >
        <div className="flex items-center justify-between mb-3">
          <div className="text-base font-semibold">移除成员</div>
          <button
            onClick={onClose}
            disabled={loading}
            aria-label="关闭"
            className="w-7 h-7 flex items-center justify-center text-neutral-500 hover:text-neutral-900 disabled:opacity-50"
          >✕</button>
        </div>
        <div className="text-[14px] text-neutral-800 mb-3">
          确定将 <b>{subject.name}</b>
          {subject.email && <>（<span className="font-mono text-[12px] text-neutral-600">{subject.email}</span>）</>}
          移出 <b>{workspaceName}</b>？
        </div>
        <div
          className="flex items-start gap-2 text-[13px] px-3 py-2 rounded-md bg-amber-50 text-amber-800 mb-4"
          role="note"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0 mt-0.5" aria-hidden="true"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"/><path d="M12 9v4M12 17h.01"/></svg>
          <span>{cascadeText}</span>
        </div>
        {isOwnerTarget && (
          <div
            className="flex items-start gap-2 text-[13px] px-3 py-2 rounded-md bg-red-50 text-red-700 mb-4"
            role="alert"
          >
            <span aria-hidden="true">⚠</span>
            <span>{ownerHint}</span>
          </div>
        )}
        <div className="flex justify-end gap-2.5">
          <button
            ref={cancelRef}
            onClick={onClose}
            disabled={loading}
            className="h-[34px] px-3.5 bg-white border border-neutral-300 rounded-md text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
          >取消</button>
          <button
            onClick={onConfirm}
            disabled={loading || isOwnerTarget}
            className="h-[34px] px-3.5 bg-red-500 text-white rounded-md hover:bg-red-600 disabled:opacity-50"
          >{loading ? "移除中…" : "移除"}</button>
        </div>
      </div>
    </div>
  );
}
