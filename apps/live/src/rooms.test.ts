/** 房间注册表 / 心跳 / 事件分发单元测试（§5.1 UT-04~UT-08 单元级）。 */
import { describe, expect, it, vi } from "vitest";
import { RoomGate, EventDispatcher, MAX_MESSAGE_BYTES } from "./dispatcher";
import { log } from "./log";
import { RoomRegistry, type WsLike, type Conn } from "./rooms";

function mockWs() {
  const sent: string[] = [];
  let closed: { code?: number | undefined; reason?: string | undefined } | null = null;
  const listeners = new Map<string, (...args: unknown[]) => void>();
  const ws: WsLike & {
    sent: string[];
    closedOf: () => { code?: number | undefined; reason?: string | undefined } | null;
    emit: (event: string, ...args: unknown[]) => void;
  } = {
    send: (data) => sent.push(data),
    close: (code, reason) => {
      if (!closed) closed = { code, reason };
    },
    terminate: () => {
      closed = closed ?? { code: -1 };
    },
    ping: () => {},
    on: (event, listener) => listeners.set(event, listener),
    sent,
    closedOf: () => closed,
    emit: (event, ...args) => listeners.get(event)?.(...args),
  };
  return ws;
}

function makeConn(registry: RoomRegistry, sub: string, wsKey: string, rooms: string[]): Conn {
  const ws = mockWs();
  const { conn } = registry.register(ws, { sub, ws: wsKey });
  for (const room of rooms) registry.join(conn, room);
  return conn;
}

describe("RoomRegistry（BR-12 两语义分立，UT-07）", () => {
  it("同 wsKey 二次连接 = 幂等替换（踢旧、不占新额度）", () => {
    const registry = new RoomRegistry();
    const key = `u1:tab-1`;
    const first = makeConn(registry, "u1", key, ["project:p1", "user:u1"]);
    const { conn: second, replaced, evicted } = registry.register(mockWs(), {
      sub: "u1", ws: key,
    });
    expect(replaced.map((c) => c.wsKey)).toEqual([key]);
    expect(evicted).toHaveLength(0);
    expect(registry.connectionCount()).toBe(1);
    expect(replaced[0]).toBe(first);
    expect(second.userId).toBe("u1");
  });

  it("每用户第 6 条连接踢最旧（4000 DUP_SESSION 由 server 层关闭）", () => {
    const registry = new RoomRegistry();
    const conns: Conn[] = [];
    for (let i = 0; i < 5; i++) {
      conns.push(makeConn(registry, "u1", `u1:tab-${i}`, [`user:u1`]));
    }
    expect(registry.connectionCount()).toBe(5);
    const { conn: sixth, evicted } = registry.register(mockWs(), {
      sub: "u1", ws: "u1:tab-6",
    });
    expect(evicted).toHaveLength(1);
    expect(evicted[0]).toBe(conns[0]); // 最旧
    expect(sixth.wsKey).toBe("u1:tab-6");
    expect(registry.connectionCount()).toBe(5); // 踢一进一
  });

  it("房间成员枚举 / seq 单调递增 / leave 清理", () => {
    const registry = new RoomRegistry();
    makeConn(registry, "u1", "u1:t1", ["project:p1"]);
    makeConn(registry, "u2", "u2:t1", ["project:p1"]);
    expect(registry.members("project:p1").size).toBe(2);
    expect(registry.nextSeq("project:p1")).toBe(1);
    expect(registry.nextSeq("project:p1")).toBe(2);
    const u1 = registry.allConnections().values().next().value as Conn;
    registry.remove(u1);
    expect(registry.members("project:p1").size).toBe(1);
    expect(registry.roomCount()).toBe(1); // 仅剩 project:p1（u2 无 user 房间）
  });
});

