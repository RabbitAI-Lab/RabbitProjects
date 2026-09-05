"""通知序列化器（COLLAB-001 §4.3.5/6/7）。

列表与单条响应同一形态；``data`` 直透（BR-15：必含跳转最小闭包）。
"""
from __future__ import annotations

from rest_framework import serializers

from plane.db.models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    """GET 列表 / POST 已读响应 —— data 直透（前端按 event 分支消费）。"""

    id = serializers.UUIDField(read_only=True)

    class Meta:
        model = Notification
        fields = (
            "id",
            "event",
            "title",
            "data",
            "read_at",
            "created_at",
        )
        read_only_fields = fields
