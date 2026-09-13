"""Slack/Zoom 集成端点（INTG-003 §4.4，P4 R5）。

订阅 CRUD（COALESCE 唯一去重→409 UNIQUE 预检）、用户映射、入站动作
（Interactive Button 回调：查看/指派/完成）、Zoom 会议关联与入站纪要
回挂受理。出站投递 slack_deliver（合并窗口 5 分钟锚点 chat.update）
同文件 bgtasks 形态经 plane/bgtasks/slack_sync.py（队列注同 ADR-0032#1）。
真实 Slack API 调用经密保库句柄解析（dev 兜底 env——LdapClient 同款），
测试注入伪 client。
"""

from __future__ import annotations

import hashlib
import logging

from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticatedAndActive, require_role
from plane.app.views._access import get_workspace_or_404
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.db.models import (
    Issue,
    IssueMeeting,
    SlackChannelSubscription,
    SlackUserMap,
    WorkspaceRole,
)

logger = logging.getLogger("plane.api.slack")

EVENT_TYPES = frozenset({"created", "state_changed", "assigned", "commented", "priority_changed"})


def _require_admin(request, view):
    require_role(request, view, WorkspaceRole.ADMIN)


def _ws_or_404(request, slug):
    return get_workspace_or_404(slug, request.user)[0]


def _slack_installation(ws):
    from plane.db.models import IntegrationInstallation

    inst = IntegrationInstallation.objects.filter(workspace=ws, provider="slack", deleted_at__isnull=True).first()
    if inst is None:
        raise AppException("RESOURCE_STATE_INVALID", message="Slack 通道未安装（先完成 OAuth 安装流）")
    return inst


# ── 订阅 CRUD ──────────────────────────────────────────────────────


class SlackSubscriptionView(APIView):
    """GET/POST .../integrations/slack/subscriptions/ + DELETE {id}。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, slug):
        _require_admin(request, self)
        ws = _ws_or_404(request, slug)
        inst = _slack_installation(ws)
        rows = SlackChannelSubscription.objects.filter(installation=inst, deleted_at__isnull=True)
        return success_response(
            [
                {
                    "id": str(s.id),
                    "channel_id": s.channel_id,
                    "channel_name": s.channel_name,
                    "project_id": str(s.project_id) if s.project_id else None,
                    "event_types": s.event_types,
                    "thread_sync": s.thread_sync,
                }
                for s in rows
            ]
        )

    def post(self, request, slug):
        _require_admin(request, self)
        ws = _ws_or_404(request, slug)
        inst = _slack_installation(ws)
        p = request.data or {}
        channel_id = str(p.get("channel_id") or "").strip()
        if not channel_id.startswith("C") or len(channel_id) > 32:
            raise AppException(
                "VALIDATION_ERROR",
                message="channel_id 须 C 开头 ≤32 字符",
                details=[{"field": "channel_id", "code": "INVALID"}],
            )
        events = p.get("event_types") or []
        unknown = [e for e in events if e not in EVENT_TYPES]
        if unknown:
            raise AppException(
                "VALIDATION_ERROR",
                message="非法事件类型",
                details=[{"field": "event_types", "code": "INVALID", "message": unknown}],
            )
        project_id = p.get("project_id") or None  # null=全项目
        dup = SlackChannelSubscription.objects.filter(
            installation=inst, channel_id=channel_id, project_id=project_id, deleted_at__isnull=True
        ).exists()
        if dup:  # ①Serializer 预检 409
            raise AppException(
                "RESOURCE_ALREADY_EXISTS",
                message="该频道已订阅此范围",
                details=[{"field": "channel_id", "code": "UNIQUE"}],
            )
        sub = SlackChannelSubscription.objects.create(
            installation=inst,
            channel_id=channel_id,
            channel_name=str(p.get("channel_name") or "")[:128],
            project_id=project_id,
            event_types=list(events),
            thread_sync=bool(p.get("thread_sync", False)),
            created_by=request.user,
        )
        return success_response({"id": str(sub.id)}, status_code=201)

    def delete(self, request, slug, sub_id):
        _require_admin(request, self)
        ws = _ws_or_404(request, slug)
        inst = _slack_installation(ws)
        sub = SlackChannelSubscription.objects.filter(pk=sub_id, installation=inst).first()
        if sub is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        sub.deleted_at = timezone.now()  # 软删让位可重建（BR-14）
        sub.updated_by = request.user
        sub.save(update_fields=["deleted_at", "updated_by", "updated_at"])
        return success_response({"deleted": True})


# ── 用户映射 ───────────────────────────────────────────────────────


class SlackUserMapView(APIView):
    """GET/PUT .../integrations/slack/user-map/。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, slug):
        _require_admin(request, self)
        ws = _ws_or_404(request, slug)
        inst = _slack_installation(ws)
        rows = SlackUserMap.objects.filter(installation=inst)
        return success_response(
            [
                {
                    "id": str(m.id),
                    "slack_user_id": m.slack_user_id,
                    "slack_display_name": m.slack_display_name,
                    "slack_email": m.slack_email,
                    "user_id": str(m.user_id) if m.user_id else None,
                }
                for m in rows
            ]
        )

    def put(self, request, slug):
        _require_admin(request, self)
        ws = _ws_or_404(request, slug)
        inst = _slack_installation(ws)
        p = request.data or {}
        slack_user_id = str(p.get("slack_user_id") or "").strip()
        if not slack_user_id:
            raise AppException(
                "VALIDATION_ERROR",
                message="slack_user_id 必填",
                details=[{"field": "slack_user_id", "code": "REQUIRED"}],
            )
        SlackUserMap.objects.update_or_create(
            installation=inst,
            slack_user_id=slack_user_id,
            defaults={
                "slack_email": str(p.get("slack_email") or f"{slack_user_id}@slack.local"),
                "slack_display_name": str(p.get("slack_display_name") or "")[:128],
                "user_id": p.get("user_id") or None,
                "updated_by": request.user,
            },
        )
        return success_response({"mapped": True})


