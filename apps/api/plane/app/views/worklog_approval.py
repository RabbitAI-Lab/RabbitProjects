"""工时审批端点（TASK-013 §4.6）——成员周提交 + 负责人审批驳回撤销 + 台账矩阵 + 导出。

行级口径（BR-14）：成员只见本人台账（worklog.read 语义）；PROJ_ADMIN+ 见全员。
"""
from __future__ import annotations

from datetime import date, timedelta

from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated, ProjectAdminPermission
from plane.app.views._access import get_project_or_404
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.db.models import WorkLogApproval, WorkLogSummary
from plane.db.models.roles import ProjectRole
from plane.db.services.worklog_approval import (
    WorkLogApprovalError,
    _week_start,
    get_or_create_config,
    review,
    submit,
)


def _err(exc: WorkLogApprovalError) -> AppException:
    details = ([{"field": exc.sub}] if exc.sub else None)
    return AppException(exc.code, message=exc.message, details=details)


class WorkLogSubmitView(APIView):
    """POST …/worklog-approvals/submit/ {week_start}——成员提交。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        week_start_raw = (request.data or {}).get("week_start")
        if not week_start_raw:
            raise AppException("VALIDATION_ERROR",
                               details=[{"field": "week_start", "code": "REQUIRED"}])
        try:
            week_start = _week_start(date.fromisoformat(str(week_start_raw)))
        except (ValueError, TypeError):
            raise AppException("VALIDATION_ERROR",
                               details=[{"field": "week_start", "code": "INVALID_DATE"}])
        try:
            batch = submit(actor=request.user, project=project, week_start=week_start)
        except WorkLogApprovalError as exc:
            raise _err(exc) from None
        return success_response({
            "id": str(batch.id), "status": batch.status,
            "week_start": str(batch.week_start),
        })


class WorkLogApproveView(APIView):
    """POST …/worklog-approvals/{id}/approve/——PROJ_ADMIN+。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, slug, project_id, aid):
        if not ProjectAdminPermission().has_permission(request, self):
            raise AppException("PERM_ROLE_INSUFFICIENT",
                               message="worklog.approve 权限不足（PROJ_ADMIN+）")
        try:
            batch = review(batch_id=aid, reviewer=request.user, action="approve")
        except WorkLogApprovalError as exc:
            raise _err(exc) from None
        return success_response({
            "id": str(batch.id), "status": batch.status, "is_frozen": True})


class WorkLogRejectView(APIView):
    """POST …/worklog-approvals/{id}/reject/ {note}（必填）。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, slug, project_id, aid):
        if not ProjectAdminPermission().has_permission(request, self):
            raise AppException("PERM_ROLE_INSUFFICIENT")
        note = (request.data or {}).get("note") or ""
        try:
            batch = review(batch_id=aid, reviewer=request.user, action="reject", note=note)
        except WorkLogApprovalError as exc:
            raise _err(exc) from None
        return success_response({"id": str(batch.id), "status": batch.status})


class WorkLogRevokeView(APIView):
    """POST …/worklog-approvals/{id}/revoke/ {note}（必填，BR-07 撤销审批）。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, slug, project_id, aid):
        if not ProjectAdminPermission().has_permission(request, self):
            raise AppException("PERM_ROLE_INSUFFICIENT")
        note = (request.data or {}).get("note") or ""
        try:
            batch = review(batch_id=aid, reviewer=request.user, action="revoke", note=note)
        except WorkLogApprovalError as exc:
            raise _err(exc) from None
        return success_response({"id": str(batch.id), "status": batch.status})


class WorkLogApprovalListView(APIView):
    """GET 负责人审批队列（PROJ_ADMIN+）/ GET 我提交的（全员）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        is_admin = project.current_user_role >= ProjectRole.ADMIN
        if is_admin and (request.query_params.get("scope") == "queue"):
            qs = WorkLogApproval.objects.filter(
                project=project, status=WorkLogApproval.Status.SUBMITTED,
                deleted_at__isnull=True).order_by("submitted_at")
        else:
            qs = WorkLogApproval.objects.filter(
                project=project, actor=request.user, deleted_at__isnull=True
            ).order_by("-week_start")
        return success_response([{
            "id": str(b.id), "actor_id": str(b.actor_id), "actor_name": b.actor.display_name,
            "week_start": str(b.week_start), "status": b.status,
            "review_note": b.review_note, "submitted_at": b.submitted_at,
            "reviewed_at": b.reviewed_at,
        } for b in qs])


class WorkLogLedgerView(APIView):
    """GET 团队台账（人×周矩阵，§3.3）——PROJ_ADMIN+ 看全员；成员仅本人。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        is_admin = project.current_user_role >= ProjectRole.ADMIN
        qs = WorkLogSummary.objects.filter(
            project=project, deleted_at__isnull=True)
        if not is_admin:
            qs = qs.filter(actor=request.user)
        week_param = request.query_params.get("week_start")
        if week_param:
            try:
                ws = _week_start(date.fromisoformat(week_param))
                qs = qs.filter(week_start__gte=ws - timedelta(days=21),
                               week_start__lte=ws + timedelta(days=7))
            except (ValueError, TypeError):
                pass
        qs = qs.order_by("-week_start", "actor__display_name")
        config = get_or_create_config(project)
        return success_response([{
            "actor_id": str(s.actor_id), "actor_name": s.actor.display_name,
            "week_start": str(s.week_start), "total_minutes": s.total_minutes,
            "task_count": s.task_count, "over_8h_days": s.over_8h_days,
            "is_frozen": s.is_frozen,
            "load_ratio": round(s.total_minutes / max(config.weekly_capacity_minutes, 1), 2),
        } for s in qs])


class WorkLogConfigView(APIView):
    """GET / PATCH …/worklog-config/——项目级配置（§4.2）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        c = get_or_create_config(project)
        return success_response({
            "approval_enabled": c.approval_enabled,
            "daily_soft_limit_minutes": c.daily_soft_limit_minutes,
            "granularity_minutes": c.granularity_minutes,
            "warn_ratio": str(c.warn_ratio),
            "weekly_capacity_minutes": c.weekly_capacity_minutes,
        })

    def patch(self, request, slug, project_id):
        if not ProjectAdminPermission().has_permission(request, self):
            raise AppException("PERM_ROLE_INSUFFICIENT")
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        c = get_or_create_config(project)
        for f in ("approval_enabled", "daily_soft_limit_minutes",
                  "granularity_minutes", "warn_ratio", "weekly_capacity_minutes"):
            if f in (request.data or {}):
                setattr(c, f, request.data[f])
        c.save()
        return success_response({"id": str(c.id)})
