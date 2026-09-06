import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { observer } from "mobx-react-lite";
import { reaction } from "mobx";
import {
  BulkAPI,
  unwrap,
  type BulkArchiveResult,
  type BulkDeleteResult,
  type BulkPreviewResult,
  type BulkUpdateResult,
} from "../../services/api";
import type { ApiError } from "../../services/axios";
import { toast } from "../Toast";
import { useStores } from "../../stores";
import { PRIORITY_COLUMNS } from "./view-dsl";
import type { LabelRow, MemberRow, ViewStateRow } from "./useViewPage";

/** BOARD-004 §3.1~§3.4 批量操作前端（C.77~C.83）。
 *  - SelectionStore（shared-state，§4.4）：全局选中池（跨页/切视图保留、切项目清空、
 *    上限 100 阻止追加、dispatching 冻结 BR-13）——本文件只做编排，不发起点外的写。
 *  - 多选三式（§3.5 交互表）：⌘ 追加 / Shift 区间 / 空白处框选（marquee 虚线矩形，
 *    与卡片 HTML5 拖拽互斥 R5：起点在卡片/按钮/输入框上不触发）；⌘A 全选当前视图
 *    结果集（BR-08 前端显式展开，>100 截断黄条 C.79）。
 *  - 批量工具条（§3.1）：状态/优先级即选即发 → PATCH …/issues/bulk/；指派/标签浮层
 *    （§3.2 模式三选 + 应用数恒显）；归档确认（§3.3）；删除预检 + 数量输入确认
 *    （BR-10 confirm_count + BR-14 Idempotency-Key）；失败定位弹层（§3.4 role=alertdialog，
 *    details[].field=issue_ids[<0 基索引>] 映射回选中列表）。 */

const BULK_CSS = `
@keyframes bulkin{from{opacity:0;transform:translateX(-50%) translateY(12px)}to{opacity:1;transform:translateX(-50%) translateY(0)}}
.bulk-bar{animation:bulkin .16s cubic-bezier(.2,.9,.3,1.1)}
.marquee{position:fixed;border:1.5px dashed var(--brand-500,#3f76ff);background:rgba(63,118,255,.08);z-index:50;pointer-events:none;border-radius:2px}
[data-sel-id].marquee-hit{box-shadow:inset 0 0 0 2px #3f76ff !important}
`;

/** ── 多选交互 hook（看板卡片 / 列表表格行共用）──
 *  modifier 点击（⌘/Shift）分发到 SelectionStore；普通点击由调用方自行处理（开详情）。 */
