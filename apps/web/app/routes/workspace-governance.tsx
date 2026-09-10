import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { GovernanceAPI, unwrap, WorkspaceAPI, type WorkspaceSummary } from "../services/api";
import { ConfirmDialog } from "../components/files/FileDialogs";
import { toast } from "../components/Toast";

/** TEAM-003 §3.1 工作空间治理页（C.128）：归档 / 全局标签 / 状态模板 / 活跃度。 */
type StateGroup = { group: string; color?: string; states: Array<{ name: string; sequence: number }> };

export default function WorkspaceGovernancePage() {
  const { workspaceSlug: slug } = useParams<{ workspaceSlug: string }>();
  const nav = useNavigate();
  const [ws, setWs] = useState<WorkspaceSummary | null>(null);
  const [tab, setTab] = useState<"archive" | "labels" | "states" | "activity">("activity");
  const [archiving, setArchiving] = useState(false);

  useEffect(() => {
    WorkspaceAPI.list().then((r) => {
      const target = unwrap<WorkspaceSummary[]>(r).find((w) => w.slug === slug) ?? null;
      setWs(target);
    }).catch(() => setWs(null));
  }, [slug]);

  const isOwner = (ws as unknown as { role?: number } | null)?.role === 20;

  return (
    <div className="h-screen flex flex-col bg-neutral-50">
      <Topbar />
      <main className="flex-1 overflow-auto p-6 max-w-[1080px] mx-auto w-full" data-sb-scope="ws-governance">
        <header className="flex items-center justify-between mb-4">
          <h1 className="text-[20px] font-semibold text-neutral-900">工作空间治理</h1>
          <button onClick={() => nav(`/${slug}/settings/members`)}
            className="h-9 px-4 rounded-md border border-neutral-300 text-[13px] hover:bg-white">成员管理</button>
        </header>
        <div className="flex gap-1 mb-4 border-b border-neutral-200" role="tablist">
          {([["activity", "活跃度"], ["labels", "全局标签"], ["states", "状态模板"], ["archive", "归档"]] as const).map(([k, label]) => (
            <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
              className={`h-10 px-4 text-[13px] border-b-2 -mb-px ${
                tab === k ? "border-brand-500 text-neutral-900" : "border-transparent text-neutral-500 hover:text-neutral-700"}`}>
              {label}
            </button>
          ))}
        </div>

        {tab === "activity" && <ActivityBlock slug={slug!} />}
        {tab === "labels" && <LabelsBlock slug={slug!} />}
        {tab === "states" && <StatesBlock slug={slug!} />}
        {tab === "archive" && (
          <section className="bg-white rounded-xl border border-neutral-200 p-6" data-sb-scope="archive-block">
            <div className="text-[14.5px] font-medium text-neutral-900 mb-2">归档工作空间</div>
            <p className="text-[13px] text-neutral-500 mb-4">
              归档后全站只读（写操作 403）——项目、任务、文件全部保留可看；仅所有者可恢复。
            </p>
            {isOwner ? (
              <button onClick={() => setArchiving(true)}
                className="h-9 px-4 rounded-md border border-red-300 text-red-600 text-[13px] hover:bg-red-50">
                归档工作空间
              </button>
            ) : (
              <div className="text-[12.5px] text-neutral-400">仅工作空间所有者可执行归档/恢复</div>
            )}
            {archiving && (
              <ConfirmDialog title="归档工作空间？" danger okText="确认归档" onClose={() => setArchiving(false)}
                onOk={async () => {
                  const r = await GovernanceAPI.archive(slug!);
                  const d = (r as { data: { affected_projects: number } }).data;
                  toast(`已归档——${d.affected_projects} 个项目进入只读`, "ok");
                  setArchiving(false);
                }}>
                归档立即生效：所有成员的写操作将被拒绝，直到所有者恢复。
              </ConfirmDialog>
            )}
          </section>
        )}
      </main>
    </div>
  );
}

