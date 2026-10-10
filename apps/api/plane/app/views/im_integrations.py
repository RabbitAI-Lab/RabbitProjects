"""企微/钉钉 IM 通道端点（INTG-005 §4.2，P4 R5b）。

通道 CRUD（integration.config）/ 订阅 CRUD（INTG-003 同构去重范式）/
入站动作受理 / 测试投递。出站 im_deliver：钉钉加签（timestamp+HMAC
base64）、企微 Markdown；失败退避 5 次置 degraded（BR-03）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time

from celery import shared_task
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticatedAndActive, require_role
from plane.app.views._access import get_workspace_or_404
from plane.audit.recorder import record
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.db.models import (
    ImSubscription,
    ImWebhookChannel,
    Issue,
    SlackUserMap,
    WorkspaceRole,
)

logger = logging.getLogger("plane.api.im")

EVENT_TYPES = frozenset({"created", "state_changed", "assigned", "commented", "priority_changed"})


def _require_admin(request, view):
    require_role(request, view, WorkspaceRole.ADMIN)


# ── 出站投递 ───────────────────────────────────────────────────────


def _dingtalk_sign(secret: str, timestamp: int) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(secret.encode(), string_to_sign.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def build_wecom_markdown(event: dict) -> dict:
    return {
        "msgtype": "markdown",
        "markdown": {
            "content": f"**【{event.get('title', '任务事件')}】"
            f"{event.get('issue_key', '')} {event.get('issue_name', '')}**\n"
            f"{event.get('detail', '')}"
        },
    }


def build_dingtalk_markdown(event: dict) -> dict:
    return {
        "msgtype": "markdown",
        "markdown": {
            "title": event.get("title", "任务事件"),
            "text": f"### 【{event.get('title', '任务事件')}】"
            f"{event.get('issue_key', '')} {event.get('issue_name', '')}\n\n"
            f"{event.get('detail', '')}",
        },
    }


def deliver_once(channel_id: str, event: dict, *, is_final_retry: bool = False) -> dict:
    """单次投递（纯函数——任务与测试共用；钉钉加签）。"""
    import os

    import requests

    channel = ImWebhookChannel.objects.filter(pk=channel_id).first()
    if channel is None:
        return {"skipped": "missing"}
    payload = build_wecom_markdown(event) if channel.provider == "wecom" else build_dingtalk_markdown(event)
    url = channel.target
    if channel.provider == "dingtalk" and channel.secret_ref:
        secret = os.environ.get(channel.secret_ref.replace("env:", "", 1) or "DINGTALK_SECRET", "")
        ts = int(time.time() * 1000)
        url = f"{url}&timestamp={ts}&sign={quote(_dingtalk_sign(secret, ts))}"
    try:
        resp = requests.post(url, json=payload, timeout=5)
        if resp.status_code != 200:
            raise RuntimeError(f"webhook {resp.status_code}")
    except Exception as exc:  # noqa: BLE001 —— 退避重试
        if is_final_retry:
            ImWebhookChannel.objects.filter(pk=channel.pk).update(is_degraded=True)
            logger.error("im.deliver_degraded channel=%s err=%s", channel.pk, exc)
        raise
    ImWebhookChannel.objects.filter(pk=channel.pk).update(is_degraded=False)
    return {"ok": True}


@shared_task(bind=True, max_retries=5, retry_backoff=True)
def im_deliver(self, channel_id: str, event: dict) -> dict:
    """群机器人投递任务（5 败 degraded BR-03）。"""
    try:
        return deliver_once(channel_id, event, is_final_retry=self.request.retries >= self.max_retries)
    except Exception:  # noqa: BLE001 —— celery 退避
        raise


# ── 通道管理 ───────────────────────────────────────────────────────


class ImChannelView(APIView):
    """GET/POST .../integrations/im/channels/ + DELETE {id}。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, slug):
        _require_admin(request, self)
        ws = get_workspace_or_404(slug, request.user)[0]
        rows = ImWebhookChannel.objects.filter(workspace=ws, deleted_at__isnull=True)
        return success_response(
            [
                {
                    "id": str(ch.id),
                    "provider": ch.provider,
                    "name": ch.name,
                    "target": ch.target[:60] + "…",
                    "is_degraded": ch.is_degraded,
                }
                for ch in rows
            ]
        )

    def post(self, request, slug):
        _require_admin(request, self)
        ws = get_workspace_or_404(slug, request.user)[0]
        p = request.data or {}
        provider = p.get("provider")
        if provider not in ("wecom", "dingtalk"):
            raise AppException(
                "VALIDATION_ERROR", message="provider 非法", details=[{"field": "provider", "code": "INVALID"}]
            )
        target = str(p.get("target") or "").strip()
        if not target.startswith("https://"):
            raise AppException(
                "VALIDATION_ERROR", message="webhook URL 非法", details=[{"field": "target", "code": "INVALID"}]
            )
        if ImWebhookChannel.objects.filter(
            workspace=ws, provider=provider, target=target, deleted_at__isnull=True
        ).exists():
            raise AppException(
                "RESOURCE_ALREADY_EXISTS", message="该 webhook 已配置", details=[{"field": "target", "code": "UNIQUE"}]
            )
        channel = ImWebhookChannel.objects.create(
            workspace=ws,
            provider=provider,
            name=str(p.get("name") or channel_name_default(provider))[:64],
            target=target,
            secret_ref=str(p.get("secret_ref") or ""),
            created_by=request.user,
        )
        record(
            event_key=f"im.channel:{channel.id}:{request.user.id}:{time.time()}",
            category="workflow",
            action="state_changed",
            workspace_id=ws.id,
            actor=request.user,
            obj={"type": "im_channel", "id": str(channel.id)},
            detail={"provider": provider},
        )
        return success_response({"id": str(channel.id)}, status_code=201)

    def delete(self, request, slug, channel_id):
        _require_admin(request, self)
        ws = get_workspace_or_404(slug, request.user)[0]
        ch = ImWebhookChannel.objects.filter(pk=channel_id, workspace=ws).first()
        if ch is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        now = timezone.now()
        ImWebhookChannel.objects.filter(pk=ch.pk).update(deleted_at=now)
        ImSubscription.objects.filter(channel=ch).update(deleted_at=now)
        return success_response({"deleted": True})


