/**
 * 甘特控制条（C.98 / 原型 O1·O3）：粒度三段器（1/2/3 快捷键在图表区键盘层）+
 * ←今天→ 导航（T）+ ⤢ 缩放（Ctrl+滚轮）+ 快捷键提示 + 甘特 ⋯ 菜单
 * （导出 PNG（⌘/Ctrl+E）/ 全屏 / 重置缩放——与 BOARD-003 视图 ⋯ 并存，勘误 2）。
 */
import { useEffect, useRef, useState } from "react";
import { observer } from "mobx-react-lite";
import type { GanttStore } from "../../stores/gantt";

const GRANS: Array<{ key: "day" | "week" | "month"; label: string }> = [
  { key: "day", label: "日" },
  { key: "week", label: "周" },
  { key: "month", label: "月" },
];

export const GanttToolbar = observer(function GanttToolbar({
  store, onExport, exportHint,
}: {
  store: GanttStore;
  onExport: () => void;
  exportHint: string;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  // 外击关闭（CLAUDE.md 教训 #4：mousedown 阶段 + closest 判 scope）
  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [menuOpen]);
  const fullscreen = () => {
    const el = document.querySelector("[data-gantt-wrap]") as HTMLElement | null;
    if (el?.requestFullscreen) void el.requestFullscreen().catch(() => { /* 拒绝/不支持静默 */ });
  };

  return (
    <div className="flex items-center gap-2 px-5 py-2 border-b border-neutral-200 bg-white shrink-0" data-sb-scope="gantt-ctl">
      {/* C.98 粒度三段器（Segmented；中心日期锚定不变，§3.4/原型 O3） */}
      <div className="inline-flex bg-neutral-100 rounded-lg p-0.5 gap-0.5" role="tablist" aria-label="粒度" data-sb-scope="gantt-gran">
        {GRANS.map((g) => (
          <button key={g.key} type="button" role="tab" aria-selected={store.granularity === g.key}
            data-sb-scope={`gantt-gran-${g.key}`}
            onClick={() => store.setGranularity(g.key)}
            className={`h-7 px-3 rounded-md text-[12.5px] transition-[width] duration-150 ${store.granularity === g.key ? "bg-white text-neutral-900 font-medium shadow-sm" : "text-neutral-400 hover:text-neutral-600"}`}>
            {g.label}
          </button>
        ))}
      </div>
      {/* C.98 今天导航：←今天→（300ms 平移动画落点在 store.panToCenter） */}
      <div className="flex items-center gap-1" data-sb-scope="gantt-today-nav">
        <button type="button" title="平移（←）" data-sb-scope="gantt-pan-left"
          onClick={() => store.panBy(-120)}
          className="h-8 px-2.5 border border-neutral-300 rounded-md text-[13px] text-neutral-700 hover:bg-neutral-50">←</button>
        <button type="button" data-sb-scope="gantt-today"
          onClick={() => store.panToCenter(store.today)}
          className="h-8 px-3 border border-neutral-300 rounded-md text-[13px] text-neutral-700 hover:bg-neutral-50">今天</button>
        <button type="button" title="平移（→）" data-sb-scope="gantt-pan-right"
          onClick={() => store.panBy(120)}
          className="h-8 px-2.5 border border-neutral-300 rounded-md text-[13px] text-neutral-700 hover:bg-neutral-50">→</button>
      </div>
      {/* C.98 缩放（⤢ + Ctrl+滚轮；锚点 = 光标处日期） */}
      <button type="button" title="Ctrl+滚轮缩放" data-sb-scope="gantt-zoom"
        onClick={() => store.zoom(store.pan + store.viewportW / 2, 1)}
        className="h-8 px-2.5 border border-neutral-300 rounded-md text-[13px] text-neutral-700 hover:bg-neutral-50">⤢ 缩放</button>
      <span className="text-[12px] text-neutral-400 ml-1 hidden lg:inline" data-sb-scope="gantt-kbd-hint">
        平移拖拽时间轴 · <span className="kbd">1/2/3</span> 粒度 · <span className="kbd">T</span> 今天 · <span className="kbd">↑↓</span> 行 · <span className="kbd">Shift+←→</span> 改期
      </span>
      {/* §1.4：day 视窗拖出 6 个月 → 提示切更优粒度 */}
      {store.suggestGranularity && (
        <button type="button" data-sb-scope="gantt-gran-suggest"
          onClick={() => store.setGranularity(store.suggestGranularity as "week")}
          className="h-7 px-2.5 rounded-md text-[12px] text-amber-700 bg-amber-50 border border-amber-200 hover:bg-amber-100">
          视窗跨度过大，建议切周粒度
        </button>
      )}
      {/* C.98 甘特 ⋯ 菜单（O2 勘误：甘特控制条自己的 ⋯，与视图 ⋯ 并存） */}
      <div className="ml-auto relative" data-sb-scope="gantt-menu" ref={menuRef}>
        <button type="button" aria-haspopup="menu" aria-expanded={menuOpen} data-sb-scope="gantt-menu-btn"
          onClick={() => setMenuOpen((v) => !v)}
          className="h-8 px-2.5 border border-neutral-300 rounded-md text-[13px] text-neutral-700 hover:bg-neutral-50">⋯</button>
        {menuOpen && (
          <div role="menu" aria-label="甘特操作" data-sb-scope="gantt-menu-pop"
            className="absolute right-0 top-9 z-40 min-w-[190px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1">
            <button type="button" role="menuitem" data-sb-scope="gantt-export"
              onClick={() => { setMenuOpen(false); onExport(); }}
              className="w-full text-left px-3 h-8 text-[13px] text-neutral-700 hover:bg-neutral-50">🖼 导出 PNG{exportHint}</button>
            <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); fullscreen(); }}
              className="w-full text-left px-3 h-8 text-[13px] text-neutral-700 hover:bg-neutral-50">⛶ 全屏</button>
            <div className="h-px bg-neutral-200 my-1" />
            <button type="button" role="menuitem" data-sb-scope="gantt-zoom-reset"
              onClick={() => { setMenuOpen(false); store.resetZoom(); }}
              className="w-full text-left px-3 h-8 text-[13px] text-neutral-700 hover:bg-neutral-50">↺ 重置缩放</button>
          </div>
        )}
      </div>
    </div>
  );
});
