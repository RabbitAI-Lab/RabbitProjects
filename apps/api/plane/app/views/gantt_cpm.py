"""关键路径视图（GANTT-003 §4.4，Sprint-9）——3 端点。

critical-path/（GET，视口仅过滤下发行集——CPM 恒按项目全图计算）、
recompute/（POST，202 + {task_id, status_url}，幂等 debounce）、cpm-config/
（GET/PATCH，预警开关与目标完工日）。高 CPU 端点自带 10/min·user throttle。
"""
from __future__ import annotations

from django.utils import timezone
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated, require_permission
from plane.app.views._access import get_project_or_404
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.base.throttling import BASE_THROTTLES, ReportAggRateThrottle
from plane.db.models import CPMAlertConfig, IssueCPMCache
from plane.db.services.cpm import cpm_recompute
from plane.db.services.issue_transition_guard import assert_completable  # noqa: F401  # 契约关联锚


def _viewport_or_400(request):
    vs = request.query_params.get("viewport_start")
    ve = request.query_params.get("viewport_end")
    if vs and ve and str(vs) > str(ve):
        raise AppException("VALIDATION_INVALID_PARAM",
                           details=[{"field": "viewport_start", "code": "INVALID_DATE_RANGE",
                                     "message": "视窗起始晚于结束"}])
    return vs, ve


class GanttCriticalPathView(APIView):
    """GET gantt/critical-path/ —— CPM 行（缓存直查；缺缓存即时算）。"""

    permission_classes = [IsAuthenticated]
    throttle_classes = [*BASE_THROTTLES, ReportAggRateThrottle]

    @require_permission("project.read")
    def get(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        vs, ve = _viewport_or_400(request)
        today = timezone.localdate()
        cache_rows = list(IssueCPMCache.objects.filter(
            project=project).select_related("issue", "issue__project"))
        if not cache_rows:
            cpm_recompute(str(project.id))     # 缓存缺失即时补算（同步）
            cache_rows = list(IssueCPMCache.objects.filter(
                project=project).select_related("issue", "issue__project"))
        rows = []
        for r in cache_rows:
            if vs and r.ef and str(r.ef) < str(vs):
                continue
            if ve and r.es and str(r.es) > str(ve):
                continue
            rows.append({
                "id": str(r.issue_id),
                "issue_key": f"{r.issue.project.identifier}-{r.issue.sequence_id}",
                "es": r.es, "ef": r.ef, "ls": r.ls, "lf": r.lf,
                "float_days": r.float_days, "is_critical": r.is_critical,
                "has_external_preds": r.has_external_preds,
            })
        approximate = len(rows) > 20000        # BR-11 >2 万节点降级标注
        return success_response({
            "computed_at": cache_rows[0].computed_at if cache_rows else None,
            "approximate": approximate, "rows": rows,
        }, meta={"anchor_today": today, "viewport": {"start": vs, "end": ve}})


class GanttCriticalPathRecomputeView(APIView):
    """POST gantt/critical-path/recompute/ —— 手动触发（202，api-conventions §13.1）。"""

    permission_classes = [IsAuthenticated]
    throttle_classes = [*BASE_THROTTLES, ReportAggRateThrottle]

    @require_permission("project.read")
    def post(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        task = cpm_recompute.apply_async(args=[str(project.id)])
        from rest_framework import status
        from rest_framework.response import Response
        return Response(
            {"status": "success",
             "data": {"task_id": task.id, "state": "PENDING",
                      "status_url": f"/api/v1/workspaces/{slug}/projects/{project.id}/gantt/critical-path/"}},
            status=status.HTTP_202_ACCEPTED)


class GanttCPMConfigView(APIView):
    """GET/PATCH gantt/cpm-config/ —— 预警开关 + 目标完工日（BR-06/13/14）。"""

    permission_classes = [IsAuthenticated]

    @require_permission("project.read")
    def get(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        config = CPMAlertConfig.objects.filter(project=project).first()
        return success_response({
            "overdue_alert_enabled": config.overdue_alert_enabled if config else True,
            "float_consumed_alert_enabled": config.float_consumed_alert_enabled if config else True,
            "target_completion_date": config.target_completion_date if config else None,
        })

    @require_permission("project.setting.manage")
    def patch(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        target = request.data.get("target_completion_date")
        if target and str(target) < str(timezone.localdate()):
            raise AppException("VALIDATION_INVALID_DATE_RANGE",
                               details=[{"field": "target_completion_date",
                                         "code": "INVALID_DATE_RANGE",
                                         "message": "目标完工日不能早于今日"}])
        config, _ = CPMAlertConfig.objects.get_or_create(project=project)
        for f in ("overdue_alert_enabled", "float_consumed_alert_enabled",
                  "target_completion_date"):
            if f in request.data:
                setattr(config, f, request.data[f])
        config.updated_by = request.user
        config.save()
        return success_response({"id": str(config.id)})
