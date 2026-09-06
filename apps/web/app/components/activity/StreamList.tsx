/**
 * 动态流三态行 + 日期分区头（COLLAB-003 §3.1，冻结原型 V-ACT 表面 21）。
 *
 *  - kind ∈ {activity, comment, batch} 三态行：头像列 + 内容列 + 任务 chip
 *    （issue_key ↗；软删置灰 ✕ 不可点 + Toast，BR-06；归档可跳转只读态，BR-07）；
 *    batch 行 bg-brand-50/40 + batch_count 加粗 +「▸ 展开明细」（§3.1 批量汇总行）；
 *  - 日期分区头 今天/昨天/M月d日 sticky（role=heading aria-level=3）——与任务时间线
 *    Tab（C.61，IssueDrawer 内置同款文案）同语义同款口径；组件独立抽出为新文件，
 *    未改动 Sprint-2 冻结的 C.61 表面（复用登记见 routes/activity.tsx 头注）；
 *  - 相对时间（刚刚/N 分钟前/昨天 14:02）+ hover 绝对时间 tooltip（title）；
 *  - 系统行 ⚙ 兜底仅 actor 为空（BR-13）；
 *  - 流为 <ol> 语义列表；每行 aria-label 完整朗读（§3.4 无障碍）。
 */
import { useMemo } from "react";
import type {
  ActivityStreamRow,
  BatchStreamRow,
  CommentStreamRow,
  StreamActorRef,
  StreamIssueRef,
  StreamRow,
} from "../../services/api";

const AVATAR_COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899", "#14b8a6", "#f97316"];
const hashColor = (id: string | null | undefined) =>
  AVATAR_COLORS[[...(id ?? "?")].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 0) % AVATAR_COLORS.length];

/** §3.1 时间显示：相对时间（<1h）；今天 HH:MM；昨天 HH:MM；更早 M月d日。 */
export function streamTimeLabel(iso: string, now: Date): string {
  const t = new Date(iso);
  const diffMs = now.getTime() - t.getTime();
  if (diffMs < 60_000) return "刚刚";
  if (diffMs < 3600_000) return `${Math.floor(diffMs / 60_000)} 分钟前`;
  const sameDay = (a: Date, b: Date) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  const hm = `${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}`;
  if (sameDay(t, now)) return hm;
  if (sameDay(t, new Date(now.getTime() - 86_400_000))) return `昨天 ${hm}`;
  return `${t.getMonth() + 1}月${t.getDate()}日 ${hm}`;
}

