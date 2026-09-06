/** 房间模型与连接注册表（COLLAB-004 §1.3 / §4.3.1）。
 *
 *  四类房间：project:{pid} / issue:{iid} / file:{asset_id}（第四类，FILE-003
 *  §4.4 登记、订阅条件在 api 换票时校验——file.read + 文件可见性）/ user:{uid}；
 *  单连接房间 ≤10（票据声明已限）、单房间连接 ≤500（§2.6 分片上限，超容仅
 *  WARN 跳过——单副本部署不达此量级）；每用户全局并发 ≤5（BR-12：第 6 条踢
 *  最旧 4000 DUP_SESSION）；同 ws 标识（{sub}:{client_tab_id}）重连幂等替换
 *  旧连接（不占新额度）。
 *
 *  房间序号 seq：房间级单调递增、内存维护、重启归零（BR-06——仅乱序检测提示，
 *  不承担补偿语义）。纯函数化设计（ws 以最小接口注入）便于单元测试。
 */

export const MAX_ROOMS_PER_CONNECTION = 10;
export const MAX_CONNECTIONS_PER_ROOM = 500;
export const MAX_CONNECTIONS_PER_USER = 5;

/** ws 最小行为面（真实 ws.WebSocket 或测试替身共用）。 */
export interface WsLike {
  send(data: string): void;
  close(code?: number, reason?: string): void;
  terminate(): void;
  ping?(): void;
  on?(event: string, listener: (...args: unknown[]) => void): unknown;
}

export interface Conn {
  ws: WsLike;
  userId: string;
  wsKey: string;
  rooms: Set<string>;
  alive: boolean;
  joinedAt: number;
  /** 心跳活性：最近一次 pong 时刻（ms；BR-04 60s 无 pong 判死） */
  lastPongAt: number;
}

interface RoomState {
  conns: Set<Conn>;
  seq: number;
}

export type PresenceEvent = "presence.joined" | "presence.left";

export interface RegisterResult {
  conn: Conn;
  /** 同 wsKey 幂等替换被踢的旧连接（重连，非 DUP_SESSION） */
  replaced: Conn[];
  /** 用户第 6+ 连接触发踢出的最旧连接（close 4000 DUP_SESSION） */
  evicted: Conn[];
}

/** 房间注册表：join/leave/成员枚举/序号/presence 钩子。 */
export class RoomRegistry {
  private rooms = new Map<string, RoomState>();
  private conns = new Set<Conn>();
  private byUser = new Map<string, Set<Conn>>();
  /** presence 进出广播钩子（server.ts 注入；仅 project 房间触发，BR-11） */
  onPresence: ((kind: PresenceEvent, room: string, conn: Conn) => void) | null = null;

  connectionCount(): number {
    return this.conns.size;
  }

  roomCount(): number {
    return this.rooms.size;
  }

  members(room: string): Set<Conn> {
    return this.rooms.get(room)?.conns ?? new Set<Conn>();
  }

  connectionsOfUser(userId: string): Set<Conn> {
    return this.byUser.get(userId) ?? new Set<Conn>();
  }

  allConnections(): Set<Conn> {
    return new Set(this.conns);
  }

  /** 房间级单调递增序号（BR-06：内存维护，重启归零）。 */
  nextSeq(room: string): number {
    const state = this.rooms.get(room);
    if (!state) return 0;
    state.seq += 1;
    return state.seq;
  }

  /** 注册连接 + BR-12 双语义（同键替换 / 每用户上限踢旧）。 */
  register(ws: WsLike, claims: { sub: string; ws: string }, now = Date.now()): RegisterResult {
    const replaced: Conn[] = [];
    const evicted: Conn[] = [];
    // ① 同 wsKey 幂等替换（重连）：踢同键旧连接，不占新额度
    for (const existing of this.allConnections()) {
      if (existing.wsKey === claims.ws) {
        this.remove(existing, { broadcastPresence: true });
        replaced.push(existing);
      }
    }
    const conn: Conn = {
      ws,
      userId: claims.sub,
      wsKey: claims.ws,
      rooms: new Set<string>(),
      alive: true,
      joinedAt: now,
      lastPongAt: now,
    };
    // ② 每用户全局 ≤5（跨 workspace 按票据 sub 计数）：第 6 条踢最旧
    const same = this.connectionsOfUser(claims.sub);
    if (same.size >= MAX_CONNECTIONS_PER_USER) {
      let oldest: Conn | null = null;
      for (const c of same) {
        if (!oldest || c.joinedAt < oldest.joinedAt) oldest = c;
      }
      if (oldest) {
        this.remove(oldest, { broadcastPresence: true });
        evicted.push(oldest);
      }
    }
    this.conns.add(conn);
    let set = this.byUser.get(claims.sub);
    if (!set) {
      set = new Set<Conn>();
      this.byUser.set(claims.sub, set);
    }
    set.add(conn);
    return { conn, replaced, evicted };
  }

  /** 加入房间（房间上限 500：超容 WARN 拒订不杀连接，§2.6）。 */
  join(conn: Conn, room: string): boolean {
    if (conn.rooms.has(room)) return true;
    if (conn.rooms.size >= MAX_ROOMS_PER_CONNECTION) return false;
    let state = this.rooms.get(room);
    if (!state) {
      state = { conns: new Set<Conn>(), seq: 0 };
      this.rooms.set(room, state);
    }
    if (state.conns.size >= MAX_CONNECTIONS_PER_ROOM) return false;
    state.conns.add(conn);
    conn.rooms.add(room);
    if (room.startsWith("project:") && this.onPresence) {
      this.onPresence("presence.joined", room, conn);
    }
    return true;
  }

  /** 退出房间（broadcastPresence=false 用于复核静默踢房，BR-03）。 */
  leave(conn: Conn, room: string, broadcastPresence = true): void {
    const state = this.rooms.get(room);
    if (!state) return;
    state.conns.delete(conn);
    if (state.conns.size === 0) this.rooms.delete(room);
    conn.rooms.delete(room);
    if (room.startsWith("project:") && broadcastPresence && state.conns.size > 0
        && this.onPresence) {
      this.onPresence("presence.left", room, conn);
    }
  }

  /** 连接整体下线：退全部房间 + 摘除索引。 */
  remove(conn: Conn, opts: { broadcastPresence?: boolean } = {}): void {
    for (const room of [...conn.rooms]) {
      this.leave(conn, room, opts.broadcastPresence ?? true);
    }
    this.conns.delete(conn);
    const set = this.byUser.get(conn.userId);
    if (set) {
      set.delete(conn);
      if (set.size === 0) this.byUser.delete(conn.userId);
    }
  }
}
