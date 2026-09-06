/** RealtimeClient（COLLAB-004 §4.4.1）：连接 / 换票 / 续签 / 退避重连 / 降级探测 / 补偿拉取。
 *
 *  分层纪律（§4.4 两层调和）：
 *  - 本 client 只做「连接与提示」——收到信封即分派 LiveEventBus，业务定向 patch 由
 *    各消费方订阅完成；正文补齐一律走 REST 拉取（红线一：推送是提示不是数据源）；
 *  - 心跳（25s 服务端 ping，浏览器协议层自动 pong）与续签（90s renew_after，BR-02
 *    分层独立、不共用定时器）各自维护；
 *  - onclose 4001 重换票 ≤2 次（BR-01）；网络/心跳断开走指数退避 1s→30s（±20% 抖动）；
 *  - 连接失败累计 30s 或 /health 探测失败 → degraded（BR-10 横幅 + 轮询模式），
 *    探测恢复 / online 事件 → 跳过退避立即重连；重连成功触发一次补偿拉取
 *    （transport.onReconnected——「推送负责知道、拉取负责最终一致」）。
 *
 *  路由切换换票重订（§1.3 / §3.4）：live 房间在连接握手时按票据 rooms 声明加入，
 *  无运行时改订协议——换票后以同 wsKey（{sub}:{client_tab_id}）重连，live 侧对同键
 *  旧连接幂等替换（BR-12），旧房随旧连接关闭自动退出（kick 1000 REPLACED 不触发重连）。
 */
import type { ConnectedPayload, LiveEnvelope, LiveServerEventName } from "@rp/types";
import { LIVE_CLOSE_CODES } from "@rp/types";
import type { LiveEventBus } from "./bus";
import type { RealtimeStore } from "./stores";

/** 换票/续签响应（§4.2.1 成功响应 data）。 */
export interface RealtimeTicket {
  token: string;
  rooms: string[];
  expires_at: string;
  renew_after: number;
}

/** app 层注入的传输适配（本包不发起 HTTP——monorepo-structure §4）。 */
export interface RealtimeTransport {
  /** POST …/projects/{pid}/realtime-token/（换票）。 */
  fetchToken(ctx: { projectId: string; issueIds: string[]; clientTabId: string }): Promise<RealtimeTicket>;
  /** POST /users/me/realtime-token/renew/（续签，旧 jti 轮换）。 */
  renewTicket(body: { token: string; client_tab_id: string; issue_rooms?: string[] }): Promise<RealtimeTicket>;
  /** WSS 连接地址（VITE_LIVE_BASE_URL 解析在 app 层 config）。 */
  wsUrl(token: string): string;
  /** /health 探测（degraded 期每 30s；恢复 → 立即重连，BR-10）。 */
  healthUrl?(): string;
  /** 重连成功补偿拉取钩子（§4.4.1 compensateAfterReconnect）。 */
  onReconnected?(): void;
  /** 诊断日志（不抛出）。 */
  warn?(msg: string, detail?: unknown): void;
}

/** 指数退避（§2.6：1s 起 ×2 至 30s 封顶，±20% 抖动——UT-11 曲线）。 */
export class ExponentialBackoff {
  private attempt = 0;
  constructor(
    private readonly opts = { baseMs: 1_000, maxMs: 30_000, jitterRatio: 0.2 },
  ) {}

  reset(): void {
    this.attempt = 0;
  }

  next(): number {
    const raw = Math.min(this.opts.baseMs * 2 ** this.attempt, this.opts.maxMs);
    this.attempt = Math.min(this.attempt + 1, 16);
    const jitter = 1 + (Math.random() * 2 - 1) * this.opts.jitterRatio;
    return Math.min(Math.round(raw * jitter), this.opts.maxMs);
  }
}

