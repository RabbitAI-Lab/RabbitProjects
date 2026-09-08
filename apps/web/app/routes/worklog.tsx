/** 工时周视图与审批（TASK-013 §3.1~§3.3）——我的周批次 + 负责人队列 + 团队台账。
 *
 * C.142 附录 C 表面（Sprint-7 UI parity）。 */
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router";

import { WorklogAPI } from "../services/api";

const STATUS_BADGE: Record<string, string> = {
  draft: "bg-neutral-100 text-neutral-500",
  submitted: "bg-amber-50 text-amber-700",
  approved: "bg-emerald-50 text-emerald-700",
  rejected: "bg-rose-50 text-rose-700",
};
const STATUS_TEXT: Record<string, string> = {
  draft: "待提交", submitted: "待审批", approved: "已通过", rejected: "已驳回",
};

function mondayOf(d: Date): string {
  const m = new Date(d);
  m.setDate(m.getDate() - m.getDay() + 1);
  // 本地分量拼串：toISOString 是 UTC——东八区 0~8 点会取到前一天，把周一算到上周（提交 400）
  const p = (n: number) => String(n).padStart(2, "0");
  return `${m.getFullYear()}-${p(m.getMonth() + 1)}-${p(m.getDate())}`;
}

export default function WorklogPage() {
  const { workspaceSlug: ws, projectId } = useParams();
  const [view, setView] = useState<"mine" | "queue" | "ledger">("mine");
  const [weekStart, setWeekStart] = useState(mondayOf(new Date()));
  const [batches, setBatches] = useState<Array<{ id: string; actor_name: string; week_start: string; status: string; review_note: string }>>([]);
  const [ledger, setLedger] = useState<Array<{ actor_name: string; week_start: string; total_minutes: number; task_count: number; over_8h_days: number; is_frozen: boolean }>>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [rejectNote, setRejectNote] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    if (!ws || !projectId) return;
    try {
      const [q, l] = await Promise.all([
        WorklogAPI.queue(ws, projectId),
        WorklogAPI.ledger(ws, projectId),
      ]);
      setBatches(q.data ?? []);
      setLedger(l.data ?? []);
    } catch { /* 错误态在页内呈现 */ }
  }, [ws, projectId]);

  // oxlint-disable-next-line react/set-state-in-effect -- 同 approvals.tsx：服务端 loader 误报面
  useEffect(() => { load(); /* eslint-disable-line react-hooks/set-state-in-effect -- 同 approvals.tsx */ }, [load]);

  async function submit() {
    if (!ws || !projectId) return;
    setBusy(true);
    try {
      await WorklogAPI.submit(ws, projectId, weekStart);
      setMessage("已提交本周工时");
      await load();
    } catch { setMessage("提交失败：本周可能没有可提交工时"); } finally { setBusy(false); }
  }

  async function act(aid: string, action: "approve" | "reject") {
    if (!ws || !projectId) return;
    if (action === "reject" && !(rejectNote[aid] ?? "").trim()) {
      setMessage("驳回必填意见");
      return;
    }
    setBusy(true);
    try {
      if (action === "approve") await WorklogAPI.approve(ws, projectId, aid);
      else await WorklogAPI.reject(ws, projectId, aid, rejectNote[aid] ?? "");
      setMessage(action === "approve" ? "已通过" : "已驳回");
      await load();
    } catch { setMessage("操作失败"); } finally { setBusy(false); }
  }

  return (
    <div className="flex-1 flex flex-col bg-neutral-50" data-sb-scope="worklog-page">
      <header className="h-12 border-b border-neutral-200 bg-white px-4 flex items-center gap-4">
        <h1 className="text-sm font-semibold">工时</h1>
        <nav className="flex gap-1" data-sb-scope="worklog-tabs">
          {([["mine", "我的周"], ["queue", "审批队列"], ["ledger", "团队台账"]] as const).map(([k, label]) => (
            <button key={k} type="button" data-tab={k} onClick={() => setView(k)}
              className={`px-3 h-7 rounded-md text-sm ${view === k ? "bg-brand-50 text-brand-600 font-medium" : "text-neutral-600 hover:bg-neutral-100"}`}>
              {label}
            </button>
          ))}
        </nav>
      </header>

      {message && <div className="px-4 py-1.5 text-xs text-brand-600 bg-brand-50">{message}</div>}

      {view === "mine" && (
        <section className="p-4" data-sb-scope="worklog-mine">
          <div className="bg-white rounded-lg border border-neutral-200 p-4 space-y-3">
            <div className="flex items-center gap-3">
              <label className="text-sm text-neutral-600">周起始
                <input type="date" value={weekStart}
                  onChange={(e) => setWeekStart(mondayOf(new Date(e.target.value)))}
                  className="ml-2 border border-neutral-200 rounded-md px-2 h-8" />
              </label>
            </div>
            <div className="text-sm text-neutral-500">提交本周工时供负责人审批；通过后本周明细锁定。</div>
            <button type="button" disabled={busy} onClick={() => void submit()}
              className="px-3 h-8 rounded-md bg-brand-600 text-white text-sm hover:bg-brand-700 disabled:opacity-50">
              提交本周
            </button>
          </div>
        </section>
      )}

      {view === "queue" && (
        <section className="p-4" data-sb-scope="worklog-queue">
          {batches.length === 0 ? (
            <div className="text-sm text-neutral-400 py-12 text-center">暂无待审批批次</div>
          ) : (
            <table className="w-full bg-white rounded-lg border border-neutral-200 text-sm">
              <thead>
                <tr className="text-left text-neutral-500 border-b border-neutral-200">
                  <th className="px-3 py-2 font-normal">成员</th>
                  <th className="px-3 py-2 font-normal">周</th>
                  <th className="px-3 py-2 font-normal">状态</th>
                  <th className="px-3 py-2 font-normal">意见</th>
                  <th className="px-3 py-2 font-normal">操作</th>
                </tr>
              </thead>
              <tbody>
                {batches.map((b) => (
                  <tr key={b.id} className="border-b border-neutral-100 last:border-0">
                    <td className="px-3 py-2 text-neutral-800">{b.actor_name}</td>
                    <td className="px-3 py-2 text-neutral-500">{b.week_start}</td>
                    <td className="px-3 py-2">
                      <span className={`px-1.5 py-0.5 rounded text-xs ${STATUS_BADGE[b.status] ?? ""}`}>{STATUS_TEXT[b.status] ?? b.status}</span>
                    </td>
                    <td className="px-3 py-2">
                      {b.status === "submitted" ? (
                        <input type="text" placeholder="驳回意见（驳回时必填）"
                          value={rejectNote[b.id] ?? ""}
                          onChange={(e) => setRejectNote((n) => ({ ...n, [b.id]: e.target.value }))}
                          className="w-full border border-neutral-200 rounded px-2 h-7 text-xs" />
                      ) : <span className="text-xs text-neutral-400">{b.review_note || "—"}</span>}
                    </td>
                    <td className="px-3 py-2">
                      {b.status === "submitted" && (
                        <div className="flex gap-1.5">
                          <button type="button" disabled={busy} onClick={() => void act(b.id, "reject")}
                            className="px-2 h-7 rounded border border-rose-200 text-rose-600 text-xs hover:bg-rose-50">驳回</button>
                          <button type="button" disabled={busy} onClick={() => void act(b.id, "approve")}
                            className="px-2 h-7 rounded bg-brand-600 text-white text-xs hover:bg-brand-700">通过</button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {view === "ledger" && (
        <section className="p-4" data-sb-scope="worklog-ledger">
          {ledger.length === 0 ? (
            <div className="text-sm text-neutral-400 py-12 text-center">暂无台账数据</div>
          ) : (
            <>
            <div className="flex justify-end mb-2">
              {/* TASK-013 §3.3（补口轮）：台账导出——页内数据客户端生成 CSV */}
              <button type="button" data-sb-scope="ledger-export-btn"
                onClick={() => {
                  const head = "成员,周,工时(小时),任务数,超8h天数,冻结";
                  const csv = [head, ...ledger.map((r) =>
                    [r.actor_name, r.week_start, (r.total_minutes / 60).toFixed(1), r.task_count, r.over_8h_days, r.is_frozen ? "是" : "否"].join(","))].join("\n");
                  const url = URL.createObjectURL(new Blob(["\uFEFF" + csv], { type: "text/csv" }));
                  const a = document.createElement("a");
                  a.href = url; a.download = `worklog-ledger-${weekStart}.csv`; a.click();
                  URL.revokeObjectURL(url);
                }}
                className="h-7 px-2.5 rounded border border-neutral-300 text-[12.5px] hover:bg-neutral-50">导出 CSV</button>
            </div>
            <table className="w-full bg-white rounded-lg border border-neutral-200 text-sm">
              <thead>
                <tr className="text-left text-neutral-500 border-b border-neutral-200">
                  <th className="px-3 py-2 font-normal">成员</th>
                  <th className="px-3 py-2 font-normal">周</th>
                  <th className="px-3 py-2 font-normal">工时</th>
                  <th className="px-3 py-2 font-normal">任务数</th>
                  <th className="px-3 py-2 font-normal">超 8h 天</th>
                  <th className="px-3 py-2 font-normal">冻结</th>
                </tr>
              </thead>
              <tbody>
                {ledger.map((row, i) => (
                  <tr key={i} className={`border-b border-neutral-100 last:border-0 ${row.over_8h_days > 0 ? "bg-rose-50/50" : ""}`}>
                    <td className="px-3 py-2 text-neutral-800">{row.actor_name}</td>
                    <td className="px-3 py-2 text-neutral-500">{row.week_start}</td>
                    <td className="px-3 py-2">{(row.total_minutes / 60).toFixed(1)}h</td>
                    <td className="px-3 py-2">{row.task_count}</td>
                    <td className="px-3 py-2">{row.over_8h_days > 0 ? <span className="text-rose-600">†{row.over_8h_days}</span> : "—"}</td>
                    <td className="px-3 py-2">{row.is_frozen ? "🔒" : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            </>
          )}
        </section>
      )}
    </div>
  );
}
