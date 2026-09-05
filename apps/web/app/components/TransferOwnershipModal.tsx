/** 转让所有权弹窗（TEAM-002 §3.4 · 480px · 双重确认）。
 *  - 仅 OWNER 可见（PermissionGate workspace.transfer 包裹）
 *  - 目标下拉：仅列 active WS_ADMIN；空态提示「先将目标成员提升为管理员」
 *  - confirm_name 输入：精确匹配工作空间名才启用「确认转让」
 *  - 默认焦点在「取消」（§3.6 危险操作范式） */
import { useEffect, useRef, useState } from "react";
import type { WorkspaceMember } from "@rp/types";
import { WorkspaceRole } from "../stores/permission";

export interface TransferOwnershipModalProps {
  workspaceName: string;
  /** 候选目标 = active WS_ADMIN 列表（不含自己、不含 OWNER） */
  candidates: WorkspaceMember[];
  loading?: boolean;
  onConfirm: (payload: { new_owner_member_id: string; confirm_name: string }) => void;
  onClose: () => void;
}

export function TransferOwnershipModal({
  workspaceName,
  candidates,
  loading = false,
  onConfirm,
  onClose,
}: TransferOwnershipModalProps) {
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const [picked, setPicked] = useState<WorkspaceMember | null>(null);
  const [confirmName, setConfirmName] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement | null>(null);

  const match = confirmName.trim() === workspaceName && picked !== null;
  const pickLabel = picked
    ? `${picked.user.display_name}（${picked.user.email}）`
    : "🔍 选择当前管理员…";

  useEffect(() => {
    cancelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !loading) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [loading, onClose]);

  // CLAUDE.md 教训 #4 dropdown 纪律：mousedown 阶段 + target.closest 判范围
  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest('[data-sb-scope="transfer-menu"]')) return;
      setMenuOpen(false);
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [menuOpen]);

  return (
    <div
      data-sb-scope="modal-root"
      className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-center justify-center p-4 z-50"
      onMouseDown={(e) => {
        if (loading) return;
        const t = e.target as HTMLElement | null;
        if (t?.closest('[data-sb-scope="modal-body"]')) return;
        onClose();
      }}
    >
      <div
        data-sb-scope="modal-body"
        role="alertdialog"
        aria-modal="true"
        aria-label="转让所有权"
        className="bg-white rounded-xl shadow-lg w-[480px] max-w-full p-6"
      >
        <div className="flex items-center justify-between mb-3">
          <div className="text-base font-semibold">转让所有权</div>
          <button
            onClick={onClose}
            disabled={loading}
            aria-label="关闭"
            className="w-7 h-7 flex items-center justify-center text-neutral-500 hover:text-neutral-900 disabled:opacity-50"
          >✕</button>
        </div>

        <div className="mb-3">
          <span className="block text-[13px] font-medium text-neutral-700 mb-1.5">新所有者</span>
          <div className="relative" ref={wrapperRef} data-sb-scope="transfer-menu">
            <button
              data-sb-scope="transfer-menu"
              onClick={() => setMenuOpen((v) => !v)}
              aria-haspopup="listbox"
              aria-expanded={menuOpen}
              disabled={loading}
              className="w-full h-9 px-2.5 border border-neutral-300 rounded-md bg-white flex items-center justify-between text-left hover:bg-neutral-50 disabled:opacity-50"
            >
              <span className={`truncate ${picked ? "text-neutral-900" : "text-neutral-500"}`}>
                {pickLabel}
              </span>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-neutral-400">
                <path d="m6 9 6 6 6-6" />
              </svg>
            </button>
            {menuOpen && (
              <div
                data-sb-scope="transfer-menu"
                role="listbox"
                className="absolute top-[calc(100%+4px)] left-0 right-0 max-h-[260px] overflow-y-auto bg-white border border-neutral-200 rounded-md shadow-lg py-1 z-10"
              >
                {candidates.length === 0 ? (
                  <div className="px-3 py-2.5 text-[13px] text-neutral-500">
                    先将目标成员提升为管理员
                  </div>
                ) : (
                  candidates.map((c) => (
                    <button
                      key={c.id}
                      role="option"
                      aria-selected={picked?.id === c.id}
                      onClick={() => { setPicked(c); setMenuOpen(false); }}
                      className={`w-full px-2.5 py-1.5 flex items-center gap-2 text-left text-[13px] hover:bg-neutral-50 ${picked?.id === c.id ? "bg-brand-50" : ""}`}
                    >
                      <span className="w-5 h-5 rounded bg-neutral-200 text-neutral-700 text-[10px] font-semibold flex items-center justify-center">
                        {c.user.display_name.slice(0, 1)}
                      </span>
                      <span className="truncate">{c.user.display_name}</span>
                      <span className="ml-auto text-xs text-neutral-400 font-mono truncate">{c.user.email}</span>
                    </button>
                  ))
                )}
              </div>
            )}
          </div>
          <p className="text-[13px] text-neutral-500 mt-1.5">转让后：对方成为所有者，你自动降为管理员。</p>
        </div>

        <div className="mb-4">
          <label htmlFor="tf-name" className="block text-[13px] font-medium text-neutral-700 mb-1.5">
            输入团队名称以确认：<b>{workspaceName}</b>
          </label>
          <input
            id="tf-name"
            autoComplete="off"
            disabled={loading}
            placeholder={workspaceName}
            value={confirmName}
            onChange={(e) => setConfirmName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && match) onConfirm({ new_owner_member_id: picked!.id, confirm_name: confirmName.trim() }); }}
            className="w-full h-9 border border-neutral-300 rounded-md px-2.5 focus:outline-none focus:border-brand-500 focus:ring-[3px] focus:ring-brand-50 disabled:opacity-50"
          />
          {confirmName.length > 0 && confirmName.trim() !== workspaceName && (
            <p className="text-[12px] text-red-600 mt-1.5">输入的团队名称不匹配</p>
          )}
        </div>

        <div className="flex justify-end gap-2.5">
          <button
            ref={cancelRef}
            onClick={onClose}
            disabled={loading}
            className="h-[34px] px-3.5 bg-white border border-neutral-300 rounded-md text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
          >取消</button>
          <button
            onClick={() => picked && onConfirm({ new_owner_member_id: picked.id, confirm_name: confirmName.trim() })}
            disabled={!match || loading}
            className="h-[34px] px-3.5 bg-red-500 text-white rounded-md hover:bg-red-600 disabled:opacity-50"
          >{loading ? "转让中…" : "确认转让"}</button>
        </div>
      </div>
    </div>
  );
}

export function filterTransferCandidates(members: WorkspaceMember[], currentUserId?: string): WorkspaceMember[] {
  return members.filter(
    (m) => m.is_active && m.role === WorkspaceRole.ADMIN && m.user.id !== currentUserId,
  );
}
