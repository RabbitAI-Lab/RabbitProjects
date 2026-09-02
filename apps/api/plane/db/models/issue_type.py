from django.db import models

from plane.db.models.base import BaseModel


class IssueType(BaseModel):
    workspace = models.ForeignKey(
        "db.Workspace", on_delete=models.CASCADE, related_name="issue_types", verbose_name="所属工作空间"
    )
    name = models.CharField(max_length=64, verbose_name="类型名称")
    description = models.TextField(blank=True, verbose_name="类型说明")
    icon = models.CharField(max_length=64, default="circle-dot", verbose_name="图标")
    color = models.CharField(max_length=9, default="#6B7280", verbose_name="主题色")
    is_default = models.BooleanField(default=False, verbose_name="是否默认类型")
    is_active = models.BooleanField(default=True, db_index=True, verbose_name="是否启用")
    is_system = models.BooleanField(default=False, verbose_name="是否内置")
    sort_order = models.PositiveIntegerField(default=1000, verbose_name="显示排序")

    class Meta(BaseModel.Meta):
        db_table = "issue_types"
        verbose_name = "任务类型"
        ordering = ("sort_order", "created_at")
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"],
                condition=models.Q(deleted_at__isnull=True),
                name="uniq_issue_type_name_per_workspace",
            ),
            models.UniqueConstraint(
                fields=["workspace"],
                condition=models.Q(is_default=True, deleted_at__isnull=True),
                name="uniq_default_issue_type_per_workspace",
            ),
        ]
