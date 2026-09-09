import { api } from "./axios";
import type {
  DeleteSubtreeResult,
  InviteResult,
  ProjectMember,
  ProjectSummary,
  SubtreeData,
  WorkspaceInvite,
  WorkspaceMember,
  WorkspaceSummary,
} from "@rp/types";

export type { Issue, ProjectSummary, WorkspaceSummary, WorkspaceMember, ProjectMember, WorkspaceInvite, InviteResult, SubtreeData, SubtreeNode, SubtreeRoot, SubtreeStats, SubtreeMeta, DeleteSubtreeResult } from "@rp/types";

export interface MeEnvelope {
  user: { id: string; email: string; display_name: string; avatar_url: string | null; is_active: boolean };
  workspaces: WorkspaceSummary[];
  default_workspace_slug: string | null;
}

/** AUTH-005 §4.2 权限快照端点 `/api/v1/users/me/permissions/` 响应形状。
 *  Roles 是整数（WorkspaceRole 5/10/15/20、ProjectRole 5/10/15/20）。
 *  真相源：`apps/api/plane/constants/permissions.py` 镜像同源。 */
export interface PermissionSnapshot {
  is_system_admin: boolean;
  workspaces: Record<string, { slug: string; role: number }>;
  projects: Record<string, { workspace_id: string; role: number; inherited: boolean }>;
  meta?: { generated_at: string; truncated: boolean } | null;
}

export const AuthAPI = {
  csrf: () => api.get("auth/csrf-token/"),
  signUp: (email: string, password: string, display_name?: string) =>
    api.post<MeEnvelope>("auth/sign-up/", { email, password, display_name }),
  signIn: (email: string, password: string, remember = false) =>
    api.post<MeEnvelope>("auth/sign-in/", { email, password, remember }),
  signOut: () => api.post("auth/sign-out/", {}),
  me: () => api.get<MeEnvelope>("users/me/"),
};

/** AUTH-005 §4.2 权限快照（C1 信封下解包）。 */
export const PermissionsAPI = {
  my: (workspaceSlug?: string) =>
    api.get<PermissionSnapshot>(
      "users/me/permissions/",
      workspaceSlug ? { params: { workspace_slug: workspaceSlug } } : undefined,
    ),
};

/** AUTH-005 §2.2：收到 403 PERM_* → 静默重拉权限快照的回调（PermissionStore 在创建时注册）。
 *  关键不变量（§2.2 步骤 12~14 / §3.4）：revalidate 必须无 toast（不打扰用户）。
 *  默认 noop；PermissionStore 在 hydrate 路径覆盖。
 *  解耦：revalidator 抽到 services/permissions-revalidator.ts 以避开 api.ts ↔ axios.ts 的 import 循环。 */
export { setPermissionsRevalidator, triggerPermissionsRevalidate } from "./permissions-revalidator";

export const WorkspaceAPI = {
  list: () => api.get<WorkspaceSummary[]>("workspaces/"),
  create: (name: string, description?: string) =>
    api.post<ProjectSummary>("workspaces/", { name, description }),
  detail: (slug: string) => api.get(`workspaces/${slug}/`),
};

export const ProjectAPI = {
  listByWs: (
    slug: string,
    params: { q?: string; status?: "active" | "archived" | "all"; favorite?: boolean; favorite_first?: boolean } = {},
  ) => api.get<ProjectSummary[]>(`workspaces/${slug}/projects/`, { params }),
  create: (slug: string, payload: { name: string; identifier: string; description?: string }) =>
    api.post<ProjectSummary>(`workspaces/${slug}/projects/`, payload),
  detail: (slug: string, projectId: string) => api.get(`workspaces/${slug}/projects/${projectId}/`),
  patch: (slug: string, projectId: string, payload: { name?: string; description?: string }) =>
    api.patch(`workspaces/${slug}/projects/${projectId}/`, payload),
  delete: (slug: string, projectId: string) => api.delete(`workspaces/${slug}/projects/${projectId}/`),
  states: (slug: string, projectId: string, params: { include_cancelled?: string } = {}) =>
    api.get<Array<{ id: string; name: string; color: string; group: string; sort_order: number; is_default: boolean }>>(
      `workspaces/${slug}/projects/${projectId}/states/`,
      { params },
    ),
  favorite: (slug: string, projectId: string) =>
    api.post<{ favorited: boolean; favorited_at: string }>(`workspaces/${slug}/projects/${projectId}/favorite/`),
  unfavorite: (slug: string, projectId: string) =>
    api.delete(`workspaces/${slug}/projects/${projectId}/favorite/`),
  archive: (slug: string, projectId: string) =>
    api.post<{ status: "active" | "archived"; archived_at?: string }>(
      `workspaces/${slug}/projects/${projectId}/archive/`,
    ),
  unarchive: (slug: string, projectId: string) =>
    api.delete<{ status: "active" | "archived" }>(`workspaces/${slug}/projects/${projectId}/archive/`),
};

export const IssueAPI = {
  list: (slug: string, projectId: string, params: {
    ordering?: string; group_by?: string; per_page?: number;
    /** TASK-004 §4.2.3：树形行级懒加载 */
    parent_id?: string;
    /** TASK-003 白名单排序参数（issue_query.apply_order；树形下仅同层兄弟） */
    order_by?: string;
    /** TASK-005 §4.2.5：只看被未完成前置阻塞的任务 */
    blocked?: boolean;
    /** TASK-009 §4.2.4：归档视图（默认排除 archived_at 非空） */
    archived?: boolean;
    /** TASK-003 白名单关键词搜索（关联/移动弹层目标搜索） */
    q?: string;
    /** BOARD-003 §4.2-6 / TASK-011 §4.2.2：② 视图层（UUID）——与 URL 筛选恒 AND */
    view_id?: string;
    /** TASK-011 §4.2.2：③ 临时层（urlencode 后的 DSL JSON 串） */
    filters?: string;
    /** BOARD-003 §4.2-6：分组端点组内页大小（默认 25，上限 100） */
    group_per_page?: number;
  } = {}) =>
    api.get(`workspaces/${slug}/projects/${projectId}/issues/`, { params }),
  create: (slug: string, projectId: string, payload: {
    name: string; state_id?: string; target_date?: string; assignee_ids?: string[]; description_html?: string;
  }) => api.post(`workspaces/${slug}/projects/${projectId}/issues/`, payload),
  /** IssueWriteSerializer 的可写字段全集（serializers/issue.py）。
   *  类型漏字段不会报错、只会在运行时被 DRF 静默忽略或 TS 拒绝 —— 之前 patch 类型
   *  缺 description_html，抽屉想存描述都编译不过。这里按后端写侧口径对齐。 */
  patch: (slug: string, projectId: string, issueId: string, payload: {
    name?: string; description_html?: string; description_json?: Record<string, unknown>;
    state_id?: string; type_id?: string; priority?: string;
    assignee_ids?: string[]; label_ids?: string[]; parent_id?: string | null;
    start_date?: string | null; target_date?: string | null; sort_order?: number;
    /** TASK-006 §4.2.3：估算（既有端点开放字段；≤525600 分钟） */
    estimate_minutes?: number | null;
    /** TASK-008 §4.2.4：自定义字段值（PATCH 合并语义；显式清空传 null） */
    custom_fields?: Record<string, unknown>;
    /** TASK-005 §4.2.4：迁入 completed 被拦截时的管理员强制通道（comment ≥5 字） */
    force?: boolean; comment?: string;
  }) => api.patch(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/`, payload),
  del: (slug: string, projectId: string, issueId: string) =>
    api.delete<DeleteSubtreeResult>(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/`),
  detail: (slug: string, projectId: string, issueId: string) =>
    api.get(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/`),
  /** TASK-004 §4.2.2 `GET …/issues/{id}/subtree/`（CTE 整树；归档根 404；
   *  stats 含根口径，TASK-006 §4.2.4 起另含 subtree_*_minutes）。 */
  subtree: (slug: string, projectId: string, issueId: string) =>
    api.get<SubtreeData>(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/subtree/`),
  /** TASK-002 §4.3.4-6 + COLLAB-001 §4.3 + FILE-001 §4.3 子资源路由。
   *  注意：activities 返回的是**裸数组**（分页信息在 meta.next_cursor），不是 `{results:[]}`。
   *  行内操作人是**平铺的 actor_id / actor_name**，不是嵌套 `actor` 对象。 */
  activities: (slug: string, projectId: string, issueId: string, params: { cursor?: string; per_page?: number } = {}) =>
    api.get<ActivityRow[]>(
      `workspaces/${slug}/projects/${projectId}/issues/${issueId}/activities/`, { params }),
  /** TASK-010 §4.2.1：epoch 组结构时间线（服务端预聚合，前端零聚合逻辑）。
   *  ?field= / ?actor_id= 过滤；游标锚定 epoch（Base64 毫秒），组永不跨页。 */
  activityGroups: (slug: string, projectId: string, issueId: string, params: { cursor?: string; field?: string; actor_id?: string; per_page?: number } = {}) =>
    api.get<ActivityGroup[]>(
      `workspaces/${slug}/projects/${projectId}/issues/${issueId}/activities/`, { params }),
  subIssues: (slug: string, projectId: string, issueId: string) =>
    api.get<Array<{ id: string; issue_key: string; name: string; state_group: string; state_name: string }>>(
      `workspaces/${slug}/projects/${projectId}/issues/${issueId}/sub-issues/`),
  createSubIssue: (slug: string, projectId: string, issueId: string, payload: { name: string }) =>
    api.post<{ id: string; issue_key: string; name: string }>(
      `workspaces/${slug}/projects/${projectId}/issues/${issueId}/sub-issues/`, payload),
  setLabels: (slug: string, projectId: string, issueId: string, labelIds: string[]) =>
    api.put(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/labels/`, { label_ids: labelIds }),
  /** TASK-009 §4.2.1：复制（201 + Location；不承诺幂等）。 */
  duplicate: (slug: string, projectId: string, issueId: string, payload: {
    include_subtrees: boolean; include_assignees: boolean; include_labels: boolean;
    include_custom_fields: boolean; include_dates: boolean;
  }) => api.post<DuplicateResult>(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/duplicate/`, payload),
  /** TASK-009 §4.2.2：归档（整树，动作幂等）。 */
  archive: (slug: string, projectId: string, issueId: string) =>
    api.post<ArchiveResult>(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/archive/`, {}),
  /** TASK-009 §4.2.2：恢复（整树，动作幂等）。 */
  unarchive: (slug: string, projectId: string, issueId: string) =>
    api.delete<RestoreResult>(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/archive/`),
};

/** TASK-002 §4.3.1 项目标签管理端点（C.26）。 */
export const LabelAPI = {
  list: (slug: string, projectId: string) =>
    api.get<Array<{ id: string; name: string; color: string; sort_order: number; is_active: boolean; usage_count: number }>>(
      `workspaces/${slug}/projects/${projectId}/labels/`),
  create: (slug: string, projectId: string, payload: { name: string; color: string }) =>
    api.post<{ id: string }>(`workspaces/${slug}/projects/${projectId}/labels/`, payload),
  patch: (slug: string, projectId: string, labelId: string, payload: { name?: string; color?: string; sort_order?: number; is_active?: boolean }) =>
    api.patch(`workspaces/${slug}/projects/${projectId}/labels/${labelId}/`, payload),
  del: (slug: string, projectId: string, labelId: string, force = false) =>
    api.delete(`workspaces/${slug}/projects/${projectId}/labels/${labelId}/`, { params: force ? { force: "true" } : undefined }),
};

