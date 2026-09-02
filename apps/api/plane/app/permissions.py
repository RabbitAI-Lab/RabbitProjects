from rest_framework.permissions import BasePermission

from plane.db.models.roles import WorkspaceRole


class IsAuthenticated(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)


class IsWorkspaceOwnerOrAdmin(BasePermission):
    """WORKSPACE OWNER/ADMIN 才能修改。WS_OWNER/ADMIN 隐式 = 全项目 ADMIN。"""

    message = "PERM_WORKSPACE_ADMIN_REQUIRED"

    def has_object_permission(self, request, view, obj):
        role = getattr(obj, "current_user_role", None)
        return bool(role and role >= WorkspaceRole.ADMIN)
