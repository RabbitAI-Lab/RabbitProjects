"""资料 / 头像 / 密码 / 重置 序列化器（AUTH-004 §4.2）。

原则：
- 每个端点一个 Serializer，禁止「一个 serializer 多端点复用」（防字段权限漂移）。
- 校验错误全部经 DRF ValidationError → handlers._flatten_validation_detail
  收敛为 ``{field, code, message}`` 形态（INFRA-004 C1）。
"""
from __future__ import annotations

from rest_framework import serializers

from plane.account.services import (
    AVATAR_ALLOWED_MIME,
    AVATAR_MAX_BYTES,
)


class ProfileUpdateSerializer(serializers.Serializer):
    """PATCH /users/me/ —— 4 字段白名单（§1.2 / §4.3.1）。

    安全字段（password / email / is_active …）在 ProfileService 层显式拒绝
    （不允许静默忽略，§1.2 防御性设计）。
    """

    display_name = serializers.CharField(required=False, max_length=150, allow_blank=False)
    first_name = serializers.CharField(required=False, max_length=150, allow_blank=True)
    last_name = serializers.CharField(required=False, max_length=150, allow_blank=True)
    intro = serializers.CharField(required=False, max_length=500, allow_blank=True)

    def validate_display_name(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("昵称不能为空")
        if len(value) > 150:
            raise serializers.ValidationError("昵称最多 150 字符")
        return value

    def validate_intro(self, value: str) -> str:
        value = (value or "").strip()
        if len(value) > 500:
            raise serializers.ValidationError("个人简介最多 500 字符")
        return value


class AvatarPresignSerializer(serializers.Serializer):
    """POST /users/me/avatar/presign/ —— BR-03 + EC-04 边界。"""

    file_name = serializers.CharField(max_length=255)
    file_size = serializers.IntegerField(min_value=1)
    content_type = serializers.CharField(max_length=64)

    def validate_file_size(self, value: int) -> int:
        if value > AVATAR_MAX_BYTES:
            raise serializers.ValidationError(f"头像最大 {AVATAR_MAX_BYTES // (1024*1024)}MB")
        return value

    def validate_content_type(self, value: str) -> str:
        if value not in AVATAR_ALLOWED_MIME:
            raise serializers.ValidationError(f"仅支持 {sorted(AVATAR_ALLOWED_MIME)}")
        return value

    def validate_file_name(self, value: str) -> str:
        # basename 化（防路径注入；与 FILE-001 §2.4 BR-01 同口径）
        from pathlib import Path
        name = Path(value).name
        if not (1 <= len(name) <= 255):
            raise serializers.ValidationError("文件名长度 1~255")
        return name


class AvatarCompleteSerializer(serializers.Serializer):
    """POST /users/me/avatar/complete/ —— 仅 asset_id。"""

    asset_id = serializers.UUIDField()


class ChangePasswordSerializer(serializers.Serializer):
    """POST /users/me/change-password/ —— 三字段 + 业务校验由 PasswordService。"""

    old_password = serializers.CharField()
    new_password = serializers.CharField(min_length=8, max_length=128)
    new_password_confirm = serializers.CharField(min_length=8, max_length=128)


class ForgotPasswordSerializer(serializers.Serializer):
    """POST /auth/forgot-password/ —— 仅 email（防枚举 §2.3）。"""

    email = serializers.EmailField()


class ResetPasswordSerializer(serializers.Serializer):
    """POST /auth/reset-password/ —— 令牌 + 新密码。"""

    token = serializers.CharField()
    new_password = serializers.CharField(min_length=8, max_length=128)
    new_password_confirm = serializers.CharField(min_length=8, max_length=128)
