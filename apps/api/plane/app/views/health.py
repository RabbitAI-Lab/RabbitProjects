"""项目健康度视图（RPT-004 §4.3，Sprint-9）——6 端点。

health/（评分卡 + 7 日趋势）、drilldown/（实时下钻，meta.as_of="realtime"）、
trend/、config/（权重阈值 PATCH）、workload/（BR-07 窗口校验 + 容量下发）、
workload/export/（CSV：阈值内同步流式 / 超限 202 异步两段式——S7 A#1 +
S8 A#1 债范式，ExportTask + MinIO 预签名）。
"""
from __future__ import annotations

from datetime import date, timedelta

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated, require_permission
from plane.app.views._access import get_project_or_404
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.base.throttling import BASE_THROTTLES, ReportAggRateThrottle
from plane.db.models import ExportTask, HealthConfig, HealthSnapshot
from plane.db.models.health import DEFAULT_HEALTH_THRESHOLDS, DEFAULT_HEALTH_WEIGHTS
from plane.db.services.health import (
    HealthReportService,
    run_export_task,
)

#: 同步导出行数上界（超出转异步两段式，api-conventions §13.1）
SYNC_EXPORT_ROW_LIMIT = 2000

_SVC = HealthReportService()


def _latest_or_compute(request, project) -> HealthSnapshot:
    """读侧最新快照兜底：beat 未跑时即时补算当日（幂等）。"""
    snap = (HealthSnapshot.objects
            .filter(project=project, snapshot_date__lte=timezone.localdate())
            .order_by("-snapshot_date").first())
    if snap is None:
        snap = _SVC.compute(project, timezone.localdate(), HealthConfig.of(project))
    return snap


