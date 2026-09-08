"""部门域序列化器（AUTH-007 §4.2，Sprint-8 R1）。"""
from rest_framework import serializers

from plane.db.models import Department, DepartmentGrantBatch, WorkspaceMember
from plane.db.models.roles import ProjectRole


class DepartmentSerializer(serializers.ModelSerializer):
    """部门平铺行（树由前端按 parent_id 组装，§6.4 平铺读取）。"""

    id = serializers.UUIDField(read_only=True)
    parent_id = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()
    with_descendants_member_count = serializers.SerializerMethodField()

    class Meta:
        model = Department
        fields = (
            "id", "parent_id", "name", "path", "sort_order",
            "member_count", "with_descendants_member_count", "created_at",
        )
        read_only_fields = fields

    def get_parent_id(self, obj) -> str | None:
        return str(obj.parent_id) if obj.parent_id else None

    def get_member_count(self, obj) -> int:
        """直属成员数（父子统计独立，§1.2）——列表 include 时由视图预取，避免 N+1。"""
        if hasattr(obj, "_member_count"):
            return obj._member_count
        return WorkspaceMember.objects.filter(
            department=obj, is_active=True, deleted_at__isnull=True,
        ).count()

    def get_with_descendants_member_count(self, obj) -> int:
        if hasattr(obj, "_with_descendants_member_count"):
            return obj._with_descendants_member_count
        from plane.db.services.department import _subtree
        return WorkspaceMember.objects.filter(
            department_id__in=_subtree(obj).values_list("id", flat=True),
            is_active=True, deleted_at__isnull=True,
        ).count()


class DepartmentCreateSerializer(serializers.Serializer):
    """POST .../departments/ —— name + parent_id（null=根部门）。"""

    name = serializers.CharField(max_length=255, trim_whitespace=True)
    parent_id = serializers.UUIDField(required=False, allow_null=True, default=None)

    def validate_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("部门名不可为空", code="INVALID")
        return value


class DepartmentPatchSerializer(serializers.Serializer):
    """PATCH .../departments/{id}/ —— 改名 / 排序（sort_after，二者至少一项）。"""

    name = serializers.CharField(max_length=255, required=False, trim_whitespace=True)
    sort_after = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        if "name" not in attrs and "sort_after" not in attrs:
            raise serializers.ValidationError("name 与 sort_after 至少提供一项")
        return attrs


class DepartmentMoveSerializer(serializers.Serializer):
    """POST .../departments/{id}/move/ —— 换父级（null=提升为根）。"""

    parent_id = serializers.UUIDField(required=False, allow_null=True, default=None)


class DepartmentBulkMoveSerializer(serializers.Serializer):
    """POST .../departments/{id}/members/bulk-move/ —— 批量调部门。"""

    member_ids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=False, max_length=100,
    )
    department_id = serializers.UUIDField(required=False, allow_null=True, default=None)


class DepartmentGrantSerializer(serializers.Serializer):
    """POST .../departments/{id}/grants/（与 preview/ 同构）。"""

    project_id = serializers.UUIDField()
    role = serializers.IntegerField()
    with_descendants = serializers.BooleanField(default=True)

    def validate_role(self, value):
        if value not in ProjectRole.values:
            raise serializers.ValidationError("非法的项目角色", code="NOT_A_CHOICE")
        return value


class DepartmentGrantBatchSerializer(serializers.ModelSerializer):
    """批次详情：快照逐人溯源（§4.2 grants/{batch_id}/）。"""

    id = serializers.UUIDField(read_only=True)
    department_id = serializers.SerializerMethodField()
    project_id = serializers.SerializerMethodField()

    class Meta:
        model = DepartmentGrantBatch
        fields = (
            "id", "department_id", "project_id", "role", "with_descendants",
            "added_count", "role_changed_count", "skipped_count",
            "unchanged_count", "member_snapshot", "created_at", "created_by",
        )
        read_only_fields = fields

    def get_department_id(self, obj) -> str:
        """授权时刻的部门 id（department_id_snapshot 自含，FK 删除后仍可溯源）。"""
        return str(obj.department_id_snapshot)

    def get_project_id(self, obj) -> str:
        return str(obj.project_id)


class DepartmentGrantResultSerializer(serializers.Serializer):
    """POST grants/ 201 响应（§4.2 示例：四计数 + skipped_detail）。"""

    id = serializers.UUIDField()
    added = serializers.ListField(child=serializers.UUIDField())
    role_changed = serializers.ListField(child=serializers.UUIDField())
    skipped = serializers.ListField(child=serializers.DictField())
    unchanged = serializers.ListField(child=serializers.UUIDField())
    skipped_detail = serializers.ListField(child=serializers.DictField(), required=False)
