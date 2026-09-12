import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { api } from "../lib/api";

/** 平台租户总览（AUTH-012 §3.2——冻结原型 V-TENANTS：水位三档色/规则徽标/
 * 状态/近 24h 汇总条；水位色 O1：<80% 蓝、80-94% 琥珀、≥95% 红）。 */
interface TenantRow {
  id: string; name: string; tier: string; seats: number;
  storage: { used: number; limit: number; ratio: number | null };
  api_rate_per_minute: number;
  open_risk_events: number;
  is_frozen: boolean; is_throttled: boolean;
}

const PLAN_STYLE: Record<string, string> = {
  free: "bg-neutral-100 text-neutral-600", standard: "bg-blue-50 text-blue-700",
  enterprise: "bg-brand-100 text-brand-600",
  flagship: "bg-violet-50 text-violet-700 border border-violet-200",
};
const PLAN_LABEL: Record<string, string> = {
  free: "免费", standard: "标准", enterprise: "企业", flagship: "旗舰",
};

function Water({ ratio }: { ratio: number | null }) {
  if (ratio == null) return <span className="text-[12px] text-neutral-400">—</span>;
  const pct = Math.round(ratio * 100);
  const cls = ratio >= 0.95 ? "bg-red-500" : ratio >= 0.8 ? "bg-amber-500" : "bg-brand-500";
  const hot = ratio >= 0.95 ? "text-red-600 font-semibold" : "text-neutral-600";
  return (
    <div className="flex items-center gap-2 min-w-[140px]" data-sb-scope="water-bar">
      <div className="flex-1 h-2 rounded bg-neutral-100 overflow-hidden">
        <div className={`h-full rounded ${cls}`} style={{ width: `${Math.min(pct, 100)}%` }} />
      </div>
      <span className={`w-10 text-right text-[12px] font-mono ${hot}`}>{pct >= 95 ? `${pct}!` : `${pct}%`}</span>
    </div>
  );
}

export default function GovernanceTenants() {
  const [rows, setRows] = useState<TenantRow[]>([]);
  const [msg, setMsg] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [tier, setTier] = useState("");
  const [state, setState] = useState("");
  const [search, setSearch] = useState("");
  const seqRef = useRef(0);

  const load = useCallback(() => {
    const my = ++seqRef.current; // 陈旧响应守卫：慢的旧请求晚到不得覆盖新结果
    const q = new URLSearchParams();
    if (search.trim()) q.set("search", search.trim());
    if (tier) q.set("tier", tier);
    if (state) q.set("state", state);
    api<TenantRow[]>("GET", `/instances/tenants/${q.size ? `?${q}` : ""}`).then((r) => {
      if (my !== seqRef.current) return;
      setLoaded(true);
      if (r.status === "success" && r.data) setRows(r.data);
      else setMsg(r.error?.message ?? "加载失败");
    });
  }, [search, tier, state]);

  useEffect(load, [load]);

  const openTotal = rows.reduce((s, t) => s + t.open_risk_events, 0);
  const frozen = rows.filter((t) => t.is_frozen).length;
  const throttled = rows.filter((t) => t.is_throttled).length;

  return (
    <section data-sb-scope="tenants-page">
      <div className="flex items-center gap-3 mb-1">
        <h1 className="text-lg font-semibold">平台租户总览</h1>
        {openTotal > 0 && (
          <span className="h-6 px-2.5 inline-flex items-center rounded-full bg-red-50 border border-red-200 text-red-600 text-[12px]">
            风控事件 {openTotal} 未处置
          </span>
        )}
        <div className="ml-auto flex gap-2">
          <input value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索租户名 / ID" data-sb-scope="tenant-search"
            className="h-8 w-48 rounded-md border border-neutral-300 px-2.5 text-[12.5px] bg-white" />
          <select value={tier} onChange={(e) => setTier(e.target.value)}
            className="h-8 rounded-md border border-neutral-300 px-2 text-[12.5px] bg-white">
            <option value="">套餐：全部</option>
            <option value="free">免费</option><option value="standard">标准</option>
            <option value="enterprise">企业</option><option value="flagship">旗舰</option>
          </select>
          <select value={state} onChange={(e) => setState(e.target.value)}
            className="h-8 rounded-md border border-neutral-300 px-2 text-[12.5px] bg-white">
            <option value="">状态：全部</option><option value="frozen">冻结中</option><option value="ok">正常</option>
          </select>
        </div>
      </div>
      <p className="text-[12px] text-neutral-400 mb-4">SaaS 实例 {rows.length} 租户 · 治理引擎运行中（TENANT_GOVERNANCE_ENABLED）</p>

      <div className="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
        <table className="w-full text-[13px]">
          <thead className="text-left text-xs font-medium text-neutral-400">
            <tr>
              <th className="px-3.5 py-2 w-44">租户</th><th className="w-20">套餐</th>
              <th>存储水位</th><th className="w-40">状态</th>
            </tr>
          </thead>
          <tbody data-sb-scope="tenant-rows">
            {rows.map((t) => (
              <tr key={t.id} className="border-t border-neutral-100 hover:bg-neutral-50">
                <td className="px-3.5 py-2.5">
                  <Link to={`/governance/tenants/${t.id}`} className="font-medium text-brand-600 no-underline hover:underline">
                    {t.name}
                  </Link>
                  <div className="font-mono text-[10.5px] text-neutral-400">{t.id.slice(0, 12)}…</div>
                </td>
                <td className="px-2 py-2.5">
                  <span className={`px-2 py-0.5 rounded text-[11px] ${PLAN_STYLE[t.tier] ?? PLAN_STYLE.free}`}>
                    {PLAN_LABEL[t.tier] ?? t.tier}
                  </span>
                </td>
                <td className="px-2 py-2.5"><Water ratio={t.storage.ratio} /></td>
                <td className="px-2 py-2.5">
                  {t.is_frozen ? (
                    <span className="inline-flex items-center gap-1.5 text-[12.5px] text-red-600">
                      <span className="w-2 h-2 rounded-full bg-red-500" />冻结中
                    </span>
                  ) : t.is_throttled ? (
                    <span className="inline-flex items-center gap-1.5 text-[12.5px] text-amber-600">
                      <span className="w-2 h-2 rounded-full bg-amber-500" />限流中
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1.5 text-[12.5px] text-green-600">
                      <span className="w-2 h-2 rounded-full bg-green-500" />正常
                    </span>
                  )}
                  {t.open_risk_events > 0 && (
                    <span className="ml-1.5 font-mono text-[11px] text-red-600">{t.open_risk_events} 事件</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {loaded && !rows.length && !msg && (
          <div className="py-10 text-center text-[13px] text-neutral-400">无匹配租户</div>
        )}
        {msg && <div className="py-8 text-center text-[13px] text-red-500" role="alert">{msg}</div>}
      </div>

      <div className="mt-3.5 px-3.5 h-10 flex items-center rounded-lg border border-neutral-200 bg-white text-[12.5px] text-neutral-600">
        <span>近 24h 全站：</span>
        <span className="ml-1">风控未处置 <b className="font-mono">{openTotal}</b></span>
        <span className="ml-4">冻结 <b className="font-mono">{frozen}</b></span>
        <span className="ml-4">限流 <b className="font-mono">{throttled}</b></span>
        <span className="ml-auto text-[11.5px] text-neutral-400">数据源：RiskRuleEngine 聚合窗口（UTC 整点桶）</span>
      </div>
    </section>
  );
}
