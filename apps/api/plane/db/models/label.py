from django.db import models

from plane.db.models.base import BaseModel


class Label(BaseModel):
    project = models.ForeignKey("db.Project", on_delete=models.CASCADE, related_name="labels", verbose_name="所属项目")
    name = models.CharField(max_length=128, verbose_name="标签名称")
    color = models.CharField(max_length=9, default="#6B7280", verbose_name="标签颜色")
    sort_order = models.FloatField(default=65535.0, verbose_name="排序值")

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
