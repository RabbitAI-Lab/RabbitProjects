/** 添加成员弹窗（PROJ-002 §3.3 · C.21 · 520px）。
 *  - 候选集：工作空间成员 − 本项目成员（前端本地差集 + 搜索）
 *  - 多选 checkbox 列表：头像 + 昵称 + 邮箱 +（空间·角色）
 *  - 项目角色：默认「协作者」(CONTRIBUTOR=15)；能力说明 aria-describedby
 *  - 候选为空：空态 + 「去邀请成员」链接
 *  - GUEST 候选警示：选中且角色 > 评论者时，提交按钮旁内联警示
 *  - 提交后逐条结果 Toast */
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "./Toast";
import { ProjectMemberAPI } from "../services/api";
import { ProjectRole, WorkspaceRole, type ProjectRoleValue } from "../stores/permission";
import type { ApiError } from "../services/axios";
import type { WorkspaceMember } from "@rp/types";

const PROJ_ROLE_LABELS: Record<ProjectRoleValue, { n: string; c: string; desc: string }> = {
  [ProjectRole.ADMIN]:       { n: "管理员", c: "#3B82F6", desc: "管理员可管理项目设置与成员" },
  [ProjectRole.CONTRIBUTOR]: { n: "协作者", c: "#10B981", desc: "协作者可创建与编辑任务" },
  [ProjectRole.COMMENTER]:   { n: "评论者", c: "#F59E0B", desc: "评论者只读+评论" },
  [ProjectRole.VIEWER]:      { n: "查看者", c: "#6B7280", desc: "查看者仅只读" },
};

/** 下拉展示顺序（权限由高到低）；用它遍历可保留字面量键类型 */
const PROJ_ROLE_ORDER: readonly ProjectRoleValue[] = [
  ProjectRole.ADMIN, ProjectRole.CONTRIBUTOR, ProjectRole.COMMENTER, ProjectRole.VIEWER,
] as const;

export interface AddProjectMemberModalProps {
  workspaceSlug: string;
  projectId: string;
  projectName: string;
  /** 工作空间成员全集（用于候选差集） */
  workspaceMembers: WorkspaceMember[];
  /** 当前已是项目成员的用户 id 集合（用于差集） */
  existingUserIds: Set<string>;
  onClose: () => void;
  onAdded?: (added: { count: number; skipped: number; failed: number }) => void;
}

