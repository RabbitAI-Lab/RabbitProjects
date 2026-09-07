import { useEffect, useState } from "react";
import { useParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { ProjectAPI, WebhookAPI, type WebhookDeliveryRow, type WebhookEndpointRow, unwrap } from "../services/api";
import { ConfirmDialog } from "../components/files/FileDialogs";
import { toast } from "../components/Toast";

/** INTG-002 §3.1~3.3 Webhook 管理页（C.133）：端点列表 + 新建/编辑抽屉 +
 *  投递日志（死信重放/ping）。事件面来自后端 meta.event_choices（闭集单源）。 */
const PING = "webhook.ping";

export default function ProjectWebhooksPage() {
  const { workspaceSlug: slug, projectId } =
    useParams<{ workspaceSlug: string; projectId: string }>();
  const [rows, setRows] = useState<WebhookEndpointRow[]>([]);
  const [eventChoices, setEventChoices] = useState<string[]>([]);
  const [editing, setEditing] = useState<WebhookEndpointRow | "new" | null>(null);
  const [logsFor, setLogsFor] = useState<WebhookEndpointRow | null>(null);
  const [removing, setRemoving] = useState<WebhookEndpointRow | null>(null);
  const [secretShown, setSecretShown] = useState<string | null>(null);
  const [projName, setProjName] = useState("…");
  const [projIdentifier, setProjIdentifier] = useState("");
  useEffect(() => {
    ProjectAPI.detail(slug!, projectId!).then((r) => {
      const p = (r as unknown as { data: { name: string; identifier: string } }).data;
      setProjName(p?.name ?? "…"); setProjIdentifier(p?.identifier ?? "");
    }).catch(() => {});
  }, [slug, projectId]);

  const load = () => WebhookAPI.list(slug!, projectId!).then((r) => {
    const body = r as { data?: WebhookEndpointRow[]; meta?: { event_choices?: string[] } };
    setRows(body.data ?? []);
    setEventChoices(body.meta?.event_choices?.filter((e) => e !== PING) ?? []);
  }).catch(() => toast("Webhook 列表加载失败", "error"));

  useEffect(() => { void load(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, [slug, projectId]);

  const toggle = async (row: WebhookEndpointRow) => {
    try {
      if (row.is_active === "active") await WebhookAPI.disable(slug!, projectId!, row.id);
      else await WebhookAPI.enable(slug!, projectId!, row.id);
      toast(row.is_active === "active" ? "已停用" : "已启用（连败清零）", "ok");
      void load();
    } catch { toast("操作失败", "error"); }
  };

  const ping = async (row: WebhookEndpointRow) => {
    try {
      await WebhookAPI.ping(slug!, projectId!, row.id);
      toast("测试投递已入队（成败不计连败）", "ok");
    } catch { toast("ping 失败", "error"); }
  };

  return (
    <div className="h-screen flex flex-col bg-neutral-50">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <ProjectSidebar projectName={projName} identifier={projIdentifier} />
        <main className="flex-1 overflow-auto p-6" data-sb-scope="webhooks-page">
          <header className="flex items-center justify-between mb-4">
            <h1 className="text-[20px] font-semibold text-neutral-900">Webhook</h1>
            <button onClick={() => setEditing("new")}
              className="h-9 px-4 rounded-md bg-brand-500 hover:bg-brand-600 text-white text-[13px]">
              ＋ 新建端点
            </button>
          </header>

          {rows.length === 0 ? (
            <div className="bg-white rounded-xl border border-dashed border-neutral-300 p-12 text-center text-[13px] text-neutral-500">
              还没有出站 Webhook——新建一个端点接收项目事件推送
            </div>
          ) : (
            <ul className="space-y-3">
              {rows.map((row) => (
                <li key={row.id} className="bg-white rounded-xl border border-neutral-200 p-4"
                  data-sb-scope="webhook-row" data-endpoint-id={row.id}>
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <div className="font-mono text-[13px] text-neutral-800 truncate">{row.url}</div>
                      <div className="mt-1.5 flex flex-wrap gap-1">
                        {row.events.map((e) => (
                          <span key={e} className="px-1.5 py-0.5 rounded bg-neutral-100 text-[11px] text-neutral-600">{e}</span>
                        ))}
                        <span className="px-1.5 py-0.5 rounded bg-neutral-50 text-[11px] text-neutral-400">{PING}（免勾选）</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <StatusBadge row={row} />
                      <button onClick={() => void ping(row)}
                        className="h-8 px-2.5 rounded-md border border-neutral-300 text-[12.5px] hover:bg-neutral-50">发送测试</button>
                      <button onClick={() => setLogsFor(row)}
                        className="h-8 px-2.5 rounded-md border border-neutral-300 text-[12.5px] hover:bg-neutral-50">投递日志</button>
                      <button onClick={() => setEditing(row)}
                        className="h-8 px-2.5 rounded-md border border-neutral-300 text-[12.5px] hover:bg-neutral-50">编辑</button>
                      <button onClick={() => void toggle(row)}
                        className="h-8 px-2.5 rounded-md border border-neutral-300 text-[12.5px] hover:bg-neutral-50">
                        {row.is_active === "active" ? "停用" : "启用"}
                      </button>
                      <button onClick={() => setRemoving(row)} aria-label="删除端点"
                        className="h-8 px-2.5 rounded-md border border-red-200 text-red-600 text-[12.5px] hover:bg-red-50">删除</button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}

          {editing && (
            <EndpointDrawer
              row={editing === "new" ? null : editing}
              eventChoices={eventChoices}
              onClose={() => setEditing(null)}
              onSaved={(secret) => { setEditing(null); setSecretShown(secret); void load(); }}
            />
          )}
          {logsFor && (
            <DeliveriesDrawer row={logsFor} onClose={() => setLogsFor(null)} />
          )}
          {removing && (
            <ConfirmDialog title="删除端点？" danger okText="删除"
              onClose={() => setRemoving(null)}
              onOk={async () => {
                await WebhookAPI.remove(slug!, projectId!, removing.id);
                setRemoving(null); toast("端点已删除（软删后同 URL 可重建）", "ok"); void load();
              }}>
              删除后不再投递；历史投递日志保留 30 天。
            </ConfirmDialog>
          )}
          {secretShown && (
            <div className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-4"
              onMouseDown={(e) => e.target === e.currentTarget && setSecretShown(null)}>
              <div className="bg-white rounded-xl p-6 w-[480px] max-w-full" role="dialog" aria-modal="true"
                aria-label="secret 一次性展示" data-sb-scope="webhook-secret-once">
                <div className="text-[15px] font-semibold mb-2">签名密钥（仅显示这一次）</div>
                <div className="font-mono text-[12.5px] bg-neutral-50 border rounded p-3 break-all">{secretShown}</div>
                <p className="mt-3 text-[12.5px] text-neutral-500">
                  接收方用 X-RP-Signature（HMAC-SHA256，"{"{timestamp}.{body}"}"）与 X-RP-Timestamp（±5 分钟）校验。
                </p>
                <div className="mt-4 flex justify-end gap-2">
                  <button onClick={() => { void navigator.clipboard?.writeText(secretShown); toast("已复制", "ok"); }}
                    className="h-9 px-3 rounded-md border border-neutral-300 text-[13px]">复制</button>
                  <button onClick={() => setSecretShown(null)}
                    className="h-9 px-4 rounded-md bg-brand-500 text-white text-[13px]">我已保存</button>
                </div>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

function StatusBadge({ row }: { row: WebhookEndpointRow }) {
  if (row.is_active === "active")
    return <span className="px-2 py-0.5 rounded-full bg-green-50 text-green-700 text-[11.5px]">● 启用</span>;
  if (row.is_active === "auto_disabled")
    return <span className="px-2 py-0.5 rounded-full bg-red-50 text-red-700 text-[11.5px]"
      title={`连续 ${row.consecutive_failures} 次失败`}>⚠ 已自动停用 · {row.consecutive_failures} 连败</span>;
  return <span className="px-2 py-0.5 rounded-full bg-neutral-100 text-neutral-500 text-[11.5px]">⚪ 已停用</span>;
}

function EndpointDrawer({ row, eventChoices, onClose, onSaved }: {
  row: WebhookEndpointRow | null;
  eventChoices: string[];
  onClose: () => void;
  onSaved: (secret: string | null) => void;
}) {
  const { workspaceSlug: slug, projectId } =
    useParams<{ workspaceSlug: string; projectId: string }>();
  const [url, setUrl] = useState(row?.url ?? "");
  const [events, setEvents] = useState<string[]>(row?.events ?? ["issue.updated"]);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      if (row) {
        await WebhookAPI.update(slug!, projectId!, row.id, { url, events });
        onSaved(null);
      } else {
        const r = await WebhookAPI.create(slug!, projectId!, { url, events });
        onSaved(unwrap<WebhookEndpointRow>(r).secret_shown_once ?? null);
      }
    } catch (e) {
      toast(e instanceof Error ? e.message : "保存失败", "error");
    } finally { setBusy(false); }
  };
  return (
    <div className="fixed inset-0 z-40 bg-black/30 flex items-center justify-center p-4"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="bg-white rounded-xl p-6 w-[520px] max-w-full max-h-[85vh] overflow-auto" role="dialog"
        aria-modal="true" aria-label={row ? "编辑端点" : "新建端点"} data-sb-scope="webhook-edit">
        <div className="text-[15px] font-semibold mb-4">{row ? "编辑端点" : "新建端点"}</div>
        <label className="block text-[12.5px] text-neutral-500 mb-1">回调地址</label>
        <input value={url} onChange={(e) => setUrl(e.target.value)} aria-label="回调地址"
          placeholder="https://hooks.example.com/rabbit"
          className="w-full h-9 px-3 border border-neutral-300 rounded-md text-[13px] mb-4" />
        <label className="block text-[12.5px] text-neutral-500 mb-1.5">订阅事件</label>
        <div className="grid grid-cols-2 gap-1.5 mb-5">
          {eventChoices.map((e) => (
            <label key={e} className="flex items-center gap-2 text-[13px] text-neutral-700">
              <input type="checkbox" checked={events.includes(e)}
                onChange={() => setEvents((cur) =>
                  cur.includes(e) ? cur.filter((x) => x !== e) : [...cur, e])} />
              <span className="font-mono text-[12px]">{e}</span>
            </label>
          ))}
        </div>
        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="h-9 px-4 rounded-md border border-neutral-300 text-[13px]">取消</button>
          <button disabled={busy || !url.startsWith("http") || events.length === 0} onClick={() => void save()}
            className="h-9 px-4 rounded-md bg-brand-500 text-white text-[13px] disabled:opacity-50">
            {busy ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}

function DeliveriesDrawer({ row, onClose }: { row: WebhookEndpointRow; onClose: () => void }) {
  const { workspaceSlug: slug, projectId } =
    useParams<{ workspaceSlug: string; projectId: string }>();
  const [deliveries, setDeliveries] = useState<Awaited<ReturnType<typeof WebhookAPI.deliveries>>["data"] | null>(null);
  const [statusFilter, setStatusFilter] = useState("");
  useEffect(() => {
    WebhookAPI.deliveries(slug!, projectId!, row.id, statusFilter ? { status: statusFilter } : {})
      .then((r) => setDeliveries((r as { data?: WebhookDeliveryRow[] }).data ?? []))
      .catch(() => setDeliveries([]));
  }, [slug, projectId, row.id, statusFilter]);
  const replay = async (id: string) => {
    try {
      await WebhookAPI.replay(slug!, projectId!, row.id, id);
      toast("已重放（新记录，原死信保留）", "ok");
    } catch { toast("重放失败", "error"); }
  };
  const deadCount = (deliveries ?? []).filter((d) => d.status === "dead").length;
  return (
    <div className="fixed inset-0 z-40 bg-black/30 flex items-center justify-center p-4"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="bg-white rounded-xl p-6 w-[880px] max-w-full max-h-[85vh] overflow-auto" role="dialog"
        aria-modal="true" aria-label="投递日志" data-sb-scope="webhook-deliveries">
        <div className="flex items-center justify-between mb-4">
          <div className="text-[15px] font-semibold">投递日志 · {row.url.slice(0, 40)}…</div>
          <div className="flex items-center gap-2">
            {deadCount > 0 && (
              <span className="px-2 py-0.5 rounded-full bg-red-50 text-red-600 text-[11.5px]">死信 {deadCount} 🔴</span>
            )}
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} aria-label="状态过滤"
              className="h-8 px-2 border border-neutral-300 rounded-md text-[13px]">
              <option value="">全部状态</option>
              {["pending", "success", "retrying", "dead", "cancelled"].map((s) => <option key={s}>{s}</option>)}
            </select>
            <button onClick={onClose} aria-label="关闭" className="w-8 h-8 rounded-md hover:bg-neutral-100">✕</button>
          </div>
        </div>
        <table className="w-full text-[12.5px]">
          <thead>
            <tr className="text-left text-neutral-400 border-b border-neutral-100">
              {["事件", "状态", "尝试", "最后码", "时间", "操作"].map((h) => <th key={h} className="py-2 font-medium">{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {(deliveries ?? []).map((d) => (
              <tr key={d.id} className="border-b border-neutral-50" data-sb-scope="delivery-row">
                <td className="py-2 font-mono">{d.event}</td>
                <td className="py-2">{d.status}</td>
                <td className="py-2 font-mono">{d.attempt_count}</td>
                <td className="py-2 font-mono">{d.attempts.at(-1)?.code ?? "—"}</td>
                <td className="py-2 font-mono text-neutral-500">{d.created_at.slice(5, 16).replace("T", " ")}</td>
                <td className="py-2">
                  {d.status === "dead"
                    ? <button onClick={() => void replay(d.id)}
                        className="text-brand-600 hover:underline">重放</button>
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
