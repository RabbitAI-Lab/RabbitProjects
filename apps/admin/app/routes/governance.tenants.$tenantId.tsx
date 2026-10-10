import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { api } from "../lib/api";
import { DisposeModal, FreezeApprovalModal, type EventRow } from "../components/governance-modals";

/** 租户详情（AUTH-012 §3.1 行 2——冻结原型 V-TENANT：配额/下挂 WS/事件/处置）。 */
interface TenantDetail {
  id: string; name: string; tier: string; seats: number;
  frozen: { is_frozen: boolean; frozen_at: string | null; reason: string };
  water: {
    storage: { used: number; limit: number; ratio: number | null };
    api_rate_per_minute: number; open_risk_events: number;
    is_frozen: boolean; is_throttled: boolean;
  };
  workspaces: Array<{ id: string; name: string; slug: string }>;
  recent_events: EventRow[];
}

const PLAN_LABEL: Record<string, string> = { free: "免费", standard: "标准", enterprise: "企业", flagship: "旗舰" };

function fmtBytes(n: number): string {
  if (!n) return "0";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0; let v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

export default function GovernanceTenantDetail() {
  const { tenantId } = useParams();
  const [t, setT] = useState<TenantDetail | null>(null);
  const [msg, setMsg] = useState("");
  const [quotaOpen, setQuotaOpen] = useState(false);
  const [dispose, setDispose] = useState<EventRow | null>(null);
  const [approval, setApproval] = useState<EventRow | null>(null);

  const load = useCallback(() => {
    if (!tenantId) return;
    api<TenantDetail>("GET", `/instances/tenants/${tenantId}/`).then((r) => {
      if (r.status === "success" && r.data) setT(r.data);
      else setMsg(r.error?.message ?? "加载失败");
    });
  }, [tenantId]);

  useEffect(load, [load]);

  async function boundaryReport() {
    if (!tenantId) return;
    const r = await api<{ state: string; status_url: string }>(
      "POST", `/instances/tenants/${tenantId}/boundary-reports/`, {});
    setMsg(r.status === "success"
      ? "报告任务已创建（202）——完成后经状态端点取 1h 预签名下载链接"
      : r.error?.message ?? "创建失败");
  }

  if (!t) return <div className="py-16 text-center text-neutral-400" role="status">{msg || "加载中…"}</div>;

  return (
    <section data-sb-scope="tenant-detail">
      <div className="flex items-center gap-3 mb-4">
        <Link to="/governance" className="h-7 px-2.5 rounded-md border border-neutral-300 text-[12px] text-neutral-600 no-underline hover:bg-neutral-100 leading-7">← 返回</Link>
        <h1 className="text-[17px] font-semibold">{t.name}</h1>
        <span className="px-2 py-0.5 rounded bg-brand-100 text-brand-600 text-[11px]">{PLAN_LABEL[t.tier] ?? t.tier}</span>
        {t.frozen.is_frozen && (
          <span className="inline-flex items-center gap-1.5 text-[12.5px] text-red-600">
            <span className="w-2 h-2 rounded-full bg-red-500" />冻结中
          </span>
        )}
        <div className="ml-auto flex gap-2">
          <button onClick={() => setQuotaOpen(true)} data-sb-scope="quota-open"
            className="h-8 px-3 rounded-md border border-neutral-300 text-[12.5px] text-neutral-600 hover:bg-neutral-50">⚙ 配额调整</button>
          <button onClick={boundaryReport} data-sb-scope="boundary-create"
            className="h-8 px-3 rounded-md border border-neutral-300 text-[12.5px] text-neutral-600 hover:bg-neutral-50">🧾 生成边界报告</button>
        </div>
      </div>
      {msg && <div className="mb-3 text-[12.5px] text-neutral-500" role="status">{msg}</div>}

      <div className="grid grid-cols-3 gap-3 mb-4">
        <div className="rounded-lg border border-neutral-200 bg-white p-3.5 shadow-sm">
          <div className="text-[12px] text-neutral-500 mb-1.5 flex items-center">
            存储（两层模型 §2.3）
            <span className="ml-auto text-neutral-400">租户硬上限</span>
          </div>
          <div className="text-[19px] font-semibold font-mono">
            {fmtBytes(t.water.storage.used)} <span className="text-[11px] font-sans text-neutral-400">/ {fmtBytes(t.water.storage.limit)}</span>
          </div>
        </div>
        <div className="rounded-lg border border-neutral-200 bg-white p-3.5 shadow-sm">
          <div className="text-[12px] text-neutral-500 mb-1.5">API 请求速率</div>
          <div className="text-[19px] font-semibold font-mono">
            {t.water.api_rate_per_minute.toLocaleString()} <span className="text-[11px] font-sans text-neutral-400">req/min</span>
          </div>
          <div className="mt-1 text-[11.5px] text-neutral-400">{t.water.is_throttled ? "限流处置中（降至 10%）" : "正常"}</div>
        </div>
        <div className="rounded-lg border border-neutral-200 bg-white p-3.5 shadow-sm">
          <div className="text-[12px] text-neutral-500 mb-1.5">下挂工作空间 / 未处置事件</div>
          <div className="text-[19px] font-semibold font-mono">
            {t.workspaces.length} <span className="text-[11px] font-sans text-neutral-400">个 WS</span>
            <span className="ml-3 text-red-600">{t.water.open_risk_events}</span>
            <span className="text-[11px] font-sans text-neutral-400"> 事件</span>
          </div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {t.workspaces.slice(0, 4).map((w) => (
              <span key={w.id} className="font-mono text-[11px] px-1.5 py-px rounded bg-neutral-100 text-neutral-600">{w.slug}</span>
            ))}
          </div>
        </div>
      </div>

      <div className="rounded-lg border border-neutral-200 bg-white shadow-sm">
        <div className="px-3.5 py-3 border-b border-neutral-200 flex items-center gap-2.5">
          <b className="text-[13.5px]">本租户风控事件</b>
          <span className="text-[12px] text-neutral-400">证据快照按 BR-05 固化</span>
        </div>
        {t.recent_events.map((ev) => (
          <div key={ev.id} className="flex items-center gap-3 px-3.5 py-2.5 border-b border-neutral-100 last:border-0 hover:bg-neutral-50">
            <span className="font-mono text-[11.5px] text-neutral-400">{ev.id.slice(0, 13)}…</span>
            <span className={`font-mono text-[11px] px-1.5 py-0.5 rounded ${ev.status === "open" ? "bg-red-500 text-white" : "bg-neutral-100 text-neutral-400"}`}>{ev.rule_code}</span>
            <span className="flex-1 min-w-0 truncate text-[12.5px] text-neutral-500">
              {String(ev.evidence.rows_used ?? ev.evidence.failed_logins ?? ev.evidence.total_calls ?? "")}
              {ev.status === "dismissed" ? " · 误报关闭" : ""}
            </span>
            {ev.freeze_approval?.pending && (
              <button onClick={() => setApproval(ev)} data-sb-scope="freeze-second-sign"
                className="h-7 px-2.5 rounded-md bg-red-500 text-white text-[12px]">
                {ev.freeze_approval.pending === "release" ? "解除二签" : "冻结二签"}
              </button>
            )}
            {ev.status === "open" && (
              <button onClick={() => setDispose(ev)} data-sb-scope="dispose-open"
                className="h-7 px-2.5 rounded-md bg-brand-500 text-white text-[12px]">处置</button>
            )}
          </div>
        ))}
        {!t.recent_events.length && <div className="py-8 text-center text-[13px] text-neutral-400">无事件</div>}
      </div>

      {dispose && <DisposeModal event={dispose} onClose={() => setDispose(null)} onDone={load} />}
      {approval && <FreezeApprovalModal event={approval} onClose={() => setApproval(null)} onDone={load} />}
      {quotaOpen && tenantId && (
        <QuotaModal tenant={t} onClose={() => setQuotaOpen(false)}
          onDone={(m) => { setMsg(m); load(); }} />
      )}
    </section>
  );
}

/** 配额调整（§2.3 档位 + BR-12 审计提示）。 */
function QuotaModal({ tenant, onClose, onDone }: {
  tenant: TenantDetail; onClose: () => void; onDone: (msg: string) => void;
}) {
  const [tier, setTier] = useState(tenant.tier);
  const [storage, setStorage] = useState(Math.round(tenant.water.storage.limit / 2**30));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function save() {
    setBusy(true); setErr("");
    const r = await api("PATCH", `/instances/tenants/${tenant.id}/quota/`, {
      tier, storage_bytes: storage * 2**30,
    });
    setBusy(false);
    if (r.status !== "success") { setErr(r.error?.message ?? "保存失败"); return; }
    onDone("配额变更已生效并落审计（BR-12）");
    onClose();
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-4"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }} data-sb-scope="quota-modal">
      <div className="bg-white rounded-xl shadow-2xl w-[480px] max-w-full p-6">
        <div className="flex items-center justify-between mb-4">
          <div className="text-base font-semibold">配额调整 · {tenant.name}</div>
          <button onClick={onClose} className="w-7 h-7 rounded-md text-neutral-400 hover:bg-neutral-100">✕</button>
        </div>
        <div className="text-[13px] font-medium text-neutral-500 mb-1.5">套餐档位</div>
        <div className="flex gap-1 p-1 rounded-lg bg-neutral-100 mb-3.5">
          {["free", "standard", "enterprise", "flagship"].map((k) => (
            <button key={k} onClick={() => setTier(k)}
              className={`h-8 flex-1 rounded-md text-[12.5px] ${tier === k ? "bg-white shadow-sm font-medium" : "text-neutral-500"}`}>
              {PLAN_LABEL[k]}
            </button>
          ))}
        </div>
        <div className="text-[13px] font-medium text-neutral-500 mb-1.5">存储硬上限（GB）</div>
        <input type="number" value={storage} onChange={(e) => setStorage(Number(e.target.value))}
          className="w-40 h-9 rounded-md border border-neutral-300 px-2.5 font-mono text-[13px]" />
        <div className="mt-1.5 text-[12px] text-neutral-400">升级即时生效；降级 30 天宽限期（只告警不硬拒）——§2.3</div>
        <div className="mt-3 px-3 py-2.5 rounded-lg bg-blue-50 border border-blue-200 text-[12px] text-blue-700">
          任何配额调整产生 AuditLog（新旧值 + 操作主体）· BR-12
        </div>
        {err && <div className="mt-2 text-[12.5px] text-red-600" role="alert">{err}</div>}
        <div className="flex justify-end gap-2.5 mt-5">
          <button onClick={onClose} className="h-9 px-3.5 rounded-md border border-neutral-300 text-[13px] text-neutral-600 hover:bg-neutral-50">取消</button>
          <button onClick={save} disabled={busy} data-sb-scope="quota-save"
            className="h-9 px-3.5 rounded-md bg-brand-500 text-white text-[13px] disabled:opacity-50">{busy ? "保存中…" : "保存"}</button>
        </div>
      </div>
    </div>
  );
}
