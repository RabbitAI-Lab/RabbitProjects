from django.db import models

from plane.db.models.base import BaseModel


class Label(BaseModel):
    project = models.ForeignKey("db.Project", on_delete=models.CASCADE, related_name="labels", verbose_name="所属项目")
    name = models.CharField(max_length=128, verbose_name="标签名称")
    color = models.CharField(max_length=9, default="#6B7280", verbose_name="标签颜色")
    sort_order = models.FloatField(default=65535.0, verbose_name="排序值")
    # P1 新增（TASK-002 §4.1.2 / §4.1.4）：unified-issue-model §2.7 当前 Label 定义未含 is_active，
    # 架构文档待回改；本字段由 0003 迁移同 AddField 落地。
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        verbose_name="是否启用",
        help_text="停用后不可新挂载；已挂载卡片淡显保留（BR-05）",
    )

    class Meta(BaseModel.Meta):
        db_table = "labels"
        verbose_name = "标签"
        ordering = ("sort_order",)
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name"],
                condition=models.Q(deleted_at__isnull=True),
                name="uniq_label_name_per_project",
            ),
        ]
        # P1 新增（TASK-002 §4.1.4 注释）：idx_label_project_active 由 0003 迁移同 AddIndex 落表。
        indexes = [
            models.Index(fields=["project", "is_active"], name="idx_label_project_active"),
        ]
