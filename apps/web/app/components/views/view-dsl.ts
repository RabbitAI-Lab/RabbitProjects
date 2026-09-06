/** TASK-011 §1.2/§2.3 前端镜像常量：字段目录 / 操作符×类型表 / 值域标签。
 *  内置字段 = BUILTIN_FIELD_PATHS（compiler.py）镜像；自定义 = Schema API —— 双源
 *  （§4.4.2「字段选择器数据 = Schema API + BUILTIN_FIELD_SCHEMA 常量」）。
 *  操作符集与 compiler.OPERATORS_BY_TYPE + BUILTIN_OPERATOR_OVERRIDES 逐行对齐。 */

export type FieldType =
  | "text" | "textarea" | "url" | "number" | "date" | "checkbox"
  | "select" | "multi_select" | "member" | "member_multi" | "currency"
  | "select_ref" | "multi_select_ref";

export interface FieldDef {
  key: string;
  name: string;
  type: FieldType;
  /** 类型色点（原型 FIELD_CATALOG 同款） */
  color: string;
  cf?: boolean;
}

/** TASK-011 §4.3.1 BUILTIN_FIELD_PATHS 白名单镜像（key → 展示名/控件类型/色点）。 */
export const BUILTIN_FIELDS: FieldDef[] = [
  { key: "name", name: "标题", type: "text", color: "#3b82f6" },
  { key: "state", name: "状态", type: "select_ref", color: "#10b981" },
  { key: "state.group", name: "状态组", type: "select", color: "#10b981" },
  { key: "issue_type", name: "任务类型", type: "select_ref", color: "#8b5cf6" },
  { key: "priority", name: "优先级", type: "select", color: "#f59e0b" },
  { key: "assignees", name: "负责人", type: "member_multi", color: "#ec4899" },
  { key: "labels", name: "标签", type: "multi_select_ref", color: "#14b8a6" },
  { key: "created_by", name: "创建者", type: "member", color: "#ec4899" },
  { key: "start_date", name: "开始日期", type: "date", color: "#f97316" },
  { key: "target_date", name: "截止日期", type: "date", color: "#f97316" },
  { key: "created_at", name: "创建时间", type: "date", color: "#f97316" },
  { key: "estimate", name: "工时", type: "number", color: "#6366f1" },
  { key: "sequence_id", name: "编号", type: "number", color: "#6366f1" },
  { key: "parent", name: "父任务", type: "select_ref", color: "#8c8c8c" },
  { key: "blocked", name: "被阻塞", type: "checkbox", color: "#ef4444" },
];

/** 操作符 × 类型全表（TASK-011 §2.3 + compiler.OPERATORS_BY_TYPE 同源）。 */
export const OPS_BY_TYPE: Record<string, string[]> = {
  text: ["contains", "not_contains", "eq", "neq", "is_empty", "is_not_empty"],
  textarea: ["contains", "not_contains", "eq", "neq", "is_empty", "is_not_empty"],
  url: ["contains", "not_contains", "eq", "neq", "is_empty", "is_not_empty"],
  number: ["eq", "neq", "gt", "gte", "lt", "lte", "between", "is_empty"],
  select: ["in", "not_in", "is_empty", "is_not_empty"],
  multi_select: ["contains_any", "contains_all", "not_contains", "is_empty"],
  date: ["eq", "before", "after", "between", "is_empty"],
  member: ["in", "not_in", "contains_any", "contains_all", "is_empty"],
  member_multi: ["in", "not_in", "contains_any", "contains_all", "is_empty"],
  checkbox: ["eq"],
  currency: ["gte", "lte", "between"],
  select_ref: ["in", "not_in"],
  multi_select_ref: ["in", "not_in", "contains_any", "contains_all", "is_empty"],
};

/** 内置键操作符收窄（compiler.BUILTIN_OPERATOR_OVERRIDES：priority 仅 in/not_in）。 */
export const OPS_OVERRIDES: Record<string, string[]> = {
  priority: ["in", "not_in"],
};

export const OP_NAMES: Record<string, string> = {
  contains: "包含", not_contains: "不包含", eq: "等于", neq: "不等于",
  is_empty: "为空", is_not_empty: "不为空", gt: "大于", gte: "≥", lt: "小于", lte: "≤",
  between: "介于", in: "属于", not_in: "不属于", contains_any: "包含任一",
  contains_all: "包含全部", before: "早于", after: "晚于",
};

