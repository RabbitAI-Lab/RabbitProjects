/** 团队负载热力图（RPT-004 §3.3——冻结原型 V-LOAD/O6，Sprint-9）。
 *
 *  人×周四档热力（<60/60-90/90-100/>100%）+ 周一起始窗口 + CSV 导出（同步流式）。 */
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router";

import { ReportAPI, unwrap } from "../services/api";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { Topbar } from "../components/Topbar";
import { toast } from "../components/Toast";

type Workload = {
  from: string; to: string; capacity_minutes: number;
  matrix: { actor: string; actor_id: string; cells: Record<string, number> }[];
};

/** 本地时区安全格式化（toISOString 是 UTC——东八区 0 点会回退一天，BR-07 周一校验被误触发） */
const fmt = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const monday = (offsetWeeks = 0) => {
  const t = new Date();
  return fmt(new Date(t.getFullYear(), t.getMonth(), t.getDate() - ((t.getDay() + 6) % 7) - offsetWeeks * 7));
};
const addDays = (d: string, n: number) => {
  const x = new Date(Date.parse(d) + n * 86400000);
  return fmt(x);
};

export default function ReportWorkloadPage() {
  const { workspaceSlug: ws, projectId } = useParams();
  const [from, setFrom] = useState(monday(3));
  const [to, setTo] = useState(addDays(monday(0), 27));
  const [data, setData] = useState<Workload | null>(null);

  const load = useCallback(async () => {
    if (!ws || !projectId) return;
    ReportAPI.workload(ws, projectId, from, to)
      .then((r) => setData(unwrap<Workload>(r) ?? null))
      .catch(() => setData(null));
  }, [ws, projectId, from, to]);

  useEffect(() => { load(); }, [load]);

  const weeks = (() => {
    const out: string[] = [];
    for (let d = from; d <= to; d = addDays(d, 7)) out.push(d);
    return out;
  })();

  const cellCls = (m: number, cap: number) => {
    const r = m / cap;
    if (r > 1) return "bg-red-100 text-red-700";
    if (r > 0.9) return "bg-amber-100 text-amber-700";
    if (r >= 0.6) return "bg-blue-100 text-blue-700";
    return "bg-emerald-100 text-emerald-700";
  };

  const exportCsv = async () => {
    if (!ws || !projectId) return;
    try {
      const resp = await ReportAPI.workloadExport(ws, projectId, from, to) as unknown as { data: Blob };
      const url = URL.createObjectURL(resp.data);
      const a = document.createElement("a");
      a.href = url; a.download = `workload-${projectId}.csv`; a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast(e instanceof Error ? e.message : "导出失败（report.export 需项目管理员）", "error");
    }
  };

  const overload = (data?.matrix ?? []).filter((r) => weeks.some((w) => (r.cells[w] ?? 0) > data!.capacity_minutes));

  return (
    <div className="flex h-screen flex-col" data-sb-scope="page-workload">
      <Topbar />
      <div className="flex flex-1 overflow-hidden">
        <ProjectSidebar projectName={projectId ?? ""} identifier="WKL" />
        <main className="flex-1 overflow-auto p-4">
          <div className="mx-auto max-w-[1240px] px-2 py-4">
            <div className="mb-[18px] flex items-center gap-3">
              <div>
                <div className="text-[17px] font-semibold">团队负载</div>
                <div className="text-[12.5px] text-neutral-400">
                  周工时 / 标准工时（WorkLogSummary 快照 · TASK-013 台账同源）· 窗口周一起始 ≤12 周（BR-07）
                </div>
              </div>
              <div className="ml-auto flex items-center gap-2">
                <input type="date" className="input h-[32px] w-[140px]" value={from}
                  onChange={(e) => { const v = e.target.value; if (new Date(v).getDay() === 1) setFrom(v); else toast("起始日必须为周一（BR-07）", "error"); }} />
                <span className="text-neutral-400">~</span>
                <input type="date" className="input h-[32px] w-[140px]" value={to} onChange={(e) => setTo(e.target.value)} />
                <button className="btn-ghost h-[30px] px-3 text-[12px]" onClick={exportCsv} data-sb-scope="workload-export">导出 CSV</button>
              </div>
            </div>

            <div className="card p-4 pb-3.5" data-sb-scope="workload-card">
              {!data || data.matrix.length === 0 ? (
                <div className="py-8 text-center text-[12.5px] text-neutral-400">暂无工时快照数据</div>
              ) : (
                <>
                  <table className="w-full border-separate border-spacing-1" data-sb-scope="workload-matrix">
                    <thead>
                      <tr>
                        <th />
                        {weeks.map((w) => <th key={w} className="pb-1 text-center text-[11.5px] font-medium text-neutral-400">{w.slice(5, 10)}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {data.matrix.map((r) => (
                        <tr key={r.actor_id}>
                          <th className="pr-2 text-left text-[12.5px] font-medium">{r.actor}</th>
                          {weeks.map((w) => {
                            const m = r.cells[w] ?? 0;
                            const ratio = data.capacity_minutes ? m / data.capacity_minutes : 0;
                            return (
                              <td key={w}>
                                <div title={`周工时 ${Math.round(m / 60)}h（明细见 TASK-013 台账）`}
                                  className={`flex h-10 w-16 items-center justify-center rounded-md font-mono text-[12px] ${m ? cellCls(m, data.capacity_minutes) : "bg-neutral-50 text-neutral-300"}`}>
                                  {m ? `${Math.round(ratio * 100)}%` : "—"}
                                </div>
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div className="mt-2.5 flex gap-3.5 px-4 text-[12px] text-neutral-500">
                    <span className="flex items-center gap-1.5"><span className="inline-block h-[9px] w-3.5 rounded-sm bg-emerald-100" />&lt;60%</span>
                    <span className="flex items-center gap-1.5"><span className="inline-block h-[9px] w-3.5 rounded-sm bg-blue-100" />60-90%</span>
                    <span className="flex items-center gap-1.5"><span className="inline-block h-[9px] w-3.5 rounded-sm bg-amber-100" />90-100%</span>
                    <span className="flex items-center gap-1.5"><span className="inline-block h-[9px] w-3.5 rounded-sm bg-red-100" />&gt;100%</span>
                    <span className="ml-auto text-neutral-400">标准工时 {Math.round(data.capacity_minutes / 60)}h/周（项目工时配置）</span>
                  </div>
                  {overload.length > 0 && (
                    <div className="mx-4 mt-3 mb-2 rounded-lg bg-amber-50 px-3 py-2.5 text-[12.5px] text-amber-700" data-sb-scope="workload-warn">
                      ⚠ {overload.map((r) => r.actor).join("、")} 存在超载周（&gt;100%）——建议调整任务分配
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
