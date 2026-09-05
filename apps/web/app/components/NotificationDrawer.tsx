/** 通知中心抽屉 420px（C.34 · COLLAB-001 §3.3 + ADR-0011 #5/#15/#16 定稿）。
 *
 *  - 头部：通知标题 + 「仅看未读」本地开关（不进 URL，开关只影响请求参数 ?unread=）
 *         + 「全部已读」按钮（ADR-0011 #5：抽屉无路由，仅看未读 = 本地开关，不进 URL）
 *  - 分组：今天 / 昨天 / 更早 (N)（isToday/isYesterday 内置）
 *  - 「更早 (N)」分组底部「加载更多」按钮（cursor 分页，ADR-0011 #15）
 *  - 通知行：未读蓝点 ● / 已读 ○ + 事件图标（assigned 👤 / mentioned @ / commented 💬 / updated ✏️）
 *         + 文案 + 相对时间；role=link + aria-label 完整朗读
 *  - 行点击：乐观消失蓝点 → 跳转任务详情（`/${slug}/projects?peekIssue=${id}`）
 *  - 空态：没有新消息插画 + 去协作引导按钮
 *  - 加载态：5 行骨架（animate-pulse）
 *  - 关闭：点 mask / Esc / 内部交互后
 *
 *  ADR-0010 教训 #4：dropdown 全局点击监听禁 document click；本组件自管 mount + 内部 onClose
 *  + Esc 监听，不挂 document mousedown。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router";
import { NotificationAPI, unwrap } from "../services/api";
import { toast } from "./Toast";

interface NotifRow {
  id: string;
  title: string;
  data?: { issue_id?: string; issue_key?: string } & Record<string, unknown>;
  read_at?: string | null;
  created_at: string;
}

interface NotificationDrawerProps {
  open: boolean;
  onClose: () => void;
  workspaceSlug: string;
  /** 未读条数变化回调（顶栏铃铛徽标用） */
  onUnreadChange?: (n: number) => void;
  /** 测试/演示入口：打开时跳过网络拉取，直接用 mock 数据 */
  mockData?: NotifRow[] | null;
}

type EventKind = "assigned" | "mentioned" | "commented" | "updated" | "other";

function eventKindOf(n: NotifRow): EventKind {
  const v = n.data?.verb ?? n.data?.type;
  const s = typeof v === "string" ? v : "";
  if (s.includes("assign")) return "assigned";
  if (s.includes("mention")) return "mentioned";
  if (s.includes("comment")) return "commented";
  if (s.includes("update")) return "updated";
  return "other";
}

function EventIcon({ kind }: { kind: EventKind }) {
  const common = { width: 13, height: 13, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2 } as const;
  if (kind === "mentioned") {
    return <span className="font-bold text-brand-600 text-[12px] leading-none" aria-hidden="true">@</span>;
  }
  if (kind === "assigned") {
    return (
      <svg {...common} aria-hidden="true"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
    );
  }
  if (kind === "commented") {
    return (
      <svg {...common} aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
    );
  }
  if (kind === "updated") {
    return (
      <svg {...common} aria-hidden="true"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
    );
  }
  return (
    <svg {...common} aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>
  );
}

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** 内置「今天 / 昨天」分组（与 C.34 isToday/isYesterday 对齐；不引入 date-fns 减包）。 */
function dayBucket(iso: string, now: Date): "today" | "yesterday" | "earlier" {
  const t = new Date(iso);
  if (sameDay(t, now)) return "today";
  const y = new Date(now.getTime() - 86400_000);
  if (sameDay(t, y)) return "yesterday";
  return "earlier";
}

