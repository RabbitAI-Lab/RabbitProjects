/** AUTH-005 §4.5.1 权限矩阵前端常量化 + MobX 存储。
 *
 *  ⚠ 真相源唯一：apps/api/plane/constants/permissions.py
 *  这份常量必须与后端 PERMISSION_MATRIX / PERMISSION_LABELS 严格同源。
 *  Sprint 2+ 由 scripts/gen-permissions.mjs 从后端常量生成（§4.6 C1），
 *  当前 P1 阶段手工镜像，CI 跑 C1 检查时再切到生成物。
 *
 *  BR-09 / BR-10 fail-closed：
 *  - is_system_admin 短路放行（与后端第二层一致）
 *  - me=null 时 can() 恒 false（未加载 / 加载失败时宁可少显示）
 *
 *  推导语义（§2.5 R1~R4、§4.3.2、§4.5.1）：
 *  effectiveProjectRole = max(显式 ProjectMember 行, workspace 隐式提升)
 *  其中 workspace 隐式提升 = WS_ADMIN+(≥15) → ProjectRole.ADMIN(20)
 */
import { action, computed, makeObservable, observable, runInAction } from "mobx";
import { PermissionsAPI, setPermissionsRevalidator, type PermissionSnapshot } from "../services/api";
import type { RootStore } from "./index";

/** 角色等级值（rbac §2、AUTH-005 §1.3.1）。前端不在此引入业务方法，只用于查表与整数比较。 */
export const WorkspaceRole = { GUEST: 5, MEMBER: 10, ADMIN: 15, OWNER: 20 } as const;
export const ProjectRole = { VIEWER: 5, COMMENTER: 10, CONTRIBUTOR: 15, ADMIN: 20 } as const;

/** 角色取值联合类型。
 *  用它做 `Record<ProjectRoleValue, …>` 的键，查表就是全覆盖的——否则在
 *  `noUncheckedIndexedAccess` 下 `Record<number, …>` 索引出来是 `T | undefined`，
 *  每个查表点都得加断言；顺带还能防住把非法整数写进角色 state。 */
export type WorkspaceRoleValue = (typeof WorkspaceRole)[keyof typeof WorkspaceRole];
export type ProjectRoleValue = (typeof ProjectRole)[keyof typeof ProjectRole];

export type Scope = "workspace" | "project";

/** 权限矩阵（域 → 权限点 → 最低角色）。与后端 PERMISSION_MATRIX 严格同源（AUTH-005 §4.4 / §4.6 C1）。
 *  「as const」让 keys 自然收窄成 PermissionKey 联合类型 —— PermissionGate 的 permission
 *  prop 类型即由此派生（BR-06：未登记 key 编译失败）。 */
export const PERMISSION_MATRIX = {
  workspace: {
    "workspace.read": WorkspaceRole.GUEST,
    "workspace.update": WorkspaceRole.ADMIN,
    "workspace.setting.manage": WorkspaceRole.ADMIN,
    "workspace.member.read": WorkspaceRole.MEMBER,
    "workspace.member.invite": WorkspaceRole.ADMIN,
    "workspace.member.manage": WorkspaceRole.ADMIN,
    "workspace.member.remove": WorkspaceRole.ADMIN,
    "workspace.member.leave": WorkspaceRole.MEMBER,
    "workspace.transfer": WorkspaceRole.OWNER,
    "project.create": WorkspaceRole.MEMBER,
  },
  project: {
    "project.read": ProjectRole.VIEWER,
    "project.update": ProjectRole.ADMIN,
    "project.delete": ProjectRole.ADMIN,
    "project.member.read": ProjectRole.VIEWER,
    "project.member.manage": ProjectRole.ADMIN,
    "project.favorite": ProjectRole.VIEWER,
    "project.archive": ProjectRole.ADMIN,
    "project.label.manage": ProjectRole.ADMIN,
    "issue.create": ProjectRole.CONTRIBUTOR,
    "issue.update": ProjectRole.CONTRIBUTOR,
    "issue.state.transition": ProjectRole.CONTRIBUTOR,
    "issue.delete": ProjectRole.ADMIN,
    "issue.delete.own": ProjectRole.CONTRIBUTOR,
    "comment.create": ProjectRole.COMMENTER,
    "file.upload": ProjectRole.CONTRIBUTOR,
  },
} as const;

/** 全部权限点 key 的联合类型（AUTH-005 §4.4 / §4.5.1 PermissionKey）。
 *  联合类型外的字符串赋给 PermissionGate.permission 会编译失败（BR-06 前端侧）。 */
export type PermissionKey = keyof typeof PERMISSION_MATRIX["workspace"] | keyof typeof PERMISSION_MATRIX["project"];

/** 权限点中文名（AUTH-005 §3.3 / §4.4 PERMISSION_LABELS）。403 页按 URL 参数渲染中文名，
 *  禁止裸露英文 key —— 未登记的 key 兜底「访问该页面」。 */