export function useBulkSelection(opts: { projectId?: string | undefined; canEdit: boolean }) {
  const { projectId, canEdit } = opts;
  const stores = useStores();
  const sel = stores.selection;
  const [, force] = useState(0);
  const lastClickIdRef = useRef<string | null>(null);

  useEffect(() => {
    sel.bindProject(projectId);
    force((n) => n + 1);
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  // store 变化 → 强制重渲染（页面无 observer HOC 的既有范式；reaction 只在引用/值变化时触发）
  useEffect(() => {
    const d = reaction(
      () => [sel.selectedIds, sel.dispatching, sel.truncatedInfo],
      () => force((x) => x + 1),
    );
    return () => d();
  }, [sel]);

  /** modifier 点击入口（⌘/Ctrl = toggle；Shift = 区间）。返回 true 表示已消费。 */
  const onModifierClick = useCallback((id: string, ev: { metaKey: boolean; ctrlKey: boolean; shiftKey: boolean }, visibleIds: string[]) => {
    if (!canEdit) { toast("当前角色无多选权限", "warning"); return true; }
    if (ev.metaKey || ev.ctrlKey) {
      sel.toggle(id);
      lastClickIdRef.current = id;
      force((n) => n + 1);
      return true;
    }
    if (ev.shiftKey && lastClickIdRef.current) {
      sel.range(lastClickIdRef.current, id, visibleIds);
      force((n) => n + 1);
      return true;
    }
    return false;
  }, [canEdit, sel]);

  /** 复选框/角标点击（无 modifier 直接 toggle）。 */
  const onCheckboxClick = useCallback((id: string) => {
    if (!canEdit) { toast("当前角色无多选权限", "warning"); return; }
    sel.toggle(id);
    lastClickIdRef.current = id;
    force((n) => n + 1);
  }, [canEdit, sel]);

  const isSelected = useCallback((id: string) => sel.selectedIds.has(id), [sel]);

  return { sel, isSelected, anySelected: sel.count > 0, onModifierClick, onCheckboxClick: onCheckboxClick };
}

/** ── 框选（marquee）：空白处 pointerdown 拖出虚线矩形，触及 [data-sel-id] 即选 ──
 *  与拖拽互斥（R5）：起点命中卡片/按钮/输入框/链接/表头即不启动。
 *  additive=false（默认）= 命中集替换当前选中；Shift 按住 = 追加（原型 bindMarquee 同款）。 */
export function useMarquee(containerRef: React.RefObject<HTMLElement | null>, opts: {
  enabled: boolean;
  onHit: (ids: string[], additive: boolean) => void;
}) {
  const { enabled } = opts;
  const stateRef = useRef<{ start: { x: number; y: number }; additive: boolean; el: HTMLDivElement } | null>(null);
  const onHitRef = useRef(opts.onHit);
  const enabledRef = useRef(enabled);
  // refs 写入只在 effect 内（react-hooks/refs 纪律）
  useEffect(() => {
    onHitRef.current = opts.onHit;
    enabledRef.current = enabled;
  }, [opts.onHit, enabled]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const onDown = (e: PointerEvent) => {
      if (e.button !== 0 || !enabledRef.current) return;
      const t = e.target as HTMLElement | null;
      // 互斥：起点在可选元素 / 交互控件 / 表头上 = 拖拽或点击，不进入框选
      if (t?.closest("[data-sel-id],button,input,textarea,a,select,thead,th,[data-no-marquee]")) return;
      const el = document.createElement("div");
      el.className = "marquee";
      el.style.display = "none";
      document.body.appendChild(el);
      stateRef.current = { start: { x: e.clientX, y: e.clientY }, additive: e.shiftKey, el };
    };
    const onMove = (e: PointerEvent) => {
      const st = stateRef.current;
      if (!st) return;
      const x = Math.min(st.start.x, e.clientX), y = Math.min(st.start.y, e.clientY);
      const w = Math.abs(e.clientX - st.start.x), h = Math.abs(e.clientY - st.start.y);
      if (w * h < 36) return;
      st.el.style.display = "block";
      Object.assign(st.el.style, { left: `${x}px`, top: `${y}px`, width: `${w}px`, height: `${h}px` });
      const r = { l: x, t: y, r: x + w, b: y + h };
      document.querySelectorAll<HTMLElement>("[data-sel-id]").forEach((el2) => {
        el2.classList.remove("marquee-hit");
        const b = el2.getBoundingClientRect();
        if (b.right > r.l && b.left < r.r && b.bottom > r.t && b.top < r.b) el2.classList.add("marquee-hit");
      });
    };
    const onUp = () => {
      const st = stateRef.current;
      if (!st) return;
      const ids = [...document.querySelectorAll<HTMLElement>("[data-sel-id].marquee-hit")].map((el2) => el2.dataset.selId ?? "").filter(Boolean);
      st.el.remove();
      stateRef.current = null;
      document.querySelectorAll<HTMLElement>("[data-sel-id]").forEach((el2) => el2.classList.remove("marquee-hit"));
      if (ids.length) onHitRef.current(ids, st.additive);
    };
    container.addEventListener("pointerdown", onDown);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      container.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      stateRef.current?.el.remove();
      stateRef.current = null;
    };
  }, [containerRef]);
}

/** 失败项（§3.4）：details[].field=issue_ids[i] 0 基索引 → 选中列表序号 + 原因。 */
export interface BulkFailItem { index: number; message: string; code: string }

export function parseBulkFailures(err: unknown): BulkFailItem[] {
  const details = (err as ApiError | undefined)?.details ?? [];
  const out: BulkFailItem[] = [];
  for (const d of details) {
    const m = /^(?:issue_ids\[)(\d+)(?:\])$/.exec(String(d?.field ?? ""));
    if (m) out.push({ index: Number(m[1]), message: String(d?.message ?? ""), code: String(d?.code ?? "") });
  }
  return out;
}

/** ── 截断黄条（C.79：⌘A >100「已选前 100 / 共 N」）── */
export const TruncationStrip = observer(function TruncationStrip() {
  const sel = useStores().selection;
  if (!sel.truncatedInfo) return null;
  return (
    <div data-sb-scope="bulk-trunc-strip" role="status"
      className="sticky top-0 z-30 flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-[12.5px] text-amber-700 mb-2">
      ⚠ 已选前 {sel.truncatedInfo.selected} / 共 {sel.truncatedInfo.total} —— 批量上限 100（BR-01）
      <button className="ml-1 text-[12.5px] text-brand-600 hover:underline" data-sb-scope="bulk-trunc-ok"
        onClick={() => sel.clearTruncation()}>知道了</button>
    </div>
  );
});

