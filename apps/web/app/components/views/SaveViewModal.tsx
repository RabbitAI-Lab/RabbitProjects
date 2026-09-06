import { useEffect, useState } from "react";
import { VIEW_ICON_POOL, type ViewLayout } from "@rp/shared-state";
import type { ViewPage } from "./useViewPage";

/** BOARD-003 §3.4 保存/另存为弹层（C.69）：名称（≤128 重名警告不阻断）/ 8 枚 icon /
 *  布局四选（gantt 禁用）/ 提示条 / [取消][创建视图]；创建后切换选中（?view_id=）。 */

const LAYOUTS: Array<{ key: ViewLayout; label: string }> = [
  { key: "list", label: "列表" },
  { key: "kanban", label: "看板" },
  { key: "table", label: "表格" },
  { key: "gantt", label: "甘特" },
];

export function SaveViewModal({ vp, onClose }: { vp: ViewPage; onClose: () => void }) {
  const defName = vp.currentView ? `${vp.currentView.name} (副本)` : "未命名视图";
  const [name, setName] = useState(defName);
  const [icon, setIcon] = useState<string>(vp.currentView?.display_props?.icon ?? "✨");
  const [layout, setLayout] = useState<ViewLayout>(vp.layout);
  const [creating, setCreating] = useState(false);
  const dupWarn = vp.views.some((v) => v.name === name.trim());

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function submit() {
    if (!name.trim() || creating) return;
    setCreating(true);
    const created = await vp.createView({ name: name.trim(), icon, layout });
    setCreating(false);
    if (created) onClose();
  }

  return (
    <div className="fixed inset-0 bg-black/32 flex items-center justify-center p-4 z-[90]" data-sb-scope="save-view-modal">
      <div className="bg-white rounded-xl shadow-lg w-[440px] max-w-full p-6" role="dialog" aria-modal="true" aria-label="保存视图">
        <div className="flex items-center justify-between mb-4">
          <span className="text-base font-semibold">保存视图</span>
          <button type="button" aria-label="关闭" onClick={onClose} className="w-7 h-7 flex items-center justify-center text-neutral-500 hover:text-neutral-900">✕</button>
        </div>
        <label className="block text-[13px] font-medium text-neutral-700 mb-1.5">名称（≤128 · 项目内重名允许，靠 id 区分）</label>
        <input autoFocus value={name} maxLength={128} onChange={(e) => setName(e.target.value)}
          aria-label="视图名称" data-sb-scope="save-view-name"
          onKeyDown={(e) => { if (e.key === "Enter") void submit(); }}
          className={`w-full h-9 border rounded-md px-2.5 text-[13px] focus:outline-none focus:ring-[3px] focus:ring-brand-50 ${dupWarn ? "border-amber-400" : "border-neutral-300 focus:border-brand-500"}`} />
        {dupWarn && (
          <div data-sb-scope="save-view-dup-warn"
            className="flex items-center gap-1.5 text-[12px] text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-2.5 py-1 mt-1.5">
            ⓘ 已存在同名视图——允许创建（靠 id 区分 · BR-09）
          </div>
        )}
        <div className="mt-3.5">
          <label className="block text-[13px] font-medium text-neutral-700 mb-1.5">图标（8 枚预设）</label>
          <div className="flex gap-1.5 flex-wrap" role="radiogroup" aria-label="视图图标" data-sb-scope="save-view-icons">
            {VIEW_ICON_POOL.map((e) => (
              <button key={e} type="button" role="radio" aria-checked={icon === e} aria-label={e}
                onClick={() => setIcon(e)}
                className={`w-9 h-9 border rounded-lg text-[17px] inline-flex items-center justify-center ${icon === e ? "border-brand-500 bg-brand-50 shadow-[0_0_0_2px_#dbe7fe]" : "border-neutral-300 hover:border-brand-500 hover:bg-brand-50"}`}>{e}</button>
            ))}
          </div>
        </div>
        <div className="mt-3.5">
          <label className="block text-[13px] font-medium text-neutral-700 mb-1.5">布局</label>
          <div className="inline-flex bg-neutral-100 rounded-lg p-0.5 gap-0.5" role="group" aria-label="布局" data-sb-scope="save-view-layout">
            {LAYOUTS.map((l) => (
              <button key={l.key} type="button" disabled={l.key === "gantt"} aria-pressed={layout === l.key}
                data-layout={l.key}
                onClick={() => setLayout(l.key)}
                className={`h-7 px-2.5 rounded-md text-[12.5px] ${layout === l.key ? "bg-white text-neutral-900 font-medium shadow-sm" : "text-neutral-400"} ${l.key === "gantt" ? "text-neutral-300 cursor-not-allowed" : "hover:text-neutral-600"}`}>{l.label}</button>
            ))}
          </div>
        </div>
        <div className="mt-4 flex items-center gap-2 bg-brand-50 border border-brand-100 rounded-lg px-3 py-2 text-[12.5px] text-neutral-600">
          ✨ 将保存当前筛选、分组与显示配置（P2 仅个人视图 · 共享归 P3 BOARD-005）
        </div>
        <div className="flex justify-end gap-2.5 mt-5">
          <button type="button" onClick={onClose} className="h-[34px] px-3.5 border border-neutral-300 rounded-md text-neutral-700 hover:bg-neutral-50">取消</button>
          <button type="button" data-sb-scope="save-view-submit" onClick={() => void submit()} disabled={!name.trim() || creating}
            className="h-[34px] px-3.5 bg-brand-500 text-white rounded-md hover:bg-brand-600 disabled:opacity-50">创建视图</button>
        </div>
      </div>
    </div>
  );
}
