/**
 * 我的租户安全（AUTH-012 §3.1 行 5——冻结原型 V-SEC：配额水位 + 阈值调紧
 * （BR-06 只紧不松）+ 本租户事件（误报申诉）+ L2 工单批准 + 审计包导出）。
 * 权限：WS_ADMIN（后端 audit.read / setting.manage / l2.approve 同档判定）。
 */
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { SystemSidebar } from "../components/SystemSidebar";
import { toast } from "../components/Toast";
import { SecurityAPI, SiteAuditAPI } from "../services/api";

interface Water {
  storage: { used: number; limit: number; ratio: number | null };
  api_rate_per_minute: number;
  open_risk_events: number; is_frozen: boolean; is_throttled: boolean;
}
interface RuleRow {
  code: string; threshold: Record<string, number>; is_override: boolean;
}
interface TicketRow {
  id: string; status: string; channel: string;
  scope: { fields?: string[]; ids?: string[] }; note: string;
}
interface EventRow {
  id: string; rule_code: string; severity: string; status: string;
  evidence: Record<string, unknown>; created_at: string;
}

const RULE_NAME: Record<string, string> = {
  "R-01": "登录风暴（失败次数 / 10min）", "R-02": "异地登录（距离 km）",
  "R-03": "批量导出预警（比例）", "R-04": "权限提升（人数 / 1h）",
  "R-05": "深夜删除（对象数）", "R-06": "API 爬虫（配额比例）",
};
/** 各规则可调紧的主数值键（与迁移种子 threshold 结构对齐）。 */
const RULE_TIGHTEN_KEY: Record<string, string> = {
  "R-01": "count", "R-02": "distance_km", "R-03": "warn_ratio",
  "R-04": "count", "R-05": "count", "R-06": "quota_ratio",
};

