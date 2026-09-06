/**
 * 项目动态流页（COLLAB-003 §3.1，冻结原型 V-ACT 表面 21/22/26）。
 *
 *  - 单列信息流：三态行 + 今天/昨天/M月d日 sticky 分区头（StreamList，与任务时间线
 *    C.61 同款口径的独立组件——未改动 Sprint-2 冻结表面）；
 *  - 过滤条：「所有人」×「全部类型」组合 AND，URL 同源（?actor=&event=，§3.3）；
 *  - [加载更早的动态] 按钮式分页（组感知游标 next_cursor；到底提示「没有更早了」）；
 *  - 60s SWR revalidate（页面可见时）+ activity.created 推送增量锚（stream_cursor）：
 *    新条 ≤5 且在顶 → 顶部划入；否则「N 条新动态 ↑」浮条（aria-live=polite）；
 *  - 批量汇总行 → BatchDetailDrawer（?epoch= 轻量拉取）；
 *  - 任务 chip 软删置灰不可点 + Toast「该任务已删除」（BR-06）；归档可跳转（BR-07）；
 *  - view-bar 右端 presence + 连接指示（O1 消费位）+ 降级横幅（BR-10）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router";
import { liveEventBus } from "@rp/shared-state";
import type { ActivityCreatedPayload, LiveEnvelope } from "@rp/types";
import { Topbar } from "../components/Topbar";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { IssueDrawer as SharedDrawer } from "../components/IssueDrawer";
import { StreamList, rowKeyOf } from "../components/activity/StreamList";
import { BatchDetailDrawer } from "../components/activity/BatchDetailDrawer";
import {
  ActivityStreamAPI,
  ProjectAPI,
  ProjectMemberAPI,
  STREAM_EVENT_GROUPS,
  parseStreamCursor,
  unwrap,
  type BatchStreamRow,
  type StreamIssueRef,
  type StreamMeta,
  type StreamRow,
} from "../services/api";
import { DegradedBanner, PresenceBar } from "../realtime/PresenceBar";
import { LIVE_RECONNECTED_EVENT } from "../realtime/RealtimeProvider";
import { toast } from "../components/Toast";

const PAGE_CSS = `
@keyframes rp-slidein{from{transform:translateY(-6px);opacity:.35}to{transform:translateY(0);opacity:1}}
.rp-stream-fresh{animation:rp-slidein .3s ease}
@keyframes rp-pop{from{opacity:0;transform:scale(.96)}to{opacity:1;transform:scale(1)}}
.rp-newdyn-bar{animation:rp-pop .2s ease-out}
`;

/** 新条到达策略阈值（§3.3：≤5 条顶部划入；>5 浮条）。 */
const FRESH_SLIDE_LIMIT = 5;
/** 60s revalidate（§3.1 自动刷新；页面可见时）。 */
const REFRESH_MS = 60_000;

