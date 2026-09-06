import { useEffect, useMemo, useState } from "react";
import {
  cloneTree,
  countConditions,
  emptyTree,
  isLogicNode,
  treeDepth,
  type FilterCondition,
  type FilterLogicNode,
} from "@rp/shared-state";
import { IssueAPI } from "../../services/api";
import { toast } from "../Toast";
import type { ViewPage } from "./useViewPage";
import {
  BUILTIN_FIELDS,
  DATE_TOKEN_LABELS,
  NO_VALUE_OPS,
  OP_NAMES,
  OPS_BY_TYPE,
  OPS_OVERRIDES,
  PRIORITY_COLUMNS,
  QUICK_CHIPS,
  STATE_GROUPS,
  TYPE_PLACEHOLDER_LABELS,
  opsOfField,
  type FieldDef,
} from "./view-dsl";

/** TASK-011 §3.1 筛选面板（640px 抽屉 · C.71~C.74）。
 *  草稿树为面板本地 React 状态（引用式操作）；[应用] 才写入 URL/FilterTreeStore。
 *  命中数：500ms 防抖 GET issues/?view_id&filters&per_page=1 读 meta.total_count（aria-live）。 */

const MAX_CONDITIONS = 20;
const MAX_DEPTH = 3;

interface RecentEntry { summary: string; dsl: FilterLogicNode; savedAt: number }

function recentKey(projectId: string) {
  return `recent-filters:${projectId}`;
}
function loadRecent(projectId: string): RecentEntry[] {
  try {
    const raw = localStorage.getItem(recentKey(projectId));
    const parsed = raw ? (JSON.parse(raw) as RecentEntry[]) : [];
    return Array.isArray(parsed) ? parsed.slice(0, 5) : [];
  } catch {
    return [];
  }
}
function saveRecent(projectId: string, list: RecentEntry[]) {
  try { localStorage.setItem(recentKey(projectId), JSON.stringify(list.slice(0, 5))); } catch { /* 私有模式 */ }
}

/** 条件摘要（最近使用列表 / chip 文案共用；占位符「活」解析展示，BR-05）。 */
function conditionSummary(c: FilterCondition, ctx: ViewPage): string {
  const def = fieldDefOf(c.field, ctx);
  return `${def?.name ?? c.field} ${OP_NAMES[c.operator] ?? c.operator} ${c.value.map((v) => valueLabel(c.field, v, ctx)).join(" · ")}`;
}

function fieldDefOf(key: string, ctx: ViewPage): FieldDef | undefined {
  const builtin = BUILTIN_FIELDS.find((f) => f.key === key);
  if (builtin) return builtin;
  const cf = ctx.cfDefs.find((d) => d.key === key && d.is_active !== false);
  if (!cf) return undefined;
  return { key: cf.key, name: cf.name, type: cf.type as FieldDef["type"], color: "#64748b", cf: true };
}

/** 值 → 展示标签（@me/占位符/UUID → 名称；true/false → 是/否）。 */
function valueLabel(fieldKey: string, v: unknown, ctx: ViewPage): string {
  if (v === "@me") return `@${ctx.meName}`;
  if (typeof v === "boolean") return v ? "是" : "否";
  const s = String(v);
  if (DATE_TOKEN_LABELS[s]) return DATE_TOKEN_LABELS[s];
  if (TYPE_PLACEHOLDER_LABELS[s]) return TYPE_PLACEHOLDER_LABELS[s];
  if (fieldKey === "state.group") return STATE_GROUPS.find((g) => g.key === s)?.name ?? s;
  if (fieldKey === "priority") return PRIORITY_COLUMNS.find((p) => p.key === s)?.name ?? s;
  if (fieldKey === "state") return ctx.states.find((x) => x.id === s)?.name ?? s;
  if (fieldKey === "assignees" || fieldKey === "created_by") return ctx.members.find((m) => m.id === s)?.name ?? s;
  if (fieldKey === "labels") return ctx.labels.find((l) => l.id === s)?.name ?? s;
  if (fieldKey.startsWith("cf_")) {
    const def = ctx.cfDefs.find((d) => d.key === fieldKey);
    const opt = def?.options.find((o) => o.value === s);
    return opt?.label ?? s;
  }
  return s;
}