function ActivityBlock({ slug }: { slug: string }) {
  const [stats, setStats] = useState<Awaited<ReturnType<typeof GovernanceAPI.activityStats>>["data"] | null>(null);
  useEffect(() => {
    GovernanceAPI.activityStats(slug).then((r) => setStats(unwrap<Awaited<ReturnType<typeof GovernanceAPI.activityStats>>["data"]>(r) ?? null))
      .catch(() => setStats(null));
  }, [slug]);
  if (!stats) return <div className="h-40 rounded-xl bg-neutral-100 animate-pulse" />;
  const BUCKET_CN: Record<string, string> = { "0": "0 次", "1_5": "1-5 次", "6_20": "6-20 次", gt_20: "20+ 次" };
  const HIST_CN: Record<string, string> = { "1": "1 天", "2_3": "2-3 天", "4_5": "4-5 天", ge_6: "6+ 天" };
  return (
    <div className="space-y-4" data-sb-scope="activity-block">
      <div className="grid grid-cols-3 gap-4">
        {[["7 天活跃", stats.active_members_7d], ["30 天活跃", stats.active_members_30d],
          ["成员总数", stats.total_members]].map(([k, v]) => (
          <div key={k as string} className="bg-white rounded-xl border border-neutral-200 p-5">
            <div className="text-[12px] text-neutral-400 mb-1">{k}</div>
            <div className="text-[32px] font-semibold text-neutral-900 leading-none">{v as number}</div>
          </div>
        ))}
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="bg-white rounded-xl border border-neutral-200 p-5 lg:col-span-2">
          <div className="text-[13px] font-medium text-neutral-700 mb-3">
            本周贡献分布（{stats.contribution_distribution[0]?.week}）
          </div>
          <Bars data={Object.entries(stats.contribution_distribution[0]?.buckets ?? {})}
            labels={BUCKET_CN} total={stats.total_members} />
        </div>
        <div className="bg-white rounded-xl border border-neutral-200 p-5">
          <div className="text-[13px] font-medium text-neutral-700 mb-3">30 天登录天数</div>
          <Bars data={Object.entries(stats.login_days_histogram)} labels={HIST_CN}
            total={Object.values(stats.login_days_histogram).reduce((a, b) => a + b, 0)} />
        </div>
      </div>
      {stats.top_actions?.issue != null && (
        <div className="bg-white rounded-xl border border-neutral-200 p-5 text-[13px] text-neutral-600">
          行为构成：任务 {Math.round((stats.top_actions.issue ?? 0) * 100)}% ·
          评论 {Math.round((stats.top_actions.comment ?? 0) * 100)}%（仅聚合口径，无个人明细）
        </div>
      )}
    </div>
  );
}

function Bars({ data, labels, total }: {
  data: Array<[string, number]>; labels: Record<string, string>; total: number }) {
  return (
    <div className="space-y-2">
      {data.map(([k, n]) => (
        <div key={k} className="flex items-center gap-3 text-[12.5px]">
          <span className="w-14 text-neutral-500">{labels[k] ?? k}</span>
          <div className="flex-1 h-3 rounded bg-neutral-100 overflow-hidden">
            <div className="h-full bg-brand-400" style={{ width: `${total ? (n / total) * 100 : 0}%` }} />
          </div>
          <span className="w-8 text-right font-mono text-neutral-700">{n}</span>
        </div>
      ))}
    </div>
  );
}

