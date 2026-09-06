/**
 * presence 头像列 + 连接指示 + 降级横幅（COLLAB-004 §3.1，冻结原型 O1 消费位
 * = 项目视图工具条右端；动态流页 view-bar 右端同款复用）。
 *
 *  - 头像列：presence.joined/left 维护的在线集（≤7 +「+N」灰度缓存位）；hover 成员卡
 *    「正在看板」；自己的头像在已连接时合成进列（live 对 presence 帧不回显本人——
 *    BR-08，本地补影子）；
 *  - 连接三态圆点（绿已连/黄重连中 pulse/灰已降级）+ 点击详情弹层（状态/房间/延迟/
 *    重连次数/重连按钮）；
 *  - 降级横幅：degraded 且未关闭时常驻黄条「实时同步暂停 · 已切换为定时刷新」+
 *    立即重连；恢复自动撤除（RealtimeStore.connected setter 复位 bannerDismissed）。
 */
import { useEffect, useRef, useState } from "react";
import { observer } from "mobx-react-lite";
import { liveRoom } from "@rp/types";
import { liveEventBus } from "@rp/shared-state";
import { useStores } from "../stores";
import { useRealtimeClient } from "./RealtimeProvider";

const AVATAR_COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899", "#14b8a6", "#f97316"];
const hashColor = (id: string) => AVATAR_COLORS[[...id].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 0) % AVATAR_COLORS.length];

const PRESENCE_CSS = `
@keyframes rp-conn-pulse{0%,100%{opacity:1}50%{opacity:.45}}
.rp-conn-dot.warn{animation:rp-conn-pulse 1.2s infinite}
@keyframes rp-pop{from{opacity:0;transform:scale(.96)}to{opacity:1;transform:scale(1)}}
.rp-pop{animation:rp-pop .12s ease-out}
`;

/** §3.1 头像列（≤7 + +N；绿点=在线 / 灰度=离线 5 分钟缓存位）。 */
export const PresenceCluster = observer(function PresenceCluster({ projectId, members }: {
  projectId: string | undefined;
  /** 成员目录（id → display_name 水合；live 广播仅携带 user.id）。 */
  members: Array<{ id: string; name: string }>;
}) {
  const { presence, realtime, session } = useStores();
  const [hover, setHover] = useState<string | null>(null);
  const room = projectId ? liveRoom.project(projectId) : "";
  const nameOf = (uid: string) => members.find((m) => m.id === uid)?.name ?? "成员";

  useEffect(() => {
    if (!room) return;
    const t = setInterval(() => presence.prune(room), 30_000); // 5 分钟缓存位清理
    return () => clearInterval(t);
  }, [room, presence]);

  const entries = room ? presence.entries(room) : [];
  const meId = session.user?.id ?? null;
  // 本地影子：自己已连接项目房间 → 合成为在线位（presence 帧不回显本人，BR-08）
  const withMe = meId && realtime.connected && !entries.some((e) => e.userId === meId)
    ? [{ userId: meId, lastSeen: Date.now(), leftAt: null }, ...entries]
    : entries;
  const online = withMe.filter((e) => e.leftAt == null);
  const offline = withMe.filter((e) => e.leftAt != null);
  const show = online.slice(0, 7);

  return (
    <span className="relative inline-flex items-center" data-sb-scope="presence-cluster" role="status"
      aria-label={`在线成员 ${online.length} 人`}>
      <style>{PRESENCE_CSS}</style>
      {show.map((e) => (
        <span key={e.userId} className="relative -ml-1.5 first:ml-0 rounded-full ring-2 ring-white"
          onMouseEnter={() => setHover(e.userId)} onMouseLeave={() => setHover(null)}>
          <span className="w-7 h-7 rounded-full text-white text-[11px] font-semibold inline-flex items-center justify-center"
            style={{ background: hashColor(e.userId) }} aria-hidden="true">{nameOf(e.userId).slice(0, 1)}</span>
          <span className="absolute -right-0.5 -bottom-0.5 w-[9px] h-[9px] rounded-full bg-emerald-500 ring-2 ring-white" aria-hidden="true" />
        </span>
      ))}
      {offline.length > 0 && (
        <span className="-ml-1 w-[26px] h-[26px] rounded-full bg-neutral-200 text-neutral-500 text-[10px] inline-flex items-center justify-center ring-2 ring-white"
          title={`${offline.length} 人近期在线`} data-sb-scope="presence-more">+{offline.length}</span>
      )}
      {hover && (() => {
        const e = withMe.find((x) => x.userId === hover);
        if (!e) return null;
        const onlineNow = e.leftAt == null;
        return (
          <div className="rp-pop absolute right-0 top-[34px] z-[97] bg-white border border-neutral-200 rounded-[10px] shadow-lg px-3.5 py-2.5 min-w-[190px] text-[12.5px]" role="tooltip"
            data-sb-scope="presence-member-card">
            <div className="flex items-center gap-2">
              <span className="w-7 h-7 rounded-full text-white text-[11px] font-semibold inline-flex items-center justify-center" style={{ background: hashColor(e.userId) }}>{nameOf(e.userId).slice(0, 1)}</span>
              <div>
                <div className="font-semibold text-neutral-900">{nameOf(e.userId)}{e.userId === meId ? "（你）" : ""}</div>
                <div className="text-neutral-400">{onlineNow ? "在线 · 正在看板" : "离线（5 分钟缓存位）"}</div>
              </div>
            </div>
            <div className="mt-1.5 text-neutral-400 flex items-center gap-1.5">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
              正在看板{onlineNow ? "（presence.joined · 25s 心跳内）" : ""}
            </div>
            <div className="mt-1 text-neutral-400">项目房间 {online.length} 人在线</div>
          </div>
        );
      })()}
    </span>
  );
});

