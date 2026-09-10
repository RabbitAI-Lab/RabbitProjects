import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { GithubIntegrationAPI, ProjectAPI, type GithubBindingRow } from "../services/api";
import { ConfirmDialog } from "../components/files/FileDialogs";
import { toast } from "../components/Toast";

/** INTG-001 §3.1/§3.2 集成设置页（C.132）：GitHub 卡 + 绑定管理 + 冲突日志。 */
const STATUS_CN: Record<GithubBindingRow["sync_status"], string> = {
  syncing: "同步中", paused: "已暂停", stale: "仓库失效", unbound: "已解绑",
};

export default function ProjectIntegrationsPage() {
  const { workspaceSlug: slug, projectId } =
    useParams<{ workspaceSlug: string; projectId: string }>();
  const [bindings, setBindings] = useState<GithubBindingRow[]>([]);
  const [conflicts, setConflicts] = useState<Array<{
    id: string; repository: string; scope: string; winner_side: string; occurred_at: string }>>([]);
  const [tab, setTab] = useState<"bindings" | "conflicts">("bindings");
  const [installUrl, setInstallUrl] = useState<string | null>(null);
  const [bindingForm, setBindingForm] = useState<{ repo: string; installation: string } | null>(null);
  const [removing, setRemoving] = useState<GithubBindingRow | null>(null);
  const [projName, setProjName] = useState("…");
  const [projIdentifier, setProjIdentifier] = useState("");
  useEffect(() => {
    ProjectAPI.detail(slug!, projectId!).then((r) => {
      const p = (r as unknown as { data: { name: string; identifier: string } }).data;
      setProjName(p?.name ?? "…"); setProjIdentifier(p?.identifier ?? "");
    }).catch(() => {});
  }, [slug, projectId]);

  const load = useCallback(() => {
    GithubIntegrationAPI.listBindings(slug!, projectId!).then(
      (r) => setBindings((r as { data?: GithubBindingRow[] }).data ?? [])).catch(() => setBindings([]));
    GithubIntegrationAPI.syncLogs(slug!, projectId!).then(
      (r) => setConflicts((r as { data?: Array<{ id: string; repository: string; scope: string; winner_side: string; occurred_at: string }> }).data ?? [])).catch(() => setConflicts([]));
  }, [slug, projectId]);
  useEffect(() => { load(); }, [load]);

  const install = async () => {
    try {
      const r = await GithubIntegrationAPI.installEntry(slug!);
      const url = (r as { data: { install_url: string } }).data.install_url;
      setInstallUrl(url);
    } catch { toast("安装入口获取失败", "error"); }
  };

  const bind = async () => {
    if (!bindingForm) return;
    try {
      const r = await GithubIntegrationAPI.createBinding(slug!, projectId!, {
        repository_full_name: bindingForm.repo.trim().toLowerCase(),
        installation_id: Number(bindingForm.installation) || 0,
      });
      const secret = (r as { data?: GithubBindingRow }).data?.webhook_secret_shown_once;
      setBindingForm(null);
      load();
      if (secret) toast(`绑定成功——Webhook secret（仅此一次）：${secret.slice(0, 8)}…（已复制）`, "ok");
      void navigator.clipboard?.writeText(secret ?? "");
    } catch (e) {
      toast(e instanceof Error ? e.message : "绑定失败", "error");
    }
  };

  // 渲染期不取时钟（react(purity)）：锚定挂载时刻，相对分钟数随重挂载刷新
  const [mountedAt] = useState(() => Date.now());
  const lastSync = (b: GithubBindingRow) =>
    b.last_synced_at ? `${Math.max(0, Math.round((mountedAt - +new Date(b.last_synced_at)) / 60000))} 分钟前` : "未同步";

  return (
    <div className="h-screen flex flex-col bg-neutral-50">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <ProjectSidebar projectName={projName} identifier={projIdentifier} />
        <main className="flex-1 overflow-auto p-6" data-sb-scope="integrations-page">
          <h1 className="text-[20px] font-semibold text-neutral-900 mb-4">集成</h1>

          <section className="bg-white rounded-xl border border-neutral-200 p-4 mb-6" data-sb-scope="github-card">
            <div className="flex items-center justify-between">
              <div>
                <div className="flex items-center gap-2">
                  <span className="w-9 h-9 rounded-lg bg-neutral-900 text-white grid place-items-center text-[16px]">G</span>
                  <div>
                    <div className="text-[14.5px] font-medium text-neutral-900">GitHub</div>
                    <div className="text-[12.5px] text-neutral-500">
                      {bindings.filter((b) => b.sync_status !== "unbound").length} 个仓库绑定 ·
                      上次同步 {bindings[0] ? lastSync(bindings[0]) : "—"}
                    </div>
                  </div>
                </div>
              </div>
              <div className="flex gap-2">
                <button onClick={() => void install()}
                  className="h-9 px-4 rounded-md border border-neutral-300 text-[13px] hover:bg-neutral-50">安装应用</button>
                <button onClick={() => setBindingForm({ repo: "", installation: "0" })}
                  disabled={bindings.filter((b) => b.sync_status !== "unbound").length >= 5}
                  className="h-9 px-4 rounded-md bg-brand-500 text-white text-[13px] disabled:opacity-50">
                  绑定仓库（≤5）
                </button>
              </div>
            </div>
          </section>

          <div className="flex gap-1 mb-3" role="tablist">
            {([["bindings", "仓库绑定"], ["conflicts", `同步冲突${conflicts.length ? ` (${conflicts.length})` : ""}`]] as const).map(([k, label]) => (
              <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k as typeof tab)}
                className={`h-9 px-4 rounded-t-md text-[13px] border-b-2 ${
                  tab === k ? "border-brand-500 text-neutral-900" : "border-transparent text-neutral-500"}`}>
                {label}
              </button>
            ))}
          </div>

          {tab === "bindings" ? (
            bindings.filter((b) => b.sync_status !== "unbound").length === 0 ? (
              <div className="bg-white rounded-xl border border-dashed border-neutral-300 p-12 text-center text-[13px] text-neutral-500">
                绑定 GitHub 仓库后：Issue 双向同步 / PR 合并自动完成 / Commit 自动挂载
              </div>
            ) : (
              <ul className="space-y-2">
                {bindings.filter((b) => b.sync_status !== "unbound").map((b) => (
                  <li key={b.id} className="bg-white rounded-xl border border-neutral-200 p-4 flex items-center justify-between"
                    data-sb-scope="binding-row" data-binding-id={b.id}>
                    <div>
                      <div className="font-mono text-[13px] text-neutral-800">{b.repository_full_name}</div>
                      <div className="text-[12px] text-neutral-500 mt-0.5">
                        <SyncBadge status={b.sync_status} /> · 上次同步 {lastSync(b)}
                      </div>
                    </div>
                    <div className="flex gap-2">
                      <button onClick={() => void GithubIntegrationAPI.updateBinding(
                          slug!, projectId!, b.id,
                          { sync_status: b.sync_status === "paused" ? "syncing" : "paused" })
                          .then(() => { toast(b.sync_status === "paused" ? "已恢复同步" : "已暂停", "ok"); load(); })}
                        className="h-8 px-2.5 rounded-md border border-neutral-300 text-[12.5px] hover:bg-neutral-50">
                        {b.sync_status === "paused" ? "恢复同步" : "暂停"}
                      </button>
                      <button onClick={() => setRemoving(b)}
                        className="h-8 px-2.5 rounded-md border border-red-200 text-red-600 text-[12.5px] hover:bg-red-50">解绑</button>
                    </div>
                  </li>
                ))}
              </ul>
            )
          ) : (
            conflicts.length === 0 ? (
              <div className="bg-white rounded-xl border border-dashed border-neutral-300 p-12 text-center text-[13px] text-neutral-500">
                没有同步冲突——双向编辑冲突时败方快照会记录在此
              </div>
            ) : (
              <ul className="space-y-2">
                {conflicts.map((c) => (
                  <li key={c.id} className="bg-white rounded-xl border border-neutral-200 p-4 text-[13px]">
                    <span className="font-mono text-[12px] text-neutral-600">{c.repository}</span>
                    <span className="mx-2 text-neutral-300">·</span>
                    {c.scope} 冲突（胜方 {c.winner_side === "github" ? "GitHub" : "系统"}）
                    <span className="float-right font-mono text-[12px] text-neutral-400">
                      {c.occurred_at.slice(0, 16).replace("T", " ")}
                    </span>
                  </li>
                ))}
              </ul>
            )
          )}

          {installUrl && (
            <div className="fixed inset-0 z-40 bg-black/30 flex items-center justify-center p-4"
              onMouseDown={(e) => e.target === e.currentTarget && setInstallUrl(null)}>
              <div className="bg-white rounded-xl p-6 w-[480px] max-w-full" role="dialog" aria-modal="true">
                <div className="text-[15px] font-semibold mb-2">跳转 GitHub 安装</div>
                <p className="text-[13px] text-neutral-600 mb-4">在 GitHub 完成安装与仓库授权后回到本页绑定仓库。</p>
                <a href={installUrl} target="_blank" rel="noreferrer"
                  className="block text-brand-600 text-[13px] break-all mb-4 hover:underline">{installUrl}</a>
                <div className="flex justify-end">
                  <button onClick={() => setInstallUrl(null)} className="h-9 px-4 rounded-md border border-neutral-300 text-[13px]">关闭</button>
                </div>
              </div>
            </div>
          )}

          {bindingForm && (
            <div className="fixed inset-0 z-40 bg-black/30 flex items-center justify-center p-4"
              onMouseDown={(e) => e.target === e.currentTarget && setBindingForm(null)}>
              <div className="bg-white rounded-xl p-6 w-[480px] max-w-full" role="dialog" aria-modal="true"
                aria-label="绑定仓库" data-sb-scope="binding-form">
                <div className="text-[15px] font-semibold mb-4">绑定仓库</div>
                <label className="block text-[12.5px] text-neutral-500 mb-1">仓库全名（owner/repo）</label>
                <input value={bindingForm.repo} onChange={(e) => setBindingForm({ ...bindingForm, repo: e.target.value })}
                  aria-label="仓库全名" placeholder="acme/rabbit-web"
                  className="w-full h-9 px-3 border border-neutral-300 rounded-md text-[13px] mb-3" />
                <label className="block text-[12.5px] text-neutral-500 mb-1">installation id（安装回调获得）</label>
                <input value={bindingForm.installation}
                  onChange={(e) => setBindingForm({ ...bindingForm, installation: e.target.value })}
                  aria-label="installation id"
                  className="w-full h-9 px-3 border border-neutral-300 rounded-md text-[13px] mb-5" />
                <div className="flex justify-end gap-2">
                  <button onClick={() => setBindingForm(null)} className="h-9 px-4 rounded-md border border-neutral-300 text-[13px]">取消</button>
                  <button disabled={!bindingForm.repo.includes("/")} onClick={() => void bind()}
                    className="h-9 px-4 rounded-md bg-brand-500 text-white text-[13px] disabled:opacity-50">绑定</button>
                </div>
              </div>
            </div>
          )}

          {removing && (
            <ConfirmDialog title="解绑仓库？" danger okText="解绑" onClose={() => setRemoving(null)}
              onOk={async () => {
                await GithubIntegrationAPI.deleteBinding(slug!, projectId!, removing.id);
                setRemoving(null); toast("已解绑（任务外部映射保留，历史可溯）", "ok"); load();
              }}>
              解绑后停止同步与 Webhook 订阅；已同步任务的 GitHub 映射保留。
            </ConfirmDialog>
          )}
        </main>
      </div>
    </div>
  );
}

function SyncBadge({ status }: { status: GithubBindingRow["sync_status"] }) {
  const cn = status === "syncing" ? "text-green-700 bg-green-50"
    : status === "paused" ? "text-neutral-500 bg-neutral-100"
    : "text-amber-700 bg-amber-50";
  return <span className={`px-1.5 py-0.5 rounded text-[11px] ${cn}`}>{STATUS_CN[status]}</span>;
}
