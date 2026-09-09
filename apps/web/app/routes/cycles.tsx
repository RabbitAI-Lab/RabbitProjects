/** 迭代管理页 + 燃尽图（RPT-003 §3.1——冻结原型 V-CYCLE/O3，Sprint-9）。
 *
 *  迭代行三态（active/planned/completed）+ 新建/开始/结束（结转|移回弹窗）+
 *  燃尽 SVG（理想线 vs 实际 + 今日实时点 + scope 事件 ▲▼）。 */
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router";

import { CycleAPI, unwrap } from "../services/api";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { Topbar } from "../components/Topbar";
import { toast } from "../components/Toast";

type Cycle = { id: string; name: string; status: string; start_date: string; end_date: string; issue_count: number; progress: number };
type Burndown = {
  measure: string; frozen: boolean;
  ideal: { date: string; remaining: number }[];
  points: { date: string; remaining: number; completed_delta?: number; provisional?: boolean }[];
  scope_events: { date: string; direction: string; issue: string }[];
};

const pct = (n: number) => `${Math.round(n * 100)}%`;
const STATUS: Record<string, { label: string; cls: string }> = {
  active: { label: "进行中", cls: "bg-blue-50 text-blue-700" },
  planned: { label: "规划中", cls: "bg-neutral-100 text-neutral-500" },
  completed: { label: "已完成", cls: "bg-emerald-50 text-emerald-700" },
};

