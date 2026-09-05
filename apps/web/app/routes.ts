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
    route(":workspaceSlug/projects/:projectId/settings", "routes/project-settings.tsx"),
    ...settingsRoutes,
    ...teamRoutes,
    ...workbenchRoutes,
    route("*", "routes/not-found.tsx"),
  ]),
] satisfies RouteConfig;
