"""项目成员序列化器（PROJ-002 §4.2.3 §4.2.4）。

CLAUDE.md 教训 #3：SerializerMethodField 必须列入 Meta.fields；
本模块 ``ProjectMemberSerializer.user`` 为 SerializerMethodField，已挂入 fields。
"""
from __future__ import annotations

from rest_framework import serializers

from plane.db.models import ProjectMember

MAX_SEARCH_LENGTH = 64
MAX_BATCH_MEMBERS = 20


class ProjectMemberSerializer(serializers.ModelSerializer):
    """GET 成员列表 / PATCH 单成员响应 —— 含 user 与 workspace_role（§4.2.3）。"""

    id = serializers.UUIDField(read_only=True)
    user = serializers.SerializerMethodField()
    workspace_role = serializers.SerializerMethodField()
    joined_at = serializers.SerializerMethodField()
    # 视图 list() 以子查询注解提供；PATCH 单行响应无注解时兜底 0
    assigned_issue_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = ProjectMember
        fields = (
            "id",
            "user",
            "role",
            "workspace_role",
            "is_active",
            "joined_at",
            "assigned_issue_count",
        )
        read_only_fields = ("id", "is_active", "joined_at", "assigned_issue_count")

    def get_user(self, obj):
        m = obj.member
        return {
            "id": str(m.id),
            "display_name": m.display_name,
            "email": m.email,
            "avatar_url": m.avatar_url or None,
        }

    def get_workspace_role(self, obj):
        # ProjectMember.workspace = 反范式冗余；取用户当前在该工作空间的角色（一次 JOIN）。
        # 注意：members 可能已被 TEAM-002 移除；返回 None 表示该用户已不在空间（前端展示「已移出成员」灰头像）。
        from plane.db.models import WorkspaceMember

        role = WorkspaceMember.objects.filter(
            workspace_id=obj.workspace_id, member_id=obj.member_id, is_active=True
        ).values_list("role", flat=True).first()
        return role

    def get_joined_at(self, obj):
        # 重新添加会创建新行（§2.3），所以 created_at 即「本次加入时间」。
        return obj.created_at


class ProjectMemberBulkAddSerializer(serializers.Serializer):
    """POST .../members/ —— 批量添加（同角色，≤ 20，BR-02/BR-03）。"""

    member_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        max_length=MAX_BATCH_MEMBERS,
    )
    role = serializers.IntegerField()


class ProjectMemberRoleChangeSerializer(serializers.Serializer):
    """PATCH .../members/{member_id}/ —— 仅 role 字段（BR-03）。"""

    role = serializers.IntegerField()


class ProjectSearchQuerySerializer(serializers.Serializer):
    """GET .../projects/?q=&status=&favorite=&favorite_first=&ordering=

    仅做参数合法性校验；分页参数由 cursor paginator 校验。
    """

    q = serializers.CharField(
        required=False, allow_blank=True, max_length=MAX_SEARCH_LENGTH
    )
    status = serializers.ChoiceField(
        required=False, choices=("active", "archived", "all")
    )
    favorite = serializers.BooleanField(required=False, default=False)
    favorite_first = serializers.BooleanField(required=False, default=False)
