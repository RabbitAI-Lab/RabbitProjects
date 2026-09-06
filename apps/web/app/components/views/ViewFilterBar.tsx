import { countConditions } from "@rp/shared-state";
import { isLogicNode, type FilterCondition, type FilterLogicNode } from "@rp/shared-state";
import { toast } from "../Toast";
import type { ViewPage } from "./useViewPage";
import { OP_NAMES } from "./view-dsl";

/** TASK-011 §3.1 + 原型 O2：高级筛选入口（⊞ 筛选 ▾ + 条件数徽章）与已激活 chips 行
 *  （视图条件只读 chips + 叠加 chip + 清空 + 命中计数）。C.74。 */

function fieldLabel(fieldKey: string, ctx: ViewPage): string {
  const names: Record<string, string> = {
    "name": "标题", "state": "状态", "state.group": "状态组", "issue_type": "任务类型",
    "priority": "优先级", "assignees": "负责人", "labels": "标签", "created_by": "创建者",
    "start_date": "开始日期", "target_date": "截止日期", "created_at": "创建时间",
    "estimate": "工时", "sequence_id": "编号", "parent": "父任务", "blocked": "被阻塞",
  };
  if (names[fieldKey]) return names[fieldKey];
  return ctx.cfDefs.find((d) => d.key === fieldKey)?.name ?? fieldKey;
}

function valueShort(v: unknown, ctx: ViewPage): string {
  if (v === "@me") return `@${ctx.meName}`;
  const s = String(v);
  const map: Record<string, string> = {
    today: "今天", this_week: "本周", this_month: "本月", overdue: "已逾期",
    __requirement__: "需求", __bug__: "缺陷", __test__: "测试",
    unstarted: "待办", started: "进行中", completed: "已完成", cancelled: "已取消", backlog: "待规划",
  };
  if (map[s]) return map[s];
  if (s === "true" || v === true) return "是";
  if (s === "false" || v === false) return "否";
  const st = ctx.states.find((x) => x.id === s);
  if (st) return st.name;
  const m = ctx.members.find((x) => x.id === s);
  if (m) return m.name;
  const l = ctx.labels.find((x) => x.id === s);
  if (l) return l.name;
  for (const d of ctx.cfDefs) {
    const o = d.options.find((opt) => opt.value === s);
    if (o) return o.label;
  }
  return s;
}

/** 视图条件 chip 文案（占位符「活」解析展示，原型 resolvedChip 同款）。 */
export function viewConditionChips(tree: FilterLogicNode | undefined | null, ctx: ViewPage): string[] {
  const out: string[] = [];
  const walk = (n: FilterLogicNode | undefined | null) => {
    n?.conditions?.forEach((c) => (isLogicNode(c) ? walk(c) : out.push(`${fieldLabel((c as FilterCondition).field, ctx)} ${OP_NAMES[(c as FilterCondition).operator] ?? (c as FilterCondition).operator} ${(c as FilterCondition).value.map((v) => valueShort(v, ctx)).join(" · ")}`)));
  };
  walk(tree);
  return out;
}

/** ⊞ 筛选按钮（filterbar 入口；有临时条件高亮 + 徽章 n）。 */
export function FilterOpenButton({ vp }: { vp: ViewPage }) {
  const n = vp.filterStore.applied ? countConditions(vp.filterStore.applied) : 0;
  return (
    <button type="button" data-sb-scope="filter-open-btn" aria-label="高级筛选"
      onClick={() => window.dispatchEvent(new CustomEvent("rp:open-filter-panel"))}
      className={`h-8 px-2.5 inline-flex items-center gap-1.5 border rounded-md text-[13px] ${n ? "border-brand-500 text-brand-600 bg-brand-50" : "border-neutral-300 text-neutral-700 hover:bg-neutral-50"}`}>
      ⊞ 筛选{n ? <span className="text-[11px] bg-brand-500 text-white rounded-full px-1.5" data-sb-scope="filter-count-badge">{n}</span> : null} ▾
    </button>
  );
}

/** 视图条件 chips 行 + 叠加 chip + 命中计数（C.74）。totalCount 由页面传入（列表/分组 meta）。 */
export function ViewChipsRow({ vp, totalCount }: { vp: ViewPage; totalCount?: number | null }) {
  const view = vp.currentView;
  const chips = viewConditionChips(view?.filters as FilterLogicNode | undefined, vp);
  const tempN = vp.filterStore.applied ? countConditions(vp.filterStore.applied) : 0;
  if (!chips.length && !tempN) return null;
  return (
    <div className="flex items-center flex-wrap gap-1.5 px-5 py-2 border-b border-neutral-200 text-[12px] bg-white shrink-0" aria-label="已选筛选条件" data-sb-scope="view-chiprow">
      {chips.map((s, i) => (
        <span key={i} className="bg-neutral-100 rounded-full px-2.5 py-0.5 text-neutral-700" title={`视图条件（${view?.is_system ? "内置·只读" : "个人视图"}）`}>{s}</span>
      ))}
      {tempN > 0 && (
        <span className="bg-brand-50 text-brand-600 rounded-full px-2.5 py-0.5 inline-flex items-center gap-1">
          ⊞ 叠加 {tempN} 条临时条件
          <button type="button" aria-label="清空临时条件" data-sb-scope="view-tree-clear"
            onClick={() => { vp.clearTempFilters(); toast("已清空临时条件", "info"); }}
            className="w-3.5 h-3.5 inline-flex items-center justify-center rounded-full hover:bg-brand-100">✕</button>
        </span>
      )}
      {totalCount != null && <span className="ml-auto text-neutral-400">{totalCount} 个任务 · 视图 {view?.name ?? "全部"}</span>}
    </div>
  );
}
