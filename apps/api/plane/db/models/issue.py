from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.utils import timezone
from django.utils.html import strip_tags

from plane.db.models.base import BaseModel


class Issue(BaseModel):
    class Priority(models.TextChoices):
        NONE = "none", "无"
        LOW = "low", "低"
        MEDIUM = "medium", "中"
        HIGH = "high", "高"
        URGENT = "urgent", "紧急"

    project = models.ForeignKey("db.Project", on_delete=models.CASCADE, related_name="issues", verbose_name="所属项目")
    name = models.CharField(max_length=512, verbose_name="标题")

    description_json = models.JSONField(default=dict, blank=True, verbose_name="描述-ProseMirror JSON")
    description_html = models.TextField(default="<p></p>", blank=True, verbose_name="描述-HTML")
    description_binary = models.BinaryField(null=True, blank=True, verbose_name="描述-Yjs Binary")
    description_stripped = models.TextField(null=True, blank=True, verbose_name="描述-纯文本")

    issue_type = models.ForeignKey(
        "db.IssueType", on_delete=models.SET_NULL, null=True, blank=True, related_name="issues", verbose_name="任务类型"
    )
    state = models.ForeignKey(
        "db.State", on_delete=models.SET_NULL, null=True, related_name="issues", verbose_name="当前状态"
    )
    priority = models.CharField(
        max_length=16, choices=Priority.choices, default=Priority.NONE, db_index=True, verbose_name="优先级"
    )

    # created_by / updated_by 由 BaseModel 提供，不重复声明（INFRA-003 §4.7 处置）
    assignees = models.ManyToManyField(
        "db.User",
        through="IssueAssignee",
        through_fields=("issue", "assignee"),
        related_name="assigned_issues",
        blank=True,
        verbose_name="负责人",
    )

    start_date = models.DateField(null=True, blank=True, verbose_name="开始时间")
    target_date = models.DateField(null=True, blank=True, db_index=True, verbose_name="截止时间")
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name="完成时间")

    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="sub_issues", verbose_name="父工作项"
    )

    sequence_id = models.IntegerField(default=1, verbose_name="项目内序列号")
    sort_order = models.FloatField(default=65535.0, verbose_name="排序值")

    # P1 新增（FILE-001 §7.1）：多态 FileAsset 方案的卡片计数冗余列；F()+1 / F()-1 维护。
    attachment_count = models.IntegerField(
        default=0,
        verbose_name="附件计数",
        help_text="F() 维护，列表 / 看板卡片徽标消费（FILE-001 §4.1 §2.3 BR-09）",
    )

    labels = models.ManyToManyField(
        "db.Label",
        through="IssueLabel",
        through_fields=("issue", "label"),
        related_name="issues",
        blank=True,
        verbose_name="标签",
    )

    custom_fields = models.JSONField(default=dict, blank=True, verbose_name="自定义字段值")

    archived_at = models.DateTimeField(null=True, blank=True, verbose_name="归档时间")

    class Meta(BaseModel.Meta):
        db_table = "issues"
        verbose_name = "工作项"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["project", "sequence_id"],
                condition=models.Q(deleted_at__isnull=True),
                name="uniq_issue_sequence_per_project",
            ),
            models.CheckConstraint(
                check=models.Q(start_date__isnull=True)  # type: ignore[call-arg]  # Django 5.1 起 check→condition，暂用旧名（运行时兼容）
                | models.Q(target_date__isnull=True)
                | models.Q(start_date__lte=models.F("target_date")),
                name="chk_issue_start_before_target",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "state", "sort_order"], name="idx_issue_proj_state_sort"),
            models.Index(fields=["project", "issue_type"], name="idx_issue_proj_type"),
            models.Index(fields=["parent"], name="idx_issue_parent"),
            models.Index(
                fields=["project", "created_at"],
                condition=models.Q(archived_at__isnull=True, deleted_at__isnull=True),
                name="idx_issue_active_by_project",
            ),
            GinIndex(fields=["custom_fields"], name="idx_issue_custom_fields"),
            GinIndex(name="idx_issue_desc_trgm", fields=["description_stripped"], opclasses=["gin_trgm_ops"]),
        ]

    def save(self, *args, **kwargs):
        if self.description_html:
            self.description_stripped = (
                None if self.description_html == "<p></p>" else strip_tags(self.description_html)
            )
        if self.state and self.state.group == "completed" and self.completed_at is None:
            self.completed_at = timezone.now()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.project.identifier}-{self.sequence_id} {self.name}"


