/** 个人设置域路由（AUTH-004）——附录 C.10 设置壳 / C.11 安全页。
 *  匿名可达的 C.12 忘记密码、C.13 重置密码在 public 分组。 */
import { type RouteConfigEntry, route } from "@react-router/dev/routes";

export const settingsRoutes: RouteConfigEntry[] = [
  route("settings/profile", "routes/settings-profile.tsx"),
  route("settings/security", "routes/settings-security.tsx"),
];
