import { useEffect, useState } from "react";
import { IssueAPI } from "../services/api";
import { StateBadge } from "./StateBadge";
import { toast } from "./Toast";
import type { Issue } from "@rp/types";

/** 任务详情抽屉 720px（TASK-001 §3.3 全规格）—— 看板卡片与列表行共用。
 *  可编辑标题（点击→输入框，Enter/blur 保存，Esc 取消）；
 *  ⋯ 菜单：复制链接 / 复制编号 / 删除任务（P0 UI 唯一删除入口）；
 *  保存反馈「已保存」2s 淡出；元信息：创建者·创建于·最后更新。 */
export function IssueDrawer({ issueId, slug, projectId, onClose, onChanged }: {
  issueId: string; slug: string; projectId: string; onClose: () => void; onChanged?: () => void;
}) {
  const [issue, setIssue] = useState<Issue | null>(null);
  const [editing, setEditing] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [savedFlash, setSavedFlash] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);

  function refresh() {
    return IssueAPI.detail(slug, projectId, issueId).then((r) => setIssue((r as any).data));
  }
  useEffect(() => { refresh(); }, [issueId, slug, projectId]);

  const saved = () => { setSavedFlash(true); setTimeout(() => setSavedFlash(false), 1800); onChanged?.(); };

  async function saveTitle() {
    setEditing(false);
    if (!issue) return;
    const v = titleDraft.trim();
    if (v && v !== issue.name) {
      await IssueAPI.patch(slug, projectId, issueId, { name: v });
      await refresh(); saved();
    }
  }

  async function del() {
    await IssueAPI.patch(slug, projectId, issueId, {}); // no-op 保险
    setConfirmDel(false); onClose();
    // 通过专用删除端点（与抽屉 ⋯ 菜单一致走 DELETE issue）
    try {
      await IssueAPI.del(slug, projectId, issueId);
      toast(`已删除 ${issue?.issue_key ?? "任务"}`);
    } catch { toast("删除失败", "error"); }
    onChanged?.();
  }

  if (!issue) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/25" onClick={onClose} />
      <aside className="relative w-[720px] max-w-[calc(100vw-64px)] bg-white border-l border-neutral-200 shadow-lg flex flex-col">
        <div className="flex items-center gap-2 px-5 py-3 border-b border-neutral-200">
          <button className="font-mono text-[13px] text-neutral-500 hover:text-brand-600"
            onClick={() => { navigator.clipboard?.writeText(issue.issue_key); toast(`已复制 ${issue.issue_key}`); }}
            title="点击复制编号">{issue.issue_key}</button>
          <div className="ml-auto flex items-center gap-1">
            <div className="relative">
              <button aria-label="更多操作" onClick={() => setMenuOpen(!menuOpen)}
                className="w-7 h-7 flex items-center justify-center text-neutral-500 hover:bg-neutral-100 rounded-md">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="1"/><circle cx="5" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></svg>
              </button>
              {menuOpen && (
                <div className="absolute top-9 right-0 w-[150px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1 z-10">
                  <button className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50" onClick={() => { navigator.clipboard?.writeText(location.origin + location.pathname + `?peekIssue=${issueId}`); toast("已复制链接"); setMenuOpen(false); }}>复制链接</button>
                  <button className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50" onClick={() => { navigator.clipboard?.writeText(issue.issue_key); toast(`已复制 ${issue.issue_key}`); setMenuOpen(false); }}>复制编号</button>
                  <div className="h-px bg-neutral-200 my-1" />
                  <button className="w-full text-left px-3 h-8 text-[13px] text-red-600 hover:bg-red-50" onClick={() => { setMenuOpen(false); setConfirmDel(true); }}>删除任务</button>
                </div>
              )}
            </div>
            <button onClick={onClose} aria-label="关闭" className="w-7 h-7 flex items-center justify-center text-neutral-500 hover:text-neutral-900">✕</button>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto p-5">
          {savedFlash && <div className="float-right text-xs text-emerald-600 flex items-center gap-1">✓ 已保存</div>}
          {editing ? (
            <input autoFocus className="w-full text-lg font-semibold border-b-2 border-brand-500 outline-none bg-transparent py-1"
              value={titleDraft} onChange={(e) => setTitleDraft(e.target.value)}
              onBlur={saveTitle}
              onKeyDown={(e) => { if (e.key === "Enter") saveTitle(); if (e.key === "Escape") setEditing(false); }} />
          ) : (
            <h2 className="text-lg font-semibold py-1 cursor-text hover:bg-neutral-50 rounded -mx-2 px-2" title="点击编辑标题"
              onClick={() => { setTitleDraft(issue.name); setEditing(true); }}>{issue.name}</h2>
          )}
          <div className="border border-neutral-200 rounded-lg mt-3">
            <div className="flex gap-0.5 px-2 py-1.5 border-b border-neutral-200 bg-neutral-50 rounded-t-lg text-[13px] text-neutral-500" aria-hidden>
              {["B", "I", "U", "≡", "☰", "⌗", "</>", "🔗"].map((t, i) => (
                <span key={i} className="min-w-[26px] h-6 inline-flex items-center justify-center rounded px-1"
                  style={t === "B" ? { fontWeight: 700 } : t === "I" ? { fontStyle: "italic" } : t === "U" ? { textDecoration: "underline" } : undefined}>{t}</span>
              ))}
            </div>
            <div className="min-h-[110px] p-2.5 text-[13px] leading-relaxed"
              dangerouslySetInnerHTML={{ __html: issue.description_html || "<p class='text-neutral-400'>添加描述…</p>" }} />
          </div>
          <div className="mt-4 border-t border-neutral-200 pt-4 grid grid-cols-[76px_1fr] gap-x-3.5 gap-y-4 items-center">
            <label className="text-[13px] text-neutral-500">状态</label>
            <div><StateBadge group={issue.state_group ?? "unstarted"} name={issue.state_name ?? "—"} /></div>
            <label className="text-[13px] text-neutral-500">负责人</label>
            <div className="text-[13px]">{issue.assignee?.name ?? "—"}</div>
            <label className="text-[13px] text-neutral-500">截止时间</label>
            <div className="text-[13px]">{issue.target_date || "—"}</div>
          </div>
          <div className="mt-5 border-t border-neutral-200 pt-3 text-xs text-neutral-400 flex gap-2 flex-wrap">
            <span>创建者 {(issue as any).created_by?.name ?? "—"}</span>
            <span>· 创建于 {issue.created_at?.slice(0, 16).replace("T", " ")}</span>
            <span>· 最后更新 {issue.updated_at?.slice(0, 16).replace("T", " ")}</span>
          </div>
        </div>
      </aside>
      {confirmDel && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center p-4 z-[60]">
          <div className="bg-white rounded-xl shadow-lg w-[420px] p-6">
            <div className="text-base font-semibold mb-3">删除任务</div>
            <div className="text-[13px] text-neutral-600 mb-5">确定删除 {issue.issue_key}「{issue.name}」？此操作不可恢复。</div>
            <div className="flex justify-end gap-2.5">
              <button onClick={() => setConfirmDel(false)} className="h-[34px] px-3.5 border border-neutral-300 rounded-md">取消</button>
              <button onClick={del} className="h-[34px] px-3.5 bg-red-500 text-white rounded-md hover:bg-red-600">删除</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
