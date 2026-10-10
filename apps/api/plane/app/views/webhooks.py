"""出站 Webhook 视图（INTG-002 §4.2——Sprint-5 T5-05）。

九端点（全部挂 ``…/workspaces/{slug}/projects/{project_id}/webhooks/``，
权限一律 ``integration.config``（PROJ_ADMIN，rbac §8.2）：
  POST / GET 列表（≤20/项目，无分页）/ PATCH / disable / enable / DELETE /
  ping（202 异步）/ deliveries 列表（游标）/ deliveries/{id} 详情。
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated, require_permission
from plane.app.views._access import get_project_or_404
from plane.base.exception import AppException
from plane.base.response import created_response, success_response
from plane.db.models import WebhookDelivery, WebhookEndpoint
from plane.db.models.integration import encrypt_secret
from plane.db.services.webhook_outbound import (
    EVENT_CHOICES,
    dispatch_events,
    new_endpoint_secret,
    replay_delivery,
    validate_endpoint_payload,
)


def _row(e: WebhookEndpoint, *, secret: str | None = None) -> dict:
    data = {
        "id": str(e.id),
        "url": e.url,
        "events": e.events,
        "is_active": e.is_active,
        "consecutive_failures": e.consecutive_failures,
        "created_at": e.created_at.isoformat(),
    }
    if secret is not None:
        data["secret_shown_once"] = secret
    return data


def _get_endpoint(project, endpoint_id) -> WebhookEndpoint:
    row = WebhookEndpoint.objects.filter(pk=endpoint_id, project=project, deleted_at__isnull=True).first()
    if row is None:
        raise NotFound("RESOURCE_NOT_FOUND") from None
    return row


class WebhookListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @require_permission("integration.config", scope="project")
    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        rows = WebhookEndpoint.objects.filter(project=project, deleted_at__isnull=True).order_by("-created_at")
        return success_response(
            [_row(e) for e in rows], meta={"count": rows.count(), "event_choices": list(EVENT_CHOICES)}
        )

    @require_permission("integration.config", scope="project")
    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        # AUTH-012 §4.5 Webhook 订阅数配额（UT-25；BR-11 门控内置）
        from plane.governance.enforcement import check_webhook_quota

        check_webhook_quota(project.workspace)
        clean = validate_endpoint_payload(request.data, project=project)
        secret = new_endpoint_secret()
        endpoint = WebhookEndpoint.objects.create(
            project=project,
            workspace_id=project.workspace_id,
            url=clean["url"],
            events=clean["events"],
            secret_encrypted=encrypt_secret(secret),
            created_by=request.user,
        )
        return created_response(
            _row(endpoint, secret=secret),
            location=request.build_absolute_uri(
                f"/api/v1/workspaces/{kwargs['slug']}/projects/{project.id}/webhooks/{endpoint.id}/"
            ),
        )


class WebhookDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @require_permission("integration.config", scope="project")
    def patch(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        endpoint = _get_endpoint(project, kwargs["endpoint_id"])
        if "events" in request.data:
            events = request.data["events"] or []
            unknown = [e for e in events if e not in EVENT_CHOICES or e == "webhook.ping"]
            if unknown or not events:
                raise AppException("VALIDATION_ERROR", message="events 非法")
            endpoint.events = sorted(set(events))
        if "url" in request.data:
            url = str(request.data["url"] or "").strip()
            if not url.startswith(("http://", "https://")):
                raise AppException("VALIDATION_ERROR", message="url 非法")
            if (
                WebhookEndpoint.objects.filter(
                    project=project,
                    url=url,
                    deleted_at__isnull=True,
                )
                .exclude(pk=endpoint.pk)
                .exists()
            ):
                raise AppException("RESOURCE_ALREADY_EXISTS", message="同项目同 URL 已存在")
            endpoint.url = url
        endpoint.updated_by = request.user
        endpoint.save(update_fields=["events", "url", "updated_by", "updated_at"])
        return success_response(_row(endpoint))

    @require_permission("integration.config", scope="project")
    def delete(self, request, *args, **kwargs):
        from django.utils import timezone

        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        endpoint = _get_endpoint(project, kwargs["endpoint_id"])
        endpoint.deleted_at = timezone.now()
        endpoint.updated_by = request.user
        endpoint.save(update_fields=["deleted_at", "updated_by", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class WebhookDisableView(APIView):
    """手动停用（幂等）。"""

    permission_classes = [IsAuthenticated]

    @require_permission("integration.config", scope="project")
    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        endpoint = _get_endpoint(project, kwargs["endpoint_id"])
        WebhookEndpoint.objects.filter(pk=endpoint.pk).exclude(is_active="active").update(
            is_active="disabled"
        )  # 幂等 SQL（并发约束）
        WebhookEndpoint.objects.filter(pk=endpoint.pk, is_active="active").update(is_active="disabled")
        endpoint.refresh_from_db()
        return success_response(_row(endpoint))


class WebhookEnableView(APIView):
    """手动启用（幂等 + 连败清零，BR-13：启用瞬间无积压补投）。"""

    permission_classes = [IsAuthenticated]

    @require_permission("integration.config", scope="project")
    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        endpoint = _get_endpoint(project, kwargs["endpoint_id"])
        WebhookEndpoint.objects.filter(pk=endpoint.pk).update(is_active="active", consecutive_failures=0)
        endpoint.refresh_from_db()
        return success_response(_row(endpoint))


class WebhookPingView(APIView):
    """测试投递（202 异步；ping 免订阅直达全部端点；成败不计连败，边界 #8）。"""

    permission_classes = [IsAuthenticated]

    @require_permission("integration.config", scope="project")
    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        endpoint = _get_endpoint(project, kwargs["endpoint_id"])
        transaction_marker = {"ping": True, "endpoint_id": str(endpoint.id), "actor": request.user.display_name}
        from django.db import transaction

        transaction.on_commit(
            lambda: dispatch_events(
                "webhook.ping", {"data": transaction_marker, "event_id": None}, project_id=project.id
            )
        )
        return success_response(
            {"ping": "queued", "endpoint_id": str(endpoint.id)}, status_code=status.HTTP_202_ACCEPTED
        )


