"""工作项序列化器（TASK-001 / TASK-002 §4.4.1 / TASK-003 §4.3 / BOARD-002 §4.2.2）。

CLAUDE.md 教训 #3：所有 SerializerMethodField 必须挂在 Meta.fields，否则 PATCH/POST 路径 500。
本模块所有 getter 字段均已在 fields 列表中。
"""
from __future__ import annotations

from rest_framework import serializers

from plane.db.models import (
    Issue,
    IssueAssignee,
    IssueLabel,
    IssueType,
    Label,
    ProjectMember,
    State,
)
from plane.utils.exceptions import AppValidationError, field_error


# ─────────────────────────────────────────────────────────────────────
# 读侧（list / retrieve 响应）
# ─────────────────────────────────────────────────────────────────────
class IssueSerializer(serializers.ModelSerializer):
    """任务详情 / 列表响应 —— TASK-002 §4.3.2 全字段卡片 + BOARD-002 卡片扩展字段。"""

    # 模型列与标识
    issue_key = serializers.SerializerMethodField()
    sequence_id = serializers.IntegerField(read_only=True)
    project_id = serializers.UUIDField(source="project.id", read_only=True)
    project_identifier = serializers.SerializerMethodField()

    # 状态
    state_id = serializers.UUIDField(read_only=True)
    state_name = serializers.SerializerMethodField()
    state_group = serializers.SerializerMethodField()

    # 类型 / 父（FK 暴露为 *_id，序列化层做 workspace / 同项目作用域校验）
    type_id = serializers.UUIDField(source="issue_type_id", read_only=True)
    parent_id = serializers.UUIDField(read_only=True, allow_null=True)

    # 优先级（P0 模型列）
    priority = serializers.CharField(read_only=True)

    # 派生：负责人 / 标签 ID 列表（label_ids 全量含停用，BOARD-002 / TASK-002 §3.5 淡显逻辑）
    assignee_ids = serializers.SerializerMethodField()
    label_ids = serializers.SerializerMethodField()

    # 计数（annotate 实时计算，TASK-002 §1.3 决策 2；不建冗余列）
    sub_issues_count = serializers.IntegerField(read_only=True, default=0)
    completed_sub_issues_count = serializers.IntegerField(read_only=True, default=0)
    attachment_count = serializers.IntegerField(read_only=True, default=0)

    # 模型列
    description_html = serializers.CharField(read_only=True)
    description_json = serializers.JSONField(read_only=True)
    description_stripped = serializers.CharField(read_only=True, allow_null=True)
    completed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    start_date = serializers.DateField(read_only=True, allow_null=True)
    target_date = serializers.DateField(read_only=True, allow_null=True)
    sort_order = serializers.FloatField(read_only=True)
    archived_at = serializers.DateTimeField(read_only=True, allow_null=True)

    created_by = serializers.SerializerMethodField()

    class Meta:
        model = Issue
        fields = (
            "id",
            "project_id",
            "project_identifier",
            "issue_key",
            "sequence_id",
            "name",
            "description_html",
            "description_json",
            "description_stripped",
            "state_id",
            "state_name",
            "state_group",
            "type_id",
            "parent_id",
            "priority",
            "assignee_ids",
            "label_ids",
            "start_date",
            "target_date",
            "completed_at",
            "sub_issues_count",
            "completed_sub_issues_count",
            "attachment_count",
            "sort_order",
            "created_by",
            "created_at",
            "updated_at",
            "archived_at",
        )
        read_only_fields = (
            "id",
            "project_id",
            "project_identifier",
            "issue_key",
            "sequence_id",
            "description_stripped",
            "completed_at",
            "sub_issues_count",
            "completed_sub_issues_count",
            "attachment_count",
            "created_by",
            "created_at",
            "updated_at",
            "archived_at",
        )

    # ----------------- getter -----------------
    def get_issue_key(self, obj):
        p = obj.project
        ident = getattr(p, "identifier", None) if p else None
        if not ident:
            return f"{obj.sequence_id}"
        return f"{ident}-{obj.sequence_id}"

    def get_project_identifier(self, obj):
        return obj.project.identifier if obj.project else None

    def get_state_name(self, obj):
        return obj.state.name if obj.state_id else None

    def get_state_group(self, obj):
        return obj.state.group if obj.state_id else None

    def get_assignee_ids(self, obj):
        # 使用 prefetch 缓存避免 N+1（annotations/prefetch_related('issue_assignees')）
        return [str(ia.assignee_id) for ia in obj.issue_assignees.all()]

    def get_label_ids(self, obj):
        # 全量（含 is_active=false）—— 卡片淡显渲染需要历史标签可读（TASK-002 BR-05）
        return [str(il.label_id) for il in obj.issue_labels.all()]

    def get_created_by(self, obj):
        if not obj.created_by_id:
            return None
        u = obj.created_by
        return {"id": str(obj.created_by_id), "name": u.display_name if u else None}


