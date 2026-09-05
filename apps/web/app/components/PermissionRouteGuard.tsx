/** AUTH-005 §3.1 / §3.3 路由级权限守卫 —— 直达无权 URL 重定向 /403。
 *
 *  清单行：C.14 路由守卫（PermissionRouteGuard 包裹路由，例 /:workspaceSlug/settings/members
 *  要求 workspace.member.manage），直达 URL 重定向 /403、不白屏。
 *
 *  BR-12：判定失败重定向 `/403?required=<permission>` 记录上下文，便于反馈。
 */
import { Navigate } from "react-router";
import { observer } from "mobx-react-lite";
import type { ReactNode } from "react";
import { useStores } from "../stores";
import { type PermissionKey, type Scope } from "../stores/permission";
import { GateSkeleton } from "./PermissionGate";

export interface PermissionRouteGuardProps {
  permission: PermissionKey;
  scope?: Scope;
  resourceId?: string;
  children: ReactNode;
}

export const PermissionRouteGuard = observer(function PermissionRouteGuard({
  permission,
  scope = "project",
  resourceId,
  children,
}: PermissionRouteGuardProps) {
  const { permission: store } = useStores();

  // §3.4 Gate 加载骨架：权限数据未到 → 不渲染（避免权限闪烁），aria-busy 走 GateSkeleton
  if (store.snapshot === null) {
    return (
      <div role="status" aria-busy="true" aria-live="polite" className="p-6">
        <GateSkeleton width={120} height={24} />
      </div>
    );
  }

  // §3.3 / BR-12：判定失败 → /403?required=<permission>，不静默白屏
  if (!store.can(permission, scope, resourceId)) {
    return <Navigate to={`/403?required=${encodeURIComponent(permission)}`} replace />;
  }
  return <>{children}</>;
});
