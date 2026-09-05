/** live 服务装配（COLLAB-004 §4.3.1）：ws 房间连接生命周期 / 心跳 / presence。
 *
 *  连接建立（onConnection）：
 *    验签失败 close(4001)（BR-01，前端重换票 ≤2 次后降级）→ 同 wsKey 幂等替换
 *    （BR-12 重连）→ 每用户 ≤5（第 6 踢最旧 4000 DUP_SESSION）→ 入房 → connected
 *    帧（§2.1：{rooms, ws, heartbeat}）。
 *
 *  心跳（BR-04）：每 LIVE_HEARTBEAT 秒 ping；60s 无 pong close(4004) 后 terminate。
 *  presence（BR-11）：project 房间进出广播 presence.joined/left（摘要 ≤200B）。
 */
import type { Server as HttpServer } from "node:http";
import { createServer } from "node:http";
import express, { type Express } from "express";
import type { Duplex } from "node:stream";
import { WebSocketServer, type WebSocket } from "ws";
import { EventBus } from "./bus";
import { EventDispatcher } from "./dispatcher";
import type { Logger } from "./log";
import { RoomRegistry, type Conn } from "./rooms";
import { extractToken, verifyTicket } from "./ticket";
import { RoomVerifier } from "./verify";

export const CONNECT_PATH = "/live/connect";
/** BR-04：60s 无 pong 判死（ping 间隔由 LIVE_HEARTBEAT 控制，默认 25s） */
export const PONG_TIMEOUT_MS = 60_000;

export interface LiveServerOptions {
  publicKey: string;
  redisUrl: string;
  apiInternalUrl: string;
  internalKey: string;
  heartbeatIntervalMs?: number;
  pongTimeoutMs?: number;
  verifyIntervalMs?: number;
  logger: Logger;
  /** 测试注入：跳过 Redis 连接（用 dispatcher.handleRaw 直灌消息） */
  connectBus?: boolean;
  /** 测试注入：跳过 60s 复核定时器（用 verifier.checkOnce() 手动触发） */
  startVerifier?: boolean;
  /** 测试注入：自定义 fetch（verify-rooms 复核 mock） */
  fetchImpl?: typeof fetch;
  now?: () => number;
}

export interface LiveServer {
  app: Express;
  httpServer: HttpServer;
  registry: RoomRegistry;
  dispatcher: EventDispatcher;
  bus: EventBus | null;
  verifier: RoomVerifier;
  port(): number;
  close(): Promise<void>;
  /** 供 /health 的实时指标快照（BR-14） */
  metrics(): {
    connections: number;
    rooms: number;
    degraded: boolean;
    events_dispatched: number;
  };
}

