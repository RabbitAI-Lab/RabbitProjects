/** 实时层 MobX store（COLLAB-004 §4.4.2 连接状态机 + §3.1 presence）。
 *  增量提示层（MobX patch）与全量收敛层（SWR/轮询 revalidate）分层——本 store
 *  只承载连接/在场状态，不承载业务实体（实体收敛走既有 REST 拉取通道）。 */
import { action, computed, makeObservable, observable } from "mobx";
import type { PresencePayload } from "@rp/types";
import type { LiveEnvelope } from "@rp/types";
import type { LiveEventBus } from "./bus";

/** §3.1 连接指示三态 + idle（未进入项目上下文）/connecting（首连中）。 */
export type RealtimeStatus = "idle" | "connecting" | "connected" | "reconnecting" | "degraded";

/** 连接状态机（§4.4.2：注入 UiStore 驱动顶栏指示与降级横幅 BR-10）。 */
export class RealtimeStore {
  status: RealtimeStatus = "idle";
  /** connected 帧回执的房间集（§2.1）。 */
  rooms: string[] = [];
  /** 应用层 ping/pong 实测 RTT（ms；connected 心跳周期探测）。 */
  latencyMs: number | null = null;
  /** 累计重连次数（详情弹层「重连 N 次」）。 */
  reconnects = 0;
  /** 降级横幅「关闭」记忆（恢复后自动复位，§3.1）。 */
  bannerDismissed = false;
  lastConnectedAt: number | null = null;
  lastError: string | null = null;

  constructor() {
    makeObservable(this, {
      status: observable,
      rooms: observable,
      latencyMs: observable,
      reconnects: observable,
      bannerDismissed: observable,
      lastConnectedAt: observable,
      lastError: observable,
      connected: computed,
      setStatus: action,
      setRooms: action,
      setLatency: action,
      bumpReconnects: action,
      setLastConnectedNow: action,
      setLastError: action,
      dismissBanner: action,
      reset: action,
    });
  }

  get connected(): boolean {
    return this.status === "connected";
  }

  setStatus(s: RealtimeStatus): void {
    this.status = s;
    if (s === "connected") this.bannerDismissed = false; // 恢复自动撤横幅
  }

  setRooms(rooms: string[]): void {
    this.rooms = rooms;
  }

  setLatency(ms: number | null): void {
    this.latencyMs = ms;
  }

  bumpReconnects(): void {
    this.reconnects += 1;
  }

  setLastConnectedNow(): void {
    this.lastConnectedAt = Date.now();
  }

  setLastError(err: string | null): void {
    this.lastError = err;
  }

  dismissBanner(): void {
    this.bannerDismissed = true;
  }

  reset(): void {
    this.status = "idle";
    this.rooms = [];
    this.latencyMs = null;
    this.reconnects = 0;
    this.bannerDismissed = false;
    this.lastError = null;
  }
}

/** presence 条目：joined/left 事件维护（BR-11 仅 project 房间）。 */
export interface PresenceEntry {
  userId: string;
  /** 最近一次 joined（或连接期心跳水位）时刻。 */
  lastSeen: number;
  /** left 时刻；null = 在线（25s 心跳内）。 */
  leftAt: number | null;
}

/** 离线缓存位保留窗口（§3.1：头像灰度保留 5 分钟）。 */
export const PRESENCE_KEEP_MS = 5 * 60_000;

/** presence 表（§3.1 头像列数据源；live 只广播 user.id，display_name 由 app 层目录水合）。 */
export class PresenceStore {
  byRoom = new Map<string, Map<string, PresenceEntry>>();

  constructor() {
    makeObservable(this, {
      byRoom: observable.shallow,
      joined: action,
      left: action,
      prune: action,
      clearRoom: action,
    });
  }

  /** presence.joined / connected 自身影子（去重幂等）。 */
  joined(room: string, userId: string, now = Date.now()): void {
    let m = this.byRoom.get(room);
    if (!m) {
      m = new Map();
      this.byRoom.set(room, m);
    }
    m.set(userId, { userId, lastSeen: now, leftAt: null });
  }

  /** presence.left：置灰不立即移除（5 分钟缓存位）。 */
  left(room: string, userId: string, now = Date.now()): void {
    const m = this.byRoom.get(room);
    const cur = m?.get(userId);
    if (!m || !cur) return;
    m.set(userId, { userId, lastSeen: cur.lastSeen, leftAt: now });
  }

  /** 清理超过缓存窗口的离线条目（页面可见时低频调用即可）。 */
  prune(room: string, now = Date.now(), keepMs = PRESENCE_KEEP_MS): void {
    const m = this.byRoom.get(room);
    if (!m) return;
    for (const [uid, e] of m) {
      if (e.leftAt != null && now - e.leftAt > keepMs) m.delete(uid);
    }
  }

  clearRoom(room: string): void {
    this.byRoom.delete(room);
  }

  /** 快照（在线在前、按 lastSeen 倒序）——头像列渲染输入。 */
  entries(room: string): PresenceEntry[] {
    const m = this.byRoom.get(room);
    if (!m) return [];
    return [...m.values()].sort((a, b) => {
      const ao = a.leftAt == null ? 0 : 1;
      const bo = b.leftAt == null ? 0 : 1;
      if (ao !== bo) return ao - bo;
      return b.lastSeen - a.lastSeen;
    });
  }

  onlineCount(room: string): number {
    return this.entries(room).filter((e) => e.leftAt == null).length;
  }

  isOnline(room: string, userId: string): boolean {
    const e = this.byRoom.get(room)?.get(userId);
    return e != null && e.leftAt == null;
  }

  /** 订阅 bus 的 presence 帧（RealtimeProvider 装配时调用一次）。 */
  bindBus(bus: LiveEventBus): () => void {
    const offJoined = bus.on("presence.joined", (env: LiveEnvelope) => {
      const p = env.payload as unknown as PresencePayload;
      if (p?.room && p?.user?.id) this.joined(p.room, p.user.id);
    });
    const offLeft = bus.on("presence.left", (env: LiveEnvelope) => {
      const p = env.payload as unknown as PresencePayload;
      if (p?.room && p?.user?.id) this.left(p.room, p.user.id);
    });
    return () => {
      offJoined();
      offLeft();
    };
  }
}