const STATUS_TEXT: Record<string, { label: string; full: string; dot: string }> = {
  connected: { label: "", full: "已连接 · 实时同步中", dot: "bg-emerald-500" },
  connecting: { label: "连接中", full: "正在连接实时通道", dot: "bg-neutral-400" },
  reconnecting: { label: "重连中", full: "正在重连（指数退避 1s→30s 封顶）", dot: "bg-amber-500 rp-conn-dot warn" },
  degraded: { label: "已降级", full: "已降级为定时刷新（30s 轮询兜底）", dot: "bg-neutral-400" },
  idle: { label: "", full: "未进入项目上下文", dot: "bg-neutral-300" },
};

/** §3.1 连接指示（三态圆点 + 文案；点击弹连接详情）。 */
export const ConnectionIndicator = observer(function ConnectionIndicator() {
  const { realtime } = useStores();
  const client = useRealtimeClient();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement | null>(null);
  const s = STATUS_TEXT[realtime.status] ?? STATUS_TEXT.idle!;

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (!t?.closest('[data-sb-scope="conn-indicator"]')) setOpen(false);
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [open]);

  return (
    <span className="relative" ref={wrapRef} data-sb-scope="conn-indicator">
      <button type="button" role="status" data-sb-scope="conn-dot-btn"
        aria-label={`实时${s.label || "已连接"}`}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 text-[12px] text-neutral-500 h-7 px-2 rounded-full border border-neutral-200 hover:bg-neutral-50">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M5 13a10 10 0 0 1 14 0"/><path d="M8.5 16.5a5 5 0 0 1 7 0"/><path d="M2 8.82a15 15 0 0 1 20 0"/><line x1="12" x2="12.01" y1="20" y2="20"/></svg>
        <span className={`w-2 h-2 rounded-full shrink-0 ${s.dot}`} aria-hidden="true" />
        {s.label ? <span>{s.label}</span> : null}
      </button>
      {open && (
        <div className="rp-pop absolute right-0 top-[34px] z-[97] bg-white border border-neutral-200 rounded-[10px] shadow-lg px-3.5 py-3 text-[12.5px] min-w-[230px]" role="dialog" aria-label="实时连接详情"
          data-sb-scope="conn-pop">
          <div className="flex justify-between gap-3 text-neutral-700 py-0.5"><span>状态</span><b className="font-semibold text-neutral-900">{s.label || "已连接"}</b></div>
          <div className="flex justify-between gap-3 text-neutral-700 py-0.5"><span>房间</span><b className="font-semibold text-neutral-900 font-mono text-[11px]">{realtime.rooms.length ? realtime.rooms.join(" ") : "—"}</b></div>
          <div className="flex justify-between gap-3 text-neutral-700 py-0.5"><span>延迟</span><b className="font-semibold text-neutral-900">{realtime.latencyMs != null ? `${realtime.latencyMs} ms` : "—"}</b></div>
          <div className="flex justify-between gap-3 text-neutral-700 py-0.5"><span>重连</span><b className="font-semibold text-neutral-900">{realtime.reconnects} 次</b></div>
          <div className="flex gap-2 mt-2">
            <button type="button" data-sb-scope="conn-reconnect-btn"
              onClick={() => client?.reconnectNow()}
              className="h-7 px-2.5 border border-neutral-300 rounded-md text-[12px] hover:bg-neutral-50">重连</button>
          </div>
          <div className="mt-1.5 text-[11px] text-neutral-400">{s.full}</div>
        </div>
      )}
    </span>
  );
});

/** §3.1 降级横幅（BR-10：连接失败累计 30s → 轮询模式 + 黄条；恢复自动撤除）。 */
export const DegradedBanner = observer(function DegradedBanner() {
  const { realtime } = useStores();
  const client = useRealtimeClient();
  if (realtime.status !== "degraded" || realtime.bannerDismissed) return null;
  return (
    <div role="status" data-sb-scope="rt-degraded-banner"
      className="flex items-center gap-2 bg-amber-50 border-b border-amber-200 text-amber-800 px-5 py-[7px] text-[12.5px] w-full shrink-0">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4M12 17h.01"/></svg>
      实时同步暂停 · 已切换为定时刷新
      <button type="button" className="ml-auto text-amber-800 underline underline-offset-2 hover:text-amber-900" data-sb-scope="rt-banner-reconnect"
        onClick={() => client?.reconnectNow()}>立即重连</button>
      <button type="button" className="text-amber-800 underline underline-offset-2 hover:text-amber-900" data-sb-scope="rt-banner-dismiss"
        onClick={() => realtime.dismissBanner()}>关闭</button>
    </div>
  );
});

/** 动态流页/看板共用的 view-bar 右端组合（presence + 连接指示，原型 O1）。 */
export const PresenceBar = observer(function PresenceBar({ projectId, members }: {
  projectId: string | undefined;
  members: Array<{ id: string; name: string }>;
}) {
  void liveEventBus; // PresenceCluster 经 store 订阅渲染（bus 绑定在 Provider 层）
  return (
    <span className="inline-flex items-center gap-2.5">
      <PresenceCluster projectId={projectId} members={members} />
      <ConnectionIndicator />
    </span>
  );
});
