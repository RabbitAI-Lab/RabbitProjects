/**
 * RealtimeProvider（COLLAB-004 §4.4）：根组件挂载 RealtimeClient 单例。
 *
 *  - 路由感知 setContext：URL `/:ws/projects/:pid/…` → {projectId, issueIds}；
 *    打开任务抽屉（?peekIssue=）追加 issue 房间；路由切换换票重订（§3.4）。
 *  - transport 注入（shared-state 不发起 HTTP 的纪律在 app 层解除）：换票/续签走
 *    RealtimeAPI；wsUrl/healthUrl 由 config.LIVE_BASE_URL 解析（dev 经 vite /live
 *    代理 upgrade）。
 *  - 重连成功补偿（§4.4.1 compensateAfterReconnect）：广播 `rp:live-reconnected`
 *    DOM 事件——动态流增量 / 看板列收敛 / 通知未读重拉各自订阅（「推送负责知道、
 *    拉取负责最终一致」，全量收敛走既有 SWR/轮询通道，不在此直连业务 store）。
 */
import { createContext, useContext, useEffect, useMemo, useRef, type ReactNode } from "react";
import { useLocation, useSearchParams } from "react-router";
import { liveEventBus, RealtimeClient } from "@rp/shared-state";
import { LIVE_BASE_URL } from "../config";
import { RealtimeAPI, unwrap } from "../services/api";
import { useStores } from "../stores";

/** 重连成功 → 各视图补偿拉取（订阅方：动态流页 / 看板 / 铃铛）。 */
export const LIVE_RECONNECTED_EVENT = "rp:live-reconnected";

export function liveWsUrl(token: string): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}${LIVE_BASE_URL}/connect?token=${encodeURIComponent(token)}`;
}

/** /health 探测地址（BR-10）：缺省 ${LIVE_BASE_URL}/health——dev 由 vite 代理改写
 *  到 live 根路径 /health；生产网关前缀不改写时以 VITE_LIVE_HEALTH_URL 覆盖。 */
export function liveHealthUrl(): string {
  const override = (import.meta as unknown as { env?: Record<string, string> }).env?.VITE_LIVE_HEALTH_URL;
  return override || `${location.origin}${LIVE_BASE_URL}/health`;
}

export const RealtimeClientContext = createContext<RealtimeClient | null>(null);

/** 消费 client（手动重连按钮等）；未挂载时返回 null 安全降级。 */
export function useRealtimeClient(): RealtimeClient | null {
  return useContext(RealtimeClientContext);
}

export function RealtimeProvider({ children }: { children: ReactNode }) {
  const stores = useStores();
  const loc = useLocation();
  const [sp] = useSearchParams();
  /** transport 闭包读取的当前路由 slug（随路由更新，client 只关心 projectId 域）。 */
  const routeRef = useRef<{ slug: string }>({ slug: loc.pathname.split("/")[1] ?? "" });

  const client = useMemo(
    () =>
      new RealtimeClient({
        transport: {
          fetchToken: async (ctx) => {
            const r = await RealtimeAPI.token(routeRef.current.slug, ctx.projectId, {
              client_tab_id: ctx.clientTabId,
              issue_rooms: ctx.issueIds,
              ...(ctx.fileIds.length ? { file_rooms: ctx.fileIds } : {}),
            });
            return unwrap(r);
          },
          renewTicket: async (body) => {
            const r = await RealtimeAPI.renew(body);
            return unwrap(r);
          },
          wsUrl: liveWsUrl,
          healthUrl: liveHealthUrl,
          onReconnected: () => {
            window.dispatchEvent(new CustomEvent(LIVE_RECONNECTED_EVENT));
          },
          warn: () => { /* 诊断静默：换票/续签失败由状态机与降级横幅表达 */ },
        },
        bus: liveEventBus,
        store: stores.realtime,
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [stores.realtime],
  );

  // presence 表绑定 bus（presence.joined/left → PresenceStore，BR-11）
  useEffect(() => stores.presence.bindBus(liveEventBus), [stores.presence]);

  // 路由感知上下文：项目房间 + 打开的任务抽屉（?peekIssue= → issue 房间）+
  // 打开的预览抽屉（?previewFile= → file 房间，FILE-003 §4.4 第四类房间）
  const m = loc.pathname.match(/^\/([^/]+)\/projects\/([^/]+)/);
  const projectId = m?.[2] ?? null;
  const issueId = sp.get("peekIssue");
  const previewFileId = sp.get("previewFile");
  useEffect(() => {
    if (m?.[1]) routeRef.current.slug = m?.[1];
    if (!projectId || !stores.session.user) {
      client.clearContext();
      return;
    }
    void client.setContext({
      projectId,
      issueIds: issueId ? [issueId] : [],
      fileIds: previewFileId ? [previewFileId] : [],
    });
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, issueId, previewFileId, stores.session.user?.id, client]);

  // 卸载销毁（页面 unload 时连接随标签页关闭，服务端 60s 心跳自然清理）
  useEffect(() => () => client.destroy(), [client]);

  return <RealtimeClientContext.Provider value={client}>{children}</RealtimeClientContext.Provider>;
}
