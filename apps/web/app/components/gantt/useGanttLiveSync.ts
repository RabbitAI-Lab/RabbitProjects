/**
 * 甘特实时接线（GANTT-001 BR-14 / GANTT-002 §2.2）——GanttChart 最小触点。
 *
 *  - issue.updated / issue.state.changed / board.moved：BR-14 增量更新对应行——
 *    不做全量刷新；issue.updated(brief=dates) 按 COLLAB-004 §4.4.2 走单实体
 *    增量拉取补日期（GanttStore.onIssueUpdated）；
 *  - BR-08：自己的操作不回显（actor_id === me 过滤——live 已滤，双保险）；
 *  - version 门在 store（ADR-0021 数值化比较，见 GanttStore.onIssueUpdated）；
 *  - 断线补偿：rp:live-reconnected → 视窗轻收敛（ensureDataWindow）。
 *
 * dev-only window.__rpGanttBus：e2e 注入合成事件的测试钩子（version 门突变
 * 自检用；生产构建 import.meta.env.DEV=false 恒不挂载）。
 */
import { useEffect } from "react";
import { liveEventBus } from "@rp/shared-state";
import type { LiveEnvelope } from "@rp/types";
import type { GanttStore } from "../../stores/gantt";

export function useGanttLiveSync(store: GanttStore, meId: string | null): void {
  useEffect(() => {
    const payloadOf = (env: LiveEnvelope): { issue_id?: string; actor_id?: string | null; version?: string } =>
      env.payload as unknown as { issue_id?: string; actor_id?: string | null; version?: string };

    const onUpdated = (env: LiveEnvelope) => {
      const p = payloadOf(env);
      if (!p?.issue_id || p.actor_id === meId) return; // BR-08
      void store.onIssueUpdated(p.issue_id, p.version);
    };
    const onStructural = (env: LiveEnvelope) => {
      // 状态/看板移动影响行集与进度 → 视窗轻收敛（单请求，非全量）
      const p = payloadOf(env);
      if (!p?.issue_id || p.actor_id === meId) return;
      void store.onIssueUpdated(p.issue_id, p.version);
    };
    const onReconnected = () => {
      void store.ensureDataWindow();
    };

    const offs = [
      liveEventBus.on("issue.updated", onUpdated),
      liveEventBus.on("issue.state.changed", onStructural),
      liveEventBus.on("board.moved", onStructural),
    ];
    window.addEventListener("rp:live-reconnected", onReconnected);

    if (import.meta.env.DEV) {
      (window as unknown as { __rpGanttBus?: typeof liveEventBus }).__rpGanttBus = liveEventBus;
    }
    return () => {
      offs.forEach((off) => off());
      window.removeEventListener("rp:live-reconnected", onReconnected);
      if (import.meta.env.DEV) {
        delete (window as unknown as { __rpGanttBus?: typeof liveEventBus }).__rpGanttBus;
      }
    };
  }, [store, meId]);
}
