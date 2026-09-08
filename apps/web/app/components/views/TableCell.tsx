import { AvatarStack } from "../issue-dialogs";
import { StateBadge } from "../StateBadge";
import { toast } from "../Toast";
import { PRIORITY_COLUMNS } from "./view-dsl";
import type { Issue } from "@rp/types";

/** 表格/列表共用的 7 列核心列渲染器（ADR-0030 修复列表布局漏接 display_props.columns）。
 *  与显示配置 `display_props.columns` 同步；未知列回退为「—」。
 *  依赖（nameOf / today / labels）由调用方注入：表格与列表的成员查表来源不同
 *  （表格走 vp.members、列表曾用本地 state，现统一走 vp.members）。 */

export interface RenderCellCtx {
  nameOf: (uid: string) => string;
  today: string;
  labels: Array<{ id: string; name: string; color: string }>;
}

export function renderTableCell(it: Issue, col: string, ctx: RenderCellCtx) {
  switch (col) {
    case "key":
      // 编号即按钮：点击复制（C.38 列表/G1 parity——1f520bb 共用化重构曾丢此行为）
      return (
        <button type="button" className="font-mono text-xs text-neutral-500 hover:text-brand-600 whitespace-nowrap"
          title="点击复制编号"
          onClick={(e) => {
            e.stopPropagation();
            navigator.clipboard?.writeText(it.issue_key)
              .then(() => toast(`已复制 ${it.issue_key}`))
              .catch(() => toast(`复制失败：${it.issue_key}`, "error"));
          }}>{it.issue_key}</button>
      );
    case "title":
      return <span className="text-[13px] text-neutral-900 truncate">{it.name}</span>;
    case "state":
      return <StateBadge group={it.state_group ?? "unstarted"} name={it.state_name ?? "—"} />;
    case "assignees":
      return it.assignee_ids?.length
        ? <AvatarStack names={it.assignee_ids.map(ctx.nameOf)} size={20} />
        : <span className="inline-flex items-center rounded-full bg-neutral-100 px-2 py-0.5 text-[12px] text-neutral-500">未指派</span>;
    case "due": {
      if (!it.target_date) return <span className="text-neutral-400">—</span>;
      const od = it.target_date < ctx.today && it.state_group !== "completed" && it.state_group !== "cancelled";
      return <span className={`font-mono text-xs tabular-nums ${od ? "text-red-500 font-semibold" : "text-neutral-500"}`}>{it.target_date.slice(5)}</span>;
    }
    case "priority": {
      const p = PRIORITY_COLUMNS.find((x) => x.key === it.priority);
      return p ? (
        <span className="inline-flex items-center gap-1.5 text-[13px] text-neutral-600 whitespace-nowrap">
          <span className="w-2 h-2 rounded-full" style={{ background: p.color }} />{p.name}
        </span>
      ) : <span className="text-neutral-400">—</span>;
    }
    case "labels":
      return it.label_ids?.length ? (
        <span className="inline-flex gap-1">
          {it.label_ids.map((id) => {
            const l = ctx.labels.find((x) => x.id === id);
            return l ? <span key={id} className="text-[11px] px-1.5 rounded text-white h-[18px] inline-flex items-center" style={{ background: l.color }}>{l.name}</span> : null;
          })}
        </span>
      ) : <span className="text-neutral-400">—</span>;
    default:
      return <span className="text-neutral-400">—</span>;
  }
}
