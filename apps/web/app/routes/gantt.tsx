import { useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router";
import { observer } from "mobx-react-lite";
import { Topbar } from "../components/Topbar";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { IssueDrawer as SharedDrawer } from "../components/IssueDrawer";
import { ProjectAPI, ViewAPI } from "../services/api";
import { ViewSwitchBar } from "../components/views/ViewSwitchBar";
import { useViewPage } from "../components/views/useViewPage";
import { GanttStore } from "../stores/gantt";
import { GanttToolbar } from "../components/gantt/GanttToolbar";
import { OverdueBar } from "../components/gantt/OverdueBar";
import { CriticalPathBar } from "../components/gantt/CriticalPathBar";
import { GanttChart } from "../components/gantt/GanttChart";
import { useGanttLiveSync } from "../components/gantt/useGanttLiveSync";
import { useStores } from "../stores";

/** 甘特视图页（GANTT-001 §3.1 / 原型 O1 工具条分层定稿）：
 *  视图条（BOARD-003 框架，layout=gantt 高亮）→ 甘特控制条 → 延期概览条 → 图表区。
 *  取数与交互全部经 GanttStore（视窗状态机——见 stores/gantt.ts 头注）。 */
const GanttPage = observer(function Gantt() {
  const { workspaceSlug, projectId } = useParams<{ workspaceSlug: string; projectId: string }>();
  const vp = useViewPage({ workspaceSlug, projectId, layout: "gantt" });
  const stores = useStores();
  const [projName, setProjName] = useState("…");
  const [projIdentifier, setProjIdentifier] = useState("");
  const myRole = stores.permission.effectiveProjectRole(projectId, workspaceSlug);
  // 快照未 hydrate 时 fail-open（写边界在后端；同 table.tsx S3V-3 回归口径）
  const canEdit = stores.permission.snapshot === null || myRole >= 15;
  const userName = stores.session.user?.display_name ?? "我";
  const meId = stores.session.user?.id ?? null;

  /** ?peekIssue 双向同步（条点击 → 任务详情 Drawer，复用 TASK-001，URL 可分享）。 */
  const [sp, setSp] = useSearchParams();
  const peekId = sp.get("peekIssue");
  function openPeek(id: string) {
    setSp((prev) => { const n = new URLSearchParams(prev); n.set("peekIssue", id); return n; }, { preventScrollReset: true });
  }
  function closePeek() {
    setSp((prev) => { const n = new URLSearchParams(prev); n.delete("peekIssue"); return n; }, { preventScrollReset: true });
  }

  useEffect(() => {
    if (!workspaceSlug || !projectId) return;
    ProjectAPI.detail(workspaceSlug, projectId)
      .then((r) => {
        const d = (r as unknown as { data: { name?: string; identifier?: string } }).data;
        setProjName(d?.name ?? "…");
        setProjIdentifier(d?.identifier ?? "");
      })
      .catch(() => {});
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceSlug, projectId]);

  /** GanttStore 生命周期：项目切换重建；视图/筛选变更热替换 ctx（TASK-011 三源同源）。 */
  const storeRef = useRef<{ store: GanttStore; proj: string | undefined } | null>(null);
  if (storeRef.current === null || storeRef.current.proj !== projectId) {
    storeRef.current = {
      store: new GanttStore({
        slug: workspaceSlug ?? "",
        projectId: projectId ?? "",
        listParams: {},
        canDrag: () => canEdit && window.innerWidth >= 1024,
      }),
      proj: projectId,
    };
  }
  const store = storeRef.current.store;

  const firstCtx = useRef(true);
  useEffect(() => {
    const listParams = {
      ...(vp.viewIdParam ? { view_id: vp.viewIdParam } : {}),
    };
    if (firstCtx.current) { firstCtx.current = false; return; }
    store.setCtx(
      {
        slug: workspaceSlug ?? "",
        projectId: projectId ?? "",
        listParams,
        canDrag: () => canEdit && window.innerWidth >= 1024,
        onCollapseChange: (ids) => {
          // BR-10 折叠持久化：display_props.collapsed（「全部」无视图存档 = 仅本地态）
          const view = vp.currentView;
          if (!view) return;
          void ViewAPI.patch(workspaceSlug!, projectId!, view.id, {
            display_props: { ...(view.display_props ?? {}), collapsed: ids },
          }).catch(() => { /* 保存失败保持本地态 */ });
        },
      },
      true,
    );
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [vp.viewIdParam, vp.filtersParam, workspaceSlug, projectId]);

  useGanttLiveSync(store, meId);

  const memberName = (uid: string) => vp.members.find((m) => m.id === uid)?.name ?? "…";
  const viewName = vp.currentView?.name ?? "全部";
  const onJump = (issueId: string) => {
    store.selectRow(issueId);
    const idx = store.visibleRows().findIndex((r) => r.id === issueId);
    if (idx >= 0) {
      const el = document.querySelector('[data-sb-scope="gantt-body"]') as HTMLElement | null;
      if (el) el.scrollTop = Math.max(0, idx * 36 - el.clientHeight / 2);
    }
  };

  /** 导出入口桥：工具条 ⋯/⌘E → 图表侧执行（含 >200 行确认与容器引用）。 */
  const exportFnRef = useRef<() => void>(() => {});
  /** GANTT-003：关键路径高亮集合（CriticalPathBar 开关驱动）。 */
  const [cpIds, setCpIds] = useState<Set<string>>(new Set());
  const [cpOn, setCpOn] = useState(false);

  return (
    <div className="flex flex-col h-screen">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <ProjectSidebar projectName={projName} identifier={projIdentifier} />
        <main className="flex-1 min-w-0 flex flex-col overflow-hidden">
          {/* C.98 工具条分层：视图条 → 甘特控制条 → 延期概览条 → 图表区（O1 定稿） */}
          <ViewSwitchBar vp={vp} />
          <GanttToolbar store={store} exportHint="（⌘/Ctrl+E）" onExport={() => exportFnRef.current()} />
          <OverdueBar store={store} memberName={memberName} onJump={onJump} />
          <CriticalPathBar onCriticalChange={(ids, on2) => { setCpIds(ids); setCpOn(on2); }} />
          <GanttChart
            store={store}
            memberName={memberName}
            openPeek={openPeek}
            onOpenList={() => { window.location.assign(`/${workspaceSlug}/projects/${projectId}/issues`); }}
            projectName={projName}
            viewName={viewName}
            userName={userName}
            canEdit={canEdit}
            exportFnRef={exportFnRef}
            criticalIds={cpIds}
            criticalOn={cpOn}
          />
          {peekId && (
            <SharedDrawer issueId={peekId} slug={workspaceSlug!} projectId={projectId!} onClose={closePeek} onChanged={() => {}} />
          )}
        </main>
      </div>
    </div>
  );
});

export default GanttPage;
