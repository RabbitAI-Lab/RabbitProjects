import { useState } from "react";
import { api } from "../lib/api";

/** 治理域共享弹窗（AUTH-012——冻结原型 M-DISPOSE/M-FREEZE/M-L2 落地）。
 *
 * 处置三选 + 误报关闭（actions/）；冻结=第一签（不置位，待异人二签）；
 * 二签/解除二签（freeze-approval/，同人 403 由 API 透出）；L2 工单发起。 */

export interface EventRow {
  id: string; rule_code: string; severity: string; status: string;
  tenant: { id: string; name: string };
  evidence: Record<string, unknown>;
  actions: Array<{ action: string; by: string; at: string; note?: string }>;
  freeze_approval: { pending?: string; initiator_name?: string } | null;
  created_at: string;
}

const SEVERITY_LABEL: Record<string, string> = { high: "🔴 高", medium: "🟡 中", low: "⚪ 低" };

function Mask({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 bg-black/30 backdrop-blur-[2px] flex items-center justify-center p-4"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }} data-sb-scope="gov-modal">
      <div className="bg-white rounded-xl shadow-2xl w-[520px] max-w-full max-h-[88vh] overflow-auto p-6">
        {children}
      </div>
    </div>
  );
}

/** 处置弹窗（§3.3 线框：证据快照 + 三档处置 + BR-07 L2 提示）。 */
export function DisposeModal({ event, onClose, onDone }: {
  event: EventRow; onClose: () => void; onDone: () => void;
}) {
  const [pick, setPick] = useState("alert");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function submit() {
    setBusy(true); setErr("");
    const r = await api<{ freeze_pending: boolean }>(
      "POST", `/instances/risk-events/${event.id}/actions/`,
      { action: pick, note });
    setBusy(false);
    if (r.status !== "success") { setErr(r.error?.message ?? "处置失败"); return; }
    onDone();
    onClose();
  }

  const opts = [
    { k: "alert", t: "仅告警", d: "记录处置备注，不限制租户功能" },
    { k: "throttle", t: "限流 24h", d: "API 速率临时降至配额 10% · 到期自动恢复 · 可提前解除（BR-03）" },
    { k: "freeze", t: "冻结租户", d: "拒绝全部新写操作 · 15 分钟缓冲后全只读（BR-09）· 数据完整保留", tag: "需双人审批" },
    { k: "dismiss", t: "误报关闭", d: "事件置 dismissed（客户申诉 accepted 同效）" },
  ];

  return (
    <Mask onClose={onClose}>
      <div className="flex items-center justify-between mb-4">
        <div className="text-base font-semibold">风控事件 {event.id.slice(0, 8)}…
          <span className="ml-2 text-[13px] text-red-600">{SEVERITY_LABEL[event.severity]}</span>
        </div>
        <button onClick={onClose} className="w-7 h-7 rounded-md text-neutral-400 hover:bg-neutral-100">✕</button>
      </div>
      <div className="grid grid-cols-[88px_1fr] gap-x-2.5 gap-y-1 text-[13px] mb-3">
        <span className="text-neutral-400">规则</span><span><b className="font-mono text-[12px] px-1.5 py-0.5 rounded bg-red-500 text-white">{event.rule_code}</b></span>
        <span className="text-neutral-400">租户</span><span>{event.tenant.name}</span>
      </div>
      <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-3 my-3">
        <div className="text-[12px] font-semibold text-neutral-500 mb-2">证据快照（BR-05 · 触发即固化）</div>
        <ul className="text-[12.5px] text-neutral-600 font-mono space-y-0.5">
          {Object.entries(event.evidence).slice(0, 6).map(([k, v]) => (
            <li key={k}>· {k}: {String(v)}</li>
          ))}
        </ul>
      </div>
      <div className="text-[13px] font-medium text-neutral-500 mb-1.5">处置</div>
      {opts.map((o) => (
        <label key={o.k} onClick={() => setPick(o.k)}
          className={`flex items-start gap-2 px-3 py-2.5 rounded-lg border mb-2 cursor-pointer transition-colors
            ${pick === o.k ? "border-brand-500 bg-brand-50" : "border-neutral-300 hover:border-brand-400"}`}>
          <input type="radio" checked={pick === o.k} onChange={() => setPick(o.k)} className="mt-1" />
          <div>
            <div className="text-[13px] font-medium">{o.t}
              {o.tag && <span className="ml-1.5 px-1.5 py-px rounded border border-amber-300 bg-amber-50 text-amber-700 text-[11px]">{o.tag}</span>}
            </div>
            <div className="text-[11.5px] text-neutral-400">{o.d}</div>
          </div>
        </label>
      ))}
      <div className="text-[13px] font-medium text-neutral-500 mt-3 mb-1.5">备注</div>
      <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} placeholder="处置说明（落审计）"
        className="w-full rounded-md border border-neutral-300 p-2 text-[13px] resize-y" />
      <div className="mt-3 px-3 py-2 rounded-lg border border-neutral-200 text-[12px] text-neutral-400 flex items-center gap-2">
        🔒 平台最小知情（BR-07）：业务内容不可见。
        <button className="ml-auto h-7 px-2.5 rounded-md border border-neutral-300 text-[12px] text-neutral-600 hover:bg-neutral-50"
          onClick={onClose}
          data-sb-scope="l2-open">申请 L2 授权工单 →</button>
      </div>
      {err && <div className="mt-2 text-[12.5px] text-red-600" role="alert">{err}</div>}
      <div className="flex justify-end gap-2.5 mt-5">
        <button onClick={onClose} className="h-8.5 px-3.5 h-9 rounded-md border border-neutral-300 text-[13px] text-neutral-600 hover:bg-neutral-50">取消</button>
        <button onClick={submit} disabled={busy} data-sb-scope="dispose-submit"
          className="h-9 px-3.5 rounded-md bg-brand-500 text-white text-[13px] disabled:opacity-50">
          {busy ? "执行中…" : "执行处置"}
        </button>
      </div>
    </Mask>
  );
}