/** client_tab_id：每标签页一次（sessionStorage 持久，§4.2.1——BR-12 去重/重连幂等键）。 */
export function getClientTabId(): string {
  try {
    const KEY = "rp:client-tab-id";
    let v = sessionStorage.getItem(KEY);
    if (!v) {
      v = typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
      sessionStorage.setItem(KEY, v);
    }
    return v;
  } catch {
    // 隐私模式等 sessionStorage 不可用：退化为内存值（同一 page 生命周期内稳定）
    return `tab-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
  }
}

/** 连接失败累计 30s → 降级（BR-10）。 */
const DEGRADE_AFTER_MS = 30_000;
/** /health 探测周期（degraded 期）。 */
const HEALTH_PROBE_MS = 30_000;
/** 续签连续失败 2 次主动断开（BR-02 / UT-15）。 */
const RENEW_FAIL_LIMIT = 2;
/** 4001 重换票上限（BR-01）。 */
const TOKEN_RETRY_LIMIT = 2;

export interface RealtimeClientOptions {
  transport: RealtimeTransport;
  bus: LiveEventBus;
  store: RealtimeStore;
  clientTabId?: string;
  now?: () => number;
}

export class RealtimeClient {
  private readonly transport: RealtimeTransport;
  private readonly bus: LiveEventBus;
  private readonly store: RealtimeStore;
  private readonly clientTabId: string;
  private readonly now: () => number;

  private ws: WebSocket | null = null;
  private backoff = new ExponentialBackoff();
  private ticket: RealtimeTicket | null = null;
  private ctx: { projectId: string; issueIds: string[] } | null = null;

  private renewTimer: ReturnType<typeof setInterval> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private healthTimer: ReturnType<typeof setInterval> | null = null;
  private degradeTimer: ReturnType<typeof setTimeout> | null = null;
  private renewFailures = 0;
  private tokenRetries = 0;
  private failingSince: number | null = null;
  /** 主动关闭（上下文切换/销毁）——不触发重连。 */
  private closedByUs = false;
  /** 同 wsKey 幂等替换（换票重订，BR-12）：旧连接 1000 REPLACED 不算断线。 */
  private replacing = false;
  private latencyPingAt: number | null = null;
  private hadConnection = false;
  private onlineBound = false;
  /** /health 预检通过时刻（25s 内缓存——避免路由切换/重试循环的探测风暴）。 */
  private lastProbeOkAt = 0;

  constructor(opts: RealtimeClientOptions) {
    this.transport = opts.transport;
    this.bus = opts.bus;
    this.store = opts.store;
    this.clientTabId = opts.clientTabId ?? getClientTabId();
    this.now = opts.now ?? (() => Date.now());
  }

  get connected(): boolean {
    return this.ws != null && this.ws.readyState === WebSocket.OPEN;
  }

  // ── 上下文（路由切换换票重订，§1.3）──

  async setContext(ctx: { projectId: string; issueIds: string[] }): Promise<void> {
    const sameRooms =
      this.ctx?.projectId === ctx.projectId &&
      JSON.stringify([...this.ctx.issueIds].sort()) === JSON.stringify([...ctx.issueIds].sort());
    this.ctx = ctx;
    if (sameRooms && this.connected) return; // 房间未变：连接复用不重建
    await this.reticket();
  }

  clearContext(): void {
    this.ctx = null;
    this.closedByUs = true;
    this.stopTimers();
    this.ws?.close(1000, "CONTEXT_CLEAR");
    this.ws = null;
    this.ticket = null;
    this.store.reset();
  }

  /** 换票 + （重）连接。 */
  private async reticket(): Promise<void> {
    if (!this.ctx) return;
    this.closedByUs = true;
    this.stopTimers();
    try {
      this.ws?.close(1000, "RETICKET");
    } catch { /* 半死 socket */ }
    this.ws = null;
    this.replacing = true;
    await this.exchangeTicket();
  }

  /** 换票 → connect；失败走退避重连循环。
   *  预检（BR-10 后半句）：未连接期先探 /live/health，不可达直接降级——不空跑
   *  换票与 WS（live 未起/密钥未配的 dev 环境零无效请求）。 */
  private async exchangeTicket(): Promise<void> {
    if (!this.ctx) return;
    if (!this.connected && this.transport.healthUrl && this.now() - this.lastProbeOkAt > 25_000) {
      const ok = await this.probeOnce();
      if (!ok) {
        this.markFailure();
        this.store.setLastError("health_down");
        this.store.setStatus("degraded");
        this.startHealthProbe(); // 30s 周期探测，恢复即重连（reconnectNow）
        return;
      }
    }
    try {
      this.ticket = await this.transport.fetchToken({
        projectId: this.ctx.projectId,
        issueIds: this.ctx.issueIds,
        clientTabId: this.clientTabId,
      });
      this.connect();
    } catch (e) {
      this.transport.warn?.("realtime_ticket_failed", e);
      this.store.setLastError("ticket_failed");
      this.scheduleReconnect();
    }
  }

  /** /health 单次探测（fetch；探测通过缓存 25s）。 */
  private async probeOnce(): Promise<boolean> {
    const url = this.transport.healthUrl?.();
    if (!url) return true;
    try {
      const r = await fetch(url, { method: "GET" });
      if (r.ok) this.lastProbeOkAt = this.now();
      return r.ok;
    } catch {
      return false;
    }
  }

  /** 退避后重连：每次换新票据（§2.2「换新票据」）。 */
  private async openFreshTicketed(): Promise<void> {
    await this.exchangeTicket();
  }

  // ── 连接 ──

  private connect(): void {
    if (!this.ticket) return;
    this.closedByUs = false;
    // 重连尝试期的三态：未失败过=connecting；失败窗口 <30s=reconnecting；≥30s 维持 degraded（横幅不闪断）
    const failing = this.failingSince != null && this.now() - this.failingSince >= DEGRADE_AFTER_MS;
    this.store.setStatus(this.failingSince != null ? (failing ? "degraded" : "reconnecting") : "connecting");
    // replacing 只覆盖「reticket 发起 → 新 socket 创建」窗口：socket 一旦创建，
    // 其 close 必须走状态机（外部 1006 不得被吞——否则退避循环死端）
    this.replacing = false;
    const ws = new WebSocket(this.transport.wsUrl(this.ticket.token));
    this.ws = ws;
    // 连接超时兜底：网关挂起 upgrade（vite proxy 上游失联实测不回 close 帧）时
    // readyState 恒 CONNECTING——10s 未 open 主动按断线处理，交回退避/降级状态机
    const connectTimer = setTimeout(() => {
      if (this.ws !== ws || ws.readyState === WebSocket.OPEN) return;
      this.ws = null;
      this.stopTimers();
      try { ws.close(); } catch { /* CONNECTING 态 close 失败由 GC 兜底 */ }
      if (!this.closedByUs) this.scheduleReconnect();
    }, 10_000);
    ws.onopen = () => {
      if (this.ws !== ws) return;
      clearTimeout(connectTimer);
      this.backoff.reset();
      this.tokenRetries = 0;
      const wasFailure = this.failingSince != null;
      this.failingSince = null;
      this.clearDegradeTimer();
      this.stopHealthProbe();
      this.hadConnection = true;
      this.store.setLastConnectedNow();
      this.store.setStatus("connected");
      this.startRenewTimer();
      if (wasFailure) {
        this.store.bumpReconnects();
        // 断线窗口补偿（§2.2/§4.4.1）：推送只负责「知道」，拉取负责最终一致
        try {
          this.transport.onReconnected?.();
        } catch { /* 补偿失败由 SWR 轮询兜底 */ }
      }
    };
    ws.onmessage = (ev: MessageEvent) => {
      if (this.ws !== ws) return;
      const raw = typeof ev.data === "string" ? ev.data : "";
      if (raw === "pong") {
        if (this.latencyPingAt != null) this.store.setLatency(Math.max(0, this.now() - this.latencyPingAt));
        this.latencyPingAt = null;
        return;
      }
      let env: LiveEnvelope | null = null;
      try {
        env = JSON.parse(raw) as LiveEnvelope;
      } catch {
        return;
      }
      if (!env || typeof env.event !== "string") return;
      if (env.event === "connected") {
        const p = (env.payload ?? {}) as unknown as ConnectedPayload;
        this.store.setRooms(p.rooms ?? []);
        this.store.setLatency(null);
        this.startPingTimer(p.heartbeat ?? 25);
        return;
      }
      this.bus.emit(env.event as LiveServerEventName, env);
    };
    ws.onclose = (ev: CloseEvent) => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.stopTimers();
      if (this.closedByUs) return;
      if (this.replacing || ev.code === 1000) {
        // 同 wsKey 幂等替换（BR-12）：旧连接被本标签页的新连接顶替（1000 REPLACED）
        // ——非故障不重连（防同键互踢循环）；replacing 已在 connect() 入口复位，
        // 此分支仅剩服务端 REPLACED 帧一种来源
        this.replacing = false;
        return;
      }
      if (ev.code === LIVE_CLOSE_CODES.TOKEN_INVALID) {
        void this.refreshTicketAndReconnect(); // BR-01：≤2 次后降级
        return;
      }
      if (ev.code === LIVE_CLOSE_CODES.DUP_SESSION) {
        this.store.setLastError("dup_session"); // 第 6 条连接被踢（§2.5）
        this.scheduleReconnect();
        return;
      }
      if (ev.code === LIVE_CLOSE_CODES.FORBIDDEN) {
        // 权限失效（被移出，§2.5）：数据刷新后 404 导出——不激进重连
        this.store.setLastError("forbidden");
        this.markFailure();
        this.store.setStatus("degraded");
        this.startHealthProbe();
        return;
      }
      this.scheduleReconnect();
    };
    ws.onerror = () => { /* close 随后到达，统一在 onclose 处理 */ };
  }

  /** 4001 重换票（≤2 次）→ 仍失败转退避。 */
  private async refreshTicketAndReconnect(): Promise<void> {
    if (this.tokenRetries < TOKEN_RETRY_LIMIT) {
      this.tokenRetries += 1;
      await this.exchangeTicket();
      return;
    }
    this.tokenRetries = 0;
    this.scheduleReconnect();
  }

  // ── 退避重连 / 降级（§2.2 / BR-10）──

  private scheduleReconnect(immediate = false): void {
    this.markFailure();
    this.store.setStatus(this.failingSince != null && this.now() - this.failingSince >= DEGRADE_AFTER_MS ? "degraded" : "reconnecting");
    if (this.reconnectTimer != null) clearTimeout(this.reconnectTimer);
    const delay = immediate ? 0 : this.backoff.next();
    this.reconnectTimer = setTimeout(() => { void this.openFreshTicketed(); }, delay);
    this.armDegradeTimer();
    this.startHealthProbe();
    this.bindOnline();
  }

  private markFailure(): void {
    if (this.failingSince == null) this.failingSince = this.now();
  }

  /** 失败窗口跨越 30s 阈值时翻 degraded（长退避等待期也要翻）。 */
  private armDegradeTimer(): void {
    if (this.failingSince == null || this.degradeTimer != null) return;
    const wait = DEGRADE_AFTER_MS - (this.now() - this.failingSince);
    this.degradeTimer = setTimeout(() => {
      this.degradeTimer = null;
      if (this.failingSince != null && !this.connected) this.store.setStatus("degraded");
    }, Math.max(wait, 0));
  }

  private clearDegradeTimer(): void {
    if (this.degradeTimer != null) {
      clearTimeout(this.degradeTimer);
      this.degradeTimer = null;
    }
  }

  /** /health 每 30s 探测（degraded/重连期）；恢复 → 跳过退避立即重连（BR-10）。 */
  private startHealthProbe(): void {
    if (this.healthTimer != null || !this.transport.healthUrl) return;
    this.healthTimer = setInterval(() => {
      const url = this.transport.healthUrl?.();
      if (!url) return;
      fetch(url, { method: "GET" })
        .then((r) => {
          if (r.ok && !this.connected) this.reconnectNow();
        })
        .catch(() => { /* 探测失败保持现状，下一轮再试 */ });
    }, HEALTH_PROBE_MS);
  }

  private stopHealthProbe(): void {
    if (this.healthTimer != null) {
      clearInterval(this.healthTimer);
      this.healthTimer = null;
    }
  }

  /** online 事件 / 手动「立即重连」：跳过退避等待（§3.4）。 */
  reconnectNow(): void {
    this.backoff.reset();
    if (this.reconnectTimer != null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    void this.openFreshTicketed();
  }

  private bindOnline(): void {
    if (this.onlineBound) return;
    this.onlineBound = true;
    window.addEventListener("online", () => {
      if (!this.connected) this.reconnectNow();
    });
  }

  // ── 续签与心跳（分层独立，BR-02）──

  private startRenewTimer(): void {
    this.stopRenewTimer();
    const seconds = this.ticket?.renew_after ?? 90;
    this.renewTimer = setInterval(() => { void this.renew(); }, seconds * 1000);
  }

  private stopRenewTimer(): void {
    if (this.renewTimer != null) {
      clearInterval(this.renewTimer);
      this.renewTimer = null;
    }
  }

  private async renew(): Promise<void> {
    if (!this.ticket || !this.connected) return;
    try {
      this.ticket = await this.transport.renewTicket({
        token: this.ticket.token,
        client_tab_id: this.clientTabId,
        ...(this.ctx && this.ctx.issueIds.length ? { issue_rooms: this.ctx.issueIds } : {}),
      });
      this.renewFailures = 0;
    } catch (e) {
      this.renewFailures += 1;
      this.transport.warn?.("realtime_renew_failed", e);
      if (this.renewFailures >= RENEW_FAIL_LIMIT) {
        // UT-15：连续 2 次失败主动断开，走重连（不等票据自然过期）
        this.renewFailures = 0;
        try {
          this.ws?.close(4000, "RENEW_FAILED");
        } catch { /* ignore */ }
      }
    }
  }

  /** 应用层 ping（connected 帧的 heartbeat 秒）——测 RTT；协议层 pong 由浏览器自动应答。 */
  private startPingTimer(seconds: number): void {
    this.stopPingTimer();
    this.pingTimer = setInterval(() => {
      if (!this.ticket || !this.connected) return;
      this.latencyPingAt = this.now();
      try {
        this.ws?.send("ping");
      } catch { /* ignore */ }
    }, Math.max(seconds, 5) * 1000);
  }

  private stopPingTimer(): void {
    if (this.pingTimer != null) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  private stopTimers(): void {
    this.stopRenewTimer();
    this.stopPingTimer();
    if (this.reconnectTimer != null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.clearDegradeTimer();
  }

  /** 卸载（Provider unmount）。 */
  destroy(): void {
    this.clearContext();
    this.stopHealthProbe();
  }
}
