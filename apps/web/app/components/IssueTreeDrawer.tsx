import { useEffect, useState } from "react";
import { IssueAPI, ProjectMemberAPI, unwrap, type SubtreeData, type SubtreeNode, type SubtreeRoot } from "../services/api";

/** 状态组 → 圆点色（与原型 STATES 同源；IssueDrawer.STATE_COLOR 同款） */
const STATE_COLOR: Record<string, string> = {
  unstarted: "#9ca3af", started: "#3b82f6", completed: "#10b981", cancelled: "#f87171", backlog: "#a1a1aa",
};

function hashColor(id: string): string {
  const cols = ["#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899", "#14b8a6", "#f97316"];
  let h = 0;
  for (const c of String(id)) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return cols[h % 7] ?? "#3b82f6";
}

/** 全屏树抽屉（TASK-004 §3.3 / C.40）。
 *  - 宽 min(960px, 100vw - 48px)，z 在 IssueDrawer(50) 之上、节点详情抽屉(70)之下；
 *  - 头部统计来自响应 stats（含根口径：total/completed 均计入任务自身，§4.2.2 契约要点 2）；
 *  - 节点行：状态圆点 + 编号 + 标题(truncate) + 负责人头像 + 子任务圆环，缩进 24px/层；
 *  - 节点点击 → 打开该任务详情 Drawer（返回时树状态保留——树不卸载）；
 *  - 截断黄条（meta.truncated=true，BR-11：500 节点上限）/ 骨架 / 空态 / 失败重试（§3.6）。
 *
 *  API 平铺 nodes 按 (depth, sequence_id) 排序——渲染用 DFS：先按 parent_id 建邻接表，
 *  再从 root 深度优先展开，保证兄弟按 sequence 有序、层序正确。 */
