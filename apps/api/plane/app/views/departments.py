"""部门域视图（AUTH-007 §4.2，Sprint-8 R1）—— 11 端点。

权限口径（§4.2 表注 / BR-06 / §5.4 PM-02）：
- 写操作（建/改/移/删/批量调部门/授权/预览）：``department.manage``
  （rbac §8.1 既有码，本迭代落地矩阵；WS_ADMIN+，403 PERM_WORKSPACE_ADMIN_REQUIRED）；
- 读操作（树 / 统计 / 批次详情）：借用 ``workspace.member.read`` 行口径
  （WS_GUEST ❌ → 403 PERM_ROLE_INSUFFICIENT，不新增 read 码）。
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
from plane.app.serializers.departments import (
    DepartmentBulkMoveSerializer,
    DepartmentCreateSerializer,
    DepartmentGrantBatchSerializer,
    DepartmentGrantSerializer,
    DepartmentMoveSerializer,
    DepartmentPatchSerializer,
    DepartmentSerializer,
)
from plane.app.views._access import get_workspace_or_404
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.db.models import Department, DepartmentGrantBatch, Project, WorkspaceMember
from plane.db.models.roles import WorkspaceRole
from plane.db.services import department as dept_service

logger = logging.getLogger("plane.api.departments")


def _department_manage(view_method):
    """department.manage 写门槛（§5.4 PM-02：403 PERM_WORKSPACE_ADMIN_REQUIRED）。"""

    @wraps(view_method)
    def wrapper(self, request, *args, **kwargs):
        from plane.app.permissions import _resolve_workspace_role

        actual = _resolve_workspace_role(request, self)
        if actual is None or actual < WorkspaceRole.ADMIN:
            raise AppException(
                "PERM_WORKSPACE_ADMIN_REQUIRED",
                message="需要工作空间管理员权限",
            )
        return view_method(self, request, *args, **kwargs)

    return wrapper


class _DepartmentReadPermission(BasePermission):
    """读取借用 workspace.member.read 口径（GUEST 403，§4.2 表注）。

    仅守护 safe method（GET/HEAD）：写方法的拒绝语义归 ``_department_manage``
    （PM-02：WS_MEMBER/WS_GUEST 一律 403 PERM_WORKSPACE_ADMIN_REQUIRED，
    不被读门槛先行改码）。
    """

    def has_permission(self, request, view) -> bool:
        if not IsAuthenticatedAndActive().has_permission(request, view):
            return False
        if request.method not in ("GET", "HEAD"):
            return True  # 写方法交 _department_manage 判定
        # GUEST 以下不可见部门树（成员归属信息）
        from plane.app.permissions import require_role

        return require_role(request, view, WorkspaceRole.MEMBER) >= WorkspaceRole.MEMBER


def _get_department_or_404(ws, department_id) -> Department:
    try:
        return Department.objects.get(
            pk=department_id, workspace=ws, deleted_at__isnull=True,
        )
    except Department.DoesNotExist:
        raise NotFound("RESOURCE_NOT_FOUND") from None


# ── 树列表 / 新建 ─────────────────────────────────────────

class DepartmentListCreateView(APIView):
    """GET / POST .../workspaces/{slug}/departments/"""

    permission_classes = [IsAuthenticatedAndActive, _DepartmentReadPermission]

    def get(self, request, slug):
        ws, _ = get_workspace_or_404(slug, request.user)
        qs = (Department.objects
              .filter(workspace=ws, deleted_at__isnull=True)
              .order_by("path", "sort_order"))
        # ?include=member_count（§4.2）：单查询子聚合，避免逐行 N+1
        if request.query_params.get("include") == "member_count":
            counts = dict(
                WorkspaceMember.objects
                .filter(workspace=ws, is_active=True, deleted_at__isnull=True,
                        department__isnull=False)
                .values_list("department_id")
                .annotate(c=Count("id"))
            )
            # 含子级计数：按 path 前缀累加（部门量 <500，内存聚合足够，§6.4）
            rows = list(qs)
            for d in rows:
                d._member_count = counts.get(d.id, 0)
                d._with_descendants_member_count = sum(
                    counts.get(c.id, 0) for c in rows if c.path.startswith(d.path))
            data = DepartmentSerializer(rows, many=True).data
        else:
            data = DepartmentSerializer(qs, many=True).data
        return success_response(data)

    @_department_manage
    def post(self, request, slug):
        ws, _ = get_workspace_or_404(slug, request.user)
        s = DepartmentCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        dept = dept_service.create_department(
            actor=request.user, workspace=ws,
            name=s.validated_data["name"],
            parent_id=s.validated_data.get("parent_id"),
        )
        return Response(
            {"status": "success",
             "data": {"department": DepartmentSerializer(dept).data}},
            status=status.HTTP_201_CREATED,
        )


# ── 改名 / 排序 ──────────────────────────────────────────

class DepartmentDetailView(APIView):
    """PATCH / DELETE .../workspaces/{slug}/departments/{id}/ —— 改名 / 排序 / 删空部门。"""

    permission_classes = [IsAuthenticatedAndActive, _DepartmentReadPermission]

    @_department_manage
    def patch(self, request, slug, department_id):
        ws, _ = get_workspace_or_404(slug, request.user)
        dept = _get_department_or_404(ws, department_id)
        s = DepartmentPatchSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if "name" in s.validated_data:
            dept = dept_service.rename_department(
                actor=request.user, department=dept,
                name=s.validated_data["name"])
        if "sort_after" in s.validated_data:
            dept = dept_service.reorder_department(
                actor=request.user, department=dept,
                sort_after_id=s.validated_data["sort_after"])
        return success_response(DepartmentSerializer(dept).data)

    @_department_manage
    def delete(self, request, slug, department_id):
        ws, _ = get_workspace_or_404(slug, request.user)
        dept = _get_department_or_404(ws, department_id)
        dept_service.delete_department(actor=request.user, department=dept)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── 移动（换父级，BR-01/03）──────────────────────────────

class DepartmentMoveView(APIView):
    """POST .../workspaces/{slug}/departments/{id}/move/"""

    permission_classes = [IsAuthenticatedAndActive, _DepartmentReadPermission]

    @_department_manage
    def post(self, request, slug, department_id):
        ws, _ = get_workspace_or_404(slug, request.user)
        dept = _get_department_or_404(ws, department_id)
        s = DepartmentMoveSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        dept = dept_service.move_department(
            actor=request.user, department=dept,
            new_parent_id=s.validated_data.get("parent_id"))
        return success_response(DepartmentSerializer(dept).data)


# ── 批量调部门 ───────────────────────────────────────────

class DepartmentBulkMoveMembersView(APIView):
    """POST .../workspaces/{slug}/departments/{id}/members/bulk-move/

    department_id 目标为 null 时 = 批量移入未分配（路径 id 取任一部门均可，
    以 body.department_id 为准——端点挂在部门树上仅为路由分组）。
    """

    permission_classes = [IsAuthenticatedAndActive, _DepartmentReadPermission]

    @_department_manage
    def post(self, request, slug, department_id):
        ws, _ = get_workspace_or_404(slug, request.user)
        _get_department_or_404(ws, department_id)  # 存在性校验（404 出域）
        s = DepartmentBulkMoveSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        moved = dept_service.bulk_move_members(
            actor=request.user, workspace=ws,
            member_ids=[str(m) for m in s.validated_data["member_ids"]],
            department_id=s.validated_data.get("department_id"),
        )
        return success_response({"moved": moved})


# ── 按部门授权（预览 / 执行 / 批次详情）─────────────────

class DepartmentGrantPreviewView(APIView):
    """POST .../workspaces/{slug}/departments/{id}/grants/preview/ —— 只读展开。"""

    permission_classes = [IsAuthenticatedAndActive, _DepartmentReadPermission]

    @_department_manage
    def post(self, request, slug, department_id):
        ws, _ = get_workspace_or_404(slug, request.user)
        dept = _get_department_or_404(ws, department_id)
        s = DepartmentGrantSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            project = Project.objects.get(
                pk=s.validated_data["project_id"], workspace=ws,
            )
        except Project.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        result = dept_service.expand_grant(
            actor=request.user, department=dept, project=project,
            role=s.validated_data["role"],
            with_descendants=s.validated_data["with_descendants"],
            dry_run=True,
        )
        return success_response(result)


class DepartmentGrantView(APIView):
    """POST .../workspaces/{slug}/departments/{id}/grants/ —— 201 + Location。"""

    permission_classes = [IsAuthenticatedAndActive, _DepartmentReadPermission]

    @_department_manage
    def post(self, request, slug, department_id):
        ws, _ = get_workspace_or_404(slug, request.user)
        dept = _get_department_or_404(ws, department_id)
        s = DepartmentGrantSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            project = Project.objects.get(
                pk=s.validated_data["project_id"], workspace=ws,
            )
        except Project.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        batch = dept_service.expand_grant(
            actor=request.user, department=dept, project=project,
            role=s.validated_data["role"],
            with_descendants=s.validated_data["with_descendants"],
        )
        skipped_detail = [
            {"member_id": m, "reason": r} for m, r in _skipped_pairs(batch)
        ]
        response = Response(
            {"status": "success",
             "data": {
                 "id": str(batch.id),
                 "added": _snapshot_ids(batch, "added"),
                 "role_changed": _snapshot_ids(batch, "role_changed"),
                 "skipped": skipped_detail,
                 "unchanged": _snapshot_ids(batch, "unchanged"),
                 "skipped_detail": skipped_detail,
             }},
            status=status.HTTP_201_CREATED,
        )
        response["Location"] = (
            f"/api/v1/workspaces/{ws.slug}/departments/{department_id}/grants/{batch.id}/"
        )
        return response


def _snapshot_ids(batch: DepartmentGrantBatch, action: str) -> list[str]:
    return [e["member_id"] for e in (batch.member_snapshot or [])
            if e.get("action") == action]


def _skipped_pairs(batch: DepartmentGrantBatch) -> list[tuple[str, str]]:
    out = []
    for e in (batch.member_snapshot or []):
        if str(e.get("action", "")).startswith("skipped:"):
            out.append((e["member_id"], str(e["action"]).split(":", 1)[1]))
    return out


class DepartmentGrantBatchDetailView(APIView):
    """GET .../workspaces/{slug}/departments/{id}/grants/{batch_id}/"""

    permission_classes = [IsAuthenticatedAndActive, _DepartmentReadPermission]

    def get(self, request, slug, department_id, batch_id):
        ws, _ = get_workspace_or_404(slug, request.user)
        # 批次详情按 snapshot 溯源：部门可能已删（BR-04），存在性只校验 workspace
        try:
            batch = DepartmentGrantBatch.objects.get(
                pk=batch_id, workspace=ws, deleted_at__isnull=True,
            )
        except DepartmentGrantBatch.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        if str(batch.department_id_snapshot) != str(department_id):
            raise NotFound("RESOURCE_NOT_FOUND") from None
        return success_response(DepartmentGrantBatchSerializer(batch).data)


# ── 统计 ─────────────────────────────────────────────────

class DepartmentStatsView(APIView):
    """GET .../workspaces/{slug}/departments/{id}/stats/"""

    permission_classes = [IsAuthenticatedAndActive, _DepartmentReadPermission]

    def get(self, request, slug, department_id):
        ws, _ = get_workspace_or_404(slug, request.user)
        dept = _get_department_or_404(ws, department_id)
        return success_response(dept_service.department_stats(dept))
