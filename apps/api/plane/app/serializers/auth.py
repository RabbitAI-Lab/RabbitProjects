from rest_framework import serializers

from plane.db.models import User, Workspace, WorkspaceMember


class SignUpSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(min_length=8, max_length=128)
    display_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    # 注册即激活：无需邮件验证（INFRA-003 排除清单）


class SignInSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()
    remember = serializers.BooleanField(required=False, default=False)


class MeSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email", "display_name", "avatar_url", "is_active", "created_at")


class WorkspaceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Workspace
        fields = ("id", "name", "slug", "description", "logo", "created_at", "updated_at")
        read_only_fields = ("id", "slug", "created_at", "updated_at")


class WorkspaceMemberSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkspaceMember
        fields = ("id", "workspace", "member", "role", "is_active", "created_at")
        read_only_fields = ("id", "workspace", "created_at")
