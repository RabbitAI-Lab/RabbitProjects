/** 个人工作台首页（C.35 · RPT-001 §3.2 + 通知摘要卡 C.34）。
 *  - 顶部 4 卡片（待办 / 今日到期 / 已逾期 / 本周完成），链接到对应过滤态的列表
 *  - 7 日完成趋势（折线）
 *  - 通知摘要卡：未读数 + 最近 3 条（点击直达任务详情）
 *  - 「🎉 已处理全部通知」当 unread==0
 *  空态：四卡均 0 时显示「🎉 没有待办，享受片刻宁静」
 *  ADR-0011 #18：侧栏「首页」已点亮为该入口 */
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { Sidebar } from "../components/Sidebar";
import { NotificationAPI, PermissionsAPI, unwrap } from "../services/api";
import { useStores } from "../stores";

interface Stats {
  todo_count: number;
  due_today_count: number;
  overdue_count: number;
  completed_this_week_count: number;
  trend: Array<{ date: string; count: number }>;
}

interface NotifItem {
  id: string;
  title: string;
  data?: { issue_id?: string; issue_key?: string };
  read_at?: string | null;
  created_at: string;
}

export default function WorkbenchPage() {
  const { workspaceSlug = "" } = useParams<{ workspaceSlug: string }>();
  const { session, permission } = useStores();
  const [stats, setStats] = useState<Stats | null>(null);
  const [notifs, setNotifs] = useState<NotifItem[]>([]);
  const [unread, setUnread] = useState(0);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    const id = setTimeout(() => {
      void (async () => {
        try {
          // 三并发拉取：stats / 通知未读 / 通知列表前 3 条
          const me = session.user?.id;
          const [statsR, unreadR, listR] = await Promise.all([
            fetch(`/api/v1/users/me/issues/stats/?workspace=${workspaceSlug}`, { credentials: "include" })
              .then((r) => r.json()),
            NotificationAPI.unreadCount().catch(() => ({})),
            NotificationAPI.list({ unread: true, per_page: 3 }).catch(() => ({ data: [] })),
          ]);
          setStats(statsR?.data ?? null);
          const unreadBody = unwrap<{ count: number } | null>(unreadR);
          setUnread((unreadBody as { count?: number } | null)?.count ?? 0);
          setNotifs(((listR as { data?: NotifItem[] }).data ?? []) as NotifItem[]);
          if (me && permission.snapshot?.projects == null) {
            try { await PermissionsAPI.my(workspaceSlug || undefined); } catch { /* 权限失败不阻塞 */ }
          }
          setLoadState("ready");
        } catch {
          setLoadState("error");
        }
      })();
    }, 0);
    return () => clearTimeout(id);
  }, [workspaceSlug, permission.snapshot, session.user]);

  const allEmpty = stats && stats.todo_count === 0 && stats.due_today_count === 0 && stats.overdue_count === 0;

  return (
    <div className="flex flex-col h-screen">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <Sidebar workspaceSlug={workspaceSlug} />
        <main className="flex-1 min-w-0 overflow-y-auto bg-neutral-50">
          <div className="max-w-[1080px] mx-auto px-6 py-6">
            <h1 className="text-[18px] font-semibold text-neutral-900">我的工作台</h1>
            <p className="mt-1 text-[12px] text-neutral-500">
              {stats ? `本周已完成 ${stats.completed_this_week_count} 项` : "…"}
            </p>

            {loadState === "error" && (
              <p className="mt-6 text-[13px] text-red-600">加载失败，请刷新重试</p>
            )}

            {/* 4 卡片网格 */}
            {stats && (
              <div className="mt-5 grid grid-cols-4 gap-4">
                <StatCard title="待办" value={stats.todo_count} tone="brand"
                          to={`/${workspaceSlug}/projects?workspace=${workspaceSlug}&assignee=me&state_group=unstarted,started`} />
                <StatCard title="今日到期" value={stats.due_today_count} tone="amber"
                          to={`/${workspaceSlug}/projects?assignee=me&state_group=started,unstarted`} />
                <StatCard title="已逾期" value={stats.overdue_count} tone={stats.overdue_count > 0 ? "red" : "neutral"}
                          to={`/${workspaceSlug}/projects?assignee=me&overdue=1`} />
                <StatCard title="本周完成" value={stats.completed_this_week_count} tone="emerald" />
              </div>
            )}

            {/* 空态：四卡均 0 时 */}
            {allEmpty && (
              <div className="mt-8 flex flex-col items-center gap-2 py-10 text-neutral-400">
                <span className="text-[28px]" aria-hidden="true">🎉</span>
                <p>没有待办，享受片刻宁静</p>
              </div>
            )}

            {/* 7 日趋势（折线） */}
            {stats && stats.trend.length === 7 && (
              <section className="mt-8 bg-white border border-neutral-200 rounded-lg p-4">
                <h2 className="text-[13px] font-semibold text-neutral-700 mb-3">近 7 日完成趋势</h2>
                <SparkLine points={stats.trend.map((t) => t.count)} />
              </section>
            )}

            {/* 通知摘要卡（C.34：未读数 + 最近 3 条） */}
            <section className="mt-6 bg-white border border-neutral-200 rounded-lg p-4">
              <div className="flex items-center justify-between mb-2">
                <h2 className="text-[13px] font-semibold text-neutral-700 flex items-center gap-2">
                  通知
                  {unread > 0 && (
                    <span className="px-1.5 h-4 inline-flex items-center bg-red-500 text-white rounded-full text-[10px] font-medium">
                      {unread}
                    </span>
                  )}
                </h2>
                <Link to="#" className="text-[12px] text-brand-600 hover:underline">查看全部</Link>
              </div>
              {unread === 0 ? (
                <p className="py-3 text-[12px] text-neutral-400">🎉 已处理全部通知</p>
              ) : notifs.length === 0 ? (
                <p className="py-3 text-[12px] text-neutral-400">没有未读通知</p>
              ) : (
                <ul>
                  {notifs.map((n) => (
                    <li key={n.id} className="border-t border-neutral-100 first:border-0 py-2.5 flex items-center gap-2">
                      <span className="w-1.5 h-1.5 rounded-full bg-brand-500 shrink-0" aria-hidden="true" />
                      <Link to={n.data?.issue_id
                                ? `/${workspaceSlug}/projects?peekIssue=${n.data.issue_id}`
                                : "#"}
                            className="flex-1 min-w-0 truncate text-[13px] text-neutral-800 hover:text-brand-600">
                        {n.title}
                      </Link>
                      <span className="text-[11px] text-neutral-400 shrink-0">{relTime(n.created_at)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        </main>
      </div>
    </div>
  );
}

function StatCard({ title, value, tone, to }:
  { title: string; value: number; tone: "brand" | "amber" | "red" | "emerald" | "neutral"; to?: string }) {
  const accent = {
    brand: "text-brand-600", amber: "text-amber-600", red: "text-red-600",
    emerald: "text-emerald-600", neutral: "text-neutral-400",
  }[tone];
  const card = (
    <div className="bg-white border border-neutral-200 rounded-lg px-4 py-3">
      <div className="text-[12px] text-neutral-500">{title}</div>
      <div className={`mt-1 text-[24px] font-semibold ${accent}`}>{value}</div>
    </div>
  );
  return to ? <Link to={to} className="block hover:shadow-sm transition">{card}</Link> : card;
}

function SparkLine({ points }: { points: number[] }) {
  if (points.length === 0) return null;
  const max = Math.max(...points, 1);
  const w = 600, h = 80, pad = 4;
  const step = (w - pad * 2) / Math.max(points.length - 1, 1);
  const d = points.map((v, i) => `${i === 0 ? "M" : "L"}${pad + i * step},${h - pad - (v / max) * (h - pad * 2)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-20" aria-label="近 7 日完成趋势">
      <path d={d} stroke="#3f76ff" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      {points.map((v, i) => (
        <circle key={i} cx={pad + i * step} cy={h - pad - (v / max) * (h - pad * 2)} r="3" fill="#3f76ff" />
      ))}
    </svg>
  );
}

function relTime(iso: string): string {
  const t = new Date(iso).getTime();
  const diff = (Date.now() - t) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}
