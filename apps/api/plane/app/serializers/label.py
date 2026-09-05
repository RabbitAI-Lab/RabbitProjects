"""项目标签序列化器（TASK-002 §4.3.1）。

设计要点：
- LabelSerializer 读侧：含 is_active=true/false（卡片淡显需要历史可读，BR-05）。
- LabelWriteSerializer 写侧：name + color（创建 / PATCH 同构）。
- PATCH 单字段部分更新（is_active / name / color / sort_order）—— DRF 标准行为。
"""
from __future__ import annotations

from rest_framework import serializers

from plane.db.models import Label
from plane.utils.exceptions import AppValidationError, field_error

MAX_LABEL_NAME_LENGTH = 128  # TASK-002 §2.7 边界，与 uniq_label_name_per_project 一致


class LabelSerializer(serializers.ModelSerializer):
    """GET 列表 / PATCH 详情 / 强制删除响应（统一封装）。"""

    project_id = serializers.UUIDField(read_only=True)
    usage_count = serializers.SerializerMethodField()

    class Meta:
        model = Label
        fields = (
            "id",
            "project_id",
            "name",
            "color",
            "is_active",
            "sort_order",
            "usage_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "project_id", "usage_count", "created_at", "updated_at")

    def get_usage_count(self, obj):
        # 调用方需 prefetch_related('issue_labels') 命中缓存；未命中走单次 COUNT
        return obj.issue_labels.filter(deleted_at__isnull=True).count() if hasattr(obj, "issue_labels") else 0


class LabelWriteSerializer(serializers.Serializer):
    """POST / PATCH .../labels/ 的 payload 校验。"""

    name = serializers.CharField(max_length=MAX_LABEL_NAME_LENGTH)
    color = serializers.CharField(max_length=9, default="#6B7280")
    is_active = serializers.BooleanField(required=False, default=True)
    sort_order = serializers.FloatField(required=False)

    def validate_color(self, value: str) -> str:
        v = (value or "").strip()
        if not (v.startswith("#") and len(v) in (4, 7, 9)):
            raise AppValidationError([
                field_error("color", "INVALID", "颜色格式须为 #RGB / #RRGGBB / #RRGGBBAA")
            ])
        return v.upper()
