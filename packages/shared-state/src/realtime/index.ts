/** Sprint-3 Phase 3-C 实时层入口（COLLAB-004 §4.4）：monorepo-structure.md 登记的
 *  「WebSocket 增量 patch 入口」= packages/shared-state/src/realtime/。 */
export { LiveEventBus, liveEventBus, type LiveHandler } from "./bus";
export {
  RealtimeClient,
  ExponentialBackoff,
  getClientTabId,
  type RealtimeTicket,
  type RealtimeTransport,
  type RealtimeClientOptions,
} from "./client";
export {
  RealtimeStore,
  PresenceStore,
  PRESENCE_KEEP_MS,
  type RealtimeStatus,
  type PresenceEntry,
} from "./stores";
