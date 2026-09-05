import { api } from "./axios";
import type {
  InviteResult,
  ProjectMember,
  ProjectSummary,
  WorkspaceInvite,
  WorkspaceMember,
  WorkspaceSummary,
} from "@rp/types";

export type { Issue, ProjectSummary, WorkspaceSummary, WorkspaceMember, ProjectMember, WorkspaceInvite, InviteResult } from "@rp/types";

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
  list: (slug: string, projectId: string, params: { ordering?: string; group_by?: string; per_page?: number } = {}) =>
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
  }) => api.patch(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/`, payload),
  del: (slug: string, projectId: string, issueId: string) =>
    api.delete(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/`),
  detail: (slug: string, projectId: string, issueId: string) =>
    api.get(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/`),
  /** TASK-002 §4.3.4-6 + COLLAB-001 §4.3 + FILE-001 §4.3 子资源路由。
   *  注意：activities 返回的是**裸数组**（分页信息在 meta.next_cursor），不是 `{results:[]}`。
   *  行内操作人是**平铺的 actor_id / actor_name**，不是嵌套 `actor` 对象。 */
  activities: (slug: string, projectId: string, issueId: string, params: { cursor?: string; per_page?: number } = {}) =>
    api.get<ActivityRow[]>(
      `workspaces/${slug}/projects/${projectId}/issues/${issueId}/activities/`, { params }),
  subIssues: (slug: string, projectId: string, issueId: string) =>
    api.get<Array<{ id: string; issue_key: string; name: string; state_group: string; state_name: string }>>(
      `workspaces/${slug}/projects/${projectId}/issues/${issueId}/sub-issues/`),
  createSubIssue: (slug: string, projectId: string, issueId: string, payload: { name: string }) =>
    api.post<{ id: string; issue_key: string; name: string }>(
      `workspaces/${slug}/projects/${projectId}/issues/${issueId}/sub-issues/`, payload),
  setLabels: (slug: string, projectId: string, issueId: string, labelIds: string[]) =>
    api.put(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/labels/`, { label_ids: labelIds }),
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

/** COLLAB-001 §4.3 评论（C.32 + C.33）。
 *  actor 形状以后端 `CommentSerializer.get_actor` 为准：`{id, display_name, avatar_url}`。
 *  **没有** `name` / `author` 字段——前端曾按 `actor.name` 取首字母，取到 undefined 兜底成「?」。 */
export interface CommentActor {
  id: string | null;
  display_name: string;
  avatar_url?: string | null;
}
export interface CommentRow {
  id: string;
  actor?: CommentActor | null;
  comment_html: string;
  mention_ids?: string[];
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
  list: (slug: string, projectId: string, issueId: string) =>
    api.get<CommentRow[]>(
      `workspaces/${slug}/projects/${projectId}/issues/${issueId}/comments/`),
  create: (slug: string, projectId: string, issueId: string, payload: { comment_html: string }) =>
    api.post<{ id: string }>(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/comments/`, payload),
  patch: (slug: string, projectId: string, issueId: string, commentId: string, payload: { comment_html: string }) =>
    api.patch(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/comments/${commentId}/`, payload),
  del: (slug: string, projectId: string, issueId: string, commentId: string) =>
    api.delete(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/comments/${commentId}/`),
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
  /** POST .../attachments/presign/ —— 申请直传 URL */
  presign: (slug: string, projectId: string, issueId: string, payload: { file_name: string; file_size: number; content_type: string }) =>
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
