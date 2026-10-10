"""企业大屏与报表端点（RPT-005 §4.3，P4 R7）。

报表 CRUD + 即时预览（查询引擎）、大屏 CRUD、播放令牌签发/心跳、订阅。
权限：设计器写面 project.setting.manage（WS_ADMIN+——require_role ADMIN
同档）；播放面 DisplayToken 匿名豁免（BR-07）。
"""

from __future__ import annotations

import hashlib
import secrets

from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticatedAndActive, require_role
from plane.app.views._access import get_workspace_or_404
from plane.audit.recorder import record
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.db.models import Dashboard, DisplayToken, Report, ReportSubscription, WorkspaceRole

_DARK_THEMES = {"dark", "light"}


def _require_design(request, view):
    require_role(request, view, WorkspaceRole.ADMIN)


def _ws_or_404(request, slug):
    return get_workspace_or_404(slug, request.user)[0]


def _audit(request, action, obj, ws_id, detail):
    import time

    record(
        event_key=hashlib.sha256(f"rpt.{action}:{obj.get('id')}:{request.user.id}:{time.time()}".encode()).hexdigest()[
            :80
        ],
        category="workflow",
        action="state_changed",
        workspace_id=ws_id,
        actor=request.user,
        obj=obj,
        detail=detail,
    )


class ReportListCreateView(APIView):
    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, slug):
        ws = _ws_or_404(request, slug)
        rows = Report.objects.filter(workspace=ws, deleted_at__isnull=True)
        return success_response(
            [
                {
                    "id": str(r.id),
                    "name": r.name,
                    "description": r.description,
                    "config": r.config,
                    "is_shared": r.is_shared,
                    "version": r.version,
                    "created_at": r.created_at,
                }
                for r in rows
            ]
        )

    def post(self, request, slug):
        _require_design(request, self)
        ws = _ws_or_404(request, slug)
        p = request.data or {}
        name = str(p.get("name") or "").strip()
        if not name:
            raise AppException("VALIDATION_ERROR", message="name 必填", details=[{"field": "name", "code": "REQUIRED"}])
        if Report.objects.filter(workspace=ws, name=name, deleted_at__isnull=True).exists():
            raise AppException(
                "RESOURCE_ALREADY_EXISTS", message="报表名已存在", details=[{"field": "name", "code": "UNIQUE"}]
            )
        report = Report.objects.create(
            workspace=ws,
            name=name[:64],
            description=str(p.get("description") or "")[:255],
            config=p.get("config") or {},
            owner=request.user,
            is_shared=bool(p.get("is_shared", False)),
            created_by=request.user,
        )
        return success_response({"id": str(report.id)}, status_code=201)


class ReportDetailView(APIView):
    permission_classes = [IsAuthenticatedAndActive]

    def patch(self, request, slug, report_id):
        _require_design(request, self)
        ws = _ws_or_404(request, slug)
        report = Report.objects.filter(pk=report_id, workspace=ws).first()
        if report is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        p = request.data or {}
        if "version" in p and int(p["version"] or 0) != report.version:
            raise AppException("RESOURCE_STATE_INVALID", message="版本冲突（乐观锁）")
        for f in ("description", "config", "is_shared"):
            if f in p:
                setattr(report, f, p[f])
        report.version += 1
        report.updated_by = request.user
        report.save()
        return success_response({"id": str(report.id), "version": report.version})

    def delete(self, request, slug, report_id):
        _require_design(request, self)
        ws = _ws_or_404(request, slug)
        updated = Report.objects.filter(pk=report_id, workspace=ws).update(
            deleted_at=timezone.now(), updated_by=request.user
        )
        if not updated:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        return success_response({"deleted": True})


class ReportPreviewView(APIView):
    """POST .../reports/preview/ —— 未保存配置即时预览（查询引擎）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, slug):
        _require_design(request, self)
        ws = _ws_or_404(request, slug)
        from plane.db.services.report_query import ReportQueryEngine

        try:
            result = ReportQueryEngine().execute(ws, (request.data or {}).get("config") or {}, viewer=request.user)
        except ValueError as exc:
            raise AppException(
                "VALIDATION_ERROR", message=str(exc), details=[{"field": "config", "code": "INVALID"}]
            ) from None
        return success_response(result)


class ReportMetricsView(APIView):
    """GET .../reports/metrics/ —— 数据集/指标/维度注册表（设计器数据源）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, slug):
        _ws_or_404(request, slug)
        from plane.db.services.report_query import DATASETS

        return success_response(
            {
                ds: {"metrics": {k: v[1] for k, v in spec["metrics"].items()}, "dimensions": spec["dimensions"]}
                for ds, spec in DATASETS.items()
            }
        )


