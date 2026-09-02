import { type RouteConfig, index, layout, route } from "@react-router/dev/routes";

export default [
  layout("layouts/public.tsx", [
    index("routes/home.tsx"),
    route("login", "routes/login.tsx"),
    route("register", "routes/register.tsx"),
  ]),
  layout("layouts/app.tsx", [
    route(":workspaceSlug", "routes/workspace.tsx"),
    route(":workspaceSlug/projects", "routes/projects-list.tsx"),
    route(":workspaceSlug/projects/new", "routes/project-new.tsx"),
    route(":workspaceSlug/projects/:projectId", "routes/project.tsx"),
    route(":workspaceSlug/projects/:projectId/board", "routes/board.tsx"),
    route(":workspaceSlug/projects/:projectId/issues", "routes/issues-list.tsx"),
    route(":workspaceSlug/projects/:projectId/settings", "routes/project-settings.tsx"),
  ]),
] satisfies RouteConfig;
