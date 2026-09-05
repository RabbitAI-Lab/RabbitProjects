"""工时端点（TASK-006 §4.2）。

GET    /workspaces/{slug}/projects/{pid}/issues/{iid}/worklogs/             列表（筛选+游标+sum_minutes）
POST   /workspaces/{slug}/projects/{pid}/issues/{iid}/worklogs/             填报（201 含 issue_spent_minutes）
PATCH  /workspaces/{slug}/projects/{pid}/issues/{iid}/worklogs/{log_id}/    修改（本人/ADMIN）
DELETE /workspaces/{slug}/projects/{pid}/issues/{iid}/worklogs/{log_id}/    软删（204）
"""

from __future__ import annotations

from datetime import datetime

from django.db.models import Sum
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated
from plane.app.views._access import get_project_or_404
from plane.base.exception import AppException
from plane.base.response import created_response, success_response
from plane.db.models import Issue, ProjectRole, WorkLog
from plane.db.services.worklog import (
    WorklogPermissionError,
    WorklogValidationError,
    delete_worklog,
    log_work,
    update_worklog,
)


def _issue_or_404(kwargs, project) -> Issue:
    issue = Issue.objects.filter(id=kwargs["issue_id"], project_id=project.id, deleted_at__isnull=True).first()
    if issue is None:
        raise NotFound("RESOURCE_NOT_FOUND") from None
    return issue


def _serialize(log: WorkLog, *, spent: int | None = None) -> dict:
    out = {
        "id": str(log.id),
        "issue_id": str(log.issue_id),
        "actor_id": str(log.actor_id),
        "worked_on": str(log.worked_on),
        "minutes": log.minutes,
        "note": log.note,
        "created_at": log.created_at,
    }
    if spent is not None:
        out["issue_spent_minutes"] = spent
    return out


