import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { api } from "../lib/api";
import { DisposeModal, FreezeApprovalModal, L2TicketModal, type EventRow } from "../components/governance-modals";

/** 风控事件中心（AUTH-012 §3.1 行 3——冻结原型 V-RISK：四筛选 + 事件流 +
 * 处置/二签/L2 三弹窗 + BR-08 聚合提示条）。 */
const SEVERITY_LABEL: Record<string, string> = { high: "🔴 高", medium: "🟡 中", low: "⚪ 低" };
const STATUS_LABEL: Record<string, string> = { open: "未处置", actioned: "已处置", dismissed: "误报关闭" };

export default function GovernanceRiskEvents() {
  const [rows, setRows] = useState<EventRow[]>([]);
  const [msg, setMsg] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [rule, setRule] = useState("");
  const [status, setStatus] = useState("");
  const [severity, setSeverity] = useState("");
  const [dispose, setDispose] = useState<EventRow | null>(null);
  const [approval, setApproval] = useState<EventRow | null>(null);
  const [l2, setL2] = useState<EventRow | null>(null);

  const load = useCallback(() => {
    const q = new URLSearchParams();
    if (rule) q.set("rule", rule);
    if (status) q.set("status", status);
    if (severity) q.set("severity", severity);
    api<EventRow[]>("GET", `/instances/risk-events/${q.size ? `?${q}` : ""}`).then((r) => {
      setLoaded(true);
      if (r.status === "success" && r.data) setRows(r.data);
      else setMsg(r.error?.message ?? "加载失败");
    });
  }, [rule, status, severity]);

  useEffect(load, [load]);

  const openCount = rows.filter((e) => e.status === "open").length;

  return (
    <section data-sb-scope="risk-events-page">
      <div className="flex items-center gap-3 mb-1">
        <h1 className="text-lg font-semibold">风控事件中心</h1>
        {openCount > 0 && (
          <span className="h-6 px-2.5 inline-flex items-center rounded-full bg-red-50 border border-red-200 text-red-600 text-[12px]">
            {openCount} 未处置
          </span>
        )}
        <div className="ml-auto flex gap-2">
          <select value={rule} onChange={(e) => setRule(e.target.value)}
            className="h-8 rounded-md border border-neutral-300 px-2 text-[12.5px] bg-white">
            <option value="">规则：全部</option>
            {["R-01", "R-02", "R-03", "R-04", "R-05", "R-06"].map((r) => <option key={r}>{r}</option>)}
          </select>
          <select value={severity} onChange={(e) => setSeverity(e.target.value)}
            className="h-8 rounded-md border border-neutral-300 px-2 text-[12.5px] bg-white">
            <option value="">级别：全部</option><option value="high">🔴 高</option>
            <option value="medium">🟡 中</option><option value="low">⚪ 低</option>
          </select>
          <select value={status} onChange={(e) => setStatus(e.target.value)}
            className="h-8 rounded-md border border-neutral-300 px-2 text-[12.5px] bg-white">
            <option value="">状态：全部</option><option value="open">未处置</option>
            <option value="actioned">已处置</option><option value="dismissed">误报关闭</option>
          </select>
        </div>
      </div>
      <p className="text-[12px] text-neutral-400 mb-4">
        平台最小知情（BR-07）：默认仅行为统计与 ID，业务内容需 L2 工单
      </p>

      <div className="rounded-lg border border-neutral-200 bg-white shadow-sm" data-sb-scope="event-list">
        {rows.map((ev) => (
          <div key={ev.id} className={`flex items-center gap-3 px-3.5 py-2.5 border-b border-neutral-100 last:border-0 hover:bg-neutral-50 ${ev.status !== "open" ? "opacity-70" : ""}`}>
            <span className="font-mono text-[11.5px] text-neutral-400 w-24 shrink-0">{ev.id.slice(0, 13)}…</span>
            <span className={`font-mono text-[11px] px-1.5 py-0.5 rounded shrink-0 ${ev.status === "open" ? "bg-red-500 text-white" : "bg-neutral-100 text-neutral-400"}`}>
              {ev.rule_code}
            </span>
            <Link to={`/governance/tenants/${ev.tenant.id}`} className="font-medium text-[13px] text-brand-600 no-underline hover:underline shrink-0">
              {ev.tenant.name}
            </Link>
            <span className="flex-1 min-w-0 truncate text-[12px] text-neutral-500">
              {Object.entries(ev.evidence).filter(([k]) => k !== "occurrences").slice(0, 2)
                .map(([k, v]) => `${k}=${String(v)}`).join(" · ")}
              {ev.evidence.occurrences ? ` · ×${ev.evidence.occurrences}` : ""}
            </span>
            <span className="text-[12px] shrink-0">{SEVERITY_LABEL[ev.severity] ?? ev.severity}</span>
            <span className="text-[12px] text-neutral-400 shrink-0">{STATUS_LABEL[ev.status] ?? ev.status}</span>
            <span className="flex gap-2 shrink-0">
              {ev.freeze_approval?.pending && (
                <button onClick={() => setApproval(ev)} data-sb-scope="freeze-second-sign"
                  className="h-7 px-2.5 rounded-md bg-red-500 text-white text-[12px]">
                  {ev.freeze_approval.pending === "release" ? "解除二签" : "冻结二签"}
                </button>
              )}
              {ev.status === "actioned" && !ev.freeze_approval?.pending && (
                <button onClick={async () => {
                  const r = await api<{ pending_second_sign: boolean }>(
                    "POST", `/instances/risk-events/${ev.id}/releases/`, { note: "" });
                  if (r.status !== "success") { setMsg(r.error?.message ?? "解除失败"); return; }
                  setMsg(r.data?.pending_second_sign
                    ? "解除已发起——需另一运营二签生效（BR-03 同审批级）"
                    : "处置已解除（即时生效）");
                  load();
                }} data-sb-scope="release-open"
                  className="h-7 px-2.5 rounded-md border border-green-500 text-green-700 text-[12px] hover:bg-green-50">
                  解除
                </button>
              )}
              {ev.status === "open" && (
                <>
                  <button onClick={() => setL2(ev)}
                    className="h-7 px-2.5 rounded-md border border-neutral-300 text-[12px] text-neutral-600 hover:bg-neutral-50">L2 工单</button>
                  <button onClick={() => setDispose(ev)} data-sb-scope="dispose-open"
                    className="h-7 px-2.5 rounded-md bg-brand-500 text-white text-[12px]">处置</button>
                </>
              )}
            </span>
          </div>
        ))}
        {loaded && !rows.length && !msg && (
          <div className="py-10 text-center text-[13px] text-neutral-400">无匹配事件</div>
        )}
        {msg && <div className="py-8 text-center text-[13px] text-red-500" role="alert">{msg}</div>}
      </div>

      <div className="mt-3.5 px-3.5 h-10 flex items-center rounded-lg border border-neutral-200 bg-white text-[12.5px] text-neutral-600">
        <span>同一租户同一规则同一主体 UTC 小时桶内聚合告警（BR-08）</span>
        <span className="ml-auto text-[11.5px] text-neutral-400">CSV 台账导出（§2.6 · 随 R1+ 边界报告台账交付）</span>
      </div>

      {dispose && <DisposeModal event={dispose} onClose={() => setDispose(null)} onDone={load} />}
      {approval && <FreezeApprovalModal event={approval} onClose={() => setApproval(null)} onDone={load} />}
      {l2 && <L2TicketModal event={l2} onClose={() => setL2(null)} onDone={(m) => { setMsg(m); }} />}
    </section>
  );
}