/** TASK-002 §4.3.1 issue-types 端点（项目工作项类型；C.22 类型下拉）。 */
export const IssueTypeAPI = {
  list: (slug: string, projectId: string) =>
    api.get<Array<{ id: string; name: string; color: string; sort_order: number; is_default: boolean; is_active: boolean }>>(
      `workspaces/${slug}/projects/${projectId}/issue-types/`),
};

/** COLLAB-001 §4.3 评论（C.32 + C.33）+ COLLAB-002 §4.2 两层结构。
 *  actor 形状以后端 `CommentSerializer.get_actor` 为准：`{id, display_name, avatar_url}`。
 *  **没有** `name` / `author` 字段——前端曾按 `actor.name` 取首字母，取到 undefined 兜底成「?」。 */
export interface CommentActor {
  id: string | null;
  display_name: string;
  avatar_url?: string | null;
}
/** COLLAB-002 §4.2.2 reactions 聚合行（?expand=reactions 时追加 user_ids）。 */
export interface CommentReactionAgg {
  emoji: string;
  count: number;
  reacted_by_me: boolean;
  user_ids?: string[];
}
/** COLLAB-002 §4.2.1：accessory.images 为服务端聚合的 asset_id 字符串列表
 *  （客户端不直传 accessory，UT-18）；名称/GIF 判定由前端从净化后的
 *  comment_html `<img src alt>` 解析（src 受控锚定 download/?variant=thumb）。 */
export interface CommentImageMeta {
  assetId: string;
  name: string;
  gif: boolean;
  thumb: boolean;
  src: string;
}
export interface CommentRow {
  id: string;
  parent_id?: string | null;
  root_id?: string | null;
  actor?: CommentActor | null;
  comment_html: string;
  mention_ids?: string[];
  reply_to_actor?: CommentActor | null;
  images?: string[];
  reactions?: CommentReactionAgg[];
  replies?: CommentRow[];
  reply_count?: number;
  is_edited: boolean;
  is_deleted: boolean;
  created_at: string;
  updated_at: string | null;
}

/** TASK-002 §4.3.5 操作日志行（`IssueActivityListView` 的裸行结构，非嵌套 actor）。 */
export interface ActivityRow {
  id: string;
  actor_id: string | null;
  actor_name: string | null;
  verb: string;
  field?: string | null;
  old_value?: string | null;
  new_value?: string | null;
  comment?: string;
  /** 毫秒时间戳（float），不是 ISO 字符串——展示时间要用 created_at */
  epoch?: number | null;
  created_at: string | null;
}

export const CommentAPI = {
  /** COLLAB-002 §4.2-2：两层结构列表（顶层 + replies[] + reply_count + reactions 聚合）。
   *  ?expand=reactions 追加 user_ids（名单浮层，§4.2-5）。 */
  list: (slug: string, projectId: string, issueId: string, params: { expand?: "reactions"; per_page?: number; cursor?: string } = {}) =>
    api.get<CommentRow[]>(
      `workspaces/${slug}/projects/${projectId}/issues/${issueId}/comments/`, { params }),
  /** COLLAB-002 §4.2-1：发表评论 / 回复（parent_id 归并生效）+ 图片（comment_json image 节点）。 */
  create: (slug: string, projectId: string, issueId: string, payload: {
    comment_html: string;
    comment_json?: Record<string, unknown>;
    parent_id?: string | null;
  }) =>
    api.post<CommentRow>(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/comments/`, payload),
  patch: (slug: string, projectId: string, issueId: string, commentId: string, payload: { comment_html: string }) =>
    api.patch(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/comments/${commentId}/`, payload),
  del: (slug: string, projectId: string, issueId: string, commentId: string) =>
    api.delete(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/comments/${commentId}/`),
  /** COLLAB-002 §4.2-3：添加表情（幂等；body 带 emoji——路径参数仅 UUID/slug）。 */
  reactOn: (slug: string, projectId: string, issueId: string, commentId: string, emoji: string) =>
    api.post<{ emoji: string; count: number; reacted_by_me: boolean; changed: boolean }>(
      `workspaces/${slug}/projects/${projectId}/issues/${issueId}/comments/${commentId}/reactions/`, { emoji }),
  /** COLLAB-002 §4.2-4：撤销表情（幂等；emoji 走请求体而非路径）。 */
  reactOff: (slug: string, projectId: string, issueId: string, commentId: string, emoji: string) =>
    api.delete<{ emoji: string; count: number; reacted_by_me: boolean; changed: boolean }>(
      `workspaces/${slug}/projects/${projectId}/issues/${issueId}/comments/${commentId}/reactions/`, { data: { emoji } }),
};

/** FILE-001 §4.3 附件（C.31）：presign → 直传 → complete → list → download → delete。
 *  - presign 返回 `{asset_id, upload_url, fields, expires_at}`（AssetService.presign）。
 *  - complete 返回 `{id, name, size, mime, uploaded_by, attachment_count, created_at}`。
 *  - download 端点是换发 302 GET，前端用 `<a>` 跳即可；为防下载链路被拦截，挂 `_download: true` 走 axios raw。
 *  - list/download/delete 都返回 `AttachmentRowSerializer` 视图（id/name/size/mime/uploaded_by_id/status/created_at/download_url）。 */
export interface AttachmentRow {
  id: string;
  name: string;
  size: number;
  mime: string;
  uploaded_by_id: string | null;
  status: string;
  created_at: string;
  download_url: string;
}

export const AttachmentAPI = {
  /** POST .../attachments/presign/ —— 申请直传 URL。
   *  COLLAB-002 §2.3：entity_type=comment_image 评论图域（5MB + 图片白名单收紧，
   *  不占 20 配额；缺省 issue 语义不变）。 */
  presign: (slug: string, projectId: string, issueId: string, payload: {
    file_name: string; file_size: number; content_type: string; entity_type?: "issue" | "comment_image";
  }) =>
    api.post<{ asset_id: string; upload_url: string; fields: Record<string, string>; expires_at: string }>(
      `workspaces/${slug}/projects/${projectId}/issues/${issueId}/attachments/presign/`, payload),
  /** POST .../attachments/{asset_id}/complete/ —— 直传完成后确认 HEAD + 计数 +1 */
  complete: (slug: string, projectId: string, issueId: string, assetId: string, payload: { etag?: string; size?: number } = {}) =>
    api.post<{ id: string; name: string; size: number; mime: string; uploaded_by: string | null; attachment_count: number; created_at: string }>(
      `workspaces/${slug}/projects/${projectId}/issues/${issueId}/attachments/${assetId}/complete/`, payload),
  /** GET .../attachments/ —— 当前 issue 附件列表 */
  list: (slug: string, projectId: string, issueId: string) =>
    api.get<AttachmentRow[]>(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/attachments/`),
  /** GET .../attachments/{asset_id}/download/ —— 换发下载链接（前端走 `<a>` 跟随 302） */
  downloadUrl: (slug: string, projectId: string, issueId: string, assetId: string) =>
    api.get<{ url: string }>(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/attachments/${assetId}/download/`),
  /** DELETE .../attachments/{asset_id}/ —— 软删 */
  del: (slug: string, projectId: string, issueId: string, assetId: string) =>
    api.delete<{ attachment_count: number }>(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/attachments/${assetId}/`),
};

/** TEAM-002 §4.2：工作空间成员 / 邀请 10 端点封装。 */
export const WorkspaceMemberAPI = {
  list: (slug: string, params: { search?: string; role__gte?: number; expand?: string; per_page?: number } = {}) =>
    api.get<WorkspaceMember[]>(`workspaces/${slug}/members/`, { params }),
  patch: (slug: string, memberId: string, payload: { role: number }) =>
    api.patch<WorkspaceMember>(`workspaces/${slug}/members/${memberId}/`, payload),
  remove: (slug: string, memberId: string) => api.delete(`workspaces/${slug}/members/${memberId}/`),
  leave: (slug: string) => api.post(`workspaces/${slug}/members/leave/`, {}),
  transfer: (slug: string, payload: { new_owner_member_id: string; confirm_name: string }) =>
    api.post<{ new_owner: { member_id: string; user_id: string; display_name: string }; previous_owner_role: number }>(
      `workspaces/${slug}/ownership/transfer/`, payload,
    ),
  invitations: (slug: string) => api.get<WorkspaceInvite[]>(`workspaces/${slug}/invitations/`),
  invite: (slug: string, payload: { emails: string[]; role: number }) =>
    api.post<InviteResult[]>(`workspaces/${slug}/invitations/`, payload),
  revokeInvite: (slug: string, inviteId: string) =>
    api.delete(`workspaces/${slug}/invitations/${inviteId}/`),
};

/** TEAM-002 §4.2 端点 5/6：邀请预检 + 接受（全局，不在 workspace 嵌套下）。 */
export const InvitationAPI = {
  precheck: (token: string) =>
    api.get<{
      workspace: { id: string; name: string; slug: string };
      role: number;
      invited_by: { id: string; display_name: string; email: string } | null;
      expires_at: string;
      masked_email: string;
    }>(`invitations/${token}/`),
  accept: (token: string) =>
    api.post<{ workspace: { id: string; name: string; slug: string }; role: number; current_user_role: number }>(
      `invitations/${token}/accept/`, {},
    ),
};

/** PROJ-002 §4.2：项目成员 / 收藏 端点封装。 */
export const ProjectMemberAPI = {
  list: (slug: string, projectId: string, params: { search?: string; expand?: string; per_page?: number } = {}) =>
    api.get<ProjectMember[]>(`workspaces/${slug}/projects/${projectId}/members/`, { params }),
  add: (slug: string, projectId: string, payload: { member_ids: string[]; role: number }) =>
    api.post<Array<{ member_id: string; status: "added" | "skipped" | "failed"; reason?: string; project_member_id?: string; role?: number }>>(
      `workspaces/${slug}/projects/${projectId}/members/`, payload,
    ),
  patch: (slug: string, projectId: string, memberId: string, payload: { role: number }) =>
    api.patch<ProjectMember>(`workspaces/${slug}/projects/${projectId}/members/${memberId}/`, payload),
  remove: (slug: string, projectId: string, memberId: string) =>
    api.delete(`workspaces/${slug}/projects/${projectId}/members/${memberId}/`),
};

/** 取已解包的业务数据。axios 拦截器已把 C1 信封的 data 写回 r.data（见 axios.ts），
 *  这里只做类型收窄、无运行时转换——替代散落各页的 `(r as any).data`。 */
export function unwrap<T>(r: unknown): T {
  return (r as { data: T }).data;
}

/** AUTH-004 §4.2 资料 / 密码 / 头像。GET /users/me/ 仍是 sprint-0 的
 *  MeEnvelope（AuthStore 消费）；PATCH 返回平铺 Profile（ProfileService.serialize）。 */
