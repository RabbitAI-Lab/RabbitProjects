"""工作空间成员 / 邀请序列化器（TEAM-002 §4.2）。

CLAUDE.md 教训 #3：SerializerMethodField 必须列入 Meta.fields。
"""
from __future__ import annotations

from rest_framework import serializers

from plane.db.models import WorkspaceMember, WorkspaceMemberInvite
from plane.db.models.roles import WorkspaceRole

MAX_SEARCH_LENGTH = 64
MAX_BATCH_INVITES = 20
# 邀请可预设的角色（OWNER 仅能由转让产生，BR-02）
INVITE_ALLOWED_ROLES = (WorkspaceRole.MEMBER, WorkspaceRole.ADMIN)


def _validate_invite_role(value: int) -> int:
    """邀请预设角色仅 MEMBER / ADMIN（BR-02）。"""
    if value not in INVITE_ALLOWED_ROLES:
        raise serializers.ValidationError(
            "邀请角色仅支持成员或管理员", code="NOT_A_CHOICE",
        )
    return value


class _MemberUserField(serializers.SerializerMethodField):
    """`user` 嵌套对象 —— 头像/昵称/邮箱/ID。"""


class WorkspaceMemberSerializer(serializers.ModelSerializer):
    """GET 成员列表 / PATCH 单成员响应（§4.2.2）。

    SerializerMethodField 全部挂入 Meta.fields，避免 GET/PATCH 写入路径 500
    （CLAUDE.md 教训 #3）。
    """

    id = serializers.UUIDField(read_only=True)
    user = serializers.SerializerMethodField()
    joined_at = serializers.SerializerMethodField()
    is_owner = serializers.SerializerMethodField()

    class Meta:
        model = WorkspaceMember
        fields = ("id", "user", "role", "is_active", "joined_at", "is_owner")
        read_only_fields = ("id", "is_active", "joined_at", "is_owner")

    def get_user(self, obj):
        m = obj.member
        return {
            "id": str(m.id),
            "display_name": m.display_name,
            "email": m.email,
            "avatar_url": m.avatar_url or None,
        }

    def get_joined_at(self, obj):
        # 复活软删行保留 created_at 作为首次加入时间（§2.3 复活语义）
        return obj.created_at

    def get_is_owner(self, obj):
        return obj.role == WorkspaceRole.OWNER


class WorkspaceMemberRoleChangeSerializer(serializers.Serializer):
    """PATCH .../members/{member_id}/ —— 仅 role 字段（BR-05/BR-06）。"""

    role = serializers.IntegerField()

    def validate_role(self, value):
        if value == WorkspaceRole.OWNER:
            raise serializers.ValidationError(
                "所有者仅能通过转让所有权产生", code="NOT_A_CHOICE",
            )
        if value not in (WorkspaceRole.MEMBER, WorkspaceRole.ADMIN):
            raise serializers.ValidationError("角色非法", code="NOT_A_CHOICE")
        return value


class WorkspaceInviteSerializer(serializers.Serializer):
    """POST .../invitations/ —— 批量邀请（≤ 20，BR-01/BR-02）。"""

    emails = serializers.ListField(
        child=serializers.CharField(allow_blank=False, max_length=254),
        min_length=1,
        max_length=MAX_BATCH_INVITES,
    )
    role = serializers.IntegerField(default=WorkspaceRole.MEMBER)

    def validate_emails(self, value):
        if len(value) > MAX_BATCH_INVITES:
            raise serializers.ValidationError(
                f"单次最多邀请 {MAX_BATCH_INVITES} 个邮箱",
                code="TOO_LONG",
            )
        # 格式校验：归一小写后逐条 EmailValidator；非法 → 400 整请求拒绝
        from django.core.exceptions import ValidationError as DjangoValidationError
        from django.core.validators import validate_email

        for raw in value:
            email = (raw or "").strip().lower()
            if not email:
                raise serializers.ValidationError("邮箱不能为空", code="INVALID")
            try:
                validate_email(email)
            except DjangoValidationError as exc:
                raise serializers.ValidationError(
                    f"邮箱格式非法：{raw}", code="INVALID",
                ) from exc
        return value

    def validate_role(self, value):
        return _validate_invite_role(value)


class WorkspaceTransferOwnershipSerializer(serializers.Serializer):
    """POST .../ownership/transfer/ —— 转让所有权（BR-08）。"""

    new_owner_member_id = serializers.UUIDField()
    confirm_name = serializers.CharField(max_length=255)


class WorkspaceInviteLiteSerializer(serializers.ModelSerializer):
    """GET .../invitations/ —— 待接受邀请列表（§4.2）。"""

    invited_by = serializers.SerializerMethodField()

    class Meta:
        model = WorkspaceMemberInvite
        fields = ("id", "email", "role", "status", "expires_at",
                  "created_at", "invited_by")
        read_only_fields = fields

    def get_invited_by(self, obj):
        if obj.invited_by is None:
            return None
        return {
            "id": str(obj.invited_by.id),
            "display_name": obj.invited_by.display_name,
            "email": obj.invited_by.email,
        }


class InvitationPrecheckSerializer(serializers.Serializer):
    """GET /api/v1/invitations/{token}/ —— 接受页预检（脱敏，§3.3）。"""

    workspace = serializers.DictField()
    role = serializers.IntegerField()
    invited_by = serializers.DictField(allow_null=True)
    expires_at = serializers.DateTimeField()
    masked_email = serializers.CharField()


def mask_email(email: str) -> str:
    """邮箱脱敏：保留首字符与域名（li***@ex.com）。"""
    if "@" not in email:
        return "***"
    local, _, domain = email.partition("@")
    if not local:
        return f"***@{domain}"
    if len(local) <= 2:
        masked_local = local[0] + "***"
    else:
        masked_local = local[0] + "***" + local[-1]
    return f"{masked_local}@{domain}"


class InvitationAcceptResponseSerializer(serializers.Serializer):
    """POST /api/v1/invitations/{token}/accept/ —— 接受响应。"""

    workspace = serializers.DictField()
    role = serializers.IntegerField()
    current_user_role = serializers.IntegerField()
