"""通知中心视图（COLLAB-001 §4.3.5/6/7）。

端点（挂 /users/me/notifications/ 下）：
  GET    /users/me/notifications/?unread=&per_page=          列表（未读过滤）
  GET    /users/me/notifications/unread-count/                未读计数（O(1)）
  POST   /users/me/notifications/{id}/read/                  单条已读（幂等）
  POST   /users/me/notifications/read-all/                   全部已读（本人域 BR-12）

权限：本人域（Notification.objects.filter(receiver=request.user) 收口）—— rbac §6.2。
"""
from __future__ import annotations

from rest_framework.exceptions import NotFound
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated

from plane.app.serializers.notification import NotificationSerializer
from plane.base.response import success_response
from plane.db.models import Notification
from plane.db.services.notify import mark_read, read_all, unread_count


# ── 列表 ──
class NotificationListView(GenericAPIView):
    """GET /users/me/notifications/ —— ?unread=true|false&per_page=&cursor="""

    permission_classes = [IsAuthenticated]
    serializer_class = NotificationSerializer

    def get(self, request, *args, **kwargs):
        qs = Notification.objects.filter(receiver=request.user)
        unread_param = (request.query_params.get("unread") or "").lower()
        if unread_param in ("true", "1", "yes"):
            qs = qs.filter(read_at__isnull=True)
        elif unread_param in ("false", "0", "no"):
            qs = qs.filter(read_at__isnull=False)
        qs = qs.order_by("-created_at", "-id")
        try:
            per_page = min(int(request.query_params.get("per_page", 20)), 100)
        except (TypeError, ValueError):
            per_page = 20
        page_qs = list(qs[:per_page])
        total = qs.count()
        meta = {
            "count": len(page_qs),
            "total_count": total,
            "per_page": per_page,
            "unread_count": unread_count(user=request.user),
        }
        return success_response(
            NotificationSerializer(page_qs, many=True).data, meta=meta,
        )


# ── 未读计数 ──
class NotificationUnreadCountView(GenericAPIView):
    """GET /users/me/notifications/unread-count/ —— 30s 轮询使用。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        return success_response(
            {"unread_count": unread_count(user=request.user)},
            headers={"Cache-Control": "no-store"},
        )


# ── 单条已读 ──
class NotificationMarkReadView(GenericAPIView):
    """POST /users/me/notifications/{id}/read/ —— 幂等。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        nid = kwargs["notification_id"]
        # 行级隔离：先校验可见性
        try:
            n = Notification.objects.get(id=nid, receiver=request.user)
        except Notification.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        mark_read(user=request.user, notification_id=str(n.id))
        # 始终刷新（幂等场景：read_at 已被设置，refresh 拿到最新值）
        n.refresh_from_db(fields=["read_at"])
        return success_response({
            "id": str(n.id),
            "read_at": n.read_at.isoformat() if n.read_at else None,
        })


# ── 全部已读 ──
class NotificationReadAllView(GenericAPIView):
    """POST /users/me/notifications/read-all/ —— 仅本人域（BR-12）。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        updated = read_all(user=request.user)
        return success_response({
            "updated_count": updated,
            "unread_count": 0,
        })
