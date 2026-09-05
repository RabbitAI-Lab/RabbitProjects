import { useEffect, useState } from "react";
import { DeadLetterAPI, unwrap, type DeadLetterRow } from "../services/api";
import type { ApiError } from "../services/axios";
import { toast } from "../components/Toast";

/** 管理端死信补偿页（C.62 / TASK-010 §3.2；布局 O2 原型先行定义）。
 *  - 数据源 Redis hash 元数据（队列 activity.dlq）：时间 / event_key / 错误摘要 / 重试次数（3/3 耗尽红显）
 *  - 操作：单条重放（幂等 dedup_skipped）/ 批量重放（≤100）/ 丢弃（二次确认 + 留痕）
 *  - 堆积计数（>100 触发 SERVER_QUEUE_ERROR 告警徽标）；空态「队列健康」。 */
export default function AdminDeadLetters() {
  const [rows, setRows] = useState<DeadLetterRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      const r = await DeadLetterAPI.list({ per_page: 100 });
      setRows(unwrap<DeadLetterRow[]>(r) ?? []);
    } catch (e: unknown) {
      const err = e as ApiError;
      toast(err?.message ?? "加载失败", "error");
    } finally { setLoaded(true); }
  }
  useEffect(() => {
    const t = setTimeout(() => { void load(); }, 0);
    return () => clearTimeout(t);
  }, []);

  /** C.62 单条重放（幂等：event_key 命中则 dedup_skipped）。 */
  async function replay(row: DeadLetterRow) {
    if (busy) return;
    setBusy(true);
    try {
      const r = await DeadLetterAPI.replay(row.id);
      const res = unwrap<{ replayed: boolean; dedup_skipped: boolean }>(r);
      toast(res?.dedup_skipped ? "已重放（幂等命中：dedup_skipped）" : "已重放死信", "ok");
      await load();
    } catch (e: unknown) {
      const err = e as ApiError;
      toast(err?.message ?? "重放失败", "error");
    } finally { setBusy(false); }
  }

  /** C.62 丢弃（二次确认 + 留痕）。 */
  async function discard(row: DeadLetterRow) {
    if (busy) return;
    if (!confirm(`确认丢弃该死信？此操作将留痕。`)) return;
    setBusy(true);
    try {
      await DeadLetterAPI.discard(row.id);
      toast("死信已丢弃（留痕）", "warning");
      await load();
    } catch (e: unknown) {
      const err = e as ApiError;
      toast(err?.message ?? "丢弃失败", "error");
    } finally { setBusy(false); }
  }

  /** C.62 批量重放（≤100；请求体携带 id 数组）。 */
  async function bulkReplay() {
    if (busy || rows.length === 0) return;
    setBusy(true);
    try {
      const r = await DeadLetterAPI.bulkReplay(rows.map((x) => x.id));
      const res = unwrap<{ replayed: number; skipped: number }>(r);
      toast(`批量重放 ${res?.replayed ?? 0} 条完成${res?.skipped ? `（跳过 ${res.skipped} 条）` : ""}`, "ok");
      await load();
    } catch (e: unknown) {
      const err = e as ApiError;
      toast(err?.details?.[0]?.message ?? err?.message ?? "批量重放失败", "error");
    } finally { setBusy(false); }
  }

  return (
    <div className="flex flex-col h-screen">
      <div className="h-14 border-b border-neutral-200 flex items-center gap-2.5 px-5 bg-white shrink-0">
        <span className="text-[15px] font-semibold">RabbitProjects 管理端</span>
        <span className="text-[11px] text-neutral-400 bg-neutral-100 rounded px-1.5 py-0.5 font-mono">权限码 system.audit.read</span>
        <div className="ml-auto flex items-center gap-2.5">
          {rows.length > 100 && (
            <span className="text-[11px] text-red-700 bg-red-50 border border-red-200 rounded px-2 py-0.5" data-sb-scope="dlq-alert">SERVER_QUEUE_ERROR</span>
          )}
          <span className="text-[12px] text-neutral-400">堆积 {rows.length} 条</span>
        </div>
      </div>
      <div className="p-5 overflow-auto flex-1">
        <div className="flex items-center gap-2 pb-3 border-b border-neutral-200 mb-3">
          <span className="text-[15px] font-semibold">死信补偿</span>
          <span className="text-[13px] text-neutral-400">队列 activity.dlq · 元数据 Redis hash（TTL 7 天）</span>
          <button onClick={() => void bulkReplay()} disabled={rows.length === 0 || busy} data-sb-scope="dlq-bulk"
            className="ml-auto h-[30px] px-3 border border-neutral-300 rounded-md text-[13px] hover:bg-neutral-50 disabled:opacity-40 disabled:cursor-not-allowed">批量重放（≤100）</button>
        </div>
        {!loaded ? (
          <div className="text-[13px] text-neutral-400">加载中…</div>
        ) : rows.length === 0 ? (
          /* C.62 空态：无死信 → 队列健康空态 */
          <div className="flex flex-col items-center gap-2 py-16 text-neutral-400">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#d4d4d4" strokeWidth="2"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>
            <div className="text-[15px] font-semibold text-neutral-600" data-sb-scope="dlq-empty">没有死信</div>
            <div className="text-[13px]">队列 activity.dlq 为空 · 投递链路健康</div>
          </div>
        ) : (
          <table className="w-full border-collapse bg-white border border-neutral-200 rounded-lg overflow-hidden">
            <thead><tr>
              {["时间", "event_key", "错误摘要", "重试", "操作"].map((h) => (
                <th key={h} className={`text-left px-3 py-2.5 border-b border-neutral-200 text-[11px] font-semibold text-neutral-400 uppercase tracking-wider bg-white ${h === "操作" ? "text-right" : ""}`}>{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {rows.map((m) => (
                <tr key={m.id} className="hover:bg-neutral-50 align-top" data-sb-scope="dlq-row">
                  <td className="px-3 py-2.5 border-b border-neutral-100 font-mono text-[12px] whitespace-nowrap" data-sb-scope="dlq-time">
                    {(m.first_failed_at ?? "").slice(0, 19).replace("T", " ")}
                  </td>
                  <td className="px-3 py-2.5 border-b border-neutral-100">
                    <div className="font-mono text-[11px] text-neutral-500 break-all max-w-[220px]" data-sb-scope="dlq-event-key">{m.event_key}</div>
                    <div className="font-mono text-[11px] text-neutral-400">{m.id}</div>
                  </td>
                  <td className="px-3 py-2.5 border-b border-neutral-100 max-w-[280px]">
                    <details>
                      <summary className="cursor-pointer text-[12.5px] text-neutral-600">{m.error_summary}</summary>
                      <div className="font-mono text-[11px] text-neutral-400 mt-1 break-all">queue={m.queue}；TTL 7 天</div>
                    </details>
                  </td>
                  <td className="px-3 py-2.5 border-b border-neutral-100">
                    <span className={`tabular-nums text-[13px] ${m.retries >= 3 ? "text-red-600 font-semibold" : ""}`} data-sb-scope="dlq-retries">{m.retries}/3</span>
                  </td>
                  <td className="px-3 py-2.5 border-b border-neutral-100 text-right whitespace-nowrap">
                    <button onClick={() => void replay(m)} data-sb-scope="dlq-replay"
                      className="h-[28px] px-2.5 border border-neutral-300 rounded-md text-[12px] hover:bg-neutral-50">↻ 重放</button>{" "}
                    <button onClick={() => void discard(m)} data-sb-scope="dlq-discard"
                      className="h-[28px] px-2.5 border border-neutral-300 rounded-md text-[12px] text-red-600 hover:bg-red-50">🗑 丢弃</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
