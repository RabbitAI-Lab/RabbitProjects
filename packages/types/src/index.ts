/** 核心领域类型 —— 手写基线；INFRA-003 交付 OpenAPI 后由 gen:api-types 生成 src/generated 并逐步替换。 */

export type UUID = string;

/** 状态五语义组（unified-issue-model.md §5.2） */
export type StateGroup = "backlog" | "unstarted" | "started" | "completed" | "cancelled";

export interface State {
  id: UUID;
  name: string;
  group: StateGroup;
  color: string;
  is_default: boolean;
}

export interface WorkspaceSummary {
  id: UUID;
  name: string;
  slug: string;
  /** 当前用户在该团队的角色等级（rbac §2.2：20/15/10/5） */
  role: number;
}

export interface ProjectSummary {
  id: UUID;
  name: string;
  identifier: string;
  description?: string;
  status: "active" | "archived";
  total_issues: number;
  /** PROJ-002 §4.2.1：注水字段（用户对该项目的收藏） */
  is_favorite?: boolean;
  /** PROJ-002 §4.2.1：当前用户有效项目角色（0=无显式行） */
  current_user_role?: number;
  /** PROJ-002 §4.2.1：显式 ProjectMember 数（隐式管理员不计入） */
  total_members?: number;
  updated_at?: string;
  created_at?: string;
}

/** 列表响应元信息（PROJ-002 §4.2.1 / api-conventions §6.3） */
export interface ProjectListMeta {
  count: number;
  total_count: number;
  favorite_count: number;
  page: number;
  per_page: number;
  next_page_results: boolean;
  prev_page_results: boolean;
  next_cursor: string | null;
  prev_cursor: string | null;
  total_pages: number;
}

/** TEAM-002 §4.2.2：工作空间成员（嵌套 user） */
export interface WorkspaceMember {
  id: UUID;
  user: {
    id: UUID;
    display_name: string;
    email: string;
    avatar_url: string | null;
  };
  /** 20/15/10/5（WorkspaceRole） */
  role: number;
  is_active: boolean;
  joined_at: string;
  is_owner: boolean;
}

/** TEAM-002 §4.2.1：批量邀请逐条结果 */
export type InviteResultStatus = "added" | "invited" | "skipped" | "failed";
export interface InviteResult {
  email: string;
  status: InviteResultStatus;
  member_id?: UUID;
  invite_id?: UUID;
  expires_at?: string;
  refreshed?: boolean;
  reason?: "already_member" | "duplicate_in_request" | "member_limit";
  message?: string;
}

/** TEAM-002 §4.2 待接受邀请（Lite） */
export interface WorkspaceInvite {
  id: UUID;
  email: string;
  role: number;
  status: "pending" | "accepted" | "revoked" | "expired";
  expires_at: string;
  created_at: string;
  invited_by: { id: UUID; display_name: string; email: string } | null;
}

/** PROJ-002 §4.2.3：项目成员（含该成员的工作空间角色） */
export interface ProjectMember {
  id: UUID;
  user: {
    id: UUID;
    display_name: string;
    email: string;
    avatar_url: string | null;
  };
  role: number;
  workspace_role: number | null;
  is_active: boolean;
  joined_at: string;
  /** 本项目中指派给该成员的任务数（PROJ-002 §3.2 BR-07：移除确认弹窗「其名下 N 个任务指派将保留」） */
  assigned_issue_count?: number;
}

/** 与后端 IssueSerializer（apps/api/plane/app/serializers/issue.py）对齐。
 *  该类型曾长期落后于后端：`assignee` 从未下发却一直存在，于是前端 4 处
 *  `issue.assignee` 全在编译期"合法"地静默失效；`assignee_ids` / `priority` /
 *  `start_date` 等真实字段反倒缺失，只能靠 `as unknown as` 强转绕过。
 *  新增字段一律可选，避免打断存量构造点。 */
export interface Issue {
  id: UUID;
  project: UUID;
  project_id?: UUID;
  project_identifier?: string;
  sequence_id: number;
  /** 服务端下发完整编号（如 TZXM-1），前端不拼接 */
  issue_key: string;
  name: string;
  description_html: string;
  description_stripped: string | null;
  description_json?: Record<string, unknown>;
  state: State;
  state_id: UUID;
  state_name: string;
  state_group: "backlog" | "unstarted" | "started" | "completed" | "cancelled";
  type_id?: UUID | null;
  parent_id?: UUID | null;
  /** Issue.Priority 五档（models/issue.py） */
  priority?: "none" | "low" | "medium" | "high" | "urgent" | null;
  assignee_ids?: UUID[];
  label_ids?: UUID[];
  /** @deprecated 后端 IssueSerializer **从不下发**该字段（只有 `assignee_ids`）。
   *  保留仅为存量渲染代码过渡；新代码一律用 `assignee_ids` 解析成员，否则永远取不到值。 */
  assignee: { id: UUID; name: string; avatar_url: string | null } | null;
  start_date?: string | null;
  /** 截止日期（全局裁决 C：字段名 target_date，后端 IssueSerializer 同名） */
  target_date: string | null;
  completed_at?: string | null;
  sort_order: number;
  sub_issues_count?: number;
  completed_sub_issues_count?: number;
  attachment_count?: number;
  archived_at?: string | null;
  created_by: { id: UUID; name: string };
  created_at: string;
  updated_at: string;
}

/** 统一响应信封（api-conventions.md §4） */
export interface ApiEnvelope<T> {
  status: boolean;
  data: T;
  meta: Record<string, unknown> | null;
}