export interface Profile {
  id: string;
  email: string;
  display_name: string;
  first_name: string;
  last_name: string;
  intro: string;
  avatar_url: string | null;
  is_default_avatar: boolean;
  is_active: boolean;
  updated_at: string;
}

export const ProfileAPI = {
  patch: (payload: Partial<Pick<Profile, "display_name" | "first_name" | "last_name" | "intro">>) =>
    api.patch<Profile>("users/me/", payload),
  changePassword: (payload: { old_password: string; new_password: string; new_password_confirm: string }) =>
    api.post("users/me/change-password/", payload),
  avatarPresign: (payload: { file_name: string; file_size: number; content_type: string }) =>
    api.post<{ asset_id: string; upload_url: string; fields: Record<string, string>; expires_at: string }>(
      "users/me/avatar/presign/", payload),
  avatarComplete: (payload: { asset_id: string }) =>
    api.post<{ avatar_url: string }>("users/me/avatar/complete/", payload),
  avatarDelete: () => api.delete("users/me/avatar/"),
};

export const PasswordAPI = {
  forgot: (email: string) => api.post<null>("auth/forgot-password/", { email }),
  reset: (payload: { token: string; new_password: string; new_password_confirm: string }) =>
    api.post<null>("auth/reset-password/", payload),
};

/** COLLAB-001 §4.2 通知中心。 */
export const NotificationAPI = {
  list: (params: { unread?: boolean; per_page?: number } = {}) =>
    api.get<Array<{ id: string; title: string; data?: Record<string, unknown>; read_at?: string | null; created_at: string }>>(
      "users/me/notifications/", { params }),
  unreadCount: () => api.get<{ count: number }>("users/me/notifications/unread-count/"),
  readAll: () => api.post("users/me/notifications/read-all/", {}),
  read: (id: string) => api.post(`users/me/notifications/${id}/read/`, {}),
};

/* ═══════════════ Sprint-2（TASK-005~010）═══════════════ */

/** TASK-005 §4.2.1 `GET …/relations/` 行（契约冻结，GANTT-001 数据源）：
 *  data[] 恒数组（50 上限天然有界，分页豁免）；related_issue 内联甘特连线必需字段。 */
export type RelationType = "blocks" | "is_blocked_by" | "relates_to" | "duplicates";
export interface RelationRow {
  id: string;
  issue_id: string;
  related_issue_id: string;
  relation_type: RelationType;
  is_blocking: boolean;
  related_issue: {
    id: string;
    issue_key: string;
    name: string;
    state_id: string | null;
    state_group: string;
    start_date: string | null;
    target_date: string | null;
  };
}

export const RelationAPI = {
  /** 全部关联（创建时间倒序；三组由前端按 relation_type 分组渲染）。 */
  list: (slug: string, projectId: string, issueId: string) =>
    api.get<RelationRow[]>(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/relations/`),
  /** 创建关联（成对两行；语义 = 当前任务 <relation_type> 目标任务）。 */
  create: (slug: string, projectId: string, issueId: string, payload: { related_issue_id: string; relation_type: RelationType }) =>
    api.post<{ id: string; mirror_id: string }>(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/relations/`, payload),
  /** 删除关联（镜像行同事务删除）。 */
  del: (slug: string, projectId: string, issueId: string, linkId: string) =>
    api.delete(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/relations/${linkId}/`),
};

/** GANTT-001 §4.2 / GANTT-002 §4.2.1：甘特域端点（视窗行取数 / 连线批量 / 未排期 /
 *  延期概览）。拖拽写通道零新端点（Issue PATCH 既有，GANTT-002 BR-01）。 */
export type GanttGranularity = "day" | "week" | "month";

/** §4.2.1 行契约 18 字段（progress/progress_source 服务端单源，BR-04 前端禁自算）。 */
export interface GanttRow {
  id: string;
  issue_key: string;
  name: string;
  depth: number;
  has_children: boolean;
  collapsed: boolean;
  start_date: string | null;
  target_date: string | null;
  progress: number;
  progress_source: "subtasks" | "state";
  state_group: string;
  state_color: string | null;
  is_overdue: boolean;
  assignee_ids: string[];
  is_aggregated: boolean;
  relation_count: number;
  estimate_minutes: number | null;
  spent_minutes: number;
}

/** §4.2.2 连线边（violation 仅 blocks 边派生，§3.3 红点提示）。 */
export interface GanttEdge {
  from_issue_id: string;
  to_issue_id: string;
  relation_type: "blocks" | "relates_to" | "duplicates";
  from: { issue_key: string; target_date: string | null };
  to: { issue_key: string; start_date: string | null; violation: boolean };
}

/** §4.2.3 未排期行（fields 裁剪：编号/标题/状态/执行人）。 */
export interface GanttUnscheduledRow {
  id: string;
  issue_key: string;
  name: string;
  state_group: string;
  state_color: string | null;
  assignee_ids: string[];
}

/** GANTT-002 §4.2.1 延期概览（统计三数字 = 完整逾期集；items 前 20 截断）。 */
export interface GanttOverdueSummary {
  overdue_count: number;
  max_overdue_days: number;
  by_assignee: Array<{ assignee_id: string; display_name: string; count: number }>;
  items: Array<{ id: string; issue_key: string; name: string; target_date: string; overdue_days: number; assignee_ids: string[] }>;
  items_truncated: boolean;
}

export interface GanttListParams {
  view_id?: string;
  filters?: string;
}

export const GanttAPI = {
  /** 视窗行取数（BR-02 相交判定 + 游标行窗口；per_page 默认 60 上限 100）。 */
  rows: (slug: string, projectId: string, params: GanttListParams & {
    granularity: GanttGranularity;
    viewport_start: string;
    viewport_end: string;
    per_page?: number;
    cursor?: string;
    tz?: string;
  }) =>
    api.get<{ rows: GanttRow[]; unscheduled_count: number }>(
      `workspaces/${slug}/projects/${projectId}/gantt/`, { params }),
  /** 连线批量（一次 ≤ 60 issue，超限由前端分批，§2.5）。 */
  relationsBulk: (slug: string, projectId: string, issueIds: string[]) =>
    api.post<{ edges: GanttEdge[] }>(
      `workspaces/${slug}/projects/${projectId}/gantt/relations/bulk/`, { issue_ids: issueIds }),
  /** 未排期任务列表（meta.total_count 供折叠区徽标）。 */
  unscheduled: (slug: string, projectId: string, params: GanttListParams & { per_page?: number; cursor?: string } = {}) =>
    api.get<GanttUnscheduledRow[]>(`workspaces/${slug}/projects/${projectId}/gantt/unscheduled/`, { params }),
  /** 延期概览聚合（端点级限流 10/min·user，超限 429 RATE_LIMIT_EXCEEDED）。 */
  overdueSummary: (slug: string, projectId: string, params: GanttListParams = {}) =>
    api.get<GanttOverdueSummary>(`workspaces/${slug}/projects/${projectId}/gantt/overdue-summary/`, { params }),
};

/** TASK-006 §4.2.1 工时记录行（POST 201 响应另含 issue_spent_minutes 实时聚合回传）。 */
export interface WorkLogRow {
  id: string;
  issue_id: string;
  actor_id: string;
  worked_on: string;
  minutes: number;
  note: string;
  created_at: string;
  issue_spent_minutes?: number;
}

export const WorkLogAPI = {
  /** 记录列表（筛选 + 游标；meta 另含 sum_minutes 服务端聚合）。 */
  list: (slug: string, projectId: string, issueId: string, params: { per_page?: number; cursor?: string; mine?: boolean } = {}) =>
    api.get<WorkLogRow[]>(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/worklogs/`, { params }),
  /** 填报（仅可补填最近 30 天）。 */
  create: (slug: string, projectId: string, issueId: string, payload: { minutes: number; worked_on: string; note?: string }) =>
    api.post<WorkLogRow>(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/worklogs/`, payload),
  /** 修改（本人 / PROJ_ADMIN）。 */
  patch: (slug: string, projectId: string, issueId: string, logId: string, payload: { minutes?: number; worked_on?: string; note?: string }) =>
    api.patch<WorkLogRow>(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/worklogs/${logId}/`, payload),
  /** 删除（软删，本人 / PROJ_ADMIN）。 */
  del: (slug: string, projectId: string, issueId: string, logId: string) =>
    api.delete(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/worklogs/${logId}/`),
};

/** TASK-007 §4.2.1 PUT / claim 的响应（changes 回传让前端零本地 diff）。 */
export interface AssigneeSyncResult {
  issue_id: string;
  assignee_ids: string[];
  changes: {
    added: Array<{ id: string; display_name: string }>;
    removed: Array<{ id: string; display_name: string }>;
  };
}

export const AssigneeAPI = {
  /** 全量替换执行人集合（转交；可选 comment ≤500 字随通知发送）。 */
  put: (slug: string, projectId: string, issueId: string, payload: { assignee_ids: string[]; comment?: string }) =>
    api.put<AssigneeSyncResult>(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/assignees/`, payload),
  /** 认领（空集合才可；已有执行人 409 RESOURCE_STATE_INVALID）。 */
  claim: (slug: string, projectId: string, issueId: string) =>
    api.post<AssigneeSyncResult>(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/assignees/claim/`, {}),
  /** 自退（仅 user_id=自己；删他人属转交语义走 PUT）。 */
  removeSelf: (slug: string, projectId: string, issueId: string, userId: string) =>
    api.delete(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/assignees/${userId}/`),
};

/** TASK-008 §4.2.1 Schema API 的 custom[] 项（与管理 CRUD 响应同构，serialize_definition 单一序列化点）。 */
export interface FieldOption {
  label: string;
  value: string;
  color?: string;
  sort_order?: number;
}
export interface CustomFieldDef {
  id: string;
  key: string;
  name: string;
  type: string;
  required: boolean;
  description: string;
  scope: "project" | "global";
  project_id: string | null;
  sort_order: number;
  default_value: unknown;
  options: FieldOption[];
  applicable_types: string[];
  filterable: boolean;
  sortable: boolean;
  groupable: boolean;
  indexed: boolean;
  is_active?: boolean;
  /** TASK-012 §4.4：{"read":["role:member"],"write":[…],"required_for":[…]}（token：role:* / user:*） */
  permission_config?: { read?: string[]; write?: string[]; required_for?: string[] };
  /** TASK-012 §4.3：级联配置（cascade 类型）。 */
  cascade_config?: { levels?: Array<{ name?: string; options?: Array<{ label: string; value: string; parent_value?: string | null }> }> };
}
export interface FieldSchema {
  builtin: Array<{ key: string; name: string; type: string; filterable: boolean; sortable: boolean; groupable: boolean }>;
  custom: CustomFieldDef[];
}

