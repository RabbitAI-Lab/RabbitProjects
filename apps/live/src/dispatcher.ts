/** 事件分发管道（COLLAB-004 §4.3.1 Redis 消息 → 房间扇出）。
 *
 *  职责链（handleRaw）：
 *    ① 2KB 载荷红线（BR-05：超限丢弃 + ERROR——提示语义，全量实体禁入）；
 *    ② 信封形态校验（event/rooms/payload/occurred_at）；
 *    ③ 单房间速率保护（BR-13：>200 msg/s 起 100ms 窗口合批同类事件，seq 取
 *       最大、batch_id 保留——批量拖 50 卡只广播聚合后若干包）；
 *    ④ 自己操作不回显（BR-08：payload.actor_id == conn.userId 跳过该连接）。
 *
 *  与 bus.ts 解耦：bus 只负责订阅与降级，分发语义全部收口在此（可单测）。
 */
import type { Logger } from "./log";
import type { Conn, RoomRegistry } from "./rooms";

/** 整条消息（含信封）红线（BR-05） */
export const MAX_MESSAGE_BYTES = 2048;
/** 单房间速率阈值（msg/s，BR-13） */
export const ROOM_RATE_LIMIT = 200;
/** 合批窗口（ms，BR-13） */
export const BATCH_WINDOW_MS = 100;
/** 合批静默期：速率回落后退出聚合模式的毫秒窗 */
const RATE_WINDOW_MS = 1000;

export interface BusMessage {
  event: string;
  rooms: string[];
  payload: Record<string, unknown>;
  occurred_at: string;
}

/** 房间速率闸：低于阈值直通；超阈值起 100ms 合批（同类保最新，seq 取最大）。 */
export class RoomGate {
  private times: number[] = [];
  private pending: Map<string, BusMessage> | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private readonly send: (msg: BusMessage) => void,
    private readonly now: () => number = () => Date.now(),
  ) {}

  offer(msg: BusMessage): void {
    const t = this.now();
    this.times = this.times.filter((x) => t - x < RATE_WINDOW_MS);
    this.times.push(t);
    if (this.times.length <= ROOM_RATE_LIMIT) {
      this.send(msg);
      return;
    }
    // 超阈值 → 聚合节流：同 (event) 保最新载荷（batch_id 保留），窗口到期一并发送
    if (!this.pending) this.pending = new Map();
    this.pending.set(msg.event, msg);
    if (this.timer === null) {
      this.timer = setTimeout(() => this.flush(), BATCH_WINDOW_MS);
    }
  }

  flush(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    const pending = this.pending;
    this.pending = null;
    if (!pending) return;
    for (const msg of pending.values()) this.send(msg);
  }

  dispose(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    this.pending = null;
  }
}

/** 事件分发器：Redis 原始消息 →（红线 / 校验 / 合批 / 不回显）→ 房间广播。 */
export class EventDispatcher {
  private gates = new Map<string, RoomGate>();
  /** 事件速率观测（BR-14）：进程启动至今分发总数 */
  dispatched = 0;

  constructor(
    private readonly registry: RoomRegistry,
    private readonly logger: Logger,
    private readonly now: () => number = () => Date.now(),
  ) {}

  health(): { events_dispatched: number } {
    return { events_dispatched: this.dispatched };
  }

  /** Redis message 回调入口（bus.ts 订阅侧透传原始字符串）。 */
  handleRaw(raw: string): void {
    if (Buffer.byteLength(raw, "utf8") > MAX_MESSAGE_BYTES) { // BR-05 前置红线
      this.logger.error("event_payload_oversize", { bytes: Buffer.byteLength(raw, "utf8") });
      return;
    }
    let msg: BusMessage;
    try {
      msg = JSON.parse(raw) as BusMessage;
    } catch {
      this.logger.warn("event_malformed_json", {});
      return;
    }
    if (
      typeof msg?.event !== "string" || msg.event.length === 0
      || !Array.isArray(msg?.rooms) || msg.rooms.length === 0
      || typeof msg?.payload !== "object" || msg.payload === null
      || typeof msg?.occurred_at !== "string"
    ) {
      this.logger.warn("event_malformed_envelope", { event: msg?.event });
      return;
    }
    // 逐房间走各自速率闸（BR-13 阈值按房间计）；投递面收敛为单房间消息
    for (const room of msg.rooms) {
      this.gateOf(room).offer({ ...msg, rooms: [room] });
    }
  }

  /** 本地事件（presence 广播）同走速率闸。 */
  broadcast(msg: BusMessage): void {
    for (const room of msg.rooms) {
      this.gateOf(room).offer({ ...msg, rooms: [room] });
    }
  }

  /** 房间单播装配（seq / 不回显），由 RoomGate.send 回调消费。 */
  deliver(msg: BusMessage): void {
    const actorId = typeof msg.payload.actor_id === "string" ? msg.payload.actor_id : null;
    for (const room of msg.rooms) {
      const members = this.registry.members(room);
      if (members.size === 0) continue;
      const seq = this.registry.nextSeq(room);
      const frame = JSON.stringify({
        event: msg.event,
        seq,
        room,
        payload: msg.payload,
        occurred_at: msg.occurred_at,
      });
      for (const conn of members) {
        if (actorId !== null && conn.userId === actorId) continue; // BR-08 不回显
        this.sendTo(conn, frame);
      }
    }
    this.dispatched += 1;
  }

  private sendTo(conn: Conn, frame: string): void {
    try {
      conn.ws.send(frame);
    } catch {
      // 发送失败（半死连接）：由心跳 60s 兜底清理，此处不中断其他成员
      this.logger.warn("event_send_failed", { user: conn.userId });
    }
  }

  private gateOf(room: string): RoomGate {
    let gate = this.gates.get(room);
    if (!gate) {
      gate = new RoomGate((msg) => this.deliver(msg), this.now);
      this.gates.set(room, gate);
    }
    return gate;
  }
}
