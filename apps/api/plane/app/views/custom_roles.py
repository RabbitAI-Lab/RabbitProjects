"""自定义角色域视图（AUTH-008 §4.2，Sprint-8 R2）—— 9 端点。

权限口径（BR-08）：
- 角色 CRUD 与目录：WS 级 role.manage（403 PERM_WORKSPACE_ADMIN_REQUIRED）；
- 挂接/卸除/批量挂接：对目标项目 project.member.manage
  （PROJ_ADMIN+，403 PERM_PROJECT_ADMIN_REQUIRED）；
- 角色列表读取：项目成员（project.read 口径）；
- effective-permissions：本人或 project.member.manage。
"""
from __future__ import annotations

import logging
from functools import wraps

from django.db.models import Count
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticatedAndActive
from plane.app.serializers.custom_roles import (
    CustomRoleCreateSerializer,
    CustomRolePatchSerializer,
    CustomRoleSerializer,
    RoleAssignmentRowSerializer,
    RoleAssignSerializer,
    RoleBulkAssignSerializer,
)
from plane.app.views._access import get_workspace_or_404
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.constants.custom_role_catalog import (
    CATALOG_GROUPS,
    CATALOG_THRESHOLDS,
    CUSTOMIZABLE_CATALOG,
)
from plane.db.models import CustomRole, Project, ProjectMember, ProjectRoleAssignment
from plane.db.models.roles import ProjectRole, WorkspaceRole
from plane.db.services.custom_role import RoleService

logger = logging.getLogger("plane.api.custom_roles")


def _role_manage(view_method):
    """WS 级 role.manage 写门槛（BR-08：403 PERM_WORKSPACE_ADMIN_REQUIRED）。"""

    @wraps(view_method)
    def wrapper(self, request, *args, **kwargs):
        from plane.app.permissions import _resolve_workspace_role

        actual = _resolve_workspace_role(request, self)
        if actual is None or actual < WorkspaceRole.ADMIN:
            raise AppException("PERM_WORKSPACE_ADMIN_REQUIRED",
                               message="需要工作空间管理员权限")
        return view_method(self, request, *args, **kwargs)

    return wrapper


def _proj_member_manage(view_method):
    """挂接域门槛（BR-08：PROJ_ADMIN+，403 PERM_PROJECT_ADMIN_REQUIRED）。"""

    @wraps(view_method)
    def wrapper(self, request, *args, **kwargs):
        from plane.app.permissions import _resolve_effective_project_role

        actual = _resolve_effective_project_role(request, self)
        if actual is None or actual < ProjectRole.ADMIN:
            raise AppException("PERM_PROJECT_ADMIN_REQUIRED",
                               message="需要项目管理员权限")
        return view_method(self, request, *args, **kwargs)

    return wrapper


class _ProjectReadPermission(BasePermission):
    """角色列表读取：项目成员（project.read 口径，PROJ_VIEWER+）。"""

    def has_permission(self, request, view) -> bool:
        if not IsAuthenticatedAndActive().has_permission(request, view):
            return False
        from plane.app.permissions import _resolve_effective_project_role

        role = _resolve_effective_project_role(request, view)
        return role is not None and role >= ProjectRole.VIEWER


def _get_project_or_404(ws, project_id) -> Project:
    try:
        return Project.objects.get(pk=project_id, workspace=ws)
    except Project.DoesNotExist:
        raise NotFound("RESOURCE_NOT_FOUND") from None


def _get_role_or_404(project: Project, role_id) -> CustomRole:
    try:
        return CustomRole.objects.get(pk=role_id, project=project,
                                      deleted_at__isnull=True)
    except CustomRole.DoesNotExist:
        raise NotFound("RESOURCE_NOT_FOUND") from None


def _base(request, project_id) -> str:
    slug = request.view.kwargs["slug"] if hasattr(request, "view") else ""
    return f"/api/v1/workspaces/{slug}/projects/{project_id}/roles/"


# ── 角色列表 / 新建 ───────────────────────────────────────