/** ── 失败定位弹层（C.82 · §3.4 role=alertdialog）── */
function FailDialog({ batch, fails, onRetry, onBack, onGoto }: {
  batch: number;
  fails: BulkFailItem[];
  onRetry: () => void;
  onBack: () => void;
  onGoto: (index: number) => void;
}) {
  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center p-4 z-[95]" data-sb-scope="bulk-fail-overlay">
      <div className="bg-white rounded-xl shadow-lg w-[520px] max-w-full p-5" role="alertdialog" aria-modal="true"
        aria-label="批量校验失败" data-sb-scope="bulk-fail-dialog">
        <div className="flex items-center gap-2 text-base font-semibold text-red-600 mb-1">
          ⚠ {batch} 项中 {fails.length} 项未通过校验，本次未执行任何修改
          <button className="ml-auto w-7 h-7 flex items-center justify-center text-neutral-500 hover:text-neutral-900" aria-label="关闭" onClick={onBack}>✕</button>
        </div>
        <p className="text-[12px] text-neutral-400 mb-1">单事务全成败：任一条目失败整批 ROLLBACK（400 · details[] 项级 0 基索引）</p>
        <div className="max-h-[300px] overflow-y-auto">
          {fails.map((f, i) => (
            <button key={i} type="button" data-sb-scope="bulk-fail-item" data-fail-index={f.index}
              onClick={() => onGoto(f.index)}
              className="w-full text-left border border-red-200 rounded-lg px-3 py-2 mt-2 hover:bg-red-50">
              <span className="font-mono text-[12px] text-neutral-400">#{f.index + 1}</span>{" "}
              <span className="text-[13px]">{f.message}</span>
              <div className="text-[12px] text-red-600 mt-0.5">{f.code === "BLOCKED_BY" ? "流转守卫拦截（前置未完成）" : f.code === "PERM_DENIED" ? "权限不足" : f.code === "DOES_NOT_EXIST" ? "任务不存在或不可见" : f.code}</div>
            </button>
          ))}
        </div>
        <div className="flex justify-end gap-2.5 mt-5">
          <button className="h-[34px] px-3.5 border border-neutral-300 rounded-md" data-sb-scope="bulk-fail-keep" onClick={onBack}>保留选中返回</button>
          <button className="h-[34px] px-3.5 bg-brand-500 text-white rounded-md hover:bg-brand-600" data-sb-scope="bulk-fail-retry"
            onClick={onRetry}>移除该项并重试（{batch - fails.length} 项）</button>
        </div>
      </div>
    </div>
  );
}