class IssueAssignee(BaseModel):
    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="issue_assignees")
    assignee = models.ForeignKey("db.User", on_delete=models.CASCADE, related_name="issue_assignees")
    assigned_by = models.ForeignKey(
        "db.User", on_delete=models.SET_NULL, null=True, related_name="assigned_issue_records"
    )

    class Meta(BaseModel.Meta):
        db_table = "issue_assignees"
        verbose_name = "工作项负责人"
        constraints = [models.UniqueConstraint(fields=["issue", "assignee"], name="uniq_issue_assignee")]
        indexes = [models.Index(fields=["assignee", "issue"], name="idx_assignee_issue")]


class IssueLabel(BaseModel):
    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="issue_labels")
    label = models.ForeignKey("db.Label", on_delete=models.CASCADE, related_name="issue_labels")

    class Meta(BaseModel.Meta):
        db_table = "issue_labels"
        verbose_name = "工作项标签"
        constraints = [models.UniqueConstraint(fields=["issue", "label"], name="uniq_issue_label")]


class IssueActivity(BaseModel):
    class Verb(models.TextChoices):
        CREATED = "created", "创建"
        UPDATED = "updated", "更新"
        DELETED = "deleted", "删除"

    issue = models.ForeignKey(
        "db.Issue", on_delete=models.CASCADE, null=True, related_name="issue_activities", verbose_name="工作项"
    )
    actor = models.ForeignKey(
        "db.User", on_delete=models.SET_NULL, null=True, related_name="issue_activities", verbose_name="操作人"
    )
    verb = models.CharField(max_length=16, choices=Verb.choices, default=Verb.CREATED, verbose_name="动作")
    field = models.CharField(max_length=64, null=True, blank=True, verbose_name="变更字段名")
    old_value = models.TextField(null=True, blank=True, verbose_name="变更前值")
    new_value = models.TextField(null=True, blank=True, verbose_name="变更后值")
    old_identifier = models.UUIDField(null=True, blank=True, verbose_name="变更前关联对象 ID")
    new_identifier = models.UUIDField(null=True, blank=True, verbose_name="变更后关联对象 ID")
    comment = models.TextField(blank=True, verbose_name="人类可读描述")
    epoch = models.FloatField(null=True, verbose_name="毫秒时间戳")

    class Meta(BaseModel.Meta):
        db_table = "issue_activities"
        verbose_name = "工作项操作日志"
        ordering = ("created_at",)
        indexes = [
            models.Index(fields=["issue", "created_at"], name="idx_activity_issue_time"),
            models.Index(fields=["actor", "created_at"], name="idx_activity_actor_time"),
            models.Index(fields=["field"], name="idx_activity_field"),
        ]


class IssueLink(BaseModel):
    class RelationType(models.TextChoices):
        BLOCKS = "blocks", "阻塞"
        IS_BLOCKED_BY = "is_blocked_by", "被阻塞于"
        RELATES_TO = "relates_to", "关联"
        DUPLICATES = "duplicates", "重复于"

    INVERSE_MAP = {
        "blocks": "is_blocked_by",
        "is_blocked_by": "blocks",
        "relates_to": "relates_to",
        "duplicates": "duplicates",
    }

    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="issue_links", verbose_name="源工作项")
    related_issue = models.ForeignKey(
        "db.Issue", on_delete=models.CASCADE, related_name="related_issue_links", verbose_name="目标工作项"
    )
    relation_type = models.CharField(
        max_length=24, choices=RelationType.choices, default=RelationType.RELATES_TO, verbose_name="关联类型"
    )

    class Meta(BaseModel.Meta):
        db_table = "issue_links"
        verbose_name = "工作项关联"
        constraints = [
            models.UniqueConstraint(
                fields=["issue", "related_issue", "relation_type"],
                condition=models.Q(deleted_at__isnull=True),
                name="uniq_issue_relation",
            ),
            models.CheckConstraint(check=~models.Q(issue=models.F("related_issue")),  # type: ignore[call-arg]
                name="chk_issue_link_no_self"),
        ]
        indexes = [
            models.Index(fields=["issue", "relation_type"], name="idx_link_issue_type"),
            models.Index(fields=["related_issue", "relation_type"], name="idx_link_related_type"),
        ]
