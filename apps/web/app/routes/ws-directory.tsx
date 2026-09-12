/**
 * 身份源 · 目录同步（AUTH-011 §3.1~§3.3——冻结原型 V-DIR/V-DRY/V-SCIM）。
 * 总览（通道卡 + 待办三态 + 台账）/ 干跑确认 / SCIM 令牌——单页分区承载，
 * 权限 directory.manage（WS_ADMIN+，后端同档判定）。
 */
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { SystemSidebar } from "../components/SystemSidebar";
import { toast } from "../components/Toast";
import { api } from "../services/axios";

interface Overview {
  channel: "ldap" | "scim" | null;
  ldap: { id: string; name: string; server_uri: string; base_dn: string;
          is_enabled: boolean; sync_interval_minutes: number } | null;
  scim: { id: string; name: string; is_enabled: boolean;
          token_prefix: string; last_used_at: string | null } | null;
  last_run: { id: string; status: string; counts: Record<string, number>;
              created_at: string; full_sync: boolean } | null;
  todos: { pending_provision: number; manual_review: number };
}
interface PendingRow {
  id: string; kind: string; status: string; dedup_key: string;
  payload: Record<string, unknown>; created_at: string;
}
interface RunRow {
  id: string; status: string; channel: string; is_dry_run: boolean;
  full_sync: boolean; counts: Record<string, number>; created_at: string;
  error?: string;
}

const STATUS_DOT: Record<string, string> = {
  success: "#059669", failed: "#ef4444", dry_run: "#f59e0b",
  confirmed: "#3f76ff", running: "#3b82f6",
};