/** ── 指派 / 标签浮层（C.81 · §3.2 模式三选 + 应用数恒显）── */
function BulkSetPop({ kind, count, members, labels, onApply, onClose }: {
  kind: "assign" | "label";
  count: number;
  members: MemberRow[];
  labels: LabelRow[];
  onApply: (mode: "replace" | "add" | "remove", ids: string[], note: string) => void;
  onClose: () => void;
}) {
  const [mode, setMode] = useState<"replace" | "add" | "remove">("replace");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [note, setNote] = useState("");
  const [q, setQ] = useState("");
  const isA = kind === "assign";
  const candidates = useMemo(() => {
    const pool = isA ? members.map((m) => ({ id: m.id, name: m.name, sub: "" })) : labels.map((l) => ({ id: l.id, name: l.name, color: l.color }));
    const s = q.trim().toLowerCase();
    return s ? pool.filter((c) => c.name.toLowerCase().includes(s)) : pool;
  }, [isA, members, labels, q]);

  const MODES: Array<["replace" | "add" | "remove", string, string]> = [
    ["replace", "替换为", "全量替换（PUT）"],
    ["add", "添加到", "并集"],
    ["remove", "从中移除", "差集"],
  ];

  return (
    <div className="fixed bottom-[78px] left-1/2 -translate-x-1/2 z-[85] w-[400px] max-w-[calc(100vw-24px)] bg-white border border-neutral-200 rounded-xl shadow-lg p-4"
      role="dialog" aria-label={`批量${isA ? "指派" : "标签"}`} data-sb-scope="bulk-set-pop">
      <div className="flex items-center mb-2.5">
        <div className="text-[15px] font-semibold">批量{isA ? "指派" : "标签"} · 将应用到 {count} 个任务</div>
        <button className="ml-auto w-7 h-7 flex items-center justify-center text-neutral-500 hover:text-neutral-900" aria-label="关闭" onClick={onClose}>✕</button>
      </div>
      <div className="flex gap-2 mb-3" role="radiogroup" aria-label="集合模式" data-sb-scope="bulk-set-modes">
        {MODES.map(([k, t, sub]) => (
          <button key={k} type="button" role="radio" aria-checked={mode === k} data-sb-scope="bulk-set-mode" data-mode={k}
            onClick={() => setMode(k)}
            className={`flex-1 border rounded-lg px-2.5 py-2 text-left flex flex-col gap-0.5 ${mode === k ? "border-brand-500 bg-brand-50 text-brand-600" : "border-neutral-300 text-neutral-600 hover:border-brand-500"}`}>
            <span className="text-[13px] font-semibold flex items-center gap-1">{mode === k ? "◉" : "○"}{t}</span>
            <span className="text-[11.5px] text-neutral-400">{sub}</span>
          </button>
        ))}
      </div>
      <div className="flex items-center gap-1.5 h-9 border border-neutral-300 rounded-md px-2.5 focus-within:border-brand-500">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-neutral-400"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
        <input className="flex-1 h-7 outline-none text-[13px] bg-transparent" placeholder={`搜索${isA ? "成员" : "标签"}…`}
          aria-label={`搜索${isA ? "成员" : "标签"}`} value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <div className="mt-1.5 border border-neutral-200 rounded-lg max-h-[180px] overflow-y-auto" role="list">
        {candidates.length === 0 ? (
          <div className="px-3 py-4 text-[13px] text-neutral-400 text-center">无候选</div>
        ) : candidates.map((c) => (
          <label key={c.id} className="flex items-center gap-2 px-2.5 py-[7px] text-[13px] cursor-pointer hover:bg-neutral-50" data-sb-scope="bulk-set-row">
            <input type="checkbox" className="accent-brand-500 w-[15px] h-[15px]" data-sb-scope="bulk-set-cb" data-v={c.id}
              checked={picked.has(c.id)}
              onChange={(e) => {
                const next = new Set(picked);
                if (e.target.checked) next.add(c.id); else next.delete(c.id);
                setPicked(next);
              }} />
            {"color" in c && c.color ? <span className="w-2 h-2 rounded-full shrink-0" style={{ background: c.color as string }} /> : null}
            <span className="w-5 h-5 rounded-full bg-brand-500 text-white text-[10px] font-semibold inline-flex items-center justify-center shrink-0" aria-hidden="true">{Array.from(c.name)[0]}</span>
            <span className="flex-1 truncate">{c.name}</span>
          </label>
        ))}
      </div>
      <div className="py-2 text-[12px]" data-sb-scope="bulk-set-picked">
        {picked.size ? [...picked].map((id) => {
          const n = isA ? members.find((m) => m.id === id)?.name : labels.find((l) => l.id === id)?.name;
          return (
            <span key={id} className="inline-flex items-center gap-1 rounded-full bg-brand-50 text-brand-600 px-2 py-0.5 mr-1 mb-1">
              {n}
              <button aria-label={`移除 ${n}`} onClick={() => { const nx = new Set(picked); nx.delete(id); setPicked(nx); }}>✕</button>
            </span>
          );
        }) : <span className="text-neutral-400">未选择</span>}
      </div>
      <label className="block text-[13px] font-medium text-neutral-700 mb-1">备注（可选，将随动态记录 · ≤500 字）</label>
      <textarea className="w-full border border-neutral-300 rounded-md p-2 text-[13px] min-h-[48px] focus:outline-none focus:border-brand-500"
        placeholder={isA ? "联调阶段统一对接人…" : "本轮修复统一打标…"} aria-label="批量备注" data-sb-scope="bulk-set-note"
        value={note} onChange={(e) => setNote(e.target.value)} />
      <div className="flex justify-end gap-2.5 mt-3.5">
        <button className="h-[34px] px-3.5 border border-neutral-300 rounded-md" onClick={onClose}>取消</button>
        <button className="h-[34px] px-3.5 bg-brand-500 text-white rounded-md hover:bg-brand-600" data-sb-scope="bulk-set-apply"
          onClick={() => {
            if (!picked.size) { toast(`请先选择至少 1 个${isA ? "成员" : "标签"}`, "warning"); return; }
            onApply(mode, [...picked], note.trim());
          }}>应用到 {count} 项</button>
      </div>
    </div>
  );
}

