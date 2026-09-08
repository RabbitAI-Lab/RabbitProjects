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
    /** 账号启停（AUTH-006 §3.3 灰标口径） */
    is_active: boolean;
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
  /** Sprint-5（INTG-001 §3.3）：GitHub 内联聚合——{ number, prs: [], commits: [] }。 */
  github_context?: {
    number?: number;
    prs?: Array<{ number: number; title?: string; url?: string; merged_at?: string }>;
    commits?: Array<{ sha: string; message: string; url?: string; author?: string }>;
  } | null;
  /** TASK-006 §4.2.3：估算（分钟；IssueSerializer 只读下发，写走 PATCH estimate_minutes）。 */
  estimate_minutes?: number | null;
  /** TASK-006：已耗（分钟；列表/详情 queryset annotate，缺省 0）。 */
  spent_minutes?: number;
  /** Sprint-7（WF-002 §3.5，补口轮）：任务有进行中的审批实例（列表 annotate；
   *  看板/列表「审」徽标数据源，详情上下文缺省 undefined）。 */
  has_pending_approval?: boolean;
  /** TASK-008 §4.2.4：自定义字段值（JSONB 整列透出；停用字段的值保留在响应中由 UI 过滤）。 */
  custom_fields?: Record<string, unknown>;
  created_by: { id: UUID; name: string };
  created_at: string;
  updated_at: string;
  /** TASK-004 §4.2.1：只读派生深度（根=1）。后端 IssueSerializer 当前未下发该字段
   *  （仅 subtree 节点携带相对根 depth）——前端树形行深度由懒加载层级推导，不在此累加。 */
  depth?: number;
}

/** TASK-004 §4.2.2 `GET …/issues/{id}/subtree/` 响应（axios 解包后的 data 形状）。
 *  - nodes 为平铺（非嵌套），depth 为**相对根层数（根=0）**，与业务 depth（根=1）相差 1；
 *  - stats 含根口径（total/completed 均计入根），truncated=true 时无 stats；
 *  - truncated/node_limit 在响应 meta（axios 拦截器挂在 r.meta）。 */
export interface SubtreeNode {
  id: UUID;
  parent_id: UUID | null;
  issue_key: string;
  sequence_id: number;
  name: string;
  state_group: StateGroup | null;
  assignee_ids: UUID[];
  /** 相对根层数（根=0） */
  depth: number;
}

export interface SubtreeRoot extends SubtreeNode {
  sub_issues_count: number;
  completed_sub_issues_count: number;
}

export interface SubtreeStats {
  total: number;
  completed: number;
  cancelled: number;
  max_depth: number;
  /** TASK-006 §4.2.4：stats 契约加字段（向后兼容）——子树口径工时汇总。 */
  subtree_spent_minutes?: number;
  subtree_estimate_minutes?: number;
}

export interface SubtreeData {
  root: SubtreeRoot | null;
  nodes: SubtreeNode[];
  stats?: SubtreeStats;
}

export interface SubtreeMeta {
  truncated: boolean;
  node_limit: number;
}

/** TASK-004 §4.2.5 `DELETE …/issues/{id}/` 200 响应（级联软删回传受影响数）。 */
export interface DeleteSubtreeResult {
  deleted_count: number;
  descendant_ids: UUID[];
}

/** 统一响应信封（api-conventions.md §4） */
export interface ApiEnvelope<T> {
  status: boolean;
  data: T;
  meta: Record<string, unknown> | null;
}

// ─────────────────────────────────────────────────────────────────────
// COLLAB-004 实时通道契约（§2.3 事件协议 / §2.5 关闭码 / §1.3 房间模型）
// —— 前端 RealtimeClient / LiveEventBus（Phase 3）与 apps/live 共用的唯一口径。
// ─────────────────────────────────────────────────────────────────────

