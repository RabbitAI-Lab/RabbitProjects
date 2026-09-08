/** 审批留痕审计页（WF-006 §3——C.148，2026-09-09 入口补口轮）。
 *
 *  哈希链完整性徽标（verify）+ 事件列表（approval-audit/，PG 触发器只增）+
 *  CSV 导出（流式代理端点 blob 下载）。 */
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router";

import { AuditAPI, unwrap } from "../services/api";
import { Topbar } from "../components/Topbar";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { toast } from "../components/Toast";

type Ev = Record<string, unknown>;

export default function ProjectAuditPage() {
  const { workspaceSlug: ws, projectId } = useParams();
  const [events, setEvents] = useState<Ev[]>([]);
  const [verify, setVerify] = useState<{ valid: boolean; event_count: number; head_hash?: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [projName, setProjName] = useState("…");
  const [projIdentifier, setProjIdentifier] = useState("");
  const [exporting, setExporting] = useState(false);

  const load = useCallback(async () => {
    if (!ws || !projectId) return;
    try {
      const [e, v] = await Promise.all([
        AuditAPI.events(ws, projectId),
        AuditAPI.verify(ws, projectId).catch(() => null),
      ]);
      setEvents(unwrap<Ev[]>(e) ?? []);
      setVerify((v as unknown as { data: { valid: boolean; event_count: number; head_hash?: string } } | null)?.data ?? null);
    } catch {
      toast("加载审计事件失败", "error");
    } finally {
      setLoading(false);
    }
  }, [ws, projectId]);

  // oxlint-disable-next-line react/set-state-in-effect -- 服务端 loader（同 approvals.tsx 基线）
  useEffect(() => { load(); }, [load]);
  // oxlint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!ws || !projectId) return;
    import("../services/api").then(({ ProjectAPI }) =>
      ProjectAPI.detail(ws!, projectId!).then((r) => {
        const d = (r as unknown as { data: { name?: string; identifier?: string } }).data;
        setProjName(d?.name ?? "…"); setProjIdentifier(d?.identifier ?? "");
      }).catch(() => {}));
  }, [ws, projectId]);

  /** CSV blob 下载（导出确认流 §3——前端仅确认 + 文件名规范）。 */
  async function exportCsv() {
    if (!ws || !projectId) return;
    if (!window.confirm(`导出本项目 ${events.length} 条审批留痕为 CSV？`)) return;
    setExporting(true);
    try {
      const r = await AuditAPI.exportCsv(ws, projectId);
      const blob = new Blob([(r as unknown as { data: Blob }).data], { type: "text/csv" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `approval-audit-${projIdentifier || projectId.slice(0, 8)}-${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      URL.revokeObjectURL(url);
      toast("已导出", "ok");
    } catch {
      toast("导出失败", "error");
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="flex flex-col h-screen bg-neutral-50">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <ProjectSidebar projectName={projName} identifier={projIdentifier} />
        <main className="flex-1 min-w-0 flex flex-col" data-sb-scope="audit-page">
          <header className="h-[56px] border-b border-neutral-200 bg-white px-5 flex items-center gap-3 shrink-0">
            <h1 className="text-[15px] font-semibold">留痕审计</h1>
            <span className="text-[12px] text-neutral-500">{projIdentifier}</span>
            {/* 完整性徽标（§3：绿=链完整；红=断裂须告警） */}
            {verify && (
              <span data-sb-scope="audit-verify-badge"
                className={`px-2 py-0.5 rounded text-xs inline-flex items-center gap-1 ${verify.valid ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700"}`}>
                {verify.valid ? "🔒 链完整" : "⚠ 链断裂"} · {verify.event_count} 事件
              </span>
            )}
            <div className="flex-1" />
            <button type="button" data-sb-scope="audit-export-btn" disabled={exporting}
              onClick={() => void exportCsv()}
              className="h-[34px] px-3.5 inline-flex items-center border border-neutral-300 rounded-md text-[13px] hover:bg-neutral-50 disabled:opacity-50">
              导出 CSV
            </button>
          </header>
          <div className="flex-1 overflow-auto p-5">
            {loading ? <div className="text-sm text-neutral-400">加载中…</div> : (
              <table className="w-full bg-white border border-neutral-200 rounded-lg text-sm" data-sb-scope="audit-events">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-wider text-neutral-400 border-b border-neutral-200">
                    <th className="px-4 py-2.5 font-semibold">时间</th>
                    <th className="px-4 py-2.5 font-semibold">操作者</th>
                    <th className="px-4 py-2.5 font-semibold">动作</th>
                    <th className="px-4 py-2.5 font-semibold">实例</th>
                    <th className="px-4 py-2.5 font-semibold">任务</th>
                    <th className="px-4 py-2.5 font-semibold">prev→hash</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((e, idx) => (
                    <tr key={String(e.id ?? idx)} className="border-b border-neutral-100 last:border-0">
                      <td className="px-4 py-3 text-[12px] text-neutral-500 whitespace-nowrap">{String(e.created_at ?? "").replace("T", " ").slice(0, 19)}</td>
                      <td className="px-4 py-3 text-neutral-700">{String(e.actor_name ?? e.actor_id ?? "—")}</td>
                      <td className="px-4 py-3 text-neutral-600">{String(e.action ?? "—")}</td>
                      <td className="px-4 py-3 font-mono text-[12px] text-neutral-400">{String(e.instance_id ?? "").slice(0, 8)}</td>
                      <td className="px-4 py-3 text-neutral-500">{String(e.issue_key ?? e.issue_name ?? "—")}</td>
                      <td className="px-4 py-3 font-mono text-[11px] text-neutral-400">
                        {String(e.prev_hash ?? "∅").slice(0, 8)} → <span className="text-neutral-600">{String(e.hash ?? "").slice(0, 8)}</span>
                      </td>
                    </tr>
                  ))}
                  {events.length === 0 && (
                    <tr><td colSpan={6} className="px-4 py-12 text-center text-neutral-400">暂无审批留痕（审批动作发生后由 PG 触发器只增写入）</td></tr>
                  )}
                </tbody>
              </table>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