/** ── 归档确认（C.83 · §3.3 可逆无需输入 · 展示含子树 N）── */
function ArchiveConfirm({ n, preview, busy, onOk, onCancel }: {
  n: number; preview: BulkPreviewResult | null; busy: boolean;
  onOk: () => void; onCancel: () => void;
}) {
  const sub = preview ? preview.cascade_total : null;
  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center p-4 z-[90]" data-sb-scope="bulk-arch-overlay">
      <div className="bg-white rounded-xl shadow-lg w-[440px] max-w-full p-5" role="dialog" aria-modal="true" aria-label="批量归档确认" data-sb-scope="bulk-arch-dialog">
        <div className="flex items-center gap-2 text-base font-semibold mb-2">🗄 批量归档 {n} 个任务？
          <button className="ml-auto w-7 h-7 flex items-center justify-center text-neutral-500 hover:text-neutral-900" aria-label="关闭" onClick={onCancel}>✕</button>
        </div>
        <p className="text-[13.5px] text-neutral-700">归档可逆（任务列表「已归档」可恢复）。{sub != null && sub > 0 ? <>其中含子任务级联 <b>{sub}</b> 个（随父级联归档）。</> : null}</p>
        {preview && preview.denied.length > 0 && (
          <div className="mt-2 border border-red-200 bg-red-50 rounded-lg px-3 py-2 text-[12.5px] text-red-700" data-sb-scope="bulk-arch-denied">
            {preview.denied.map((d) => <div key={d.index}>{d.issue_key}：{d.reason}</div>)}
          </div>
        )}
        <div className="flex justify-end gap-2.5 mt-5">
          <button className="h-[34px] px-3.5 border border-neutral-300 rounded-md" onClick={onCancel}>取消</button>
          <button className="h-[34px] px-3.5 bg-brand-500 text-white rounded-md hover:bg-brand-600" data-sb-scope="bulk-arch-ok" disabled={busy} onClick={onOk}>归档 {n} 项</button>
        </div>
      </div>
    </div>
  );
}

/** ── 删除确认（C.83 · §3.3 预检级联统计 + 数量输入激活，BR-10）── */
function DeleteConfirm({ n, preview, confirmInput, busy, onInput, onOk, onCancel }: {
  n: number; preview: BulkPreviewResult | null; confirmInput: string; busy: boolean;
  onInput: (v: string) => void; onOk: () => void; onCancel: () => void;
}) {
  const ok = Number(confirmInput) === n && confirmInput !== "";
  const affected = preview?.affected_total ?? n;
  const cascade = preview?.cascade_total ?? null;
  const withSub = preview?.with_subtree ?? null;
  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center p-4 z-[90]" data-sb-scope="bulk-del-overlay">
      <div className="bg-white rounded-xl shadow-lg w-[520px] max-w-full p-5" role="dialog" aria-modal="true" aria-label="批量删除确认" data-sb-scope="bulk-del-dialog">
        <div className="flex items-center gap-2 text-base font-semibold text-red-600 mb-2">⚠ 批量删除 {n} 个任务？
          <button className="ml-auto w-7 h-7 flex items-center justify-center text-neutral-500 hover:text-neutral-900" aria-label="关闭" onClick={onCancel}>✕</button>
        </div>
        <div id="bulk-del-stats" className="border border-red-200 bg-red-50 rounded-lg px-3.5 py-2.5 text-[13px] text-red-700" data-sb-scope="bulk-del-stats">
          {withSub != null && withSub > 0 ? <>其中 {withSub} 个含子任务（将级联删除 {cascade} 个子任务，</> : null}
          合计影响 <b>{affected}</b> 个任务{withSub != null && withSub > 0 ? <>）。</> : <>。</>}
        </div>
        <p className="text-[13px] text-neutral-400 mt-2">删除后任务进入回收站（管理端可恢复），关联的评论、工时、依赖将被保留但随任务隐藏。</p>
        {preview && preview.denied.length > 0 && (
          <div className="mt-2 border border-red-200 bg-red-50 rounded-lg px-3 py-2 text-[12.5px] text-red-700" data-sb-scope="bulk-del-denied">
            {preview.denied.map((d) => <div key={d.index}>{d.issue_key}：{d.reason}</div>)}
          </div>
        )}
        <div className="flex items-center gap-2.5 mt-3.5">
          <span className="text-[13px] text-neutral-700">请输入数量确认：</span>
          <input className={`w-[90px] h-9 border rounded-md text-center text-[15px] tabular-nums focus:outline-none ${ok ? "border-emerald-600 text-emerald-600 font-semibold" : "border-neutral-300"}`}
            inputMode="numeric" placeholder="0" aria-describedby="bulk-del-stats" data-sb-scope="bulk-del-count"
            value={confirmInput} onChange={(e) => onInput(e.target.value.replace(/[^\d]/g, ""))} />
          <span className="text-[12px] text-neutral-400">输入 {n} 激活按钮</span>
        </div>
        <div className="flex justify-end gap-2.5 mt-5">
          <button className="h-[34px] px-3.5 border border-neutral-300 rounded-md" onClick={onCancel}>取消</button>
          <button className="h-[34px] px-3.5 bg-red-500 text-white rounded-md hover:bg-red-600 disabled:opacity-45 disabled:cursor-not-allowed"
            data-sb-scope="bulk-del-ok" disabled={!ok || busy} onClick={onOk}>确认删除 {n} 项</button>
        </div>
      </div>
    </div>
  );
}

/** ── 批量操作宿主（工具条 + 全部弹层 + 动作分发）──
 *  挂载于看板/列表/表格三路由（C.80）；visibleIds = 当前视图结果集（⌘A 作用域，BR-08）。 */
