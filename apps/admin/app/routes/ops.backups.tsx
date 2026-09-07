import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";

interface BackupRow {
  id: string; kind: string; status: string;
  started_at: string; finished_at: string | null;
  dump_size_bytes: number | null; checksum_sha256: string;
  object_key: string; error: string; drill_report: Record<string, unknown>;
}

const STATUS_STYLE: Record<string, string> = {
  success: "bg-green-100 text-green-700", failed: "bg-red-100 text-red-700",
  running: "bg-amber-100 text-amber-700",
};

/** 备份管理页（INFRA-005 §3.2——C.135：记录列表 + 立即备份 + 连败红线）。 */
export default function OpsBackups() {
  const [rows, setRows] = useState<BackupRow[]>([]);
  const [streak, setStreak] = useState(0);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(() => {
    api<BackupRow[]>("GET", "/instances/backups/").then((r) => {
      setLoaded(true);
      if (r.status === "success" && r.data) {
        setRows(r.data);
        setStreak(Number((r.meta as { failure_streak?: number })?.failure_streak ?? 0));
      } else setMsg(r.error?.message ?? "加载失败");
    });
  }, []);

  useEffect(load, [load]);

  async function trigger() {
    setBusy(true); setMsg("");
    const r = await api<{ queued: boolean }>("POST", "/instances/backups/trigger/", {});
    setBusy(false);
    setMsg(r.status === "success" ? "已入队（202）——数分钟内可在列表看到本次记录" : r.error?.message ?? "触发失败");
  }

  return (
    <section data-sb-scope="backups-page">
      <div className="flex items-center gap-3 mb-1">
        <h1 className="text-lg font-semibold">备份管理</h1>
        <button onClick={trigger} disabled={busy} data-sb-scope="backup-trigger"
          className="h-8 px-3 rounded-md bg-brand-500 text-white text-[13px] disabled:opacity-50">
          {busy ? "触发中…" : "立即备份"}
        </button>
        {msg && <span className="text-[12px] text-neutral-500" data-sb-scope="backup-msg" role="status">{msg}</span>}
      </div>
      <p className="text-[12px] text-neutral-400 mb-4">每日 03:07 全量（beat）；30 天保留双保险；恢复演练 restore-drill.sh</p>
      {streak >= 2 && (
        <div className="mb-3 px-3 h-9 flex items-center rounded-md bg-red-50 text-red-700 text-[13px]"
             data-sb-scope="backup-streak" role="alert">
          ⚠ 连续 {streak} 次失败——已通知 WS Admin，发布 checklist 阻塞
        </div>
      )}
      {!loaded ? <div className="text-sm text-neutral-500">加载中…</div> : (
        <table className="w-full border border-neutral-200 rounded-lg bg-white text-[13px]" data-sb-scope="backups-table">
          <thead><tr className="bg-neutral-50 text-left text-neutral-500">
            {["类型", "状态", "开始", "大小", "SHA-256", "对象键 / 失败原因"].map((h) => (
              <th key={h} className="px-3 py-2 font-medium">{h}</th>))}
          </tr></thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={6} className="px-3 py-8 text-center text-neutral-400" data-sb-scope="backups-empty">
                暂无备份记录——点「立即备份」创建第一份</td></tr>)}
            {rows.map((b) => (
              <tr key={b.id} className="border-t border-neutral-100">
                <td className="px-3 py-1.5">{b.kind}</td>
                <td className="px-3 py-1.5"><span className={`px-2 py-0.5 rounded text-[12px] ${STATUS_STYLE[b.status] ?? ""}`}>{b.status}</span></td>
                <td className="px-3 py-1.5">{b.started_at.replace("T", " ").slice(0, 19)}</td>
                <td className="px-3 py-1.5">{b.dump_size_bytes ? `${(b.dump_size_bytes / 1048576).toFixed(1)}MB` : "—"}</td>
                <td className="px-3 py-1.5 font-mono text-[12px]">{b.checksum_sha256 || "—"}</td>
                <td className="px-3 py-1.5 text-neutral-500">{b.error || b.object_key}</td>
              </tr>))}
          </tbody>
        </table>)}
    </section>
  );
}
