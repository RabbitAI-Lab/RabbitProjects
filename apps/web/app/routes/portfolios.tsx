/** 项目集汇总面板（PROJ-004 §3.1——冻结原型 V-PORT，Sprint-9）。
 *
 *  左组合树（选中节点）/ 右三卡（进度/资源/风险）+ 挂载项目列表；
 *  权重口径注脚常显（BR-13）；数值按操作者可见项目聚合（BR-14）。 */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { PortfolioAPI, ProjectAPI, unwrap } from "../services/api";
import { Sidebar } from "../components/Sidebar";
import { Topbar } from "../components/Topbar";
import { toast } from "../components/Toast";

type PfNode = {
  id: string; name: string; parent_id: string | null; depth: number;
  manager_id: string | null; project_count: number; children: PfNode[];
};
type Summary = {
  portfolio: { id: string; name: string; manager_id: string | null };
  progress: { overall: number; by_project: { project_id: string; identifier: string; ratio: number }[] };
  resource: { weeks: string[]; matrix: { actor: string; actor_id: string; cells: Record<string, number> }[] };
  risks: { type: string; [k: string]: unknown }[];
};
type ProjectLite = { id: string; name: string; status: string };

const pct = (n: number) => `${Math.round(n * 100)}%`;

export default function PortfoliosPage() {
  const { workspaceSlug: ws } = useParams();
  const nav = useNavigate();
  const [tree, setTree] = useState<PfNode[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [projects, setProjects] = useState<ProjectLite[]>([]);
  const [mountOpen, setMountOpen] = useState(false);
  const [mountTarget, setMountTarget] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!ws) return;
    try {
      const t = unwrap<PfNode[]>(await PortfolioAPI.tree(ws)) ?? [];
      setTree(t);
      setSelected((prev) => prev ?? firstLeaf(t)?.id ?? null);
      const p = unwrap<ProjectLite[]>(await ProjectAPI.listByWs(ws, { status: "all" }).catch(() => null));
      setProjects(p ?? []);
    } catch {
      toast("加载项目集失败", "error");
    } finally {
      setLoading(false);
    }
  }, [ws]);

  // oxlint-disable-next-line react/set-state-in-effect -- 服务端 loader（org-structure 基线）
  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!ws || !selected) return;
    PortfolioAPI.summary(ws, selected)
      .then((r) => setSummary(unwrap<Summary>(r) ?? null))
      .catch(() => setSummary(null));
  }, [ws, selected]);

  const mount = async () => {
    if (!ws || !selected || !mountTarget) return;
    try {
      await PortfolioAPI.mountProject(ws, selected, mountTarget);
      toast("项目已挂载", "ok");
      setMountOpen(false);
      load();
    } catch (e) {
      toast(e instanceof Error ? e.message : "挂载失败", "error");
    }
  };

  const rows: PfNode[] = [];
  const flatten = (nodes: PfNode[]) => {
    for (const n of nodes) { rows.push(n); flatten(n.children ?? []); }
  };
  flatten(tree);

  return (
    <div className="flex h-screen flex-col" data-sb-scope="page-portfolios">
      <Topbar />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar workspaceSlug={ws ?? ""} />
        <main className="flex-1 overflow-auto p-4">
          <div className="mx-auto max-w-[1240px] px-2 py-4">
            <div className="mb-[18px] flex items-center gap-3">
              <div>
                <div className="text-[17px] font-semibold">项目集</div>
                <div className="text-[12.5px] text-neutral-400">多项目归集 · 里程碑 · 跨项目依赖 · 整体进度（PROJ-004）</div>
              </div>
              <div className="ml-auto flex gap-2">
                <Link to={`/${ws}/portfolios/${selected ?? ""}/milestones`}
                  className="btn-ghost h-[30px] px-3 text-[12px]">里程碑</Link>
                <Link to={`/${ws}/portfolios/${selected ?? ""}/graph`}
                  className="btn-ghost h-[30px] px-3 text-[12px]">依赖图</Link>
              </div>
            </div>

            <div className="flex gap-4" data-sb-scope="pf-body">
              {/* 组合树 */}
              <div className="w-[260px] shrink-0 card p-2" data-sb-scope="pf-tree">
                <div className="px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-neutral-400">组合树</div>
                {rows.length === 0 && !loading && (
                  <div className="px-2 py-6 text-center text-[12.5px] text-neutral-400">暂无项目集——由空间管理员创建</div>
                )}
                {rows.map((n) => (
                  <button key={n.id}
                    onClick={() => { setSelected(n.id); setSummary(null); }}
                    className={`h-[32px] w-full rounded-md px-2.5 text-left text-[13px] flex items-center gap-2 ${selected === n.id ? "bg-brand-50 text-brand-600 font-medium" : "text-neutral-700 hover:bg-neutral-50"}`}
                    style={{ paddingLeft: 10 + (n.depth - 1) * 16 }}>
                    <span className="truncate">{n.name}</span>
                    <span className="ml-auto text-[11px] text-neutral-400">{n.project_count}</span>
                  </button>
                ))}
              </div>

              <div className="min-w-0 flex-1">
                {!summary ? (
                  <div className="card p-10 text-center text-neutral-400" data-sb-scope="pf-empty">
                    {loading ? "加载中…" : "选择左侧项目集查看汇总面板"}
                  </div>
                ) : (
                  <>
                    {/* 三卡（O1：进度/资源/风险等宽横排） */}
                    <div className="grid grid-cols-[1.25fr_1fr_1fr] gap-3.5" data-sb-scope="pf-cards">
                      <div className="card p-4" data-sb-scope="pf-card-progress">
                        <div className="mb-2.5 flex text-[12.5px] font-medium text-neutral-600">整体进度
                          <span className="ml-auto font-mono text-[12px]">{pct(summary.progress.overall)}</span></div>
                        <div className="mb-2 h-3 overflow-hidden rounded bg-neutral-100"><div className="h-full rounded bg-brand-500" style={{ width: pct(summary.progress.overall) }} /></div>
                        {summary.progress.by_project.map((p) => (
                          <div key={p.project_id} className="mb-1.5 flex items-center gap-2 text-[12.5px]">
                            <span className="w-[76px] text-neutral-600">{p.identifier}</span>
                            <div className="h-2 flex-1 overflow-hidden rounded bg-neutral-100"><div className="h-full rounded bg-brand-500" style={{ width: pct(p.ratio) }} /></div>
                            <span className="w-[40px] text-right font-mono text-[12px]">{pct(p.ratio)}</span>
                          </div>
                        ))}
                        <div className="mt-2 text-[11.5px] text-neutral-400">权重 = 未取消任务数（BR-13）</div>
                      </div>
                      <div className="card p-4" data-sb-scope="pf-card-resource">
                        <div className="mb-2.5 text-[12.5px] font-medium text-neutral-600">资源 <span className="ml-1 text-[11px] font-normal text-neutral-400">近 4 周</span></div>
                        {summary.resource.matrix.length === 0 && <div className="text-[12.5px] text-neutral-400">暂无工时快照</div>}
                        {summary.resource.matrix.map((r) => (
                          <div key={r.actor_id} className="mb-1.5 flex items-baseline gap-2 text-[12.5px]">
                            <span className="w-[52px] font-medium">{r.actor}</span>
                            <span className="text-[11.5px] text-neutral-400">
                              {Object.entries(r.cells).map(([k, v]) => `${k.slice(5, 10)} ${Math.round(v / 60)}h`).join(" · ")}
                            </span>
                            <span className="ml-auto font-mono text-neutral-600">
                              {Math.round(Object.values(r.cells).reduce((a, b) => a + b, 0) / 60)}h
                            </span>
                          </div>
                        ))}
                      </div>
                      <div className="card p-4" data-sb-scope="pf-card-risks">
                        <div className="mb-2.5 flex text-[12.5px] font-medium text-neutral-600">风险
                          <span className={`ml-auto text-[11px] ${summary.risks.length ? "text-red-600" : "text-neutral-400"}`}>{summary.risks.length}</span></div>
                        {summary.risks.length === 0 && <div className="text-[12.5px] text-neutral-400">无风险项</div>}
                        {summary.risks.slice(0, 6).map((r, i) => (
                          <div key={i} className="mb-1.5 flex items-start gap-2 rounded px-2 py-1.5 text-[12.5px] hover:bg-neutral-50">
                            <span className={r.type === "overdue_issue" ? "text-red-500" : "text-amber-500"}>⚠</span>
                            <span>{riskText(r)}</span>
                          </div>
                        ))}
                      </div>
                    </div>

                    {/* 项目列表 */}
                    <div className="card mt-4" data-sb-scope="pf-projects">
                      <div className="flex items-center border-b border-neutral-200 p-3">
                        <b className="text-[13.5px]">项目列表</b>
                        <span className="ml-2.5 text-[12px] text-neutral-400">按操作者可见项目聚合（BR-14）</span>
                        <button className="btn-primary ml-auto h-[28px] px-2.5 text-[12px]" onClick={() => { setMountTarget(""); setMountOpen(true); }}>+ 挂载项目</button>
                      </div>
                      <table className="tbl">
                        <thead><tr><th>项目</th><th>进度</th><th>占比</th></tr></thead>
                        <tbody>
                          {summary.progress.by_project.map((p) => {
                            const proj = projects.find((x) => x.id === p.project_id);
                            return (
                              <tr key={p.project_id} className="cursor-pointer" onClick={() => proj && nav(`/${ws}/projects/${proj.id}/board`)}>
                                <td>{proj?.name ?? p.project_id}</td>
                                <td><div className="flex items-center gap-2"><div className="h-2 w-[90px] overflow-hidden rounded bg-neutral-100"><div className="h-full rounded bg-brand-500" style={{ width: pct(p.ratio) }} /></div></div></td>
                                <td className="font-mono">{pct(p.ratio)}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>
        </main>
      </div>

      {mountOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={() => setMountOpen(false)} onClickCapture={(e) => e.stopPropagation()}>
          <div className="w-[440px] rounded-xl bg-white p-6 shadow-2xl" onClick={(e) => e.stopPropagation()} data-sb-scope="pf-mount-modal">
            <div className="mb-4 flex text-[16px] font-semibold">挂载项目
              <button className="ml-auto icon-btn" onClick={() => setMountOpen(false)}>✕</button></div>
            <label className="label">项目（BR-02：仅可挂载叶子项目集）</label>
            <select className="input mb-2" value={mountTarget} onChange={(e) => setMountTarget(e.target.value)}>
              <option value="">选择项目…</option>
              {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <div className="text-[11.5px] text-neutral-400">一个项目至多挂载一个项目集；双端权限校验（WS_ADMIN+ 或项目集 manager）。</div>
            <div className="mt-5 flex justify-end gap-2.5">
              <button className="btn-ghost" onClick={() => setMountOpen(false)}>取消</button>
              <button className="btn-primary" disabled={!mountTarget} onClick={mount}>挂载</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function firstLeaf(nodes: PfNode[]): PfNode | null {
  for (const n of nodes) {
    if (!n.children?.length) return n;
    const leaf = firstLeaf(n.children);
    if (leaf) return leaf;
  }
  return null;
}

function riskText(r: { type: string; [k: string]: unknown }): string {
  if (r.type === "overdue_issue") return `${r.issue} 逾期 ${r.days} 天（${r.title ?? ""}）`;
  if (r.type === "milestone_slip") return `里程碑「${r.milestone}」完成度 ${pct(Number(r.progress) || 0)}，剩 ${r.days_left} 天`;
  return `${r.issue} 被 ${(r.blocked_by as string[])?.length ?? 0} 个外部依赖阻塞`;
}