export const FieldAPI = {
  /** Schema API（ETag 协商缓存：定义变更即失效，未变 304）。 */
  schema: (slug: string, projectId: string, params: { issue_type?: string } = {}) =>
    api.get<FieldSchema>(`workspaces/${slug}/projects/${projectId}/field-schema/`, { params }),
  /** 字段定义列表（管理页；?scope=all|global|project；meta.limits 含 50/10 上限）。 */
  list: (slug: string, projectId: string, params: { scope?: "all" | "global" | "project" } = {}) =>
    api.get<CustomFieldDef[]>(`workspaces/${slug}/projects/${projectId}/issue-properties/`, { params }),
  /** 创建项目私有字段（201；field_key 须 cf_ 前缀 snake_case）。 */
  create: (slug: string, projectId: string, payload: {
    name: string; field_key: string; field_type: string; is_required?: boolean; is_indexed?: boolean;
    description?: string; applicable_types?: string[]; options?: Array<{ label: string; value: string; color?: string; sort_order?: number }>;
    default_value?: unknown;
  }) => api.post<CustomFieldDef>(`workspaces/${slug}/projects/${projectId}/issue-properties/`, payload),
  /** 编辑（BR-01/06：field_key / field_type 创建后不可变）。 */
  patch: (slug: string, projectId: string, propertyId: string, payload: Partial<{
    name: string; description: string; is_active: boolean; is_required: boolean; is_indexed: boolean;
    options: Array<{ label: string; value: string; color?: string; sort_order: number }>; applicable_types: string[]; default_value: unknown;
    permission_config: { read?: string[]; write?: string[]; required_for?: string[] };
  }>) => api.patch<CustomFieldDef>(`workspaces/${slug}/projects/${projectId}/issue-properties/${propertyId}/`, payload),
  /** 删除（软删 + 异步清理 → 202 {task_id, affected_issues, status_url}）。 */
  del: (slug: string, projectId: string, propertyId: string) =>
    api.delete<{ task_id: string; state: string; affected_issues: number; status_url: string; field_key: string }>(
      `workspaces/${slug}/projects/${projectId}/issue-properties/${propertyId}/`),
  /** 拖拽排序（prev_id/next_id 浮点插值，BOARD-001 同算法）。 */
  sortOrder: (slug: string, projectId: string, propertyId: string, payload: { prev_id?: string | null; next_id?: string | null }) =>
    api.patch<{ id: string; field_key: string; sort_order: number }>(
      `workspaces/${slug}/projects/${projectId}/issue-properties/${propertyId}/sort-order/`, payload),
};

/** TASK-009 §4.2.1 `POST …/duplicate/` 201 响应（连续号段 + 父子重建映射）。 */
export interface DuplicateResult {
  root: {
    id: string;
    issue_key: string;
    name: string;
    state_id: string;
    parent_id: string | null;
    source_issue_id: string;
  };
  copies: Array<{ source_id: string; id: string; issue_key: string; parent_id: string | null; name: string }>;
  total_created: number;
}
export interface ArchiveResult { archived_count: number; archived_at: string }
export interface RestoreResult { restored_count: number }

/** TASK-010 §4.2.1 activities/ epoch 组结构（服务端预聚合；field_label 服务端解析）。
 *  旧 ActivityRow（TASK-002 平铺行）保留给既有消费者，新时间线一律用本组结构。 */
export interface ActivityItem {
  field: string | null;
  field_label: string | null;
  old_value: string | null;
  new_value: string | null;
  old_identifier: string | null;
  new_identifier: string | null;
}
export interface ActivityGroup {
  id: string;
  epoch: number;
  actor: { id: string | null; display_name: string | null };
  verb: string;
  comment: string | null;
  created_at: string | null;
  items: ActivityItem[];
}

/** TASK-010 §4.2.2 admin 死信补偿（系统级顶层资源，权限码 system.audit.read）。 */
export interface DeadLetterRow {
  id: string;
  event_key: string;
  queue: string;
  error_summary: string;
  retries: number;
  first_failed_at: string;
}

export const DeadLetterAPI = {
  list: (params: { per_page?: number } = {}) =>
    api.get<DeadLetterRow[]>("activity-dead-letters/", { params }),
  replay: (messageId: string) =>
    api.post<{ message_id: string; replayed: boolean; dedup_skipped: boolean }>(`activity-dead-letters/${messageId}/replay/`, {}),
  bulkReplay: (messageIds: string[]) =>
    api.post<{ replayed: number; skipped: number }>("activity-dead-letters/bulk/", { message_ids: messageIds }),
  discard: (messageId: string) => api.delete(`activity-dead-letters/${messageId}/`),
};

/* ═══════════════ Sprint-3 Phase 3-A（BOARD-003 §4.2 / TASK-011 §4.2）═══════════════ */

/** BOARD-003 §4.2 views/ CRUD（TASK-011 消费同一端点族，不另建）。 */
export const ViewAPI = {
  /** 列表（内置 + 本人个人视图，按 sort_order；全量无分页）。 */
  list: (slug: string, projectId: string) =>
    api.get(`workspaces/${slug}/projects/${projectId}/views/`),
  create: (slug: string, projectId: string, payload: {
    name: string; layout?: "list" | "kanban" | "gantt" | "table";
    filters?: unknown; display_props?: unknown; description?: string;
  }) => api.post(`workspaces/${slug}/projects/${projectId}/views/`, payload),
  patch: (slug: string, projectId: string, viewId: string, payload: {
    name?: string; layout?: "list" | "kanban" | "gantt" | "table";
    filters?: unknown; display_props?: unknown;
  }) => api.patch(`workspaces/${slug}/projects/${projectId}/views/${viewId}/`, payload),
  del: (slug: string, projectId: string, viewId: string) =>
    api.delete(`workspaces/${slug}/projects/${projectId}/views/${viewId}/`),
};

/** BOARD-003 §4.2 注（BR-10）：「设为默认」零新端点——PATCH /users/me/settings/ 偏好键
 *  board.default_view_id（值按项目记 {"<project_id>": "<view_uuid>"}；取消 = 传 null 删条目）。 */
export const UserSettingsAPI = {
  get: () => api.get<Record<string, unknown>>("users/me/settings/"),
  patch: (payload: { "board.default_view_id"?: Record<string, string | null> | null }) =>
    api.patch<Record<string, unknown>>("users/me/settings/", payload),
};

/** BOARD-003 §4.2.2 分组信封（BOARD-002 契约的维度泛化）。
 *  data 键 = 裸列值键（State UUID / 枚举值 / UUID / 选项值 / __none__）；
 *  响应不内嵌列元数据（BR-16）——列头名称/颜色由前端配置源渲染；
 *  meta：grouped_by / applied / view_id / degraded / group_cursors。 */
export interface GroupedEnvelope {
  data: Record<string, { results: unknown[]; total_results: number; unfiltered_total_results: number }>;
  meta: {
    grouped_by: string;
    sub_grouped_by: string | null;
    total_count: number;
    applied?: Record<string, unknown>;
    view_id?: string;
    degraded?: Record<string, string> | null;
    group_cursors?: Record<string, { next_cursor: string | null }>;
    warning?: string;
  };
}

/* ═══════════════ Sprint-3 Phase 3-B（BOARD-004 §4.2 / COLLAB-002 §4.2）═══════════════ */

/** BOARD-004 §4.2.1 PATCH …/issues/bulk/ 成功响应 data。 */
export interface BulkUpdateResult {
  updated: number;
  epoch: number;
  action: string;
  comment?: string;
}
/** BOARD-004 §4.2.2 批量归档响应 data（archived_count = 实际新置档行数口径）。 */
export interface BulkArchiveResult {
  archived_count: number;
  affected_total: number;
  epoch: number;
}
/** BOARD-004 §4.2.3 批量删除响应 data。 */
export interface BulkDeleteResult {
  deleted: number;
  affected_total: number;
  epoch: number;
}
/** BOARD-004 §4.2.4 危险动作预检响应 data（denied = 权限失败项前移）。 */
export interface BulkPreviewResult {
  selected: number;
  with_subtree: number;
  cascade_total: number;
  affected_total: number;
  links: number;
  worklogs: number;
  comments: number;
  denied: Array<{ index: number; issue_key: string; reason: string }>;
}

export const BulkAPI = {
  /** #1 PATCH …/issues/bulk/ —— 状态 / 优先级（patch）/ 指派 / 标签（集合三模式）。 */
  patch: (slug: string, projectId: string, payload: {
    issue_ids: string[];
    patch?: { state_id?: string; priority?: string };
    assignees?: { mode: "replace" | "add" | "remove"; assignee_ids: string[] };
    labels?: { mode: "replace" | "add" | "remove"; label_ids: string[] };
    comment?: string;
  }) => api.patch<BulkUpdateResult>(`workspaces/${slug}/projects/${projectId}/issues/bulk/`, payload),
  /** #2 POST …/issues/bulk/archive/ —— 整树级联归档（同步 200 豁免）。 */
  archive: (slug: string, projectId: string, payload: { issue_ids: string[]; comment?: string }) =>
    api.post<BulkArchiveResult>(`workspaces/${slug}/projects/${projectId}/issues/bulk/archive/`, payload),
  /** #3 DELETE …/issues/bulk/ —— 软删 + 级联（confirm_count 数量确认，BR-10）。
   *  危险动作默认带 Idempotency-Key（BR-14，UUID/批）。 */
  del: (slug: string, projectId: string, payload: { issue_ids: string[]; confirm_count: number }) =>
    api.delete<BulkDeleteResult>(`workspaces/${slug}/projects/${projectId}/issues/bulk/`, {
      data: payload,
      headers: { "Idempotency-Key": crypto.randomUUID() },
    }),
  /** #4 POST …/issues/bulk/preview/ —— 危险动作预检（只读；issue.read）。 */
  preview: (slug: string, projectId: string, payload: { issue_ids: string[]; action: "delete" | "archive" }) =>
    api.post<BulkPreviewResult>(`workspaces/${slug}/projects/${projectId}/issues/bulk/preview/`, payload),
};

/* ═══════════════ Sprint-3 Phase 3-C（COLLAB-003 §4.2 / COLLAB-004 §4.2）═══════════════ */

/** COLLAB-003 §4.2.1 动态流行结构：kind 三态（activity / comment / batch）。 */
export interface StreamActorRef {
  id: string | null;
  display_name: string;
  avatar_url?: string | null;
}
export interface StreamIssueRef {
  id: string;
  issue_key: string;
  name: string;
  is_deleted: boolean;
  is_archived: boolean;
}
export interface ActivityStreamRow {
  kind: "activity";
  id: string;
  epoch?: number | null;
  actor: StreamActorRef | null;
  verb: string;
  field: string | null;
  /** activity_builder 写入的可读摘要（无则前端按 verb/field 兜底组句）。 */
  text: string | null;
  is_system?: boolean;
  issue: StreamIssueRef | null;
  created_at: string;
}
export interface CommentStreamRow {
  kind: "comment";
  id: string;
  actor: StreamActorRef | null;
  text: string | null;
  root_id: string | null;
  reply_to_actor: { id: string | null; display_name: string } | null;
  issue: StreamIssueRef | null;
  created_at: string;
}
export interface BatchStreamRow {
  kind: "batch";
  epoch: number;
  actor: StreamActorRef | null;
  summary: string;
  /** 全部相同直出；不同 =「多种变更」（§3.2 变更摘要）。 */
  change_brief: string | null;
  batch_count: number;
  created_at: string;
}
export type StreamRow = ActivityStreamRow | CommentStreamRow | BatchStreamRow;