export default function WsDirectoryPage() {
  const { workspaceSlug: ws } = useParams();
  const [ov, setOv] = useState<Overview | null>(null);
  const [todos, setTodos] = useState<PendingRow[]>([]);
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [dryRun, setDryRun] = useState<RunRow | null>(null);
  const [dryDetail, setDryDetail] = useState<Array<Record<string, unknown>> | null>(null);
  const [scimToken, setScimToken] = useState("");

  const load = useCallback(async () => {
    if (!ws) return;
    try {
      const [o, t, r] = await Promise.all([
        api.get<Overview>(`workspaces/${ws}/directory/`),
        api.get<PendingRow[]>(`workspaces/${ws}/directory/pending-actions/`,
          { params: { status: "pending" } }),
        api.get<RunRow[]>(`workspaces/${ws}/directory/runs/`),
      ]);
      setOv(o.data ?? null);
      setTodos(t.data ?? []);
      setRuns(r.data ?? []);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "加载失败");
    }
  }, [ws]);

  // oxlint-disable-next-line react/set-state-in-effect -- 服务端 loader（同 audit-logs.tsx 基线）
  useEffect(() => { load(); }, [load]);

  async function triggerSync(dry: boolean) {
    if (!ws || busy) return;
    setBusy(true);
    try {
      const r = await api.post<{ run_id: string }>(
        `workspaces/${ws}/directory/sync/`, { dry_run: dry });
      toast(dry ? "干跑已受理（202）——完成后在台账「去确认」" : "同步已受理（202）");
      if (dry && r.data?.run_id) setTimeout(load, 2500);
      else setTimeout(load, 4000);
    } catch (e) {
      toast(e instanceof Error ? e.message : "触发失败", "error");
    } finally { setBusy(false); }
  }

  async function resolve(action: PendingRow, decision: string) {
    if (!ws) return;
    try {
      await api.post(`workspaces/${ws}/directory/pending-actions/${action.id}/resolve/`,
        { action: decision });
      toast("处置完成（落审计）");
      load();
    } catch (e) {
      toast(e instanceof Error ? e.message : "处置失败", "error");
    }
  }

  async function openDryRun(run: RunRow) {
    if (!ws) return;
    setDryRun(run);
    setDryDetail(null);
    const r = await api.get<{ detail: Array<Record<string, unknown>> }>(
      `workspaces/${ws}/directory/runs/${run.id}/`);
    setDryDetail(r.data?.detail ?? []);
  }

  async function confirmDryRun() {
    if (!ws || !dryRun) return;
    try {
      await api.post(`workspaces/${ws}/directory/runs/${dryRun.id}/confirm/`, {});
      toast("干跑已确认并真实执行（影响清单缺席计数清零，BR-04 豁免）");
      setDryRun(null);
      load();
    } catch (e) {
      toast(e instanceof Error ? e.message : "确认失败", "error");
    }
  }

  async function setupScim() {
    if (!ws) return;
    try {
      const r = await api.post<{ token: string }>(
        `workspaces/${ws}/directory/scim/`, { name: "SCIM 通道" });
      setScimToken(r.data?.token ?? "");
      toast("SCIM 已启用——令牌仅本次展示（O4）");
      load();
    } catch (e) {
      toast(e instanceof Error ? e.message : "启用失败", "error");
    }
  }

  if (msg) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-neutral-50">
        <div className="text-center">
          <div className="text-[13px] text-red-500">{msg}</div>
          <div className="mt-1 text-[12px] text-neutral-400">本页需工作空间管理员身份</div>
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
          <div className="mx-auto max-w-[1080px] px-2 py-4" data-sb-scope="ws-directory">
            <div className="mb-[18px] flex items-center gap-3">
              <div>
                <div className="text-[17px] font-semibold">身份源 · 目录同步</div>
                <div className="text-[12.5px] text-neutral-400">
                  离职当天自动禁用 · 漏禁 = 安全事故（AUTH-011 · LDAP 拉取 / SCIM 推送）
                </div>
              </div>
            </div>

            {/* 通道卡 O1 */}
            <div className="card p-4 mb-3.5" style={{ boxShadow: "0 1px 2px rgba(0,0,0,.05)" }}>
              <div className="flex items-center gap-2.5 flex-wrap">
                <span className="w-2 h-2 rounded-full shrink-0"
                  style={{ background: ov?.channel ? "#059669" : "#d4d4d4" }} />
                <b className="text-[13.5px]">
                  {ov?.channel === "scim" ? "SCIM 2.0 推送" : "LDAP/AD 同步"}
                </b>
                {ov?.channel ? (
                  <span className="px-2 py-0.5 rounded-full bg-green-50 text-green-700 text-[11px]">
                    已启用{ov.ldap ? ` · 每 ${ov.ldap.sync_interval_minutes} 分钟` : ""}
                  </span>
                ) : (
                  <span className="px-2 py-0.5 rounded-full bg-neutral-100 text-neutral-500 text-[11px]">未启用</span>
                )}
                <div className="ml-auto flex gap-2">
                  <button onClick={() => triggerSync(false)} disabled={busy || !ov?.channel}
                    data-sb-scope="dir-sync"
                    className="h-7 px-2.5 rounded-md bg-blue-600 text-white text-[12px] disabled:opacity-50">
                    {busy ? "受理中…" : "⟳ 立即同步"}
                  </button>
                  <button onClick={() => triggerSync(true)} disabled={busy || !ov?.channel}
                    className="h-7 px-2.5 rounded-md border border-neutral-300 text-[12px] text-neutral-600 hover:bg-neutral-50 disabled:opacity-50">
                    ◌ 干跑一次
                  </button>
                  {!ov?.channel && (
                    <button onClick={setupScim} data-sb-scope="scim-setup"
                      className="h-7 px-2.5 rounded-md bg-blue-600 text-white text-[12px]">
                      启用 SCIM 通道
                    </button>
                  )}
                </div>
              </div>
              {ov?.ldap && (
                <div className="grid grid-cols-[88px_1fr] gap-x-2.5 gap-y-1 mt-2 text-[12.5px]">
                  <span className="text-neutral-400">目录</span>
                  <span className="font-mono text-[12px]">{ov.ldap.server_uri} · base: {ov.ldap.base_dn}</span>
                  <span className="text-neutral-400">最近同步</span>
                  <span>
                    {ov.last_run
                      ? `${new Date(ov.last_run.created_at).toLocaleString("zh-CN")} · ${
                          Object.entries(ov.last_run.counts).map(([k, v]) => `${v} ${k}`).join(" · ")}`
                      : "尚未同步"}
                  </span>
                </div>
              )}
              {scimToken && (
                <div className="mt-3 p-2.5 rounded-lg border border-amber-200 bg-amber-50">
                  <div className="text-[12px] text-amber-800 font-medium mb-1">
                    SCIM Bearer Token（仅本次展示一次 · O4）
                  </div>
                  <div className="font-mono text-[12.5px] break-all select-all">{scimToken}</div>
                </div>
              )}
            </div>

            {/* 待办卡 O2 */}
            <div className="card p-4 mb-3.5" style={{ boxShadow: "0 1px 2px rgba(0,0,0,.05)" }}>
              <b className="text-[13.5px]">待办</b>
              <div className="flex flex-col gap-2 mt-2.5" data-sb-scope="dir-todos">
                {todos.map((t) => (
                  <div key={t.id} className={`flex items-center gap-2.5 px-2.5 py-2 rounded-lg border
                    ${t.kind === "pending_provision"
                      ? "border-amber-200 bg-amber-50 text-amber-800"
                      : "border-neutral-200"}`}>
                    <span>{t.kind === "pending_provision" ? "⚠" : "⚠"}</span>
                    <span className="text-[13px]">
                      {t.kind === "pending_provision" ? "待开通" : "人工裁决"}（
                      {String(t.payload?.email ?? t.payload?.new_email ?? t.dedup_key)}）——
                      {t.kind === "pending_provision"
                        ? "席位不足，请扩容或选择不开通"
                        : "邮箱变更疑似同人，需确认"}
                    </span>
                    <span className="ml-auto flex gap-2">
                      {t.kind === "pending_provision" ? (
                        <button onClick={() => resolve(t, "expand_seats")}
                          className="h-7 px-2.5 rounded-md bg-blue-600 text-white text-[12px]">
                          扩容补开通
                        </button>
                      ) : (
                        <button onClick={() => resolve(t, "merge")}
                          className="h-7 px-2.5 rounded-md bg-blue-600 text-white text-[12px]">
                          确认同人归并
                        </button>
                      )}
                      <button onClick={() => resolve(t, "dismiss")}
                        className="h-7 px-2.5 rounded-md border border-neutral-300 bg-white text-[12px] text-neutral-600">
                        放弃
                      </button>
                    </span>
                  </div>
                ))}
                {!todos.length && (
                  <div className="text-[13px] text-neutral-400 py-2">无待办——通道运行正常</div>
                )}
              </div>
            </div>

            {/* 台账 O5 */}
            <div className="card" style={{ boxShadow: "0 1px 2px rgba(0,0,0,.05)" }}>
              <div className="px-3.5 py-3 border-b border-neutral-200 flex items-center gap-2.5">
                <b className="text-[13.5px]">同步台账</b>
                <span className="text-[12px] text-neutral-400">缺席判定仅全量批 · 双确认禁用（BR-04）</span>
              </div>
              {runs.map((r) => (
                <div key={r.id} className="flex items-center gap-3 px-3.5 py-2.5 border-b border-neutral-100 last:border-0 hover:bg-neutral-50">
                  <span className="font-mono text-[12px] text-neutral-500 w-40 shrink-0">
                    {new Date(r.created_at).toLocaleString("zh-CN", { hour12: false })}
                  </span>
                  <span className="text-[12px] shrink-0">
                    {r.is_dry_run ? "干跑" : r.full_sync ? "全量" : "增量"}
                  </span>
                  <span className="flex-1 min-w-0 flex gap-1.5 flex-wrap">
                    {Object.entries(r.counts).filter(([, v]) => v > 0).map(([k, v]) => (
                      <span key={k} className="font-mono text-[11px] px-1.5 py-px rounded bg-neutral-100 text-neutral-600">
                        {v} {k === "created" ? "新增" : k === "updated" ? "变更" : k === "disabled" ? "禁用" : k}
                      </span>
                    ))}
                  </span>
                  <span className="inline-flex items-center gap-1.5 text-[12.5px] shrink-0"
                    style={{ color: STATUS_DOT[r.status] ?? "#8c8c8c" }}>
                    <span className="w-2 h-2 rounded-full" style={{ background: STATUS_DOT[r.status] ?? "#d4d4d4" }} />
                    {r.status === "dry_run" ? "待确认" : r.status === "success" ? "成功" : r.status}
                  </span>
                  {r.is_dry_run && r.status === "dry_run" ? (
                    <button onClick={() => openDryRun(r)} data-sb-scope="dry-open"
                      className="h-7 px-2.5 rounded-md bg-blue-600 text-white text-[12px] shrink-0">去确认</button>
                  ) : (
                    <button onClick={() => openDryRun(r)}
                      className="h-7 px-2.5 rounded-md border border-neutral-300 text-[12px] text-neutral-600 shrink-0">明细</button>
                  )}
                </div>
              ))}
              {!runs.length && <div className="py-8 text-center text-[13px] text-neutral-400">无同步记录</div>}
            </div>

            {/* 干跑确认弹层 O3 */}
            {dryRun && (
              <div className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-4"
                onClick={(e) => { if (e.target === e.currentTarget) setDryRun(null); }}>
                <div className="bg-white rounded-xl shadow-2xl w-[720px] max-w-full max-h-[85vh] overflow-auto p-6">
                  <div className="flex items-center justify-between mb-2">
                    <div className="text-base font-semibold">干跑结果{dryRun.status === "dry_run" ? " · 待确认" : " · 明细"}</div>
                    <button onClick={() => setDryRun(null)} className="w-7 h-7 rounded-md text-neutral-400 hover:bg-neutral-100">✕</button>
                  </div>
                  <p className="text-[12.5px] text-neutral-400 mb-3">
                    确认应用时影响清单内映射缺席计数清零重计（BR-04 豁免，防过滤条件变化误禁）
                  </p>
                  <div className="rounded-lg border border-neutral-200 overflow-hidden">
                    <table className="w-full text-[13px]">
                      <thead className="text-left text-xs text-neutral-400 bg-neutral-50">
                        <tr><th className="px-3 py-2">账号 / 引用</th><th className="w-24">动作</th><th>原因</th></tr>
                      </thead>
                      <tbody>
                        {(dryDetail ?? []).map((d, i) => (
                          <tr key={i} className="border-t border-neutral-100">
                            <td className="px-3 py-1.5 font-mono text-[12px]">
                              {String(d.email ?? d.external_id ?? "-")}
                            </td>
                            <td className="px-2 py-1.5">{String(d.action)}</td>
                            <td className="px-2 py-1.5 text-neutral-400">{String(d.reason ?? "")}</td>
                          </tr>
                        ))}
                        {!dryDetail?.length && (
                          <tr><td colSpan={3} className="py-6 text-center text-neutral-400 text-[12.5px]">无明细行</td></tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                  {dryRun.status === "dry_run" && (
                    <div className="flex justify-end gap-2.5 mt-5">
                      <button onClick={() => setDryRun(null)}
                        className="h-9 px-3.5 rounded-md border border-neutral-300 text-[13px] text-neutral-600">放弃变更</button>
                      <button onClick={confirmDryRun} data-sb-scope="dry-confirm"
                        className="h-9 px-3.5 rounded-md bg-blue-600 text-white text-[13px]">确认并应用 →</button>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
