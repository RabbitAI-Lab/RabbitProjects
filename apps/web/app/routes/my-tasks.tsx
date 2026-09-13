/**
 * 我的任务（RPT-001 §3——个人工作台；后端 Sprint-1 已交付，本页补齐前端）。
 *
 * 四统计卡（待办 / 今日到期 / 已逾期 / 本周完成）+ 7 日完成趋势条 +
 * 跨项目「指派给我」待办列表（状态语义组筛选 + 截止排序）。
 * C.35 附录表面：卡片点击即筛选、行点击跳源项目任务抽屉。
 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router";

import { Topbar } from "../components/Topbar";
import { Sidebar } from "../components/Sidebar";
import { toast } from "../components/Toast";
import { api } from "../services/axios";

interface Stats {
  todo_count: number;
  due_today_count: number;
  overdue_count: number;
  completed_this_week_count: number;
  trend: Array<{ day: string; count: number }>;
}
interface IssueRow {
  id: string;
  name: string;
  issue_key?: string;
  project_id?: string;
  project?: string;
  state_id?: string;
  state?: string;
  state_group?: string;
  priority?: string;
  target_date?: string | null;
  completed_at?: string | null;
}

const GROUP_LABEL: Record<string, string> = {
  unstarted: "未开始", started: "进行中", completed: "已完成",
  cancelled: "已取消",
};
const GROUPS = ["unstarted", "started", "completed", "cancelled"] as const;
type Group = (typeof GROUPS)[number];

/** 卡片 ↔ 列表筛选的联动键（todo=开放两类合计） */
type Filter = "all" | "todo" | "due_today" | "overdue" | Group;