/** §4.2.1 meta（组感知分页 + BR-12 stream_cursor 仅首页携带）。 */
export interface StreamMeta {
  next_cursor: string | null;
  next_page_results: boolean;
  count: number;
  total_count: number;
  total_pages: number;
  page: number;
  per_page: number;
  stream_cursor?: string;
  total_count_estimated?: boolean;
}

/** §2.3 event 语义组（与后端 EVENT_CHOICES 白名单同源；未知值 400 BR-08）。
 * lifecycle 为 Sprint-5 扩域补登（PROJ-003 §2.3 → COLLAB-003 §2.3 回改登记）。 */
export const STREAM_EVENT_GROUPS: Array<{ key: string; label: string }> = [
  { key: "created", label: "创建" },
  { key: "state", label: "状态" },
  { key: "assignees", label: "指派" },
  { key: "priority", label: "优先级" },
  { key: "dates", label: "日期" },
  { key: "estimate", label: "工时" },
  { key: "custom_fields", label: "字段" },
  { key: "relations", label: "关联" },
  { key: "parent", label: "父子" },
  { key: "worklog", label: "工时" },
  { key: "archived", label: "归档" },
  { key: "deleted", label: "删除" },
  { key: "comment", label: "评论" },
  { key: "lifecycle", label: "生命周期" },
];

/** stream_cursor 解析（§4.2.1 要点 2）：自右向左取最后一段为 UUID、其余整体为时间戳。 */
export function parseStreamCursor(cursor: string): { createdAt: string; id: string } | null {
  const idx = cursor.lastIndexOf(":");
  if (idx <= 0) return null;
  return { createdAt: cursor.slice(0, idx), id: cursor.slice(idx + 1) };
}

export const ActivityStreamAPI = {
  /** #1 GET …/projects/{pid}/activities/ —— 动态流（合流 + 折叠 + 组感知游标）。 */
  stream: (slug: string, projectId: string, params: {
    actor_id?: string; event?: string; cursor?: string; per_page?: number;
  } = {}) =>
    api.get<StreamRow[]>(`workspaces/${slug}/projects/${projectId}/activities/`, { params }),
  /** #2 GET …/activities/?epoch=<f> —— 批量明细（轻量行；meta 翻页豁免四字段）。 */
  batchDetail: (slug: string, projectId: string, epoch: number, params: { per_page?: number } = {}) =>
    api.get<BatchDetailRow[]>(`workspaces/${slug}/projects/${projectId}/activities/`, {
      params: { epoch: String(epoch), ...params },
    }),
};

/** §4.2.2 批量明细轻量行。 */
export interface BatchDetailRow {
  issue_id: string;
  issue_key: string;
  name: string;
  field: string | null;
  old_value: string | null;
  new_value: string | null;
}
export interface BatchDetailMeta {
  count: number;
  total_count: number;
  truncated: boolean;
  limit: number;
}

/* ═══════════════ Sprint-4（FILE-002 §4.2 项目文件库）═══════════════ */

/** FILE-002 §4.2 #1：目录树行（扁平行 + parent_id/depth，前端组树；可见性剪枝后仅含
 *  「到根的全部祖先均可见」的目录；file_count 为可见子树聚合——计数不透出不可见子孙）。 */
export interface FileFolderRow {
  id: string;
  name: string;
  parent_id: string | null;
  depth: number;
  visibility: FileVisibility;
  allowed_members: string[];
  file_count: number;
  children_count: number;
  created_at: string;
  updated_at: string;
}

export type FileVisibility = "all" | "admins" | "members";

/** FILE-002 §4.2.1 / services.file_library.file_row：meta 九字段 + total_size_bytes
 *  （仅文件列表）；回收站行另含 deleted_at（trash_rows 追加）。 */
export interface LibraryFileRow {
  id: string;
  name: string;
  size_bytes: number;
  content_type: string;
  type_category: "image" | "document" | "video" | "archive" | "other";
  visibility: FileVisibility;
  folder_id: string | null;
  issue_id: string | null;
  uploaded_by: string | null;
  /** §4.2.1 注：仅 ?expand=uploaded_by 时追加（原 ID 字段照常保留）。 */
  uploaded_by_detail?: { id: string; display_name: string };
  download_count: number;
  status: string;
  created_at: string;
  updated_at: string;
  deleted_at?: string | null;
}

/** FILE-002 §4.2.3：配额用量四字段。 */
export interface StorageUsage {
  quota_bytes: number;
  used_bytes: number;
  pending_bytes: number;
  usage_ratio: number;
}

/** FILE-002 §4.2 端点表 #1~#14（全部强制尾斜杠；files/trash|storage 字面量段）。 */
export const FileLibraryAPI = {
  /** #1 目录树（file.read；按请求者可见性剪枝）。 */
  folders: (slug: string, projectId: string) =>
    api.get<FileFolderRow[]>(`workspaces/${slug}/projects/${projectId}/folders/`),
  /** #2 新建目录（folder.manage；同层同名 409 RESOURCE_ALREADY_EXISTS）。 */
  createFolder: (slug: string, projectId: string, payload: { name: string; parent_id?: string | null }) =>
    api.post<FileFolderRow>(`workspaces/${slug}/projects/${projectId}/folders/`, payload),
  /** #3 改名/移动（folder.manage）；可见性（file.permission.manage 仅 ADMIN）。 */
  patchFolder: (slug: string, projectId: string, folderId: string, payload: {
    name?: string; parent_id?: string | null; visibility?: FileVisibility; allowed_members?: string[];
  }) => api.patch<FileFolderRow>(`workspaces/${slug}/projects/${projectId}/folders/${folderId}/`, payload),
  /** #4 删除（整树软删；回传 folders_deleted/files_deleted）。 */
  deleteFolder: (slug: string, projectId: string, folderId: string) =>
    api.delete<{ id: string; folders_deleted: number; files_deleted: number }>(
      `workspaces/${slug}/projects/${projectId}/folders/${folderId}/`),
  /** #5 目录文件列表（游标 + name/type/uploaded_by 筛选 + meta.total_size_bytes）。 */
  files: (slug: string, projectId: string, folderId: string, params: {
    name?: string; type?: string; uploaded_by?: string; ordering?: string;
    per_page?: number; cursor?: string; expand?: string;
  } = {}) =>
    api.get<LibraryFileRow[]>(`workspaces/${slug}/projects/${projectId}/folders/${folderId}/files/`, { params }),
  /** #6 上传预签名（FILE-001 §4.3.2 协议复用：asset_id/upload_url/fields/expires_in）。 */
  presign: (slug: string, projectId: string, folderId: string, payload: {
    file_name: string; file_size: number; content_type: string;
  }) =>
    api.post<{ asset_id: string; upload_url: string; fields: Record<string, string>; expires_at: string; expires_in: number }>(
      `workspaces/${slug}/projects/${projectId}/folders/${folderId}/files/presign/`, payload),
  /** #14 完成确认（幂等；200 回文件元数据行）。 */
  complete: (slug: string, projectId: string, assetId: string) =>
    api.post<LibraryFileRow>(`workspaces/${slug}/projects/${projectId}/files/${assetId}/complete/`, {}),
  /** #7 下载预签名（5 分钟；200 {download_url, expires_in} → window.open）。 */
  downloadUrl: (slug: string, projectId: string, assetId: string) =>
    api.get<{ download_url: string; expires_in: number }>(
      `workspaces/${slug}/projects/${projectId}/files/${assetId}/download-url/`),
  /** #8 重命名/移动/双挂（file.update，R1）；可见性（file.permission.manage）。 */
  patchFile: (slug: string, projectId: string, assetId: string, payload: {
    name?: string; folder_id?: string | null; issue_id?: string | null;
    visibility?: FileVisibility; allowed_members?: string[];
  }) => api.patch<LibraryFileRow>(`workspaces/${slug}/projects/${projectId}/files/${assetId}/`, payload),
  /** #9 删除（软删进回收站；204）。 */
  delFile: (slug: string, projectId: string, assetId: string) =>
    api.delete(`workspaces/${slug}/projects/${projectId}/files/${assetId}/`),
  /** #10 回收站还原（BR-07 冲突落根 + (恢复) 后缀）。 */
  restore: (slug: string, projectId: string, assetId: string) =>
    api.post<LibraryFileRow>(`workspaces/${slug}/projects/${projectId}/files/${assetId}/restore/`, {}),
  /** #11 回收站列表（R1 同键过滤：ADMIN 全量 / CONTRIBUTOR 仅本人删除项）。 */
  trash: (slug: string, projectId: string, params: { per_page?: number; cursor?: string } = {}) =>
    api.get<LibraryFileRow[]>(`workspaces/${slug}/projects/${projectId}/files/trash/`, { params }),
  /** #13 彻底删除（仅 ADMIN；引用计数判对象，BR-06）。 */
  purge: (slug: string, projectId: string, assetId: string) =>
    api.delete<{ id: string; purged: boolean }>(`workspaces/${slug}/projects/${projectId}/files/${assetId}/purge/`),
  /** #12 配额用量（file.read）。 */
  storage: (slug: string, projectId: string) =>
    api.get<StorageUsage>(`workspaces/${slug}/projects/${projectId}/files/storage/`),
};

/** COLLAB-004 §4.2 换票 / 续签（§4.2.1 信封 data：token/rooms/expires_at/renew_after）。 */
export interface RealtimeTokenResult {
  token: string;
  rooms: string[];
  expires_at: string;
  renew_after: number;
}

export const RealtimeAPI = {
  /** #1 POST …/projects/{pid}/realtime-token/（project.read；issue 不可见 403 拒整票；
   *  file_rooms = file:{asset_id} 第四类房间，FILE-003 §4.4——file.read + 可见性校验）。 */
  token: (slug: string, projectId: string, payload: { client_tab_id: string; issue_rooms: string[]; file_rooms?: string[] }) =>
    api.post<RealtimeTokenResult>(`workspaces/${slug}/projects/${projectId}/realtime-token/`, payload),
  /** #2 POST /users/me/realtime-token/renew/（旧 jti 轮换；issue_rooms/file_rooms 缺省沿用旧票房间集）。 */
  renew: (payload: { token: string; client_tab_id: string; issue_rooms?: string[]; file_rooms?: string[] }) =>
    api.post<RealtimeTokenResult>("users/me/realtime-token/renew/", payload),
};

/* ═══════════════ Sprint-4（FILE-003 §4.2 分片会话 / 版本 / 预览调度）═══════════════ */

/** FILE-003 §4.2 #2 / services.upload_session.session_row：断点续传取片表。 */
export interface UploadSessionRow {
  session_id: string;
  file_name: string;
  file_size: number;
  status: "uploading" | "completed" | "aborted" | "expired";
  chunk_size: number;
  total_chunks: number;
  uploaded_chunks: number[];
  expires_at: string;
  asset_id: string | null;
}

/** FILE-003 §4.2 #3 / services.upload_session.presign_chunk：片预签名换发。 */
export interface ChunkPresignRow {
  part_number: number;
  upload_url: string;
  expires_in: number;
}

