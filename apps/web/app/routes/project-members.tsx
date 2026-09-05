/** 项目设置·成员 Tab（C.20 · PROJ-002 §3.2）。
 *  - Tab 条「基本｜成员 (N)」（基线=C.8 设置页新增第 2 区块）
 *  - 隐式管理员提示条：WS_ADMIN+ 且无 ProjectMember 行（PermissionStore.inherited）
 *  - 页头「成员（N）」+ 搜索 + 角色 ▾（全部）+ ＋ 添加成员（PermissionGate project.member.manage）
 *  - 成员表五列：成员 / 邮箱 / 项目角色（行内下拉）/ 加入时间 / 操作 ⋯
 *  - 角色四档带能力说明（aria-describedby）；GUEST 行仅 查看者/评论者（BR-05 前端预拦）
 *  - 互改失败回滚 + Toast（BR-12）；移除确认「其名下 N 个任务指派将保留」（BR-07）
 *  - 加载失败：alert-circle + error.message + 重试
 *  - 无管理权（project.member.manage 之下）→ 只读名单（操作列与行内下拉禁用） */
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { Topbar } from "../components/Topbar";
import { ProjectSidebar } from "../components/ProjectSidebar";
import { toast } from "../components/Toast";
import { PermissionGate, usePermission } from "../components/PermissionGate";
import { AddProjectMemberModal } from "../components/AddProjectMemberModal";
import { ConfirmRemoveModal } from "../components/ConfirmRemoveModal";
import { useStores } from "../stores";
import { ProjectAPI, ProjectMemberAPI, WorkspaceMemberAPI, unwrap } from "../services/api";
import { ProjectRole, type ProjectRoleValue } from "../stores/permission";
import type { ProjectMember, ProjectSummary, WorkspaceMember } from "@rp/types";

const PROJ_ROLE: Record<number, { n: string; c: string; desc: string }> = {
  [ProjectRole.ADMIN]:       { n: "管理员", c: "#3B82F6", desc: "可管理项目设置、成员与标签" },
  [ProjectRole.CONTRIBUTOR]: { n: "协作者", c: "#10B981", desc: "可创建与编辑任务、上传附件" },
  [ProjectRole.COMMENTER]:   { n: "评论者", c: "#F59E0B", desc: "可查看并评论，不能改任务" },
  [ProjectRole.VIEWER]:      { n: "查看者", c: "#6B7280", desc: "仅只读" },
};
const PROJ_ROLE_ORDER: readonly ProjectRoleValue[] = [
  ProjectRole.ADMIN, ProjectRole.CONTRIBUTOR, ProjectRole.COMMENTER, ProjectRole.VIEWER,
] as const;
/** BR-05：成员空间角色为 GUEST 时仅这两档（前端预拦） */
const GUEST_ALLOWED: readonly number[] = [ProjectRole.COMMENTER, ProjectRole.VIEWER];

const ROLE_FILTERS = [
  { v: -1, n: "全部" },
  { v: ProjectRole.ADMIN, n: "管理员" },
  { v: ProjectRole.CONTRIBUTOR, n: "协作者" },
  { v: ProjectRole.COMMENTER, n: "评论者" },
  { v: ProjectRole.VIEWER, n: "查看者" },
] as const;

function fmtDate(iso: string | undefined): string {
  return iso ? iso.slice(0, 10) : "—";
}

export default function ProjectMembersPage() {
  return <ProjectMembersInner />;
}

