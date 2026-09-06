import { useEffect, useState } from "react";
import { CARD_FIELD_KEYS, DEFAULT_CARD_FIELDS } from "@rp/shared-state";
import type { ViewPage } from "./useViewPage";
import { CARD_FIELD_NAMES, TABLE_COL_NAMES, groupDisplayName } from "./view-dsl";

/** BOARD-003 §3.3 显示配置面板（320px Drawer · C.68）。
 *  改动即预览（乐观应用——patchDisplay 直接改 effDisplay）；[保存到视图] 才落库。 */

const ORDER_OPTIONS: Array<{ key: string; label: string }> = [
  { key: "sort_order", label: "拖拽顺序" },
  { key: "-priority", label: "按优先级" },
  { key: "-created_at", label: "创建时间（新→旧）" },
  { key: "target_date", label: "截止时间" },
];

function Switch({ on, label, onClick }: { on: boolean; label: string; onClick: () => void }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} onClick={onClick}
      className={`ml-auto w-[34px] h-[19px] rounded-full relative transition-colors shrink-0 ${on ? "bg-brand-500" : "bg-neutral-300"}`}>
      <span className={`absolute top-0.5 left-0.5 w-[15px] h-[15px] rounded-full bg-white shadow-sm transition-transform ${on ? "translate-x-[15px]" : ""}`} />
    </button>
  );
}