class CustomRoleListCreateView(APIView):
    """GET / POST .../projects/{pid}/roles/

    GET 支持可选 ``?name=<角色名>`` 精确解析（BR-17：单对象 200 / 0 条 404 /
    歧义 409）与 ``?search=`` 模糊（二者互斥）。
    """

    permission_classes = [IsAuthenticatedAndActive, _ProjectReadPermission]

    def get(self, request, slug, project_id):
        ws, _ = get_workspace_or_404(slug, request.user)
        project = _get_project_or_404(ws, project_id)
        qs = (CustomRole.objects
              .filter(project=project, deleted_at__isnull=True)
              .annotate(_assigned_count=Count(
                  "assignments",
                  filter=models_q_assignments_alive()))
              .order_by("name"))
        name = request.query_params.get("name")
        search = request.query_params.get("search")
        if name and search:
            raise AppException(
                "VALIDATION_ERROR",
                message="name 与 search 互斥",
                details=[{"field": "name", "code": "INVALID",
                          "message": "name（精确解析）与 search（模糊）互斥"}],
            )
        if name is not None:
            hits = [r for r in qs if r.name.lower() == name.strip().lower()]
            if not hits:
                raise NotFound("RESOURCE_NOT_FOUND")
            if len(hits) > 1:  # 防御（BR-03 排除；跨软删脏数据兜底）
                raise AppException(
                    "RESOURCE_ALREADY_EXISTS",
                    message="角色名歧义，请改用 role_id",
                    details=[{"field": "name", "code": "UNIQUE",
                              "message": f"命中 {len(hits)} 条，请改用 role_id"}],
                )
            return success_response(CustomRoleSerializer(hits[0]).data)
        if search:
            qs = qs.filter(name__icontains=search.strip())
        data = CustomRoleSerializer(list(qs), many=True).data
        return success_response(data, meta={"count": len(data),
                                            "total_count": len(data)})

    @_role_manage
    def post(self, request, slug, project_id):
        ws, _ = get_workspace_or_404(slug, request.user)
        project = _get_project_or_404(ws, project_id)
        s = CustomRoleCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        role = RoleService().create_role(
            actor=request.user, project=project,
            name=s.validated_data.get("name") or "",
            description=s.validated_data.get("description") or "",
            permissions=s.validated_data.get("permissions") or [],
            template_key=s.validated_data.get("template_key"),
        )
        response = Response(
            {"status": "success",
             "data": {"role": CustomRoleSerializer(role).data}},
            status=status.HTTP_201_CREATED,
        )
        response["Location"] = (
            f"/api/v1/workspaces/{slug}/projects/{project_id}/roles/{role.id}/"
        )
        return response


def models_q_assignments_alive():
    from django.db.models import Q

    return Q(assignments__deleted_at__isnull=True)


# ── 改名 / 描述 / 权限码 ─────────────────────────────────

class CustomRoleDetailView(APIView):
    """PATCH / DELETE .../roles/{role_id}/"""

    permission_classes = [IsAuthenticatedAndActive, _ProjectReadPermission]

    @_role_manage
    def patch(self, request, slug, project_id, role_id):
        ws, _ = get_workspace_or_404(slug, request.user)
        project = _get_project_or_404(ws, project_id)
        role = _get_role_or_404(project, role_id)
        s = CustomRolePatchSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        role = RoleService().update_role(
            actor=request.user, role=role,
            name=s.validated_data.get("name"),
            description=s.validated_data.get("description"),
            permissions=s.validated_data.get("permissions"),
        )
        return success_response(CustomRoleSerializer(role).data)

    @_role_manage
    def delete(self, request, slug, project_id, role_id):
        ws, _ = get_workspace_or_404(slug, request.user)
        project = _get_project_or_404(ws, project_id)
        role = _get_role_or_404(project, role_id)
        RoleService().delete_role(actor=request.user, role=role)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── 可勾选目录 ───────────────────────────────────────────

class PermissionsCatalogView(APIView):
    """GET .../roles/permissions-catalog/ —— 按域分组，冻结基线 42 码。"""

    permission_classes = [IsAuthenticatedAndActive, _ProjectReadPermission]

    @_role_manage
    def get(self, request, slug, project_id):
        ws, _ = get_workspace_or_404(slug, request.user)
        _get_project_or_404(ws, project_id)
        return success_response({
            "groups": [
                {"domain": domain,
                 "codes": [{"code": c,
                            "min_project_role": CATALOG_THRESHOLDS.get(c)}
                           for c in codes]}
                for domain, codes in CATALOG_GROUPS
            ],
            "total": len(CUSTOMIZABLE_CATALOG),
        })


# ── 挂接 / 卸除 ──────────────────────────────────────────

