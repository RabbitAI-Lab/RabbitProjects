/** 工作台与全局表面（RPT-001 / AUTH-005 / INFRA-004）——附录 C.35 个人工作台、
 *  C.14 403 路由页、C.36 错误空态页。 */
import { type RouteConfigEntry, route } from "@react-router/dev/routes";

export const workbenchRoutes: RouteConfigEntry[] = [
  // 三条 Sprint-7 路由均挂 workspace 前缀（侧栏/画布入口按 /:ws/… 生成链接，页面取 workspaceSlug 参数）
  route(":workspaceSlug/approvals", "routes/approvals.tsx"), // WF-002 §3.1 审批中心（C.140，Sprint-7）
  // WF-005 §3（C.145，2026-09-09 入口补口轮）：WS 级模板库——预设四套 + 两步下发
  route(":workspaceSlug/workflow-templates", "routes/workflow-templates.tsx"),
  // WF-001 §3.1 画布（C.141）——项目域嵌套路径
  route(":workspaceSlug/projects/:projectId/workflows/:wfId/canvas", "routes/workflow-canvas.tsx"),
  // WF-001/WF-005（C.144，补口轮）：工作流列表（画布入口/新建草稿/归档）
  route(":workspaceSlug/projects/:projectId/workflows", "routes/workflows-list.tsx"),
  // WF-003 §4.5（C.146，补口轮）：自动化规则（列表/启停/Dry Run/运行日志）
  route(":workspaceSlug/projects/:projectId/automation", "routes/automation-rules.tsx"),
  // WF-006 §3（C.148，补口轮）：审批留痕审计（链校验/事件/CSV 导出）
  route(":workspaceSlug/projects/:projectId/audit", "routes/project-audit.tsx"),
  // TASK-013 §3.1~3.3 工时（C.142）
  route(":workspaceSlug/projects/:projectId/worklog", "routes/worklog.tsx"),
  // AUTH-007 §3.1（C.150，Sprint-8 R6）：组织管理——部门树/成员归属/批量授权
  route(":workspaceSlug/org", "routes/org-structure.tsx"),
  // AUTH-010 §3.1（C.152，Sprint-8 R6）：全站审计日志——筛选/游标分页/导出
  route(":workspaceSlug/audit-logs", "routes/audit-logs.tsx"),
  // AUTH-008 §3.1/§3.2（C.151，Sprint-8 R6）：自定义角色与权限矩阵编辑器
  route(":workspaceSlug/projects/:projectId/roles", "routes/roles-admin.tsx"),
  // AUTH-009 §3.1（C.155，Sprint-8 R6）：SSO 配置（WS_OWNER 面）
  route(":workspaceSlug/settings/sso", "routes/sso-config.tsx"),
];
