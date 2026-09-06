"""文件分享序列化器（FILE-004 §4.2.1 / §4.2.5）。

Serializer 做形状 + 边界校验（字段子码 ``TOO_SMALL`` / ``TOO_LARGE``，
§4.2.5 约定）：上限（BR-11）、延期状态机等业务校验统一在 service 层
（错误码可映射，FILE-002 同款边界）。
"""
from __future__ import annotations

from rest_framework import serializers

from plane.db.models import FileShareLink

MAX_EXPIRY_DAYS = 365


class ShareCreateSerializer(serializers.Serializer):
    """POST …/files/{asset_id}/share-links/ —— 密码 4~64（§2.5）、有效期 ≤365 天。"""

    permission = serializers.ChoiceField(
        choices=FileShareLink.Permission.choices,
        default=FileShareLink.Permission.DOWNLOAD,
    )
    password = serializers.CharField(
        required=False, allow_blank=True, trim_whitespace=True
    )
    expires_in_days = serializers.IntegerField(required=False, allow_null=True)

    def validate_password(self, value: str) -> str:
        if not value:  # 空 = 无密码
            return value
        if len(value) < 4:
            raise serializers.ValidationError("密码至少 4 位", code="TOO_SMALL")
        if len(value) > 64:
            raise serializers.ValidationError("密码不能超过 64 位", code="TOO_LARGE")
        return value

    def validate_expires_in_days(self, value) -> int | None:
        if value is None:  # 空 = 永久
            return None
        if value < 1:
            raise serializers.ValidationError("有效期至少 1 天", code="TOO_SMALL")
        if value > MAX_EXPIRY_DAYS:
            raise serializers.ValidationError(
                f"有效期不能超过 {MAX_EXPIRY_DAYS} 天", code="TOO_LARGE")
        return value


class ShareExtendSerializer(serializers.Serializer):
    """POST …/share-links/{link_id}/extend/ —— 1~365 整数（结果上限在 service）。"""

    extend_days = serializers.IntegerField()

    def validate_extend_days(self, value: int) -> int:
        if value < 1:
            raise serializers.ValidationError("延期步长至少 1 天", code="TOO_SMALL")
        if value > MAX_EXPIRY_DAYS:
            raise serializers.ValidationError(
                f"延期步长不能超过 {MAX_EXPIRY_DAYS} 天", code="TOO_LARGE")
        return value
