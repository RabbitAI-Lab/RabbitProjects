/** 累积流图（RPT-003 §3.3——冻结原型 V-CFD/O4，Sprint-9）。五组堆叠 + 区间切换 + 洞察条。 */
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router";

import { ReportAPI, unwrap } from "../services/api";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { Topbar } from "../components/Topbar";

type Cfd = { series: { date: string; counts: Record<string, number> }[] };
const GROUPS = ["backlog", "unstarted", "started", "completed", "cancelled"] as const;
const COLOR: Record<string, string> = {
  backlog: "rgba(156,163,175,.55)", unstarted: "rgba(199,210,254,.7)",
  started: "rgba(59,130,246,.45)", completed: "rgba(16,185,129,.4)", cancelled: "rgba(75,85,99,.8)",
};
const RANGE = [{ n: "近 14 天", d: 14 }, { n: "近 30 天", d: 30 }, { n: "近 90 天", d: 90 }];

const iso = (d: Date) => d.toISOString().slice(0, 10);

export default function ReportCfdPage() {
  const { workspaceSlug: ws, projectId } = useParams();
  const [days, setDays] = useState(30);
  const [data, setData] = useState<Cfd | null>(null);

  const load = useCallback(async () => {
    if (!ws || !projectId) return;
    const to = new Date(), from = new Date(Date.now() - days * 86400000);
    ReportAPI.cfd(ws, projectId, iso(from), iso(to))
      .then((r) => setData(unwrap<Cfd>(r) ?? null))
      .catch(() => setData(null));
  }, [ws, projectId, days]);

  useEffect(() => { load(); }, [load]);

  const W = 640, H = 230, L = 44, B = 204;
  const series = data?.series ?? [];
  const maxV = Math.max(...series.map((s) => GROUPS.reduce((a, g) => a + (s.counts?.[g] ?? 0), 0)), 1);
  const x = (i: number) => L + (series.length <= 1 ? 0 : (i / (series.length - 1)) * (W - L - 20));
  const cumAt = (s: (typeof series)[number], uptoIdx: number) =>
    GROUPS.slice(0, uptoIdx + 1).reduce((a, g) => a + (s.counts?.[g] ?? 0), 0);
  const y = (v: number) => B - (v / maxV) * (B - 10);

  // 各组带状 path（自底向上累积）
  const bandPath = (gi: number) => {
    if (series.length === 0) return "";
    const top = series.map((s, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(cumAt(s, gi)).toFixed(1)}`).join(" ");
    const bottom = [...series].map((_, i) => {
      const s2 = series[series.length - 1 - i]!;
      return `L${x(series.length - 1 - i).toFixed(1)},${y(cumAt(s2, gi - 1)).toFixed(1)}`;
    }).join(" ");
    return `${top} ${bottom} Z`;
  };

  // 洞察：started 段近 7 日增厚
  const startedGrowth = (() => {
    if (series.length < 8) return 0;
    const last = series.at(-1)!.counts?.started ?? 0;
    const prev = series.at(-8)!.counts?.started ?? 0;
    return last - prev;
  })();

  return (
    <div className="flex h-screen flex-col" data-sb-scope="page-cfd">
      <Topbar />
      <div className="flex flex-1 overflow-hidden">
        <ProjectSidebar projectName={projectId ?? ""} identifier="CFD" />
        <main className="flex-1 overflow-auto p-4">
          <div className="mx-auto max-w-[1240px] px-2 py-4">
            <div className="mb-[18px] flex items-center gap-3">
              <div>
                <div className="text-[17px] font-semibold">累积流</div>
                <div className="text-[12.5px] text-neutral-400">每日五组快照（00:10 落盘）· 已结束迭代只读快照直查 · 历史修改零影响（BR-12）</div>
              </div>
              <div className="ml-auto flex rounded-lg bg-neutral-100 p-[3px]">
                {RANGE.map((r) => (
                  <button key={r.d} onClick={() => setDays(r.d)}
                    className={`h-[28px] rounded-md px-3 text-[12.5px] ${days === r.d ? "bg-white font-medium shadow-sm" : "text-neutral-500"}`}>
                    {r.n}
                  </button>
                ))}
              </div>
            </div>
            <div className="card p-5" data-sb-scope="cfd-card">
              {series.length === 0 ? (
                <div className="py-8 text-center text-[12.5px] text-neutral-400">暂无快照——beat 每日 00:10 落盘（次日起有数据）</div>
              ) : (
                <>
                  <svg viewBox={`0 0 ${W} ${H + 20}`} className="w-full max-w-[720px]">
                    <line x1={L} y1={8} x2={L} y2={B} stroke="#e5e5e5" />
                    <line x1={L} y1={B} x2={W - 20} y2={B} stroke="#d4d4d4" />
                    {GROUPS.map((g, gi) => <path key={g} d={bandPath(gi)} fill={COLOR[g]} />)}
                    {[1, 0.75, 0.5, 0.25, 0].map((f) => (
                      <text key={f} x="10" y={y(maxV * f) + 4} className="fill-neutral-400 font-mono" fontSize="10">{Math.round(maxV * f)}</text>
                    ))}
                    {series.map((s, i) => i % Math.ceil(series.length / 4) === 0 && (
                      <text key={s.date} x={x(i)} y={B + 16} textAnchor="middle" className="fill-neutral-400 font-mono" fontSize="10">{s.date.slice(5)}</text>
                    ))}
                    <text x={W - 150} y={y(cumAt(series.at(-1)!, 3)) - 6} className="fill-emerald-700" fontSize="10">completed 持续增厚 = 交付健康</text>
                  </svg>
                  {startedGrowth > 0 && (
                    <div className="mt-3 rounded-lg bg-amber-50 px-3.5 py-2.5 text-[12.5px] text-amber-700" data-sb-scope="cfd-insight">
                      ⚠ 近 7 日 started 段增厚（+{startedGrowth}）——进行中积压，建议控制 WIP
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
