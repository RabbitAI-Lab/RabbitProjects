/** Redis 订阅总线（COLLAB-004 BR-09：api → live 唯一通道，channel rp:events）。
 *
 *  - 断连降级：degraded=true → /health 503（供前端 BR-10 降级判定）；恢复自动重订；
 *  - 指数重连：1s 起 ×2 至 30s 封顶（ioredis retryStrategy）；
 *  - 订阅连接与发布分离：live 只订阅（publish 侧在 api Worker）。
 */
import Redis from "ioredis";
import type { Logger } from "./log";

export const EVENTS_CHANNEL = "rp:events";

export interface BusState {
  degraded: boolean;
}

/** 订阅总线：消息原文透传 dispatcher（分发语义不在此，可测性分离）。 */
export class EventBus {
  private sub: Redis | null = null;
  private stopped = false;
  degraded = true; // 未建立连接前视为降级

  constructor(
    private readonly redisUrl: string,
    private readonly onMessage: (raw: string) => void,
    private readonly logger: Logger,
    private readonly onStateChange?: (degraded: boolean) => void,
  ) {}

  connect(): void {
    this.stopped = false;
    const sub = new Redis(this.redisUrl, {
      // 指数重连：1s 起 ×2 至 30s 封顶（§2.2 退避曲线的服务端形态）
      retryStrategy: (times) => Math.min(1000 * 2 ** Math.min(times, 5), 30_000),
      maxRetriesPerRequest: null,
      lazyConnect: false,
      enableOfflineQueue: true,
    });
    this.sub = sub;
    sub.on("message", (_channel: string, raw: string) => this.onMessage(raw));
    sub.on("ready", () => {
      this.setDegraded(false);
      this.logger.info("bus_ready", { channel: EVENTS_CHANNEL });
    });
    sub.on("error", (err: Error) => {
      // 连接级错误交由 retryStrategy 重连；此处仅降级标记 + 结构化日志
      if (!this.degraded) {
        this.logger.error("bus_error", { error: err.message });
      }
      this.setDegraded(true);
    });
    sub.on("end", () => {
      if (!this.stopped) this.setDegraded(true);
    });
    void sub.subscribe(EVENTS_CHANNEL).catch((err: Error) => {
      this.setDegraded(true);
      this.logger.warn("bus_subscribe_deferred", { error: err.message });
    });
  }

  private setDegraded(degraded: boolean): void {
    if (this.degraded === degraded) return;
    this.degraded = degraded;
    this.onStateChange?.(degraded);
  }

  close(): void {
    this.stopped = true;
    this.sub?.disconnect();
    this.sub = null;
  }
}
