/** 自动化规则配置页（WF-003 §3/§4.5——C.146，2026-09-09 入口补口轮）。
 *
 *  规则 Tab：列表 + 启停开关 + 删除 + Dry Run 弹层（§2.4 0 写预演）；
 *  三段式编辑器（触发器/条件/动作——受限 DSL 的结构化表单，服务端校验兜底）；
 *  日志 Tab：运行日志（automation-runs/，成员可读 BR-12 透明度）。 */
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router";

import {
  AutomationAPI, IssueAPI, LabelAPI, ProjectAPI, ProjectMemberAPI, unwrap,
} from "../services/api";
import { Topbar } from "../components/Topbar";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { toast } from "../components/Toast";
import type { ApiError } from "../services/axios";

type Rule = { id: string; name: string; is_active: boolean;
  trigger: { type: string; config?: Record<string, unknown> };
  conditions: Array<Record<string, unknown>>;
  actions: Array<{ type: string; config?: Record<string, unknown> }>; last_run_at: string | null };
type Run = Record<string, unknown>;
type Member = { id: string; user: { id: string; display_name: string } };
type StateRow = { id: string; name: string; group: string };
type LabelRow = { id: string; name: string };

const TRIGGER_LABEL: Record<string, string> = {
  state_changed: "状态变更", issue_created: "任务创建", field_changed: "字段变更", due_approaching: "临期",
};
const ACTION_LABEL: Record<string, string> = {
  set_field: "设置字段", assign: "指派", notify: "发通知", transition: "流转", add_label: "加标签",
};
const TRIGGER_FIELDS = ["state_changed", "issue_created", "field_changed", "due_approaching"];
const ACTION_FIELDS = ["set_field", "assign", "notify", "transition", "add_label"];
const COND_FIELDS = ["priority", "state_group", "due", "estimate_minutes", "assignees", "labels"];
const COND_OPS = ["eq", "ne", "in", "gte", "lte", "contains", "before", "after"];
const GROUPS = ["backlog", "unstarted", "started", "completed", "cancelled"];
const PRIORITIES = ["urgent", "high", "medium", "low", "none"];

type CondRow = { field: string; operator: string; value: string };
type ActRow = { type: string; field: string; value: string; to_state_id: string; user_id: string;
  label_id: string; title: string; targets: string };