export default function CyclesPage() {
  const { workspaceSlug: ws, projectId } = useParams();
  const [cycles, setCycles] = useState<Cycle[]>([]);
  const [burndown, setBurndown] = useState<Burndown | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [closeFor, setCloseFor] = useState<Cycle | null>(null);
  const [carry, setCarry] = useState<"next" | "backlog">("backlog");
  const [form, setForm] = useState({ name: "", start_date: "", end_date: "" });

  const load = useCallback(async () => {
    if (!ws || !projectId) return;
    try {
      const list = unwrap<Cycle[]>(await CycleAPI.list(ws, projectId)) ?? [];
      setCycles(list);
      setSelected((prev) => prev ?? list.find((c) => c.status === "active")?.id ?? list[0]?.id ?? null);
    } catch {
      toast("加载迭代失败", "error");
    }
  }, [ws, projectId]);

  // oxlint-disable-next-line react/set-state-in-effect -- 服务端 loader
  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!ws || !projectId || !selected) return;
    CycleAPI.burndown(ws, projectId, selected)
      .then((r) => setBurndown(unwrap<Burndown>(r) ?? null))
      .catch(() => setBurndown(null));
  }, [ws, projectId, selected]);

  const create = async () => {
    if (!ws || !projectId || !form.name) return;
    try {
      await CycleAPI.create(ws, projectId, form);
      toast("迭代已创建", "ok");
      setCreating(false);
      setForm({ name: "", start_date: "", end_date: "" });
      load();
    } catch (e) { toast(e instanceof Error ? e.message : "创建失败", "error"); }
  };
  const start = async (c: Cycle) => {
    if (!ws || !projectId) return;
    try {
      await CycleAPI.start(ws, projectId, c.id);
      toast(`迭代 ${c.name} 已开始`, "ok");
      load();
    } catch (e) { toast(e instanceof Error ? e.message : "开始失败", "error"); }
  };
  const complete = async () => {
    if (!ws || !projectId || !closeFor) return;
    try {
      const r = unwrap<{ carried_over: number; moved_back: number }>(
        await CycleAPI.complete(ws, projectId, closeFor.id, carry)) ?? {} as never;
      toast(`迭代已归档 · 终版快照落盘（${carry === "next" ? `结转 ${r.carried_over}` : `移回 ${r.moved_back}`}）`, "ok");
      setCloseFor(null);
      load();
    } catch (e) { toast(e instanceof Error ? e.message : "结束失败", "error"); }
  };

  return (
    <div className="flex h-screen flex-col" data-sb-scope="page-cycles">
      <Topbar />
      <div className="flex flex-1 overflow-hidden">
        <ProjectSidebar projectName={projectId ?? ""} identifier="CYC" />
        <main className="flex-1 overflow-auto p-4">
          <div className="mx-auto max-w-[1240px] px-2 py-4">
            <div className="mb-[18px] flex items-center gap-3">
              <div>
                <div className="text-[17px] font-semibold">迭代</div>
                <div className="text-[12.5px] text-neutral-400">时间盒管理 · 燃尽图（理想线 vs 实际 + scope 事件）· 结转/移回（RPT-003）</div>
              </div>
              <button className="btn-primary ml-auto h-[30px] px-3 text-[12px]" onClick={() => setCreating(true)} data-sb-scope="cycles-new">+ 新建迭代</button>
            </div>

            <div className="card" data-sb-scope="cycles-list">
              {cycles.length === 0 && <div className="p-10 text-center text-[12.5px] text-neutral-400">暂无迭代</div>}
              {cycles.map((c) => {
                const st = STATUS[c.status] ?? STATUS.planned!;
                return (
                  <button key={c.id} onClick={() => setSelected(c.id)}
                    className={`flex w-full items-center gap-3.5 border-b border-neutral-200 p-3.5 text-left last:border-b-0 hover:bg-neutral-50 ${selected === c.id ? "bg-brand-50/60" : ""}`}
                    data-sb-scope="cycle-row">
                    <span className={`h-2.5 w-2.5 rounded-full ${c.status === "active" ? "bg-blue-500" : c.status === "completed" ? "bg-emerald-500" : "bg-neutral-400"}`} />
                    <span className="w-[96px] text-[13.5px] font-semibold">{c.name}</span>
                    <span className="w-[150px] font-mono text-[12px] text-neutral-400">{c.start_date.slice(5)} ~ {c.end_date.slice(5)}</span>
                    <span className={`rounded-full px-2 py-0.5 text-[11px] ${st.cls}`}>{st.label}</span>
                    {c.status === "active" && (
                      <div className="flex items-center gap-2"><div className="h-2 w-[120px] overflow-hidden rounded bg-neutral-100"><div className="h-full rounded bg-brand-500" style={{ width: pct(c.progress) }} /></div><span className="font-mono text-[12px]">{pct(c.progress)}</span></div>
                    )}
                    <span className="text-[12px] text-neutral-400">任务 {c.issue_count}</span>
                    <span className="ml-auto flex gap-2">
                      {c.status === "planned" && <span className="btn-ghost h-[26px] px-2 text-[11.5px]" onClick={(e) => { e.stopPropagation(); start(c); }}>开始迭代</span>}
                      {c.status === "active" && <span className="btn-primary h-[26px] px-2 text-[11.5px]" onClick={(e) => { e.stopPropagation(); setCloseFor(c); setCarry("backlog"); }}>结束迭代 ▸</span>}
                    </span>
                  </button>
                );
              })}
            </div>

            {/* 燃尽图（O3） */}
            {burndown && selected && (
              <div className="card mt-4 p-5" data-sb-scope="burndown-card">
                <div className="mb-1 flex items-center gap-2.5 text-[13.5px] font-semibold">
                  燃尽图 · {cycles.find((c) => c.id === selected)?.name}
                  <span className="ml-auto text-[12px] font-normal text-neutral-400">度量：{burndown.measure === "estimate_minutes" ? "预估工时" : burndown.measure === "count" ? "任务数" : burndown.measure}</span>
                </div>
                <div className="mb-3.5 text-[11.5px] text-neutral-400">理想线 vs 实际 · 悬停数据点看当日净完成与 scope 事件 · 归档后只读终版快照（BR-06）</div>
                <BurnChart data={burndown} />
              </div>
            )}
          </div>
        </main>
      </div>

      {creating && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={() => setCreating(false)}>
          <div className="w-[460px] rounded-xl bg-white p-6 shadow-2xl" onClick={(e) => e.stopPropagation()} data-sb-scope="cycle-new-modal">
            <div className="mb-4 flex text-[16px] font-semibold">新建迭代
              <button className="ml-auto icon-btn" onClick={() => setCreating(false)}>✕</button></div>
            <label className="label" htmlFor="cycle-name">名称</label>
            <input id="cycle-name" className="input mb-3" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Sprint 25" />
            <div className="flex gap-3">
              <div className="flex-1"><label className="label">开始日期</label><input className="input" type="date" value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} /></div>
              <div className="flex-1"><label className="label">结束日期</label><input className="input" type="date" value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} /></div>
            </div>
            <div className="mt-2 text-[11.5px] text-neutral-400">同项目唯一 active（BR-03）；时间盒不可倒挂。</div>
            <div className="mt-5 flex justify-end gap-2.5">
              <button className="btn-ghost" onClick={() => setCreating(false)}>取消</button>
              <button className="btn-primary" disabled={!form.name || !form.start_date || !form.end_date} onClick={create}>创建</button>
            </div>
          </div>
        </div>
      )}

      {/* 结束迭代弹窗（BR-04：勾选结转） */}
      {closeFor && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={() => setCloseFor(null)}>
          <div className="w-[520px] rounded-xl bg-white p-6 shadow-2xl" onClick={(e) => e.stopPropagation()} data-sb-scope="cycle-close-modal">
            <div className="mb-4 flex text-[16px] font-semibold">结束迭代 · {closeFor.name}
              <button className="ml-auto icon-btn" onClick={() => setCloseFor(null)}>✕</button></div>
            <div className="mb-3 text-[13px] text-neutral-600">
              未完成任务（{closeFor.issue_count - Math.round(closeFor.progress * closeFor.issue_count)} 个）处理方式：
            </div>
            <div className="flex gap-2.5">
              <button className={`flex-1 rounded-lg border p-3 text-left ${carry === "next" ? "border-brand-500 bg-brand-50" : "border-neutral-200"}`} onClick={() => setCarry("next")}>
                <b className="text-[13px]">结转下一迭代</b>
                <div className="mt-0.5 text-[11.5px] text-neutral-400">移入最近的规划中迭代（BR-04）</div>
              </button>
              <button className={`flex-1 rounded-lg border p-3 text-left ${carry === "backlog" ? "border-brand-500 bg-brand-50" : "border-neutral-200"}`} onClick={() => setCarry("backlog")}>
                <b className="text-[13px]">移回待规划</b>
                <div className="mt-0.5 text-[11.5px] text-neutral-400">清空迭代归属，回到待规划池</div>
              </button>
            </div>
            <div className="mt-3 rounded-lg bg-blue-50 px-3 py-2 text-[11.5px] text-blue-700">
              结束即落终版快照（is_final）——此后修改历史任务不改变已归档报表（BR-04/06）。
            </div>
            <div className="mt-5 flex justify-end gap-2.5">
              <button className="btn-ghost" onClick={() => setCloseFor(null)}>取消</button>
              <button className="btn-primary" onClick={complete}>结束迭代</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/** 燃尽 SVG：理想线（虚线灰）+ 实际线（brand）+ 今日实时点（脉冲色点）+ scope ▲▼。 */
function BurnChart({ data }: { data: Burndown }) {
  const pts = data.points;
  if (pts.length === 0) return <div className="py-8 text-center text-[12.5px] text-neutral-400">迭代尚未开始或无快照</div>;
  const W = 640, H = 220, L = 46, B = 196;
  const maxV = Math.max(...pts.map((p) => p.remaining), ...data.ideal.map((p) => p.remaining), 1);
  const x = (i: number) => L + (i / Math.max(pts.length - 1, 1)) * (W - L - 20);
  const y = (v: number) => B - (v / maxV) * (B - 14);
  const segs = pts.map((p, i) => ({ x: x(i), y: y(p.remaining) }));
  const line = segs.map((s2, i) => `${i === 0 ? "M" : "L"}${s2.x.toFixed(1)},${s2.y.toFixed(1)}`).join(" ");
  const [idealA, idealB] = data.ideal;
  const idealD = idealA && idealB
    ? `M${L},${y(idealA.remaining)} L${W - 20},${y(idealB.remaining)}` : "";
  return (
    <svg viewBox={`0 0 ${W} ${H + 22}`} className="w-full max-w-[720px]">
      <line x1={L} y1="10" x2={L} y2={B} stroke="#e5e5e5" />
      <line x1={L} y1={B} x2={W - 20} y2={B} stroke="#d4d4d4" />
      {[1, 0.75, 0.5, 0.25, 0].map((f) => (
        <text key={f} x="12" y={y(maxV * f) + 4} className="fill-neutral-400 font-mono" fontSize="10">{Math.round(maxV * f)}</text>
      ))}
      {idealD && <path d={idealD} stroke="#a3a3a3" strokeDasharray="4 4" strokeWidth="1.2" fill="none" />}
      <path d={line} stroke="#3f76ff" strokeWidth="2.4" fill="none" />
      {pts.map((p, i) => {
        const px = x(i), py = y(p.remaining);
        return (
          <g key={i}>
            {p.provisional && <circle cx={px} cy={py} r="4.5" fill="#3f76ff" />}
            <circle cx={px} cy={py} r="3" fill="#fff" stroke="#3f76ff" strokeWidth="1.5">
              <title>{`${p.date} · 剩余 ${Math.round(p.remaining)}${p.completed_delta !== undefined ? ` · 净完成 ${Math.round(p.completed_delta)}` : ""}${p.provisional ? "（进行中，实时）" : ""}`}</title>
            </circle>
            {i % Math.ceil(pts.length / 8) === 0 && (
              <text x={px} y={B + 16} textAnchor="middle" className="fill-neutral-400 font-mono" fontSize="10">{p.date.slice(5)}</text>
            )}
          </g>
        );
      })}
      {/* scope 事件 ▲▼：与数据点同 x，标在点上方 */}
      {data.scope_events.map((ev, i) => {
        const idx = pts.findIndex((p) => p.date === ev.date);
        if (idx < 0) return null;
        return (
          <path key={i} d={ev.direction === "added" ? `M${x(idx)},${y(pts[idx]!.remaining) - 14} l-5,-9 h10 z` : `M${x(idx)},${y(pts[idx]!.remaining) - 14} l-5,9 h10 z`}
            fill={ev.direction === "added" ? "#10b981" : "#ef4444"}>
            <title>{`${ev.date} ${ev.direction === "added" ? "加入" : "移出"} ${ev.issue}`}</title>
          </path>
        );
      })}
    </svg>
  );
}
