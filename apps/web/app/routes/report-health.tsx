/** 项目健康度评分卡（RPT-004 §3.1/§3.2——冻结原型 V-HEALTH/D-DRILL，Sprint-9）。
 *
 *  总评大卡（分档色 + 7 日趋势）+ 四维卡（色档/口径/下钻）+ 下钻抽屉（实时口径）。 */
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router";

import { ReportAPI, unwrap } from "../services/api";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { Topbar } from "../components/Topbar";

type Dim = { value: number; score: number; n: number; drilldown_count: number } | null;
type Health = {
  snapshot_date: string;
  dimensions: Record<string, Dim>;
  total_score: number | null;
  band: "green" | "yellow" | "red" | "insufficient";
  trend_7d: { date: string; score: number | null }[];
};
type DrillRow = { id: string; issue_key: string; name: string; target_date: string | null; state_group: string; assignee?: string | null };

const DIM_META: Record<string, { name: string; unit: (d: NonNullable<Dim>) => string; hint: string }> = {
  progress: { name: "进度偏差", unit: (d) => `完成/时间偏差 ${d.value >= 0 ? "+" : ""}${d.value}`, hint: "参考线：时间进度" },
  overdue: { name: "逾期率", unit: (d) => `逾期率 ${pct(Math.max(d.value, 0))}`, hint: "阈值 ≤10%（超标角标）" },
  effort: { name: "工时偏差", unit: (d) => `spent/est = ${d.value}`, hint: "阈值 0.8~1.2" },
  blocked: { name: "阻塞率", unit: (d) => `阻塞 ${pct(Math.max(d.value, 0))}`, hint: "阈值 ≤5%（含跨项目边）" },
};
const BAND = {
  green: { label: "🟢 健康", cls: "text-emerald-600" },
  yellow: { label: "🟡 关注", cls: "text-amber-600" },
  red: { label: "🔴 风险", cls: "text-red-600" },
  insufficient: { label: "数据不足", cls: "text-neutral-400" },
};
const scoreColor = (s: number) => (s >= 80 ? "text-emerald-600" : s >= 60 ? "text-amber-600" : "text-red-600");
const pct = (n: number) => `${Math.round(n * 100)}%`;

