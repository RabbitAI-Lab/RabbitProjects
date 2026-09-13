"""跨组织经营分析端点（RPT-006，P4 R7）。

集团横向视图：总览（逐空间行）/ 指标对比。BR-01 仅 SYSTEM_ADMIN（rbac
附录 B 口径——跨组织边界数据）；BR-02 纯读零写路径。
"""

from __future__ import annotations

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticatedAndActive, is_system_admin
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.db.models import HealthSnapshot, Issue, Project, WorkLogSummary, Workspace

_METRICS = {"issues_total", "issues_done", "issues_open", "projects", "logged_minutes", "health_score"}


def _require_sysadmin(request) -> None:
    if not is_system_admin(request.user):
        raise AppException("PERM_DENIED", message="跨组织分析需系统管理员身份（BR-01）")


def _org_row(ws) -> dict:
    projects = Project.objects.filter(workspace=ws, deleted_at__isnull=True)
    issues = Issue.objects.filter(project__workspace=ws, deleted_at__isnull=True)
    today = __import__("django.utils.timezone", fromlist=["timezone"]).now().date()
    agg = issues.aggregate(
        total=Count("id"),
        done=Count("id", filter=Q(completed_at__isnull=False)),
        open=Count("id", filter=Q(completed_at__isnull=True)),
        overdue=Count("id", filter=Q(completed_at__isnull=True, target_date__lt=today)),
    )
    worklog = WorkLogSummary.objects.filter(project__workspace=ws).aggregate(minutes=Coalesce(Sum("total_minutes"), 0))
    health = (
        HealthSnapshot.objects.filter(project__workspace=ws)
        .order_by("-created_at")
        .values_list("total_score", flat=True)
        .first()
    )
    return {
        "workspace_id": str(ws.id),
        "name": ws.name,
        "slug": ws.slug,
        "projects": projects.count(),
        "issues_total": agg["total"],
        "issues_done": agg["done"],
        "issues_open": agg["open"],
        "issues_overdue": agg["overdue"],
        "logged_minutes": int(worklog["minutes"]),
        "health_score": health,
    }


class CrossOrgSummaryView(APIView):
    """GET /api/v1/instances/cross-org/summary/ —— 逐组织一行（BR-01 守门）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request):
        _require_sysadmin(request)
        qs = Workspace.objects.filter(deleted_at__isnull=True).order_by("created_at")
        ids = [i for i in (request.query_params.get("ids") or "").split(",") if i]
        if ids:
            qs = qs.filter(pk__in=ids)
        rows = [_org_row(ws) for ws in qs[:100]]
        return success_response(rows, meta={"count": len(rows)})


class CrossOrgCompareView(APIView):
    """GET /api/v1/instances/cross-org/compare/?ids=a,b&metric=issues_done。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request):
        _require_sysadmin(request)
        metric = request.query_params.get("metric") or "issues_total"
        if metric not in _METRICS:
            raise AppException(
                "VALIDATION_ERROR",
                message="metric 非法",
                details=[{"field": "metric", "code": "INVALID", "message": sorted(_METRICS)}],
            )
        ids = [i for i in (request.query_params.get("ids") or "").split(",") if i]
        if not ids:
            raise AppException("VALIDATION_ERROR", message="ids 必填", details=[{"field": "ids", "code": "REQUIRED"}])
        rows = []
        for ws in Workspace.objects.filter(pk__in=ids, deleted_at__isnull=True):
            row = _org_row(ws)
            rows.append(
                {"workspace_id": row["workspace_id"], "name": row["name"], "metric": metric, "value": row.get(metric)}
            )
        rows.sort(key=lambda r: -(r["value"] or 0))
        return success_response(rows)
