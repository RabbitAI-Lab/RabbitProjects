import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";

interface GateEvent { id: number; event_type: string; payload: Record<string, unknown>; actor: string | null; created_at: string }
interface ChecklistEntry { key: string; revoked?: boolean; records?: Array<{ actor: string; at: string; signed: boolean; note: string }> }
interface Gate {
  id: string; version: string; commit_sha: string;
  gates: Record<string, string>; artifacts: Record<string, unknown>;
  checklist: ChecklistEntry[]; verdict: string; created_at: string;
  events?: GateEvent[];
}

const CHECKLIST_LABELS: Record<string, string> = {
  preflight: "前置检查全绿", image_scan: "镜像扫描无 Critical", migration_drill: "迁移彩排通过",
  restore_drill: "恢复演练 ≤30min", rate_limit_config: "限流配置=冻结表", backup_streak: "备份无连败",
  perf_baseline: "压测两轮达标", e2e_matrix: "E2E 矩阵全绿",
};
const GATE_LABELS: Record<string, string> = {
  defects: "缺陷", perf: "压测", security: "安全", compat: "兼容",
};
const GATE_STYLE: Record<string, string> = {
  passed: "bg-green-100 text-green-700", blocked: "bg-red-100 text-red-700",
  running: "bg-amber-100 text-amber-700", pending: "bg-neutral-100 text-neutral-500",
};