function fmtBytes(n: number): string {
  if (!n) return "0";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0; let v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

export default function WsSecurityPage() {
  const { workspaceSlug: ws } = useParams();
  const [water, setWater] = useState<Water | null>(null);
  const [rules, setRules] = useState<RuleRow[]>([]);
  const [tickets, setTickets] = useState<TicketRow[]>([]);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [msg, setMsg] = useState("");
  const [exporting, setExporting] = useState(false);

  const load = useCallback(async () => {
    if (!ws) return;
    try {
      const r = await SecurityAPI.events(ws);
      const resp = r as unknown as {
        data?: EventRow[];
        meta?: { water?: Water; rules?: RuleRow[]; l2_tickets?: TicketRow[] };
      };
      setEvents(resp.data ?? []);
      setWater(resp.meta?.water ?? null);
      setRules(resp.meta?.rules ?? []);
      setTickets(resp.meta?.l2_tickets ?? []);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "加载失败");
    }
  }, [ws]);

  // oxlint-disable-next-line react/set-state-in-effect -- 服务端 loader（同 audit-logs.tsx 基线）
  useEffect(() => { load(); }, [load]);

  if (msg) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-neutral-50">
        <div className="text-center">
          <div className="text-[13px] text-red-500">{msg}</div>
          <div className="mt-1 text-[12px] text-neutral-400">
            本页需工作空间管理员身份；私有化部署未启用租户治理
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen flex-col">
      <Topbar />
      <div className="flex flex-1 overflow-hidden">
        <SystemSidebar workspaceSlug={ws ?? ""} />
        <main className="flex-1 overflow-auto p-4">
          <div className="mx-auto max-w-[1000px] px-2 py-4" data-sb-scope="ws-security">
            <div className="mb-[18px]">
              <div className="text-[17px] font-semibold">我的租户安全</div>
              <div className="text-[12.5px] text-neutral-400">
                本租户风控与配额 · 平台只见统计不见内容（BR-07）· 水位 ≥80% 黄 · ≥95% 红并通知管理员
              </div>
            </div>

            <div className="grid grid-cols-[1.1fr_.9fr] gap-3.5 mb-3.5">
              <div className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
                <b className="text-[13.5px]">配额水位</b>
                {water && (
                  <div className="mt-2.5 flex flex-col gap-2">
                    <WaterLine label={`存储 ${fmtBytes(water.storage.used)}/${fmtBytes(water.storage.limit)}`} ratio={water.storage.ratio} />
                    <WaterLine label={`API ${water.api_rate_per_minute.toLocaleString()} req/min 上限`} ratio={null} />
                    <div className="text-[12px] text-neutral-400 mt-1">
                      {water.is_throttled && <span className="text-amber-600">限流处置中（降至 10%）· </span>}
                      未处置风控事件 {water.open_risk_events} 起
                    </div>
                  </div>
                )}
              </div>
              <div className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
                <b className="text-[13.5px]">审计包导出</b>
                <div className="mt-2 text-[12px] text-neutral-400">
                  本租户各工作空间 AuditLog 逐空间导出合并 + SHA-256 清单签名；任务创建后 1h 预签名下载链接（邮件发送管理员）。
                </div>
                <button
                  onClick={async () => {
                    if (!ws || exporting) return;
                    setExporting(true);
                    try {
                      await SiteAuditAPI.exportCsv(ws, "");
                      toast("审计导出完成");
                    } catch (e) {
                      toast(e instanceof Error ? e.message : "导出失败", "error");
                    } finally { setExporting(false); }
                  }}
                  disabled={exporting}
                  data-sb-scope="audit-export"
                  className="mt-3 h-8 px-3 rounded-md bg-blue-600 text-white text-[12.5px] disabled:opacity-50">
                  {exporting ? "生成中…" : "生成审计导出"}
                </button>
              </div>
            </div>

            <div className="rounded-lg border border-neutral-200 bg-white shadow-sm mb-3.5">
              <div className="px-3.5 py-3 border-b border-neutral-200 flex items-center">
                <b className="text-[13.5px]">风控规则阈值（调紧）</b>
                <span className="ml-2 text-[12px] text-neutral-400">此操作只会更严格，平台默认值不可放宽（BR-06）· 即时生效</span>
              </div>
              <div className="grid grid-cols-[150px_1fr_92px_88px] gap-2.5 px-3.5 py-2 text-[11.5px] text-neutral-400 border-b border-neutral-200">
                <span>规则</span><span className="text-right">平台默认</span>
                <span className="text-right">本租户（可调紧）</span><span />
              </div>
              {rules.map((r) => (
                <TightenRow key={r.code} rule={r} ws={ws} onSaved={() => {
                  toast("阈值已保存并即时生效 · 落审计");
                  load();
                }} />
              ))}
            </div>

            <div className="rounded-lg border border-neutral-200 bg-white shadow-sm mb-3.5">
              <div className="px-3.5 py-3 border-b border-neutral-200 flex items-center">
                <b className="text-[13.5px]">本租户风控事件</b>
                <span className="ml-2 text-[12px] text-neutral-400">与平台侧同源（完整视角）</span>
              </div>
              {events.map((ev) => (
                <div key={ev.id} className="flex items-center gap-3 px-3.5 py-2.5 border-b border-neutral-100 last:border-0">
                  <span className="font-mono text-[11.5px] text-neutral-400">{ev.id.slice(0, 13)}…</span>
                  <span className={`font-mono text-[11px] px-1.5 py-0.5 rounded ${ev.status === "open" ? "bg-red-500 text-white" : "bg-neutral-100 text-neutral-400"}`}>
                    {ev.rule_code}
                  </span>
                  <span className="flex-1 min-w-0 truncate text-[12.5px] text-neutral-500">
                    {Object.entries(ev.evidence).filter(([k]) => k !== "occurrences").slice(0, 2)
                      .map(([k, v]) => `${k}=${String(v)}`).join(" · ")}
                  </span>
                  {ev.status === "open" && (
                    <button
                      onClick={async () => {
                        if (!ws) return;
                        const reason = window.prompt("申诉理由（运营复核后自动解除误报处置）", "");
                        if (!reason) return;
                        try {
                          await SecurityAPI.appeal(ws, ev.id, reason);
                          toast("申诉已提交，等待平台复核");
                          load();
                        } catch (e) {
                          toast(e instanceof Error ? e.message : "提交失败", "error");
                        }
                      }}
                      data-sb-scope="appeal-btn"
                      className="h-7 px-2.5 rounded-md border border-neutral-300 text-[12px] text-neutral-600 hover:bg-neutral-50">
                      误报申诉
                    </button>
                  )}
                  {ev.status === "dismissed" && <span className="text-[12px] text-neutral-400">误报关闭</span>}
                </div>
              ))}
              {!events.length && <div className="py-8 text-center text-[13px] text-neutral-400">无事件</div>}
            </div>

            {tickets.length > 0 && (
              <div>
                <b className="text-[13.5px]">L2 授权工单（平台请求查看业务内容）</b>
                {tickets.map((t) => (
                  <div key={t.id} className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-3.5">
                    <div className="grid grid-cols-[88px_1fr] gap-x-2.5 gap-y-1 text-[12.5px]">
                      <span className="text-neutral-400">申请内容</span>
                      <span>{(t.scope.fields ?? []).join(" / ") || "对象清单"}（{(t.scope.ids ?? []).length} 个对象）</span>
                      <span className="text-neutral-400">理由</span><span>{t.note || "风控事件复核"}</span>
                      <span className="text-neutral-400">时效</span><span>批准后 24h 有效 · 全程审计（§2.5）</span>
                    </div>
                    <div className="flex gap-2 mt-2.5 justify-end">
                      <button
                        onClick={async () => {
                          if (!ws) return;
                          try {
                            await SecurityAPI.decideTicket(ws, t.id, "reject");
                            toast("已拒绝 L2 工单"); load();
                          } catch (e) { toast(e instanceof Error ? e.message : "操作失败", "error"); }
                        }}
                        className="h-8 px-3 rounded-md border border-neutral-300 bg-white text-[12.5px] text-neutral-600">
                        拒绝
                      </button>
                      <button
                        onClick={async () => {
                          if (!ws) return;
                          try {
                            await SecurityAPI.decideTicket(ws, t.id, "approve");
                            toast("L2 授权已批准（24h）"); load();
                          } catch (e) { toast(e instanceof Error ? e.message : "操作失败", "error"); }
                        }}
                        data-sb-scope="l2-approve"
                        className="h-8 px-3 rounded-md bg-blue-600 text-white text-[12.5px]">
                        批准（24h）
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}

function WaterLine({ label, ratio }: { label: string; ratio: number | null }) {
  const pct = ratio == null ? 0 : Math.round(ratio * 100);
  const cls = ratio == null ? "bg-blue-500" : ratio >= 0.95 ? "bg-red-500" : ratio >= 0.8 ? "bg-amber-500" : "bg-blue-500";
  return (
    <div className="flex items-center gap-2">
      <span className="w-40 text-[12.5px] text-neutral-600">{label}</span>
      <div className="flex-1 h-2 rounded bg-neutral-100 overflow-hidden">
        <div className={`h-full rounded ${cls}`} style={{ width: `${Math.min(pct, 100)}%` }} />
      </div>
      <span className="w-10 text-right font-mono text-[12px] text-neutral-500">{ratio == null ? "—" : `${pct}%`}</span>
    </div>
  );
}

/** 阈值调紧行（BR-06：放宽标红拒保存——服务端 400 TOO_LARGE 双保险）。 */
function TightenRow({ rule, ws, onSaved }: {
  rule: RuleRow; ws: string | undefined; onSaved: () => void;
}) {
  const key: string | undefined = RULE_TIGHTEN_KEY[rule.code];
  const base = key ? rule.threshold[key] : undefined;
  const [val, setVal] = useState(base != null ? String(base) : "");
  const [err, setErr] = useState("");
  // oxlint-disable-next-line react/set-state-in-effect -- 规则行 props 同步复位（数据源重载）
  useEffect(() => { setVal(base != null ? String(base) : ""); }, [base]);

  if (base == null || key == null) return null;
  const looser = Number(val) > Number(base);

  return (
    <div className="grid grid-cols-[150px_1fr_92px_88px] gap-2.5 items-center px-3.5 py-2 border-b border-neutral-100 last:border-0 text-[12.5px]">
      <span className="font-medium">
        {rule.code} {RULE_NAME[rule.code]?.split("（")[0]}
        <span className="block text-[11px] text-neutral-400 font-normal">{RULE_NAME[rule.code]?.split("（")[1]?.replace("）", "")}</span>
      </span>
      <span className="text-right font-mono text-neutral-500">{String(base)}</span>
      <input value={val} onChange={(e) => { setVal(e.target.value); setErr(""); }}
        type="number" step="any"
        className={`h-7.5 h-8 rounded-md border px-2 font-mono text-[12.5px] text-right w-full
          ${looser ? "border-red-400 bg-red-50" : "border-neutral-300"}`} />
      <span className="text-right">
        {rule.is_override && !looser && <span className="text-[11px] text-green-600 mr-1.5">已调紧 ✓</span>}
        <button
          disabled={looser || val === String(base)}
          onClick={async () => {
            try {
              await SecurityAPI.tightenRule(ws ?? "", rule.code, { [key]: Number(val) });
              onSaved();
            } catch (e) {
              setErr(e instanceof Error ? e.message : "保存失败");
            }
          }}
          className="h-7 px-2.5 rounded-md bg-blue-600 text-white text-[12px] disabled:opacity-40">
          保存
        </button>
        {looser && <span className="block text-[10.5px] text-red-500 mt-0.5">不可放宽（BR-06）</span>}
        {err && <span className="block text-[10.5px] text-red-500 mt-0.5">{err}</span>}
      </span>
    </div>
  );
}