export const BulkOperations = observer(function BulkOperations(opts: {
  workspaceSlug?: string | undefined;
  projectId?: string | undefined;
  canEdit: boolean;
  visibleIds: string[];
  states: ViewStateRow[];
  members: MemberRow[];
  labels: LabelRow[];
  onOpenIssue: (id: string) => void;
  onMutated: () => void;
}) {
  const { workspaceSlug, projectId, canEdit, visibleIds, states, members, labels, onOpenIssue, onMutated } = opts;
  const sel = useStores().selection;
  const [, force] = useState(0);
  const bump = () => force((n) => n + 1);
  /** 状态/优先级下拉开关（menu 渲染在工具条上方）。 */
  const [menu, setMenu] = useState<null | "state" | "priority">(null);
  const [setPop, setSetPop] = useState<null | "assign" | "label">(null);
  const [archOpen, setArchOpen] = useState(false);
  const [archPreview, setArchPreview] = useState<BulkPreviewResult | null>(null);
  const [delOpen, setDelOpen] = useState(false);
  const [delPreview, setDelPreview] = useState<BulkPreviewResult | null>(null);
  const [delInput, setDelInput] = useState("");
  const [fail, setFail] = useState<null | { fails: BulkFailItem[]; retry: (() => Promise<void>) | null }>(null);

  // store 变化 → 重渲染（reaction：引用替换/冻结态/截断条变化时触发）
  useEffect(() => {
    const d = reaction(
      () => [sel.selectedIds, sel.dispatching, sel.truncatedInfo],
      () => force((x) => x + 1),
    );
    return () => d();
  }, [sel]);

  const ids = useMemo(() => [...sel.selectedIds], [sel.selectedIds]);
  const n = sel.count;
  const busy = sel.dispatching;

  // Esc 层级：弹窗 → 浮层/菜单 → 清空选中（C.79 Esc/✕）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (fail) { setFail(null); return; }
      if (archOpen || delOpen) { setArchOpen(false); setDelOpen(false); return; }
      if (setPop || menu) { setSetPop(null); setMenu(null); return; }
      if (sel.count > 0) { sel.clear(); bump(); }
    };
    // ⌘A 全选当前视图结果集（C.79；输入框聚焦时不拦截）
    const onA = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "a") {
        const t = e.target as HTMLElement | null;
        if (t?.closest("input,textarea,[contenteditable=true]")) return;
        if (!canEdit || sel.dispatching) return;
        e.preventDefault();
        if (visibleIds.length === 0) { toast("当前视图无任务", "warning"); return; }
        sel.selectViewResults(visibleIds);
        bump();
      }
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("keydown", onA);
    return () => { document.removeEventListener("keydown", onKey); document.removeEventListener("keydown", onA); };
  }, [fail, archOpen, delOpen, setPop, menu, sel, canEdit, visibleIds, n]);

  /** 统一失败处置：项级 details → 失败定位弹层；载荷级 → toast（C.82）。 */
  function handleFailure(err: unknown, retry: () => Promise<void>): boolean {
    const fails = parseBulkFailures(err);
    if (fails.length > 0) {
      setFail({ fails, retry: () => retry() });
      return true;
    }
    const e = err as ApiError | undefined;
    toast(e?.details?.[0]?.message ?? e?.message ?? "批量操作失败", "error");
    return false;
  }

  async function dispatch(fn: () => Promise<void>) {
    sel.setDispatching(true);
    bump();
    try {
      await fn();
    } finally {
      sel.setDispatching(false);
      bump();
    }
  }

  function succeed(msg: string) {
    toast(msg, "ok");
    sel.clear();
    bump();
    onMutated();
  }

  /** PATCH …/issues/bulk/（状态/优先级即选即发 + 指派/标签浮层应用，§4.2.1）。 */
  async function patchBulk(payload: Parameters<typeof BulkAPI.patch>[2], successMsg: (updated: number) => string, retryPayload?: () => Parameters<typeof BulkAPI.patch>[2]) {
    if (!workspaceSlug || !projectId) return;
    await dispatch(async () => {
      try {
        const r = await BulkAPI.patch(workspaceSlug, projectId, payload);
        succeed(successMsg(unwrap<BulkUpdateResult>(r)?.updated ?? payload.issue_ids.length));
      } catch (err) {
        handleFailure(err, async () => { await patchBulk(retryPayload ? retryPayload() : payload, successMsg, retryPayload); });
      }
    });
  }

  function applyState(stateId: string) {
    setMenu(null);
    void patchBulk(
      { issue_ids: ids, patch: { state_id: stateId } },
      (u) => `已更新 ${u} 项`,
      () => ({ issue_ids: [...sel.selectedIds], patch: { state_id: stateId } }),
    );
  }

  function applyPriority(priority: string) {
    setMenu(null);
    void patchBulk(
      { issue_ids: ids, patch: { priority } },
      (u) => `已更新 ${u} 项`,
      () => ({ issue_ids: [...sel.selectedIds], patch: { priority } }),
    );
  }

  function applySet(kind: "assign" | "label", mode: "replace" | "add" | "remove", picked: string[], note: string) {
    setSetPop(null);
    const mk = (useIds: string[]): Parameters<typeof BulkAPI.patch>[2] => {
      const base = kind === "assign"
        ? { issue_ids: useIds, assignees: { mode, assignee_ids: picked } }
        : { issue_ids: useIds, labels: { mode, label_ids: picked } };
      return note ? { ...base, comment: note } : base;
    };
    void patchBulk(
      mk(ids),
      (u) => `已更新 ${u} 项`,
      () => mk([...sel.selectedIds]),
    );
  }

  /** 归档：打开确认时并发预检（含子树 N + denied 前移，§4.2.4）。 */
  async function openArchive() {
    setArchOpen(true);
    setArchPreview(null);
    if (!workspaceSlug || !projectId) return;
    try {
      const r = await BulkAPI.preview(workspaceSlug, projectId, { issue_ids: ids, action: "archive" });
      setArchPreview(unwrap(r));
    } catch { /* 预检失败：确认层退化为本计数 */ }
  }

  async function doArchive() {
    setArchOpen(false);
    if (!workspaceSlug || !projectId) return;
    await dispatch(async () => {
      try {
        const r = await BulkAPI.archive(workspaceSlug, projectId, { issue_ids: ids });
        const d = unwrap<BulkArchiveResult>(r);
        succeed(`已归档 ${d?.archived_count ?? ids.length} 项（级联影响 ${d?.affected_total ?? ids.length}）`);
      } catch (err) {
        handleFailure(err, async () => {
          const rest = [...sel.selectedIds];
          if (rest.length === 0) return;
          try {
            const r2 = await BulkAPI.archive(workspaceSlug!, projectId!, { issue_ids: rest });
            const d2 = unwrap<BulkArchiveResult>(r2);
            succeed(`重试成功：已归档 ${d2?.archived_count ?? rest.length} 项`);
          } catch (err2) { toast((err2 as ApiError)?.message ?? "重试失败", "error"); }
        });
      }
    });
  }

  /** 删除：打开确认时预检级联统计（§4.2.4），输入数量激活（BR-10）。 */
  async function openDelete() {
    setDelOpen(true);
    setDelInput("");
    setDelPreview(null);
    if (!workspaceSlug || !projectId) return;
    try {
      const r = await BulkAPI.preview(workspaceSlug, projectId, { issue_ids: ids, action: "delete" });
      setDelPreview(unwrap(r));
    } catch { /* 预检失败：统计退化为本计数 */ }
  }

  async function doDelete() {
    setDelOpen(false);
    if (!workspaceSlug || !projectId) return;
    const cur = ids;
    await dispatch(async () => {
      try {
        const r = await BulkAPI.del(workspaceSlug, projectId, { issue_ids: cur, confirm_count: cur.length });
        const d = unwrap<BulkDeleteResult>(r);
        succeed(`已删除 ${d?.deleted ?? cur.length} 项（级联影响 ${d?.affected_total ?? cur.length} · Idempotency-Key 已携带）`);
      } catch (err) {
        handleFailure(err, async () => {
          const rest = [...sel.selectedIds];
          if (rest.length === 0) return;
          try {
            const r2 = await BulkAPI.del(workspaceSlug!, projectId!, { issue_ids: rest, confirm_count: rest.length });
            const d2 = unwrap<BulkDeleteResult>(r2);
            succeed(`重试成功：已删除 ${d2?.deleted ?? rest.length} 项`);
          } catch (err2) { toast((err2 as ApiError)?.message ?? "重试失败", "error"); }
        });
      }
    });
  }

  if (!canEdit || n === 0) return <style>{BULK_CSS}</style>;

  return (
    <>
      <style>{BULK_CSS}</style>
      {/* C.80 批量工具条：底部居中浮动，role=toolbar */}
      <div data-sb-scope="bulk-bar"
        className={`bulk-bar fixed bottom-4 left-1/2 -translate-x-1/2 z-[80] bg-white border border-neutral-300 rounded-[14px] shadow-lg h-[52px] flex items-center gap-2 pl-4 pr-2 ${busy ? "opacity-75 pointer-events-none" : ""}`}
        role="toolbar" aria-label={`批量操作，已选 ${n} 项`}>
        <span className="text-[13px] font-semibold tabular-nums whitespace-nowrap" data-sb-scope="bulk-count">✓ 已选 {n} 项</span>
        <span className="w-px h-[22px] bg-neutral-200" />
        <div className="relative">
          <button type="button" data-sb-scope="bulk-state-btn" className={`h-8 px-2.5 rounded-lg text-[13px] text-neutral-600 hover:bg-neutral-100 flex items-center gap-1.5 ${busy ? "animate-pulse" : ""}`}
            onClick={() => setMenu(menu === "state" ? null : "state")}>状态 ▾</button>
          {menu === "state" && (
            <div role="menu" aria-label="批量改状态" data-sb-scope="bulk-state-menu"
              className="absolute bottom-10 left-0 z-10 min-w-[160px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1 max-h-[260px] overflow-y-auto">
              {states.map((s) => (
                <button key={s.id} role="menuitem" data-sb-scope="bulk-state-item" data-state-id={s.id}
                  onClick={() => applyState(s.id)}
                  className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50 flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full shrink-0" style={{ background: s.color }} />{s.name}
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="relative">
          <button type="button" data-sb-scope="bulk-pri-btn" className={`h-8 px-2.5 rounded-lg text-[13px] text-neutral-600 hover:bg-neutral-100 flex items-center gap-1.5 ${busy ? "animate-pulse" : ""}`}
            onClick={() => setMenu(menu === "priority" ? null : "priority")}>优先级 ▾</button>
          {menu === "priority" && (
            <div role="menu" aria-label="批量改优先级" data-sb-scope="bulk-pri-menu"
              className="absolute bottom-10 left-0 z-10 min-w-[140px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1">
              {PRIORITY_COLUMNS.map((p) => (
                <button key={p.key} role="menuitem" data-sb-scope="bulk-pri-item" data-priority={p.key}
                  onClick={() => applyPriority(p.key)}
                  className="w-full text-left px-3 h-8 text-[13px] hover:bg-neutral-50 flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full shrink-0" style={{ background: p.color }} />{p.name}
                </button>
              ))}
            </div>
          )}
        </div>
        <button type="button" data-sb-scope="bulk-assign-btn" className="h-8 px-2.5 rounded-lg text-[13px] text-neutral-600 hover:bg-neutral-100"
          onClick={() => { setMenu(null); setSetPop(setPop === "assign" ? null : "assign"); }}>指派…</button>
        <button type="button" data-sb-scope="bulk-label-btn" className="h-8 px-2.5 rounded-lg text-[13px] text-neutral-600 hover:bg-neutral-100"
          onClick={() => { setMenu(null); setSetPop(setPop === "label" ? null : "label"); }}>标签…</button>
        <span className="w-px h-[22px] bg-neutral-200" />
        <button type="button" data-sb-scope="bulk-archive-btn" className="h-8 px-2.5 rounded-lg text-[13px] text-neutral-600 hover:bg-neutral-100"
          onClick={() => void openArchive()}>🗄 归档</button>
        <button type="button" data-sb-scope="bulk-delete-btn" className="h-8 px-2.5 rounded-lg text-[13px] text-red-600 hover:bg-red-50"
          onClick={() => void openDelete()}>🗑 删除</button>
        <span className="w-px h-[22px] bg-neutral-200" />
        <button type="button" data-sb-scope="bulk-clear" aria-label="清空选中（Esc）"
          className="w-[30px] h-[30px] rounded-lg flex items-center justify-center text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700"
          onClick={() => { sel.clear(); bump(); }}>✕</button>
      </div>

      {/* C.81 指派/标签浮层 */}
      {setPop && (
        <BulkSetPop kind={setPop} count={n} members={members} labels={labels}
          onApply={(mode, picked, note) => applySet(setPop, mode, picked, note)}
          onClose={() => setSetPop(null)} />
      )}

      {/* C.83 归档/删除确认 */}
      {archOpen && (
        <ArchiveConfirm n={n} preview={archPreview} busy={busy}
          onOk={() => void doArchive()} onCancel={() => setArchOpen(false)} />
      )}
      {delOpen && (
        <DeleteConfirm n={n} preview={delPreview} confirmInput={delInput} busy={busy}
          onInput={setDelInput} onOk={() => void doDelete()} onCancel={() => setDelOpen(false)} />
      )}

      {/* C.82 失败定位弹层 */}
      {fail && (
        <FailDialog batch={n} fails={fail.fails}
          onRetry={() => {
            // 剔除全部失败项重发（BR-08：仍是显式列表）
            const failIdx = new Set(fail.fails.map((f) => f.index));
            sel.removeMany(ids.filter((_, i) => failIdx.has(i)));
            setFail(null);
            bump();
            if (fail.retry) void fail.retry();
          }}
          onBack={() => setFail(null)}
          onGoto={(index) => {
            const id = ids[index];
            setFail(null);
            if (id) onOpenIssue(id);
          }} />
      )}
    </>
  );
});