export function createLiveServer(opts: LiveServerOptions): LiveServer {
  const registry = new RoomRegistry();
  const dispatcher = new EventDispatcher(registry, opts.logger, opts.now);
  const bus = opts.connectBus === false
    ? null
    : new EventBus(opts.redisUrl, (raw) => dispatcher.handleRaw(raw), opts.logger);
  const verifier = new RoomVerifier({
    apiInternalUrl: opts.apiInternalUrl,
    internalKey: opts.internalKey,
    registry,
    logger: opts.logger,
    fetchImpl: opts.fetchImpl,
    intervalMs: opts.verifyIntervalMs,
  });

  // ── presence（BR-11）：project 房间进出广播（user 摘要 ≤200B——仅 user_id，
  //    前端以成员目录水合 display_name；live 不持业务数据，BR-09 无状态）──
  registry.onPresence = (kind, room, conn) => {
    dispatcher.broadcast({
      event: kind,
      rooms: [room],
      payload: { room, user: { id: conn.userId }, actor_id: conn.userId },
      occurred_at: new Date().toISOString(),
    });
  };

  const wss = new WebSocketServer({ noServer: true });
  const heartbeatMs = opts.heartbeatIntervalMs ?? 25_000;
  const pongTimeoutMs = opts.pongTimeoutMs ?? PONG_TIMEOUT_MS;

  const handleUpgrade = (req: import("node:http").IncomingMessage,
                          socket: Duplex, head: Buffer): void => {
    const url = req.url ?? "";
    if (!url.startsWith(CONNECT_PATH)) {
      socket.destroy();
      return;
    }
    const claims = verifyTicket(extractToken(url), opts.publicKey);
    // 无效票据也完成握手再 close(4001)：把关闭码可靠送达前端（§2.1 时序图）
    wss.handleUpgrade(req, socket, head, (ws) => {
      if (claims === null) {
        ws.close(4001, "TOKEN_INVALID");
        opts.logger.warn("ticket_rejected", { path: CONNECT_PATH });
        return;
      }
      onConnection(ws, claims);
    });
  };

  const onConnection = (ws: WebSocket, claims: {
    sub: string; ws: string; rooms: string[];
  }): void => {
    const { conn, replaced, evicted } = registry.register(ws, claims);
    // 同 wsKey 幂等替换：旧连接正常关闭（同一标签页的重连，非 DUP_SESSION）
    for (const old of replaced) {
      old.ws.close(1000, "REPLACED");
    }
    // BR-12：每用户全局第 6 条连接 → 踢最旧 4000 DUP_SESSION
    for (const old of evicted) {
      old.ws.close(4000, "DUP_SESSION");
      opts.logger.info("conn_evicted_dup_session", { user: old.userId, ws: old.wsKey });
    }
    for (const room of claims.rooms) {
      if (!registry.join(conn, room)) {
        opts.logger.warn("room_join_rejected", { room, user: conn.userId });
      }
    }
    ws.on("pong", () => {
      conn.lastPongAt = Date.now();
      conn.alive = true;
    });
    ws.on("message", (data) => {
      // 应用层 ping（非浏览器客户端 / 调试）；协议层 pong 由浏览器自动应答
      if (data.toString() === "ping") ws.send("pong");
    });
    ws.on("close", () => {
      registry.remove(conn);
      opts.logger.info("conn_closed", {
        user: conn.userId, rooms_left: conn.rooms.size,
      });
    });
    ws.on("error", () => {
      registry.remove(conn);
    });
    // connected 帧（§2.1）：握手成功即下发房间集与心跳间隔
    ws.send(JSON.stringify({
      event: "connected",
      seq: 0,
      payload: { rooms: [...conn.rooms], ws: conn.wsKey, heartbeat: Math.round(heartbeatMs / 1000) },
      occurred_at: new Date().toISOString(),
    }));
    opts.logger.info("conn_open", { user: conn.userId, rooms: conn.rooms.size });
  };

  // ── 心跳（BR-04）：interval ping；60s 无 pong → close(4004) + terminate 兜底 ──
  const heartbeat = setInterval(() => {
    for (const conn of registry.allConnections()) {
      if (Date.now() - conn.lastPongAt > pongTimeoutMs) {
        try {
          conn.ws.close(4004, "HEARTBEAT_TIMEOUT");
        } catch {
          // close 抛错（半死 socket）→ 直接 terminate
        }
        setTimeout(() => conn.ws.terminate(), 1000).unref?.();
        registry.remove(conn);
        opts.logger.info("conn_heartbeat_timeout", { user: conn.userId });
        continue;
      }
      conn.ws.ping?.();
    }
  }, heartbeatMs);

  const app = express();
  app.disable("x-powered-by");
  app.get("/health", (_req, res) => {
    const m = metrics();
    if (m.degraded) {
      res.status(503).json({
        status: "fail", service: "live", degraded: { redis: true },
        connections: m.connections, rooms: m.rooms, events_dispatched: m.events_dispatched,
      });
      return;
    }
    res.status(200).json({
      status: "ok", service: "live", degraded: { redis: false },
      connections: m.connections, rooms: m.rooms, events_dispatched: m.events_dispatched,
    });
  });

  const httpServer = createServer(app);
  httpServer.on("upgrade", handleUpgrade);

  if (opts.startVerifier !== false) verifier.start();
  if (bus !== null) bus.connect();
  heartbeat.unref?.();

  const metrics = () => ({
    connections: registry.connectionCount(),
    rooms: registry.roomCount(),
    degraded: bus?.degraded ?? false,
    events_dispatched: dispatcher.dispatched,
  });

  return {
    app,
    httpServer,
    registry,
    dispatcher,
    bus,
    verifier,
    port: () => {
      const addr = httpServer.address();
      return typeof addr === "object" && addr !== null ? addr.port : 0;
    },
    metrics,
    close: async () => {
      clearInterval(heartbeat);
      verifier.stop();
      bus?.close();
      for (const conn of registry.allConnections()) {
        conn.ws.close(1001, "SERVER_SHUTDOWN");
      }
      await new Promise<void>((resolve) => {
        httpServer.close(() => resolve());
      });
    },
  };
}

/** 注册表连接（供测试断言的辅助别名）。 */
export type { Conn };