export function opsOfField(def: FieldDef | undefined): string[] {
  if (!def) return ["eq"];
  return OPS_OVERRIDES[def.key] ?? OPS_BY_TYPE[def.type] ?? ["eq"];
}

/** 值无需输入的操作符（TASK-011 §2.3）。 */
export const NO_VALUE_OPS = new Set(["is_empty", "is_not_empty"]);
/** 多值列表操作符（value 恒数组）。 */
export const LIST_OPS = new Set(["in", "not_in", "contains_any", "contains_all"]);

/** 日期快捷占位符（compiler._DATE_TOKENS + next_n_days；显示标签）。 */
export const DATE_TOKEN_LABELS: Record<string, string> = {
  today: "今天", this_week: "本周", this_month: "本月", overdue: "已逾期",
};

/** 类型名占位符（compiler.TYPE_NAME_PLACEHOLDERS：编译期按 IssueType.name 解析 UUID）。 */
export const TYPE_PLACEHOLDER_LABELS: Record<string, string> = {
  __requirement__: "需求", __bug__: "缺陷", __test__: "测试",
};

/** BOARD-003 §2.3 / issue_grouping.PRIORITY_COLUMNS 五档固定列（枚举固定表）。 */
export const PRIORITY_COLUMNS: Array<{ key: string; name: string; color: string }> = [
  { key: "urgent", name: "紧急", color: "#EF4444" },
  { key: "high", name: "高", color: "#F97316" },
  { key: "medium", name: "中", color: "#F59E0B" },
  { key: "low", name: "低", color: "#3B82F6" },
  { key: "none", name: "无", color: "#6B7280" },
];

/** 状态组枚举（State.Group；state.group 字段值域）。 */
export const STATE_GROUPS: Array<{ key: string; name: string }> = [
  { key: "backlog", name: "待规划" },
  { key: "unstarted", name: "待办" },
  { key: "started", name: "进行中" },
  { key: "completed", name: "已完成" },
  { key: "cancelled", name: "已取消" },
];

/** 表格列名（原型 COL_NAMES / O3）。 */
export const TABLE_COL_NAMES: Record<string, string> = {
  key: "编号", title: "标题", state: "状态", assignees: "负责人",
  due: "截止", priority: "优先级", labels: "标签",
};

export const CARD_FIELD_NAMES: Record<string, string> = {
  labels: "标签", sub_issues: "子任务", attachments: "附件数", estimate: "工时",
  priority: "优先级", timer: "计时器", target_date: "截止时间",
};

/** 分组维度候选（BOARD-003 §3.2「按状态」置顶；cf_* 由 Schema groupable 动态追加）。 */
export const BUILTIN_GROUPBYS: Array<{ key: string; name: string }> = [
  { key: "state_id", name: "按状态" },
  { key: "priority", name: "按优先级" },
  { key: "assignee_id", name: "按负责人" },
  { key: "label_id", name: "按标签" },
];

/** Schema 键 → group_by 取值别名归一（BOARD-003 BR-05 / resolve_dimension 同表）。 */
export const GROUP_KEY_ALIAS: Record<string, string> = {
  state: "state_id", assignees: "assignee_id", labels: "label_id",
};

export function groupDisplayName(key: string, cfName?: string): string {
  const hit = BUILTIN_GROUPBYS.find((g) => g.key === key);
  if (hit) return hit.name;
  if (key.startsWith("cf_")) return `按${cfName ?? key.replace(/^cf_/, "")}（自定义）`;
  return key;
}

/** 快捷 chips 五枚（TASK-011 §3.1 快捷 chips 行；原型同款）。
 *  已逾期 = between:["overdue"]——overdue 是日期占位符（≤今天 区间），
 *  不是操作符（§2.3 date 行操作符集无 overdue；原型写法为示意笔误）。 */
export const QUICK_CHIPS: Array<{ label: string; field: string; operator: string; value: unknown[] }> = [
  { label: "@我", field: "assignees", operator: "in", value: ["@me"] },
  { label: "未指派", field: "assignees", operator: "is_empty", value: [] },
  { label: "今天到期", field: "target_date", operator: "eq", value: ["today"] },
  { label: "已逾期", field: "target_date", operator: "between", value: ["overdue"] },
  { label: "本周", field: "target_date", operator: "between", value: ["this_week"] },
];