export function DisplayDrawer({ vp, onClose }: { vp: ViewPage; onClose: () => void }) {
  const { effDisplay, groupCandidates } = vp;
  const [groupOpen, setGroupOpen] = useState(false);
  const [orderOpen, setOrderOpen] = useState(false);

  useEffect(() => {
    if (!groupOpen && !orderOpen) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (groupOpen && !t?.closest('[data-sb-scope="disp-group-dd"]')) setGroupOpen(false);
      if (orderOpen && !t?.closest('[data-sb-scope="disp-order-dd"]')) setOrderOpen(false);
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [groupOpen, orderOpen]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  /** 卡片开关固定 7 项 + 生效 cf_*（BR-09 键域同源）。 */
  const cfCardKeys = vp.cfDefs.filter((d) => d.is_active !== false).map((d) => ({ key: d.key, name: `自定义：${d.name}` }));
  const cardFields = { ...DEFAULT_CARD_FIELDS, ...(effDisplay.card_fields ?? {}) };
  /** 列配置（list/table 布局才有该区；「-」前缀 = 隐藏，原型 colcfg-chip）。 */
  const columns = effDisplay.columns ?? Object.keys(TABLE_COL_NAMES);

  const orderBy = effDisplay.order_by ?? "sort_order";
  const showEmpty = effDisplay.show_empty_groups !== false;

  return (
    <div className="fixed inset-0 z-[60] flex justify-end">
      <div className="absolute inset-0 bg-black/28" onClick={onClose} aria-hidden="true" />
      <aside className="relative w-[320px] max-w-full bg-white border-l border-neutral-200 shadow-lg flex flex-col"
        role="dialog" aria-modal="true" aria-label="显示配置" data-sb-scope="display-drawer">
        <div className="flex items-center gap-2.5 px-5 py-4 border-b border-neutral-200">
          <span className="text-base font-semibold flex-1">显示配置</span>
          <button type="button" aria-label="关闭" onClick={onClose} className="w-7 h-7 flex items-center justify-center text-neutral-500 hover:text-neutral-900">✕</button>
        </div>
        <div className="flex-1 overflow-y-auto px-5">
          {/* 分组 */}
          <section className="border-b border-neutral-200 py-3" data-sb-scope="disp-sec-group">
            <div className="text-[12px] font-semibold text-neutral-400 uppercase tracking-wider mb-2">分组</div>
            <span className="relative inline-block" data-sb-scope="disp-group-dd">
              <button type="button" aria-haspopup="menu" data-sb-scope="disp-group-btn"
                onClick={() => setGroupOpen((v) => !v)}
                className="h-8 px-2.5 border border-neutral-300 rounded-md text-[13px] text-neutral-700 hover:bg-neutral-50">
                {groupDisplayName(vp.groupBy, vp.cfDefs.find((d) => d.key === vp.groupBy)?.name)} ▾
              </button>
              {groupOpen && (
                <div role="menu" aria-label="分组维度" className="absolute left-0 top-9 z-30 min-w-[200px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1 max-h-[300px] overflow-y-auto">
                  {groupCandidates.map((g) => (
                    <button key={g.key} type="button" role="menuitem" onClick={() => { setGroupOpen(false); vp.patchDisplay({ group_by: g.key }); }}
                      className={`w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50 flex items-center gap-2 ${g.key === vp.groupBy ? "text-brand-600 font-medium" : "text-neutral-700"}`}>
                      {g.key === vp.groupBy ? <span className="w-3.5">✓</span> : <span className="w-3.5" />}
                      {g.name}
                    </button>
                  ))}
                </div>
              )}
            </span>
          </section>
          {/* 排序 */}
          <section className="border-b border-neutral-200 py-3" data-sb-scope="disp-sec-order">
            <div className="text-[12px] font-semibold text-neutral-400 uppercase tracking-wider mb-2">排序</div>
            <span className="relative inline-block" data-sb-scope="disp-order-dd">
              <button type="button" aria-haspopup="menu" data-sb-scope="disp-order-btn"
                onClick={() => setOrderOpen((v) => !v)}
                className="h-8 px-2.5 border border-neutral-300 rounded-md text-[13px] text-neutral-700 hover:bg-neutral-50">
                {ORDER_OPTIONS.find((o) => o.key === orderBy)?.label ?? orderBy} ▾
              </button>
              {orderOpen && (
                <div role="menu" aria-label="排序方式" className="absolute left-0 top-9 z-30 min-w-[200px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1">
                  {ORDER_OPTIONS.map((o) => (
                    <button key={o.key} type="button" role="menuitem" onClick={() => { setOrderOpen(false); vp.patchDisplay({ order_by: o.key }); }}
                      className={`w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50 flex items-center gap-2 ${o.key === orderBy ? "text-brand-600 font-medium" : "text-neutral-700"}`}>
                      {o.key === orderBy ? <span className="w-3.5">✓</span> : <span className="w-3.5" />}
                      {o.label}
                    </button>
                  ))}
                </div>
              )}
            </span>
            <div className="mt-1.5 text-[12px] text-neutral-400">仅优先级维度与拖拽序解耦（组内语义权重）</div>
          </section>
          {/* 卡片显示（固定 7 + cf_*） */}
          <section className="border-b border-neutral-200 py-3" data-sb-scope="disp-sec-card">
            <div className="text-[12px] font-semibold text-neutral-400 uppercase tracking-wider mb-2">卡片显示</div>
            <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
              {CARD_FIELD_KEYS.map((k) => (
                <div key={k} className="flex items-center gap-2 min-h-8 text-[13px] text-neutral-600">
                  <span>{CARD_FIELD_NAMES[k] ?? k}</span>
                  <Switch label={CARD_FIELD_NAMES[k] ?? k} on={Boolean(cardFields[k])}
                    onClick={() => vp.patchDisplay({ card_fields: { ...cardFields, [k]: !cardFields[k] } })} />
                </div>
              ))}
              {cfCardKeys.map((c) => (
                <div key={c.key} className="flex items-center gap-2 min-h-8 text-[13px] text-neutral-600">
                  <span>{c.name}</span>
                  <Switch label={c.name} on={Boolean(cardFields[c.key])}
                    onClick={() => vp.patchDisplay({ card_fields: { ...cardFields, [c.key]: !cardFields[c.key] } })} />
                </div>
              ))}
            </div>
          </section>
          {/* 空组显隐 */}
          <section className="border-b border-neutral-200 py-3" data-sb-scope="disp-sec-empty">
            <div className="flex items-center gap-2 min-h-8 text-[13px] text-neutral-600">
              <span>显示空分组</span>
              <Switch label="显示空分组" on={showEmpty} onClick={() => vp.patchDisplay({ show_empty_groups: !showEmpty })} />
            </div>
            <div className="text-[12px] text-neutral-400">债务可见原则：空组恒在（默认开）</div>
          </section>
          {/* 列配置（仅 list/table） */}
          {(vp.layout === "list" || vp.layout === "table") && (
            <section className="py-3" data-sb-scope="disp-sec-columns">
              <div className="text-[12px] font-semibold text-neutral-400 uppercase tracking-wider mb-2">列（列表/表格布局）</div>
              <div className="flex gap-1.5 flex-wrap">
                {columns.map((c) => {
                  const hidden = c.startsWith("-");
                  const bare = c.replace(/^-/, "");
                  return (
                    <button key={c} type="button" data-sb-scope="disp-col-chip" data-col={bare} aria-pressed={!hidden}
                      onClick={() => {
                        const next = columns.map((x) => (x === c ? (hidden ? bare : `-${bare}`) : x));
                        vp.patchDisplay({ columns: next });
                      }}
                      className={`inline-flex items-center gap-1.5 border border-neutral-300 rounded-md px-2 py-0.5 text-[12px] text-neutral-600 bg-white hover:border-brand-500 ${hidden ? "opacity-45 line-through" : ""}`}>
                      {TABLE_COL_NAMES[bare] ?? bare}
                    </button>
                  );
                })}
              </div>
              <div className="mt-1.5 text-[12px] text-neutral-400">拖拽排序 · 点击显隐</div>
            </section>
          )}
        </div>
        <div className="px-4 py-3 border-t border-neutral-200 flex gap-2 justify-end">
          <button type="button" data-sb-scope="disp-reset"
            onClick={() => vp.patchDisplay({ card_fields: { ...DEFAULT_CARD_FIELDS }, show_empty_groups: true, columns: Object.keys(TABLE_COL_NAMES), group_by: "state_id", order_by: "sort_order" })}
            className="h-7 px-2.5 border border-neutral-300 bg-white rounded-md text-[12.5px] text-neutral-700 hover:bg-neutral-50">重置</button>
          <button type="button" data-sb-scope="disp-save"
            onClick={() => { onClose(); void vp.saveInPlace(); }}
            disabled={!vp.currentView}
            title={vp.currentView ? undefined : "「全部」裸态无视图存档——请先另存为视图"}
            className="h-7 px-2.5 bg-brand-500 text-white rounded-md text-[12.5px] hover:bg-brand-600 disabled:opacity-50 disabled:cursor-not-allowed">保存到视图</button>
        </div>
      </aside>
    </div>
  );
}
