/** 集成测试：真实 HTTP+WS server（随机端口）+ ws 客户端（§5.2 IT-01 最小版 + UT-06/UT-10）。
 *
 *  Redis 总线以 connectBus:false 跳过，事件经 dispatcher.handleRaw 直灌——
 *  分发语义（合批/红线/不回显）已由 rooms.test.ts 单元覆盖，此处验端到端链路：
 *  换票 connect → connected 帧 → 事件扇出 → 关闭码 / 复核踢房 / 心跳超时。
 */
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { WebSocket } from "ws";
import { log } from "./log";
import { createLiveServer, type LiveServer } from "./server";
import { makeKeyPair, signTicket, uuid } from "./testutil";

const keys = makeKeyPair();
let server: LiveServer;
let baseUrl: string;

beforeAll(async () => {
  server = createLiveServer({
    publicKey: keys.publicKey,
    redisUrl: "redis://localhost:6399/9", // 不实际连接（connectBus: false）
    apiInternalUrl: "http://api.internal",
    internalKey: "test-internal-key",
    logger: log,
    connectBus: false,
    startVerifier: false,
    heartbeatIntervalMs: 25_000,
  });
  await new Promise<void>((resolve) => {
    server.httpServer.listen(0, () => resolve());
  });
  baseUrl = `ws://127.0.0.1:${server.port()}`;
});

afterAll(async () => {
  await server.close();
});

interface Frame {
  event: string;
  seq: number;
  room?: string;
  payload: Record<string, unknown>;
  occurred_at: string;
}

function connect(token: string): Promise<{ ws: WebSocket; frames: Frame[]; open: boolean }> {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`${baseUrl}/live/connect?token=${encodeURIComponent(token)}`);
    const frames: Frame[] = [];
    ws.on("message", (raw) => frames.push(JSON.parse(String(raw)) as Frame));
    ws.on("open", () => resolve({ ws, frames, open: true }));
    ws.on("error", reject);
    ws.on("close", () => { /* 关闭码断言走 close 事件专用监听 */ });
    // 兜底：握手失败也会触发 close，统一在 open 之后断言
    ws.on("unexpected-response", (_req, res) =>
      reject(new Error(`unexpected response ${res.statusCode}`)));
  });
}

function nextFrame(frames: Frame[], event: string, timeoutMs = 2000): Promise<Frame> {
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const poll = () => {
      const hit = frames.find((f) => f.event === event);
      if (hit) return resolve(hit);
      if (Date.now() - started > timeoutMs) {
        return reject(new Error(`timeout waiting ${event}; got ${frames.map((f) => f.event)}`));
      }
      setTimeout(poll, 20);
    };
    poll();
  });
}

function wsUrl(token: string): string {
  return `${baseUrl}/live/connect?token=${encodeURIComponent(token)}`;
}

describe("连接建立与票据闸（§2.1）", () => {
  it("有效票据 connect → connected 帧（rooms/ws/heartbeat）", async () => {
    const sub = uuid();
    const projectId = uuid();
    const token = signTicket(keys, {
      sub, clientTabId: "tab-A", rooms: [`project:${projectId}`, `user:${sub}`],
    });
    const { ws, frames } = await connect(token);
    const connected = await nextFrame(frames, "connected");
    expect(connected.payload.rooms).toEqual([`project:${projectId}`, `user:${sub}`]);
    expect(connected.payload.ws).toBe(`${sub}:tab-A`);
    expect(connected.payload.heartbeat).toBe(25);
    ws.close();
  });

  it("无效票据：握手后 close 4001 TOKEN_INVALID", async () => {
    const ws = new WebSocket(wsUrl("forged.token.value"));
    const code = await new Promise<number>((resolve) => {
      ws.on("close", (c) => resolve(c));
      ws.on("error", () => { /* ws 客户端对 4001 会 emit error + close */ });
    });
    expect(code).toBe(4001);
  });

  it("过期票据 close 4001", async () => {
    const ws = new WebSocket(wsUrl(signTicket(keys, { expired: true })));
    const code = await new Promise<number>((resolve) => {
      ws.on("close", (c) => resolve(c));
      ws.on("error", () => {});
    });
    expect(code).toBe(4001);
  });
});

