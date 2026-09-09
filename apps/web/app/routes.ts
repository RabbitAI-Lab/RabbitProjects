import { type RouteConfig, index, layout, route } from "@react-router/dev/routes";

import { permissionRoutes } from "./route-groups/permissions";
import { publicExtraRoutes } from "./route-groups/public-extra";
import { settingsRoutes } from "./route-groups/settings";
import { teamRoutes } from "./route-groups/team";
import { workbenchRoutes } from "./route-groups/workbench";

// sprint-1 起路由按功能域拆分到 app/route-groups/*.ts（见该目录 README）：
// 一次性新增的页面很多，平铺在本文件会让并行实现互相踩踏。
// 本文件只做汇总与布局归属，具体页面在各自分组内增删。
export default [
  layout("layouts/public.tsx", [
    index("routes/home.tsx"),
    route("login", "routes/login.tsx"),
    route("sso/claim", "routes/sso-claim.tsx"),  // AUTH-009 认领页（公开）
    route("register", "routes/register.tsx"),
    // C.26 标签管理面板的调试入口（生产由列表 / 看板筛选条「标签」下拉尾部触发）
    route("labels-admin", "routes/labels-admin.tsx"),
    ...publicExtraRoutes,
  ]),
  layout("layouts/app.tsx", [
    route(":workspaceSlug", "routes/workspace.tsx"),
    route(":workspaceSlug/projects", "routes/projects-list.tsx"),
    route(":workspaceSlug/projects/new", "routes/project-new.tsx"),
    route(":workspaceSlug/projects/:projectId", "routes/project.tsx"),
    route(":workspaceSlug/projects/:projectId/board", "routes/board.tsx"),
    route(":workspaceSlug/projects/:projectId/issues", "routes/issues-list.tsx"),
    // Sprint-3（BOARD-003 §4.4）：表格布局（list/kanban/table 三段 + gantt 占位禁用）
    route(":workspaceSlug/projects/:projectId/table", "routes/table.tsx"),
    // Sprint-4（GANTT-001 §3.1 / C.98）：甘特视图（替换 table 路由 gantt 占位的禁用态）
    route(":workspaceSlug/projects/:projectId/gantt", "routes/gantt.tsx"),
    // Sprint-4（FILE-002 §3.1 / C.112）：项目文件库 + 回收站（左树右表双视图）
    route(":workspaceSlug/projects/:projectId/files", "routes/files.tsx"),
    route(":workspaceSlug/projects/:projectId/files/trash", "routes/files-trash.tsx"),
    // Sprint-3 Phase 3-C（COLLAB-003 §3.1）：项目动态流页（侧栏「动态」入口）
    route(":workspaceSlug/projects/:projectId/activity", "routes/activity.tsx"),
    route(":workspaceSlug/projects/:projectId/settings", "routes/project-settings.tsx"),
    // Sprint-2（TASK-008 §3.1 / C.52）：项目设置 → 字段
    route(":workspaceSlug/projects/:projectId/settings/fields", "routes/project-fields.tsx"),
    route(":workspaceSlug/projects/:projectId/stats", "routes/project-stats.tsx"),
    route(":workspaceSlug/projects/:projectId/settings/integrations", "routes/project-integrations.tsx"),
    route(":workspaceSlug/projects/:projectId/settings/webhooks", "routes/project-webhooks.tsx"),
    // Sprint-2（TASK-010 §3.2 / C.62）：admin 死信补偿页（系统级顶层资源，不嵌 workspace）
    route("admin/dead-letters", "routes/admin-dead-letters.tsx"),
    ...settingsRoutes,
    ...teamRoutes,
    ...workbenchRoutes,
    route("*", "routes/not-found.tsx"),
  ]),
] satisfies RouteConfig;