function LabelsBlock({ slug }: { slug: string }) {
  const [rows, setRows] = useState<Array<{ id: string; name: string; color: string; description: string }>>([]);
  const [form, setForm] = useState({ name: "", color: "#3B82F6", description: "" });
  const [removing, setRemoving] = useState<{ id: string; name: string } | null>(null);
  const load = useCallback(() => GovernanceAPI.listLabels(slug).then(
    (r) => setRows((r as { data?: typeof rows }).data ?? []), () => setRows([])), [slug]);
  useEffect(() => { load(); }, [load]);
  return (
    <section className="bg-white rounded-xl border border-neutral-200 p-6" data-sb-scope="labels-block">
      <div className="flex items-center justify-between mb-4">
        <div>
          <div className="text-[14.5px] font-medium text-neutral-900">全局标签</div>
          <div className="text-[12.5px] text-neutral-500">下发到全部项目（并集）；项目可本地覆盖同名标签</div>
        </div>
      </div>
      <div className="flex gap-2 mb-4">
        <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
          aria-label="标签名" placeholder="security"
          className="h-9 w-40 px-3 border border-neutral-300 rounded-md text-[13px]" />
        <input type="color" value={form.color} aria-label="颜色"
          onChange={(e) => setForm({ ...form, color: e.target.value })}
          className="h-9 w-12 border border-neutral-300 rounded-md" />
        <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })}
          aria-label="描述" placeholder="安全相关任务"
          className="flex-1 h-9 px-3 border border-neutral-300 rounded-md text-[13px]" />
        <button disabled={!form.name.trim()}
          onClick={() => GovernanceAPI.createLabel(slug, form).then(() => {
            toast("已创建并下发", "ok"); setForm({ name: "", color: "#3B82F6", description: "" }); load();
          }, (e) => toast(e instanceof Error ? e.message : "创建失败", "error"))}
          className="h-9 px-4 rounded-md bg-brand-500 text-white text-[13px] disabled:opacity-50">新建</button>
      </div>
      <ul className="divide-y divide-neutral-100">
        {rows.map((r) => (
          <li key={r.id} className="py-2.5 flex items-center gap-3 text-[13px]" data-sb-scope="ws-label-row">
            <span className="w-3.5 h-3.5 rounded" style={{ background: r.color }} />
            <span className="font-mono">{r.name}</span>
            <span className="text-neutral-400 truncate flex-1">{r.description}</span>
            <button onClick={() => setRemoving(r)} className="text-red-500 hover:underline text-[12.5px]">删除</button>
          </li>
        ))}
        {rows.length === 0 && <li className="py-8 text-center text-[13px] text-neutral-400">暂无全局标签</li>}
      </ul>
      {removing && (
        <ConfirmDialog title={`删除全局标签「${removing.name}」？`} danger okText="删除" onClose={() => setRemoving(null)}
          onOk={async () => {
            const r = await GovernanceAPI.deleteLabel(slug, removing.id);
            const affected = (r as { data?: { affected_issues?: number } }).data?.affected_issues ?? 0;
            toast(`已删除（${affected} 个任务引用写入名字快照）`, "ok");
            setRemoving(null); load();
          }}>
          已挂载该标签的任务将保留删除时刻的名字快照，不影响任务本身。
        </ConfirmDialog>
      )}
    </section>
  );
}

function StatesBlock({ slug }: { slug: string }) {
  const [snap, setSnap] = useState<{ name: string; version: number; groups: StateGroup[] } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    GovernanceAPI.getDefaultStates(slug).then((r) => setSnap((r as { data?: { name: string; version: number; groups: StateGroup[] } }).data ?? null));
  }, [slug]);
  if (!snap) return <div className="h-40 rounded-xl bg-neutral-100 animate-pulse" />;
  const GROUP_CN: Record<string, string> = {
    backlog: "待规划", unstarted: "未开始", started: "进行中",
    completed: "已完成", cancelled: "已取消",
  };
  const rename = (gi: number, si: number, name: string) => {
    const groups = snap.groups.map((g, i) => i !== gi ? g : {
      ...g, states: g.states.map((s, j) => j !== si ? s : { ...s, name }) });
    setSnap({ ...snap, groups });
  };
  const save = async () => {
    setBusy(true);
    try {
      const r = await GovernanceAPI.putDefaultStates(slug, { groups: snap.groups });
      const d = (r as { data?: { name: string; version: number; groups: StateGroup[] } }).data;
      setSnap(d ?? snap);
      toast(`已保存（版本 v${d?.version ?? snap.version + 1}，新项目起生效——快照语义）`, "ok");
    } catch (e) {
      toast(e instanceof Error ? e.message : "保存失败", "error");
    } finally { setBusy(false); }
  };
  return (
    <section className="bg-white rounded-xl border border-neutral-200 p-6" data-sb-scope="states-block">
      <div className="flex items-center justify-between mb-4">
        <div>
          <div className="text-[14.5px] font-medium text-neutral-900">基础状态模板</div>
          <div className="text-[12.5px] text-neutral-500">新建项目的默认状态集（当前版本 v{snap.version}）</div>
        </div>
        <button disabled={busy} onClick={() => void save()}
          className="h-9 px-4 rounded-md bg-brand-500 text-white text-[13px] disabled:opacity-50">保存</button>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
        {snap.groups.map((g, gi) => (
          <div key={g.group} className="rounded-lg border border-neutral-200 p-3">
            <div className="text-[12px] text-neutral-400 mb-2">{GROUP_CN[g.group] ?? g.group}</div>
            {g.states.map((s, si) => (
              <input key={si} value={s.name} aria-label={`${GROUP_CN[g.group]} 状态名`}
                onChange={(e) => rename(gi, si, e.target.value)}
                className="w-full h-8 mb-1.5 px-2 border border-neutral-200 rounded text-[13px]" />
            ))}
          </div>
        ))}
      </div>
    </section>
  );
}