/** 业务事件名（§2.3 表核心六类 + file 域五事件补登；与 api 侧 EVENT_MAP 对齐） */
export type LiveEventName =
  | "issue.updated"
  | "issue.state.changed"
  | "board.moved"
  | "comment.created"
  | "activity.created"
  | "notification.created"
  // ── file 域五事件（Sprint-4 FILE-003 §4.4 / FILE-004 BR-13 登记，
  //    COLLAB-004 §2.3 补登；rooms = project + file:{asset_id}）──
  | "file.version.created"
  | "file.transcode.completed"
  | "file.share.created"
  | "file.share.revoked"
  | "file.share.extended";

/** 全部下行事件名（含连接确认与 presence，非业务六类） */
export type LiveServerEventName = LiveEventName | "connected" | "presence.joined" | "presence.left";

/** 房间名构造（§1.3：project / issue / user 三类 + file 第四类（FILE-003 §4.4
 *  登记，订阅条件在换票时校验）；file 房间订阅条件 = file.read + 文件可见性） */
export const liveRoom = {
  project: (projectId: UUID) => `project:${projectId}` as const,
  issue: (issueId: UUID) => `issue:${issueId}` as const,
  file: (assetId: UUID) => `file:${assetId}` as const,
  user: (userId: UUID) => `user:${userId}` as const,
} as const;

/** WS 关闭码（§2.5 异常表；前端按码选择重换票 / 退避重连 / 提示） */
export const LIVE_CLOSE_CODES = {
  /** 重复连接：每用户全局第 6 条连接踢最旧（BR-12） */
  DUP_SESSION: 4000,
  /** 票据无效/过期：重换票 ≤ 2 次后降级（BR-01） */
  TOKEN_INVALID: 4001,
  /** 权限失效：60s 复核被踢出全部房间（BR-03） */
  FORBIDDEN: 4003,
  /** 心跳超时：60s 无 pong 服务端清理（BR-04） */
  HEARTBEAT_TIMEOUT: 4004,
} as const;

/** 事件信封公共字段（§1.4；seq 房间级单调递增、重启归零，仅乱序提示用）。
 *  connected 确认帧与 presence 帧不隶属单一房间，room 可缺省。 */
export interface LiveEnvelope<P = Record<string, unknown>> {
  event: LiveServerEventName;
  seq: number;
  room?: string;
  payload: P;
  occurred_at: string;
}

/** payload.actor_id：操作者用户 ID（live 据此跳过本人连接——BR-08 不回显） */
export interface LiveActorPayload {
  actor_id: UUID | null;
}

export interface IssueUpdatedPayload extends LiveActorPayload {
  issue_id: UUID;
  /** 实体 updated_at（ISO）——前端比对本地版本，旧于等于本地忽略（BR-07） */
  version: string;
  /** 变更域提示（字段族），定向 patch 范围选择依据 */
  brief: string;
  /** 批量操作时逐实体附带（同批共享 epoch 同值；单条不携带，BR-15） */
  batch_id?: number;
}

export interface IssueStateChangedPayload extends IssueUpdatedPayload {
  from_group: StateGroup | null;
  to_group: StateGroup | null;
}

export interface BoardMovedPayload extends LiveActorPayload {
  issue_id: UUID;
  from_group: StateGroup | null;
  to_group: StateGroup | null;
  /** 目标列排序版本（该列最近一次 sort_order 写入时刻，§2.3 注 3） */
  column_version: string;
  batch_id?: number;
}

export interface CommentCreatedPayload extends LiveActorPayload {
  comment_id: UUID;
  issue_id: UUID;
}

export interface ActivityCreatedPayload extends LiveActorPayload {
  issue_id: UUID;
  /** 动态流水位锚（COLLAB-003 stream_cursor 形态 `{created_at}:{id}`）——按水位增量拉取正文 */
  stream_cursor: string;
  occurred_at: string;
}

export interface NotificationCreatedPayload {
  notification_id: UUID;
  unread_delta: number;
}

/** 连接确认载荷（§2.1：握手成功即下发房间集与心跳间隔） */
export interface ConnectedPayload {
  rooms: string[];
  ws: string;
  heartbeat: number;
}

/** presence 广播载荷（BR-11：user 摘要 ≤200B，仅 project 房间） */
export interface PresencePayload {
  room: string;
  user: {
    id: UUID;
    display_name: string;
  };
}
