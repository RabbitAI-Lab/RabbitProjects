from django.db import models

from plane.db.models.base import BaseModel


class State(BaseModel):
    class Group(models.TextChoices):
        BACKLOG = "backlog", "待规划"
        UNSTARTED = "unstarted", "未开始"
        STARTED = "started", "进行中"
        COMPLETED = "completed", "已完成"
        CANCELLED = "cancelled", "已取消"

    project = models.ForeignKey("db.Project", on_delete=models.CASCADE, related_name="states", verbose_name="所属项目")
    name = models.CharField(max_length=64, verbose_name="状态名称")
    color = models.CharField(max_length=9, default="#6B7280", verbose_name="状态颜色")
    group = models.CharField(
        max_length=16, choices=Group.choices, default=Group.BACKLOG, db_index=True, verbose_name="语义分组"
    )
    sort_order = models.FloatField(default=65535.0, verbose_name="排序值")
    is_default = models.BooleanField(default=False, verbose_name="是否默认状态")
    issue_type = models.ForeignKey(
        "db.IssueType",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="states",
        verbose_name="专属任务类型",
    )

    class Meta(BaseModel.Meta):
        db_table = "states"
        verbose_name = "任务状态"
        ordering = ("sort_order",)
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name", "issue_type"],
                condition=models.Q(deleted_at__isnull=True),
                name="uniq_state_name_per_project_type",
            ),
            models.UniqueConstraint(
                fields=["project", "issue_type"],
                condition=models.Q(is_default=True, deleted_at__isnull=True),
                name="uniq_default_state_per_project_type",
            ),
        ]