export function IssueTreeDrawer({ issueId, issueName, slug, projectId, onClose, onOpenIssue, onAddFirst }: {
  issueId: string;
  issueName: string;
  slug: string;
  projectId: string;
  onClose: () => void;
  onOpenIssue: (id: string) => void;
  /** 空态「添加第一个子任务」：focus 到详情抽屉的添加子任务输入行（§3.3 空态规格） */
  onAddFirst?: () => void;
}) {
  const [data, setData] = useState<SubtreeData | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [retryTick, setRetryTick] = useState(0);
  const [members, setMembers] = useState<Array<{ id: string; user: { id: string; display_name: string } }>>([]);

  const load = async () => {
    setLoading(true); setError(null);
    try {
      const r = await IssueAPI.subtree(slug, projectId, issueId);
      setData(unwrap<SubtreeData>(r));
      setTruncated(Boolean((r as unknown as { meta?: { truncated?: boolean } }).meta?.truncated));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const handle = setTimeout(() => { void load(); }, 0);
    return () => clearTimeout(handle);
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [issueId, slug, projectId, retryTick]);

  // 负责人头像解析（subtree 只给 assignee_ids）
  useEffect(() => {
    ProjectMemberAPI.list(slug, projectId, { per_page: 100 })
      .then((r) => setMembers(unwrap<typeof members>(r) ?? []))
      .catch(() => {});
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [slug, projectId]);

  // Esc 关闭（§3.7 无障碍：键盘可达）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  // DFS 平铺（root 在前，兄弟按返回序）
  const ordered: Array<SubtreeNode | SubtreeRoot> = (() => {
    if (!data?.root) return [];
    const byParent = new Map<string, SubtreeNode[]>();
    for (const n of data.nodes) {
      const list = byParent.get(n.parent_id ?? "") ?? [];
      list.push(n);
      byParent.set(n.parent_id ?? "", list);
    }
    const out: Array<SubtreeNode | SubtreeRoot> = [data.root];
    const walk = (parent: string) => {
      for (const child of byParent.get(parent) ?? []) { out.push(child); walk(child.id); }
    };
    walk(data.root.id);
    return out;
  })();

  const childCount = (n: SubtreeNode): { total: number; done: number } => {
    const kids = data?.nodes.filter((x) => x.parent_id === n.id) ?? [];
    // BR-05：cancelled 不进分子分母（有效子任务完成率）
    const valid = kids.filter((k) => k.state_group !== "cancelled");
    return { total: valid.length, done: valid.filter((k) => k.state_group === "completed").length };
  };

  const nameOf = (uid: string) => members.find((m) => m.user.id === uid)?.user.display_name;

  return (
    <div className="fixed inset-0 z-[60] flex justify-end">
      <div className="absolute inset-0 bg-black/28" onClick={onClose} aria-hidden="true" />
      <aside
        className="relative w-[min(960px,calc(100vw-48px))] max-w-full bg-white border-l border-neutral-200 shadow-lg flex flex-col"
        role="dialog"
        aria-modal="true"
        aria-label="任务树"
        data-sb-scope="tree-drawer"
      >
        {/* 头部：标题 + 统计（stats 含根口径，font-mono） */}
        <div className="flex items-start gap-3 px-5 py-4 border-b border-neutral-200">
          <div className="flex-1 min-w-0">
            <div className="text-base font-semibold truncate">{issueName} 的任务树</div>
            <div className="mt-0.5 font-mono text-[12px] text-neutral-400 tabular-nums" data-sb-scope="tree-stats">
              {loading
                ? "…"
                : truncated
                  ? `${ordered.length}+ 个任务（含自身）`
                  : data?.stats
                    ? `${data.stats.total} 个任务（含自身）· ${data.stats.completed} 已完成 · 最深 ${data.stats.max_depth} 层`
                    : "—"}
            </div>
          </div>
          <button onClick={onClose} aria-label="关闭" data-sb-scope="tree-close"
            className="w-7 h-7 flex items-center justify-center text-neutral-500 hover:text-neutral-900">✕</button>
        </div>

        <div className="flex-1 overflow-y-auto p-3" data-sb-scope="tree-body">
          {/* 加载骨架：3 层 × 5 行（§3.3） */}
          {loading && (
            <div className="flex flex-col gap-1" aria-label="加载中" data-sb-scope="tree-skeleton">
              {[0, 1, 2].map((layer) =>
                Array.from({ length: 5 }, (_, i) => (
                  <div key={`${layer}-${i}`} className="h-10 rounded-lg animate-pulse bg-neutral-100"
                    style={{ marginLeft: 12 + layer * 24, width: `calc(100% - ${12 + layer * 24}px)` }} />
                )),
              )}
            </div>
          )}

          {/* 失败：alert + error.message + 重试（§3.6） */}
          {!loading && error && (
            <div className="flex flex-col items-center gap-3 py-14 text-neutral-500" data-sb-scope="tree-error">
              <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>
              <div className="text-[13px]">{error}</div>
              <button onClick={() => setRetryTick((n) => n + 1)}
                className="h-[30px] px-3 bg-brand-500 text-white rounded-md text-[13px] hover:bg-brand-600">重试</button>
            </div>
          )}

          {/* 空态：「暂无子任务」+「添加第一个子任务」（focus 到详情抽屉添加行） */}
          {!loading && !error && ordered.length <= 1 && (
            <div className="flex flex-col items-center gap-2 py-14 text-neutral-500" data-sb-scope="tree-empty">
              <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#d4d4d4" strokeWidth="2"><rect width="8" height="4" x="8" y="2" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4M12 16h4M8 11h.01M8 16h.01"/></svg>
              <div className="text-[15px] font-semibold text-neutral-700">暂无子任务</div>
              {onAddFirst && (
                <button onClick={onAddFirst} data-sb-scope="tree-add-first"
                  className="mt-1 h-[32px] px-3.5 bg-brand-500 text-white rounded-md text-[13px] font-medium hover:bg-brand-600">+ 添加第一个子任务</button>
              )}
            </div>
          )}

          {/* 节点行：状态圆点 + 编号 + 标题 + 负责人头像 + 子任务圆环；缩进 24px/层 */}
          {!loading && !error && ordered.length > 1 && ordered.map((n) => {
            const kids = childCount(n);
            const firstAssignee = n.assignee_ids?.[0];
            const assigneeName = firstAssignee ? nameOf(firstAssignee) : undefined;
            const full = kids.total > 0 && kids.done === kids.total;
            return (
              <div
                key={n.id}
                role="button"
                tabIndex={0}
                data-sb-scope="tree-node"
                data-node-id={n.id}
                onClick={() => onOpenIssue(n.id)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); onOpenIssue(n.id); } }}
                className="flex items-center gap-2.5 min-h-10 px-3 py-1 rounded-lg text-[13px] cursor-pointer hover:bg-neutral-50"
                style={{ marginLeft: 12 + n.depth * 24 }}
                title={`${n.issue_key} · ${n.name}`}
              >
                <span className="w-[9px] h-[9px] rounded-full shrink-0" style={{ background: STATE_COLOR[n.state_group ?? ""] ?? "#9ca3af" }} aria-label={`状态 ${n.state_group ?? ""}`} />
                <span className="font-mono text-[12px] text-neutral-400 shrink-0">{n.issue_key}</span>
                <span className={`flex-1 min-w-0 truncate ${n.depth === 0 ? "font-medium" : ""}`}>{n.name}</span>
                {assigneeName && (
                  <span className="w-5 h-5 rounded-full text-white text-[10px] font-semibold flex items-center justify-center shrink-0"
                    style={{ background: hashColor(firstAssignee!) }} title={assigneeName} aria-hidden="true">
                    {Array.from(assigneeName)[0]}
                  </span>
                )}
                {kids.total > 0 && (
                  <span className="inline-flex items-center gap-1.5 text-[12px] text-neutral-600 tabular-nums shrink-0"
                    role="img" aria-label={`子任务 ${kids.total} 个，已完成 ${kids.done} 个`}>
                    <span className="w-4 h-4 rounded-full shrink-0"
                      style={full
                        ? { border: "2px solid #10b981", background: "#10b981" }
                        : { border: "2px solid #e5e5e5", borderTopColor: kids.done > 0 ? "#10b981" : "#9ca3af" }} />
                    {kids.done}/{kids.total}
                  </span>
                )}
              </div>
            );
          })}

          {/* 截断黄条（meta.truncated=true，BR-11） */}
          {!loading && !error && truncated && (
            <div className="flex items-center gap-2 my-2.5 px-3.5 py-2 rounded-lg border border-amber-200 bg-amber-50 text-amber-800 text-[13px]" data-sb-scope="tree-truncated" role="status">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4M12 17h.01"/></svg>
              结果超过 500 节点已截断 · 建议按状态 / 负责人筛选缩小范围
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}
