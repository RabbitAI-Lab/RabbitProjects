/** 邀请成员弹窗（TEAM-002 §3.2 · C.16 · 560px）。
 *  - Tag 输入：回车/逗号/分号/空格/粘贴切分；退格删除末 Tag；非法 Tag 红框
 *  - 计数 n / 20；达 20 后输入禁用 + 提示
 *  - 预设角色下拉：仅「成员 / 管理员」；每项 aria-describedby
 *  - 提交后**替换表单区**为结果视图：四态 added/invited/skipped/failed + SMTP 降级复制链接
 *  - 提交中按钮 loading（loader-2 旋转 + 发送中…）+ Modal 锁定
 *  - 关闭：✕ / Esc / 遮罩；表单有内容时二次确认；提交中不可关 */
import { useEffect, useRef, useState } from "react";
import { toast } from "./Toast";
import type { InviteResult } from "@rp/types";
import { WorkspaceRole } from "../stores/permission";
import { WorkspaceMemberAPI } from "../services/api";

const MAX_TAGS = 20;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export interface InviteMemberModalProps {
  workspaceSlug: string;
  workspaceName: string;
  onClose: () => void;
  onInvited?: () => void; // 提交后通知父级刷新成员 / 待接受列表
}

type Phase = "form" | "result";

export function InviteMemberModal({
  workspaceSlug,
  workspaceName,
  onClose,
  onInvited,
}: InviteMemberModalProps) {
  const [tags, setTags] = useState<string[]>([]);
  const [role, setRole] = useState<number>(WorkspaceRole.MEMBER);
  const [sending, setSending] = useState(false);
  const [phase, setPhase] = useState<Phase>("form");
  const [results, setResults] = useState<InviteResult[]>([]);
  const [inviteLinks, setInviteLinks] = useState<Record<string, string> | null>(null);
  const [roleMenuOpen, setRoleMenuOpen] = useState(false);
  const roleWrapRef = useRef<HTMLDivElement | null>(null);

  // Esc 关闭（发送中锁定）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !sending) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [sending, onClose]);

  // 角色下拉：mousedown 阶段 + target.closest（CLAUDE.md 教训 #4）
  useEffect(() => {
    if (!roleMenuOpen) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest('[data-sb-scope="invite-role-menu"]')) return;
      setRoleMenuOpen(false);
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [roleMenuOpen]);

  const close = () => {
    if (sending) return;
    if (phase === "form" && tags.length > 0) {
      if (!confirm("放弃当前邀请内容？")) return;
    }
    onClose();
  };

  const commitInput = (raw: string) => {
    if (!raw) return;
    const parts = raw.split(/[,;\s\n]+/).map((s) => s.trim()).filter(Boolean);
    if (!parts.length) return;
    setTags((cur) => {
      const next = [...cur];
      for (const p of parts) {
        if (next.length >= MAX_TAGS) break;
        if (!next.includes(p)) next.push(p);
      }
      return next;
    });
  };

  const removeTag = (i: number) => {
    setTags((cur) => cur.filter((_, idx) => idx !== i));
  };

  const onBackspace = () => {
    setTags((cur) => cur.slice(0, -1));
  };

  const submit = async () => {
    if (!tags.length || sending) return;
    setSending(true);
    try {
      const r: any = await WorkspaceMemberAPI.invite(workspaceSlug, { emails: tags, role });
      setResults(((r as any).data ?? []) as InviteResult[]);
      setInviteLinks(((r as any).meta?.invite_links ?? null) as Record<string, string> | null);
      setPhase("result");
      onInvited?.();
    } catch (e: any) {
      toast(e?.message ?? "发送失败", "error");
    } finally {
      setSending(false);
    }
  };

  const again = () => {
    setTags([]);
    setPhase("form");
    setResults([]);
    setInviteLinks(null);
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
        aria-label="邀请成员"
        className="bg-white rounded-xl shadow-lg w-[560px] max-w-full p-6"
      >
        <div className="flex items-center justify-between mb-4">
          <div className="text-base font-semibold">
            邀请成员加入「{workspaceName}」
          </div>
          <button onClick={close} disabled={sending} aria-label="关闭" className="w-7 h-7 flex items-center justify-center text-neutral-500 hover:text-neutral-900 disabled:opacity-50">✕</button>
        </div>

        {phase === "form" ? (
          <>
            <div className="mb-4">
              <span className="block text-[13px] font-medium text-neutral-700 mb-1.5" id="inv-email-lbl">
                邮箱（1-20 个）
              </span>
              <div
                className="flex flex-wrap gap-1.5 items-center min-h-[40px] border border-neutral-300 rounded-md px-2 py-1.5 focus-within:border-brand-500 focus-within:ring-[3px] focus-within:ring-brand-50 bg-white"
                aria-labelledby="inv-email-lbl"
                onClick={(e) => {
                  // 点击容器时把焦点放进 input
                  const inp = (e.currentTarget.querySelector("input") as HTMLInputElement | null);
                  inp?.focus();
                }}
              >
                {tags.map((t, i) => {
                  const valid = EMAIL_RE.test(t);
                  return (
                    <span
                      key={t + i}
                      className={`inline-flex items-center gap-1 px-2 h-7 rounded-md text-[13px] ${valid ? "bg-brand-50 text-brand-700" : "bg-red-50 text-red-700 border border-red-200"}`}
                    >
                      {t}
                      <button
                        type="button"
                        aria-label={`邮箱 ${t}，按退格删除`}
                        onClick={() => removeTag(i)}
                        className="w-4 h-4 flex items-center justify-center rounded hover:bg-black/10"
                      >✕</button>
                    </span>
                  );
                })}
                <input
                  id="invite-email-input"
                  type="text"
                  disabled={tags.length >= MAX_TAGS || sending}
                  placeholder={tags.length >= MAX_TAGS ? "已达上限 20 个" : "继续输入或粘贴…"}
                  aria-label="邮箱输入"
                  autoComplete="off"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === "," || e.key === ";" || e.key === " ") {
                      e.preventDefault();
                      commitInput((e.target as HTMLInputElement).value);
                      (e.target as HTMLInputElement).value = "";
                    } else if (e.key === "Backspace" && !(e.target as HTMLInputElement).value) {
                      e.preventDefault();
                      onBackspace();
                    }
                  }}
                  onPaste={(e) => {
                    e.preventDefault();
                    const txt = e.clipboardData.getData("text");
                    commitInput(txt);
                  }}
                  className="flex-1 min-w-[160px] h-7 outline-none text-[13px] bg-transparent"
                />
              </div>
              <p className="text-[12px] text-neutral-500 mt-1.5">
                支持逗号 / 分号 / 空格 / 换行分隔，粘贴自动切分 ·{" "}
                <span className="font-mono">{tags.length} / 20</span>
              </p>
            </div>

            <div className="mb-5">
              <span className="block text-[13px] font-medium text-neutral-700 mb-1.5">预设角色</span>
              <div className="relative" ref={roleWrapRef} data-sb-scope="invite-role-menu">
                <button
                  data-sb-scope="invite-role-menu"
                  onClick={() => setRoleMenuOpen((v) => !v)}
                  disabled={sending}
                  aria-haspopup="listbox"
                  aria-expanded={roleMenuOpen}
                  aria-describedby="invite-role-desc"
                  className="h-9 px-2.5 border border-neutral-300 rounded-md bg-white flex items-center gap-2 hover:bg-neutral-50 disabled:opacity-50"
                >
                  <RoleBadge value={role} /> ▾
                </button>
                {roleMenuOpen && (
                  <div
                    data-sb-scope="invite-role-menu"
                    role="listbox"
                    className="absolute top-[calc(100%+4px)] left-0 w-[260px] bg-white border border-neutral-200 rounded-md shadow-lg py-1 z-10"
                  >
                    {[
                      { v: WorkspaceRole.MEMBER, n: "成员" },
                      { v: WorkspaceRole.ADMIN, n: "管理员" },
                    ].map((opt) => (
                      <button
                        key={opt.v}
                        role="option"
                        aria-selected={role === opt.v}
                        onClick={() => { setRole(opt.v); setRoleMenuOpen(false); }}
                        className={`w-full px-2.5 py-1.5 flex items-center gap-2 text-left text-[13px] hover:bg-neutral-50 ${role === opt.v ? "bg-brand-50" : ""}`}
                      >
                        <RoleBadge value={opt.v} />
                        <span>{opt.n}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <p id="invite-role-desc" className="text-[12px] text-neutral-500 mt-1.5">
                成员可参与协作；管理员可管理成员与项目
              </p>
            </div>

            <div className="flex justify-end gap-2.5">
              <button onClick={close} disabled={sending} className="h-[34px] px-3.5 bg-white border border-neutral-300 rounded-md text-neutral-700 hover:bg-neutral-50 disabled:opacity-50">取消</button>
              <button
                onClick={submit}
                disabled={!tags.length || sending}
                className="h-[34px] px-3.5 bg-brand-500 text-white rounded-md hover:bg-brand-600 disabled:opacity-50 inline-flex items-center gap-1.5"
              >
                {sending && (
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="animate-spin" aria-hidden="true">
                    <path d="M21 12a9 9 0 1 1-6.22-8.56" />
                  </svg>
                )}
                {sending ? "发送中…" : `发送邀请（${tags.length}）`}
              </button>
            </div>
          </>
        ) : (
          <InviteResultView
            results={results}
            inviteLinks={inviteLinks}
            onAgain={again}
            onClose={onClose}
          />
        )}
      </div>
    </div>
  );
}

function InviteResultView({
  results,
  inviteLinks,
  onAgain,
  onClose,
}: {
  results: InviteResult[];
  inviteLinks: Record<string, string> | null;
  onAgain: () => void;
  onClose: () => void;
}) {
  const summary = results.reduce(
    (acc, r) => { acc[r.status] = (acc[r.status] ?? 0) + 1; return acc; },
    {} as Record<string, number>,
  );

  return (
    <>
      <div className="text-[15px] font-semibold mb-3">邀请结果</div>
      <div className="text-[13px] text-neutral-500 mb-3 flex flex-wrap gap-x-3 gap-y-1">
        {summary.added !== undefined && <span className="text-emerald-700">✓ 直接加入 {summary.added}</span>}
        {summary.invited !== undefined && <span className="text-blue-700">✉ 已发送 {summary.invited}</span>}
        {summary.skipped !== undefined && <span className="text-neutral-500">⏭ 跳过 {summary.skipped}</span>}
        {summary.failed !== undefined && <span className="text-red-700">✗ 失败 {summary.failed}</span>}
      </div>
      <div className="space-y-2 mb-5 max-h-[280px] overflow-y-auto">
        {results.map((r, i) => {
          const link = inviteLinks?.[r.email];
          return (
            <div key={r.email + i} className="flex items-center gap-2 text-[13px] py-1.5">
              <ResultIcon status={r.status} />
              <span className="font-mono text-neutral-800">{r.email}</span>
              <span className={RESULT_COLOR[r.status]}>{RESULT_TEXT[r.status]}</span>
              {r.message && <span className="text-[12px] text-neutral-500">：{r.message}</span>}
              {r.status === "invited" && link && (
                <button
                  type="button"
                  className="ml-auto h-7 px-2 border border-neutral-300 rounded text-[12px] hover:bg-neutral-50"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(link);
                      toast("邀请链接已复制");
                    } catch { toast("复制失败，请手动选中", "error"); }
                  }}
                >复制邀请链接</button>
              )}
            </div>
          );
        })}
      </div>
      <div className="flex justify-end gap-2.5">
        <button onClick={onAgain} className="h-[34px] px-3.5 bg-white border border-neutral-300 rounded-md text-neutral-700 hover:bg-neutral-50">继续邀请</button>
        <button onClick={onClose} className="h-[34px] px-3.5 bg-brand-500 text-white rounded-md hover:bg-brand-600">完成</button>
      </div>
    </>
  );
}

