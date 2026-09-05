/** 团队与项目成员域路由（TEAM-002 / PROJ-002）——附录 C.15 团队成员页、
 *  C.17 邀请接受页（在 public 分组）、C.20 项目设置·成员 Tab。 */
import { type RouteConfigEntry, route } from "@react-router/dev/routes";

export const teamRoutes: RouteConfigEntry[] = [
  // C.15 团队成员设置页（ADR-0011 #18：侧栏「团队设置」点亮为该入口）
  route(":workspaceSlug/settings/members", "routes/team-members.tsx"),
  // C.20 项目设置·成员 Tab（基线=C.8 设置页新增第 2 区块）
  route(
    ":workspaceSlug/projects/:projectId/settings/members",
    "routes/project-members.tsx",
  ),
];
