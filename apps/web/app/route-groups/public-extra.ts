/** 匿名可达页（AUTH-004 / TEAM-002 / AUTH-005）——附录 C.12 忘记密码、C.13 重置密码、
 *  C.17 邀请接受、C.14 403 页。
 *  挂在 public layout 下，不进工作空间布局——尤其 403，本就是「你无权」的提示页，
 *  不应在受 Guard 保护的工作空间布局里渲染（否则未登录用户直达 /403 会被跳 /login）。
 */
import { type RouteConfigEntry, route } from "@react-router/dev/routes";

export const publicExtraRoutes: RouteConfigEntry[] = [
  route("403", "routes/forbidden.tsx"),
  route("invite/:token", "routes/invite-accept.tsx"),
];