def channel_name_default(provider: str) -> str:
    return "企业微信群机器人" if provider == "wecom" else "钉钉群机器人"


# ── 订阅 ───────────────────────────────────────────────────────────


class ImSubscriptionView(APIView):
    """GET/POST .../integrations/im/subscriptions/ + DELETE {id}。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, slug):
        _require_admin(request, self)
        ws = get_workspace_or_404(slug, request.user)[0]
        rows = ImSubscription.objects.filter(channel__workspace=ws, deleted_at__isnull=True)
        return success_response(
            [
                {
                    "id": str(s.id),
                    "channel_id": str(s.channel_id),
                    "provider": s.channel.provider,
                    "project_id": str(s.project_id) if s.project_id else None,
                    "event_types": s.event_types,
                }
                for s in rows
            ]
        )

    def post(self, request, slug):
        _require_admin(request, self)
        ws = get_workspace_or_404(slug, request.user)[0]
        p = request.data or {}
        channel = ImWebhookChannel.objects.filter(pk=p.get("channel_id"), workspace=ws, deleted_at__isnull=True).first()
        if channel is None:
            raise AppException(
                "VALIDATION_ERROR",
                message="channel_id 无效",
                details=[{"field": "channel_id", "code": "DOES_NOT_EXIST"}],
            )
        events = p.get("event_types") or []
        unknown = [e for e in events if e not in EVENT_TYPES]
        if unknown:
            raise AppException(
                "VALIDATION_ERROR",
                message="非法事件类型",
                details=[{"field": "event_types", "code": "INVALID", "message": unknown}],
            )
        project_id = p.get("project_id") or None
        if ImSubscription.objects.filter(channel=channel, project_id=project_id, deleted_at__isnull=True).exists():
            raise AppException(
                "RESOURCE_ALREADY_EXISTS",
                message="该群已订阅此范围（BR-01）",
                details=[{"field": "channel_id", "code": "UNIQUE"}],
            )
        sub = ImSubscription.objects.create(
            channel=channel, project_id=project_id, event_types=list(events), created_by=request.user
        )
        return success_response({"id": str(sub.id)}, status_code=201)

    def delete(self, request, slug, sub_id):
        _require_admin(request, self)
        ws = get_workspace_or_404(slug, request.user)[0]
        sub = ImSubscription.objects.filter(pk=sub_id, channel__workspace=ws).first()
        if sub is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        sub.deleted_at = timezone.now()
        sub.save(update_fields=["deleted_at", "updated_at"])
        return success_response({"deleted": True})


# ── 入站动作与测试投递 ─────────────────────────────────────────────


class ImActionView(APIView):
    """POST /api/v1/integrations/im/actions/ —— 卡片按钮（Slack 同构）。"""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def post(self, request):
        p = request.data if isinstance(request.data, dict) else {}
        action = str(p.get("action") or "")
        user_map = (
            SlackUserMap.objects.filter(slack_user_id=str(p.get("im_user_id") or ""), user__isnull=False)
            .select_related("user")
            .first()
        )
        if user_map is None:
            return Response({"ok": False, "message": "IM 账号未映射系统用户"})
        issue = Issue.objects.filter(pk=p.get("issue_id"), deleted_at__isnull=True).first()
        if issue is None:
            return Response({"ok": False, "message": "任务不存在"})
        user = user_map.user
        if action == "assign_me":
            from plane.db.models import IssueAssignee

            IssueAssignee.objects.get_or_create(issue=issue, assignee=user, defaults={"created_by": user})
            return Response({"ok": True})
        if action == "complete":
            issue.completed_at = timezone.now()
            issue.save(update_fields=["completed_at", "updated_at"])
            return Response({"ok": True})
        return Response({"ok": False, "message": f"未知动作 {action}"})


class ImTestView(APIView):
    """POST .../integrations/im/test/ —— 测试投递（同步直调不进重试面）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, slug):
        _require_admin(request, self)
        ws = get_workspace_or_404(slug, request.user)[0]
        p = request.data or {}
        channel = ImWebhookChannel.objects.filter(pk=p.get("channel_id"), workspace=ws, deleted_at__isnull=True).first()
        if channel is None:
            raise AppException(
                "VALIDATION_ERROR",
                message="channel_id 无效",
                details=[{"field": "channel_id", "code": "DOES_NOT_EXIST"}],
            )
        im_deliver.delay(
            str(channel.id),
            {
                "title": "通道测试",
                "issue_key": "TEST",
                "issue_name": "测试消息",
                "detail": "这是一条通道连通性测试消息（INTG-005）",
            },
        )
        return success_response({"queued": True})


def quote(s: str) -> str:
    import urllib.parse

    return urllib.parse.quote_plus(s)