/** 冻结/解除二签（§3.4 BR-04：另一 tenant_ops 登录后操作；同人 403 由 API 透出）。 */
export function FreezeApprovalModal({ event, onClose, onDone }: {
  event: EventRow; onClose: () => void; onDone: () => void;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const pending = event.freeze_approval?.pending ?? "";

  async function approve() {
    setBusy(true); setErr("");
    const r = await api<{ is_frozen: boolean }>(
      "POST", `/instances/risk-events/${event.id}/freeze-approval/`, { note });
    setBusy(false);
    if (r.status !== "success") { setErr(r.error?.message ?? "二签失败"); return; }
    onDone(); onClose();
  }

  return (
    <Mask onClose={onClose}>
      <div className="flex items-center justify-between mb-1">
        <div className="text-base font-semibold">
          {pending === "release" ? "解除冻结 · 第二签" : "冻结租户 · 第二签"}（BR-04）
        </div>
        <button onClick={onClose} className="w-7 h-7 rounded-md text-neutral-400 hover:bg-neutral-100">✕</button>
      </div>
      <div className="text-[12.5px] text-neutral-400 mb-4">
        发起人：{event.freeze_approval?.initiator_name ?? "—"} · 二签人不得与发起人相同（同人 403）
      </div>
      <div className="grid grid-cols-[88px_1fr] gap-x-2.5 gap-y-1 text-[13px] mb-3">
        <span className="text-neutral-400">目标租户</span><span>{event.tenant.name}</span>
        <span className="text-neutral-400">规则</span><span className="font-mono">{event.rule_code}</span>
        <span className="text-neutral-400">签署动作</span>
        <span>{pending === "release" ? "解除冻结（恢复读写）" : "冻结（拒新写 · 15 分钟缓冲后全只读）"}</span>
      </div>
      <div className="text-[13px] font-medium text-neutral-500 mb-1.5">二签备注（落审计）</div>
      <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2}
        className="w-full rounded-md border border-neutral-300 p-2 text-[13px] resize-y" />
      {err && <div className="mt-2 text-[12.5px] text-red-600" role="alert">{err}</div>}
      <div className="flex justify-end gap-2.5 mt-5">
        <button onClick={onClose} className="h-9 px-3.5 rounded-md border border-neutral-300 text-[13px] text-neutral-600 hover:bg-neutral-50">取消</button>
        <button onClick={approve} disabled={busy} data-sb-scope="freeze-approve"
          className={`h-9 px-3.5 rounded-md text-white text-[13px] disabled:opacity-50
            ${pending === "release" ? "bg-green-600 hover:bg-green-700" : "bg-red-500 hover:bg-red-600"}`}>
          {busy ? "签署中…" : pending === "release" ? "确认解除" : "确认冻结"}
        </button>
      </div>
    </Mask>
  );
}