class DashboardView(APIView):
    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, slug):
        ws = _ws_or_404(request, slug)
        rows = Dashboard.objects.filter(workspace=ws, deleted_at__isnull=True)
        return success_response(
            [
                {
                    "id": str(d.id),
                    "name": d.name,
                    "layout": d.layout,
                    "theme": d.theme,
                    "created_at": d.created_at,
                }
                for d in rows
            ]
        )

    def post(self, request, slug):
        _require_design(request, self)
        ws = _ws_or_404(request, slug)
        p = request.data or {}
        theme = p.get("theme") or "dark"
        if theme not in _DARK_THEMES:
            raise AppException(
                "VALIDATION_ERROR", message="theme 非法", details=[{"field": "theme", "code": "INVALID"}]
            )
        board = Dashboard.objects.create(
            workspace=ws,
            name=str(p.get("name") or "未命名大屏")[:64],
            layout=p.get("layout") or {},
            theme=theme,
            owner=request.user,
            created_by=request.user,
        )
        return success_response({"id": str(board.id)}, status_code=201)

    def delete(self, request, slug, dashboard_id):
        _require_design(request, self)
        ws = _ws_or_404(request, slug)
        updated = Dashboard.objects.filter(pk=dashboard_id, workspace=ws).update(deleted_at=timezone.now())
        if not updated:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        return success_response({"deleted": True})


class DisplayTokenView(APIView):
    """POST .../dashboards/{id}/display-tokens/ 签发（report.export）；
    DELETE {token_id}/ 吊销。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, slug, dashboard_id):
        _require_design(request, self)
        ws = _ws_or_404(request, slug)
        board = Dashboard.objects.filter(pk=dashboard_id, workspace=ws).first()
        if board is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        raw = f"dsp_{secrets.token_urlsafe(24)}"
        token = DisplayToken.objects.create(
            dashboard=board,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            token_prefix=raw[:10],
            created_by=request.user,
        )
        return success_response({"id": str(token.id), "token": raw, "warning": "令牌仅本次显示"}, status_code=201)

    def delete(self, request, slug, dashboard_id, token_id):
        _require_design(request, self)
        ws = _ws_or_404(request, slug)
        updated = DisplayToken.objects.filter(pk=token_id, dashboard_id=dashboard_id, dashboard__workspace=ws).update(
            revoked_at=timezone.now()
        )
        if not updated:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        return success_response({"revoked": True})


class DisplayScreenView(APIView):
    """GET /api/v1/display/{token}/screen/{i}/ —— 播放面（匿名 BR-07）。"""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request, token, screen_index):
        dt = (
            DisplayToken.objects.filter(token_hash=hashlib.sha256((token or "").encode()).hexdigest())
            .select_related("dashboard")
            .first()
        )
        if dt is None or dt.revoked_at:
            return success_response({"error": "AUTH_INVALID_TOKEN"}, status_code=404)
        if dt.expires_at and dt.expires_at < timezone.now():
            return success_response({"error": "AUTH_TOKEN_EXPIRED"}, status_code=410)
        DisplayToken.objects.filter(pk=dt.pk).update(last_seen_at=timezone.now())
        items = (dt.dashboard.layout or {}).get("items") or []
        if int(screen_index) >= len(items):
            return success_response({"error": "RANGE"}, status=404)
        return success_response(
            {
                "dashboard": {"name": dt.dashboard.name, "theme": dt.dashboard.theme},
                "screen": items[int(screen_index)],
                "screens": len(items),
            }
        )


class DisplayHeartbeatView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []

    def post(self, request, token):
        updated = DisplayToken.objects.filter(token_hash=hashlib.sha256((token or "").encode()).hexdigest()).update(
            last_seen_at=timezone.now()
        )
        if not updated:
            return success_response({"alive": False}, status_code=404)
        return success_response({"alive": True})


class SubscriptionView(APIView):
    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, slug, report_id):
        _require_design(request, self)
        ws = _ws_or_404(request, slug)
        report = Report.objects.filter(pk=report_id, workspace=ws).first()
        if report is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        p = request.data or {}
        schedule = p.get("schedule")
        if schedule not in ("daily", "weekly"):
            raise AppException(
                "VALIDATION_ERROR", message="schedule 非法", details=[{"field": "schedule", "code": "INVALID"}]
            )
        sub = ReportSubscription.objects.create(
            report=report, schedule=schedule, channel=p.get("channel") or {}, created_by=request.user
        )
        _audit(request, "created", {"type": "report_subscription", "id": str(sub.id)}, ws.id, {"schedule": schedule})
        return success_response({"id": str(sub.id)}, status_code=201)
