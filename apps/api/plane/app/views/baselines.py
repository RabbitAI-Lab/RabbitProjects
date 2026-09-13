"""任务基线端点（TASK-015 §4.4，P4 R3）。

六端点：列表 / 创建（≤2 万同步 201 · >2 万 202 building 轮询，BR-11）/
详情（status 轮询载体）/ 删除整套（PROJ_ADMIN+，审计）/ 对比行（七值
diff_type 过滤 + cursor 分页）/ 偏差统计。权限：创建/删除
gantt.baseline.manage（PROJ_ADMIN+）；查看项目成员可读。
快照一致性（BR-03）：创建在 REPEATABLE READ 事务内（首语句设置隔离级）。
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import uuid as _uuid

from django.db import transaction
from django.http import HttpResponse
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticatedAndActive
from plane.app.views._access import get_project_or_404
from plane.audit.recorder import record
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.db.models import Baseline, Issue
from plane.db.models.roles import ProjectRole

logger = logging.getLogger("plane.api.baseline")

MAX_BASELINES = 11  # BR-02（对齐 MS Project）
ASYNC_THRESHOLD = 20_000  # BR-11 大项目异步线

_DIFF_TYPES = frozenset({"added", "deleted", "delayed", "on_track", "unscheduled", "assignee_changed", "drifted"})


def _require_baseline_admin(request, view, project):
    """gantt.baseline.manage：PROJ_ADMIN+（rbac §8.2）。"""
    from plane.app.permissions import _resolve_effective_project_role

    role = _resolve_effective_project_role(request, view)
    if role is None or role < ProjectRole.ADMIN:
        from plane.app.permissions import PermissionDeniedWithCode

        raise PermissionDeniedWithCode("缺少 gantt.baseline.manage（需项目管理员）")


def _audit(request, action: str, obj: dict, ws_id, detail: dict):
    record(
        event_key=hashlib.sha256(
            f"baseline.{action}:{obj.get('id')}:{request.user.id}:{_uuid.uuid4()}".encode()
        ).hexdigest()[:80],
        category="workflow",
        action="state_changed",
        workspace_id=ws_id,
        actor=request.user,
        obj=obj,
        detail=detail,
    )


class BaselineListCreateView(APIView):
    """GET 列表 / POST 创建（BR-02/03/11/13）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        rows = Baseline.objects.filter(project=project).order_by("-created_at")
        return success_response(
            [
                {
                    "id": str(b.id),
                    "name": b.name,
                    "reason": b.reason,
                    "note": b.note,
                    "status": b.status,
                    "issue_count": b.issue_count,
                    "created_by": str(b.created_by_id),
                    "created_at": b.created_at,
                }
                for b in rows
            ]
        )

    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        _require_baseline_admin(request, self, project)
        p = request.data or {}
        name = str(p.get("name") or "").strip()
        reason = str(p.get("reason") or "").strip()
        if not name:
            raise AppException("VALIDATION_ERROR", message="name 必填", details=[{"field": "name", "code": "REQUIRED"}])
        if not reason:  # BR-13
            raise AppException(
                "VALIDATION_ERROR",
                message="变更原因必填",
                details=[{"field": "reason", "code": "REQUIRED", "message": "创建基线必须填写变更原因（需求 §3.4）"}],
            )
        quota = Baseline.objects.filter(project=project, status__in=("building", "ready")).count()
        if quota >= MAX_BASELINES:  # BR-02（failed 不占）
            raise AppException("RESOURCE_LIMIT_EXCEEDED", message=f"每项目基线上限 {MAX_BASELINES} 套，请先删除旧套")
        if Baseline.objects.filter(project=project, name=name).exists():
            raise AppException(
                "VALIDATION_ERROR", message="基线名已存在", details=[{"field": "name", "code": "UNIQUE"}]
            )

        issue_count = Issue.objects.filter(project=project, deleted_at__isnull=True).count()
        baseline = Baseline.objects.create(
            project=project,
            name=name[:32],
            reason=reason[:255],
            note=str(p.get("note") or "")[:255],
            status=Baseline.Status.BUILDING,
            created_by=request.user,
        )
        from rest_framework import status as _st
        from rest_framework.response import Response

        if issue_count > ASYNC_THRESHOLD:  # BR-11 大项目异步
            from plane.db.services.baseline import fill_baseline_async

            transaction.on_commit(lambda: fill_baseline_async.delay(str(baseline.id)))
            return Response(
                {"status": "success", "data": {"baseline_id": str(baseline.id), "status": "building"}},
                status=_st.HTTP_202_ACCEPTED,
            )
        from plane.db.services.baseline import fill_baseline_sync

        fill_baseline_sync(baseline.id)
        baseline.refresh_from_db()
        _audit(
            request,
            "created",
            {"type": "baseline", "id": str(baseline.id), "name": baseline.name},
            project.workspace_id,
            {"issues": baseline.issue_count, "reason": reason},
        )
        return Response(
            {"status": "success", "data": self._detail(baseline)},
            status=_st.HTTP_201_CREATED,
            headers={
                "Location": request.build_absolute_uri(
                    f"/api/v1/workspaces/{kwargs['slug']}/projects/{project.id}/baselines/{baseline.id}/"
                )
            },
        )

    @staticmethod
    def _detail(b) -> dict:
        return {"baseline_id": str(b.id), "status": b.status, "issue_count": b.issue_count}