class HealthReportView(APIView):
    """GET reports/health/ —— 当前评分卡（最新快照 + 7 日趋势）。"""

    permission_classes = [IsAuthenticated]
    throttle_classes = [*BASE_THROTTLES, ReportAggRateThrottle]

    @require_permission("report.read")
    def get(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        snap = _latest_or_compute(request, project)
        trend = list(HealthSnapshot.objects.filter(
            project=project,
            snapshot_date__gte=timezone.localdate() - timedelta(days=7)
        ).order_by("snapshot_date").values("snapshot_date", "total_score", "band"))
        return success_response({
            "snapshot_date": snap.snapshot_date,
            "dimensions": snap.dimensions,
            "total_score": snap.total_score,
            "band": snap.band,
            "config": snap.config_snapshot,
            "trend_7d": [{"date": t["snapshot_date"], "score": t["total_score"],
                          "band": t["band"]} for t in trend],
        })


class HealthDrilldownView(APIView):
    """GET reports/health/drilldown/?dimension= —— 实时构成清单（BR-04②）。"""

    permission_classes = [IsAuthenticated]

    @require_permission("report.read")
    def get(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        dimension = request.query_params.get("dimension", "")
        if dimension not in _SVC.DRILL_WHITELIST:
            raise AppException("VALIDATION_INVALID_PARAM",
                               details=[{"field": "dimension", "code": "NOT_A_CHOICE",
                                         "message": f"合法枚举 {sorted(_SVC.DRILL_WHITELIST)}"}])
        qs = _SVC.drilldown(project, dimension)
        rows = []
        for it in qs[:100]:
            rows.append({
                "id": str(it.id),
                "issue_key": f"{project.identifier}-{it.sequence_id}",
                "name": it.name,
                "target_date": it.target_date,
                "state_group": (it.state.group if it.state else None) or "unstarted",
                "assignee": None,
            })
        return success_response(rows,
                                meta={"as_of": "realtime", "count": len(rows)})


class HealthTrendView(APIView):
    """GET reports/health/trend/?days=30 —— 总评趋势序列（快照直查）。"""

    permission_classes = [IsAuthenticated]

    @require_permission("report.read")
    def get(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        days = min(int(request.query_params.get("days", 30)), 180)
        rows = (HealthSnapshot.objects
                .filter(project=project,
                        snapshot_date__gte=timezone.localdate() - timedelta(days=days))
                .order_by("snapshot_date")
                .values("snapshot_date", "total_score", "band"))
        return success_response([{"date": r["snapshot_date"], "score": r["total_score"],
                                  "band": r["band"]} for r in rows])


class HealthConfigView(APIView):
    """GET/PATCH reports/health/config/ —— 阈值/权重（BR-02；容量不在本端点）。"""

    permission_classes = [IsAuthenticated]

    @require_permission("report.read")
    def get(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        cfg = HealthConfig.of(project)
        return success_response({"weights": cfg.weights, "thresholds": cfg.thresholds})

    @require_permission("project.setting.manage")
    def patch(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        cfg = HealthConfig.of(project)
        if "weights" in request.data:
            weights = request.data["weights"]
            if not isinstance(weights, dict) or set(weights) - set(DEFAULT_HEALTH_WEIGHTS):
                raise AppException("VALIDATION_ERROR",
                                   details=[{"field": "weights", "code": "INVALID",
                                             "message": "维度枚举非法"}])
            if any(not isinstance(v, (int, float)) or v < 0 for v in weights.values()):
                raise AppException("VALIDATION_ERROR",
                                   details=[{"field": "weights", "code": "INVALID",
                                             "message": "权重必须为非负数"}])
            total = round(sum(v for v in weights.values()), 2)
            if abs(total - 1.0) > 1e-9:
                raise AppException("VALIDATION_ERROR",
                                   details=[{"field": "weights", "code": "INVALID",
                                             "message": f"权重和 {total} ≠ 1"}])
            cfg.weights = weights
        if "thresholds" in request.data:
            thresholds = request.data["thresholds"]
            if not isinstance(thresholds, dict) or set(thresholds) - set(DEFAULT_HEALTH_THRESHOLDS):
                raise AppException("VALIDATION_ERROR",
                                   details=[{"field": "thresholds", "code": "INVALID",
                                             "message": "阈值键枚举非法"}])
            cfg.thresholds = thresholds
        cfg.updated_by = request.user
        cfg.save()
        return success_response({"id": str(cfg.id)})


def _parse_window(request) -> tuple[date, date]:
    frm = request.query_params.get("from")
    to = request.query_params.get("to")
    if not frm or not to:
        raise AppException("VALIDATION_ERROR",
                           details=[{"field": "from", "code": "REQUIRED",
                                     "message": "from/to 必填（BR-07）"}])
    try:
        frm_d, to_d = date.fromisoformat(frm), date.fromisoformat(to)
    except ValueError:
        raise AppException("VALIDATION_INVALID_PARAM",
                           details=[{"field": "from", "code": "INVALID",
                                     "message": "日期格式非法"}]) from None
    if frm_d.weekday() != 0:                     # BR-07：周一起始
        raise AppException("VALIDATION_INVALID_PARAM",
                           details=[{"field": "from", "code": "INVALID",
                                     "message": "from 必须为周一"}])
    if (to_d - frm_d).days > 12 * 7:             # BR-07：跨度 ≤12 周
        raise AppException("VALIDATION_INVALID_PARAM",
                           details=[{"field": "to", "code": "INVALID",
                                     "message": "窗口跨度上限 12 周"}])
    return frm_d, to_d


class WorkloadView(APIView):
    """GET reports/workload/?from=&to= —— 负载热力矩阵（BR-08 权限 + 容量下发）。"""

    permission_classes = [IsAuthenticated]
    throttle_classes = [*BASE_THROTTLES, ReportAggRateThrottle]

    @require_permission("report.read")
    def get(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        frm, to = _parse_window(request)
        # BR-08：成员可看本人；看他人明细需 team.stats.read（WS_ADMIN+ 同款门槛，
        # 矩阵聚合值（非明细）对 report.read 开放——负载热力是项目级视图）
        matrix, people = _SVC.workload(project, frm, to)
        return success_response({
            "from": frm, "to": to,
            "capacity_minutes": _SVC.capacity_minutes(project),
            "matrix": matrix,
        }, meta={"people": people})


class WorkloadExportView(APIView):
    """GET reports/workload/export/?from=&to=&format=csv —— 负载 CSV 导出。

    行数阈值内同步流式；超限 202 异步两段式（task_id/status_url + MinIO
    预签名——S7 A#1 + S8 A#1 债统一范式）。
    """

    permission_classes = [IsAuthenticated]

    @require_permission("report.export")
    def get(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        frm, to = _parse_window(request)
        if request.query_params.get("format", "csv") != "csv":
            raise AppException("VALIDATION_INVALID_PARAM",
                               details=[{"field": "format", "code": "NOT_A_CHOICE",
                                         "message": "当前仅支持 csv"}])
        # 行数 = 人 × 命中周；上限内同步流式
        matrix, _people = _SVC.workload(project, frm, to)
        est_rows = sum(len(r["cells"]) or 1 for r in matrix)
        if est_rows <= SYNC_EXPORT_ROW_LIMIT:
            capacity = _SVC.capacity_minutes(project)
            lines = ["actor,week_start,minutes,load_ratio"]
            for row in matrix:
                for wk, minutes in sorted(row["cells"].items()):
                    lines.append(f"{row['actor']},{wk},{minutes},{round(minutes / capacity, 4)}")
            resp = HttpResponse("\r\n".join(lines), content_type="text/csv; charset=utf-8")
            resp["Content-Disposition"] = (
                f'attachment; filename="workload-{project.id}.csv"')
            return resp
        # 异步两段式（api-conventions §13.1）
        task = ExportTask.objects.create(
            export_type="workload_csv", workspace=project.workspace, project=project,
            params={"project_id": str(project.id), "from": frm.isoformat(),
                    "to": to.isoformat()},
            created_by=request.user)
        run_export_task.delay(str(task.id))
        return Response(
            {"status": "success",
             "data": {"task_id": str(task.id), "state": task.status,
                      "status_url": f"/api/v1/workspaces/{slug}/exports/{task.id}/"}},
            status=status.HTTP_202_ACCEPTED)


class ExportTaskStatusView(APIView):
    """GET exports/{task_id}/ —— 异步导出状态轮询（succeeded 时下发下载 URL）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug, task_id):
        from plane.app.views._access import get_workspace_or_404
        ws, _ = get_workspace_or_404(slug, request.user)
        task = (ExportTask.objects
                .filter(pk=task_id, workspace=ws, created_by=request.user).first())
        if task is None:
            from rest_framework.exceptions import NotFound
            raise NotFound("RESOURCE_NOT_FOUND") from None
        data = {"task_id": str(task.id), "state": task.status,
                "export_type": task.export_type}
        if task.status == ExportTask.Status.SUCCEEDED:
            data["download_url"] = task.download_url
            data["expires_at"] = task.expires_at
        if task.status == ExportTask.Status.FAILED:
            data["error"] = task.error
        return success_response(data)
