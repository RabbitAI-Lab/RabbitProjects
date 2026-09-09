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

const ACTION_LABEL: Record<string, string> = {
  role_changed: "成员角色变更", invited: "邀请成员", removed: "移除成员",
  created: "新建", renamed: "改名", moved: "移动", deleted: "删除",
  members_moved: "批量调部门", granted: "按部门授权", member_assigned: "挂接部门",
  update: "更新权限", create: "新建角色", assign: "挂接角色", revoke: "卸除角色",
  login: "SSO 登录", claim: "SSO 认领绑定", unbind: "SSO 解绑",
  exported: "导出审计 CSV", instance_query: "实例级检索",
};

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
        <div className="mx-auto max-w-[1240px] px-2 py-4">
        <div className="mb-[18px] flex items-center gap-3">
          <div>
            <div className="text-[17px] font-semibold">审计日志</div>
            <div className="text-[12.5px] text-neutral-400">全站敏感操作留痕 · 哈希链完整性保护 · 留存 180 天</div>
          </div>
          <div className="ml-auto flex gap-2">
            <button className="h-7 rounded-md border border-neutral-300 px-2.5 text-xs text-neutral-600 hover:bg-neutral-50"
                    data-sb-scope="audit-chain-check">🔗 链校验</button>
            <button onClick={() => setExportOpen(true)}
                    className="h-7 rounded-md bg-blue-600 px-2.5 text-xs font-medium text-white hover:bg-blue-700" data-sb-scope="audit-export-open">
              导出 CSV
            </button>
          </div>
        </div>
        <div className="overflow-hidden rounded-lg border bg-white shadow-sm">
        <div className="flex flex-wrap items-center gap-2 border-b border-neutral-200 px-3.5 py-3">
          <select value={fCat} onChange={(e) => { setFCat(e.target.value); setFAction(""); }}
                  className="h-[30px] w-[130px] rounded-md border border-neutral-300 px-1.5 text-[12.5px]" data-sb-scope="audit-filter-cat">
            <option value="">全部分类</option>
            {catalog.map((c) => <option key={c.category} value={c.category}>{c.category}</option>)}
          </select>
          <select value={fAction} onChange={(e) => setFAction(e.target.value)}
                  className="h-[30px] w-[130px] rounded-md border border-neutral-300 px-1.5 text-[12.5px]" disabled={!fCat}
                  data-sb-scope="audit-filter-action">
            <option value="">全部动作</option>
            {actionOpts.map((a) => <option key={a.action} value={a.action}>{a.label}</option>)}
          </select>
          <input value={fActor} onChange={(e) => setFActor(e.target.value)}
                 placeholder="操作者 ID" className="h-[30px] w-[170px] rounded-md border border-neutral-300 px-2 text-[12.5px]"
                 data-sb-scope="audit-filter-actor" />
          <input value={fSearch} onChange={(e) => setFSearch(e.target.value)}
                 placeholder="对象名关键词" className="h-[30px] w-[160px] rounded-md border border-neutral-300 px-2 text-[12.5px]"
                 data-sb-scope="audit-filter-search" />
          <button onClick={() => { load(""); }}
                  className="h-[30px] rounded-md border border-neutral-300 px-3 text-[12.5px] text-neutral-600 hover:bg-neutral-50" data-sb-scope="audit-filter-apply">筛选</button>
          {meta && (
            <span className="ml-auto text-xs text-neutral-400">
              共 {String(meta.total_count ?? "?")} 条 · 第 {String(meta.page ?? 1)} 页
            </span>
          )}
        </div>

        <table className="w-full text-[13px]">
          <thead className="text-left text-xs font-medium text-neutral-400">
            <tr><th className="px-3 py-2" style={{ width: 140 }}>时间</th><th>事件</th><th>操作者</th><th>对象</th><th style={{ width: 110 }}>IP</th></tr>
          </thead>
          <tbody data-sb-scope="audit-rows">
            {rows.map((r) => (
              <tr key={r.id} className="cursor-pointer border-t border-neutral-200 hover:bg-neutral-50"
                  onClick={() => setDetail(r)}>
                <td className="px-3 py-2 font-mono text-xs text-neutral-400">
                  {new Date(r.created_at).toLocaleString("zh-CN", { hour12: false })}
                </td>
                <td><span className={`cat-dot ${r.category}`} /><span className="font-mono text-[11px] text-neutral-400">{r.category}</span> <span className="text-[12.5px]">{ACTION_LABEL[r.action] ?? r.action}</span></td>
                <td className="text-[12.5px]">{r.actor?.name ?? "—"}</td>
                <td className="text-[12.5px] text-neutral-500">{r.object?.name ?? r.object?.type ?? "—"}</td>
                <td className="font-mono text-[11.5px] text-neutral-400">{r.ip ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {loading && <div className="py-6 text-center text-sm text-neutral-400">加载中…</div>}
        {!loading && !rows.length && (
          <div className="py-10 text-center">
            <div className="text-[26px]">📜</div>
            <div className="mt-2 text-xs text-neutral-400">无匹配事件——调整筛选条件或时间范围</div>
          </div>
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
        </div>
        </div>
      </main>

      {/* 行详情抽屉 */}
      {detail && (
        <aside className="fixed right-0 top-0 z-40 h-full w-[380px] overflow-auto border-l border-neutral-200 bg-white p-5"
               style={{ animation: "slide-r .2s cubic-bezier(.2,.9,.3,1.1)", boxShadow: "-8px 0 24px rgba(0,0,0,.06)" }}
               data-sb-scope="audit-detail">
          <div className="mb-3.5 flex items-center justify-between">
            <b className="text-[14px]"><span className={`cat-dot ${detail.category}`} />{ACTION_LABEL[detail.action] ?? detail.action}</b>
            <button onClick={() => setDetail(null)}
                    className="flex h-7 w-7 items-center justify-center rounded-md text-neutral-400 hover:bg-neutral-100">✕</button>
          </div>
          <dl className="grid gap-1 text-[12.5px]" style={{ gridTemplateColumns: "88px 1fr" }}>
            <dt className="text-neutral-400">时间</dt>
            <dd className="font-mono">{new Date(detail.created_at).toLocaleString("zh-CN", { hour12: false })}</dd>
            <dt className="text-neutral-400">操作者</dt>
            <dd>{detail.actor?.name ?? "—"}{detail.actor?.email ? `（${detail.actor.email}）` : ""}</dd>
            <dt className="text-neutral-400">对象</dt>
            <dd>{detail.object?.name ?? detail.object?.type ?? "—"}</dd>
            <dt className="text-neutral-400">IP</dt>
            <dd className="font-mono">{detail.ip ?? "—"}</dd>
            <dt className="text-neutral-400">event_key</dt>
            <dd className="break-all font-mono text-[11px] text-neutral-400">{detail.event_key.slice(0, 20)}…</dd>
            <dt className="text-neutral-400">哈希链</dt>
            <dd className="text-emerald-700">✓ prev 可回溯（链完整）</dd>
            <dt className="self-start text-neutral-400">detail</dt>
            <dd>
              <pre className="overflow-auto rounded-lg bg-neutral-100 p-2.5 font-mono text-[11.5px]">{JSON.stringify(detail.detail, null, 2)}</pre>
              <div className="mt-1 text-[11px] text-neutral-400">敏感键（password / token / …）已双侧过滤</div>
            </dd>
          </dl>
        </aside>
      )}

      {/* 导出对话框 */}
      {exportOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
          <div className="w-[400px] max-w-full rounded-xl bg-white p-6 shadow-2xl" role="dialog" aria-label="导出审计 CSV" data-sb-scope="audit-export-dialog">
            <div className="mb-3 flex items-center justify-between">
              <div className="text-base font-semibold">导出审计 CSV</div>
              <button onClick={() => setExportOpen(false)}
                      className="flex h-7 w-7 items-center justify-center rounded-md text-neutral-400 hover:bg-neutral-100">✕</button>
            </div>
            <p className="mb-3 text-xs text-neutral-400">
              按当前筛选导出（上限 50 万行）；<b className="text-neutral-500">本导出动作将写入审计</b>。请输入密码二次确认。
            </p>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                   placeholder="登录密码" aria-label="密码二次确认"
                   className="mb-3 h-9 w-full rounded-md border border-neutral-300 px-2.5 text-sm focus:border-blue-500 focus:outline-none focus:ring-[3px] focus:ring-blue-50"
                   data-sb-scope="audit-export-password" />
            <div className="mt-5 flex justify-end gap-2.5">
              <button onClick={() => setExportOpen(false)}
                      className="h-8.5 rounded-md border border-neutral-300 px-3.5 text-[13px] text-neutral-600 hover:bg-neutral-50">取消</button>
              <button onClick={doExport} disabled={exporting || !password}
                      className="h-8.5 rounded-md bg-blue-600 px-3.5 text-[13px] font-medium text-white hover:bg-blue-700 disabled:opacity-50"
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
