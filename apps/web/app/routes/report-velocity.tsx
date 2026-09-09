/** 迭代速率页（RPT-003 §3.2——冻结原型 V-VELO，Sprint-9）。柱图完成 vs 计划 + 移动均值线 + 结论卡。 */
import { useEffect, useState } from "react";
import { useParams } from "react-router";

import { ReportAPI, unwrap } from "../services/api";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { Topbar } from "../components/Topbar";

type Velocity = { bars: { cycle: string; completed: number; planned: number }[]; moving_avg: number[]; warnings: string[] };

export default function ReportVelocityPage() {
  const { workspaceSlug: ws, projectId } = useParams();
  const [data, setData] = useState<Velocity | null>(null);

  useEffect(() => {
    if (!ws || !projectId) return;
    ReportAPI.velocity(ws, projectId).then((r) => setData(unwrap<Velocity>(r) ?? null)).catch(() => setData(null));
  }, [ws, projectId]);

  const W = 640, H = 210, L = 46, B = 160;
  const maxV = Math.max(...(data?.bars ?? []).flatMap((b) => [b.completed, b.planned]), 1);
  const band = (data?.bars.length ?? 0) * 100;
  const cx = (i: number) => L + i * band + band / 2;
  const y = (v: number) => B - (v / maxV) * (B - 16);

  return (
    <div className="flex h-screen flex-col" data-sb-scope="page-velocity">
      <Topbar />
      <div className="flex flex-1 overflow-hidden">
        <ProjectSidebar projectName={projectId ?? ""} identifier="VEL" />
        <main className="flex-1 overflow-auto p-4">
          <div className="mx-auto max-w-[1240px] px-2 py-4">
            <div className="mb-[18px]">
              <div className="text-[17px] font-semibold">迭代速率</div>
              <div className="text-[12.5px] text-neutral-400">近 5 个已完成迭代 · 完成 vs 计划 · 移动均值(5) · planned = 首日快照 scope_total（BR-10）</div>
            </div>
            <div className="card p-5" data-sb-scope="velocity-card">
              {!data || data.bars.length === 0 ? (
                <div className="py-8 text-center text-[12.5px] text-neutral-400">暂无已完成迭代——结束首个迭代后生成</div>
              ) : (
                <>
                  <svg viewBox={`0 0 ${W} ${H}`} className="w-full max-w-[720px]">
                    <line x1={L} y1={B} x2={W - 20} y2={B} stroke="#d4d4d4" />
                    {[1, 0.75, 0.5, 0.25].map((f) => (
                      <text key={f} x="10" y={y(maxV * f) + 4} className="fill-neutral-400 font-mono" fontSize="10">{Math.round(maxV * f)}</text>
                    ))}
                    {/* 移动均值虚线（有值区段） */}
                    {data.moving_avg.map((m, i) => m != null && i > 0 && data.moving_avg[i - 1] != null && (
                      <line key={`m${i}`} x1={cx(i - 1)} y1={y(data.moving_avg[i - 1]!)} x2={cx(i)} y2={y(m)} stroke="#f59e0b" strokeDasharray="5 5" strokeWidth="1.8" />
                    ))}
                    {data.bars.map((b, i) => (
                      <g key={b.cycle}>
                        <rect x={cx(i) - 16} y={y(b.completed)} width="26" height={B - y(b.completed)} rx="3" fill="#3f76ff" />
                        <rect x={cx(i) + 11} y={y(b.planned)} width="11" height={B - y(b.planned)} rx="3" fill="#dbe7fe" />
                        <text x={cx(i)} y={B + 16} textAnchor="middle" className="fill-neutral-400 font-mono" fontSize="10">{b.cycle}</text>
                      </g>
                    ))}
                  </svg>
                  <div className="mt-2 flex gap-4 text-[12px] text-neutral-500">
                    <span className="flex items-center gap-1.5"><span className="inline-block h-[9px] w-3.5 rounded-sm bg-brand-500" />完成</span>
                    <span className="flex items-center gap-1.5"><span className="inline-block h-[9px] w-3.5 rounded-sm bg-blue-100" />计划（首日快照）</span>
                    <span className="flex items-center gap-1.5"><span className="inline-block h-0.5 w-3.5 bg-amber-500" />移动均值(5)</span>
                  </div>
                  {data.warnings.length === 0 ? (
                    <div className="mt-3.5 rounded-lg bg-blue-50 px-3.5 py-3 text-[13px] text-blue-700" data-sb-scope="velocity-conclusion">
                      结论卡：团队稳定产能 ≈ {Math.round(data.moving_avg.at(-1) ?? 0)} /双周 · 建议下一迭代计划不超过该值
                    </div>
                  ) : (
                    <div className="mt-3.5 rounded-lg bg-amber-50 px-3.5 py-3 text-[12.5px] text-amber-700">
                      {data.warnings.map((w) => <div key={w}>⚠ {w.includes("first-day") ? "存在迭代首日快照缺失，planned 按零值降级（beat 漏跑兜底已触发补跑）" : w}</div>)}
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