class BaselineDetailView(APIView):
    """GET 详情（BR-11 轮询载体）/ DELETE 删除整套（BR-01 例外）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        b = Baseline.objects.filter(pk=kwargs["baseline_id"], project=project).first()
        if b is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        return success_response(
            {
                "id": str(b.id),
                "name": b.name,
                "reason": b.reason,
                "note": b.note,
                "status": b.status,
                "issue_count": b.issue_count,
                "stats_cache": b.stats_cache,
                "created_at": b.created_at,
            }
        )

    def delete(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        _require_baseline_admin(request, self, project)
        b = Baseline.objects.filter(pk=kwargs["baseline_id"], project=project).first()
        if b is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        _audit(request, "deleted", {"type": "baseline", "id": str(b.id)}, project.workspace_id, {"name": b.name})
        b.delete()  # DB 级联清快照行
        return success_response({"deleted": True, "id": str(b.id)})


class BaselineCompareView(APIView):
    """GET .../compare/ —— 快照集 ∪ 当前集全集对比（BR-05 行不灭失）。
    diff_type 七值过滤（五差异类 + 两标志列，§4.4）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        b = Baseline.objects.filter(pk=kwargs["baseline_id"], project=project).first()
        if b is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        diff_type = request.query_params.get("diff_type")
        if diff_type and diff_type not in _DIFF_TYPES:
            raise AppException(
                "VALIDATION_ERROR",
                message="非法 diff_type",
                details=[{"field": "diff_type", "code": "INVALID", "message": f"{diff_type}（七值枚举）"}],
            )
        from plane.db.services.baseline import build_compare_rows

        rows = build_compare_rows(b)
        if diff_type:
            if diff_type in ("assignee_changed", "drifted"):
                rows = [r for r in rows if r.get(diff_type)]
            else:
                rows = [r for r in rows if r["diff_type"] == diff_type]
        try:
            per_page = min(int(request.query_params.get("per_page", 100)), 200)
        except ValueError:
            per_page = 100
        cursor = request.query_params.get("cursor")
        offset = 0
        if cursor:
            try:
                _, offset, _ = cursor.split(":")
                offset = int(offset)
            except (ValueError, AttributeError):
                raise AppException("VALIDATION_INVALID_CURSOR", message="游标非法") from None
        page = rows[offset : offset + per_page]
        return success_response(
            page,
            meta={
                "count": len(page),
                "total_count": len(rows),
                "per_page": per_page,
                "next_cursor": f"{per_page}:{offset + per_page}:0" if len(rows) > offset + per_page else None,
                "prev_cursor": f"{per_page}:{max(offset - per_page, 0)}:1" if offset > 0 else None,
            },
        )


class BaselineStatsView(APIView):
    """GET .../stats/ —— 偏差统计聚合（§2.4）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        b = Baseline.objects.filter(pk=kwargs["baseline_id"], project=project).first()
        if b is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        from plane.db.services.baseline import compute_stats

        return success_response(compute_stats(b))


class BaselineExportView(APIView):
    """GET .../export/ —— CSV 导出（同步流式；导出入审计 BR-09）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        b = Baseline.objects.filter(pk=kwargs["baseline_id"], project=project).first()
        if b is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        from plane.db.services.baseline import build_compare_rows

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(
            [
                "seq",
                "name",
                "diff_type",
                "start_date",
                "target_date",
                "base_target",
                "variance_days",
                "assignees",
                "critical",
            ]
        )
        for r in build_compare_rows(b):
            w.writerow(
                [
                    r["sequence_id"],
                    r["name"],
                    r["diff_type"],
                    r.get("start_date"),
                    r.get("target_date"),
                    r.get("base_target_date"),
                    r.get("variance_days"),
                    ",".join(r.get("assignees") or []),
                    r.get("is_critical", False),
                ]
            )
        _audit(
            request,
            "deleted" if False else "updated",
            {"type": "baseline_export", "id": str(b.id)},
            project.workspace_id,
            {"rows": b.issue_count},
        )
        resp = HttpResponse(buf.getvalue(), content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="baseline-{b.name}.csv"'
        resp["Cache-Control"] = "no-store"
        return resp
