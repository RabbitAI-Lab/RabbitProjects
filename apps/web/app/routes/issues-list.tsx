import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { Sidebar } from "../components/Sidebar";
import { StateBadge } from "../components/StateBadge";
import { IssueAPI } from "../services/api";
import type { Issue } from "@rp/types";

export default function IssuesList() {
  const { workspaceSlug, projectId } = useParams<{ workspaceSlug: string; projectId: string }>();
  const [issues, setIssues] = useState<Issue[]>([]);
  const [quick, setQuick] = useState("");

  async function load() {
    const r = await IssueAPI.list(workspaceSlug!, projectId!, { ordering: "sort_order" });
    setIssues((r as any).data);
  }
  useEffect(() => { load(); }, [workspaceSlug, projectId]);

  return (
    <div className="flex flex-col h-screen">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <Sidebar workspaceSlug={workspaceSlug!} />
        <main className="flex-1 overflow-y-auto p-4">
          <div className="flex items-center gap-1.5 border border-dashed border-neutral-300 h-[38px] px-3 rounded-md mb-3.5 text-neutral-500">
            <span>+</span>
            <input id="tq-input" className="flex-1 bg-transparent outline-none text-[13px]" placeholder="输入任务标题后按回车快速创建…" value={quick} onChange={(e) => setQuick(e.target.value)}
              onKeyDown={async (e) => {
                if (e.key === "Enter" && quick.trim()) {
                  e.preventDefault();
                  await IssueAPI.create(workspaceSlug!, projectId!, { name: quick });
                  setQuick(""); await load();
                  document.getElementById("tq-input")?.focus();
                }
              }} />
            <span className="text-xs">Enter 创建</span>
          </div>
          {issues.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-12 text-neutral-500">
              <div className="text-[15px] font-semibold text-neutral-700">暂无任务</div>
              <div className="text-[13px]">创建第一个任务开始工作</div>
            </div>
          ) : (
            <table className="w-full">
              <thead><tr className="text-[11px] font-semibold text-neutral-400 uppercase tracking-wider text-left">
                <th className="px-3 py-2 border-b border-neutral-200 w-24">编号</th>
                <th className="px-3 py-2 border-b border-neutral-200">标题</th>
                <th className="px-3 py-2 border-b border-neutral-200 w-[120px]">状态</th>
                <th className="px-3 py-2 border-b border-neutral-200 w-[110px]">负责人</th>
                <th className="px-3 py-2 border-b border-neutral-200 w-[130px]">截止时间</th>
              </tr></thead>
              <tbody>
                {issues.map((i) => (
                  <tr key={i.id} className="hover:bg-neutral-50 cursor-pointer">
                    <td className="px-3 py-2.5 border-b border-neutral-100 font-mono text-xs text-neutral-500">{i.issue_key}</td>
                    <td className="px-3 py-2.5 border-b border-neutral-100 text-[13px]"><Link to={`/${workspaceSlug}/projects/${projectId}/board`} className="hover:text-brand-600 hover:underline">{i.name}</Link></td>
                    <td className="px-3 py-2.5 border-b border-neutral-100"><StateBadge group={i.state_group ?? "unstarted"} name={i.state_name ?? "—"} /></td>
                    <td className="px-3 py-2.5 border-b border-neutral-100 text-[13px]">{i.assignee ? i.assignee.name : <span className="text-neutral-400">—</span>}</td>
                    <td className="px-3 py-2.5 border-b border-neutral-100 text-[13px] text-neutral-500 font-mono text-xs">{i.target_date || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </main>
      </div>
    </div>
  );
}