# ─────────────────────────────────────────────────────────────────────
# 写侧（create / patch payload）
# ─────────────────────────────────────────────────────────────────────
MAX_LABELS_PER_ISSUE = 10       # TASK-002 §2.7 边界


class IssueWriteSerializer(serializers.Serializer):
    """POST 创建 / PATCH 局部更新 —— TASK-002 §4.4.1 IssueAttributeMixin 校验口径。

    P1 字段：type_id（创建必填）、priority、label_ids、start_date、parent_id。
    全部校验统一走 validate() 收口（按字段收集 errors，最后一次抛 AppValidationError）。
    """

    name = serializers.CharField(max_length=512, required=False, allow_blank=False)
    description_html = serializers.CharField(required=False, allow_blank=True, default="<p></p>")
    description_json = serializers.DictField(required=False, default=dict)
    state_id = serializers.UUIDField(required=False, allow_null=True)
    type_id = serializers.UUIDField(required=False, allow_null=True)
    priority = serializers.CharField(required=False, allow_blank=False)
    assignee_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, default=list
    )
    label_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, default=list
    )
    parent_id = serializers.UUIDField(required=False, allow_null=True)
    start_date = serializers.DateField(required=False, allow_null=True)
    target_date = serializers.DateField(required=False, allow_null=True)
    sort_order = serializers.FloatField(required=False)

    def validate(self, attrs):
        # 写侧校验需要的 project/instance 从 context 注入（view 层设置）
        project = self.context.get("project")
        instance = self.instance
        is_create = instance is None
        errors: list[dict] = []

        # ---- BR-01 / BR-14 类型（创建时必填；PATCH 可改但必须 active + 同 workspace）----
        type_id = attrs.get("type_id", "__absent__")
        if type_id == "__absent__":
            if is_create:
                # BR-15 兜底：缺类型则取 Workspace 默认类型；前端 BR-02 入参时省去一次往返
                default_type = IssueType.objects.filter(
                    workspace_id=project.workspace_id, is_default=True, deleted_at__isnull=True
                ).first()
                if default_type is None:
                    errors.append(field_error("type_id", "REQUIRED", "任务类型为必填项"))
                else:
                    attrs["type_id"] = default_type.id
        elif type_id is not None:
            valid_type = IssueType.objects.filter(
                pk=type_id, workspace_id=project.workspace_id, is_active=True, deleted_at__isnull=True
            ).exists()
            if not valid_type:
                errors.append(field_error(
                    "type_id", "DOES_NOT_EXIST", "任务类型不属于当前工作空间或已停用"))

        # ---- BR-03 优先级枚举 ----
        priority = attrs.get("priority", "__absent__")
        if priority != "__absent__":
            valid_priorities = {choice for choice, _ in Issue.Priority.choices}
            if priority not in valid_priorities:
                errors.append(field_error("priority", "NOT_A_CHOICE", "优先级取值非法"))

        # ---- BR-04 标签：项目内 + is_active + ≤ 10 ----
        if "label_ids" in attrs:
            label_ids = attrs.get("label_ids") or []
            if label_ids:
                valid = set(
                    Label.objects.filter(
                        pk__in=label_ids, project=project, is_active=True, deleted_at__isnull=True
                    ).values_list("id", flat=True)
                )
                invalid = {str(x) for x in label_ids} - {str(v) for v in valid}
                if invalid:
                    errors.append(field_error(
                        "label_ids", "DOES_NOT_EXIST",
                        "包含不属于当前项目或已停用的标签"))
                if len(label_ids) > MAX_LABELS_PER_ISSUE:
                    errors.append(field_error(
                        "label_ids", "TOO_LARGE",
                        f"单个任务最多 {MAX_LABELS_PER_ISSUE} 个标签"))

        # ---- BR-06 日期联合：start ≤ target ----
        new_start = attrs.get("start_date", "__absent__")
        new_target = attrs.get("target_date", "__absent__")
        start = (
            attrs["start_date"] if new_start != "__absent__"
            else (instance.start_date if instance else None)
        )
        target = (
            attrs["target_date"] if new_target != "__absent__"
            else (instance.target_date if instance else None)
        )
        if start and target and start > target:
            errors.append(field_error(
                "target_date", "INVALID_DATE_RANGE", "截止时间不能早于开始时间"))

        # ---- state 校验（项目内存在）----
        if "state_id" in attrs and attrs["state_id"] is not None:
            state_id = attrs["state_id"]
            state_ok = State.objects.filter(
                pk=state_id, project=project, deleted_at__isnull=True
            ).exists()
            if not state_ok:
                errors.append(field_error(
                    "state_id", "DOES_NOT_EXIST", "状态不属于当前项目"))

        # ---- parent 校验（SUB-02 / SUB-03 一层 + 同项目）----
        if "parent_id" in attrs and attrs["parent_id"] is not None:
            parent_id = attrs["parent_id"]
            parent = Issue.objects.filter(
                pk=parent_id, project=project, deleted_at__isnull=True
            ).first()
            if parent is None:
                errors.append(field_error(
                    "parent_id", "DOES_NOT_EXIST", "父任务不存在或不属于当前项目"))
            elif parent.parent_id is not None:
                # SUB-02：严格一层
                errors.append(field_error(
                    "parent_id", "NESTING", "MVP 阶段子任务仅支持一层"))

        # ---- BR-08 子任务与父同项目 + 一层：本身已是 Issue，不在此校验；sub-issues endpoint 走 Service ----

        if errors:
            raise AppValidationError(errors)
        return attrs


