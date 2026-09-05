"""FileAsset —— 全系统唯一文件通道（FILE-001 §4.1）。

P1 挂载点：issue（任务附件）、avatar（AUTH-004 头像）。
P2+ 通过 entity_type 注册制扩展，零 DDL。
"""
from django.db import models

from plane.db.models.base import BaseModel


class FileAsset(BaseModel):
    """文件资产 —— 对标 Plane FileAsset，参见 `docs/sprint-1-mvp/FILE-001-task-attachment.md` §4.1。"""

    class Status(models.TextChoices):
        UPLOADING = "uploading", "直传中"
        UPLOADED = "uploaded", "已上传"
        ABANDONED = "abandoned", "已弃置"

    class EntityType(models.TextChoices):
        """注册制（FILE-001 §2.4 BR-12）：新增宿主须在 §1.4 矩阵登记并经架构评审。"""
        ISSUE = "issue", "任务"
        AVATAR = "avatar", "头像"

    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="assets",
        verbose_name="所属工作空间",
    )
    project = models.ForeignKey(
        "db.Project",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="assets",
        verbose_name="所属项目",
        help_text="头像等无项目实体为 NULL",
    )
    entity_type = models.CharField(
        max_length=32, choices=EntityType.choices, verbose_name="宿主类型"
    )
    entity_id = models.UUIDField(verbose_name="宿主实体 ID")

    attributes = models.JSONField(
        default=dict,
        verbose_name="原始属性",
        help_text='{"name":"error-500.png","size":2097152,"mime":"image/png","ext":".png"}',
    )
    size = models.BigIntegerField(default=0, verbose_name="字节数", db_index=True)
    storage_path = models.TextField(
        verbose_name="对象键",
        help_text="ws/proj/entity_type/entity_id/{ulid}.{ext}",
    )

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.UPLOADING,
        db_index=True,
        verbose_name="上传状态",
    )
    is_uploaded = models.BooleanField(
        default=False,
        verbose_name="完成确认位",
        help_text="status=uploaded 的冗余布尔，兼容 Plane 语义",
    )
    uploaded_by = models.ForeignKey(
        "db.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="uploaded_files",
        verbose_name="上传人",
    )

    class Meta(BaseModel.Meta):
        db_table = "file_assets"
        verbose_name = "文件资产"
        indexes = [
            # 反查宿主附件列表：WHERE entity_type=? AND entity_id=? AND deleted_at IS NULL ORDER BY created_at
            models.Index(fields=["entity_type", "entity_id"], name="idx_asset_entity"),
            # 清理任务扫描：status + created_at（mark_abandoned_uploads）
            models.Index(fields=["status", "created_at"], name="idx_asset_status_time"),
            # 存储治理：按工作空间统计体积（配额与报表）
            models.Index(fields=["workspace", "status"], name="idx_asset_ws_status"),
        ]
        # 白名单双层防御：DB CheckConstraint 由 §0003 迁移通过 RunSQL 以原生 PG `~*` 落表
        # （Django ORM 的 __regex 仅桥接 PG `~`，不区分大小写在原生层表达）。