def _get_pm_or_404(project: Project, member_id) -> ProjectMember:
    try:
        return ProjectMember.objects.get(
            pk=member_id, project=project, is_active=True,
            deleted_at__isnull=True,
        )
    except ProjectMember.DoesNotExist:
        raise NotFound("RESOURCE_NOT_FOUND") from None


class RoleAssignmentView(APIView):
    """GET / POST .../members/{member_id}/role-assignments/

    GET 返回该成员的挂接清单（我的权限面板数据源之一）。
    """

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, slug, project_id, member_id):
        ws, _ = get_workspace_or_404(slug, request.user)
        project = _get_project_or_404(ws, project_id)
        pm = _get_pm_or_404(project, member_id)
        # 本人或 project.member.manage（BR-08）
        from plane.app.permissions import _resolve_effective_project_role

        if pm.member_id != request.user.id:
            actual = _resolve_effective_project_role(request, self)
            if actual is None or actual < ProjectRole.ADMIN:
                raise AppException("PERM_PROJECT_ADMIN_REQUIRED",
                                   message="需要项目管理员权限")
        rows = (ProjectRoleAssignment.objects
                .filter(project=project, user_id=pm.member_id,
                        deleted_at__isnull=True)
                .select_related("role").order_by("created_at"))
        return success_response(RoleAssignmentRowSerializer(rows, many=True).data)

    @_proj_member_manage
    def post(self, request, slug, project_id, member_id):
        ws, _ = get_workspace_or_404(slug, request.user)
        project = _get_project_or_404(ws, project_id)
        pm = _get_pm_or_404(project, member_id)
        s = RoleAssignSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        role = _get_role_or_404(project, s.validated_data["role_id"])
        RoleService().assign(actor=request.user, role=role, target_user=pm.member)
        return Response({"status": "success", "data": {"role_id": str(role.id)}},
                        status=status.HTTP_201_CREATED)


class RoleAssignmentDeleteView(APIView):
    """DELETE .../members/{member_id}/role-assignments/{role_id}/"""

    permission_classes = [IsAuthenticatedAndActive]

    @_proj_member_manage
    def delete(self, request, slug, project_id, member_id, role_id):
        ws, _ = get_workspace_or_404(slug, request.user)
        project = _get_project_or_404(ws, project_id)
        pm = _get_pm_or_404(project, member_id)
        role = _get_role_or_404(project, role_id)
        RoleService().revoke(actor=request.user, role=role, target_user=pm.member)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── 批量挂接 ─────────────────────────────────────────────

class RoleBulkAssignView(APIView):
    """POST .../roles/{role_id}/assignments/bulk/ —— user_ids 或 department_id。"""

    permission_classes = [IsAuthenticatedAndActive]

    @_proj_member_manage
    def post(self, request, slug, project_id, role_id):
        ws, _ = get_workspace_or_404(slug, request.user)
        project = _get_project_or_404(ws, project_id)
        role = _get_role_or_404(project, role_id)
        s = RoleBulkAssignSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        result = RoleService().bulk_assign(
            actor=request.user, role=role,
            user_ids=[str(u) for u in (s.validated_data.get("user_ids") or [])]
            or None,
            department_id=s.validated_data.get("department_id"),
        )
        return Response({"status": "success", "data": result},
                        status=status.HTTP_201_CREATED)


# ── 有效权限（我的权限 / 排障）──────────────────────────

class EffectivePermissionsView(APIView):
    """GET .../members/{member_id}/effective-permissions/ —— 并集清单。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, slug, project_id, member_id):
        ws, _ = get_workspace_or_404(slug, request.user)
        project = _get_project_or_404(ws, project_id)
        pm = _get_pm_or_404(project, member_id)
        from plane.app.permissions import _resolve_effective_project_role

        if pm.member_id != request.user.id:
            actual = _resolve_effective_project_role(request, self)
            if actual is None or actual < ProjectRole.ADMIN:
                raise AppException("PERM_PROJECT_ADMIN_REQUIRED",
                                   message="需要项目管理员权限")
        from plane.app.effective_perms import effective_codes

        codes = effective_codes(pm.member_id, project.id)
        fixed_role = (ProjectMember.objects
                      .filter(pk=pm.pk)
                      .values_list("role", flat=True).first())
        return success_response({
            "fixed_role": fixed_role,
            "permissions": sorted(codes),
            "is_union": True,
        })
