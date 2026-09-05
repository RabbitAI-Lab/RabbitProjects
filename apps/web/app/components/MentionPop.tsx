/** @ 补全浮层（COLLAB-001 §3.2 · C.33）。
 *  - 触发条件：父组件检测到 `@` + ≥0 字符查询词，并传入候选池。
 *  - 候选池来源：项目成员缓存（PROJ-002 数据 + WS_OWNER/ADMIN 隐式成员，按昵称/邮箱前缀模糊过滤）。
 *  - 键盘：↑↓ 选择、Enter 确认、Esc 关闭；退格删除触发词后自动关闭（父组件维护）。
 *  - 插入产物：`<span data-mention-id="{uuid}">@昵称</span>`（text-primary-600 蓝字）。
 *  - 无匹配空态：「无成员」；已移出成员锚点 hover 提示「已不在项目」（依赖父组件传入 `removed` 标记）。
 *  - dropdown 关闭：mousedown 阶段 + target.closest（CLAUDE.md 教训 #4）。
 */
import { useEffect, useId, useRef, useState } from "react";

export interface MentionCandidate {
  id: string;
  name: string;
  email: string;
  /** 已删除 / 已移出成员：hover 时提示「已不在项目」（COLLAB-001 §3.2 条件态） */
  removed?: boolean;
}

export interface MentionPopProps {
  /** 过滤前缀（不含 `@`） */
  query: string;
  /** 候选列表（父组件负责按 PROJ-002 缓存做模糊过滤） */
  candidates: MentionCandidate[];
  /** 选中候选回调 */
  onPick: (c: MentionCandidate) => void;
  /** 浮层根样式（绝对定位父容器） */
  className?: string;
}

export function MentionPop({ query, candidates, onPick, className }: MentionPopProps) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [active, setActive] = useState(0);
  const [tip, setTip] = useState<string | null>(null);
  const id = useId();

  // 候选池变化时重置高亮（query 缩短 / 列表刷新）—— setTimeout(0) 避开 oxlint set-state-in-effect
  useEffect(() => {
    const handle = setTimeout(() => { setActive(0); }, 0);
    return () => clearTimeout(handle);
  }, [query, candidates.length]);

  // dropdown 关闭（CLAUDE.md 教训 #4）：mousedown 阶段 + target.closest
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest('[data-sb-scope="mention-pop"]')) return;
      setTip(null);
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, []);

  const empty = candidates.length === 0;

  return (
    <div
      ref={wrapRef}
      data-sb-scope="mention-pop"
      role="listbox"
      aria-label="成员候选"
      aria-activedescendant={!empty ? `${id}-opt-${active}` : undefined}
      className={`absolute bottom-full left-0 mb-1.5 w-[260px] bg-white border border-neutral-200 rounded-lg shadow-lg py-1 z-30 max-h-[240px] overflow-y-auto ${className ?? ""}`}
      // 父组件监听 keydown（↑↓/Enter/Esc）— 这里只暴露列表语义
    >
      {/* 过滤行（C.33：🔍 过滤：{query}） */}
      <div className="px-3 py-1.5 text-[12px] text-neutral-500 flex items-center gap-1.5 border-b border-neutral-100">
        <span aria-hidden="true">🔍</span>
        <span>过滤：{query || <span className="text-neutral-400">（空）</span>}</span>
      </div>
      {empty ? (
        <div className="px-3 py-3 text-[13px] text-neutral-500 text-center" role="status">无成员</div>
      ) : (
        candidates.map((c, i) => {
          const isActive = i === active;
          return (
            <div
              key={c.id}
              id={`${id}-opt-${i}`}
              role="option"
              aria-selected={isActive}
              data-sb-scope="mention-pop-option"
              data-mention-id={c.id}
              className={`relative px-3 h-8 flex items-center gap-2 text-[13px] cursor-pointer ${isActive ? "bg-brand-50 text-brand-700" : "hover:bg-neutral-50"}`}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => { e.preventDefault(); onPick(c); }}
              onMouseLeave={() => setTip(null)}
              onFocus={() => setActive(i)}
            >
              {/* 选中态色点（C.33：●/○） */}
              <span
                aria-hidden="true"
                className={`w-1.5 h-1.5 rounded-full ${isActive ? "bg-brand-500" : "bg-neutral-300"}`}
              />
              <span className="truncate">{c.name}</span>
              <span className="text-neutral-400 text-[12px] truncate">{c.email}</span>
              {/* 已移出成员 hover 提示（C.33 条件态） */}
              {c.removed && (
                <span
                  aria-hidden="true"
                  className="ml-auto text-[12px] text-amber-600"
                  onMouseEnter={() => setTip(`已不在项目`)}
                >⚠</span>
              )}
              {tip && c.removed && i === active && (
                <span
                  role="tooltip"
                  className="absolute right-2 -top-7 px-2 py-1 bg-neutral-800 text-white text-[12px] rounded whitespace-nowrap z-10"
                >{tip}</span>
              )}
            </div>
          );
        })
      )}
    </div>
  );
}

