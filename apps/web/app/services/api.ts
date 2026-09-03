import { api } from "./axios";
import type { Issue, ProjectSummary, WorkspaceSummary } from "@rp/types";

export type { Issue, ProjectSummary, WorkspaceSummary } from "@rp/types";

export interface MeEnvelope {
  user: { id: string; email: string; display_name: string; avatar_url: string; is_active: boolean };
  workspaces: WorkspaceSummary[];
  default_workspace_slug: string | null;
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

export const WorkspaceAPI = {
  list: () => api.get<WorkspaceSummary[]>("workspaces/"),
  create: (name: string, description?: string) =>
    api.post<ProjectSummary>("workspaces/", { name, description }),
  detail: (slug: string) => api.get(`workspaces/${slug}/`),
};

export const ProjectAPI = {
  listByWs: (slug: string) => api.get<ProjectSummary[]>(`workspaces/${slug}/projects/`),
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
};

export const IssueAPI = {
  list: (slug: string, projectId: string, params: { ordering?: string; group_by?: string; per_page?: number } = {}) =>
    api.get(`workspaces/${slug}/projects/${projectId}/issues/`, { params }),
  create: (slug: string, projectId: string, payload: {
    name: string; state_id?: string; target_date?: string; assignee_ids?: string[]; description_html?: string;
  }) => api.post(`workspaces/${slug}/projects/${projectId}/issues/`, payload),
  patch: (slug: string, projectId: string, issueId: string, payload: {
    name?: string; state_id?: string; sort_order?: number; target_date?: string; assignee_ids?: string[];
  }) => api.patch(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/`, payload),
  del: (slug: string, projectId: string, issueId: string) =>
    api.delete(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/`),
  detail: (slug: string, projectId: string, issueId: string) =>
    api.get(`workspaces/${slug}/projects/${projectId}/issues/${issueId}/`),
};