export const PERMISSION_LABELS: Record<string, string> = {
  // workspace 域
  "workspace.read": "查看团队",
  "workspace.update": "编辑团队信息",
  "workspace.setting.manage": "管理团队设置",
  "workspace.member.read": "查看成员列表",
  "workspace.member.invite": "邀请成员",
  "workspace.member.manage": "管理成员角色",
  "workspace.member.remove": "移除成员",
  "workspace.member.leave": "退出团队",
  "workspace.transfer": "转让所有权",
  "project.create": "创建项目",
  // project 域
  "project.read": "查看项目",
  "project.update": "编辑项目设置",
  "project.delete": "删除项目",
  "project.member.read": "查看项目成员",
  "project.member.manage": "管理项目成员",
  "project.favorite": "收藏项目",
  "project.archive": "归档或恢复项目",
  "project.label.manage": "管理项目标签",
  "issue.create": "创建任务",
  "issue.update": "编辑任务",
  "issue.state.transition": "流转任务状态",
  "issue.delete": "删除任务",
  "issue.delete.own": "删除自己创建的任务",
  "comment.create": "发表评论",
  "file.upload": "上传文件",
};

export class PermissionStore {
  /** 当前快照。AUTH-005 §2.3 状态机：
   *  null = Unloaded / LoadFailed → fail-closed，can() 恒 false（BR-10）。
   *  非 null = Loaded；meta.generated_at 供调试 / 截断判断。 */
  snapshot: PermissionSnapshot | null = null;
  /** 加载中：触发 Gate 渲染骨架（BR-11 / §3.4「Gate 加载骨架」）。 */
  loading = false;

  constructor(private root: RootStore) {
    makeObservable(this, {
      snapshot: observable,
      loading: observable,
      isSystemAdmin: computed,
      truncated: computed,
      hydrate: action,
      setLoading: action,
      reset: action,
    });
    // AUTH-005 §2.2 / §3.4：注册到 axios 拦截器；PERM_* 403 触发静默重拉，无 toast。
    // 该回调在 axios response interceptor `triggerPermissionsRevalidate()` 处被调用。
    setPermissionsRevalidator(() => {
      this.refetch().catch(() => { /* 重拉失败仍维持旧快照，避免 UI 闪烁 */ });
    });
  }

  get isSystemAdmin(): boolean {
    return this.snapshot?.is_system_admin ?? false;
  }

  get truncated(): boolean {
    return this.snapshot?.meta?.truncated ?? false;
  }

  setLoading(v: boolean) {
    this.loading = v;
  }

  hydrate = (data: PermissionSnapshot) =>
    runInAction(() => {
      this.snapshot = data;
      this.loading = false;
    });

  reset = () =>
    runInAction(() => {
      this.snapshot = null;
      this.loading = false;
    });

  /** 拉取当前快照。AUTH-005 §2.6：
   *  - 登录后 / 页面刷新 → bootstrap 路径调用
   *  - 收到 PERM_* 403 → axios 拦截器触发（§3.4 静默无 toast）
   *  - 退出登录 → 不调用此函数；由 reset() 清空 + setPermissionsRevalidator(null) 解绑 */
  async refetch(workspaceSlug?: string): Promise<void> {
    this.setLoading(true);
    try {
      const r = await PermissionsAPI.my(workspaceSlug);
      this.hydrate((r as any).data as PermissionSnapshot);
    } finally {
      this.setLoading(false);
    }
  }

  /** 工作空间角色：AUTH-005 §4.5.1 —— 优先 id 精确匹配，退化为当前路由 slug 解析。
   *  返回 -1 = 未知（fail-closed：BR-10 任何比较 -1 < required 恒败）。 */
  workspaceRole(workspaceId?: string, workspaceSlug?: string): number {
    if (!this.snapshot) return -1;
    if (workspaceId && this.snapshot.workspaces[workspaceId])
      return this.snapshot.workspaces[workspaceId].role;
    if (workspaceSlug) {
      for (const w of Object.values(this.snapshot.workspaces)) {
        if (w.slug === workspaceSlug) return w.role;
      }
    }
    return -1;
  }

  /** 有效项目角色：AUTH-005 §4.5.1 —— 与后端 get_effective_project_role 逐字一致（§2.1 关键不变量）。
   *  effective = max(显式 ProjectMember 角色, workspace 隐式提升)
   *  隐式提升：WS_ADMIN+(≥15) → ProjectRole.ADMIN(20)（rbac §7.4）。 */
  effectiveProjectRole(projectId?: string, workspaceSlug?: string): number {
    if (!this.snapshot) return -1;
    const explicit = projectId ? this.snapshot.projects[projectId]?.role ?? -1 : -1;
    const wsId = projectId ? this.snapshot.projects[projectId]?.workspace_id : undefined;
    const wsRole = this.workspaceRole(wsId, workspaceSlug);
    const derived = wsRole >= WorkspaceRole.ADMIN ? ProjectRole.ADMIN : -1;
    return Math.max(explicit, derived);
  }

  /** 权限判定（AUTH-005 §4.5.1 can()）。 */
  can(permission: PermissionKey, scope: Scope, resourceId?: string, ctx?: { workspaceSlug?: string | undefined }): boolean {
    if (this.isSystemAdmin) return true;                       // BR-09
    const required = (PERMISSION_MATRIX[scope] as Record<string, number>)[permission];
    if (required === undefined) return false;                  // 未登记 key（不应发生：BR-06 编译期防）
    const actual = scope === "workspace"
      ? this.workspaceRole(resourceId, ctx?.workspaceSlug)
      : this.effectiveProjectRole(resourceId, ctx?.workspaceSlug);
    return actual >= required;                                  // BR-10：-1 < required 恒败
  }
}
