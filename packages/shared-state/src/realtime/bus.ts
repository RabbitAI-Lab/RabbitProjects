/** LiveEventBus（COLLAB-004 §4.4.2）：RealtimeClient → 各消费方的六类事件 + presence 分派。
 *
 *  设计约束：
 *  - 本包不发起 HTTP（monorepo-structure §4）——RealtimeClient 的换票/续签/健康探测
 *    由 app 层注入 transport（见 client.ts）；
 *  - bus 只承载「提示」语义信封（LiveEnvelope，推送负责知道、拉取负责最终一致）；
 *  - 订阅方自行做 BR-07（version/水位比对）与 BR-08（actor==me 过滤）双保险——
 *    live 服务端已按 actor_id 过滤本人连接，前端再滤一道防中继/多开。
 */
import type { LiveEnvelope, LiveServerEventName } from "@rp/types";

export type LiveHandler = (env: LiveEnvelope) => void;

export class LiveEventBus {
  private handlers = new Map<string, Set<LiveHandler>>();

  /** 订阅某事件名；返回退订函数（React useEffect 清理用）。 */
  on(event: LiveServerEventName, fn: LiveHandler): () => void {
    let set = this.handlers.get(event);
    if (!set) {
      set = new Set();
      this.handlers.set(event, set);
    }
    set.add(fn);
    return () => this.off(event, fn);
  }

  off(event: LiveServerEventName, fn: LiveHandler): void {
    this.handlers.get(event)?.delete(fn);
  }

  emit(event: LiveServerEventName, env: LiveEnvelope): void {
    const set = this.handlers.get(event);
    if (!set) return;
    for (const fn of [...set]) {
      try {
        fn(env);
      } catch {
        // 单个订阅方异常不阻断其它消费方（实时层是加速器，不是数据源）
      }
    }
  }

  listenerCount(event: LiveServerEventName): number {
    return this.handlers.get(event)?.size ?? 0;
  }
}

/** 应用内单例（RealtimeProvider 创建 client 时注入同一实例）。 */
export const liveEventBus = new LiveEventBus();