describe("事件扇出（不回显 / 房间隔离）", () => {
  it("actor 本人无回显（BR-08），他者 <1s 收到 issue.state.changed（IT-01 最小版）", async () => {
    const actor = uuid();
    const viewer = uuid();
    const room = `project:${uuid()}`;
    const actorToken = signTicket(keys, {
      sub: actor, clientTabId: "tab-actor", rooms: [room, `user:${actor}`],
    });
    const viewerToken = signTicket(keys, {
      sub: viewer, clientTabId: "tab-viewer", rooms: [room, `user:${viewer}`],
    });
    const a = await connect(actorToken);
    const b = await connect(viewerToken);
    await nextFrame(a.frames, "connected");
    await nextFrame(b.frames, "connected");

    server.dispatcher.handleRaw(JSON.stringify({
      event: "issue.state.changed",
      rooms: [room],
      payload: {
        issue_id: uuid(), actor_id: actor,
        from_group: "unstarted", to_group: "started", version: "2026-09-05T06:32:00.114Z",
      },
      occurred_at: "2026-09-05T06:32:00.220Z",
    }));

    const got = await nextFrame(b.frames, "issue.state.changed", 1000);
    expect(got.room).toBe(room);
    expect(got.seq).toBeGreaterThan(0);
    // actor 帧序列中无业务事件（connected + presence 非回显范畴）
    await new Promise((r) => setTimeout(r, 150));
    const business = a.frames.filter(
      (f) => f.event !== "connected" && !f.event.startsWith("presence."));
    expect(business).toHaveLength(0);
    a.ws.close();
    b.ws.close();
  });

  it("房间隔离：不在房间的连接收不到", async () => {
    const sub1 = uuid();
    const room = `project:${uuid()}`;
    const outside = await connect(signTicket(keys, {
      sub: sub1, clientTabId: "t", rooms: [`project:${uuid()}`, `user:${sub1}`],
    }));
    await nextFrame(outside.frames, "connected");
    server.dispatcher.handleRaw(JSON.stringify({
      event: "issue.updated", rooms: [room],
      payload: { issue_id: uuid(), actor_id: null }, occurred_at: "t",
    }));
    await new Promise((r) => setTimeout(r, 120));
    expect(outside.frames.filter((f) => f.event === "issue.updated")).toHaveLength(0);
    outside.ws.close();
  });

  it("presence：project 房间进出广播 joined/left（BR-11 摘要 ≤200B）", async () => {
    const first = uuid();
    const room = `project:${uuid()}`;
    const a = await connect(signTicket(keys, {
      sub: first, clientTabId: "t1", rooms: [room, `user:${first}`],
    }));
    await nextFrame(a.frames, "connected");
    const second = uuid();
    const b = await connect(signTicket(keys, {
      sub: second, clientTabId: "t2", rooms: [room, `user:${second}`],
    }));
    await nextFrame(b.frames, "connected");
    const joined = await nextFrame(a.frames, "presence.joined");
    expect(joined.payload.user).toEqual({ id: second });
    expect(JSON.stringify(joined.payload).length).toBeLessThanOrEqual(200);
    b.ws.close();
    const left = await nextFrame(a.frames, "presence.left");
    expect(left.payload.user).toEqual({ id: second });
    a.ws.close();
  });
});