class WorkLogListCreateView(APIView):
    """GET（VIEWER+）/ POST（CONTRIBUTOR+）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        issue = _issue_or_404(kwargs, project)
        qs = WorkLog.objects.filter(issue=issue, deleted_at__isnull=True)

        applied = {}
        actor = request.query_params.get("actor_id")
        if request.query_params.get("mine") in ("true", "1"):
            actor = str(request.user.id)
            applied["mine"] = True
        if actor:
            qs = qs.filter(actor_id=actor)
            applied["actor_id"] = actor
        worked = request.query_params.get("worked_on")
        if worked:
            mod = "on"
            if ";" in worked:
                worked, mod = worked.split(";", 1)
            try:
                days = [datetime.strptime(x, "%Y-%m-%d").date() for x in worked.split(",") if x]
            except ValueError:
                raise AppException(
                    "VALIDATION_ERROR",
                    details=[{"field": "worked_on", "code": "INVALID_DATE", "message": "日期格式应为 YYYY-MM-DD"}],
                ) from None
            if mod == "between" and len(days) == 2:
                qs = qs.filter(worked_on__range=(min(days), max(days)))
            elif mod == "before" and days:
                qs = qs.filter(worked_on__lt=days[0])
            elif mod == "after" and days:
                qs = qs.filter(worked_on__gt=days[0])
            elif len(days) == 1:
                qs = qs.filter(worked_on=days[0])
            applied["worked_on"] = request.query_params.get("worked_on")

        try:
            per_page = min(max(int(request.query_params.get("per_page", 20)), 1), 100)
        except ValueError:
            per_page = 20
        qs = qs.order_by("-worked_on", "-created_at")
        total = qs.count()
        offset = 0
        cursor = request.query_params.get("cursor")
        if cursor:
            try:
                import base64

                offset = max(int(base64.b64decode(cursor).decode().split(":")[1]), 0)
            except Exception:  # noqa: BLE001 —— 游标非法按 400 契约
                raise AppException("VALIDATION_INVALID_CURSOR") from None
        rows = list(qs[offset : offset + per_page])
        import base64

        next_cursor = None
        if offset + per_page < total:
            next_cursor = base64.b64encode(f"{offset + per_page}:{rows[-1].worked_on}".encode()).decode()
        sum_minutes = qs.aggregate(s=Sum("minutes"))["s"] or 0
        return success_response(
            [_serialize(r) for r in rows],
            meta={
                "next_cursor": next_cursor,
                "next_page_results": next_cursor is not None,
                "count": len(rows),
                "total_count": total,
                "page": offset // per_page + 1,
                "per_page": per_page,
                "sum_minutes": sum_minutes,
                "applied": applied,
            },
        )

    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        if project.current_user_role < ProjectRole.CONTRIBUTOR:
            raise AppException("PERM_ROLE_INSUFFICIENT")
        issue = _issue_or_404(kwargs, project)
        minutes = request.data.get("minutes")
        worked_on = request.data.get("worked_on")
        errors = []
        if not isinstance(minutes, int):
            errors.append({"field": "minutes", "code": "REQUIRED", "message": "时长为必填整数分钟"})
        try:
            worked = datetime.strptime(str(worked_on), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            errors.append({"field": "worked_on", "code": "INVALID_DATE", "message": "工作日期格式应为 YYYY-MM-DD"})
            worked = None
        if errors:
            raise AppException("VALIDATION_ERROR", details=errors)
        try:
            log, spent = log_work(
                issue=issue,
                actor_id=request.user.id,
                minutes=minutes,
                worked_on=worked,
                note=str(request.data.get("note") or ""),
            )
        except WorklogValidationError as e:
            raise AppException(
                "VALIDATION_ERROR", details=[{"field": e.field, "code": e.code, "message": e.message}]
            ) from None
        except WorklogPermissionError as e:
            raise AppException("PERM_PROJECT_ARCHIVED", message=str(e)) from None
        return created_response(
            _serialize(log, spent=spent),
            location=f"/api/v1/workspaces/{kwargs['slug']}/projects/{kwargs['project_id']}/"
            f"issues/{kwargs['issue_id']}/worklogs/{log.id}/",
        )


class WorkLogDetailView(APIView):
    """PATCH / DELETE（本人或 PROJ_ADMIN，BR-05）。"""

    permission_classes = [IsAuthenticated]

    def patch(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        if project.current_user_role < ProjectRole.CONTRIBUTOR:
            raise AppException("PERM_ROLE_INSUFFICIENT")
        issue = _issue_or_404(kwargs, project)
        payload = {}
        if "minutes" in request.data:
            payload["minutes"] = request.data["minutes"]
        if "worked_on" in request.data:
            try:
                payload["worked_on"] = datetime.strptime(str(request.data["worked_on"]), "%Y-%m-%d").date()
            except ValueError:
                raise AppException(
                    "VALIDATION_ERROR",
                    details=[{"field": "worked_on", "code": "INVALID_DATE", "message": "工作日期格式应为 YYYY-MM-DD"}],
                ) from None
        if "note" in request.data:
            payload["note"] = str(request.data["note"])
        try:
            log, spent = update_worklog(
                log_id=kwargs["log_id"],
                issue_id=issue.id,
                actor_id=request.user.id,
                is_admin=project.current_user_role >= ProjectRole.ADMIN,
                **payload,
            )
        except WorklogValidationError as e:
            raise AppException(
                "VALIDATION_ERROR", details=[{"field": e.field, "code": e.code, "message": e.message}]
            ) from None
        except WorklogPermissionError as e:
            raise AppException("PERM_DENIED", message=str(e)) from None
        if log is None:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        return success_response(_serialize(log, spent=spent))

    def delete(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        if project.current_user_role < ProjectRole.CONTRIBUTOR:
            raise AppException("PERM_ROLE_INSUFFICIENT")
        issue = _issue_or_404(kwargs, project)
        try:
            spent = delete_worklog(
                log_id=kwargs["log_id"],
                issue_id=issue.id,
                actor_id=request.user.id,
                is_admin=project.current_user_role >= ProjectRole.ADMIN,
            )
        except WorklogPermissionError as e:
            raise AppException("PERM_DENIED", message=str(e)) from None
        if spent < 0:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        return Response(status=204)