/** 字段值域选项（值选择器菜单）。 */
function valueOptions(fieldKey: string, ctx: ViewPage): Array<{ value: string; label: string; color?: string }> {
  const def = fieldDefOf(fieldKey, ctx);
  if (fieldKey === "state") return ctx.states.map((st) => ({ value: st.id, label: st.name, color: st.color as string }));
  if (fieldKey === "state.group") return STATE_GROUPS.map((g) => ({ value: g.key, label: g.name }));
  if (fieldKey === "issue_type") return Object.entries(TYPE_PLACEHOLDER_LABELS).map(([value, label]) => ({ value, label }));
  if (fieldKey === "priority") return PRIORITY_COLUMNS.map((p) => ({ value: p.key, label: p.name, color: p.color }));
  if (fieldKey === "assignees" || fieldKey === "created_by") {
    return [{ value: "@me", label: `@${ctx.meName}` }, ...ctx.members.map((m) => ({ value: m.id, label: m.name }))];
  }
  if (fieldKey === "labels") return ctx.labels.map((l) => ({ value: l.id, label: l.name, color: l.color as string }));
  if (fieldKey.startsWith("cf_")) {
    const def2 = ctx.cfDefs.find((d) => d.key === fieldKey);
    return (def2?.options ?? []).map((o) => ({ value: o.value, label: o.label, color: o.color ?? "#999" }));
  }
  if (def && (def.type === "date")) {
    return Object.entries(DATE_TOKEN_LABELS).map(([value, label]) => ({ value, label }));
  }
  return [];
}