/** >50MB 强制分片（BR-01）——与后端直传端点拒收上限对称。 */
export const CHUNK_UPLOAD_THRESHOLD = 50 * 1024 * 1024;
/** 8MB/片（BR-02）——展示用（片大小真相在服务端 session_row.chunk_size）。 */
export const CHUNK_SIZE_BYTES = 8 * 1024 * 1024;
/** 并行片数（§2.6）。 */
export const CHUNK_PARALLELISM = 3;
/** 片级重传上限（BR-03：MD5 与 ETag 不符该片重传 ≤3 次）。 */
export const CHUNK_MAX_RETRIES = 3;

export const FileUploadSessionAPI = {
  /** #1 POST …/upload-sessions/（file.upload；201 回断点片表）。 */
  init: (slug: string, projectId: string, payload: {
    file_name: string; file_size: number; content_type: string; folder_id: string; content_md5?: string;
  }) => api.post<UploadSessionRow>(`workspaces/${slug}/projects/${projectId}/upload-sessions/`, payload),
  /** #2 GET …/upload-sessions/{sid}/（file.upload + 属主；断点片表）。 */
  status: (slug: string, projectId: string, sessionId: string) =>
    api.get<UploadSessionRow>(`workspaces/${slug}/projects/${projectId}/upload-sessions/${sessionId}/`),
  /** #3 POST …/upload-sessions/{sid}/chunks/{n}/（换发该片预签名 UploadPart URL）。 */
  chunkUrl: (slug: string, projectId: string, sessionId: string, chunkNumber: number) =>
    api.post<ChunkPresignRow>(`workspaces/${slug}/projects/${projectId}/upload-sessions/${sessionId}/chunks/${chunkNumber}/`, {}),
  /** #4 PATCH …/upload-sessions/{sid}/chunks/{n}/（登记片完成 etag + md5 核对）。 */
  registerChunk: (slug: string, projectId: string, sessionId: string, chunkNumber: number, payload: {
    etag: string; md5?: string;
  }) => api.patch<{ part_number: number; uploaded_chunks: number[] }>(
    `workspaces/${slug}/projects/${projectId}/upload-sessions/${sessionId}/chunks/${chunkNumber}/`, payload),
  /** #5 POST …/upload-sessions/{sid}/complete/（ListParts 核对 + 合并 + 落库，201）。 */
  complete: (slug: string, projectId: string, sessionId: string) =>
    api.post<{ file: LibraryFileRow; version: FileVersionRow }>(
      `workspaces/${slug}/projects/${projectId}/upload-sessions/${sessionId}/complete/`, {}),
  /** #6 DELETE …/upload-sessions/{sid}/（Abort；204；残片 30 分钟后标记 abandoned）。 */
  abort: (slug: string, projectId: string, sessionId: string) =>
    api.delete(`workspaces/${slug}/projects/${projectId}/upload-sessions/${sessionId}/`),
};

/** FILE-003 §4.2 #7 / services.upload_session.version_row：版本行（新→旧，is_current 标当前）。 */
export interface FileVersionRow {
  version_id: string;
  version_number: number;
  size_bytes: number;
  content_type: string;
  md5: string | null;
  source_version_id: string | null;
  source_version_number: number | null;
  uploaded_by: string | null;
  is_current: boolean;
  created_at: string;
}

/** FILE-003 §4.2.2 预览调度载荷（就绪 200 / 排队 202；archive/other → no_preview）。 */
export interface PreviewDispatchRow {
  kind: "image" | "pdf" | "text" | "video" | "archive" | "other";
  ready: boolean;
  state?: "transcoding" | "too_large" | "unsupported" | "no_preview";
  preview_url?: string;
  poster_url?: string;
  poster_state?: string;
  eta_seconds?: number;
  fallback_download: boolean;
}

export const FileVersionsAPI = {
  /** #7 GET …/files/{asset_id}/versions/（file.read + 可见性 404）。 */
  list: (slug: string, projectId: string, assetId: string) =>
    api.get<FileVersionRow[]>(`workspaces/${slug}/projects/${projectId}/files/${assetId}/versions/`),
  /** #8 POST …/files/{asset_id}/versions/{vid}/rollback/（file.version.manage；201 新版本行）。 */
  rollback: (slug: string, projectId: string, assetId: string, versionId: string) =>
    api.post<{ version_id: string; version_number: number; source_version_number: number | null; object_key: string; created_at: string }>(
      `workspaces/${slug}/projects/${projectId}/files/${assetId}/versions/${versionId}/rollback/`, {}),
  /** 指定版本正文换发路径（#9 302 跳预签名 GET——diff/原图直接以它为 src/fetch）。 */
  contentPath: (slug: string, projectId: string, assetId: string, versionId: string) =>
    `workspaces/${slug}/projects/${projectId}/files/${assetId}/versions/${versionId}/content/`,
};

export const FilePreviewAPI = {
  /** #10 GET …/files/{asset_id}/preview/（就绪 200 / 排队 202；r.status 区分）。 */
  dispatch: (slug: string, projectId: string, assetId: string) =>
    api.get<PreviewDispatchRow>(`workspaces/${slug}/projects/${projectId}/files/${assetId}/preview/`),
  /** #11 衍生物换发路径（302；网格缩略/悬浮小卡 <img> 直接 src）。 */
  derivativePath: (slug: string, projectId: string, assetId: string, kind: "thumbnail" | "preview" | "poster") =>
    `workspaces/${slug}/projects/${projectId}/files/${assetId}/derivatives/${kind}/`,
};

/* ═══════════════ Sprint-4（FILE-004 §4.2 分享内部四端点）═══════════════ */

/** FILE-004 §4.2.1 / §4.2.4 services.file_share.share_row。 */
export interface ShareLinkRow {
  id: string;
  slug: string;
  share_url: string;
  permission: "view" | "download";
  has_password: boolean;
  expires_at: string | null;
  status: "active" | "revoked" | "expired" | "invalidated";
  access_count: number;
  created_at: string;
}

export const FileShareAPI = {
  /** #1 POST …/files/{asset_id}/share-links/（file.share + can_view_file；201）。 */
  create: (slug: string, projectId: string, assetId: string, payload: {
    permission: "view" | "download"; password?: string; expires_in_days?: number | null;
  }) => api.post<ShareLinkRow>(`workspaces/${slug}/projects/${projectId}/files/${assetId}/share-links/`, payload),
  /** #2 GET …/files/{asset_id}/share-links/（file.share；含失效态供管理弹层）。 */
  list: (slug: string, projectId: string, assetId: string) =>
    api.get<ShareLinkRow[]>(`workspaces/${slug}/projects/${projectId}/files/${assetId}/share-links/`),
  /** #3 POST …/share-links/{link_id}/extend/（file.share + 创建者/ADMIN；BR-15 非幂等）。 */
  extend: (slug: string, projectId: string, linkId: string, payload: { extend_days: number }) =>
    api.post<{ id: string; expires_at: string | null; status: ShareLinkRow["status"] }>(
      `workspaces/${slug}/projects/${projectId}/share-links/${linkId}/extend/`, payload),
  /** #4 DELETE …/share-links/{link_id}/（吊销终态；204；BR-14 预签名 5 分钟自然过期）。 */
  revoke: (slug: string, projectId: string, linkId: string) =>
    api.delete(`workspaces/${slug}/projects/${projectId}/share-links/${linkId}/`),
};


// ════════════════════════════════════════════════════════════════
// Sprint-5 域服务（AUTH-006 / TEAM-003 / PROJ-003 / RPT-002 / INTG-001/002）
// ════════════════════════════════════════════════════════════════

/** AUTH-006 §2.3/§4.4：批量角色（部分成功语义）+ 账号启停。 */
export interface BulkRoleResult {
  updated: number;
  skipped: Array<{ user_id: string; reason: string }>;
  failed: Array<{ user_id: string; reason: string; message?: string }>;
}

export const MemberAdminAPI = {
  bulkRole: (slug: string, payload: { user_ids: string[]; role: number }) =>
    api.post<BulkRoleResult>(`workspaces/${slug}/members/bulk-role/`, payload),
  disable: (slug: string, memberRowId: string) =>
    api.post<{ user_id: string; is_active: boolean; disabled_at: string | null;
              revoked: { sessions: number; api_keys: number; ws_connections: number } }>(
      `workspaces/${slug}/members/${memberRowId}/disable/`),
  enable: (slug: string, memberRowId: string) =>
    api.post<{ user_id: string; is_active: boolean; disabled_at: null }>(
      `workspaces/${slug}/members/${memberRowId}/enable/`),
  bulkRoleProject: (slug: string, projectId: string, payload: { member_ids: string[]; role: number }) =>
    api.post<BulkRoleResult>(
      `workspaces/${slug}/projects/${projectId}/members/bulk-role/`, payload),
};

/** TEAM-003 §4.2：治理四区块。 */
export interface WorkspaceLabelRow {
  id: string; name: string; color: string; description: string; created_at: string;
}

export const GovernanceAPI = {
  archive: (slug: string) =>
    api.post<{ slug: string; archived_at: string | null; affected_projects: number; idempotent: boolean }>(
      `workspaces/${slug}/archive/`),
  restore: (slug: string) =>
    api.post<{ slug: string; archived_at: null; idempotent: boolean }>(
      `workspaces/${slug}/restore/`),
  listLabels: (slug: string) =>
    api.get<WorkspaceLabelRow[]>(`workspaces/${slug}/labels/`),
  createLabel: (slug: string, payload: { name: string; color: string; description?: string }) =>
    api.post<WorkspaceLabelRow>(`workspaces/${slug}/labels/`, payload),
  updateLabel: (slug: string, id: string, payload: Partial<{ name: string; color: string; description: string }>) =>
    api.patch<WorkspaceLabelRow>(`workspaces/${slug}/labels/${id}/`, payload),
  deleteLabel: (slug: string, id: string) =>
    api.delete<{ id: string; deleted_at: string; affected_issues: number }>(
      `workspaces/${slug}/labels/${id}/`),
  getDefaultStates: (slug: string) =>
    api.get<{ name: string; version: number; groups: Array<{
      group: string; color?: string; states: Array<{ name: string; sequence: number }> }> }>(
      `workspaces/${slug}/default-states/`),
  putDefaultStates: (slug: string, payload: { name?: string; groups: Array<{
      group: string; color?: string; states: Array<{ name: string; sequence: number }> }> }) =>
    api.put<{ name: string; version: number; groups: unknown[] }>(
      `workspaces/${slug}/default-states/`, payload),
  activityStats: (slug: string, days = 30) =>
    api.get<{
      active_members_7d: number; active_members_30d: number; total_members: number;
      contribution_distribution: Array<{ week: string; buckets: Record<string, number> }>;
      login_days_histogram: Record<string, number>;
      top_actions: { issue: number | null; comment: number | null } | null;
    }>(`workspaces/${slug}/activity-stats/`, { params: { days } }),
};

/** PROJ-003 §4.2：生命周期 + 模板。 */
export type ProjectStatus = "draft" | "active" | "archived" | "closed";