export default function AutomationRulesPage() {
  const { workspaceSlug: ws, projectId } = useParams();
  const [tab, setTab] = useState<"rules" | "runs">("rules");
  const [rules, setRules] = useState<Rule[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);
  const [projName, setProjName] = useState("…");
  const [projIdentifier, setProjIdentifier] = useState("");
  // 下拉数据源
  const [members, setMembers] = useState<Member[]>([]);
  const [states, setStates] = useState<StateRow[]>([]);
  const [labels, setLabels] = useState<LabelRow[]>([]);
  // 新建弹层
  const [createOpen, setCreateOpen] = useState(false);
  const [f, setF] = useState({
    name: "", trig: "state_changed", to_group: "started", field: "", lead_days: "2",
    conds: [] as CondRow[], acts: [] as ActRow[],
  });
  const [busy, setBusy] = useState(false);
  // Dry Run 弹层
  const [dryFor, setDryFor] = useState<Rule | null>(null);
  const [dryQ, setDryQ] = useState("");
  const [dryIssues, setDryIssues] = useState<Array<{ id: string; name: string; issue_key: string }>>([]);
  const [dryResult, setDryResult] = useState<Record<string, unknown> | null>(null);

  const load = useCallback(async () => {
    if (!ws || !projectId) return;
    try {
      const r = await AutomationAPI.list(ws, projectId);
      setRules(unwrap<Rule[]>(r) ?? []);
    } catch {
      toast("加载规则失败", "error");
    } finally {
      setLoading(false);
    }
  }, [ws, projectId]);

  const loadRuns = useCallback(async () => {
    if (!ws || !projectId) return;
    try {
      const r = await AutomationAPI.runs(ws, projectId);
      setRuns(unwrap<Run[]>(r) ?? []);
    } catch {
      toast("加载运行日志失败", "error");
    }
  }, [ws, projectId]);

  // oxlint-disable-next-line react/set-state-in-effect -- 服务端 loader（同 approvals.tsx 基线）
  useEffect(() => { load(); }, [load]);
  // oxlint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!ws || !projectId || tab !== "runs") return;
    loadRuns();
  }, [tab, ws, projectId]);
  // oxlint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!ws || !projectId) return;
    ProjectAPI.detail(ws, projectId).then((r) => {
      const d = (r as unknown as { data: { name?: string; identifier?: string } }).data;
      setProjName(d?.name ?? "…"); setProjIdentifier(d?.identifier ?? "");
    }).catch(() => {});
    ProjectMemberAPI.list(ws, projectId, { per_page: 100 }).then((r) => setMembers(unwrap<Member[]>(r) ?? [])).catch(() => {});
    ProjectAPI.states(ws, projectId, { include_cancelled: "1" }).then((r) => setStates(unwrap<StateRow[]>(r) ?? [])).catch(() => {});
    LabelAPI.list(ws, projectId).then((r) => setLabels(unwrap<LabelRow[]>(r) ?? [])).catch(() => {});
  }, [ws, projectId]);

  async function toggle(r: Rule) {
    if (!ws || !projectId) return;
    try {
      await AutomationAPI.patch(ws, projectId, r.id, { is_active: !r.is_active });
      setRules((rs) => rs.map((x) => x.id === r.id ? { ...x, is_active: !r.is_active } : x));
    } catch (e) {
      toast((e as ApiError)?.message ?? "启停失败", "error");
    }
  }

  async function remove(r: Rule) {
    if (!ws || !projectId || !window.confirm(`删除规则「${r.name}」？`)) return;
    try {
      await AutomationAPI.remove(ws, projectId, r.id);
      toast("已删除", "ok");
      await load();
    } catch (e) {
      toast((e as ApiError)?.message ?? "删除失败", "error");
    }
  }

  /** 三段式表单 → DSL payload（服务端 validate_rule_definition 兜底校验）。 */
  function buildPayload(): Record<string, unknown> {
    const trigCfg: Record<string, unknown> = f.trig === "state_changed"
      ? { to_group: f.to_group }
      : f.trig === "field_changed" ? { field: f.field } : f.trig === "due_approaching"
        ? { lead_days: Number(f.lead_days) || 2 } : {};
    const conditions = f.conds
      .filter((c) => c.field && c.operator)
      .map((c) => ({ field: c.field, operator: c.operator,
        value: c.operator === "in" ? c.value.split(",").map((s) => s.trim()).filter(Boolean) : c.value }));
    const actions = f.acts.filter((a) => a.type).map((a) => {
      const cfg: Record<string, unknown> = {};
      if (a.type === "set_field") { cfg.field = a.field; cfg.value = a.value; }
      if (a.type === "assign" && a.user_id) cfg.user_ids = [a.user_id];
      if (a.type === "notify") { if (a.title) cfg.title = a.title; cfg.targets = [a.targets || "assignees"]; }
      if (a.type === "transition" && a.to_state_id) cfg.to_state_id = a.to_state_id;
      if (a.type === "add_label" && a.label_id) cfg.label_ids = [a.label_id];
      return { type: a.type, config: cfg };
    });
    return { name: f.name.trim(), trigger: { type: f.trig, config: trigCfg }, conditions, actions };
  }

  async function create() {
    if (!ws || !projectId || !f.name.trim()) return;
    setBusy(true);
    try {
      await AutomationAPI.create(ws, projectId, buildPayload());
      toast("规则已创建", "ok");
      setCreateOpen(false);
      setF({ ...f, name: "", conds: [], acts: [] });
      await load();
    } catch (e) {
      const err = e as ApiError;
      const d = err?.details?.[0]?.message;
      toast(d ? `${d}（${(err.details ?? []).length} 项校验未过）` : (err?.message ?? "创建失败"), "error");
    } finally {
      setBusy(false);
    }
  }

  async function searchIssues(q: string) {
    if (!ws || !projectId || !q.trim()) { setDryIssues([]); return; }
    const r = await IssueAPI.list(ws, projectId, { q, per_page: 8 });
    setDryIssues(unwrap<Array<{ id: string; name: string; issue_key: string }>>(r) ?? []);
  }

  async function runDry(issueId: string) {
    if (!ws || !projectId || !dryFor) return;
    setBusy(true);
    try {
      const r = await AutomationAPI.dryRun(ws, projectId, dryFor.id, issueId);
      setDryResult((r as unknown as { data: Record<string, unknown> }).data);
    } catch (e) {
      toast((e as ApiError)?.message ?? "Dry Run 失败", "error");
    } finally {
      setBusy(false);
    }
  }

  const setAct = (i: number, patch: Partial<ActRow>) =>
    setF((s) => ({ ...s, acts: s.acts.map((a, j) => j === i ? { ...a, ...patch } : a) }));
  const inputCls = "mt-1 w-full h-8 border border-neutral-200 rounded-md px-2 text-[13px]";

  return (
    <div className="flex flex-col h-screen bg-neutral-50">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <ProjectSidebar projectName={projName} identifier={projIdentifier} />
        <main className="flex-1 min-w-0 flex flex-col" data-sb-scope="automation-page">
          <header className="h-[56px] border-b border-neutral-200 bg-white px-5 flex items-center gap-3 shrink-0">
            <h1 className="text-[15px] font-semibold">自动化</h1>
            <span className="text-[12px] text-neutral-500">{projIdentifier}</span>
            <nav className="flex gap-1" data-sb-scope="automation-tabs">
              {([["rules", "规则"], ["runs", "运行日志"]] as const).map(([k, label]) => (
                <button key={k} type="button" data-tab={k}
                  onClick={() => setTab(k)}
                  className={`px-3 h-7 rounded-md text-[13px] ${tab === k ? "bg-brand-50 text-brand-600 font-medium" : "text-neutral-600 hover:bg-neutral-100"}`}>{label}</button>
              ))}
            </nav>
            <div className="flex-1" />
            {tab === "rules" && (
              <button type="button" data-sb-scope="rule-create-btn"
                onClick={() => setCreateOpen(true)}
                className="h-[34px] px-3.5 inline-flex items-center gap-1.5 bg-brand-500 text-white rounded-md text-[13px] font-medium hover:bg-brand-600">＋ 新建规则</button>
            )}
          </header>

          <div className="flex-1 overflow-auto p-5">
            {tab === "rules" ? (loading ? <div className="text-sm text-neutral-400">加载中…</div> : (
              <table className="w-full bg-white border border-neutral-200 rounded-lg text-sm">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-wider text-neutral-400 border-b border-neutral-200">
                    <th className="px-4 py-2.5 font-semibold">规则</th>
                    <th className="px-4 py-2.5 font-semibold">触发器</th>
                    <th className="px-4 py-2.5 font-semibold">条件</th>
                    <th className="px-4 py-2.5 font-semibold">动作</th>
                    <th className="px-4 py-2.5 font-semibold">启用</th>
                    <th className="px-4 py-2.5 font-semibold text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {rules.map((r) => (
                    <tr key={r.id} data-sb-scope="rule-row" data-rule-id={r.id} className="border-b border-neutral-100 last:border-0">
                      <td className="px-4 py-3 font-medium text-neutral-800">{r.name}</td>
                      <td className="px-4 py-3 text-neutral-600">{TRIGGER_LABEL[r.trigger.type] ?? r.trigger.type}</td>
                      <td className="px-4 py-3 text-neutral-500">{r.conditions.length} 条</td>
                      <td className="px-4 py-3 text-neutral-500">{r.actions.map((a) => ACTION_LABEL[a.type] ?? a.type).join(" + ")}</td>
                      <td className="px-4 py-3">
                        <button type="button" data-sb-scope="rule-toggle" role="switch" aria-checked={r.is_active}
                          onClick={() => void toggle(r)}
                          className={`w-9 h-5 rounded-full relative transition ${r.is_active ? "bg-brand-500" : "bg-neutral-300"}`}>
                          <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ${r.is_active ? "left-4.5" : "left-0.5"}`} />
                        </button>
                      </td>
                      <td className="px-4 py-3 text-right space-x-2">
                        <button type="button" data-sb-scope="rule-dryrun-btn"
                          onClick={() => { setDryFor(r); setDryResult(null); setDryIssues([]); setDryQ(""); }}
                          className="px-2.5 h-7 rounded border border-neutral-200 text-[12.5px] hover:bg-neutral-50">Dry Run</button>
                        <button type="button" onClick={() => void remove(r)}
                          className="px-2.5 h-7 rounded border border-rose-200 text-rose-600 text-[12.5px] hover:bg-rose-50">删除</button>
                      </td>
                    </tr>
                  ))}
                  {rules.length === 0 && (
                    <tr><td colSpan={6} className="px-4 py-12 text-center text-neutral-400">还没有规则——「新建规则」按 触发器→条件→动作 三段配置</td></tr>
                  )}
                </tbody>
              </table>
            )) : (
              <table className="w-full bg-white border border-neutral-200 rounded-lg text-sm" data-sb-scope="runs-table">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-wider text-neutral-400 border-b border-neutral-200">
                    <th className="px-4 py-2.5 font-semibold">时间</th>
                    <th className="px-4 py-2.5 font-semibold">规则</th>
                    <th className="px-4 py-2.5 font-semibold">任务</th>
                    <th className="px-4 py-2.5 font-semibold">状态</th>
                    <th className="px-4 py-2.5 font-semibold">耗时</th>
                    <th className="px-4 py-2.5 font-semibold">动作明细</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r) => (
                    <tr key={String(r.id)} className="border-b border-neutral-100 last:border-0 align-top">
                      <td className="px-4 py-3 text-[12px] text-neutral-500 whitespace-nowrap">{String(r.created_at ?? "").replace("T", " ").slice(0, 19)}</td>
                      <td className="px-4 py-3 text-neutral-700">{String(r.rule_name ?? "")}</td>
                      <td className="px-4 py-3 text-neutral-500">{String(r.issue_key ?? "")} {String(r.issue_name ?? "")}</td>
                      <td className="px-4 py-3">
                        <span className={`text-xs px-1.5 py-0.5 rounded ${r.status === "success" ? "bg-emerald-50 text-emerald-700"
                          : r.status === "failed" ? "bg-rose-50 text-rose-700" : r.status === "skipped" ? "bg-neutral-100 text-neutral-500" : "bg-amber-50 text-amber-700"}`}>
                          {String(r.status)}{r.skip_reason ? `·${String(r.skip_reason)}` : ""}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-neutral-500 text-[12px]">{String(r.duration_ms ?? 0)}ms</td>
                      <td className="px-4 py-3 text-[12px] text-neutral-500"><code className="break-all">{JSON.stringify(r.action_results)}</code></td>
                    </tr>
                  ))}
                  {runs.length === 0 && (
                    <tr><td colSpan={6} className="px-4 py-12 text-center text-neutral-400">暂无执行日志（规则命中后写入 automation_runs，90 天留存）</td></tr>
                  )}
                </tbody>
              </table>
            )}
          </div>
        </main>
      </div>

      {/* 三段式规则编辑器 */}
      {createOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/25" data-sb-scope="rule-create-dialog">
          <div className="w-[640px] max-h-[85vh] overflow-auto bg-white rounded-xl shadow-2xl p-5">
            <h2 className="text-sm font-semibold mb-4">新建自动化规则（三段式 · 服务端校验兜底）</h2>
            <label className="block text-[13px] text-neutral-600 mb-3">
              规则名称
              <input type="text" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })}
                placeholder="如：高优需求提级" className={inputCls} />
            </label>
            <div className="text-[12px] font-semibold text-neutral-500 mb-1.5">① 触发器</div>
            <div className="flex gap-2 mb-4">
              <select value={f.trig} onChange={(e) => setF({ ...f, trig: e.target.value })}
                className="h-8 border border-neutral-200 rounded-md px-2 text-[13px]">
                {TRIGGER_FIELDS.map((t) => <option key={t} value={t}>{TRIGGER_LABEL[t]}</option>)}
              </select>
              {f.trig === "state_changed" && (
                <select value={f.to_group} onChange={(e) => setF({ ...f, to_group: e.target.value })}
                  className="h-8 border border-neutral-200 rounded-md px-2 text-[13px]">
                  {GROUPS.map((g) => <option key={g} value={g}>迁入 {g}</option>)}
                </select>
              )}
              {f.trig === "field_changed" && (
                <input type="text" value={f.field} onChange={(e) => setF({ ...f, field: e.target.value })}
                  placeholder="字段名（如 priority）" className="flex-1 h-8 border border-neutral-200 rounded-md px-2 text-[13px]" />
              )}
              {f.trig === "due_approaching" && (
                <input type="number" value={f.lead_days} onChange={(e) => setF({ ...f, lead_days: e.target.value })}
                  className="w-24 h-8 border border-neutral-200 rounded-md px-2 text-[13px]" />
              )}
            </div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-[12px] font-semibold text-neutral-500">② 条件（可选，全部满足）</span>
              <button type="button" onClick={() => setF({ ...f, conds: [...f.conds, { field: "priority", operator: "eq", value: "high" }] })}
                className="text-[12px] text-brand-600">＋ 条件</button>
            </div>
            <div className="space-y-2 mb-4">
              {f.conds.map((c, i) => (
                <div key={i} className="flex gap-2 items-center">
                  <select value={c.field} onChange={(e) => setF({ ...f, conds: f.conds.map((x, j) => j === i ? { ...x, field: e.target.value } : x) })}
                    className="h-8 border border-neutral-200 rounded-md px-2 text-[13px]">
                    {COND_FIELDS.map((x) => <option key={x} value={x}>{x}</option>)}
                  </select>
                  <select value={c.operator} onChange={(e) => setF({ ...f, conds: f.conds.map((x, j) => j === i ? { ...x, operator: e.target.value } : x) })}
                    className="h-8 border border-neutral-200 rounded-md px-2 text-[13px]">
                    {COND_OPS.map((x) => <option key={x} value={x}>{x}</option>)}
                  </select>
                  <input type="text" value={c.value} placeholder={c.operator === "in" ? "逗号分隔多值" : "值"}
                    onChange={(e) => setF({ ...f, conds: f.conds.map((x, j) => j === i ? { ...x, value: e.target.value } : x) })}
                    className="flex-1 h-8 border border-neutral-200 rounded-md px-2 text-[13px]" />
                  <button type="button" onClick={() => setF({ ...f, conds: f.conds.filter((_, j) => j !== i) })}
                    className="text-neutral-400 hover:text-rose-500">✕</button>
                </div>
              ))}
            </div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-[12px] font-semibold text-neutral-500">③ 动作（顺序执行，≤5）</span>
              <button type="button" onClick={() => setF({ ...f, acts: [...f.acts, { type: "set_field", field: "priority", value: "urgent", to_state_id: "", user_id: "", label_id: "", title: "", targets: "assignees" }] })}
                className="text-[12px] text-brand-600">＋ 动作</button>
            </div>
            <div className="space-y-2 mb-5">
              {f.acts.map((a, i) => (
                <div key={i} className="flex gap-2 items-center flex-wrap border border-neutral-100 rounded-md p-2">
                  <select value={a.type} onChange={(e) => setAct(i, { type: e.target.value })}
                    className="h-8 border border-neutral-200 rounded-md px-2 text-[13px]">
                    {ACTION_FIELDS.map((x) => <option key={x} value={x}>{ACTION_LABEL[x]}</option>)}
                  </select>
                  {a.type === "set_field" && (<>
                    <select value={a.field} onChange={(e) => setAct(i, { field: e.target.value })}
                      className="h-8 border border-neutral-200 rounded-md px-2 text-[13px]">
                      <option value="priority">priority</option><option value="estimate_minutes">estimate_minutes</option>
                      <option value="target_date">target_date</option><option value="start_date">start_date</option>
                    </select>
                    {a.field === "priority"
                      ? <select value={a.value} onChange={(e) => setAct(i, { value: e.target.value })} className="h-8 border border-neutral-200 rounded-md px-2 text-[13px]">
                          {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
                        </select>
                      : <input type="text" value={a.value} onChange={(e) => setAct(i, { value: e.target.value })} placeholder="值"
                          className="h-8 border border-neutral-200 rounded-md px-2 text-[13px]" />}
                  </>)}
                  {a.type === "transition" && (
                    <select value={a.to_state_id} onChange={(e) => setAct(i, { to_state_id: e.target.value })}
                      className="h-8 border border-neutral-200 rounded-md px-2 text-[13px]">
                      <option value="">选目标状态…</option>
                      {states.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                    </select>
                  )}
                  {a.type === "assign" && (
                    <select value={a.user_id} onChange={(e) => setAct(i, { user_id: e.target.value })}
                      className="h-8 border border-neutral-200 rounded-md px-2 text-[13px]">
                      <option value="">选成员…</option>
                      {members.map((m) => <option key={m.user.id} value={m.user.id}>{m.user.display_name}</option>)}
                    </select>
                  )}
                  {a.type === "add_label" && (
                    <select value={a.label_id} onChange={(e) => setAct(i, { label_id: e.target.value })}
                      className="h-8 border border-neutral-200 rounded-md px-2 text-[13px]">
                      <option value="">选标签…</option>
                      {labels.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                    </select>
                  )}
                  {a.type === "notify" && (<>
                    <input type="text" value={a.title} onChange={(e) => setAct(i, { title: e.target.value })} placeholder="通知标题"
                      className="flex-1 min-w-[140px] h-8 border border-neutral-200 rounded-md px-2 text-[13px]" />
                    <select value={a.targets} onChange={(e) => setAct(i, { targets: e.target.value })}
                      className="h-8 border border-neutral-200 rounded-md px-2 text-[13px]">
                      <option value="assignees">负责人</option><option value="reporter">创建人</option>
                    </select>
                  </>)}
                  <button type="button" onClick={() => setF({ ...f, acts: f.acts.filter((_, j) => j !== i) })}
                    className="text-neutral-400 hover:text-rose-500 ml-auto">✕</button>
                </div>
              ))}
            </div>
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => setCreateOpen(false)} className="h-8 px-3 rounded-md border border-neutral-200 text-sm">取消</button>
              <button type="button" disabled={busy || !f.name.trim()} onClick={() => void create()}
                className="h-8 px-3 rounded-md bg-brand-600 text-white text-sm disabled:opacity-50">创建规则</button>
            </div>
          </div>
        </div>
      )}

      {/* Dry Run 弹层（§2.4：0 写预演） */}
      {dryFor && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/25" data-sb-scope="dryrun-dialog">
          <div className="w-[520px] max-h-[80vh] overflow-auto bg-white rounded-xl shadow-2xl p-5">
            <h2 className="text-sm font-semibold mb-1">Dry Run「{dryFor.name}」</h2>
            <p className="text-[12px] text-neutral-400 mb-3">选一个任务样本预演——不写库（BR-11），回显将命中/将执行的动作</p>
            <input type="text" value={dryQ} placeholder="搜索任务名…"
              onChange={(e) => { setDryQ(e.target.value); void searchIssues(e.target.value); }}
              className="w-full h-9 border border-neutral-300 rounded-md px-3 text-sm" />
            {dryIssues.length > 0 && !dryResult && (
              <div className="mt-2 border border-neutral-200 rounded-md divide-y divide-neutral-100 max-h-[200px] overflow-auto">
                {dryIssues.map((i) => (
                  <button key={i.id} type="button" onClick={() => void runDry(i.id)}
                    className="w-full text-left px-3 py-2 text-[13px] hover:bg-brand-50">
                    <span className="font-mono text-[12px] text-neutral-400 mr-2">{i.issue_key}</span>{i.name}
                  </button>
                ))}
              </div>
            )}
            {dryResult && (
              <pre className="mt-3 text-[12px] bg-neutral-50 border border-neutral-200 rounded-md p-3 whitespace-pre-wrap"
                data-sb-scope="dryrun-result">{JSON.stringify(dryResult, null, 2)}</pre>
            )}
            <div className="flex justify-end mt-4">
              <button type="button" onClick={() => setDryFor(null)} className="h-8 px-3 rounded-md border border-neutral-200 text-sm">关闭</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
