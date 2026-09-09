"""自定义角色域序列化器（AUTH-008 §4.2，Sprint-8 R2）。"""
from rest_framework import serializers

from plane.db.models import CustomRole, ProjectRoleAssignment


class CustomRoleSerializer(serializers.ModelSerializer):
    """角色行（列表与详情共用；assigned_count 由视图注水避免 N+1）。"""

    id = serializers.UUIDField(read_only=True)
    permissions_count = serializers.SerializerMethodField()
    assigned_count = serializers.SerializerMethodField()

    class Meta:
        model = CustomRole
        fields = (
            "id", "name", "description", "permissions",
            "is_builtin_template", "template_key",
            "permissions_count", "assigned_count", "created_at",
        )
        read_only_fields = fields

    def get_permissions_count(self, obj) -> int:
        return len(obj.permissions or [])

    def get_assigned_count(self, obj) -> int:
        if hasattr(obj, "_assigned_count"):
            return obj._assigned_count
        return obj.assignments.filter(deleted_at__isnull=True).count()


class CustomRoleCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=40, required=False,
                                 allow_blank=True, trim_whitespace=True)
    description = serializers.CharField(max_length=200, required=False,
                                       allow_blank=True, default="")
    permissions = serializers.ListField(
        child=serializers.CharField(max_length=64), required=False, default=list,
    )
    template_key = serializers.CharField(max_length=40, required=False,
                                        allow_null=True, default=None)


class CustomRolePatchSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=40, required=False,
                                 trim_whitespace=True)
    description = serializers.CharField(max_length=200, required=False,
                                       allow_blank=True)
    permissions = serializers.ListField(
        child=serializers.CharField(max_length=64), required=False)


class RoleAssignSerializer(serializers.Serializer):
    """POST role-assignments/ —— 挂接 {role_id}。"""

    role_id = serializers.UUIDField()


class RoleBulkAssignSerializer(serializers.Serializer):
    """POST roles/{id}/assignments/bulk/ —— user_ids 或 department_id 二选一。"""

    user_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=False,
        max_length=100,
    )
    department_id = serializers.UUIDField(required=False, allow_null=True)


class RoleAssignmentRowSerializer(serializers.ModelSerializer):
    """挂接行（成员视角的已挂角色清单）。"""

    id = serializers.UUIDField(read_only=True)
    role_id = serializers.SerializerMethodField()
    role_name = serializers.SerializerMethodField()
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = ProjectRoleAssignment
        fields = ("id", "role_id", "role_name", "permissions", "created_at")
        read_only_fields = fields

    def get_role_id(self, obj) -> str:
        return str(obj.role_id)

    def get_role_name(self, obj) -> str:
        return obj.role.name

    def get_permissions(self, obj) -> list[str]:
        return obj.role.permissions or []