describe("连接生命周期（BR-12 / BR-03）", () => {
  it("同 client_tab_id 重连幂等替换：旧连接被关闭（1000 REPLACED）", async () => {
    const sub = uuid();
    const room = `project:${uuid()}`;
    const tokenFor = () => signTicket(keys, {
      sub, clientTabId: "same-tab", rooms: [room, `user:${sub}`],
    });
    const first = await connect(tokenFor());
    await nextFrame(first.frames, "connected");
    const closedCode = new Promise<number>((resolve) => {
      first.ws.on("close", (c) => resolve(c));
    });
    const second = await connect(tokenFor());
    await nextFrame(second.frames, "connected");
    expect(await closedCode).toBe(1000);
    second.ws.close();
  });

  it("第 6 条连接（不同 client_tab_id）踢最旧 4000 DUP_SESSION", async () => {
    const sub = uuid();
    const room = `project:${uuid()}`;
    const tabs: WebSocket[] = [];
    let oldestClosed = -1;
    for (let i = 0; i < 6; i++) {
      const token = signTicket(keys, {
        sub, clientTabId: `tab-${i}`, rooms: [room, `user:${sub}`],
      });
      // eslint-disable-next-line no-await-in-loop
      const c = await connect(token);
      await nextFrame(c.frames, "connected");
      if (i === 0) {
        c.ws.on("close", (code) => { oldestClosed = code; });
      }
      tabs.push(c.ws);
    }
    await new Promise((r) => setTimeout(r, 200));
    expect(oldestClosed).toBe(4000);
    expect(server.registry.connectionsOfUser(sub).size).toBe(5);
    for (const ws of tabs) ws.close();
  });

  it("verify-rooms 复核失效踢房；rooms 清空 close 4003 FORBIDDEN（UT-10）", async () => {
    const sub = uuid();
    const projectRoom = `project:${uuid()}`;
    const userRoom = `user:${sub}`;
    const token = signTicket(keys, {
      sub, clientTabId: "t", rooms: [projectRoom, userRoom],
    });
    const c = await connect(token);
    await nextFrame(c.frames, "connected");
    const closedCode = new Promise<number>((resolve) => {
      c.ws.on("close", (code) => resolve(code));
    });

    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      status: "success",
      data: { invalid: [{ sub, rooms: [projectRoom] }] },
    }), { status: 200 }));
    server.verifier.setFetchImpl(fetchMock as unknown as typeof fetch);
    await server.verifier.checkOnce();

    // user:{sub} 恒有效 → 房间仍在，连接未被 4003
    await new Promise((r) => setTimeout(r, 120));
    const conn = server.registry.connectionsOfUser(sub).values().next().value;
    expect(conn).toBeDefined();
    expect(conn?.rooms.has(projectRoom)).toBe(false);
    expect(conn?.rooms.has(userRoom)).toBe(true);
    expect(conn?.rooms.size).toBe(1);

    // 第二轮：user 房间也失效 → rooms 清空 → 4003
    fetchMock.mockImplementation(async () => new Response(JSON.stringify({
      status: "success",
      data: { invalid: [{ sub, rooms: [userRoom] }] },
    }), { status: 200 }));
    await server.verifier.checkOnce();
    expect(await closedCode).toBe(4003);
    expect(server.registry.connectionsOfUser(sub).size).toBe(0);
  });

  it("verify-rooms api 不可达：fail-open 不踢连接", async () => {
    const sub = uuid();
    const room = `project:${uuid()}`;
    const c = await connect(signTicket(keys, {
      sub, clientTabId: "t", rooms: [room, `user:${sub}`],
    }));
    await nextFrame(c.frames, "connected");
    server.verifier.setFetchImpl(
      vi.fn(async () => { throw new Error("api down"); }) as unknown as typeof fetch);
    await server.verifier.checkOnce();
    await new Promise((r) => setTimeout(r, 100));
    expect(server.registry.connectionsOfUser(sub).size).toBe(1);
    c.ws.close();
  });
});

describe("心跳超时（BR-04，UT-06）——独立小间隔实例", () => {
  it("60s（测试中缩短）无 pong → close 4004 HEARTBEAT_TIMEOUT", async () => {
    const hb = createLiveServer({
      publicKey: keys.publicKey,
      redisUrl: "redis://localhost:6399/9",
      apiInternalUrl: "http://api.internal",
      internalKey: "k",
      logger: log,
      connectBus: false,
      startVerifier: false,
      heartbeatIntervalMs: 40,
      pongTimeoutMs: 150,
    });
    await new Promise<void>((resolve) => {
      hb.httpServer.listen(0, () => resolve());
    });
    try {
      const sub = uuid();
      const token = signTicket(keys, {
        sub, clientTabId: "t", rooms: [`project:${uuid()}`, `user:${sub}`],
      });
      const url = `ws://127.0.0.1:${hb.port()}/live/connect?token=${encodeURIComponent(token)}`;
      const ws = new WebSocket(url);
      const closed = new Promise<number>((resolve) => {
        ws.on("close", (code) => resolve(code));
        ws.on("error", () => {});
      });
      await new Promise<void>((resolve) => ws.on("open", () => resolve()));
      // 模拟死客户端：吞掉服务端 ping（客户端收不到 ping 即不会 pong）
      const conn = hb.registry.connectionsOfUser(sub).values().next().value;
      if (!conn) throw new Error("connection not found");
      (conn.ws as WebSocket).ping = () => {};
      expect(await closed).toBe(4004);
    } finally {
      await hb.close();
    }
  });
});

describe("/health 指标（BR-14）", () => {
  it("返回 connections/rooms 指标", async () => {
    const res = await fetch(`http://127.0.0.1:${server.port()}/health`);
    expect(res.status).toBe(200);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.status).toBe("ok");
    expect(typeof body.connections).toBe("number");
    expect(typeof body.rooms).toBe("number");
    expect(body.degraded).toEqual({ redis: false });
  });
});