/** 绝对时间 tooltip（hover；§3.1 表「相对时间+hover 绝对时间」）。 */
export function absoluteTime(iso: string): string {
  const t = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())} ${p(t.getHours())}:${p(t.getMinutes())}`;
}

const sameDay = (a: Date, b: Date) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

/** 日期分区键（今天/昨天/M月d日[ YYYY]）。 */
export function dayKeyOf(iso: string, now: Date): { key: string; label: string; sub: string } {
  const t = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  const md = `${t.getMonth() + 1}月${t.getDate()}日`;
  if (sameDay(t, now)) return { key: `d-${t.toDateString()}`, label: "今天", sub: `${p(t.getMonth() + 1)}-${p(t.getDate())}` };
  if (sameDay(t, new Date(now.getTime() - 86_400_000))) return { key: `d-${t.toDateString()}`, label: "昨天", sub: `${p(t.getMonth() + 1)}-${p(t.getDate())}` };
  return { key: `d-${t.toDateString()}`, label: t.getFullYear() === now.getFullYear() ? md : `${t.getFullYear()}年${md}`, sub: `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())}` };
}

/** activity 行文案兜底（text 缺失时按 verb/field 组句；activity_builder 常写有可读摘要）。 */
const FIELD_LABELS: Record<string, string> = {
  state: "状态", priority: "优先级", assignees: "执行人", labels: "标签", description: "描述",
  start_date: "开始日期", target_date: "截止日期", estimate_minutes: "估算", worklog: "工时",
  relation: "关联", parent: "父任务", archived_at: "归档", name: "标题", issue_type: "类型",
};
function activityText(r: ActivityStreamRow): string {
  if (r.text) return r.text;
  const fl = r.field ? (FIELD_LABELS[r.field] ?? (r.field.startsWith("cf_") ? "自定义字段" : r.field)) : "";
  if (r.verb === "created") return "创建了任务";
  if (r.verb === "deleted") return "删除了任务";
  return fl ? `更新了 ${fl}` : "更新了任务";
}

function Avatar({ actor }: { actor: StreamActorRef | null }) {
  if (!actor) {
    return (
      <span className="w-7 h-7 rounded-full bg-neutral-100 text-neutral-400 inline-flex items-center justify-center text-[13px]"
        title="系统" aria-hidden="true" data-sb-scope="stream-sys-av">⚙</span>
    );
  }
  return (
    <span className="w-7 h-7 rounded-full text-white text-[11px] font-semibold inline-flex items-center justify-center"
      style={{ background: hashColor(actor.id) }} aria-hidden="true">{(actor.display_name || "?").slice(0, 1)}</span>
  );
}

/** 任务 chip（issue_key ↗；软删置灰 ✕ 不可点 + Toast；归档可跳转只读态，BR-06/07）。 */
export function IssueChip({ issue, onOpen }: { issue: StreamIssueRef | null; onOpen: (issue: StreamIssueRef) => void }) {
  if (!issue) return null;
  if (issue.is_deleted) {
    return (
      <button type="button" disabled
        className="inline-flex items-center gap-1 font-mono text-[11.5px] text-neutral-400 bg-neutral-100 rounded-md px-2 py-px line-through cursor-not-allowed"
        data-sb-scope="stream-chip-dead" aria-label={`${issue.issue_key} 已删除`}>
        {issue.issue_key} ✕
      </button>
    );
  }
  return (
    <button type="button"
      className="inline-flex items-center gap-1 font-mono text-[11.5px] text-brand-600 bg-brand-50 rounded-md px-2 py-px hover:bg-brand-100 whitespace-nowrap focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
      data-sb-scope="stream-chip" data-issue-key={issue.issue_key}
      onClick={() => onOpen(issue)}
      aria-label={`打开任务 ${issue.issue_key} ${issue.name}`}>
      {issue.issue_key} ↗
    </button>
  );
}

function CommentIcon() {
  return <span aria-hidden="true" className="text-neutral-500">💬</span>;
}

const rowKeyOf = (r: StreamRow): string => (r.kind === "batch" ? `b:${r.epoch}` : `${r.kind[0]}:${r.id}`);

function ActivityRowItem({ row, now, onOpenIssue, fresh }: { row: ActivityStreamRow; now: Date; onOpenIssue: (i: StreamIssueRef) => void; fresh?: boolean | undefined }) {
  const name = row.actor?.display_name ?? (row.is_system ? "系统" : "已注销用户");
  return (
    <li className={`flex gap-3 py-3 border-b border-neutral-100 items-start ${fresh ? "rp-stream-fresh" : ""}`}
      data-sb-scope="stream-row" data-kind="activity"
      aria-label={`${name}，${absoluteTime(row.created_at)}，${activityText(row)}${row.issue ? `，${row.issue.issue_key}` : ""}`}>
      <span className="font-mono text-[12px] text-neutral-400 w-10 shrink-0 pt-2 text-right" title={absoluteTime(row.created_at)}>
        {streamTimeLabel(row.created_at, now)}
      </span>
      <span className="shrink-0 pt-1"><Avatar actor={row.actor} /></span>
      <div className="flex-1 min-w-0 text-[13.5px] text-neutral-800 leading-relaxed">
        <span className="font-semibold text-neutral-900">{name}</span>{" "}
        <span className="text-neutral-600">{activityText(row)}</span>
        {row.issue && <div className="mt-1"><IssueChip issue={row.issue} onOpen={onOpenIssue} /></div>}
      </div>
    </li>
  );
}

function CommentRowItem({ row, now, onOpenIssue, fresh }: { row: CommentStreamRow; now: Date; onOpenIssue: (i: StreamIssueRef) => void; fresh?: boolean | undefined }) {
  const name = row.actor?.display_name ?? "已注销用户";
  const text = row.text ?? "";
  const action = row.root_id ? "回复了" : "评论了";
  return (
    <li className={`flex gap-3 py-3 border-b border-neutral-100 items-start ${fresh ? "rp-stream-fresh" : ""}`}
      data-sb-scope="stream-row" data-kind="comment"
      aria-label={`${name}，${absoluteTime(row.created_at)}，${action} ${row.issue?.issue_key ?? ""}：${text}`}>
      <span className="font-mono text-[12px] text-neutral-400 w-10 shrink-0 pt-2 text-right" title={absoluteTime(row.created_at)}>
        {streamTimeLabel(row.created_at, now)}
      </span>
      <span className="shrink-0 pt-1"><Avatar actor={row.actor} /></span>
      <div className="flex-1 min-w-0 text-[13.5px] text-neutral-800 leading-relaxed">
        <CommentIcon />{" "}
        <span className="font-semibold text-neutral-900">{name}</span>{" "}
        <span className="text-neutral-600">
          {action} {row.issue ? <span className="font-mono text-[12px]">{row.issue.issue_key}</span> : "任务"}：「{text}」
        </span>
        {row.reply_to_actor && (
          <span className="ml-1.5 text-[11px] text-brand-600 bg-brand-50 rounded px-1.5 py-px" data-sb-scope="stream-reply-badge">
            回复 @{row.reply_to_actor.display_name}
          </span>
        )}
        {row.issue && <div className="mt-1"><IssueChip issue={row.issue} onOpen={onOpenIssue} /></div>}
      </div>
    </li>
  );
}

function BatchRowItem({ row, now, onExpand, fresh }: { row: BatchStreamRow; now: Date; onExpand: (b: BatchStreamRow) => void; fresh?: boolean | undefined }) {
  const name = row.actor?.display_name ?? "系统";
  /** §2.5 大批量折叠：>100 → 「100+」封顶文案。 */
  const countLabel = row.batch_count > 100 ? "100+" : String(row.batch_count);
  return (
    <li className={`flex gap-3 py-3 px-3.5 my-2 items-start bg-brand-50/40 border border-brand-100 rounded-[10px] ${fresh ? "rp-stream-fresh" : ""}`}
      data-sb-scope="stream-row" data-kind="batch"
      aria-label={`${name}，${absoluteTime(row.created_at)}，${row.summary}${row.change_brief ? `，${row.change_brief}` : ""}`}>
      <span className="font-mono text-[12px] text-neutral-400 w-10 shrink-0 pt-2 text-right" title={absoluteTime(row.created_at)}>
        {streamTimeLabel(row.created_at, now)}
      </span>
      <span className="shrink-0 pt-1"><Avatar actor={row.actor} /></span>
      <div className="flex-1 min-w-0 text-[13.5px] text-neutral-800 leading-relaxed">
        <span className="font-semibold text-neutral-900">{name}</span>{" "}
        <span className="font-bold tabular-nums" data-sb-scope="stream-batch-count">
          {row.summary.replace(/\d+/, countLabel)}
        </span>
        {row.change_brief && <span className="text-neutral-500"> · {row.change_brief}</span>}{" "}
        <button type="button" aria-haspopup="dialog" data-sb-scope="stream-batch-expand" data-epoch={row.epoch}
          onClick={() => onExpand(row)}
          className="text-[12.5px] text-brand-600 inline-flex items-center gap-1 whitespace-nowrap hover:underline">
          ▸ 展开明细
        </button>
      </div>
    </li>
  );
}

/** 流列表：按行内 created_at 就序渲染（服务端全局时间倒序，BR-03 零前端排序）。 */
export function StreamList({ rows, now, onOpenIssue, onExpandBatch, freshIds }: {
  rows: StreamRow[];
  now: Date;
  onOpenIssue: (issue: StreamIssueRef) => void;
  onExpandBatch: (row: BatchStreamRow) => void;
  /** 刚划入的新行（≤5 条顶部划入动画标记，§3.3）。 */
  freshIds?: Set<string>;
}) {
  const items = useMemo(() => {
    const out: React.ReactNode[] = [];
    let lastDay = "";
    for (const r of rows) {
      const d = dayKeyOf(r.created_at, now);
      if (d.key !== lastDay) {
        lastDay = d.key;
        out.push(
          <li key={d.key} className="sticky top-0 z-[5] bg-neutral-50 list-none" role="heading" aria-level={3}
            data-sb-scope="stream-day">
            <div className="flex items-center gap-2 pt-3.5 pb-1.5 border-b border-neutral-200 text-[12px] text-neutral-400">
              <span className="font-semibold text-neutral-700">{d.label}</span>
              <span>{d.sub}</span>
            </div>
          </li>,
        );
      }
      const fresh = freshIds?.has(rowKeyOf(r));
      out.push(
        r.kind === "batch"
          ? <BatchRowItem key={rowKeyOf(r)} row={r} now={now} onExpand={onExpandBatch} fresh={fresh} />
          : r.kind === "comment"
            ? <CommentRowItem key={rowKeyOf(r)} row={r} now={now} onOpenIssue={onOpenIssue} fresh={fresh} />
            : <ActivityRowItem key={rowKeyOf(r)} row={r} now={now} onOpenIssue={onOpenIssue} fresh={fresh} />,
      );
    }
    return out;
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, now, freshIds]);

  return <ol className="list-none p-0 m-0" aria-label="项目动态流" data-sb-scope="stream-list">{items}</ol>;
}

export { rowKeyOf };