export const LifecycleAPI = {
  transition: (slug: string, projectId: string, payload: { to_status: ProjectStatus; force?: boolean; reason?: string }) =>
    api.post<{ id: string; status: ProjectStatus; transitioned_at: string | null;
              affected_issues: number; idempotent: boolean }>(
      `workspaces/${slug}/projects/${projectId}/transitions/`, payload),
  statusLogs: (slug: string, projectId: string) =>
    api.get<Array<{ id: string; from_status: string; to_status: string;
                    operator: { id: string; display_name: string } | null;
                    reason: string; transitioned_at: string }>>(
      `workspaces/${slug}/projects/${projectId}/status-logs/`),
  duplicate: (slug: string, projectId: string) =>
    api.post<unknown>(`workspaces/${slug}/projects/${projectId}/duplicate/`),
  listTemplates: (slug: string) =>
    api.get<Array<{ id: string; name: string; description: string; is_builtin: boolean;
                    states_snapshot: unknown[]; labels_snapshot: unknown[];
                    folders_snapshot: unknown[] }>>(`workspaces/${slug}/project-templates/`),
};

/** RPT-002 §4.2：项目统计双端点。 */
export const ProjectStatsAPI = {
  progress: (slug: string, projectId: string, params: { days?: number; tz?: string } = {}) =>
    api.get<{
      project_id: string; as_of: string;
      state_distribution: Record<string, number>; total: number;
      completion_rate: number | null; overdue_count: number;
      worklog_summary: { estimate_minutes: number; logged_minutes: number;
                         remaining_minutes: number; overrun_minutes: number;
                         unestimated_count: number };
      trend: { days: number; created: Array<{ date: string; count: number }>;
               completed: Array<{ date: string; count: number }> };
    }>(`workspaces/${slug}/projects/${projectId}/stats/`, { params }),
  members: (slug: string, projectId: string, params: { role?: string; order_by?: string } = {}) =>
    api.get<{
      rows: Array<{ member_id: string; display_name: string; avatar_url: string | null;
                    role: number; is_active: boolean; open_count: number;
                    done_count_30d: number; overdue_count: number;
                    estimate_minutes_open: number; logged_minutes_30d: number }>;
      unassigned: { open_count: number; overdue_count: number; estimate_minutes_open: number };
      totals: { open_count: number; done_count_30d: number; overdue_count: number;
                estimate_minutes_open: number; logged_minutes_30d: number };
    }>(`workspaces/${slug}/projects/${projectId}/stats/members/`, { params }),
};

/** INTG-001 §4.2：GitHub 集成。 */
export interface GithubBindingRow {
  id: string; installation_id: number; repository_full_name: string;
  repository_node_id: string; sync_status: "syncing" | "paused" | "stale" | "unbound";
  default_issue_type_id: string | null; last_synced_at: string | null;
  webhook_secret_shown_once?: string; webhook_registered?: boolean;
}

export const GithubIntegrationAPI = {
  installEntry: (slug: string) =>
    api.get<{ install_url: string; state: string; expires_in: number }>(
      `workspaces/${slug}/integrations/github/app/`),
  repositories: (slug: string, projectId: string, installationId: number) =>
    api.get<Array<{ full_name: string; node_id: string; private: boolean; html_url: string }>>(
      `workspaces/${slug}/projects/${projectId}/integrations/github/repositories/`,
      { params: { installation_id: installationId } }),
  listBindings: (slug: string, projectId: string) =>
    api.get<GithubBindingRow[]>(
      `workspaces/${slug}/projects/${projectId}/integrations/github/bindings/`),
  createBinding: (slug: string, projectId: string, payload: {
      repository_full_name: string; repository_node_id?: string;
      installation_id?: number; default_issue_type_id?: string | null }) =>
    api.post<GithubBindingRow>(
      `workspaces/${slug}/projects/${projectId}/integrations/github/bindings/`, payload),
  updateBinding: (slug: string, projectId: string, id: string, payload: {
      sync_status?: "syncing" | "paused"; default_issue_type_id?: string | null }) =>
    api.patch<GithubBindingRow>(
      `workspaces/${slug}/projects/${projectId}/integrations/github/bindings/${id}/`, payload),
  deleteBinding: (slug: string, projectId: string, id: string) =>
    api.delete(`workspaces/${slug}/projects/${projectId}/integrations/github/bindings/${id}/`),
  syncLogs: (slug: string, projectId: string) =>
    api.get<Array<{ id: string; repository: string; scope: string; direction: string;
                    winner_side: string; winner_payload: unknown; loser_payload: unknown;
                    occurred_at: string }>>(
      `workspaces/${slug}/projects/${projectId}/integrations/github/sync-logs/`),
};

/** INTG-002 §4.2：出站 Webhook。 */
export interface WebhookEndpointRow {
  id: string; url: string; events: string[];
  is_active: "active" | "disabled" | "auto_disabled";
  consecutive_failures: number; created_at: string;
  secret_shown_once?: string;
}

export interface WebhookDeliveryRow {
  id: string; event: string; event_id: string;
  status: "pending" | "success" | "retrying" | "dead" | "cancelled";
  attempts: Array<{ n: number; at: string; code: number; latency_ms: number; error: string | null }>;
  attempt_count: number; next_retry_at: string | null; replay_of: string | null;
  payload: Record<string, unknown>; created_at: string;
}

export const WebhookAPI = {
  list: (slug: string, projectId: string) =>
    api.get<WebhookEndpointRow[]>(`workspaces/${slug}/projects/${projectId}/webhooks/`),
  create: (slug: string, projectId: string, payload: { url: string; events: string[] }) =>
    api.post<WebhookEndpointRow>(`workspaces/${slug}/projects/${projectId}/webhooks/`, payload),
  update: (slug: string, projectId: string, id: string, payload: { url?: string; events?: string[] }) =>
    api.patch<WebhookEndpointRow>(`workspaces/${slug}/projects/${projectId}/webhooks/${id}/`, payload),
  disable: (slug: string, projectId: string, id: string) =>
    api.post<WebhookEndpointRow>(`workspaces/${slug}/projects/${projectId}/webhooks/${id}/disable/`),
  enable: (slug: string, projectId: string, id: string) =>
    api.post<WebhookEndpointRow>(`workspaces/${slug}/projects/${projectId}/webhooks/${id}/enable/`),
  remove: (slug: string, projectId: string, id: string) =>
    api.delete(`workspaces/${slug}/projects/${projectId}/webhooks/${id}/`),
  ping: (slug: string, projectId: string, id: string) =>
    api.post<{ ping: string }>(`workspaces/${slug}/projects/${projectId}/webhooks/${id}/ping/`),
  deliveries: (slug: string, projectId: string, id: string, params: {
      status?: string; event?: string; cursor?: string } = {}) =>
    api.get<WebhookDeliveryRow[]>(
      `workspaces/${slug}/projects/${projectId}/webhooks/${id}/deliveries/`, { params }),
  replay: (slug: string, projectId: string, id: string, deliveryId: string) =>
    api.post<WebhookDeliveryRow>(
      `workspaces/${slug}/projects/${projectId}/webhooks/${id}/deliveries/${deliveryId}/`),
};

/* ════════════════ Sprint-7 M11-WF（WF-001/002/003 + TASK-013）════════════════ */

/** WF-001 §4.8① available 项：requires_payload 配置态 / blocked_by 执行态计数。 */
export interface TransitionAvailableItem {
  transition_id: string;
  name: string;
  to_state: { id: string; name: string; group: string; color: string };
  requires_payload: string[];
  has_approval: boolean;
  allowed: boolean;
  deny_reason?: string;
  blocked_by: Array<{ type: string; count: number }>;
}

/** WF-002 §4.6 审批中心行。 */
export interface ApprovalRow {
  instance_id: string;
  status: "pending" | "approved" | "rejected" | "withdrawn" | "terminated";
  current_level: number;
  flow_name: string;
  issue: { id: string; issue_key: string; name: string; project_id: string };
  initiator_id: string;
  created_at: string;
  completed_at?: string | null;
  my_action?: string;
}

