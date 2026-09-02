import { Topbar } from "../components/Topbar";
import { Sidebar } from "../components/Sidebar";
import { NotFoundState } from "../components/ErrorStates";

/** 404/无权（AUTH-002 §3.4 + AUTH-003 §3.2：保留全局导航与侧边栏，仅内容区替换）。 */
export default function NotFound() {
  return (
    <div className="flex flex-col h-screen">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <Sidebar workspaceSlug="" />
        <main className="flex-1 min-w-0 overflow-y-auto"><NotFoundState /></main>
      </div>
    </div>
  );
}
