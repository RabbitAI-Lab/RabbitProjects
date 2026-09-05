/** 权限周期复核（COLLAB-004 BR-03，§4.3.2）。
 *
 *  每 60s 批量 POST api 内部端点校验全部连接 rooms → 失效踢房间；rooms 清空
 *  即 close(4003 FORBIDDEN)——封死「长连接 = 长权限」（权限收缩时效上界 60s）。
 *  api 不可达时跳过本轮（fail-open：复核通道故障不误杀连接，权限由 120s 票据
 *  过期 + 前端换票重校验兜底）。
 */
import type { Logger } from "./log";
import type { RoomRegistry } from "./rooms";

export const VERIFY_INTERVAL_MS = 60_000;

export interface VerifyOptions {
  apiInternalUrl: string;
  internalKey: string;
  registry: RoomRegistry;
  logger: Logger;
  fetchImpl?: typeof fetch | undefined;
  intervalMs?: number | undefined;
}

interface VerifyResponse {
  status?: string;
  data?: { invalid?: Array<{ sub: string; rooms: string[] }> };
}

export class RoomVerifier {
  private timer: ReturnType<typeof setInterval> | null = null;
  private running = false;

  constructor(private readonly opts: VerifyOptions) {}

  /** 测试注入：替换 fetch 实现（verify-rooms mock）。 */
  setFetchImpl(impl: typeof fetch): void {
    this.opts.fetchImpl = impl;
  }

  start(): void {
    if (this.timer !== null) return;
    this.timer = setInterval(() => {
      void this.checkOnce();
    }, this.opts.intervalMs ?? VERIFY_INTERVAL_MS);
  }

  stop(): void {
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
  }

  /** 单轮复核（导出供测试与时钟对齐）：按 sub 聚合 rooms → 失效项踢房。 */
  async checkOnce(): Promise<void> {
    if (this.running) return;
    this.running = true;
    try {
      const byUser = new Map<string, Set<string>>();
      for (const conn of this.opts.registry.allConnections()) {
        const rooms = byUser.get(conn.userId) ?? new Set<string>();
        for (const room of conn.rooms) rooms.add(room);
        byUser.set(conn.userId, rooms);
      }
      if (byUser.size === 0) return;
      const tickets = [...byUser.entries()].map(([sub, rooms]) => ({
        sub,
        rooms: [...rooms],
      }));
      const fetchImpl = this.opts.fetchImpl ?? fetch;
      const res = await fetchImpl(
        `${this.opts.apiInternalUrl.replace(/\/$/, "")}/api/v1/internal/realtime/verify-rooms/`,
        {
          method: "POST",
          headers: {
            "X-Internal-Key": this.opts.internalKey,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ tickets }),
          signal: AbortSignal.timeout(5000),
        },
      );
      if (!res.ok) {
        this.opts.logger.warn("verify_rooms_http_error", { status: res.status });
        return; // fail-open：复核通道异常不踢连接（票据过期自然兜底）
      }
      const body = (await res.json()) as VerifyResponse;
      const invalid = body.data?.invalid ?? [];
      for (const { sub, rooms } of invalid) {
        for (const conn of this.opts.registry.allConnections()) {
          if (conn.userId !== sub) continue;
          for (const room of rooms) {
            this.opts.registry.leave(conn, room, false); // 静默移出（BR-03）
          }
          if (conn.rooms.size === 0) {
            try {
              conn.ws.close(4003, "FORBIDDEN");
            } catch {
              // 半死连接：close 抛错忽略，GC 兜底
            }
            this.opts.registry.remove(conn, { broadcastPresence: false });
          }
        }
      }
      if (invalid.length > 0) {
        this.opts.logger.warn("verify_rooms_evicted", { invalid_count: invalid.length });
      }
    } catch (err) {
      this.opts.logger.warn("verify_rooms_failed", {
        error: err instanceof Error ? err.message : String(err),
      });
    } finally {
      this.running = false;
    }
  }
}
