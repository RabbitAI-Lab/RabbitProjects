import { Outlet } from "react-router";
import { RealtimeProvider } from "../realtime/RealtimeProvider";

/** 受保护路由布局：挂 RealtimeClient 单例（COLLAB-004 §4.4——根组件级、路由感知换票）。 */
export default function AppLayout() {
  return (
    <div className="min-h-screen bg-neutral-50">
      <RealtimeProvider>
        <Outlet />
      </RealtimeProvider>
    </div>
  );
}