# ── 入站动作（Interactive Button；协议豁免统一信封——Slack 回包形态）──


class SlackActionView(APIView):
    """POST /api/v1/integrations/slack/actions/ —— Slack 回调（payload JSON）。

    Slack 期望 200 空体（错误时 JSON message）；不走统一信封（协议端点
    豁免边界——Slack 回包语义，同 SCIM 范式）。
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def post(self, request):
        import json as _json

        raw = request.data.get("payload") if isinstance(request.data, dict) else None
        if isinstance(raw, str):  # Slack 表单编码形态
            try:
                payload = _json.loads(raw)
            except ValueError:
                return Response({"ok": False}, status=400)
        elif isinstance(raw, dict):  # JSON 嵌套形态
            payload = raw
        else:
            return Response({"ok": False}, status=400)
        action = (payload.get("actions") or [{}])[0].get("action_id", "")
        issue_id = (payload.get("callback_id") or "").replace("issue:", "")
        user_map = None
        from plane.db.models import SlackUserMap

        user_map = (
            SlackUserMap.objects.filter(slack_user_id=(payload.get("user") or {}).get("id", ""), user__isnull=False)
            .select_related("user")
            .first()
        )
        if user_map is None:
            return Response({"ok": False, "message": "Slack 账号未映射系统用户"})
        user = user_map.user
        issue = Issue.objects.filter(pk=issue_id, deleted_at__isnull=True).first()
        if issue is None:
            return Response({"ok": False, "message": "任务不存在"})
        if action == "assign_me":
            from plane.db.models import IssueAssignee

            IssueAssignee.objects.get_or_create(issue=issue, assignee=user, defaults={"created_by": user})
            return Response({"ok": True})
        if action == "complete":
            issue.completed_at = timezone.now()
            issue.save(update_fields=["completed_at", "updated_at"])
            return Response({"ok": True})
        if action == "view":
            return Response({"ok": True, "url": f"/issues/{issue_id}"})
        return Response({"ok": False, "message": f"未知动作 {action}"})


# ── Zoom ───────────────────────────────────────────────────────────


class ZoomMeetingView(APIView):
    """POST .../integrations/zoom/meetings/ —— 创建会议关联；GET 列表。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, slug):

        _require_admin(request, self)
        ws = _ws_or_404(request, slug)
        p = request.data or {}
        issue = Issue.objects.filter(pk=p.get("issue_id"), project__workspace=ws, deleted_at__isnull=True).first()
        if issue is None:
            raise AppException(
                "VALIDATION_ERROR", message="issue_id 无效", details=[{"field": "issue_id", "code": "DOES_NOT_EXIST"}]
            )
        meeting = IssueMeeting.objects.create(
            issue=issue,
            meeting_id=str(p.get("meeting_id") or hashlib.sha1(f"{issue.id}".encode()).hexdigest()[:11]),
            join_url=str(p.get("join_url") or "")[:512],
            topic=str(p.get("topic") or issue.name)[:255],
            start_time=p.get("start_time") or None,
            created_by=request.user,
        )
        return success_response({"id": str(meeting.id), "meeting_id": meeting.meeting_id}, status_code=201)

    def get(self, request, slug):
        _require_admin(request, self)
        ws = _ws_or_404(request, slug)
        rows = IssueMeeting.objects.filter(issue__project__workspace=ws).order_by("-created_at")[:50]
        return success_response(
            [
                {
                    "id": str(m.id),
                    "issue_id": str(m.issue_id),
                    "meeting_id": m.meeting_id,
                    "topic": m.topic,
                    "join_url": m.join_url,
                    "start_time": m.start_time,
                }
                for m in rows
            ]
        )


class ZoomMinutesInboundView(APIView):
    """POST /api/v1/integrations/zoom/minutes/ —— 纪要回挂受理（HMAC 验签）。

    受理落点：纪要文本挂任务评论（COLLAB-001 载体）；验签失败 401。
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def post(self, request):
        from plane.db.models import IssueComment, ZoomConnector

        connector = ZoomConnector.objects.filter(is_active=True).first()
        if connector is None:
            return Response({"ok": False, "message": "Zoom 未配置"}, status=404)
        sig = request.headers.get("X-Zoom-Signature", "")
        secret = (connector.webhook_secret_ref or "").replace("env:", "")
        import os

        secret_value = os.environ.get(secret or "ZOOM_WEBHOOK_SECRET", "zoom-secret")
        import hmac as _hmac

        payload_raw = request.body.decode("utf-8", "ignore")
        expect = _hmac.new(secret_value.encode(), payload_raw.encode(), hashlib.sha256).hexdigest()
        if not sig or not _hmac.compare_digest(expect, sig):
            return Response({"ok": False, "message": "验签失败"}, status=401)
        p = request.data or {}
        meeting = IssueMeeting.objects.filter(meeting_id=str(p.get("meeting_id") or "")).select_related("issue").first()
        if meeting is None:
            return Response({"ok": False, "message": "会议未关联"}, status=404)
        text = str(p.get("summary") or "")[:5000]
        if text:
            IssueComment.objects.create(
                issue=meeting.issue,
                actor=meeting.created_by,
                comment_html=f"<p>🎥 Zoom 纪要（{meeting.topic}）：<br>{text}</p>",
                comment_stripped=f"Zoom 纪要（{meeting.topic}）：{text}",
                created_by=meeting.created_by,
            )
        return Response({"ok": True})
