"""视图序列化器（BOARD-003 §4.2）。

读侧 IssueViewSerializer 统一 GET 列表 / 详情 / PATCH 回显；
写侧 IssueViewWriteSerializer 只做类型与长度校验，业务防线
（数量上限 / 扁平 filters / group_by 白名单 / card_fields 键域）在
view_service.validate_view_payload —— 需要 project 上下文，视图层调用。
"""
from __future__ import annotations

from rest_framework import serializers

from plane.db.models import IssueView

MAX_VIEW_NAME_LENGTH = 128  # §4.1.1


class IssueViewSerializer(serializers.ModelSerializer):
    """GET 列表 / 详情 / PATCH 回显（统一封装）。"""

    project_id = serializers.UUIDField(read_only=True)
    workspace_id = serializers.UUIDField(read_only=True)
    owner_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = IssueView
        fields = (
            "id",
            "project_id",
            "workspace_id",
            "name",
            "description",
            "access",
            "layout",
            "owner_id",
            "is_system",
            "is_locked",
            "filters",
            "display_props",
            "sort_order",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "project_id",
            "workspace_id",
            "owner_id",
            "is_system",
            "is_locked",
            "created_at",
            "updated_at",
        )


class IssueViewWriteSerializer(serializers.Serializer):
    """POST/PATCH .../views/ 的载荷校验（类型层）。"""

    name = serializers.CharField(max_length=MAX_VIEW_NAME_LENGTH)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    access = serializers.CharField(required=False, default="personal")
    layout = serializers.ChoiceField(choices=IssueView.Layout.choices, required=False, default=IssueView.Layout.LIST)
    filters = serializers.DictField(required=False, default=dict)
    display_props = serializers.DictField(required=False, default=dict)
    sort_order = serializers.FloatField(required=False)