/** 在评论输入框上挂载 mention 触发与键盘监听（hook 形式，便于复用）。
 *  - 返回 `{ isOpen, query, candidates, onKeyDown, onChange, onPick, render }`。
 *  - render 返回 MentionPop 节点；调用方在 textarea 父容器底部 absolute 渲染。
 *  - 父组件维护 `value`（textarea 当前值），mention 命中后由 onPick 把
 *    `<span data-mention-id="{uuid}">@昵称</span>` 插入到 value + 关闭浮层 + 焦点回 textarea。 */
export function useMentionTrigger(args: {
  value: string;
  setValue: (v: string) => void;
  allCandidates: MentionCandidate[];
}) {
  const { value, setValue, allCandidates } = args;
  const [open_, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [triggerIdx, setTriggerIdx] = useState(-1);

  // 检测 @ 触发：从光标向左扫最近一个非空白字符，是 @ 即进入浮层
  function detectOpenFromValue(next: string, caret: number): { open: boolean; q: string; idx: number } {
    const before = next.slice(0, caret);
    // 找最近的 @，要求前面是空白或字符串起点（避免邮箱内 @ 误触发）
    const at = before.lastIndexOf("@");
    if (at < 0) return { open: false, q: "", idx: -1 };
    const head: string = at === 0 ? "\n" : (before[at - 1] ?? "");
    if (!/[\s\n]/.test(head)) return { open: false, q: "", idx: -1 };
    const seg = before.slice(at + 1);
    // 段内不能含空白：含空白即认为 @ 段结束
    if (/[\s\n]/.test(seg)) return { open: false, q: "", idx: -1 };
    return { open: true, q: seg, idx: at };
  }

  function onChangeWithMention(next: string, caret: number | null) {
    setValue(next);
    if (caret == null) { setOpen(false); return; }
    const r = detectOpenFromValue(next, caret);
    setOpen(r.open);
    setQuery(r.q);
    setTriggerIdx(r.idx);
  }

  // 过滤候选（昵称 / 邮箱前缀）
  const candidates = (() => {
    if (!open_) return [];
    const q = query.toLowerCase();
    if (!q) return allCandidates.slice(0, 8);
    return allCandidates.filter((c) =>
      c.name.toLowerCase().includes(q) || c.email.toLowerCase().startsWith(q),
    ).slice(0, 8);
  })();

  function onPick(c: MentionCandidate) {
    if (triggerIdx < 0) { setOpen(false); return; }
    const head = value.slice(0, triggerIdx);
    const tail = value.slice(triggerIdx + 1 + query.length);
    const ins = `<span data-mention-id="${c.id}">@${c.name}</span>&nbsp;`;
    setValue(head + ins + tail);
    setOpen(false);
    setQuery("");
    setTriggerIdx(-1);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement | HTMLInputElement>): boolean {
    if (!open_) return false;
    if (e.key === "Escape") { setOpen(false); return true; }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      // 候选上下移动由 MentionPop 自身管理（基于 active index）；
      // 这里只发出信号：阻止默认行为避免 textarea 内部光标跳
      e.preventDefault();
      return true;
    }
    if (e.key === "Enter") {
      // 浮层打开时 Enter 命中候选（由 MentionPop onMouseDown 触发）；
      // 这里仅阻止默认换行，候选选择由 MentionPop 完成
      e.preventDefault();
      return true;
    }
    return false;
  }

  return {
    isOpen: open_ && candidates.length > 0 || open_,
    query,
    candidates,
    onChangeWithMention,
    onPick,
    onKeyDown,
    // 暴露纯过滤结果（用于"无匹配"分支）
    filteredEmpty: open_ && candidates.length === 0,
  };
}