export function AddProjectMemberModal({
  workspaceSlug,
  projectId,
  projectName,
  workspaceMembers,
  existingUserIds,
  onClose,
  onAdded,
}: AddProjectMemberModalProps) {
  const [search, setSearch] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [role, setRole] = useState<ProjectRoleValue>(ProjectRole.CONTRIBUTOR);
  const [roleMenuOpen, setRoleMenuOpen] = useState(false);
  const [sending, setSending] = useState(false);
  const roleWrapRef = useRef<HTMLDivElement | null>(null);

  const candidates = useMemo(() => {
    const q = search.trim().toLowerCase();
    return workspaceMembers
      .filter((m) => m.is_active && !existingUserIds.has(m.user.id))
      .filter((m) => !q || (m.user.display_name + m.user.email).toLowerCase().includes(q));
  }, [workspaceMembers, existingUserIds, search]);

  const guestWarn = useMemo(() => {
    if (role <= ProjectRole.COMMENTER) return false;
    return Array.from(picked).some((uid) => {
      const m = workspaceMembers.find((x) => x.user.id === uid);
      return m && m.role === WorkspaceRole.GUEST;
    });
  }, [picked, workspaceMembers, role]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !sending) {
        if (picked.size > 0 && !confirm("放弃当前选择？")) return;
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [sending, picked, onClose]);

  useEffect(() => {
    if (!roleMenuOpen) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest('[data-sb-scope="am-role-menu"]')) return;
      setRoleMenuOpen(false);
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [roleMenuOpen]);

  const close = () => {
    if (sending) return;
    if (picked.size > 0 && !confirm("放弃当前选择？")) return;
    onClose();
  };

  const submit = async () => {
    if (picked.size === 0 || sending) return;
    setSending(true);
    try {
      const r = await ProjectMemberAPI.add(workspaceSlug, projectId, {
        member_ids: Array.from(picked),
        role,
      });
      const data: Array<{ status: string }> = (r as unknown as { data: Array<{ status: string }> }).data ?? [];
      const added = data.filter((d) => d.status === "added").length;
      const skipped = data.filter((d) => d.status === "skipped").length;
      const failed = data.filter((d) => d.status === "failed").length;
      const names = Array.from(picked)
        .map((uid) => workspaceMembers.find((m) => m.user.id === uid)?.user.display_name)
        .filter(Boolean)
        .slice(0, 3);
      const roleName = PROJ_ROLE_LABELS[role].n;
      if (added) toast(`✅ ${names.join("、")}${names.length < picked.size ? " 等" : ""} 已加入（${roleName}）`);
      if (skipped) toast(`⏭ ${skipped} 人已是项目成员，已跳过`, "error");
      if (failed) toast(`✗ ${failed} 人未能加入（请检查其工作空间身份）`, "error");
      onAdded?.({ count: added, skipped, failed });
      onClose();
    } catch (e: unknown) {
      const err = e as ApiError;
      toast(err.message ?? "添加失败", "error");
    } finally {
      setSending(false);
    }
  };

  return (
    <div
      data-sb-scope="modal-root"
      className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-center justify-center p-4 z-50"
      onMouseDown={(e) => {
        if (sending) return;
        const t = e.target as HTMLElement | null;
        if (t?.closest('[data-sb-scope="modal-body"]')) return;
        close();
      }}
    >
      <div
        data-sb-scope="modal-body"
        role="dialog"
        aria-modal="true"
        aria-label="添加成员"
        className="bg-white rounded-xl shadow-lg w-[520px] max-w-full p-6"
      >
        <div className="flex items-center justify-between mb-4">
          <div className="text-base font-semibold">添加成员到「{projectName}」</div>
          <button onClick={close} disabled={sending} aria-label="关闭" className="w-7 h-7 flex items-center justify-center text-neutral-500 hover:text-neutral-900 disabled:opacity-50">✕</button>
        </div>

        <div className="mb-3">
          <div className="flex items-center gap-2 h-9 border border-neutral-300 rounded-md px-2.5 bg-white focus-within:border-brand-500 focus-within:ring-[3px] focus-within:ring-brand-50">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-neutral-400"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
            <input
              type="search"
              placeholder="搜索空间成员…"
              aria-label="搜索空间成员"
              value={search}
              disabled={sending}
              onChange={(e) => setSearch(e.target.value)}
              className="flex-1 h-7 outline-none text-[13px] bg-transparent"
            />
          </div>
        </div>

        <div className="mb-2 max-h-[260px] overflow-y-auto border border-neutral-200 rounded-md">
          {candidates.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2 py-7 text-neutral-500">
              <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="#d4d4d4" strokeWidth="2" aria-hidden="true"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
              <div className="text-[13px] text-neutral-700">空间成员都已在项目中</div>
              <button
                type="button"
                onClick={() => {
                  // 提示用户去团队成员设置页打开邀请弹窗
                  const slug = workspaceSlug;
                  window.location.href = `/${slug}/settings/members?open=invite`;
                }}
                className="text-[12px] text-brand-600 hover:underline"
              >去邀请成员 →</button>
            </div>
          ) : (
            <ul role="listbox" aria-multiselectable="true" aria-label="空间成员候选" className="py-1">
              {candidates.map((m) => {
                const on = picked.has(m.user.id);
                return (
                  <li
                    key={m.user.id}
                    role="option"
                    aria-selected={on}
                    onClick={() => {
                      setPicked((cur) => {
                        const n = new Set(cur);
                        if (n.has(m.user.id)) n.delete(m.user.id);
                        else n.add(m.user.id);
                        return n;
                      });
                    }}
                    className={`flex items-center gap-2 px-2.5 py-1.5 cursor-pointer ${on ? "bg-brand-50" : "hover:bg-neutral-50"}`}
                  >
                    <span
                      aria-hidden="true"
                      className={`w-4 h-4 rounded border flex items-center justify-center text-[10px] ${on ? "bg-brand-500 border-brand-500 text-white" : "border-neutral-300 bg-white"}`}
                    >{on ? "✓" : ""}</span>
                    <Avatar name={m.user.display_name} />
                    <span className="text-[13px] truncate">{m.user.display_name}</span>
                    <span className="ml-auto text-[12px] text-neutral-400 font-mono truncate">
                      {m.user.email}（空间·{WS_ROLE_N[m.role]}{m.role === WorkspaceRole.GUEST ? "·访客" : ""}）
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <p className="text-[12px] text-neutral-500 mb-4">
          已选 <b>{picked.size}</b> 人（已在项目中的成员不再显示）
        </p>

        <div className="mb-4">
          <span className="block text-[13px] font-medium text-neutral-700 mb-1.5">项目角色</span>
          <div className="relative" ref={roleWrapRef} data-sb-scope="am-role-menu">
            <button
              data-sb-scope="am-role-menu"
              onClick={() => setRoleMenuOpen((v) => !v)}
              disabled={sending}
              aria-haspopup="listbox"
              aria-expanded={roleMenuOpen}
              aria-describedby="am-role-desc"
              className="h-9 px-2.5 border border-neutral-300 rounded-md bg-white flex items-center gap-2 hover:bg-neutral-50 disabled:opacity-50"
            >
              <span className="w-2 h-2 rounded-full" style={{ background: PROJ_ROLE_LABELS[role].c }} />
              {PROJ_ROLE_LABELS[role].n} ▾
            </button>
            {roleMenuOpen && (
              <div
                data-sb-scope="am-role-menu"
                role="listbox"
                className="absolute top-[calc(100%+4px)] left-0 w-[260px] bg-white border border-neutral-200 rounded-md shadow-lg py-1 z-10"
              >
                {PROJ_ROLE_ORDER.map((k) => {
                  const v = PROJ_ROLE_LABELS[k];
                  const descId = `am-role-opt-${k}-desc`;
                  return (
                  <button
                    key={k}
                    role="option"
                    aria-selected={role === k}
                    aria-describedby={descId}
                    onClick={() => { setRole(k); setRoleMenuOpen(false); }}
                    className={`w-full px-2.5 py-1.5 flex flex-col items-start gap-0.5 text-left text-[13px] hover:bg-neutral-50 ${role === k ? "bg-brand-50" : ""}`}
                  >
                    <span className="flex items-center gap-2">
                      <span className="w-2 h-2 rounded-full" style={{ background: v.c }} />
                      {v.n}
                    </span>
                    <span id={descId} className="text-[11px] text-neutral-500 leading-tight">{v.desc}</span>
                  </button>
                  );
                })}
              </div>
            )}
          </div>
          <p id="am-role-desc" className="text-[12px] text-neutral-500 mt-1.5">
            {PROJ_ROLE_LABELS[role].desc}
          </p>
        </div>

        {guestWarn && (
          <div
            className="flex items-start gap-2 text-[13px] px-3 py-2 rounded-md bg-amber-50 text-amber-800 mb-4"
            role="note"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0 mt-0.5" aria-hidden="true"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"/><path d="M12 9v4M12 17h.01"/></svg>
            <span>所选访客（GUEST）仅能担任评论者 / 查看者，当前角色将被后端拒绝</span>
          </div>
        )}

        <div className="flex justify-end gap-2.5">
          <button onClick={close} disabled={sending} className="h-[34px] px-3.5 bg-white border border-neutral-300 rounded-md text-neutral-700 hover:bg-neutral-50 disabled:opacity-50">取消</button>
          <button
            onClick={submit}
            disabled={picked.size === 0 || sending}
            className="h-[34px] px-3.5 bg-brand-500 text-white rounded-md hover:bg-brand-600 disabled:opacity-50 inline-flex items-center gap-1.5"
          >
            {sending && (
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="animate-spin" aria-hidden="true">
                <path d="M21 12a9 9 0 1 1-6.22-8.56" />
              </svg>
            )}
            {sending ? "添加中…" : `添加（${picked.size}）`}
          </button>
        </div>
      </div>
    </div>
  );
}

const WS_ROLE_N: Record<number, string> = {
  [WorkspaceRole.OWNER]: "所有者",
  [WorkspaceRole.ADMIN]: "管理员",
  [WorkspaceRole.MEMBER]: "成员",
  [WorkspaceRole.GUEST]: "访客",
};

function Avatar({ name }: { name: string }) {
  return (
    <span
      className="w-5 h-5 rounded bg-neutral-200 text-neutral-700 text-[10px] font-semibold flex items-center justify-center shrink-0"
      aria-hidden="true"
    >{name.slice(0, 1)}</span>
  );
}
