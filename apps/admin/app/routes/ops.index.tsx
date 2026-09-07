import { useEffect, useState } from "react";
import { api } from "../lib/api";

interface Snapshot { [k: string]: string | number | { [k: string]: number } }

/** 限流监控页（INFRA-005 §3.1——C.134：冻结配额快照 + 降级旗标 + L2 开关态）。 */
export default function OpsRateLimit() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [degraded, setDegraded] = useState<boolean | null>(null);
  const [l2, setL2] = useState<boolean | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    api<{ config_snapshot: Snapshot; degraded: boolean; l2_enabled: boolean }>(
      "GET", "/instances/rate-limit/summary/",
    ).then((r) => {
      if (!alive) return;
      setLoading(false);
      if (r.status === "success" && r.data) {
        setSnap(r.data.config_snapshot);
        setDegraded(r.data.degraded);
        setL2(r.data.l2_enabled);
      } else setErr(r.error?.message ?? "加载失败");
    });
    return () => { alive = false; };
  }, []);

  const rows: Array<[string, string]> = snap ? (Object.entries(snap)
    .filter(([, v]) => typeof v === "string") as Array<[string, string]>) : [];
  const edge = (snap?.edge ?? {}) as { [k: string]: number };

  if (loading) return <div className="text-sm text-neutral-500" data-sb-scope="rl-loading">加载中…</div>;
  if (err) return <div className="text-sm text-red-600" data-sb-scope="rl-error" role="alert">{err}（需系统管理员会话）</div>;
  return (
    <section data-sb-scope="ratelimit-page">
      <h1 className="text-lg font-semibold mb-1">限流监控</h1>
      <p className="text-[12px] text-neutral-400 mb-4">
        配额快照与 api-conventions §7.2 冻结表比对（数值漂移=CI 断言红）
      </p>
      <div className="flex gap-3 mb-4" data-sb-scope="rl-flags">
        <span className={`px-2.5 h-7 flex items-center rounded-full text-[12px] ${degraded ? "bg-red-100 text-red-700" : "bg-green-100 text-green-700"}`}>
          {degraded ? "降级中" : "正常"}
        </span>
        <span className="px-2.5 h-7 flex items-center rounded-full text-[12px] bg-neutral-100 text-neutral-600">
          L2 全局：{l2 ? "启用" : "未启用（dev 口径）"}
        </span>
      </div>
      <table className="border border-neutral-200 rounded-lg bg-white text-[13px]" data-sb-scope="rl-table">
        <thead><tr className="bg-neutral-50 text-left text-neutral-500">
          <th className="px-3 py-2 font-medium">层</th><th className="px-3 py-2 font-medium">端点/主体</th>
          <th className="px-3 py-2 font-medium">配额</th>
        </tr></thead>
        <tbody>
          <tr className="border-t border-neutral-100"><td className="px-3 py-1.5" rowSpan={3}>L1 边缘（按 IP）</td>
            <td className="px-3 py-1.5">api 兜底区</td><td className="px-3 py-1.5">{edge.api_per_min}/min · burst 60</td></tr>
          <tr className="border-t border-neutral-100"><td className="px-3 py-1.5">auth 敏感区</td><td className="px-3 py-1.5">{edge.auth_per_min}/min</td></tr>
          <tr className="border-t border-neutral-100"><td className="px-3 py-1.5">public 匿名区</td><td className="px-3 py-1.5">{edge.public_per_min}/min</td></tr>
          {rows.map(([k, v]) => (
            <tr key={k} className="border-t border-neutral-100">
              <td className="px-3 py-1.5">L2/L3 应用</td>
              <td className="px-3 py-1.5">{k}</td><td className="px-3 py-1.5">{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
