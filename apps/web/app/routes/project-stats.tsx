import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { ProjectAPI, ProjectStatsAPI, unwrap } from "../services/api";
import { toast } from "../components/Toast";

/** RPT-002 §3.1/§3.2 项目统计页（C.131）：进度卡 + 工时卡 + 趋势 + 成员任务量表。
 *  数据全部来自两聚合端点（口径单源 BR-01——前端不自行计算）。 */
export default function ProjectStatsPage() {
  const { workspaceSlug: slug, projectId } =
    useParams<{ workspaceSlug: string; projectId: string }>();
  const [days, setDays] = useState<7 | 14 | 30 | 90>(30);
  const [progress, setProgress] = useState<Awaited<ReturnType<typeof ProjectStatsAPI.progress>>["data"] | null>(null);
  const [members, setMembers] = useState<Awaited<ReturnType<typeof ProjectStatsAPI.members>>["data"] | null>(null);
  const [roleFilter, setRoleFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [projName, setProjName] = useState("…");
  const [projIdentifier, setProjIdentifier] = useState("");
  useEffect(() => {
    ProjectAPI.detail(slug!, projectId!).then((r) => {
      const p = (r as unknown as { data: { name: string; identifier: string } }).data;
      setProjName(p?.name ?? "…"); setProjIdentifier(p?.identifier ?? "");
    }).catch(() => { /* 侧栏名缺省 */ });
  }, [slug, projectId]);

  const load = useCallback(() => {
    Promise.all([
      ProjectStatsAPI.progress(slug!, projectId!, { days }),
      ProjectStatsAPI.members(slug!, projectId!, roleFilter ? { role: roleFilter } : {}),
    ]).then(([p, m]) => {
      setProgress(unwrap<typeof progress>(p) ?? null);
      setMembers(unwrap<typeof members>(m) ?? null);
    }).catch(() => toast("统计加载失败", "error")).finally(() => setLoading(false));
  }, [slug, projectId, days, roleFilter]);

  useEffect(load, [load]);

  const GROUP_CN: Record<string, string> = {
    backlog: "待规划", unstarted: "未开始", started: "进行中",
    completed: "已完成", cancelled: "已取消",
  };

  return (
    <div className="h-screen flex flex-col bg-neutral-50">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <ProjectSidebar projectName={projName} identifier={projIdentifier} />
        <main className="flex-1 overflow-auto p-6 space-y-6" data-sb-scope="project-stats">
          <header className="flex items-center justify-between">
            <h1 className="text-[20px] font-semibold text-neutral-900">统计</h1>
            <div className="flex gap-1" role="tablist" aria-label="统计窗口">
              {([7, 14, 30, 90] as const).map((d) => (
                <button key={d} role="tab" aria-selected={days === d}
                  onClick={() => { setLoading(true); setDays(d); }}
                  className={`h-8 px-3 rounded-md text-[13px] border ${
                    days === d ? "bg-brand-500 text-white border-brand-500"
                      : "bg-white text-neutral-700 border-neutral-300 hover:bg-neutral-50"}`}>
                  {d} 天
                </button>
              ))}
            </div>
          </header>

          {loading && <div className="h-40 rounded-xl bg-neutral-100 animate-pulse" />}

          {progress && (
            <>
              <section className="grid grid-cols-1 lg:grid-cols-3 gap-4" data-sb-scope="stats-progress">
                <div className="bg-white rounded-xl border border-neutral-200 p-5">
                  <div className="text-[12px] text-neutral-400 mb-1">完成率（剔已取消）</div>
                  <div className="text-[36px] font-semibold text-neutral-900 leading-none">
                    {progress.completion_rate == null ? "—" : `${Math.round(progress.completion_rate * 100)}%`}
                  </div>
                  <div className="mt-3 space-y-1.5">
                    {Object.entries(progress.state_distribution).map(([g, n]) => (
                      <div key={g} className="flex items-center gap-2 text-[12.5px]">
                        <span className="w-14 text-neutral-500">{GROUP_CN[g] ?? g}</span>
                        <div className="flex-1 h-2 rounded bg-neutral-100 overflow-hidden">
                          <div className="h-full bg-brand-400"
                            style={{ width: `${progress.total ? (n / progress.total) * 100 : 0}%` }} />
                        </div>
                        <span className="w-8 text-right font-mono text-neutral-600">{n}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="bg-white rounded-xl border border-neutral-200 p-5">
                  <div className="text-[12px] text-neutral-400 mb-2">逾期</div>
                  <div className={`text-[36px] font-semibold leading-none ${progress.overdue_count > 0 ? "text-red-600" : "text-neutral-900"}`}>
                    {progress.overdue_count}
                  </div>
                  <div className="mt-3 text-[12.5px] text-neutral-500">
                    总任务 <span className="font-mono">{progress.total}</span>
                  </div>
                </div>
                <div className="bg-white rounded-xl border border-neutral-200 p-5" data-sb-scope="stats-worklog">
                  <div className="text-[12px] text-neutral-400 mb-2">工时（分钟）</div>
                  <dl className="text-[13px] space-y-1">
                    {[["估算（开放）", progress.worklog_summary.estimate_minutes],
                      ["已登记", progress.worklog_summary.logged_minutes],
                      ["剩余", progress.worklog_summary.remaining_minutes],
                      ["超支", progress.worklog_summary.overrun_minutes],
                      ["未估算任务", progress.worklog_summary.unestimated_count]].map(([k, v]) => (
                      <div key={k as string} className="flex justify-between">
                        <dt className="text-neutral-500">{k}</dt>
                        <dd className="font-mono text-neutral-800">{v as number}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              </section>

              <section className="bg-white rounded-xl border border-neutral-200 p-5" data-sb-scope="stats-trend">
                <div className="text-[13px] font-medium text-neutral-700 mb-3">
                  趋势（近 {progress.trend.days} 天 · 创建 vs 完成）
                </div>
                <TrendSpark points={progress.trend.created} points2={progress.trend.completed} />
              </section>
            </>
          )}

          {members && (
            <section className="bg-white rounded-xl border border-neutral-200 overflow-auto" data-sb-scope="stats-members">
              <div className="flex items-center justify-between p-4 border-b border-neutral-100">
                <div className="text-[13px] font-medium text-neutral-700">成员任务量</div>
                <select value={roleFilter} onChange={(e) => { setLoading(true); setRoleFilter(e.target.value); }}
                  aria-label="角色筛选"
                  className="h-8 px-2 border border-neutral-300 rounded-md text-[13px] bg-white">
                  <option value="">全部角色</option>
                  <option value="PROJ_ADMIN">管理员</option>
                  <option value="PROJ_CONTRIBUTOR">贡献者</option>
                  <option value="PROJ_COMMENTER">评论者</option>
                  <option value="PROJ_VIEWER">只读</option>
                </select>
              </div>
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="text-left text-[12px] text-neutral-400 border-b border-neutral-100">
                    {["成员", "开放", "30 天完成", "逾期", "开放估算 (分)", "30 天登记 (分)"].map((h) => (
                      <th key={h} className="px-4 py-2 font-medium">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {members.rows.map((r) => (
                    <tr key={r.member_id} className={`border-b border-neutral-50 ${!r.is_active ? "opacity-50" : ""}`}>
                      <td className="px-4 py-2">
                        {r.display_name}
                        {!r.is_active && <span className="ml-2 text-[11px] text-neutral-400">已禁用</span>}
                      </td>
                      <td className="px-4 py-2 font-mono">{r.open_count}</td>
                      <td className="px-4 py-2 font-mono">{r.done_count_30d}</td>
                      <td className={`px-4 py-2 font-mono ${r.overdue_count > 0 ? "text-red-600" : ""}`}>{r.overdue_count}</td>
                      <td className="px-4 py-2 font-mono">{r.estimate_minutes_open}</td>
                      <td className="px-4 py-2 font-mono">{r.logged_minutes_30d}</td>
                    </tr>
                  ))}
                  <tr className="border-b border-neutral-50 text-neutral-500">
                    <td className="px-4 py-2">未指派</td>
                    <td className="px-4 py-2 font-mono">{members.unassigned.open_count}</td>
                    <td className="px-4 py-2">—</td>
                    <td className="px-4 py-2 font-mono">{members.unassigned.overdue_count}</td>
                    <td className="px-4 py-2 font-mono">{members.unassigned.estimate_minutes_open}</td>
                    <td className="px-4 py-2">—</td>
                  </tr>
                  <tr className="font-medium bg-neutral-50">
                    <td className="px-4 py-2">合计</td>
                    <td className="px-4 py-2 font-mono">{members.totals.open_count}</td>
                    <td className="px-4 py-2 font-mono">{members.totals.done_count_30d}</td>
                    <td className="px-4 py-2 font-mono">{members.totals.overdue_count}</td>
                    <td className="px-4 py-2 font-mono">{members.totals.estimate_minutes_open}</td>
                    <td className="px-4 py-2 font-mono">{members.totals.logged_minutes_30d}</td>
                  </tr>
                </tbody>
              </table>
            </section>
          )}
        </main>
      </div>
    </div>
  );
}

/** 双序列极简折线（无依赖；数值标尺 + 网格由 SVG 直绘）。 */
function TrendSpark({ points, points2 }: {
  points: Array<{ date: string; count: number }>;
  points2: Array<{ date: string; count: number }>;
}) {
  const all = [...points, ...points2];
  const max = Math.max(1, ...all.map((p) => p.count));
  const W = 720, H = 120;
  const path = (arr: Array<{ count: number }>) =>
    arr.map((p, i) => `${i === 0 ? "M" : "L"}${(i / Math.max(1, arr.length - 1)) * W},${H - (p.count / max) * H}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-28" role="img"
      aria-label={`创建最高 ${max} 次`}>
      <path d={path(points)} fill="none" stroke="#3B82F6" strokeWidth="2" />
      <path d={path(points2)} fill="none" stroke="#10B981" strokeWidth="2" />
    </svg>
  );
}
