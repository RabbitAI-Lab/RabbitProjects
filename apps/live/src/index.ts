/**
 * live 服务入口（INFRA-002 容器 / COLLAB-004 §4.3.1）。
 *
 * P0 骨架（Express + /health）之上接入业务事件通道（Sprint-3 COLLAB-004）：
 *   - WSS /live/connect?token=…（经 proxy upgrade，RS256 票据验签 + 房间扇出）；
 *   - Redis 订阅 rp:events（api Worker 扇出的唯一下游通道，BR-09）；
 *   - 60s 权限周期复核（BR-03）+ 心跳 25s（BR-04）+ presence（BR-11）；
 *   - /health 附 connections/rooms/degraded 指标（BR-14；Redis 断连 → 503）。
 *
 * 协同编辑（Hocuspocus + Yjs）仍为 P3——届时挂入本进程复用票据与房间模型。
 */
import { z } from "zod";
import { log } from "./log";
import { createLiveServer } from "./server";

/** 最小日志封装已在 log.ts（INFRA-001 §4.11：禁止裸 console）。 */
const EnvSchema = z.object({
  LIVE_PORT: z.coerce.number().int().positive(),
  API_INTERNAL_URL: z.string().url(),
  REDIS_URL: z.string().url(),
  /** RS256 公钥 PEM：live 只持公钥（§4.1 密钥分离——被攻破也无法伪造票据） */
  LIVE_JWT_PUBLIC_KEY: z.string().min(1),
  /** live→api 服务间复核认证（api-conventions.md §9.7） */
  INTERNAL_KEY: z.string().min(1),
  /** 心跳间隔（秒，BR-04；60s 无 pong 断开） */
  LIVE_HEARTBEAT: z.coerce.number().int().positive().default(25),
});

// fail-fast：缺失即退出，不静默取默认值（INFRA-001 §4.6 live 关键点）
const parsed = EnvSchema.safeParse(process.env);
if (!parsed.success) {
  const missing = parsed.error.issues.map((i) => i.path.join(".")).join(", ");
  log.error("env_validation_failed", { missing });
  process.exit(1);
}

const env = parsed.data;
const server = createLiveServer({
  publicKey: env.LIVE_JWT_PUBLIC_KEY,
  redisUrl: env.REDIS_URL,
  apiInternalUrl: env.API_INTERNAL_URL,
  internalKey: env.INTERNAL_KEY,
  heartbeatIntervalMs: env.LIVE_HEARTBEAT * 1000,
  logger: log,
});

server.httpServer.listen(env.LIVE_PORT, () => {
  log.info("listening", {
    port: env.LIVE_PORT,
    health: "/health",
    ws: "/live/connect",
    channel: "rp:events",
  });
});

const shutdown = (signal: string) => {
  log.info("shutdown", { signal });
  void server.close().finally(() => process.exit(0));
};
process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));
