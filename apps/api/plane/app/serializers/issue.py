from rest_framework import serializers

from plane.db.models import Issue, IssueAssignee, ProjectMember


class IssueSerializer(serializers.ModelSerializer):
    issue_key = serializers.SerializerMethodField()
    sequence_id = serializers.IntegerField(read_only=True)
    state_id = serializers.UUIDField(allow_null=True, required=False)
    state_name = serializers.SerializerMethodField()
    state_group = serializers.SerializerMethodField()
    assignee = serializers.SerializerMethodField()
    created_by = serializers.SerializerMethodField()

    class Meta:
        model = Issue
        fields = (
            "id",
            "project",
            "issue_key",
            "sequence_id",
            "name",
            "description_html",
            "description_json",
            "state_id",
            "state_name",
            "state_group",
            "assignee",
            "start_date",
            "target_date",
            "sort_order",
            "created_by",
            "created_at",
            "updated_at",
            "archived_at",
        )
        read_only_fields = ("id", "project", "issue_key", "sequence_id", "created_at", "updated_at")

    def get_issue_key(self, obj):
        p = obj.project
        return f"{p.identifier}-{obj.sequence_id}" if p else ""

    def get_state_name(self, obj):
        return obj.state.name if obj.state_id else None

    def get_state_group(self, obj):
        return obj.state.group if obj.state_id else None

    def get_created_by(self, obj):
        if not obj.created_by_id:
            return None
        return {"id": str(obj.created_by_id), "name": obj.created_by.display_name}

    def get_assignee(self, obj):
        first = obj.issue_assignees.select_related("assignee").first()
        if not first:
            return None
        return {
            "id": str(first.assignee_id),
            "name": first.assignee.display_name,
            "avatar_url": first.assignee.avatar_url or None,
        }


class IssueWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=512)
    description_html = serializers.CharField(required=False, allow_blank=True, default="<p></p>")
    description_json = serializers.DictField(required=False, default=dict)
    state_id = serializers.UUIDField(required=False, allow_null=True)
    assignee_ids = serializers.ListField(child=serializers.UUIDField(), required=False, default=list, max_length=1)
    start_date = serializers.DateField(required=False, allow_null=True)
    target_date = serializers.DateField(required=False, allow_null=True)
    sort_order = serializers.FloatField(required=False)


def sync_assignees(issue, assignee_ids, actor_id) -> None:
    """P0 限单人（BR-5）。替换式：先清旧 + 后 bulk_create；中间表无软删除，物理删除。"""
    IssueAssignee.objects.filter(issue=issue).delete()
    for uid in assignee_ids:
        IssueAssignee.objects.create(issue=issue, assignee_id=uid, assigned_by_id=actor_id)


def validate_assignees(project_id, assignee_ids) -> None:
    """任一非本项目 active ProjectMember → 整事务回滚（VALIDATION_ERROR + DOES_NOT_EXIST）。"""
    if not assignee_ids:
        return
    valid_members = set(
        ProjectMember.objects.filter(project_id=project_id, member_id__in=assignee_ids, is_active=True).values_list(
            "member_id", flat=True
        )
    )
    if set(str(u) for u in assignee_ids) - {str(v) for v in valid_members}:
        from rest_framework.exceptions import ValidationError

        raise ValidationError({"assignee_ids": "DOES_NOT_EXIST"})
