import { useEffect, useState } from "react";
import { IssueAPI } from "../services/api";
import { StateBadge } from "./StateBadge";
import type { Issue } from "@rp/types";

/** 任务详情抽屉 720px（高保真 TASK-001 §3.3）—— 看板卡片与列表行共用 */
export function IssueDrawer({ issueId, slug, projectId, onClose, onChanged }: {
  issueId: string; slug: string; projectId: string; onClose: () => void; onChanged?: () => void;
}) {
  const [issue, setIssue] = useState<Issue | null>(null);
  useEffect(() => { IssueAPI.detail(slug, projectId, issueId).then((r) => setIssue((r as any).data)); }, [issueId]);
  if (!issue) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/25" onClick={onClose} />
      <aside className="relative w-[720px] max-w-[calc(100vw-64px)] bg-white border-l border-neutral-200 shadow-lg flex flex-col">
        <div className="flex items-center gap-2 px-5 py-3 border-b border-neutral-200">
          <button className="font-mono text-[13px] text-neutral-500 hover:text-brand-600"
            onClick={() => { navigator.clipboard?.writeText(issue.issue_key); }}
            title="点击复制编号">{issue.issue_key}</button>
          <button onClick={onClose} className="ml-auto w-7 h-7 flex items-center justify-center text-neutral-500 hover:text-neutral-900">✕</button>
        </div>
        <div className="flex-1 overflow-y-auto p-5">
          <h2 className="text-lg font-semibold mb-3">{issue.name}</h2>
          <div className="border border-neutral-200 rounded-lg">
            <div className="flex gap-0.5 px-2 py-1.5 border-b border-neutral-200 bg-neutral-50 rounded-t-lg text-[13px] text-neutral-500">B I U ≡ ☰ ⌗ &lt;/&gt; 🔗</div>
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
        </div>
      </aside>
    </div>
  );
}
