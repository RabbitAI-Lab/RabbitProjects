/** 里程碑视图（PROJ-004 §3.2——冻结原型 V-MS，Sprint-9）。
 *
 *  里程碑行（完成度/剩余天数/贡献项计数）+ 新建/完成置位；三态色点。 */
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router";

import { PortfolioAPI, unwrap } from "../services/api";
import { Sidebar } from "../components/Sidebar";
import { Topbar } from "../components/Topbar";
import { toast } from "../components/Toast";

type Ms = {
  id: string; name: string; target_date: string; completed_at: string | null;
  progress: number; item_count: number;
};

export default function PortfolioMilestonesPage() {
  const { workspaceSlug: ws, portfolioId } = useParams();
  const [items, setItems] = useState<Ms[]>([]);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [target, setTarget] = useState("");

  const load = useCallback(async () => {
    if (!ws || !portfolioId) return;
    try {
      setItems(unwrap<Ms[]>(await PortfolioAPI.milestones(ws, portfolioId)) ?? []);
    } catch {
      toast("加载里程碑失败", "error");
    }
  }, [ws, portfolioId]);

  // oxlint-disable-next-line react/set-state-in-effect -- 服务端 loader
  useEffect(() => { load(); }, [load]);

  const create = async () => {
    if (!ws || !portfolioId || !name || !target) return;
    try {
      await PortfolioAPI.createMilestone(ws, portfolioId, { name, target_date: target });
      toast("里程碑已创建", "ok");
      setCreating(false); setName("");
      load();
    } catch (e) {
      toast(e instanceof Error ? e.message : "创建失败", "error");
    }
  };

  const toggleComplete = async (m: Ms) => {
    if (!ws || !portfolioId) return;
    try {
      await PortfolioAPI.patchMilestone(ws, portfolioId, m.id,
        { completed_at: m.completed_at ? null : new Date().toISOString() });
      load();
    } catch (e) {
      toast(e instanceof Error ? e.message : "操作失败", "error");
    }
  };

  return (
    <div className="flex h-screen flex-col" data-sb-scope="page-pf-milestones">
      <Topbar />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar workspaceSlug={ws ?? ""} />
        <main className="flex-1 overflow-auto p-4">
          <div className="mx-auto max-w-[1240px] px-2 py-4">
            <div className="mb-[18px] flex items-center gap-3">
              <div>
                <div className="text-[17px] font-semibold">里程碑</div>
                <div className="text-[12.5px] text-neutral-400">贡献项跨项目归集 · 完成度 = 权重快照口径（BR-05）</div>
              </div>
              <div className="ml-auto flex gap-2">
                <Link to={`/${ws}/portfolios/${portfolioId}`} className="btn-ghost h-[30px] px-3 text-[12px]">汇总面板</Link>
                <Link to={`/${ws}/portfolios/${portfolioId}/graph`} className="btn-ghost h-[30px] px-3 text-[12px]">依赖图</Link>
                <button className="btn-primary h-[30px] px-3 text-[12px]" onClick={() => setCreating(true)} data-sb-scope="pf-ms-new">+ 新建里程碑</button>
              </div>
            </div>

            <div className="card" data-sb-scope="pf-ms-list">
              {items.length === 0 && (
                <div className="p-10 text-center text-[12.5px] text-neutral-400">暂无里程碑</div>
              )}
              {items.map((m) => {
                const done = Boolean(m.completed_at);
                const overdue = !done && new Date(m.target_date) < new Date();
                return (
                  <div key={m.id} className="border-b border-neutral-200 p-4 last:border-b-0" data-sb-scope="pf-ms-row">
                    <div className="flex items-center gap-2.5">
                      <span className={`h-2.5 w-2.5 rounded-full ${done ? "bg-emerald-500" : overdue ? "bg-red-500" : "bg-blue-500"}`} />
                      <span className="w-[52px] font-mono text-[12.5px] text-neutral-600">{m.target_date.slice(5)}</span>
                      <span className="text-[13.5px] font-semibold">{m.name}</span>
                      <div className="h-2 w-[130px] overflow-hidden rounded bg-neutral-100">
                        <div className={`h-full rounded ${done ? "bg-emerald-500" : "bg-brand-500"}`} style={{ width: pct(m.progress) }} />
                      </div>
                      <span className="font-mono text-[12px] text-neutral-500">{pct(m.progress)}</span>
                      <span className={`text-[12px] ${done ? "text-emerald-600" : overdue ? "text-red-600" : "text-neutral-400"}`}>
                        {done ? `已完成 ${m.completed_at?.slice(0, 10)}` : overdue ? "已延期" : `剩 ${daysLeft(m.target_date)} 天`}
                      </span>
                      <span className="text-[12px] text-neutral-400">贡献项 {m.item_count}</span>
                      <button className="btn-ghost ml-auto h-[26px] px-2 text-[11.5px]" onClick={() => toggleComplete(m)}>
                        {done ? "重开" : "置为完成"}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </main>
      </div>

      {creating && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={() => setCreating(false)}>
          <div className="w-[440px] rounded-xl bg-white p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="mb-4 flex text-[16px] font-semibold">新建里程碑
              <button className="ml-auto icon-btn" onClick={() => setCreating(false)}>✕</button></div>
            <label className="label">名称</label>
            <input className="input mb-3" value={name} onChange={(e) => setName(e.target.value)} placeholder="如：全量联调" />
            <label className="label">目标日期</label>
            <input className="input" type="date" value={target} onChange={(e) => setTarget(e.target.value)} />
            <div className="mt-2 text-[11.5px] text-neutral-400">创建后可在详情增删贡献项（跨项目 issue，BR-04）。</div>
            <div className="mt-5 flex justify-end gap-2.5">
              <button className="btn-ghost" onClick={() => setCreating(false)}>取消</button>
              <button className="btn-primary" disabled={!name || !target} onClick={create}>创建</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const pct = (n: number) => `${Math.round(n * 100)}%`;
const daysLeft = (d: string) => Math.ceil((new Date(d).getTime() - Date.now()) / 86400000);
