/** 权限门控域路由（AUTH-005）——PermissionGate 组件族本身不是路由，放
 *  app/components/；/403 路由页移到 public-extra（匿名可达）。
 *
 *  PermissionRouteGuard 的重定向落点：直接 nav("/403?required=…")，
 *  由于 /403 不在工作空间 layout 下、未登录也能正确渲染（§3.4）。
 */
export const permissionRoutes = [];