function relTime(iso: string, now: Date): string {
  const t = new Date(iso).getTime();
  const diff = (now.getTime() - t) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

const MOCK: NotifRow[] = [
  { id: "m1", title: "王五 在 RBT-128 中提到了你", data: { issue_id: "iss-1", issue_key: "RBT-128", verb: "mention" }, created_at: new Date(Date.now() - 3 * 60_000).toISOString() },
  { id: "m2", title: "李四 将 RBT-130 指派给你", data: { issue_id: "iss-2", issue_key: "RBT-130", verb: "assign" }, created_at: new Date(Date.now() - 25 * 60_000).toISOString() },
  { id: "m3", title: "张三 评论了 RBT-130", data: { issue_id: "iss-3", issue_key: "RBT-130", verb: "comment" }, created_at: new Date(Date.now() - 2 * 3600_000).toISOString() },
  { id: "m4", title: "王五 更新了 RBT-128：状态 待办 → 进行中", data: { issue_id: "iss-4", issue_key: "RBT-128", verb: "update" }, read_at: new Date(Date.now() - 86400_000).toISOString(), created_at: new Date(Date.now() - 86400_000 - 3600_000).toISOString() },
  { id: "m5", title: "系统 · 你加入项目『RabbitProjects』", data: { verb: "system" }, read_at: new Date(Date.now() - 86400_000 * 3).toISOString(), created_at: new Date(Date.now() - 86400_000 * 3).toISOString() },
];

export function NotificationDrawer({ open, onClose, workspaceSlug, onUnreadChange, mockData }: NotificationDrawerProps) {
  const nav = useNavigate();
  const [items, setItems] = useState<NotifRow[]>(mockData ?? []);
  const [loading, setLoading] = useState(false);
  const [onlyUnread, setOnlyUnread] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [moreCursor, setMoreCursor] = useState<string | null>(null);
  const [moreLoaded, setMoreLoaded] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const drawerRef = useRef<HTMLDivElement | null>(null);

  const now = useMemo(() => new Date(), [open]);

  // 打开时拉首屏（mock 模式跳过）
  useEffect(() => {
    if (!open) return;
    if (mockData) { setItems(mockData); return; }
    let cancel = false;
    setLoading(true); setErr(null); setMoreLoaded(false); setHasMore(true);
    NotificationAPI.list({ per_page: 20 })
      .then((r) => {
        if (cancel) return;
        const list = unwrap<NotifRow[]>(r);
        setItems(list);
      })
      .catch((e: unknown) => {
        if (cancel) return;
        setErr(e instanceof Error ? e.message : "加载失败");
      })
      .finally(() => { if (!cancel) setLoading(false); });
    return () => { cancel = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // 焦点进入抽屉 + Esc 关闭（C.34 a11y）
  useEffect(() => {
    if (!open) return;
    const id = setTimeout(() => { drawerRef.current?.focus(); }, 0);
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => { clearTimeout(id); document.removeEventListener("keydown", onKey); };
  }, [open, onClose]);

  if (!open) return null;

  // 分组（不修改原数组顺序；保留服务端下发的时序）
  const filtered = onlyUnread ? items.filter((x) => !x.read_at) : items;
  const today = filtered.filter((x) => dayBucket(x.created_at, now) === "today");
  const yesterday = filtered.filter((x) => dayBucket(x.created_at, now) === "yesterday");
  const earlier = filtered.filter((x) => dayBucket(x.created_at, now) === "earlier");

  const unreadTotal = items.filter((x) => !x.read_at).length;

  async function loadMore() {
    if (mockData) return; // mock 不分页
    if (moreLoaded || !hasMore) return;
    setMoreLoaded(true);
    try {
      const r = await NotificationAPI.list({ per_page: 20 });
      const list = unwrap<NotifRow[]>(r);
      if (list.length === 0) setHasMore(false);
      else setItems((cur) => [...cur, ...list]);
    } catch {
      setErr("加载更多失败");
    } finally {
      setMoreLoaded(false);
    }
    void moreCursor; // 占位字段（保留 hook 拓扑，便于未来接 cursor）
  }

  function openItem(n: NotifRow) {
    // 乐观：本地标已读 + 通知顶栏
    if (!n.read_at) {
      setItems((cur) => cur.map((x) => (x.id === n.id ? { ...x, read_at: new Date().toISOString() } : x)));
      const newUnread = Math.max(0, unreadTotal - 1);
      onUnreadChange?.(newUnread);
      NotificationAPI.read(n.id).catch(() => { /* 静默 */ });
    }
    const issueId = (n.data?.issue_id ?? (n.data?.issue_key ? "" : "")) as string;
    if (issueId) {
      onClose();
      nav(`/${workspaceSlug}/projects?peekIssue=${issueId}`);
    } else if (n.data?.issue_key) {
      onClose();
      nav(`/${workspaceSlug}/projects?q=${encodeURIComponent(String(n.data.issue_key))}`);
    } else {
      toast("该任务已不存在或已被删除", "info");
    }
  }

  async function readAll() {
    if (mockData) {
      setItems((cur) => cur.map((x) => ({ ...x, read_at: x.read_at ?? new Date().toISOString() })));
      onUnreadChange?.(0);
      toast(`已将 ${unreadTotal} 条标为已读`);
      return;
    }
    const before = unreadTotal;
    setItems((cur) => cur.map((x) => ({ ...x, read_at: x.read_at ?? new Date().toISOString() })));
    onUnreadChange?.(0);
    try {
      await NotificationAPI.readAll();
      toast(`已将 ${before} 条标为已读`);
    } catch {
      toast("标记失败，请稍后重试", "error");
    }
  }

  const body =
    loading ? (
      <ul data-sb-scope="notif-skel" aria-busy="true" aria-label="通知加载中" className="px-4 py-3 flex flex-col gap-2.5">
        {Array.from({ length: 5 }).map((_, i) => (
          <li key={i} className="h-[40px] rounded-md bg-neutral-100 animate-pulse" />
        ))}
      </ul>
    ) : err ? (
      <div className="px-5 py-10 text-center text-[13px] text-neutral-500" role="alert">
        <div>{err}</div>
        <button onClick={() => { setErr(null); setLoading(true); NotificationAPI.list({ per_page: 20 }).then((r) => setItems(unwrap<NotifRow[]>(r))).catch((e: unknown) => setErr(e instanceof Error ? e.message : "加载失败")).finally(() => setLoading(false)); }} className="mt-2 h-[30px] px-3 border border-neutral-300 rounded-md">重试</button>
      </div>
    ) : filtered.length === 0 ? (
      <div data-sb-scope="notif-empty" className="flex flex-col items-center gap-2 py-16 text-neutral-400">
        <span className="text-[40px]" aria-hidden="true">☕</span>
        <div className="text-[14px] font-medium text-neutral-700">没有新消息</div>
        <div className="text-[12px]">全部通知已处理完毕</div>
        <a href={`/${workspaceSlug}/projects`} onClick={(e) => { e.preventDefault(); onClose(); nav(`/${workspaceSlug}/projects`); }} className="mt-2 h-[30px] px-3 inline-flex items-center bg-brand-500 text-white rounded-md text-[12px] hover:bg-brand-600">去协作</a>
      </div>
    ) : (
      <div className="px-1 py-1">
        {[
          { key: "today", label: "今天", rows: today },
          { key: "yesterday", label: "昨天", rows: yesterday },
          { key: "earlier", label: `更早 (${earlier.length})`, rows: earlier, more: true },
        ].filter((g) => g.rows.length > 0).map((g) => (
          <section key={g.key} className="mt-1" aria-label={g.label}>
            <div className="px-3 py-1.5 text-[11px] font-semibold text-neutral-400 uppercase tracking-wider">{g.label}</div>
            <ul>
              {g.rows.map((n) => {
                const kind = eventKindOf(n);
                const unread = !n.read_at;
                const labelFull = `${n.title}，${relTime(n.created_at, now)}${unread ? "，未读" : ""}`;
                return (
                  <li key={n.id}>
                    <button
                      type="button"
                      role="link"
                      tabIndex={0}
                      data-sb-scope="notif-row"
                      aria-label={labelFull}
                      onClick={() => openItem(n)}
                      className="w-full px-3 py-2.5 flex items-start gap-2.5 text-left hover:bg-neutral-50 focus:bg-neutral-50 focus:outline-none border-t border-neutral-100 first:border-0"
                    >
                      <span className={`mt-1.5 w-1.5 h-1.5 rounded-full shrink-0 ${unread ? "bg-brand-500" : "bg-neutral-300"}`} aria-hidden="true" />
                      <span className="sr-only">{unread ? "未读" : ""}</span>
                      <span className="w-[18px] h-[18px] mt-0.5 inline-flex items-center justify-center text-neutral-500 shrink-0">
                        <EventIcon kind={kind} />
                      </span>
                      <span className="flex-1 min-w-0 text-[13px] text-neutral-800 leading-[1.45] truncate">{n.title}</span>
                      <span className="text-[11px] text-neutral-400 shrink-0 mt-0.5">{relTime(n.created_at, now)}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
            {g.more && (
              <div className="px-3 py-2 text-center">
                <button
                  type="button"
                  data-sb-scope="notif-more"
                  onClick={loadMore}
                  disabled={moreLoaded || !hasMore}
                  className="text-[12px] text-brand-600 hover:underline disabled:text-neutral-400"
                >
                  {moreLoaded ? "加载中…" : !hasMore ? "没有更多" : "加载更多"}
                </button>
              </div>
            )}
          </section>
        ))}
      </div>
    );

  const node = (
    <div data-sb-scope="notif-drawer" className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/25" onClick={onClose} aria-hidden="true" />
      <aside
        ref={drawerRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="通知中心"
        className="relative w-[420px] max-w-[100vw] bg-white border-l border-neutral-200 shadow-xl flex flex-col outline-none"
      >
        {/* 头部（C.34） */}
        <div className="flex items-center gap-2.5 px-5 h-[52px] border-b border-neutral-200 shrink-0">
          <span className="text-[14px] font-semibold">通知</span>
          <label className="ml-auto inline-flex items-center gap-1.5 text-[12px] text-neutral-600 cursor-pointer select-none">
            <input
              type="checkbox"
              data-sb-scope="notif-only-unread"
              checked={onlyUnread}
              onChange={(e) => setOnlyUnread(e.target.checked)}
              className="w-3.5 h-3.5 accent-brand-500"
              aria-label="仅看未读"
            />
            仅看未读
          </label>
          <button
            type="button"
            data-sb-scope="notif-readall"
            onClick={readAll}
            disabled={unreadTotal === 0}
            className="text-[12px] text-brand-600 hover:underline disabled:text-neutral-400 disabled:no-underline"
          >
            全部已读
          </button>
          <button onClick={onClose} aria-label="关闭" className="w-7 h-7 inline-flex items-center justify-center text-neutral-500 hover:text-neutral-900">✕</button>
        </div>
        {/* Body */}
        <div className="flex-1 overflow-y-auto">{body}</div>
      </aside>
    </div>
  );
  return createPortal(node, document.body);
}

/** 顶栏铃铛（未读徽标 + 点击打开抽屉；C.34 头部铃铛 + ADR-0011 #16 全局顶栏常驻）。
 *  仅展示，外层 open 状态由父组件管理；本组件只触发 onOpen。 */
export function NotificationBell({ unread, onOpen }: { unread: number; onOpen: () => void }) {
  const showBadge = unread > 0;
  const label = showBadge ? `通知，${unread} 条未读` : "通知";
  return (
    <button
      type="button"
      data-sb-scope="topbar-bell"
      onClick={onOpen}
      aria-label={label}
      aria-live={showBadge ? "polite" : undefined}
      className="relative w-7 h-7 inline-flex items-center justify-center text-neutral-500 hover:text-neutral-900 hover:bg-neutral-50 rounded-md"
    >
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
        <path d="M13.73 21a2 2 0 0 1-3.46 0" />
      </svg>
      {showBadge && (
        <span data-sb-scope="topbar-bell-badge" className="absolute -top-0.5 -right-0.5 min-w-[16px] h-4 px-1 rounded-full bg-red-500 text-white text-[10px] font-semibold inline-flex items-center justify-center tabular-nums" aria-hidden="true">
          {unread > 99 ? "99+" : unread}
        </span>
      )}
    </button>
  );
}