/** 打开面板：以已应用树（或空树）为草稿。 */
export function FilterPanelDrawer({ vp, onClose }: { vp: ViewPage; onClose: () => void }) {
  const [tree, setTree] = useState<FilterLogicNode>(() => (vp.filterStore.applied ? cloneTree(vp.filterStore.applied) : emptyTree()));
  const [, force] = useState(0);
  const bump = () => force((n) => n + 1);
  const [hit, setHit] = useState<number | null>(null);
  const [hitPending, setHitPending] = useState(false);
  const [recentOpen, setRecentOpen] = useState(false);
  /** 当前展开的下拉：{kind, nodeId} —— 引用 id 用 WeakMap 不便，直接持有 DOM 锚定与目标节点引用。 */
  const [menu, setMenu] = useState<null | { kind: "field" | "oper" | "value"; cond: FilterCondition }>(null);
  const projectId = location.pathname.split("/projects/")[1]?.split("/")[0] ?? "";

  const conds = countConditions(tree);
  const depth = treeDepth(tree);

  /** 命中数（500ms 防抖；draft 树序列化进 ?filters）。 */
  useEffect(() => {
    if (conds === 0) { setHit(null); return; }
    setHitPending(true);
    const t = setTimeout(() => {
      void (async () => {
        try {
          const slug = location.pathname.split("/")[1] ?? "";
          const r = await IssueAPI.list(slug, projectId, {
            ...(vp.viewIdParam ? { view_id: vp.viewIdParam } : {}),
            filters: JSON.stringify(tree),
            per_page: 1,
          });
          const meta = (r as unknown as { meta?: { total_count?: number } }).meta;
          setHit(meta?.total_count ?? null);
        } catch {
          setHit(null);
        } finally {
          setHitPending(false);
        }
      })();
    }, 500);
    return () => clearTimeout(t);
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(tree), vp.viewIdParam]);

  useEffect(() => {
    if (!menu && !recentOpen) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (menu && !t?.closest('[data-sb-scope="fpanel-menu"]') && !t?.closest('[data-sb-scope="fpanel-ctl"]')) setMenu(null);
      if (recentOpen && !t?.closest('[data-sb-scope="fpanel-recent"]')) setRecentOpen(false);
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [menu, recentOpen]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { setMenu(null); onClose(); } };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  // ── 树操作（引用式；根组不可删）──
  function addCondition(g: FilterLogicNode) {
    if (conds >= MAX_CONDITIONS) { toast(`条件数已达 ${MAX_CONDITIONS} 上限`, "warning"); return; }
    g.conditions.push({ field: "state.group", operator: "in", value: ["unstarted"] });
    bump();
  }
  function addGroup(g: FilterLogicNode) {
    if (depth >= MAX_DEPTH) { toast("层级已达 3 层上限（400 · api-conventions §5.3）", "warning"); return; }
    if (conds >= MAX_CONDITIONS) { toast(`条件数已达 ${MAX_CONDITIONS} 上限`, "warning"); return; }
    g.conditions.push({ op: "OR", conditions: [] } as FilterLogicNode);
    bump();
  }
  function removeNode(parentTree: FilterLogicNode, node: FilterCondition | FilterLogicNode) {
    parentTree.conditions = parentTree.conditions.filter((c) => c !== node);
    bump();
  }

  function apply() {
    vp.filterStore.setTree(cloneTree(tree));
    vp.applyFilterTree();
    // 最近使用（BR-16：localStorage 5 条滚动）
    if (conds > 0) {
      const list = loadRecent(projectId);
      const flat: string[] = [];
      const walk = (n: FilterLogicNode) => n.conditions.forEach((c) => (isLogicNode(c) ? walk(c) : flat.push(conditionSummary(c, vp))));
      walk(tree);
      const summary = flat.length > 2 ? `${flat.slice(0, 2).join(" · ")} 等 ${flat.length} 条` : flat.join(" · ");
      saveRecent(projectId, [{ summary, dsl: cloneTree(tree), savedAt: Date.now() }, ...list.filter((x) => x.summary !== summary)]);
    }
    onClose();
    toast("已应用 · 临时 filters 入 URL（?filters=…）· 命中数以 meta.total_count 为准", "ok");
  }

  const recents = useMemo(() => loadRecent(projectId), [projectId]);

  /** 条件行渲染（字段 → 操作符 → 值 → 删除）。 */
  function renderCondition(c: FilterCondition, parent: FilterLogicNode) {
    const def = fieldDefOf(c.field, vp);
    const ops = opsOfField(def);
    const menuOpenForThis = menu && menu.cond === c ? menu : null;
    return (
      <div className="flex items-center gap-2 py-1 flex-wrap" data-cond={c.field} data-sb-scope="frow">
        {/* 字段选择器（内置+自定义混排 · 类型色点） */}
        <div className="relative" data-sb-scope="fpanel-ctl">
          <button type="button" aria-label="选择字段" data-sb-scope="frow-field"
            onClick={() => setMenu(menuOpenForThis?.kind === "field" ? null : { kind: "field", cond: c })}
            className="h-8 border border-neutral-300 rounded-lg bg-white text-[13px] text-neutral-700 inline-flex items-center gap-1.5 px-2.5 min-w-[130px] hover:border-brand-500">
            <span className="w-2 h-2 rounded-sm shrink-0" style={{ background: def?.color ?? "#999" }} />
            {def?.name ?? c.field}{def?.cf ? <span className="text-[11px] text-neutral-400">自定义</span> : null} ▾
          </button>
          {menuOpenForThis?.kind === "field" && (
            <div role="menu" aria-label="字段" data-sb-scope="fpanel-menu"
              className="absolute left-0 top-9 z-40 min-w-[200px] max-h-[300px] overflow-y-auto bg-white border border-neutral-200 rounded-lg shadow-lg py-1">
              {BUILTIN_FIELDS.map((f) => (
                <button key={f.key} type="button" role="menuitem" data-field={f.key}
                  onClick={() => {
                    c.field = f.key;
                    c.operator = (OPS_OVERRIDES[f.key] ?? OPS_BY_TYPE[f.type] ?? ["eq"])[0] ?? "eq";
                    c.value = [];
                    setMenu(null); bump();
                  }}
                  className={`w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50 flex items-center gap-2 ${f.key === c.field ? "text-brand-600 font-medium" : "text-neutral-700"}`}>
                  <span className="w-2 h-2 rounded-sm" style={{ background: f.color }} />{f.name}
                </button>
              ))}
              <div className="h-px bg-neutral-100 my-1" />
              {vp.cfDefs.filter((d) => d.is_active !== false).map((d) => (
                <button key={d.key} type="button" role="menuitem" data-field={d.key}
                  onClick={() => {
                    c.field = d.key;
                    c.operator = (OPS_BY_TYPE[d.type] ?? ["eq"])[0] ?? "eq";
                    c.value = [];
                    setMenu(null); bump();
                  }}
                  className={`w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50 flex items-center gap-2 ${d.key === c.field ? "text-brand-600 font-medium" : "text-neutral-700"}`}>
                  <span className="w-2 h-2 rounded-sm bg-slate-500" />{d.name}<span className="text-[11px] text-neutral-400">自定义</span>
                </button>
              ))}
            </div>
          )}
        </div>
        {/* 操作符下拉（按类型出 §2.3 集合） */}
        <div className="relative" data-sb-scope="fpanel-ctl">
          <button type="button" aria-label="选择操作符" data-sb-scope="frow-oper"
            onClick={() => setMenu(menuOpenForThis?.kind === "oper" ? null : { kind: "oper", cond: c })}
            className="h-8 border border-neutral-300 rounded-lg bg-white text-[13px] text-neutral-700 inline-flex items-center gap-1.5 px-2.5 hover:border-brand-500">
            <span className="text-[12px] text-neutral-400">{OP_NAMES[c.operator] ?? c.operator}</span> ▾
          </button>
          {menuOpenForThis?.kind === "oper" && (
            <div role="menu" aria-label="操作符" data-sb-scope="fpanel-menu"
              className="absolute left-0 top-9 z-40 min-w-[140px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1">
              {ops.map((o) => (
                <button key={o} type="button" role="menuitem"
                  onClick={() => { c.operator = o; c.value = NO_VALUE_OPS.has(o) ? [] : []; setMenu(null); bump(); }}
                  className={`w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50 ${o === c.operator ? "text-brand-600 font-medium" : "text-neutral-700"}`}>
                  {OP_NAMES[o] ?? o}
                </button>
              ))}
            </div>
          )}
        </div>
        {/* 值控件（类型匹配） */}
        {renderValue(c, def)}
        <button type="button" aria-label="删除条件" data-sb-scope="frow-del"
          onClick={() => removeNode(parent, c)}
          className="w-6 h-6 rounded-md inline-flex items-center justify-center text-neutral-400 hover:text-red-500 hover:bg-red-50 shrink-0">✕</button>
      </div>
    );
  }

  function renderValue(c: FilterCondition, def: FieldDef | undefined) {
    if (NO_VALUE_OPS.has(c.operator)) {
      return <span className="text-[12px] text-neutral-400" data-sb-scope="frow-novalue">（无需值）</span>;
    }
    const type = def?.type;
    // 是非三态（checkbox：序列化 true/false——compiler._check_condition_values 布尔口径）
    if (type === "checkbox") {
      return (
        <span className="inline-flex items-center gap-1.5 flex-wrap" data-sb-scope="frow-value">
          {[true, false].map((b) => (
            <button key={String(b)} type="button"
              onClick={() => { c.value = [b]; bump(); }}
              className={`border rounded-full px-3 py-0.5 text-[12px] ${c.value[0] === b ? "bg-brand-500 border-brand-500 text-white" : "border-neutral-300 text-neutral-600 hover:border-brand-500"}`}>
              {b ? "是" : "否"}
            </button>
          ))}
        </span>
      );
    }
    if (type === "date") {
      const between = c.operator === "between";
      return (
        <span className="inline-flex items-center gap-1.5 flex-wrap" data-sb-scope="frow-value">
          {(between ? [0, 1] : [0]).map((i) => (
            <input key={i} type="date" aria-label={between ? (i === 0 ? "起始日期" : "结束日期") : "日期"}
              value={typeof c.value[i] === "string" && !DATE_TOKEN_LABELS[String(c.value[i])] ? String(c.value[i]) : ""}
              onChange={(e) => { const v = [...c.value]; v[i] = e.target.value; c.value = v; bump(); }}
              className="h-8 border border-neutral-300 rounded-lg px-2.5 text-[12.5px] text-neutral-700 bg-white" />
          ))}
          {/* 快捷占位符 chips（今天/本周/本月/已逾期） */}
          {Object.entries(DATE_TOKEN_LABELS).map(([token, label]) => (
            <button key={token} type="button" title={`快捷占位符：${label}`}
              onClick={() => { c.value = [token]; bump(); }}
              className={`border rounded-full px-2.5 py-0.5 text-[11.5px] ${c.value[0] === token ? "bg-brand-500 border-brand-500 text-white" : "border-neutral-300 text-neutral-500 hover:border-brand-500"}`}>{label}</button>
          ))}
        </span>
      );
    }
    if (type && ["select", "multi_select", "member", "member_multi", "select_ref", "multi_select_ref"].includes(type)) {
      const opts = valueOptions(c.field, vp);
      return (
        <span className="inline-flex items-center gap-1.5 flex-wrap" data-sb-scope="frow-value">
          {c.value.map((v) => (
            <span key={String(v)} className="inline-flex items-center gap-1 bg-neutral-100 rounded-full px-2.5 py-0.5 text-[12px] text-neutral-700">
              {valueLabel(c.field, v, vp)}
              <button type="button" aria-label={`移除值 ${valueLabel(c.field, v, vp)}`}
                onClick={() => { c.value = c.value.filter((x) => x !== v); bump(); }}
                className="text-neutral-400 hover:text-red-500 inline-flex w-4 h-4 items-center justify-center rounded-full">✕</button>
            </span>
          ))}
          <span className="relative" data-sb-scope="fpanel-ctl">
            <button type="button" aria-label="添加值" data-sb-scope="frow-value-add"
              onClick={() => setMenu(menu && menu.cond === c && menu.kind === "value" ? null : { kind: "value", cond: c })}
              className="h-8 border border-neutral-300 rounded-lg bg-white text-[13px] text-neutral-600 inline-flex items-center px-2.5 hover:border-brand-500">+ 值 ▾</button>
            {menu && menu.cond === c && menu.kind === "value" && (
              <div role="menu" aria-label="值" data-sb-scope="fpanel-menu"
                className="absolute left-0 top-9 z-40 min-w-[180px] max-h-[260px] overflow-y-auto bg-white border border-neutral-200 rounded-lg shadow-lg py-1">
                {opts.length === 0 && <div className="px-3 py-2 text-[12.5px] text-neutral-400">无可选值</div>}
                {opts.map((o) => {
                  const on = c.value.includes(o.value);
                  return (
                    <button key={o.value} type="button" role="menuitem" data-value={o.value}
                      onClick={() => {
                        c.value = on ? c.value.filter((x) => x !== o.value) : [...c.value, o.value];
                        bump();
                      }}
                      className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50 flex items-center gap-2 text-neutral-700">
                      <span className="w-3.5">{on ? "✓" : ""}</span>
                      {o.color ? <span className="w-2 h-2 rounded-sm" style={{ background: o.color }} /> : null}
                      {o.label}
                    </button>
                  );
                })}
              </div>
            )}
          </span>
        </span>
      );
    }
    if (type === "number" || type === "currency") {
      const between = c.operator === "between";
      return (
        <span className="inline-flex items-center gap-1.5" data-sb-scope="frow-value">
          {(between ? [0, 1] : [0]).map((i) => (
            <input key={i} type="number" aria-label={between ? (i === 0 ? "最小值" : "最大值") : "数值"}
              value={c.value[i] === undefined || c.value[i] === null ? "" : String(c.value[i])}
              onChange={(e) => { const v = [...c.value]; v[i] = e.target.value === "" ? 0 : Number(e.target.value); c.value = v; bump(); }}
              className="h-8 w-[92px] border border-neutral-300 rounded-lg px-2.5 text-[13px] text-neutral-700 bg-white" />
          ))}
        </span>
      );
    }
    // text / textarea / url
    return (
      <input aria-label="文本值" data-sb-scope="frow-value"
        defaultValue={typeof c.value[0] === "string" ? String(c.value[0]) : ""}
        onChange={(e) => { c.value = [e.target.value]; }}
        onBlur={bump}
        className="h-8 w-[170px] border border-neutral-300 rounded-lg px-2.5 text-[13px] text-neutral-700 bg-white focus:outline-none focus:border-brand-500" />
    );
  }

  /** 组节点递归渲染（虚线框 + AND/OR 切换 + [+条件][+组] + 删除；根组不可删）。 */
  function renderGroup(g: FilterLogicNode, parent: FilterLogicNode | null, level: number) {
    const canAddC = conds < MAX_CONDITIONS;
    const canAddG = depth < MAX_DEPTH && conds < MAX_CONDITIONS;
    const opLabel = `满足 ${g.op === "AND" ? "全部" : "任一"}（${g.op}）`;
    return (
      <div className={`border-[1.5px] border-dashed border-neutral-300 rounded-xl px-3 pb-3 mb-2.5 ${level > 1 ? "bg-neutral-50/60" : ""}`}
        role="group" aria-label={`条件组：${opLabel}，共 ${g.conditions.length} 项`} data-sb-scope="fgroup" data-op={g.op}>
        <div className="flex items-center gap-2 py-2">
          <div className="relative" data-sb-scope="fpanel-ctl">
            <button type="button" aria-label="切换 AND/OR" data-sb-scope="fgroup-op"
              onClick={() => setMenu(menu && menu.cond === (g as unknown as FilterCondition) ? null : { kind: "oper", cond: g as unknown as FilterCondition })}
              className="h-[26px] border border-neutral-300 rounded-full bg-white text-[12px] text-neutral-700 inline-flex items-center gap-1 px-2.5 hover:border-brand-500">
              {opLabel} ▾
            </button>
            {menu && menu.cond === (g as unknown as FilterCondition) && (
              <div role="menu" aria-label="逻辑运算" data-sb-scope="fpanel-menu"
                className="absolute left-0 top-8 z-40 min-w-[160px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1">
                {(["AND", "OR"] as const).map((o) => (
                  <button key={o} type="button" role="menuitem"
                    onClick={() => { g.op = o; setMenu(null); bump(); }}
                    className={`w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50 ${g.op === o ? "text-brand-600 font-medium" : "text-neutral-700"}`}>
                    满足 {o === "AND" ? "全部" : "任一"}（{o}）
                  </button>
                ))}
              </div>
            )}
          </div>
          <button type="button" data-sb-scope="fgroup-add-cond" disabled={!canAddC}
            title={canAddC ? undefined : `条件数已达 ${MAX_CONDITIONS} 上限`}
            onClick={() => addCondition(g)}
            className={`h-[26px] rounded-md text-[12px] inline-flex items-center gap-1 px-2 ${canAddC ? "text-neutral-400 hover:bg-neutral-100 hover:text-brand-600" : "text-neutral-300 cursor-not-allowed"}`}>+ 条件</button>
          <button type="button" data-sb-scope="fgroup-add-group" disabled={!canAddG}
            title={canAddG ? undefined : "层级已达 3 层上限"}
            onClick={() => addGroup(g)}
            className={`h-[26px] rounded-md text-[12px] inline-flex items-center gap-1 px-2 ${canAddG ? "text-neutral-400 hover:bg-neutral-100 hover:text-brand-600" : "text-neutral-300 cursor-not-allowed"}`}>+ 组</button>
          {parent && (
            <button type="button" aria-label="删除组" data-sb-scope="fgroup-del"
              onClick={() => removeNode(parent, g)}
              className="ml-auto w-6 h-6 rounded-md inline-flex items-center justify-center text-neutral-400 hover:text-red-500 hover:bg-red-50">✕</button>
          )}
        </div>
        {g.conditions.map((child, i) =>
          isLogicNode(child)
            ? <div key={i}>{renderGroup(child, g, level + 1)}</div>
            : <div key={i}>{renderCondition(child, g)}</div>,
        )}
      </div>
    );
  }

  const viewChips = (() => {
    const v = vp.currentView;
    if (!v) return null;
    const vt = v.filters as FilterLogicNode | undefined;
    const chips: string[] = [];
    const walk = (n: FilterLogicNode | undefined) => n?.conditions?.forEach((c) => (isLogicNode(c) ? walk(c) : chips.push(conditionSummary(c, vp))));
    walk(vt);
    return { view: v, chips };
  })();

  return (
    <div className="fixed inset-0 z-[65] flex justify-end">
      <div className="absolute inset-0 bg-black/28" onClick={onClose} aria-hidden="true" />
      <aside className="relative w-[640px] max-w-full bg-white border-l border-neutral-200 shadow-lg flex flex-col"
        role="dialog" aria-modal="true" aria-label="筛选面板" data-sb-scope="filter-panel">
        {/* 头部：筛选 + [最近▾][清空全部][保存视图] */}
        <div className="flex items-center gap-2 px-5 pt-4 pb-2.5 shrink-0">
          <span className="text-[15px] font-semibold">⊞ 筛选</span>
          <div className="ml-auto flex gap-2">
            <span className="relative" data-sb-scope="fpanel-recent">
              <button type="button" data-sb-scope="fpanel-recent-btn" onClick={() => setRecentOpen((v) => !v)} className="text-[13px] text-brand-600 hover:underline">最近 ▾</button>
              {recentOpen && (
                <div role="menu" aria-label="最近使用筛选"
                  className="absolute right-0 top-7 z-40 min-w-[260px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1 max-h-[300px] overflow-y-auto">
                  {recents.length === 0 && <div className="px-3 py-2 text-[12.5px] text-neutral-400">暂无最近筛选</div>}
                  {recents.map((r, i) => (
                    <button key={r.savedAt} type="button" role="menuitem" data-sb-scope="fpanel-recent-item" data-idx={i}
                      onClick={() => { setTree(cloneTree(r.dsl)); setRecentOpen(false); toast("已载入最近筛选（localStorage recent-filters:{projectId}）", "info"); }}
                      className="w-full text-left px-3 min-h-8 py-1.5 text-[13px] text-neutral-700 hover:bg-neutral-50 truncate">{r.summary}</button>
                  ))}
                </div>
              )}
            </span>
            <button type="button" data-sb-scope="fpanel-clear-all"
              onClick={() => { setTree(emptyTree()); setHit(null); }}
              className="text-[13px] text-brand-600 hover:underline">清空全部</button>
            <button type="button" data-sb-scope="fpanel-save-view"
              onClick={() => { onClose(); window.dispatchEvent(new CustomEvent("rp:open-save-view")); }}
              className="h-7 px-2.5 bg-brand-500 text-white rounded-md text-[12.5px] hover:bg-brand-600">保存视图</button>
          </div>
        </div>
        {/* 视图段（选中视图时：只读 chips + 叠加区 + [另存][脱离]） */}
        {viewChips && (
          <div className="flex items-center gap-2 bg-neutral-100 rounded-[10px] px-3 py-2 mx-5 mb-2.5 text-[12.5px] text-neutral-600 flex-wrap shrink-0" data-sb-scope="fpanel-viewseg">
            视图：{viewChips.view.display_props?.icon ? <span>{viewChips.view.display_props.icon}</span> : null} {viewChips.view.name}（{viewChips.view.is_system ? "内置 · 只读" : "个人"}）
            <span className="inline-flex gap-1 flex-wrap">
              {viewChips.chips.map((s, i) => (
                <span key={i} className="bg-brand-50 text-brand-600 rounded-full px-2.5 py-0.5 text-[12px]" title={`视图条件（${viewChips.view.is_system ? "内置·只读" : "个人视图"}）`}>{s}</span>
              ))}
            </span>
            <span className="ml-auto flex gap-1.5">
              <SaveViaEvent />
              <button type="button" data-sb-scope="fpanel-detach"
                onClick={() => { vp.switchView(null); toast("已脱离视图，条件转为纯临时层", "info"); }}
                className="h-7 px-2.5 border border-neutral-300 bg-white rounded-md text-[12.5px] text-neutral-700 hover:bg-neutral-50">脱离</button>
            </span>
          </div>
        )}
        {/* 快捷 chips 五枚 */}
        <div className="flex gap-1.5 flex-wrap px-5 pb-3 shrink-0" aria-label="快捷条件" data-sb-scope="fpanel-quickchips">
          {QUICK_CHIPS.map((q) => (
            <button key={q.label} type="button" data-sb-scope="fpanel-quickchip"
              onClick={() => {
                if (conds >= MAX_CONDITIONS) { toast(`条件数已达 ${MAX_CONDITIONS} 上限`, "warning"); return; }
                tree.conditions.push({ field: q.field, operator: q.operator, value: [...q.value] });
                bump();
              }}
              className="border border-neutral-300 rounded-full px-3 py-0.5 text-[12px] text-neutral-600 hover:border-brand-500 hover:text-brand-600">{q.label}</button>
          ))}
        </div>
        {/* 树体 */}
        <div className="flex-1 overflow-y-auto px-5 pb-5">
          {renderGroup(tree, null, 1)}
          {conds === 0 && (
            <div className="text-[13px] text-neutral-400 py-6 text-center" data-sb-scope="fpanel-empty">暂无筛选条件——用快捷 chips 或 [+ 条件] 开始</div>
          )}
        </div>
        {/* 底部：命中数（aria-live）+ 配额 + [取消][应用] */}
        <div className="flex items-center gap-2.5 px-5 py-3 border-t border-neutral-200 bg-white shrink-0" data-sb-scope="fpanel-foot">
          <span className="text-[13px] text-neutral-700 inline-flex items-center gap-1.5" aria-live="polite" data-sb-scope="fpanel-hitcount">
            ⓘ 命中 <b className="text-brand-600 tabular-nums">{hitPending ? "…" : (hit ?? "—")}</b> 个任务
          </span>
          <span className={`text-[12px] ${conds >= MAX_CONDITIONS || depth >= MAX_DEPTH ? "text-red-500 font-semibold" : "text-neutral-400"}`} data-sb-scope="fpanel-quota">
            条件 {conds}/{MAX_CONDITIONS} · 层级 {depth}/{MAX_DEPTH}
          </span>
          <span className="ml-auto flex gap-2">
            <button type="button" onClick={onClose} className="h-[34px] px-3.5 border border-neutral-300 rounded-md text-neutral-700 hover:bg-neutral-50">取消</button>
            <button type="button" data-sb-scope="fpanel-apply" onClick={apply} className="h-[34px] px-3.5 bg-brand-500 text-white rounded-md hover:bg-brand-600">应用</button>
          </span>
        </div>
      </aside>
    </div>
  );
}

/** 视图段 [另存]：把「视图 filters AND 临时树」合并树存新视图（TASK-011 §4.4.1）——
 *  弹层由 ViewSwitchBar 监听 rp:open-save-view 事件打开。 */
function SaveViaEvent() {
  return (
    <button type="button" data-sb-scope="fpanel-merge-save"
      onClick={() => { window.dispatchEvent(new CustomEvent("rp:open-save-view")); }}
      className="h-7 px-2.5 border border-neutral-300 bg-white rounded-md text-[12.5px] text-neutral-700 hover:bg-neutral-50">另存</button>
  );
}