export default function MyTasksPage() {
  const { workspaceSlug: ws } = useParams<{ workspaceSlug: string }>();
  const nav = useNavigate();
  const [stats, setStats] = useState<Stats | null>(null);
  const [rows, setRows] = useState<IssueRow[]>([]);
  const [total, setTotal] = useState(0);
  const [filter, setFilter] = useState<Filter>("all");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!ws) return;
    setLoading(true);
    try {
      const [s, l] = await Promise.all([
        api.get<Stats>("users/me/issues/stats/", { params: { workspace: ws } }),
        api.get<IssueRow[]>("users/me/issues/", {
          params: { workspace: ws, per_page: 50, ordering: "target_date" },
        }),
      ]);
      setStats(s.data ?? null);
      setRows(l.data ?? []);
      setTotal((l as unknown as { meta?: { total_count?: number } })
        .meta?.total_count ?? (l.data ?? []).length);
    } catch (e) {
      toast(e instanceof Error ? e.message : "加载失败", "error");
    } finally {
      setLoading(false);
    }
  }, [ws]);

  // oxlint-disable-next-line react/set-state-in-effect -- 服务端 loader（同 audit-logs.tsx 基线）
  useEffect(() => { load(); }, [load]);

  /** 筛选语义与后端口径对齐：todo=unstarted+started；due/overdue 仅开放类 */
  const visible = (rows ?? []).filter((r) => {
    const group = r.state_group ?? "unstarted";
    const open = group === "unstarted" || group === "started";
    const today = new Date().toISOString().slice(0, 10);
    switch (filter) {
      case "all": return true;
      case "todo": return open;
      case "due_today": return open && r.target_date === today;
      case "overdue": return open && !!r.target_date && r.target_date < today;
      case "completed": case "cancelled":
      case "unstarted": case "started":
        return group === filter;
      default: return true;
    }
  });

  const cards: Array<{ key: Filter; label: string; value: number; tone: string }> = [
    { key: "todo", label: "待办", value: stats?.todo_count ?? 0,
      tone: "text-brand-600" },
    { key: "due_today", label: "今日到期", value: stats?.due_today_count ?? 0,
      tone: "text-amber-600" },
    { key: "overdue", label: "已逾期", value: stats?.overdue_count ?? 0,
      tone: "text-rose-600" },
    { key: "completed", label: "本周完成", value: stats?.completed_this_week_count ?? 0,
      tone: "text-emerald-600" },
  ];
  const trendMax = Math.max(1, ...(stats?.trend ?? []).map((t) => t.count));

  return (
    <div className="flex h-screen flex-col">
      <Topbar />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar workspaceSlug={ws ?? ""} />
        <main className="flex-1 overflow-auto p-4" data-sb-scope="my-tasks">
          <div className="mx-auto max-w-[1080px] px-2 py-4">
            <div className="mb-4 flex items-center gap-3">
              <div>
                <div className="text-[17px] font-semibold">我的任务</div>
                <div className="text-[12.5px] text-neutral-400">
                  跨项目「指派给我」· {total} 条（本工作空间）
                </div>
              </div>
              <button onClick={() => void load()} disabled={loading}
                className="ml-auto h-8 px-3 rounded-md border border-neutral-300 text-[12.5px] text-neutral-600 hover:bg-neutral-50 disabled:opacity-50"
                data-sb-scope="my-tasks-refresh">
                {loading ? "加载中…" : "⟳ 刷新"}
              </button>
            </div>

            {/* 四统计卡（点击即筛列表） */}
            <div className="grid grid-cols-4 gap-3 mb-3.5" data-sb-scope="my-tasks-cards">
              {cards.map((c) => (
                <button key={c.key} onClick={() => setFilter(c.key)}
                  className={`text-left rounded-lg border bg-white p-3.5 shadow-sm transition-colors ${
                    filter === c.key ? "border-brand-400 ring-1 ring-brand-100" : "border-neutral-200 hover:border-neutral-300"}`}
                  data-sb-scope="my-tasks-card">
                  <div className="text-[12px] text-neutral-500">{c.label}</div>
                  <div className={`text-[22px] font-semibold font-mono ${c.tone}`}>{c.value}</div>
                </button>
              ))}
            </div>

            {/* 7 日完成趋势 */}
            <div className="rounded-lg border border-neutral-200 bg-white p-3.5 mb-3.5 shadow-sm">
              <div className="text-[12px] text-neutral-500 mb-2.5">7 日完成趋势</div>
              <div className="flex items-end gap-2 h-[64px]" data-sb-scope="my-tasks-trend">
                {(stats?.trend ?? []).map((t, i) => (
                  <div key={`${t.day ?? i}`} className="flex-1 flex flex-col items-center gap-1"
                    title={`${t.day ?? "—"}：${t.count} 个`}>
                    <div className="w-full rounded-t bg-emerald-400/80"
                      style={{ height: `${Math.max(4, (t.count / trendMax) * 48)}px` }} />
                    <div className="text-[10px] text-neutral-400 font-mono">
                      {t.day ? String(t.day).slice(5) : "—"}
                    </div>
                  </div>
                ))}
                {!stats?.trend?.length && (
                  <div className="text-[12.5px] text-neutral-400 py-4">暂无数据</div>
                )}
              </div>
            </div>

            {/* 语义组筛选 */}
            <div className="flex items-center gap-1.5 mb-2.5">
              {(["all", ...GROUPS] as Filter[]).map((g) => (
                <button key={g} onClick={() => setFilter(g)}
                  className={`h-7 px-2.5 rounded-md text-[12.5px] ${
                    filter === g ? "bg-brand-50 text-brand-600 font-medium" : "text-neutral-600 hover:bg-neutral-100"}`}>
                  {g === "all" ? "全部" : GROUP_LABEL[g]}
                </button>
              ))}
              <span className="ml-auto text-xs text-neutral-400">
                {visible.length} / {rows.length} 条
              </span>
            </div>

            {/* 待办列表 */}
            <div className="rounded-lg border border-neutral-200 bg-white shadow-sm overflow-hidden">
              <table className="w-full text-[13px]">
                <thead className="text-left text-xs text-neutral-400 bg-neutral-50">
                  <tr>
                    <th className="px-3 py-2 w-[100px]">编号</th>
                    <th>任务</th>
                    <th className="w-[90px]">状态</th>
                    <th className="w-[80px]">优先级</th>
                    <th className="w-[110px]">截止</th>
                  </tr>
                </thead>
                <tbody data-sb-scope="my-tasks-rows">
                  {visible.map((r) => {
                    const today = new Date().toISOString().slice(0, 10);
                    const open = r.state_group === "unstarted" || r.state_group === "started";
                    const overdue = open && !!r.target_date && r.target_date < today;
                    return (
                      <tr key={r.id} className="border-t border-neutral-100 hover:bg-neutral-50 cursor-pointer"
                        onClick={() => r.project_id &&
                          nav(`/${ws}/projects/${r.project_id}?issue=${r.id}`)}>
                        <td className="px-3 py-2 font-mono text-[12px] text-neutral-400">
                          {r.issue_key ?? r.id.slice(0, 8)}
                        </td>
                        <td className="px-2 py-2">
                          <span className="text-neutral-800">{r.name}</span>
                          {r.project && (
                            <span className="ml-2 text-[11.5px] text-neutral-400">{r.project}</span>
                          )}
                        </td>
                        <td className="px-2 py-2">
                          <span className="px-1.5 py-0.5 rounded text-xs bg-neutral-100 text-neutral-600">
                            {GROUP_LABEL[r.state_group ?? "unstarted"] ?? r.state_group}
                          </span>
                        </td>
                        <td className="px-2 py-2 text-neutral-500">{r.priority ?? "—"}</td>
                        <td className={`px-2 py-2 font-mono text-[12px] ${
                          overdue ? "text-rose-600 font-semibold" : "text-neutral-500"}`}>
                          {r.target_date ?? "—"}
                        </td>
                      </tr>
                    );
                  })}
                  {!loading && !visible.length && (
                    <tr><td colSpan={5} className="py-12 text-center text-neutral-400 text-[13px]">
                      {filter === "all" ? "当前没有指派给你的任务" : "该筛选下暂无任务"}
                    </td></tr>
                  )}
                  {loading && (
                    <tr><td colSpan={5} className="py-10 text-center text-neutral-400 text-[13px]">加载中…</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