describe("RoomGate 合批（BR-13，UT-08）", () => {
  it("低于 200 msg/s 直通；超阈值 100ms 窗口合批（seq 取最大语义由 deliver 承担）", () => {
    vi.useFakeTimers();
    try {
      const sent: string[] = [];
      const gate = new RoomGate((msg) => sent.push(msg.event));
      // 200 条内逐条直发
      for (let i = 0; i < 200; i++) {
        gate.offer({ event: "issue.updated", rooms: ["r"], payload: { i }, occurred_at: "t" });
      }
      expect(sent.length).toBe(200);
      // 第 201 条起：进入合批——100ms 窗口内同类只保最新
      for (let i = 0; i < 50; i++) {
        gate.offer({ event: "issue.updated", rooms: ["r"], payload: { i }, occurred_at: "t" });
      }
      gate.offer({ event: "issue.state.changed", rooms: ["r"], payload: {}, occurred_at: "t" });
      expect(sent.length).toBe(200); // 未到窗口不动
      vi.advanceTimersByTime(100);
      expect(sent.length).toBe(202); // 两类各一包
      vi.useRealTimers();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("EventDispatcher（UT-04 不回显 / UT-05 载荷红线）", () => {
  function setup() {
    const registry = new RoomRegistry();
    const actor = makeConn(registry, "actor-1", "actor-1:tab", ["project:p1"]);
    const other = makeConn(registry, "other-1", "other-1:tab", ["project:p1"]);
    const dispatcher = new EventDispatcher(registry, log);
    return { registry, dispatcher, actor, other };
  }

  it("actor_id == sub 的连接不回显（BR-08），他者收到", () => {
    const { dispatcher, actor, other } = setup();
    dispatcher.handleRaw(JSON.stringify({
      event: "issue.state.changed",
      rooms: ["project:p1"],
      payload: { issue_id: "i1", actor_id: "actor-1" },
      occurred_at: "2026-09-05T06:32:00.220Z",
    }));
    const actorFrames = (actor.ws as ReturnType<typeof mockWs>).sent;
    const otherFrames = (other.ws as ReturnType<typeof mockWs>).sent;
    expect(actorFrames).toHaveLength(0);
    expect(otherFrames).toHaveLength(1);
    const env = JSON.parse(otherFrames[0] as string);
    expect(env.event).toBe("issue.state.changed");
    expect(env.room).toBe("project:p1");
    expect(env.seq).toBe(1);
    expect(env.payload.actor_id).toBe("actor-1");
  });

  it("超过 2KB 的消息丢弃且不投递（BR-05）", () => {
    const { dispatcher, other } = setup();
    const big = JSON.stringify({
      event: "issue.updated",
      rooms: ["project:p1"],
      payload: { blob: "x".repeat(MAX_MESSAGE_BYTES) },
      occurred_at: "t",
    });
    expect(Buffer.byteLength(big)).toBeGreaterThan(MAX_MESSAGE_BYTES);
    dispatcher.handleRaw(big);
    expect((other.ws as ReturnType<typeof mockWs>).sent).toHaveLength(0);
  });

  it("非法 JSON / 信封缺字段静默丢弃", () => {
    const { dispatcher, other } = setup();
    dispatcher.handleRaw("{not json");
    dispatcher.handleRaw(JSON.stringify({ event: "x" }));
    expect((other.ws as ReturnType<typeof mockWs>).sent).toHaveLength(0);
  });

  it("多房间扇出：seq 房间级独立递增", () => {
    const registry = new RoomRegistry();
    makeConn(registry, "u1", "u1:t", ["project:p1", "issue:i1"]);
    const dispatcher = new EventDispatcher(registry, log);
    dispatcher.handleRaw(JSON.stringify({
      event: "issue.updated", rooms: ["project:p1", "issue:i1"],
      payload: { actor_id: null }, occurred_at: "t",
    }));
    dispatcher.handleRaw(JSON.stringify({
      event: "issue.updated", rooms: ["project:p1"],
      payload: { actor_id: null }, occurred_at: "t",
    }));
    const conn = registry.allConnections().values().next().value as Conn;
    const frames = (conn.ws as ReturnType<typeof mockWs>).sent.map((f) => JSON.parse(f));
    expect(frames.map((f) => [f.room, f.seq])).toEqual([
      ["project:p1", 1], ["issue:i1", 1], ["project:p1", 2],
    ]);
  });

  it("file 域事件扇出到 file:{asset_id} 房间（FILE-003 §4.4：rooms = project + file）", () => {
    const registry = new RoomRegistry();
    const subscriber = makeConn(registry, "u1", "u1:t", ["file:a1", "user:u1"]);
    makeConn(registry, "u2", "u2:t", ["project:p1"]); // 不在 file 房间：收 project 侧即可
    const dispatcher = new EventDispatcher(registry, log);
    dispatcher.handleRaw(JSON.stringify({
      event: "file.version.created",
      rooms: ["project:p1", "file:a1"],
      payload: { asset_id: "a1", version_number: 3, actor_id: "u9" },
      occurred_at: "t",
    }));
    const frames = (subscriber.ws as ReturnType<typeof mockWs>).sent
      .map((f) => JSON.parse(f));
    expect(frames).toHaveLength(1); // 只收 file 房间帧（未订 project:p1）
    expect(frames[0].event).toBe("file.version.created");
    expect(frames[0].room).toBe("file:a1");
    expect(frames[0].seq).toBe(1);
    expect(frames[0].payload.version_number).toBe(3);
  });
});