export default function ReportHealthPage() {
  const { workspaceSlug: ws, projectId } = useParams();
  const [data, setData] = useState<Health | null>(null);
  const [drillDim, setDrillDim] = useState<string | null>(null);
  const [drillRows, setDrillRows] = useState<DrillRow[]>([]);
  /** 抽屉打开时刻锚定（Date.now 渲染期禁用——purity；实时口径的「现在」取开抽屉瞬间足够） */
  const [drillNow, setDrillNow] = useState(() => Date.now());

  const load = useCallback(async () => {
    if (!ws || !projectId) return;
    ReportAPI.health(ws, projectId).then((r) => setData(unwrap<Health>(r) ?? null)).catch(() => setData(null));
  }, [ws, projectId]);

  useEffect(() => { load(); }, [load]);

  const openDrill = async (dim: string) => {
    if (!ws || !projectId) return;
    setDrillDim(dim);
    setDrillNow(Date.now());
    try {
      setDrillRows(unwrap<DrillRow[]>(await ReportAPI.healthDrilldown(ws, projectId, dim)) ?? []);
    } catch { setDrillRows([]); }
  };

  const trendMax = Math.max(...(data?.trend_7d ?? []).map((t) => t.score ?? 0), 100);

  return (
    <div className="flex h-screen flex-col" data-sb-scope="page-health">
      <Topbar />
      <div className="flex flex-1 overflow-hidden">
        <ProjectSidebar projectName={projectId ?? ""} identifier="HLT" />
        <main className="flex-1 overflow-auto p-4">
          <div className="mx-auto max-w-[1240px] px-2 py-4">
            <div className="mb-[18px] flex items-center gap-3">
              <div>
                <div className="text-[17px] font-semibold">项目健康度</div>
                <div className="text-[12.5px] text-neutral-400 font-mono">
                  {data?.snapshot_date ?? "—"} 快照（每日 00:10）· 四维等权（BR-01）
                </div>
              </div>
            </div>

            {!data ? (
              <div className="card p-10 text-center text-neutral-400">加载中…</div>
            ) : (
              <>
                {/* 总评大卡 */}
                <div className="card mb-3.5 flex items-center gap-7 p-5" data-sb-scope="health-hero">
                  <div>
                    <div className="text-[12.5px] text-neutral-500">总评</div>
                    <div className="font-mono text-[44px] font-bold leading-none">
                      {data.total_score ?? "—"}
                    </div>
                    <div className={`text-[13px] font-semibold ${BAND[data.band].cls}`}>{BAND[data.band].label}</div>
                  </div>
                  <div className="flex-1">
                    <div className="h-3.5 overflow-hidden rounded bg-neutral-100">
                      <div className={`h-full rounded ${data.band === "green" ? "bg-emerald-500" : data.band === "yellow" ? "bg-amber-500" : "bg-red-500"}`}
                        style={{ width: `${data.total_score ?? 0}%` }} />
                    </div>
                    <div className="mt-2 text-[11.5px] text-neutral-400">
                      0–100 · 四维等权（BR-01）；样本不足维度剔除后重归一（BR-05）
                    </div>
                  </div>
                  <div className="flex items-center gap-3 text-[12.5px] text-neutral-500">
                    近 7 日
                    <div className="flex items-end gap-[3px]">
                      {data.trend_7d.map((t) => (
                        <i key={t.date} title={`${t.date} · ${t.score ?? "—"}`}
                          className={`block w-[9px] rounded-t-sm ${t === data.trend_7d.at(-1) ? "bg-brand-500" : "bg-brand-100"}`}
                          style={{ height: `${Math.max(((t.score ?? 0) / trendMax) * 40, 3)}px` }} />
                      ))}
                    </div>
                    <span className="font-mono">{data.trend_7d.map((t) => t.score ?? "—").join("→")}</span>
                  </div>
                </div>

                {/* 四维卡（O5） */}
                <div className="grid grid-cols-4 gap-3.5" data-sb-scope="health-dims">
                  {Object.entries(DIM_META).map(([key, meta]) => {
                    const d = data.dimensions[key];
                    return (
                      <div key={key} className="card p-4" data-sb-scope={`health-dim-${key}`}>
                        <div className="text-[13px] font-semibold">{meta.name}</div>
                        {d === null ? (
                          <>
                            <div className="mt-1 font-mono text-[26px] font-bold text-neutral-300">—</div>
                            <div className="min-h-[36px] text-[12px] text-neutral-400">样本不足（BR-05）——该维不参与总评</div>
                          </>
                        ) : (
                          (() => {
                            const dd = d!;
                            return (
                              <>
                                <div className={`mt-1 font-mono text-[26px] font-bold ${scoreColor(dd.score)}`}>{dd.score}</div>
                                <div className="min-h-[36px] text-[12px] text-neutral-500">{meta.unit(dd)}<br />n={dd.n}</div>
                                <div className="text-[11.5px] text-neutral-400">{meta.hint}</div>
                                <button className="btn-ghost mt-2 h-[26px] px-2 text-[11.5px]" onClick={() => openDrill(key)}>
                                  下钻 {dd.drilldown_count} ▸
                                </button>
                              </>
                            );
                          })()
                        )}
                      </div>
                    );
                  })}
                </div>
              </>
            )}
          </div>
        </main>
      </div>

      {/* 下钻抽屉（BR-04② 实时口径） */}
      {drillDim && (
        <>
          <div className="fixed inset-0 z-40 bg-black/20" onClick={() => setDrillDim(null)} />
          <div className="fixed top-0 right-0 bottom-0 z-50 flex w-[440px] max-w-[92vw] flex-col bg-white shadow-2xl" data-sb-scope="health-drill-drawer">
            <div className="flex items-center gap-2.5 border-b border-neutral-200 px-4 py-3.5">
              <button className="icon-btn" onClick={() => setDrillDim(null)}>✕</button>
              <b className="text-[14px]">{DIM_META[drillDim]?.name} · 构成任务（{drillRows.length} 实时）</b>
              <span className="ml-auto shrink-0 text-[11.5px] text-neutral-400">实时时点（BR-04②）</span>
            </div>
            <div className="flex-1 overflow-auto">
              <table className="tbl">
                <thead><tr><th>任务</th><th>截止</th><th>逾期</th><th>负责人</th></tr></thead>
                <tbody>
                  {drillRows.map((r) => {
                    const overdueDays = r.target_date && r.state_group !== "completed"
                      ? Math.floor((drillNow - Date.parse(r.target_date)) / 86400000) : 0;
                    return (
                      <tr key={r.id} className="cursor-pointer">
                        <td>
                          <span className="badge-id">{r.issue_key}</span>
                          <div className="mt-0.5 text-[12.5px]">{r.name}</div>
                        </td>
                        <td className="whitespace-nowrap font-mono text-[12px]">{r.target_date?.slice(5) ?? "—"}</td>
                        <td className={`whitespace-nowrap font-mono text-[12px] ${overdueDays > 0 ? "font-semibold text-red-600" : "text-neutral-300"}`}>
                          {overdueDays > 0 ? `${overdueDays} 天` : "—"}
                        </td>
                        <td className="whitespace-nowrap text-neutral-500">{r.assignee ?? "—"}</td>
                      </tr>
                    );
                  })}
                  {drillRows.length === 0 && <tr><td colSpan={4} className="py-8 text-center text-neutral-400">无构成任务</td></tr>}
                </tbody>
              </table>
              <div className="px-4 py-2.5 text-[11.5px] text-neutral-400">…（游标分页 · 点击行跳转任务详情）</div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