function ProjectMembersInner() {
  const { workspaceSlug, projectId } = useParams<{ workspaceSlug: string; projectId: string }>();
  const { permission } = useStores();
  const canManage = usePermission("project.member.manage", "project", projectId);

  const [proj, setProj] = useState<ProjectSummary | null>(null);
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [wsMembers, setWsMembers] = useState<WorkspaceMember[]>([]);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [errMsg, setErrMsg] = useState("");
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState(-1);
  const [roleMenuFor, setRoleMenuFor] = useState<string | null>(null);
  const [roleFilterMenu, setRoleFilterMenu] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [removing, setRemoving] = useState<ProjectMember | null>(null);
  const [removingBusy, setRemovingBusy] = useState(false);

  // C.20 隐式管理员提示条：WS_ADMIN+ 且无 ProjectMember 行 === 权限快照 inherited
  const implicitAdmin = projectId
    ? permission.snapshot?.projects?.[projectId]?.inherited === true
    : false;

  // 拉取（外部系统同步）。不在入口同步 setState：初始 state 已是 loading，
  // 重试走 retry() 显式置 loading（oxlint set-state-in-effect）。
  const load = useCallback(async () => {
    if (!workspaceSlug || !projectId) return;
    try {
      const [pm, pr, wm] = await Promise.all([
        ProjectMemberAPI.list(workspaceSlug, projectId, { per_page: 100 }),
        ProjectAPI.detail(workspaceSlug, projectId),
        WorkspaceMemberAPI.list(workspaceSlug, { per_page: 100 }),
      ]);
      setMembers(unwrap(pm) ?? []);
      setProj(unwrap(pr) ?? null);
      setWsMembers(unwrap(wm) ?? []);
      setLoadState("ready");
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : "加载失败");
      setLoadState("error");
    }
  }, [workspaceSlug, projectId]);

  const retry = useCallback(() => {
    setLoadState("loading");
    void load();
  }, [load]);

  // 挂载拉取：0ms 定时器把调用挪出 effect 同步段（oxlint set-state-in-effect），
  // 同时天然获得卸载取消（清理函数清掉定时器）。
  useEffect(() => {
    const id = setTimeout(() => { void load(); }, 0);
    return () => clearTimeout(id);
  }, [load]);

  // 下拉外点关闭：mousedown 阶段 + data-sb-scope 范围判定（教训 #4）
  useEffect(() => {
    const onDown = (ev: MouseEvent) => {
      const t = ev.target as HTMLElement;
      if (t.closest('[data-sb-scope="pm-role-menu"]')) return;
      setRoleMenuFor(null);
      if (t.closest('[data-sb-scope="pm-rolef-menu"]')) return;
      setRoleFilterMenu(false);
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, []);

  async function changeRole(m: ProjectMember, next: number) {
    setRoleMenuFor(null);
    if (next === m.role) return;
    const snap = m;
    setMembers((cur) => cur.map((x) => (x.id === m.id ? { ...x, role: next } : x)));
    try {
      const r = await ProjectMemberAPI.patch(workspaceSlug!, projectId!, m.id, { role: next });
      setMembers((cur) => cur.map((x) => (x.id === m.id ? unwrap(r) : x)));
      toast(`已调整为 ${PROJ_ROLE[next]?.n ?? "新角色"}`);
    } catch (e) {
      // BR-12：后端 403/409 拦截 → 前端回滚 + Toast
      setMembers((cur) => cur.map((x) => (x.id === m.id ? snap : x)));
      toast(e instanceof Error ? e.message : "角色调整被拦截", "error");
    }
  }

  async function doRemove() {
    if (!removing) return;
    setRemovingBusy(true);
    try {
      await ProjectMemberAPI.remove(workspaceSlug!, projectId!, removing.id);
      setMembers((cur) => cur.filter((x) => x.id !== removing.id));
      toast(`已移除 ${removing.user.display_name}`);
      setRemoving(null);
    } catch (e) {
      toast(e instanceof Error ? e.message : "移除失败", "error");
    } finally {
      setRemovingBusy(false);
    }
  }

  const kw = search.trim().toLowerCase();
  const shown = members.filter((m) => {
    if (roleFilter >= 0 && m.role !== roleFilter) return false;
    if (!kw) return true;
    return m.user.display_name.toLowerCase().includes(kw) || m.user.email.toLowerCase().includes(kw);
  });

  const base = `/${workspaceSlug}/projects/${projectId}/settings`;
  const name = proj?.name ?? "…";
  const identifier = proj?.identifier ?? "";
  const curFilter = ROLE_FILTERS.find((f) => f.v === roleFilter) ?? ROLE_FILTERS[0];

  return (
    <div className="flex flex-col h-screen">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <ProjectSidebar projectName={name} identifier={identifier} />
        <main className="flex-1 min-w-0 overflow-y-auto bg-neutral-50">
          <div className="max-w-[860px] mx-auto px-6 py-5">
            {/* Tab 条：基本｜成员（基线=C.8） */}
            <div className="flex items-center gap-1 border-b border-neutral-200" role="tablist" aria-label="项目设置">
              <Link to={base} role="tab" aria-selected="false"
                    className="px-3.5 h-9 inline-flex items-center text-[13px] text-neutral-600 hover:bg-neutral-100 rounded-t-md">基本</Link>
              <span role="tab" aria-selected="true"
                    className="px-3.5 h-9 inline-flex items-center text-[13px] text-brand-600 font-medium border-b-2 border-brand-500">
                成员 ({members.length})
              </span>
            </div>

            {/* C.20 隐式管理员提示条（bg-blue-50 text-blue-700） */}
            {implicitAdmin && (
              <div className="mt-4 flex items-center gap-2 bg-brand-50 text-brand-600 border border-brand-100 rounded-md px-3.5 py-2 text-[13px]" role="status">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>
                你以工作空间管理员身份管理此项目
              </div>
            )}

            {/* 页头与筛选（C.20：＋ 添加成员由 PermissionGate 包裹） */}
            <div className="mt-4 flex items-center gap-3 flex-wrap">
              <h1 className="text-[15px] font-semibold text-neutral-900">成员（{members.length}）</h1>
              <div className="flex-1" />
              <div className="relative w-[220px]">
                <input
                  className="w-full h-8 pl-8 pr-3 border border-neutral-300 rounded-md text-[13px] bg-white"
                  placeholder="搜索成员…" value={search}
                  onChange={(e) => setSearch(e.target.value)} aria-label="搜索项目成员"
                />
                <svg className="absolute left-2.5 top-2 text-neutral-400" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>
              </div>
              <div className="relative" data-sb-scope="pm-rolef-menu">
                <button className="h-8 px-2.5 border border-neutral-300 rounded-md bg-white text-[13px] text-neutral-700 inline-flex items-center gap-1.5 hover:bg-neutral-50"
                        aria-haspopup="listbox" onClick={() => setRoleFilterMenu(!roleFilterMenu)}>
                  角色（{curFilter.n}） ▾
                </button>
                {roleFilterMenu && (
                  <div data-sb-scope="pm-rolef-menu" role="listbox"
                       className="absolute top-[calc(100%+4px)] right-0 w-[140px] bg-white border border-neutral-200 rounded-md shadow-lg py-1 z-10">
                    {ROLE_FILTERS.map((f) => (
                      <button key={f.v} role="option" aria-selected={roleFilter === f.v}
                              className={`w-full px-2.5 py-1.5 text-left text-[13px] hover:bg-neutral-50 ${roleFilter === f.v ? "text-brand-600 font-medium" : ""}`}
                              onClick={() => { setRoleFilter(f.v); setRoleFilterMenu(false); }}>
                        {f.n}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              {projectId && (
              <PermissionGate permission="project.member.manage" scope="project" resourceId={projectId} mode="hide">
                <button
                  className="h-8 px-3 inline-flex items-center gap-1.5 bg-brand-500 hover:bg-brand-600 text-white rounded-md text-[13px] font-medium"
                  onClick={() => setAddOpen(true)}
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>
                  添加成员
                </button>
              </PermissionGate>
            )}
            </div>

            {loadState === "loading" && (
              <div className="mt-6 space-y-2" aria-busy="true">
                {[0, 1, 2].map((i) => <div key={i} className="h-12 rounded-md bg-neutral-100 animate-pulse" />)}
              </div>
            )}

            {/* 加载失败：alert-circle + error.message + 重试（C.20） */}
            {loadState === "error" && (
              <div className="mt-8 flex flex-col items-center gap-3 py-10 text-neutral-500">
                <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>
                <p className="text-[13px]">{errMsg}</p>
                <button className="h-8 px-3 border border-neutral-300 rounded-md text-[13px] bg-white hover:bg-neutral-50" onClick={retry}>重试</button>
              </div>
            )}

            {loadState === "ready" && (
              <table className="mt-4 w-full bg-white border border-neutral-200 rounded-lg text-[13px]">
                <thead>
                  <tr className="text-left text-neutral-500 border-b border-neutral-200">
                    <th className="px-3.5 py-2 font-medium">成员</th>
                    <th className="px-3.5 py-2 font-medium">邮箱</th>
                    <th className="px-3.5 py-2 font-medium w-[150px]">项目角色</th>
                    <th className="px-3.5 py-2 font-medium w-[110px]">加入时间</th>
                    <th className="px-3.5 py-2 font-medium w-[70px] text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((m) => {
                    const guest = m.workspace_role === 5; // WorkspaceRole.GUEST（BR-05）
                    const cur = PROJ_ROLE[m.role] ?? { n: "成员", c: "#6B7280", desc: "" };
                    const options = PROJ_ROLE_ORDER.filter((r) => !guest || GUEST_ALLOWED.includes(r));
                    return (
                      <tr key={m.id} className="border-b border-neutral-100 last:border-0">
                        <td className="px-3.5 py-2.5">
                          <div className="flex items-center gap-2">
                            <span className="w-6 h-6 rounded-full bg-brand-100 text-brand-600 text-[11px] font-semibold inline-flex items-center justify-center">
                              {m.user.display_name.slice(0, 1)}
                            </span>
                            <span className="font-medium text-neutral-800">{m.user.display_name}</span>
                          </div>
                        </td>
                        <td className="px-3.5 py-2.5 text-neutral-500">{m.user.email}</td>
                        <td className="px-3.5 py-2.5">
                          <div className="relative" data-sb-scope="pm-role-menu">
                            <button
                              className="h-7 px-2 inline-flex items-center gap-1.5 border border-neutral-300 rounded-md bg-white hover:bg-neutral-50 text-[12px] disabled:opacity-50 disabled:cursor-not-allowed"
                              aria-haspopup="listbox"
                              aria-label={`调整 ${m.user.display_name} 的角色`}
                              aria-describedby="pm-role-cap"
                              disabled={!canManage}
                              onClick={() => setRoleMenuFor(roleMenuFor === m.id ? null : m.id)}
                            >
                              <span className="w-1.5 h-1.5 rounded-full" style={{ background: cur.c }} />
                              {cur.n} ▾
                            </button>
                            {roleMenuFor === m.id && (
                              <div data-sb-scope="pm-role-menu" role="listbox"
                                   className="absolute top-[calc(100%+4px)] left-0 w-[260px] bg-white border border-neutral-200 rounded-md shadow-lg py-1 z-10">
                                {options.map((r) => (
                                  <button key={r} role="option" aria-selected={m.role === r}
                                          className="w-full px-2.5 py-1.5 flex items-start gap-2 text-left text-[13px] hover:bg-neutral-50"
                                          onClick={() => changeRole(m, r)}>
                                    <span className="w-1.5 h-1.5 mt-1.5 rounded-full shrink-0" style={{ background: (PROJ_ROLE[r] ?? cur).c }} />
                                    <span>
                                      <span className="block">{(PROJ_ROLE[r] ?? cur).n}</span>
                                      <span className="block text-[11px] text-neutral-400">{(PROJ_ROLE[r] ?? cur).desc}</span>
                                    </span>
                                  </button>
                                ))}
                              </div>
                            )}
                          </div>
                        </td>
                        <td className="px-3.5 py-2.5 text-neutral-500">{fmtDate(m.joined_at)}</td>
                        <td className="px-3.5 py-2.5 text-right">
                          {canManage && (
                            <button className="w-7 h-7 inline-flex items-center justify-center rounded-md hover:bg-neutral-100 text-neutral-500"
                                    aria-label={`移除 ${m.user.display_name}`} title="移除成员"
                                    onClick={() => setRemoving(m)}>
                              <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                  {shown.length === 0 && (
                    <tr><td colSpan={5} className="px-3.5 py-10 text-center text-neutral-400">
                      {kw ? "未找到匹配的成员" : "暂无成员"}
                    </td></tr>
                  )}
                </tbody>
              </table>
            )}
            <p id="pm-role-cap" className="mt-1.5 text-[12px] text-neutral-500">
              管理员可管理项目设置与成员；协作者可创建与编辑任务；评论者只读并可评论；查看者仅只读。
            </p>
          </div>
        </main>
      </div>

      {addOpen && workspaceSlug && projectId && (
        <AddProjectMemberModal
          workspaceSlug={workspaceSlug}
          projectId={projectId}
          projectName={name}
          workspaceMembers={wsMembers}
          existingUserIds={new Set(members.map((m) => m.user.id))}
          onClose={() => setAddOpen(false)}
          onAdded={retry}
        />
      )}

      {removing && (
        <ConfirmRemoveModal
          subject={{ name: removing.user.display_name, email: removing.user.email }}
          workspaceName={name}
          cascadeText={`其名下 ${removing.assigned_issue_count ?? 0} 个任务指派将保留，以已移出成员展示`}
          loading={removingBusy}
          onConfirm={doRemove}
          onClose={() => setRemoving(null)}
        />
      )}
    </div>
  );
}
