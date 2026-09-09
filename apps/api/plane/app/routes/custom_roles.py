"""custom_roles 域路由片段（AUTH-008 — 自定义角色组，Sprint-8 R2）。

9 端点：角色 CRUD（含 ?name= 精确解析）/ 可勾选目录 / 挂接清单 + 挂接 /
卸除 / 批量挂接 / 有效权限并集。
"""
from django.urls import path

from plane.app.views.custom_roles import (
    CustomRoleDetailView,
    CustomRoleListCreateView,
    EffectivePermissionsView,
    PermissionsCatalogView,
    RoleAssignmentDeleteView,
    RoleAssignmentView,
    RoleBulkAssignView,
)

urlpatterns = [
    # 可勾选目录（先于 detail 注册，防 UUID 路径吞字面量段）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/roles/permissions-catalog/",
        PermissionsCatalogView.as_view(),
        name="roles-permissions-catalog",
    ),
    # 角色列表 / 新建
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/roles/",
        CustomRoleListCreateView.as_view(),
        name="roles-list-create",
    ),
    # 改名 / 描述 / 权限码 / 删除
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/roles/<uuid:role_id>/",
        CustomRoleDetailView.as_view(),
        name="roles-detail",
    ),
    # 挂接清单 / 挂接
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/members/<uuid:member_id>/role-assignments/",
        RoleAssignmentView.as_view(),
        name="role-assignments-list-create",
    ),
    # 卸除
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/members/<uuid:member_id>/role-assignments/<uuid:role_id>/",
        RoleAssignmentDeleteView.as_view(),
        name="role-assignments-delete",
    ),
    # 批量挂接（user_ids 或 department_id）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/roles/<uuid:role_id>/assignments/bulk/",
        RoleBulkAssignView.as_view(),
        name="roles-bulk-assign",
    ),
    # 有效权限并集（我的权限 / 排障）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/members/<uuid:member_id>/effective-permissions/",
        EffectivePermissionsView.as_view(),
        name="members-effective-permissions",
    ),
]