/** L2 授权工单发起（§2.5：对象 ID 清单 + 理由；批准后 24h 有效）。 */
export function L2TicketModal({ event, onClose, onDone }: {
  event: EventRow; onClose: () => void; onDone: (msg: string) => void;
}) {
  const [ids, setIds] = useState(String(event.evidence.actor_id ?? ""));
  const [reason, setReason] = useState(`${event.rule_code} 事件复核需确认对象清单`);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function submit() {
    setBusy(true); setErr("");
    const r = await api<{ ticket_id: string }>("POST", "/instances/l2-tickets/", {
      risk_event_id: event.id,
      scope: { fields: ["issue.title", "file.name"], ids: ids.split(/[\n,]/).map((s) => s.trim()).filter(Boolean) },
      approve_channel: "online",
    });
    setBusy(false);
    if (r.status !== "success") { setErr(r.error?.message ?? "提交失败"); return; }
    onDone(`工单 ${r.data?.ticket_id.slice(0, 8)}… 已提交客户 WS_ADMIN 审批`);
    onClose();
  }

  return (
    <Mask onClose={onClose}>
      <div className="flex items-center justify-between mb-1">
        <div className="text-base font-semibold">申请 L2 授权工单</div>
        <button onClick={onClose} className="w-7 h-7 rounded-md text-neutral-400 hover:bg-neutral-100">✕</button>
      </div>
      <div className="text-[12.5px] text-neutral-400 mb-4">L1 行为统计与 ID（默认）→ L2 业务内容（工单）→ L3 正文永不（§2.5）</div>
      <div className="text-[13px] font-medium text-neutral-500 mb-1.5">申请对象（ID 清单）</div>
      <textarea value={ids} onChange={(e) => setIds(e.target.value)} rows={3}
        className="w-full rounded-md border border-neutral-300 p-2 text-[12px] font-mono resize-y" />
      <div className="text-[13px] font-medium text-neutral-500 mt-3 mb-1.5">理由</div>
      <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2}
        className="w-full rounded-md border border-neutral-300 p-2 text-[13px] resize-y" />
      <div className="mt-2.5 text-[12px] text-neutral-400">
        生效条件：客户 WS_ADMIN 在线批准或书面授权工单号；授权 24h 有效，访问全程审计。
      </div>
      {err && <div className="mt-2 text-[12.5px] text-red-600" role="alert">{err}</div>}
      <div className="flex justify-end gap-2.5 mt-5">
        <button onClick={onClose} className="h-9 px-3.5 rounded-md border border-neutral-300 text-[13px] text-neutral-600 hover:bg-neutral-50">取消</button>
        <button onClick={submit} disabled={busy} data-sb-scope="l2-submit"
          className="h-9 px-3.5 rounded-md bg-brand-500 text-white text-[13px] disabled:opacity-50">
          {busy ? "提交中…" : "提交工单"}
        </button>
      </div>
    </Mask>
  );
}
