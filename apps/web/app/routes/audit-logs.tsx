/** 全站审计日志页（AUTH-010 §3.1——C.152，Sprint-8 R6）。
 *
 *  筛选条（category/action/actor/关键词）+ 事件表（游标分页）+
 *  行详情抽屉（object/detail 快照）+ CSV 导出对话框（密码二次确认）。 */
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router";

import { SiteAuditAPI, unwrap } from "../services/api";
import { Topbar } from "../components/Topbar";
import { toast } from "../components/Toast";

type Row = {
  id: string; event_key: string; category: string; action: string;
  actor: { name?: string; email?: string };
  object: { type?: string; name?: string };
  detail: Record<string, unknown>;
  ip: string | null; created_at: string;
};
type CatGroup = { category: string; actions: { action: string; label: string }[] };

export default function AuditLogsPage() {
  const { workspaceSlug: ws } = useParams();
  const [rows, setRows] = useState<Row[]>([]);
  const [catalog, setCatalog] = useState<CatGroup[]>([]);
  const [meta, setMeta] = useState<Record<string, unknown> | null>(null);
  const [fCat, setFCat] = useState("");
  const [fAction, setFAction] = useState("");
  const [fActor, setFActor] = useState("");
  const [fSearch, setFSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [detail, setDetail] = useState<Row | null>(null);
  const [exportOpen, setExportOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [exporting, setExporting] = useState(false);

  const load = useCallback(async (cur = "") => {
    if (!ws) return;
    setLoading(true);
    try {
      const params: Record<string, unknown> = {};
      if (fCat) params.category = fCat;
      if (fAction) params.action = fAction;
      if (fActor) params.actor = fActor;
      if (fSearch) params.search = fSearch;
      if (cur) params.cursor = cur;
      const r = await SiteAuditAPI.list(ws, params);
      setRows(unwrap<Row[]>(r) ?? []);
      setMeta((r as unknown as { meta?: Record<string, unknown> }).meta ?? null);
    } catch {
      toast("加载审计日志失败", "error");
    } finally {
      setLoading(false);
    }
  }, [ws, fCat, fAction, fActor, fSearch]);

  // oxlint-disable-next-line react/set-state-in-effect -- 服务端 loader（同 approvals.tsx 基线）
  useEffect(() => { load(""); }, [load]);

  useEffect(() => {
    if (!ws) return;
    SiteAuditAPI.catalog(ws).then((r) => {
      setCatalog(unwrap<CatGroup[]>(r) ?? []);
    }).catch(() => {});
  }, [ws]);

  async function doExport() {
    if (!ws || !password) return;
    setExporting(true);
    try {
      const params: Record<string, unknown> = {};
      if (fCat) params.category = fCat;
      if (fAction) params.action = fAction;
      const r = await SiteAuditAPI.exportCsv(ws, password);
      const blob = new Blob([r as unknown as BlobPart], { type: "text/csv" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `audit-log-${ws}.csv`;
      a.click();
      URL.revokeObjectURL(url);
      toast("导出完成（本动作已入审计）");
      setExportOpen(false);
      setPassword("");
    } catch (e) {
      toast((e as { message?: string })?.message ?? "导出失败（密码或链校验）", "error");
    } finally {
      setExporting(false);
    }
  }

  const actionOpts = catalog.find((c) => c.category === fCat)?.actions ?? [];

  return (
    <div className="flex h-screen flex-col">
      <Topbar />
      <main className="flex-1 overflow-auto p-4">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <h1 className="mr-2 text-base font-semibold">审计日志</h1>
          <select value={fCat} onChange={(e) => { setFCat(e.target.value); setFAction(""); }}
                  className="rounded border px-2 py-1 text-sm" data-sb-scope="audit-filter-cat">
            <option value="">全部分类</option>
            {catalog.map((c) => <option key={c.category} value={c.category}>{c.category}</option>)}
          </select>
          <select value={fAction} onChange={(e) => setFAction(e.target.value)}
                  className="rounded border px-2 py-1 text-sm" disabled={!fCat}
                  data-sb-scope="audit-filter-action">
            <option value="">全部动作</option>
            {actionOpts.map((a) => <option key={a.action} value={a.action}>{a.label}</option>)}
          </select>
          <input value={fActor} onChange={(e) => setFActor(e.target.value)}
                 placeholder="操作者 ID" className="w-40 rounded border px-2 py-1 text-sm"
                 data-sb-scope="audit-filter-actor" />
          <input value={fSearch} onChange={(e) => setFSearch(e.target.value)}
                 placeholder="对象名关键词" className="w-40 rounded border px-2 py-1 text-sm"
                 data-sb-scope="audit-filter-search" />
          <button onClick={() => { load(""); }}
                  className="rounded border px-3 py-1 text-sm" data-sb-scope="audit-filter-apply">筛选</button>
          <button onClick={() => setExportOpen(true)}
                  className="rounded bg-blue-600 px-3 py-1 text-sm text-white" data-sb-scope="audit-export-open">
            导出 CSV
          </button>
          {meta && (
            <span className="ml-auto text-xs text-neutral-400">
              共 {String(meta.total_count ?? "?")} 条 · 第 {String(meta.page ?? 1)} 页
            </span>
          )}
        </div>

        <table className="w-full text-sm">
          <thead className="text-left text-xs text-neutral-400">
            <tr><th className="py-1.5">时间</th><th>分类.动作</th><th>操作者</th><th>对象</th><th>IP</th></tr>
          </thead>
          <tbody data-sb-scope="audit-rows">
            {rows.map((r) => (
              <tr key={r.id} className="cursor-pointer border-t hover:bg-neutral-50"
                  onClick={() => setDetail(r)}>
                <td className="py-1.5 text-xs text-neutral-500">
                  {new Date(r.created_at).toLocaleString("zh-CN")}
                </td>
                <td><span className="rounded bg-neutral-100 px-1.5 py-0.5 text-xs">{r.category}</span>
                    <span className="ml-1 text-xs">{r.action}</span></td>
                <td className="text-xs">{r.actor?.name ?? "—"}</td>
                <td className="text-xs">{r.object?.name ?? r.object?.type ?? "—"}</td>
                <td className="text-xs text-neutral-400">{r.ip ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {loading && <div className="py-6 text-center text-sm text-neutral-400">加载中…</div>}
        {!loading && !rows.length && (
          <div className="py-10 text-center text-sm text-neutral-400">无匹配事件</div>
        )}

        <div className="mt-3 flex justify-between">
          <button
            onClick={() => { const c = String(meta?.prev_cursor ?? ""); load(c); }}
            disabled={!meta?.prev_cursor}
            className="rounded border px-3 py-1 text-sm disabled:opacity-40">
            上一页
          </button>
          <button
            onClick={() => { const c = String(meta?.next_cursor ?? ""); load(c); }}
            disabled={!meta?.next_page_results}
            className="rounded border px-3 py-1 text-sm disabled:opacity-40" data-sb-scope="audit-next">
            下一页
          </button>
        </div>
      </main>

      {/* 行详情抽屉 */}
      {detail && (
        <aside className="fixed right-0 top-0 z-40 h-full w-[380px] overflow-auto border-l bg-white p-4 shadow-lg"
               data-sb-scope="audit-detail">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold">{detail.category}.{detail.action}</h2>
            <button onClick={() => setDetail(null)} className="text-neutral-400">✕</button>
          </div>
          <dl className="space-y-2 text-xs">
            <div><dt className="text-neutral-400">时间</dt><dd>{new Date(detail.created_at).toLocaleString("zh-CN")}</dd></div>
            <div><dt className="text-neutral-400">操作者</dt>
                 <dd>{detail.actor?.name} {detail.actor?.email ? `(${detail.actor.email})` : ""}</dd></div>
            <div><dt className="text-neutral-400">对象</dt>
                 <dd>{detail.object?.name ?? "—"} {detail.object?.type ? `· ${detail.object.type}` : ""}</dd></div>
            <div><dt className="text-neutral-400">IP</dt><dd>{detail.ip ?? "—"}</dd></div>
            <div><dt className="text-neutral-400">event_key</dt><dd className="break-all font-mono">{detail.event_key}</dd></div>
            <div><dt className="text-neutral-400">detail（已滤敏感键）</dt>
                 <dd><pre className="mt-1 overflow-auto rounded bg-neutral-50 p-2">{JSON.stringify(detail.detail, null, 2)}</pre></dd></div>
          </dl>
        </aside>
      )}

      {/* 导出对话框 */}
      {exportOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
          <div className="w-[380px] rounded-lg bg-white p-5 shadow-xl" data-sb-scope="audit-export-dialog">
            <h2 className="mb-2 text-sm font-semibold">导出审计 CSV</h2>
            <p className="mb-3 text-xs text-neutral-500">
              按当前筛选条件导出（上限 50 万行）；导出动作本身将入审计。请输入密码二次确认。
            </p>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                   placeholder="登录密码" className="mb-3 w-full rounded border px-2 py-1.5 text-sm"
                   data-sb-scope="audit-export-password" />
            <div className="flex justify-end gap-2">
              <button onClick={() => setExportOpen(false)}
                      className="rounded border px-3 py-1.5 text-sm">取消</button>
              <button onClick={doExport} disabled={exporting || !password}
                      className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-50"
                      data-sb-scope="audit-export-confirm">
                {exporting ? "导出中…" : "确认导出"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
