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
  /** 当前用户在该团队的角色 label */
  role: string;
}

export interface ProjectSummary {
  id: UUID;
  name: string;
  identifier: string;
  status: "active" | "archived";
  total_issues: number;
}

export interface Issue {
  id: UUID;
  project: UUID;
  sequence_id: number;
  /** 服务端下发完整编号（如 TZXM-1），前端不拼接 */
  issue_key: string;
  name: string;
  description_html: string;
  description_stripped: string;
  state: State;
  assignee: { id: UUID; name: string; avatar_url: string | null } | null;
  due_date: string | null;
  sort_order: number;
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