export const WorkflowAPI = {
  list: (slug: string, projectId: string) =>
    api.get<Array<{ id: string; name: string; issue_type_id: string | null; status: string;
      version: number; state_count: number; transition_count: number; updated_at: string }>>(
      `workspaces/${slug}/projects/${projectId}/workflows/`),
  detail: (slug: string, projectId: string, wfId: string) =>
    api.get<WorkflowDetail>(`workspaces/${slug}/projects/${projectId}/workflows/${wfId}/`),
  create: (slug: string, projectId: string, payload: { name: string; issue_type_id?: string | null; description?: string }) =>
    api.post<WorkflowDetail>(`workspaces/${slug}/projects/${projectId}/workflows/`, payload),
  patch: (slug: string, projectId: string, wfId: string, payload: Partial<{ name: string; description: string; issue_type_id: string | null }>) =>
    api.patch(`workspaces/${slug}/projects/${projectId}/workflows/${wfId}/`, payload),
  saveGraph: (slug: string, projectId: string, wfId: string, payload: { states: Array<Record<string, unknown>>; transitions: Array<Record<string, unknown>> }, etag: string) =>
    api.put<WorkflowDetail>(`workspaces/${slug}/projects/${projectId}/workflows/${wfId}/graph/`, payload,
      { headers: { "If-Match": etag } }),
  publish: (slug: string, projectId: string, wfId: string) =>
    api.post(`workspaces/${slug}/projects/${projectId}/workflows/${wfId}/publish/`),
  archive: (slug: string, projectId: string, wfId: string) =>
    api.post(`workspaces/${slug}/projects/${projectId}/workflows/${wfId}/archive/`),
  /** 任务侧：当前可用流转（fallback=true 时退化为 V1.0 状态下拉）。 */
  available: (slug: string, projectId: string, issueId: string) =>
    api.get<{ workflow: { id: string; name: string; version: number } | null;
      current_state: { id: string; name: string; group: string; color: string } | null;
      available: TransitionAvailableItem[] | null; fallback: boolean }>(
      `workspaces/${slug}/projects/${projectId}/issues/${issueId}/transitions/available/`),
  /** 执行流转（200 成功 / 202 审批挂起 / 4xx 守卫拦截结构化）。 */
  execute: (slug: string, projectId: string, issueId: string, payload: {
    to_state_id: string; transition_id?: string; guard_payload?: Record<string, unknown> }) =>
    api.post(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/transitions/`, payload),
};

/** WF-001 §4.8⑤ 图详情（画布载荷）。 */
export interface WorkflowDetail {
  id: string;
  name: string;
  issue_type_id: string | null;
  status: string;
  version: number;
  based_on_version: number | null;
  states: Array<{ id: string; state_id: string; name?: string; group?: string; color?: string;
    is_initial: boolean; layout_x: number; layout_y: number; field_locks: Array<{ field: string }> }>;
  transitions: Array<{ id: string; name: string; from_state_id: string; to_state_id: string;
    guards: Array<{ type: string; config?: Record<string, unknown> }>;
    side_effects: Array<{ type: string; config?: Record<string, unknown> }>;
    approval_flow_id: string | null; sort_order: number }>;
}

export const ApprovalAPI = {
  pending: (slug: string, params: { cursor?: string } = {}) =>
    api.get<ApprovalRow[]>(`workspaces/${slug}/approvals/pending/`, { params }),
  pendingCount: (slug: string) =>
    api.get<{ count: number }>(`workspaces/${slug}/approvals/pending/`, { params: { count_only: 1 } }),
  acted: (slug: string, params: { cursor?: string } = {}) =>
    api.get<ApprovalRow[]>(`workspaces/${slug}/approvals/acted/`, { params }),
  mine: (slug: string, params: { cursor?: string } = {}) =>
    api.get<ApprovalRow[]>(`workspaces/${slug}/approvals/mine/`, { params }),
  instance: (slug: string, projectId: string, aid: string) =>
    api.get<ApprovalInstanceDetail>(
      `workspaces/${slug}/projects/${projectId}/approval-instances/${aid}/`),
  act: (slug: string, projectId: string, aid: string, payload: { action: string; comment?: string }) =>
    api.post(`workspaces/${slug}/projects/${projectId}/approval-instances/${aid}/actions/`, payload),
  issueApprovals: (slug: string, projectId: string, issueId: string) =>
    api.get<ApprovalRow[]>(
      `workspaces/${slug}/projects/${projectId}/issues/${issueId}/approvals/`),
};

/** WF-002 §4.6 实例详情（时间线）。 */
export interface ApprovalInstanceDetail extends ApprovalRow {
  from_state: { id: string };
  nodes: Array<{ level: number; pass_mode: string; approver_type: string;
    approver_config: Record<string, unknown> }>;
  records: Array<{ level: number; approver_id: string; approver_name: string;
    action: string; comment: string; acted_at: string | null }>;
}

export const WorklogAPI = {
  submit: (slug: string, projectId: string, weekStart: string) =>
    api.post(`workspaces/${slug}/projects/${projectId}/worklog-approvals/submit/`, { week_start: weekStart }),
  approve: (slug: string, projectId: string, aid: string) =>
    api.post(`workspaces/${slug}/projects/${projectId}/worklog-approvals/${aid}/approve/`),
  reject: (slug: string, projectId: string, aid: string, note: string) =>
    api.post(`workspaces/${slug}/projects/${projectId}/worklog-approvals/${aid}/reject/`, { note }),
  revoke: (slug: string, projectId: string, aid: string, note: string) =>
    api.post(`workspaces/${slug}/projects/${projectId}/worklog-approvals/${aid}/revoke/`, { note }),
  queue: (slug: string, projectId: string) =>
    api.get<Array<{ id: string; actor_id: string; actor_name: string; week_start: string;
      status: string; review_note: string; submitted_at: string | null }>>(
      `workspaces/${slug}/projects/${projectId}/worklog-approvals/`, { params: { scope: "queue" } }),
  ledger: (slug: string, projectId: string) =>
    api.get<Array<{ actor_id: string; actor_name: string; week_start: string;
      total_minutes: number; task_count: number; over_8h_days: number; is_frozen: boolean }>>(
      `workspaces/${slug}/projects/${projectId}/worklog-ledger/`),
};

/** ── Sprint-7 补口（2026-09-09 UI parity 收口轮）──────────────────────
 *  审批流定义（画布侧栏挂接）/ 模板库（WF-005）/ 自动化规则（WF-003）/
 *  审计留痕（WF-006）四组端点此前仅种子脚本/jmeter 消费，无前端接线。 */

export const ApprovalFlowAPI = {
  list: (slug: string, projectId: string) =>
    api.get<Array<{ id: string; name: string; is_active: boolean; node_count?: number }>>(
      `workspaces/${slug}/projects/${projectId}/approval-flows/`),
};

export const TemplateAPI = {
  list: (slug: string) =>
    api.get<Array<{ id: string; name: string; description: string; is_builtin: boolean;
      status: string; version: number; state_count: number; updated_at: string }>>(
      `workspaces/${slug}/workflow-templates/`),
  /** 两步下发第一步：预演（BR-05 状态映射回显）；confirm=true 实例化。 */
  distribute: (slug: string, projectId: string, templateId: string, confirm = false) =>
    api.post(`workspaces/${slug}/projects/${projectId}/workflow-templates/`,
      { template_id: templateId, confirm }, { params: undefined }),
};

export const AutomationAPI = {
  list: (slug: string, projectId: string) =>
    api.get<Array<{ id: string; name: string; is_active: boolean; trigger: { type: string; config?: Record<string, unknown> };
      conditions: Array<Record<string, unknown>>; actions: Array<{ type: string; config?: Record<string, unknown> }>;
      last_run_at: string | null }>>(`workspaces/${slug}/projects/${projectId}/automation-rules/`),
  create: (slug: string, projectId: string, payload: Record<string, unknown>) =>
    api.post(`workspaces/${slug}/projects/${projectId}/automation-rules/`, payload),
  patch: (slug: string, projectId: string, ruleId: string, payload: Record<string, unknown>) =>
    api.patch(`workspaces/${slug}/projects/${projectId}/automation-rules/${ruleId}/`, payload),
  remove: (slug: string, projectId: string, ruleId: string) =>
    api.delete(`workspaces/${slug}/projects/${projectId}/automation-rules/${ruleId}/`),
  /** Dry Run（WF-003 §2.4）：按规则 0 写预演，回显将命中/将执行的动作。 */
  dryRun: (slug: string, projectId: string, ruleId: string, issueId: string) =>
    api.post<Record<string, unknown>>(
      `workspaces/${slug}/projects/${projectId}/automation-rules/${ruleId}/dry-run/`, { issue_id: issueId }),
  /** 运行日志（§4.5：?rule=&status=&from=&to=；-created_at,-id）。 */
  runs: (slug: string, projectId: string, params: { rule?: string; status?: string; from?: string; to?: string } = {}) =>
    api.get<Array<Record<string, unknown>>>(
      `workspaces/${slug}/projects/${projectId}/automation-runs/`, { params }),
};

export const ViewGovernanceAPI = {
  lock: (slug: string, projectId: string, viewId: string,
         body: { is_locked: boolean; is_project_default?: boolean | null }) =>
    api.post(`workspaces/${slug}/projects/${projectId}/views/${viewId}/lock/`, body),
  duplicate: (slug: string, projectId: string, viewId: string) =>
    api.post(`workspaces/${slug}/projects/${projectId}/views/${viewId}/duplicate/`),
  pin: (slug: string, projectId: string, viewId: string) =>
    api.post(`workspaces/${slug}/projects/${projectId}/views/${viewId}/pin/`),
  unpin: (slug: string, projectId: string, viewId: string) =>
    api.delete(`workspaces/${slug}/projects/${projectId}/views/${viewId}/pin/`),
  setAccess: (slug: string, projectId: string, viewId: string, access: "personal" | "shared") =>
    api.patch(`workspaces/${slug}/projects/${projectId}/views/${viewId}/`, { access }),
};

export const DepartmentAPI = {
  list: (slug: string, include = false) =>
    api.get<Array<Record<string, unknown>>>(`workspaces/${slug}/departments/`,
      include ? { params: { include: "member_count" } } : {}),
  create: (slug: string, body: { name: string; parent_id?: string | null }) =>
    api.post(`workspaces/${slug}/departments/`, body),
  patch: (slug: string, id: string, body: Record<string, unknown>) =>
    api.patch(`workspaces/${slug}/departments/${id}/`, body),
  delete: (slug: string, id: string) =>
    api.delete(`workspaces/${slug}/departments/${id}/`),
  move: (slug: string, id: string, parent_id: string | null) =>
    api.post(`workspaces/${slug}/departments/${id}/move/`, { parent_id }),
  bulkMove: (slug: string, id: string, member_ids: string[], department_id: string | null) =>
    api.post(`workspaces/${slug}/departments/${id}/members/bulk-move/`, { member_ids, department_id }),
  grantPreview: (slug: string, id: string, body: Record<string, unknown>) =>
    api.post(`workspaces/${slug}/departments/${id}/grants/preview/`, body),
  grant: (slug: string, id: string, body: Record<string, unknown>) =>
    api.post(`workspaces/${slug}/departments/${id}/grants/`, body),
  stats: (slug: string, id: string) =>
    api.get<Record<string, unknown>>(`workspaces/${slug}/departments/${id}/stats/`),
};

export const CustomRoleAPI = {
  list: (slug: string, projectId: string, params: Record<string, unknown> = {}) =>
    api.get<Array<Record<string, unknown>>>(`workspaces/${slug}/projects/${projectId}/roles/`, { params }),
  create: (slug: string, projectId: string, body: Record<string, unknown>) =>
    api.post(`workspaces/${slug}/projects/${projectId}/roles/`, body),
  patch: (slug: string, projectId: string, roleId: string, body: Record<string, unknown>) =>
    api.patch(`workspaces/${slug}/projects/${projectId}/roles/${roleId}/`, body),
  delete: (slug: string, projectId: string, roleId: string) =>
    api.delete(`workspaces/${slug}/projects/${projectId}/roles/${roleId}/`),
  catalog: (slug: string, projectId: string) =>
    api.get<Record<string, unknown>>(`workspaces/${slug}/projects/${projectId}/roles/permissions-catalog/`),
  assign: (slug: string, projectId: string, memberId: string, roleId: string) =>
    api.post(`workspaces/${slug}/projects/${projectId}/members/${memberId}/role-assignments/`, { role_id: roleId }),
  revoke: (slug: string, projectId: string, memberId: string, roleId: string) =>
    api.delete(`workspaces/${slug}/projects/${projectId}/members/${memberId}/role-assignments/${roleId}/`),
  assignments: (slug: string, projectId: string, memberId: string) =>
    api.get<Array<Record<string, unknown>>>(`workspaces/${slug}/projects/${projectId}/members/${memberId}/role-assignments/`),
  effective: (slug: string, projectId: string, memberId: string) =>
    api.get<Record<string, unknown>>(`workspaces/${slug}/projects/${projectId}/members/${memberId}/effective-permissions/`),
  bulkAssign: (slug: string, projectId: string, roleId: string, body: Record<string, unknown>) =>
    api.post(`workspaces/${slug}/projects/${projectId}/roles/${roleId}/assignments/bulk/`, body),
};

export const SiteAuditAPI = {
  list: (slug: string, params: Record<string, unknown> = {}) =>
    api.get<Array<Record<string, unknown>>>(`workspaces/${slug}/audit-logs/`, { params }),
  catalog: (slug: string) =>
    api.get<Array<Record<string, unknown>>>(`workspaces/${slug}/audit-logs/catalog/`),
  exportCsv: (slug: string, password: string) =>
    api.post(`workspaces/${slug}/audit-logs/exports/`, { password }, { responseType: "blob" }),
};

export const AuditAPI = {
  events: (slug: string, projectId: string, params: { page?: number } = {}) =>
    api.get<Array<Record<string, unknown>>>(
      `workspaces/${slug}/projects/${projectId}/approval-audit/`, { params }),
  verify: (slug: string, projectId: string) =>
    api.get<{ chain_intact: boolean; broken_event_ids: number[] }>(
      `workspaces/${slug}/projects/${projectId}/approval-audit/verify/`),
  /** CSV 流式导出（WF-006 §2.3）——blob 下载。 */
  exportCsv: (slug: string, projectId: string) =>
    api.get(`workspaces/${slug}/projects/${projectId}/approval-audit/export/`, { responseType: "blob" }),
};
