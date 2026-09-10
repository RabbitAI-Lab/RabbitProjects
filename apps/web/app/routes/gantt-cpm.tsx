/** 浮动时间卡 + 预警配置（GANTT-003 §3.2/§3.3——冻结原型 V-CPMC，Sprint-9）。 */
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router";

import { CriticalPathAPI, unwrap } from "../services/api";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { Topbar } from "../components/Topbar";
import { toast } from "../components/Toast";

type CpmRow = { id: string; issue_key: string; es: string; ef: string; ls: string; lf: string; float_days: number; is_critical: boolean; has_external_preds: boolean };
type CpmConfig = { overdue_alert_enabled: boolean; float_consumed_alert_enabled: boolean; target_completion_date: string | null };

export default function GanttCpmPage() {
  const { workspaceSlug: ws, projectId } = useParams();
  const [rows, setRows] = useState<CpmRow[]>([]);
  const [selected, setSelected] = useState<CpmRow | null>(null);
  const [config, setConfig] = useState<CpmConfig | null>(null);
  const [recomputing, setRecomputing] = useState(false);

  const load = useCallback(async () => {
    if (!ws || !projectId) return;
    const data = unwrap<{ rows: CpmRow[] }>(await CriticalPathAPI.rows(ws, projectId).catch(() => null));
    setRows(data?.rows ?? []);
    setSelected((prev) => prev && data?.rows.some((r) => r.id === prev.id) ? prev : data?.rows[0] ?? null);
    try {
      setConfig(unwrap<CpmConfig>(await CriticalPathAPI.config(ws, projectId)) ?? null);
    } catch { /* 取数失败：保持默认开关展示 */ }
  }, [ws, projectId]);

  // oxlint-disable-next-line react/set-state-in-effect -- 服务端 loader（org-structure 基线）
  useEffect(() => { load(); }, [load]);

  const recompute = async () => {
    if (!ws || !projectId) return;
    setRecomputing(true);
    try {
      await CriticalPathAPI.recompute(ws, projectId);
      toast("重算已排队（60s 窗口内合并）——稍后刷新", "ok");
      setTimeout(load, 2500);
    } catch (e) { toast(e instanceof Error ? e.message : "触发失败", "error"); }
    finally { setRecomputing(false); }
  };

  const saveConfig = async (patch: Partial<CpmConfig>) => {
    if (!ws || !projectId) return;
    const next = { ...config, ...patch } as CpmConfig;
    setConfig(next);
    try {
      await CriticalPathAPI.patchConfig(ws, projectId, patch as Record<string, unknown>);
      toast("预警配置已保存", "ok");
    } catch (e) {
      toast(e instanceof Error ? e.message : "保存失败（需项目管理员）", "error");
      load();
    }
  };

  return (
    <div className="flex h-screen flex-col" data-sb-scope="page-gantt-cpm">
      <Topbar />
      <div className="flex flex-1 overflow-hidden">
        <ProjectSidebar projectName={projectId ?? ""} identifier="CPM" />
        <main className="flex-1 overflow-auto p-4">
          <div className="mx-auto max-w-[1240px] px-2 py-4">
            <div className="mb-[18px] flex items-center gap-3">
              <div>
                <div className="text-[17px] font-semibold">关键路径 · 计划分析</div>
                <div className="text-[12.5px] text-neutral-400">CPM 正推/逆推 · 浮动时间（负值 = 已落后于最晚开始）· 每日 09:30 重算与逾期预警（GANTT-003）</div>
              </div>
              <div className="ml-auto flex gap-2">
                <Link to={`/${ws}/projects/${projectId}/gantt`} className="btn-ghost h-[30px] px-3 text-[12px]">返回甘特</Link>
                <button className="btn-ghost h-[30px] px-3 text-[12px]" disabled={recomputing} onClick={recompute}>手动重算</button>
              </div>
            </div>

            <div className="grid grid-cols-[340px_1fr] items-start gap-4">
              {/* 浮动时间卡（§3.2） */}
              <div className="card p-4" data-sb-scope="cpm-float-card">
                <div className="mb-3 text-[13.5px] font-semibold">计划分析（CPM）</div>
                {!selected ? (
                  <div className="py-6 text-center text-[12.5px] text-neutral-400">暂无排期完整的任务——CPM 需要起止日期齐全（BR-01）</div>
                ) : (
                  <>
                    <div className="mb-2 flex items-center gap-2 text-[13px]">
                      <span className="badge-id">{selected.issue_key}</span>
                      <span className={`rounded-full px-2 py-0.5 text-[11px] ${selected.is_critical ? "bg-red-50 text-red-600" : "bg-neutral-100 text-neutral-500"}`}>
                        {selected.is_critical ? "🔴 关键" : "非关键"}
                      </span>
                    </div>
                    <div className="flex justify-between py-1 text-[12.5px] text-neutral-600"><span>最早开始 → 完成</span><b className="font-mono">{selected.es} → {selected.ef}</b></div>
                    <div className="flex justify-between py-1 text-[12.5px] text-neutral-600"><span>最晚开始 → 完成</span><b className="font-mono">{selected.ls} → {selected.lf}</b></div>
                    <div className="flex justify-between py-1 text-[12.5px] text-neutral-600">
                      <span>浮动时间</span>
                      <b className={`font-mono ${selected.float_days < 0 ? "text-red-600" : ""}`}>{selected.float_days} 天</b>
                    </div>
                    <div className="py-1 text-[12.5px] text-neutral-600">
                      状态：{selected.is_critical ? "关键（延期即项目延期）" : "非关键（浮动耗尽时将转为关键并预警）"}
                    </div>
                    {selected.has_external_preds && (
                      <div className="mt-1 text-[12.5px] text-amber-700">⚓ 有外部前置（跨项目，不进 CPM——BR-03 软策略）</div>
                    )}
                  </>
                )}
              </div>

              {/* 预警配置（§3.3） */}
            <div className="card p-4" data-sb-scope="cpm-config">
              <div className="mb-1.5 text-[13.5px] font-semibold">预警配置（项目管理员）</div>
              <div className="flex items-center gap-2.5 border-b border-neutral-200 py-2.5 text-[13px]">
                <div><b>关键任务逾期预警</b><div className="text-[11.5px] text-neutral-400">每日一条至负责人 + 项目经理（BR-08）</div></div>
                <Toggle on={config?.overdue_alert_enabled ?? true} onChange={(v) => saveConfig({ overdue_alert_enabled: v })} />
              </div>
              <div className="flex items-center gap-2.5 border-b border-neutral-200 py-2.5 text-[13px]">
                <div><b>浮动耗尽预警</b><div className="text-[11.5px] text-neutral-400">转关键即时一条（BR-09）</div></div>
                <Toggle on={config?.float_consumed_alert_enabled ?? true} onChange={(v) => saveConfig({ float_consumed_alert_enabled: v })} />
              </div>
              <div className="flex items-center gap-2.5 py-2.5 text-[13px]">
                <div><b>项目目标完工日</b><div className="text-[11.5px] text-neutral-400">逆推锚点（BR-06），影响最晚开始计算</div></div>
                <input type="date" className="input ml-auto h-[30px] w-[170px]"
                  value={config?.target_completion_date ?? ""}
                  onChange={(e) => saveConfig({ target_completion_date: e.target.value || null })} />
              </div>
            </div>
            {/* 行表 */}
              <div className="card" data-sb-scope="cpm-rows">
                <table className="tbl">
                  <thead><tr><th>任务</th><th>最早</th><th>最晚</th><th>浮动</th><th>关键</th></tr></thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.id} className={`cursor-pointer ${selected?.id === r.id ? "bg-brand-50/60" : ""}`} onClick={() => setSelected(r)}>
                        <td><span className="badge-id">{r.issue_key}</span>{r.has_external_preds && <span className="ml-1.5" title="有外部前置">⚓</span>}</td>
                        <td className="whitespace-nowrap font-mono text-[12px] text-neutral-600">{r.es.slice(5)}</td>
                        <td className="whitespace-nowrap font-mono text-[12px] text-neutral-600">{r.ls.slice(5)}</td>
                        <td className={`font-mono ${r.float_days < 0 ? "text-red-600" : r.float_days === 0 ? "text-amber-600" : ""}`}>{r.float_days}</td>
                        <td>{r.is_critical ? <span className="rounded bg-red-50 px-1.5 py-0.5 text-[11px] text-red-600">关键</span> : <span className="text-neutral-400">—</span>}</td>
                      </tr>
                    ))}
                    {rows.length === 0 && <tr><td colSpan={5} className="py-8 text-center text-neutral-400">暂无 CPM 行</td></tr>}
                  </tbody>
                </table>
              </div>
            </div>

          </div>
        </main>
      </div>
    </div>
  );
}

function Toggle({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button role="switch" aria-checked={on} onClick={() => onChange(!on)}
      className={`ml-auto h-5 w-9 shrink-0 rounded-full transition-colors ${on ? "bg-emerald-500" : "bg-neutral-200"}`}>
      <span className={`block h-4 w-4 translate-x-0.5 rounded-full bg-white shadow transition-transform ${on ? "translate-x-[18px]" : ""}`} />
    </button>
  );
}
