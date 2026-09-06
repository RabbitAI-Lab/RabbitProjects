/**
 * 批量明细抽屉（COLLAB-003 §3.2）——?epoch= 轻量拉取（BR-05：仅摘要字段，不重复全字段）。
 *
 *  - 头部：操作者 + 汇总文案 + 绝对时间 + ✕；
 *  - 变更摘要：同 epoch 各条 field/old/new 的交集描述（后端 change_brief：全同直出 /
 *    「多种变更」）；
 *  - 明细行：issue_key + 标题 + ↗ 跳转（openIssueDrawer）；100 上限截断提示
 *    （meta.truncated，§2.6/§2.5「100 条上限，剩余 N 条已截断」）。
 */
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { ActivityStreamAPI, unwrap, type BatchDetailRow, type BatchStreamRow } from "../../services/api";

export function BatchDetailDrawer({ slug, projectId, batch, onClose, onOpenIssue }: {
  slug: string;
  projectId: string;
  batch: BatchStreamRow;
  onClose: () => void;
  /** 行点击 → 打开任务详情 Drawer（不离开流页，§3.3）。 */
  onOpenIssue: (issueId: string) => void;
}) {
  const [rows, setRows] = useState<BatchDetailRow[]>([]);
  const [meta, setMeta] = useState<{ count: number; total_count: number; truncated: boolean; limit: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancel = false;
    setLoading(true);
    setErr(null);
    ActivityStreamAPI.batchDetail(slug, projectId, batch.epoch, { per_page: 100 })
      .then((r) => {
        if (cancel) return;
        setRows(unwrap<BatchDetailRow[]>(r) ?? []);
        const m = (r as unknown as { meta: Record<string, unknown> }).meta ?? {};
        setMeta({
          count: Number(m.count ?? 0),
          total_count: Number(m.total_count ?? 0),
          truncated: Boolean(m.truncated),
          limit: Number(m.limit ?? 100),
        });
      })
      .catch((e: unknown) => {
        if (!cancel) setErr(e instanceof Error ? e.message : "明细加载失败");
      })
      .finally(() => { if (!cancel) setLoading(false); });
    return () => { cancel = true; };
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [slug, projectId, batch.epoch]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const node = (
    <div className="fixed inset-0 z-50 flex justify-end" data-sb-scope="batch-drawer">
      <div className="absolute inset-0 bg-black/25" onClick={onClose} aria-hidden="true" />
      <aside className="relative w-[460px] max-w-[100vw] bg-white border-l border-neutral-200 shadow-xl flex flex-col"
        role="dialog" aria-modal="true" aria-label="批量明细">
        {/* 头部：操作者 + 汇总 + 绝对时间（§3.2 表） */}
        <div className="flex items-start gap-2.5 px-5 py-3.5 border-b border-neutral-200">
          <div className="min-w-0">
            <div className="text-[13px] font-semibold truncate">
              {batch.actor?.display_name ?? "系统"} · {batch.summary}
            </div>
            <div className="text-[11.5px] text-neutral-400 mt-0.5" data-sb-scope="batch-drawer-at">
              {new Date(batch.created_at).toLocaleString("zh-CN", { hour12: false })} · epoch {batch.epoch}
            </div>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭"
            className="ml-auto w-7 h-7 inline-flex items-center justify-center text-neutral-500 hover:text-neutral-900">✕</button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-3.5">
          {/* 变更摘要：全同直出 /「多种变更」（§3.2） */}
          <div className="text-[13px] text-neutral-700 bg-neutral-50 rounded-md px-3 py-2" data-sb-scope="batch-drawer-brief">
            变更摘要：{batch.change_brief ?? "多种变更"}
          </div>
          {loading ? (
            <ul className="mt-3 flex flex-col gap-2" aria-busy="true" data-sb-scope="batch-drawer-skel">
              {Array.from({ length: 5 }).map((_, i) => <li key={i} className="h-9 rounded-md bg-neutral-100 animate-pulse" />)}
            </ul>
          ) : err ? (
            <div className="mt-6 text-center text-[13px] text-neutral-500" role="alert" data-sb-scope="batch-drawer-err">
              <div>{err}</div>
              <button type="button" className="mt-2 h-[30px] px-3 border border-neutral-300 rounded-md" data-sb-scope="batch-drawer-retry"
                onClick={() => setErr(null)}>重试</button>
            </div>
          ) : rows.length === 0 ? (
            <div className="mt-6 text-center text-[13px] text-neutral-400" data-sb-scope="batch-drawer-empty">明细已不可用</div>
          ) : (
            <div className="mt-3 flex flex-col">
              {rows.map((it) => (
                <button type="button" key={it.issue_id} data-sb-scope="batch-drawer-row" data-issue-key={it.issue_key}
                  onClick={() => onOpenIssue(it.issue_id)}
                  className="w-full flex items-center gap-2.5 px-1.5 py-2 text-left border-b border-neutral-100 hover:bg-neutral-50 rounded-sm">
                  <span className="font-mono text-[12px] text-brand-600 shrink-0">{it.issue_key}</span>
                  <span className="flex-1 min-w-0 truncate text-[13px] text-neutral-800">{it.name}</span>
                  <span className="text-neutral-300" aria-hidden="true">↗</span>
                </button>
              ))}
              {meta?.truncated && (
                <div className="mt-3 flex items-center gap-1.5 text-[12px] text-neutral-500 bg-amber-50 border border-amber-200 text-amber-800 rounded-md px-3 py-2"
                  data-sb-scope="batch-drawer-truncated">
                  ⚠ {meta.limit} 条上限，剩余 {Math.max(0, meta.total_count - rows.length)} 条已截断（BR-11 truncated）
                </div>
              )}
            </div>
          )}
        </div>
      </aside>
    </div>
  );
  return createPortal(node, document.body);
}
