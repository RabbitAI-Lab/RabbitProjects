"""FileFolder —— 项目文件库目录树（FILE-002 §4.1.1）。

自引用树、深度 ≤ 5（复用 TASK-004 层级治理经验）、可见性逐目录独立声明
（不继承，§1.6 竞品参考：P3 视需要加继承）。

根层同名同层拒绝是双层防线（BR-01）：
1. Serializer 全层 clean（含根层显式查重）；
2. DB 偏条件唯一约束 ``uniq_folder_name_per_parent``（非根层）+ §4.1.3 迁移
   RunSQL 的 ``uniq_folder_name_root`` COALESCE 表达式唯一索引（根层——
   PG 视 NULL 互异，偏条件约束对 ``(NULL, name)`` 不去重）。
"""
from django.db import models

from plane.db.models.base import BaseModel


class FileFolder(BaseModel):
    """项目文件目录 —— 自引用树，深度 ≤ 5，可见性独立声明（不继承）。"""

    class Visibility(models.TextChoices):
        ALL = "all", "全员可见"
        ADMINS = "admins", "仅项目管理员"
        MEMBERS = "members", "指定成员"

    project = models.ForeignKey(
        "db.Project",
        on_delete=models.CASCADE,
        related_name="file_folders",
        verbose_name="所属项目",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
        verbose_name="父目录",
    )
    name = models.CharField(max_length=64, verbose_name="目录名")
    visibility = models.CharField(
        max_length=16,
        choices=Visibility.choices,
        default=Visibility.ALL,
        verbose_name="可见性",
    )
    allowed_members = models.JSONField(
        default=list,
        blank=True,
        verbose_name="指定可见成员（UUID 列表，members 态生效）",
    )

    class Meta(BaseModel.Meta):
        db_table = "file_folders"
        verbose_name = "文件目录"
        indexes = [
            models.Index(fields=["project", "parent"], name="idx_folder_project_parent"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["parent", "name"],
                condition=models.Q(deleted_at__isnull=True),
                name="uniq_folder_name_per_parent",
            ),
            # 注：偏条件唯一在根层失效——parent=NULL 时 PG 视 NULL 互异，(NULL, name)
            # 不去重；根层同层同名由 Serializer clean + §4.1.3 COALESCE 表达式唯一索引
            # （uniq_folder_name_root）双层兜底（BR-01）。
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.project_id})"
