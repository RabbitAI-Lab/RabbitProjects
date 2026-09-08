/** 工作台与全局表面（RPT-001 / AUTH-005 / INFRA-004）——附录 C.35 个人工作台、
 *  C.14 403 路由页、C.36 错误空态页。 */
import { type RouteConfigEntry, route } from "@react-router/dev/routes";

export const workbenchRoutes: RouteConfigEntry[] = [
  route("approvals", "routes/approvals.tsx"), // WF-002 §3.1 审批中心（C.140，Sprint-7）
  // WF-001 §3.1 画布（C.141）——项目域嵌套路径
  route("projects/:projectId/workflows/:wfId/canvas", "routes/workflow-canvas.tsx"),
  // TASK-013 §3.1~3.3 工时（C.142）
  route("projects/:projectId/worklog", "routes/worklog.tsx"),
];