class WebhookDeliveriesView(APIView):
    """投递日志（游标 + status/event 过滤 + ordering 白名单）。"""

    permission_classes = [IsAuthenticated]

    @require_permission("integration.config", scope="project")
    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        endpoint = _get_endpoint(project, kwargs["endpoint_id"])
        qs = WebhookDelivery.objects.filter(endpoint=endpoint)
        if s := request.query_params.get("status"):
            qs = qs.filter(status=s)
        if e := request.query_params.get("event"):
            qs = qs.filter(event=e)
        ordering = request.query_params.get("ordering", "-created_at")
        if ordering.lstrip("-") not in ("created_at",):
            raise AppException("VALIDATION_INVALID_PARAM", message="ordering 非白名单值")
        qs = qs.order_by(ordering, "-id")
        per_page = min(int(request.query_params.get("per_page", 30) or 30), 100)
        offset = max(int(request.query_params.get("offset", 0) or 0), 0)
        rows = list(qs[offset : offset + per_page])
        import base64

        def _cursor(off: int) -> str | None:
            return base64.b64encode(f"c:{off}:0".encode()).decode() if 0 <= off < qs.count() else None

        return success_response(
            [_delivery_row(r) for r in rows],
            meta={
                "next_cursor": _cursor(offset + per_page) if offset + per_page < qs.count() else None,
                "prev_cursor": _cursor(offset - per_page) if offset > 0 else None,
                "count": len(rows),
                "total_count": qs.count(),
            },
        )


def _delivery_row(r: WebhookDelivery) -> dict:
    return {
        "id": str(r.id),
        "event": r.event,
        "event_id": str(r.event_id),
        "status": r.status,
        "attempts": r.attempts,
        "attempt_count": len(r.attempts),
        "next_retry_at": r.next_retry_at.isoformat() if r.next_retry_at else None,
        "replay_of": str(r.replay_of) if r.replay_of else None,
        "payload": r.payload,
        "created_at": r.created_at.isoformat(),
    }


class WebhookDeliveryDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @require_permission("integration.config", scope="project")
    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        endpoint = _get_endpoint(project, kwargs["endpoint_id"])
        row = WebhookDelivery.objects.filter(pk=kwargs["delivery_id"], endpoint=endpoint).first()
        if row is None:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        return success_response(_delivery_row(row))

    @require_permission("integration.config", scope="project")
    def post(self, request, *args, **kwargs):
        """死信重放（BR-07）：新建 pending 行，原 dead 行不动。"""
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        endpoint = _get_endpoint(project, kwargs["endpoint_id"])
        row = replay_delivery(kwargs["delivery_id"], actor=request.user)
        if row is None or str(row.endpoint_id) != str(endpoint.id):
            raise AppException("RESOURCE_STATE_INVALID", message="仅死信可重放")
        return success_response(_delivery_row(row))