export default function ActivityStreamPage() {
  const { workspaceSlug, projectId } = useParams<{ workspaceSlug: string; projectId: string }>();
  const [sp, setSp] = useSearchParams();
  const actorFilter = sp.get("actor") ?? "";
  const eventFilter = sp.get("event") ?? "";

  const [projName, setProjName] = useState("…");
  const [projIdentifier, setProjIdentifier] = useState("");
  const [members, setMembers] = useState<Array<{ id: string; name: string }>>([]);
  const [rows, setRows] = useState<StreamRow[]>([]);
  const [meta, setMeta] = useState<StreamMeta | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [batchDrawer, setBatchDrawer] = useState<BatchStreamRow | null>(null);
  /** 新动态浮条（>5 条或页面不在顶，§3.3）。 */
  const [pendingNew, setPendingNew] = useState(0);
  const [freshIds, setFreshIds] = useState<Set<string>>(new Set());
  const [now, setNow] = useState(() => new Date());
  const streamCursorRef = useRef<string | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // ── 首屏 + 过滤变化（URL 同源 → 重新拉首页）──
  const filterKey = `${actorFilter}|${eventFilter}`;
  useEffect(() => {
    if (!workspaceSlug || !projectId) return;
    let cancel = false;
    setLoading(true);
    setErr(null);
    setPendingNew(0);
    ActivityStreamAPI.stream(workspaceSlug, projectId, {
      ...(actorFilter ? { actor_id: actorFilter } : {}),
      ...(eventFilter ? { event: eventFilter } : {}),
      per_page: 30,
    })
      .then((r) => {
        if (cancel) return;
        setRows(unwrap<StreamRow[]>(r) ?? []);
        const m = (r as unknown as { meta: StreamMeta }).meta;
        setMeta(m ?? null);
        streamCursorRef.current = m?.stream_cursor ?? null;
      })
      .catch((e: unknown) => {
        if (!cancel) setErr(e instanceof Error ? e.message : "动态加载失败");
      })
      .finally(() => { if (!cancel) setLoading(false); });
    return () => { cancel = true; };
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceSlug, projectId, filterKey]);

  // 项目名 + 成员目录（过滤条下拉 / presence 水合）
  useEffect(() => {
    if (!workspaceSlug || !projectId) return;
    ProjectAPI.detail(workspaceSlug, projectId)
      .then((r) => {
        const d = (r as unknown as { data: { name?: string; identifier?: string } }).data;
        setProjName(d?.name ?? "…");
        setProjIdentifier(d?.identifier ?? "");
      })
      .catch(() => {});
    ProjectMemberAPI.list(workspaceSlug, projectId, { per_page: 100 })
      .then((r) => setMembers((unwrap<Array<{ user: { id: string; display_name: string } }>>(r) ?? [])
        .map((m) => ({ id: m.user.id, name: m.user.display_name }))))
      .catch(() => {});
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceSlug, projectId]);

  /** 增量拉取（activity.created 推送 / 60s 轮询 / 重连补偿共用；幂等）。 */
  const rowsRef = useRef<StreamRow[]>([]);
  rowsRef.current = rows;
  const loadingRef = useRef(false);
  loadingRef.current = loading;
  const incrementalFetch = useCallback(async () => {
    if (!workspaceSlug || !projectId || loadingRef.current) return;
    try {
      const r = await ActivityStreamAPI.stream(workspaceSlug, projectId, {
        ...(actorFilter ? { actor_id: actorFilter } : {}),
        ...(eventFilter ? { event: eventFilter } : {}),
        per_page: 30,
      });
      const fresh = unwrap<StreamRow[]>(r) ?? [];
      const m = (r as unknown as { meta: StreamMeta }).meta;
      const cur = parseStreamCursor(streamCursorRef.current ?? "");
      // 新行 = 比水位更新且不在当前列表（推送只负责「知道」，拉取负责最终一致）
      const existing = new Set(rowsRef.current.map(rowKeyOf));
      const news = fresh.filter((x) => {
        if (existing.has(rowKeyOf(x))) return false;
        if (!cur) return true;
        return x.created_at >= cur.createdAt; // 同秒并列行由 existing 集去重兜底
      });
      if (m?.stream_cursor) streamCursorRef.current = m.stream_cursor;
      if (news.length === 0) return;
      // §3.3：页面在顶且可见 & ≤5 → 顶部划入；否则浮条（不抢阅读位置）
      const el = scrollRef.current;
      const atTop = !el || el.scrollTop < 120;
      if (atTop && document.visibilityState === "visible" && news.length <= FRESH_SLIDE_LIMIT) {
        setRows((curRows) => {
          const seen = new Set(curRows.map(rowKeyOf));
          const add = news.filter((x) => !seen.has(rowKeyOf(x)));
          return [...add, ...curRows];
        });
        setFreshIds(new Set(news.map(rowKeyOf)));
        setTimeout(() => setFreshIds(new Set()), 800);
      } else {
        setPendingNew((n) => n + news.length);
      }
    } catch { /* 轮询/推送增量失败静默（下一轮兜底） */ }
  }, [workspaceSlug, projectId, actorFilter, eventFilter]);

  // 60s SWR revalidate（页面可见时；不可见暂停，§3.3）
  useEffect(() => {
    const t = setInterval(() => {
      if (document.visibilityState === "visible") void incrementalFetch();
    }, REFRESH_MS);
    return () => clearInterval(t);
  }, [incrementalFetch]);

  // 相对时间每分钟刷新（文案更新无请求，§3.3）
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(t);
  }, []);

  // activity.created 推送 → 增量拉取（COLLAB-004 §3.3：页面在顶可见划入/否则浮条）
  useEffect(() => {
    const onActivity = (env: LiveEnvelope) => {
      const p = env.payload as unknown as ActivityCreatedPayload;
      void p.stream_cursor; // 水位锚：incrementalFetch 以本地 streamCursor 比对
      void incrementalFetch();
    };
    const onReconnected = () => void incrementalFetch();
    const off = liveEventBus.on("activity.created", onActivity);
    window.addEventListener(LIVE_RECONNECTED_EVENT, onReconnected);
    return () => {
      off();
      window.removeEventListener(LIVE_RECONNECTED_EVENT, onReconnected);
    };
  }, [incrementalFetch]);

  /** [加载更早的动态]（按钮式；组感知游标，§3.3）。 */
  async function loadMore() {
    if (!workspaceSlug || !projectId || !meta?.next_cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const r = await ActivityStreamAPI.stream(workspaceSlug, projectId, {
        ...(actorFilter ? { actor_id: actorFilter } : {}),
        ...(eventFilter ? { event: eventFilter } : {}),
        cursor: meta.next_cursor,
        per_page: 30,
      });
      const more = unwrap<StreamRow[]>(r) ?? [];
      setRows((cur) => {
        const seen = new Set(cur.map(rowKeyOf));
        return [...cur, ...more.filter((x) => !seen.has(rowKeyOf(x)))];
      });
      setMeta((r as unknown as { meta: StreamMeta }).meta ?? null);
    } catch (e) {
      toast(e instanceof Error ? e.message : "加载更早失败", "error");
    } finally {
      setLoadingMore(false);
    }
  }

  /** 过滤条选择 → URL 同源（§3.3：过滤态 URL 同步）。 */
  function setFilter(key: "actor" | "event", value: string) {
    setSp((prev) => {
      const n = new URLSearchParams(prev);
      if (value) n.set(key, value); else n.delete(key);
      return n;
    }, { preventScrollReset: true });
  }

  /** 任务 chip → 打开任务详情 Drawer（不离开流页，§3.3）；软删 Toast（BR-06）。 */
  function openIssue(issue: StreamIssueRef) {
    if (issue.is_deleted) {
      toast("该任务已删除", "info");
      return;
    }
    setSp((prev) => {
      const n = new URLSearchParams(prev);
      n.set("peekIssue", issue.id);
      return n;
    }, { preventScrollReset: true });
  }
  const peekIssue = sp.get("peekIssue");
  function closePeek() {
    setSp((prev) => {
      const n = new URLSearchParams(prev);
      n.delete("peekIssue");
      return n;
    }, { preventScrollReset: true });
  }

  const filtered = Boolean(actorFilter || eventFilter);
  const actorName = members.find((m) => m.id === actorFilter)?.name;
  const eventLabel = STREAM_EVENT_GROUPS.find((g) => g.key === eventFilter)?.label;
  const hasMore = meta?.next_page_results ?? false;

  const filterMenu = useMemo(() => ({
    actor: [
      { key: "", label: "所有人" },
      ...members.map((m) => ({ key: m.id, label: m.name })),
    ],
    event: [{ key: "", label: "全部类型" }, ...STREAM_EVENT_GROUPS.map((g) => ({ key: g.key, label: g.label }))],
  }), [members]);

  const [menuOpen, setMenuOpen] = useState<"actor" | "event" | null>(null);
  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (!t?.closest('[data-sb-scope="stream-filter-dd"]')) setMenuOpen(null);
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [menuOpen]);

  return (
    <div className="flex flex-col h-screen">
      <style>{PAGE_CSS}</style>
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <ProjectSidebar projectName={projName} identifier={projIdentifier} />
        <main className="flex-1 min-w-0 flex flex-col">
          {/* view-bar：动态 · 项目名 + presence + 连接指示（原型 renderViewBar 同款） */}
          <div className="h-[56px] border-b border-neutral-200 flex items-center gap-2.5 px-5 bg-white shrink-0" data-sb-scope="activity-viewbar">
            <span className="text-[15px] font-semibold">动态</span>
            <span className="text-[12px] text-neutral-500">{projIdentifier}</span>
            <span className="ml-auto"><PresenceBar projectId={projectId} members={members} /></span>
          </div>
          <DegradedBanner />
          {/* 过滤条（§3.1：所有人 × 全部类型 组合 AND；URL 同源） */}
          <div className="flex items-center gap-2 px-5 py-2.5 border-b border-neutral-200 shrink-0" data-sb-scope="stream-filters">
            {(["actor", "event"] as const).map((k) => (
              <span key={k} className="relative" data-sb-scope="stream-filter-dd">
                <button type="button" aria-haspopup="menu" aria-expanded={menuOpen === k}
                  data-sb-scope={`stream-filter-${k}`}
                  onClick={() => setMenuOpen(menuOpen === k ? null : k)}
                  className={`h-8 px-2.5 inline-flex items-center gap-1.5 border rounded-md text-[13px] ${(k === "actor" ? actorFilter : eventFilter) ? "border-brand-300 text-brand-700 bg-brand-50" : "border-neutral-300 text-neutral-700 hover:bg-neutral-50"}`}>
                  {k === "actor" ? "👤" : "☰"} {k === "actor" ? (actorName ?? "所有人") : (eventLabel ?? "全部类型")} ▾
                </button>
                {menuOpen === k && (
                  <div role="menu" className="absolute left-0 top-[36px] z-40 min-w-[150px] max-h-[320px] overflow-y-auto bg-white border border-neutral-200 rounded-lg shadow-lg py-1">
                    {filterMenu[k].map((it) => (
                      <button key={it.key || "__all__"} type="button" role="menuitem"
                        data-sb-scope={`stream-filter-${k}-item`} data-value={it.key}
                        onClick={() => { setMenuOpen(null); setFilter(k, it.key); }}
                        className={`w-full text-left px-3 h-8 text-[13px] ${((k === "actor" ? actorFilter : eventFilter) === it.key) ? "text-brand-600 font-medium bg-brand-50" : "text-neutral-700 hover:bg-neutral-50"}`}>
                        {it.label}
                      </button>
                    ))}
                  </div>
                )}
              </span>
            ))}
            <span className="ml-auto text-[12px] text-neutral-400">60s 自动刷新 · 页面可见时</span>
          </div>
          {/* 单列信息流（§3.1：56px 头像列 + 内容列 + 任务 chip） */}
          <div className="flex-1 overflow-y-auto px-5 pb-10" ref={scrollRef} data-sb-scope="streamwrap">
            <div className="max-w-[860px] w-full mx-auto">
              {loading ? (
                <ul className="flex flex-col gap-3 pt-4" aria-busy="true" data-sb-scope="stream-skel">
                  {Array.from({ length: 6 }).map((_, i) => <li key={i} className="h-14 rounded-md bg-neutral-100 animate-pulse" />)}
                </ul>
              ) : err ? (
                <div className="flex flex-col items-center gap-2 py-16 text-neutral-500" role="alert" data-sb-scope="stream-err">
                  <span className="text-[15px] font-semibold text-neutral-700">{err.includes("404") || err.includes("不存在") ? "项目不可见或已删除" : "动态加载失败"}</span>
                  <div className="text-[13px]">{err}</div>
                  <button type="button" className="mt-1 h-[30px] px-3 border border-neutral-300 rounded-md" data-sb-scope="stream-retry"
                    onClick={() => { setErr(null); setLoading(true); void incrementalFetch(); location.reload(); }}>重试</button>
                </div>
              ) : rows.length === 0 ? (
                <div className="flex flex-col items-center gap-2 py-16 text-neutral-400" data-sb-scope="stream-empty">
                  <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M3 12h4l3-9 4 18 3-9h4"/></svg>
                  <div className="text-[14px] font-medium text-neutral-700">{filtered ? "该过滤条件下暂无动态" : "项目还没有动静"}</div>
                </div>
              ) : (
                <>
                  <StreamList rows={rows} now={now} onOpenIssue={openIssue} onExpandBatch={setBatchDrawer} freshIds={freshIds} />
                  <div className="flex justify-center pt-4 pb-2">
                    {hasMore ? (
                      <button type="button" onClick={() => void loadMore()} disabled={loadingMore} data-sb-scope="stream-more"
                        className="h-9 px-4 border border-neutral-300 rounded-md text-[13px] text-neutral-600 hover:bg-neutral-50 disabled:opacity-50">
                        {loadingMore ? "加载中…" : "加载更早的动态"}
                      </button>
                    ) : (
                      <span className="text-[12.5px] text-neutral-400 py-2" data-sb-scope="stream-end">没有更早了</span>
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
        </main>
      </div>
      {/* 新动态浮条（>5 条或不在顶；点击才真正刷新，§3.3——避免阅读位置跳动） */}
      {pendingNew > 0 && (
        <button type="button" aria-live="polite" data-sb-scope="newdyn-bar"
          onClick={() => {
            setPendingNew(0);
            scrollRef.current?.scrollTo({ top: 0, behavior: "smooth" });
            void incrementalFetch();
            toast("已拉取增量（stream_cursor 水位 · 幂等补偿）", "ok");
          }}
          className="rp-newdyn-bar fixed top-[60px] left-1/2 -translate-x-1/2 z-[65] bg-brand-500 text-white rounded-full px-4 py-1.5 text-[13px] shadow-md inline-flex gap-1.5 items-center">
          ↑ {pendingNew} 条新动态
        </button>
      )}
      {batchDrawer && workspaceSlug && projectId && (
        <BatchDetailDrawer slug={workspaceSlug} projectId={projectId} batch={batchDrawer}
          onClose={() => setBatchDrawer(null)}
          onOpenIssue={(id) => {
            setBatchDrawer(null);
            setSp((prev) => { const n = new URLSearchParams(prev); n.set("peekIssue", id); return n; }, { preventScrollReset: true });
          }} />
      )}
      {peekIssue && workspaceSlug && projectId && (
        <SharedDrawer issueId={peekIssue} slug={workspaceSlug} projectId={projectId} onClose={closePeek} />
      )}
    </div>
  );
}