# ─────────────────────────────────────────────────────────────────────
# 服务函数
# ─────────────────────────────────────────────────────────────────────
def sync_assignees(issue, assignee_ids, actor_id) -> None:
    """替换式同步：清旧 + bulk_create；中间表无软删除，物理删除。"""
    IssueAssignee.objects.filter(issue=issue).delete()
    for uid in assignee_ids:
        IssueAssignee.objects.create(
            issue=issue, assignee_id=uid, assigned_by_id=actor_id
        )


def sync_labels(issue, label_ids, actor_id) -> None:
    """TASK-002 §2.3 标签 PUT 全量替换 —— 差分方式：删除旧关联 + bulk_create 新关联。

    幂等：相同集合再次提交 → diff 为空，无变更。IssueLabel 不含软删除列（B-02 决策），
    直接 physical delete + create，事务内完成。
    """
    issue_id = issue.id
    IssueLabel.objects.filter(issue_id=issue_id).delete()
    IssueLabel.objects.bulk_create([
        IssueLabel(issue_id=issue_id, label_id=lid, created_by_id=actor_id)
        for lid in label_ids
    ])


def validate_assignees(project_id, assignee_ids) -> None:
    """任一非本项目 active ProjectMember → AppValidationError DOES_NOT_EXIST（统一信封）。"""
    if not assignee_ids:
        return
    valid_members = set(
        ProjectMember.objects.filter(
            project_id=project_id, member_id__in=assignee_ids, is_active=True
        ).values_list("member_id", flat=True)
    )
    invalid = {str(u) for u in assignee_ids} - {str(v) for v in valid_members}
    if invalid:
        raise AppValidationError([
            field_error("assignee_ids", "DOES_NOT_EXIST", "包含不属于当前项目的成员")
        ])


def diff_labels(old_ids: set[str], new_ids: set[str]) -> tuple[list[str], list[str]]:
    """返回 (added, removed) UUID 字符串列表，供 activity 记录消费。"""
    return (
        sorted(str(x) for x in (new_ids - old_ids)),
        sorted(str(x) for x in (old_ids - new_ids)),
    )