/** 发布门禁页（QA-001 §3.1——C.136：四门禁 + 8 项签署 + 裁决 + 事件时间线）。 */
export default function OpsRelease() {
  const [gate, setGate] = useState<Gate | null>(null);
  const [msg, setMsg] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [version, setVersion] = useState("1.0.0");
  const [sha, setSha] = useState("");

  const load = useCallback(async (id?: string) => {
    const r = await api<Gate[]>("GET", "/instances/release-gates/");
    {
      setLoaded(true);
      if (r.status === "success" && r.data && r.data.length > 0) {
        const found = (id ? r.data.find((g) => g.id === id) : undefined) ?? r.data[0];
        if (found) {
          const d = await api<Gate>("GET", `/instances/release-gates/${found.id}/`);
          if (d.status === "success" && d.data) setGate(d.data);
          return;
        }
      }
      if (r.status === "success") setGate(null);
      else setMsg(r.error?.message ?? "加载失败");
    }
  }, []);

  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  async function create() {
    const r = await api<Gate>("POST", "/instances/release-gates/create/",
      { version, commit_sha: sha || "0".repeat(40) });
    if (r.status === "success" && r.data) { setMsg(""); load(r.data.id); }
    else setMsg(r.error?.message ?? "创建失败");
  }
  async function sign(key: string, signed: boolean) {
    if (!gate) return;
    const r = await api<Gate>("POST",
      `/instances/release-gates/${gate.id}/checklist/${key}/sign/`, { signed });
    if (r.status === "success") load(gate.id);
    else setMsg(r.error?.message ?? "签署失败");
  }
  async function verdict(v: "approved" | "rejected") {
    if (!gate) return;
    const r = await api<Gate>("POST", `/instances/release-gates/${gate.id}/verdict/`,
      { verdict: v, note: "" });
    if (r.status === "success") { setMsg(""); load(gate.id); }
    else setMsg(`${r.error?.message}（${r.error?.details?.[0]?.message ?? ""}）`);
  }

  if (!loaded) return <div className="text-sm text-neutral-500">加载中…</div>;
  return (
    <section data-sb-scope="release-page">
      <h1 className="text-lg font-semibold mb-1">发布门禁</h1>
      <p className="text-[12px] text-neutral-400 mb-4">
        四门禁 + 8 项签署全绿方可裁决放行；一切变化 append-only 留痕
      </p>
      {!gate ? (
        <div className="p-4 border border-neutral-200 rounded-lg bg-white max-w-[480px]" data-sb-scope="release-create">
          <div className="text-[13px] mb-3">创建发布尝试（CI 流水线首步）</div>
          <div className="flex gap-2 mb-3">
            <input value={version} onChange={(e) => setVersion(e.target.value)} placeholder="版本号 v1.0.0"
              className="flex-1 h-9 border border-neutral-300 rounded-md px-2.5 text-[13px]" data-sb-scope="release-version" />
            <input value={sha} onChange={(e) => setSha(e.target.value)} placeholder="commit sha（40 位）"
              className="flex-1 h-9 border border-neutral-300 rounded-md px-2.5 text-[13px]" data-sb-scope="release-sha" />
          </div>
          <button onClick={create} data-sb-scope="release-create-btn"
            className="h-9 px-4 rounded-md bg-brand-500 text-white text-[13px]">创建</button>
          {msg && <div className="mt-2 text-[12px] text-red-600" role="alert">{msg}</div>}
        </div>
      ) : (
        <div className="max-w-[860px]">
          <div className="flex items-center gap-3 mb-3" data-sb-scope="release-head">
            <span className="text-[15px] font-semibold">{gate.version}</span>
            <code className="text-[12px] text-neutral-400">{gate.commit_sha.slice(0, 8)}</code>
            <span className={`px-2 py-0.5 rounded text-[12px] ${gate.verdict === "approved" ? "bg-green-100 text-green-700" : gate.verdict === "rejected" ? "bg-red-100 text-red-700" : "bg-neutral-100 text-neutral-500"}`}
              data-sb-scope="release-verdict">{gate.verdict}</span>
          </div>
          <div className="flex gap-2 mb-4" data-sb-scope="release-gates">
            {Object.entries(GATE_LABELS).map(([k, label]) => (
              <span key={k} className={`px-2.5 h-7 flex items-center rounded-full text-[12px] ${GATE_STYLE[gate.gates[k] ?? ""] ?? ""}`}>
                {label}：{gate.gates[k]}</span>))}
          </div>
          <table className="w-full border border-neutral-200 rounded-lg bg-white text-[13px] mb-4" data-sb-scope="release-checklist">
            <thead><tr className="bg-neutral-50 text-left text-neutral-500">
              <th className="px-3 py-2 font-medium">Checklist（8 项）</th>
              <th className="px-3 py-2 font-medium">签署</th>
              <th className="px-3 py-2 font-medium w-40">操作</th>
            </tr></thead>
            <tbody>
              {Object.keys(CHECKLIST_LABELS).map((key) => {
                const entry = gate.checklist.find((c) => c.key === key);
                const recs = entry?.records ?? [];
                const last = recs.length > 0 ? recs[recs.length - 1] : undefined;
                const signed = !!last && last.signed && !entry?.revoked;
                return (
                  <tr key={key} className="border-t border-neutral-100" data-sb-scope="release-cl-row">
                    <td className="px-3 py-1.5">{CHECKLIST_LABELS[key]}<code className="ml-1.5 text-[11px] text-neutral-400">{key}</code></td>
                    <td className="px-3 py-1.5">{signed && last ? `✓ ${last.actor}` : entry?.revoked ? "已反签" : "—"}</td>
                    <td className="px-3 py-1.5">
                      <button onClick={() => sign(key, true)} className="h-7 px-2 mr-1.5 border border-neutral-300 rounded text-[12px]">签署</button>
                      <button onClick={() => sign(key, false)} className="h-7 px-2 border border-neutral-300 rounded text-[12px] text-neutral-500">反签</button>
                    </td>
                  </tr>);
              })}
            </tbody>
          </table>
          <div className="flex gap-2 mb-3" data-sb-scope="release-verdict-actions">
            <button onClick={() => verdict("approved")} className="h-9 px-4 rounded-md bg-brand-500 text-white text-[13px]">裁决放行</button>
            <button onClick={() => verdict("rejected")} className="h-9 px-4 rounded-md border border-neutral-300 text-[13px]">打回</button>
            {msg && <span className="text-[12px] text-red-600 self-center" role="alert" data-sb-scope="release-msg">{msg}</span>}
          </div>
          <details data-sb-scope="release-events">
            <summary className="text-[13px] text-neutral-500 cursor-pointer">事件时间线（append-only）</summary>
            <ul className="mt-2 border border-neutral-200 rounded-lg bg-white text-[12px] divide-y divide-neutral-100">
              {(gate.events ?? []).map((e) => (
                <li key={e.id} className="px-3 py-1.5 flex gap-3">
                  <span className="text-neutral-400 w-36 shrink-0">{e.created_at.replace("T", " ").slice(0, 19)}</span>
                  <span className="font-medium w-32 shrink-0">{e.event_type}</span>
                  <span className="text-neutral-500">{e.actor ?? "CI"}</span>
                </li>))}
            </ul>
          </details>
        </div>)}
    </section>
  );
}
