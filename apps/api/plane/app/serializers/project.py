from rest_framework import serializers

from plane.db.models import Project, State


class ProjectSerializer(serializers.ModelSerializer):
    current_user_role = serializers.IntegerField(read_only=True, default=0)
    total_members = serializers.IntegerField(read_only=True, default=0)
    total_issues = serializers.IntegerField(read_only=True, default=0)
    default_state_id = serializers.UUIDField(read_only=True, default=None)
    is_favorite = serializers.BooleanField(read_only=True, default=False)

    class Meta:
        model = Project
        fields = (
            "id",
            "name",
            "description",
            "identifier",
            "status",
            "created_at",
            "updated_at",
            "current_user_role",
            "total_members",
            "total_issues",
            "default_state_id",
            "is_favorite",
        )
        read_only_fields = ("id", "identifier", "status", "created_at", "updated_at")


class ProjectWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    identifier = serializers.CharField(min_length=2, max_length=5)

    def validate_identifier(self, v):
        return v.strip().upper()


class StateSerializer(serializers.ModelSerializer):
    class Meta:
        model = State
        fields = ("id", "name", "color", "group", "sort_order", "is_default")
        read_only_fields = ("id", "sort_order", "is_default")