const RESULT_TEXT: Record<InviteResult["status"], string> = {
  added: "已直接加入",
  invited: "邮件已发送，7 天内有效",
  skipped: "已是成员，已跳过",
  failed: "失败",
};
const RESULT_COLOR: Record<InviteResult["status"], string> = {
  added: "text-emerald-700",
  invited: "text-blue-700",
  skipped: "text-neutral-500",
  failed: "text-red-700",
};

function ResultIcon({ status }: { status: InviteResult["status"] }) {
  const cls = "shrink-0";
  if (status === "added") return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#059669" strokeWidth="2" className={cls}><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="M22 4 12 14.01l-3-3"/></svg>;
  if (status === "invited") return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#2563eb" strokeWidth="2" className={cls}><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-10 5L2 7"/></svg>;
  if (status === "skipped") return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#6b7280" strokeWidth="2" className={cls}><polygon points="5 4 15 12 5 20 19 20 19 4 5 4"/></svg>;
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#dc2626" strokeWidth="2" className={cls}><circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/></svg>;
}

function RoleBadge({ value }: { value: number }) {
  const m = value === WorkspaceRole.OWNER
    ? { c: "#8B5CF6", n: "所有者" }
    : value === WorkspaceRole.ADMIN
      ? { c: "#3B82F6", n: "管理员" }
      : { c: "#6B7280", n: "成员" };
  return (
    <span
      className="inline-flex items-center gap-1.5 px-1.5 h-6 rounded text-[12px] font-medium"
      style={{ background: `${m.c}1a`, color: m.c }}
    >
      <span className="w-1.5 h-1.5 rounded-full" style={{ background: m.c }} />
      {m.n}
    </span>
  );
}
