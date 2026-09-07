"""工作空间治理视图（TEAM-003 §4.2——Sprint-5 T5-08）。

端点（工作空间作用域，全部强制尾斜杠）：
  GET/POST  …/workspaces/{slug}/labels/            全局标签列表 / 创建（WS_ADMIN+）
  PATCH/DELETE …/workspaces/{slug}/labels/{id}/    更新 / 软删（含 affected_issues）
  GET/PUT    …/workspaces/{slug}/default-states/   基础状态模板快照
  GET        …/workspaces/{slug}/activity-stats/   成员活跃度（team.stats.read，WS_ADMIN+）
  POST       …/workspaces/{slug}/archive/          归档（仅 OWNER，ADR-0023 收窄）
  POST       …/workspaces/{slug}/restore/          恢复（仅 OWNER）
"""
from __future__ import annotations

from rest_framework.exceptions import NotFound
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated
from plane.app.views._access import get_workspace_or_404
from plane.base.exception import AppException
from plane.base.response import created_response, success_response
from plane.db.models import WorkspaceLabel, WorkspaceRole
from plane.db.services.workspace_governance import WorkspaceGovernanceService

_SVC = WorkspaceGovernanceService()


def _require_ws_admin(member) -> None:
    if member.role < WorkspaceRole.ADMIN:
        raise AppException("PERM_WORKSPACE_ADMIN_REQUIRED",
                           message="需要工作空间管理员权限")


def _require_owner(member) -> None:
    if member.role != WorkspaceRole.OWNER:
        raise AppException("PERM_WORKSPACE_OWNER_REQUIRED",
                           message="仅工作空间所有者可操作")


def _get_label(ws, label_id) -> WorkspaceLabel:
    row = WorkspaceLabel.objects.filter(
        pk=label_id, workspace=ws, deleted_at__isnull=True).first()
    if row is None:
        raise NotFound("RESOURCE_NOT_FOUND") from None
    return row


class WorkspaceLabelListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        ws, _ = get_workspace_or_404(slug, request.user)
        data, meta = _SVC.list_labels(ws)
        return success_response(data, meta=meta)

    def post(self, request, slug):
        ws, member = get_workspace_or_404(slug, request.user)
        _require_ws_admin(member)
        row = _SVC.create_label(ws, payload=request.data, actor=request.user)
        return created_response(row, location=request.build_absolute_uri(
            f"/api/v1/workspaces/{ws.slug}/labels/{row['id']}/"))


class WorkspaceLabelDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, slug, label_id):
        ws, member = get_workspace_or_404(slug, request.user)
        _require_ws_admin(member)
        row = _get_label(ws, label_id)
        return success_response(_SVC.update_label(ws, row, payload=request.data))

    def delete(self, request, slug, label_id):
        ws, member = get_workspace_or_404(slug, request.user)
        _require_ws_admin(member)
        row = _get_label(ws, label_id)
        return success_response(
            _SVC.delete_label(ws, row, actor=request.user))


class WorkspaceDefaultStatesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        ws, _ = get_workspace_or_404(slug, request.user)
        return success_response(_SVC.get_default_states(ws))

    def put(self, request, slug):
        ws, member = get_workspace_or_404(slug, request.user)
        _require_ws_admin(member)
        return success_response(
            _SVC.put_default_states(ws, payload=request.data, actor=request.user))


class WorkspaceActivityStatsView(APIView):
    """BR-09 红线：响应键路径无 user_id（UT Schema 断言）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        ws, member = get_workspace_or_404(slug, request.user)
        # team.stats.read（rbac §8.1 Report 分区新增行，ADR-0025 登记）：WS_ADMIN+
        if member.role < WorkspaceRole.ADMIN:
            raise AppException("PERM_WORKSPACE_ADMIN_REQUIRED",
                               message="需要工作空间管理员权限")
        days = 30
        raw = request.query_params.get("days")
        if raw:
            try:
                days = int(raw)
            except (TypeError, ValueError):
                days = -1
        if days not in (7, 14, 30, 90):
            raise AppException("VALIDATION_INVALID_PARAM",
                               message="days 必须为 7/14/30/90 之一")
        return success_response(_SVC.activity_stats(ws, days=days),
                                headers={"Cache-Control": "no-store"})


class WorkspaceArchiveView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, slug):
        ws, member = get_workspace_or_404(slug, request.user)
        _require_owner(member)
        return success_response(_SVC.archive(ws, actor=request.user))


class WorkspaceRestoreView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, slug):
        ws, member = get_workspace_or_404(slug, request.user)
        _require_owner(member)
        return success_response(_SVC.restore(ws, actor=request.user))
