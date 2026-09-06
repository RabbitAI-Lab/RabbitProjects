"""FileFolder / UploadSession / FileVersion —— 文件域三模型。

- FileFolder（FILE-002 §4.1.1）：自引用树、深度 ≤ 5（复用 TASK-004 层级治理经验）、
  可见性逐目录独立声明（不继承，§1.6 竞品参考：P3 视需要加继承）。

  根层同名同层拒绝是双层防线（BR-01）：
  1. Serializer 全层 clean（含根层显式查重）；
  2. DB 偏条件唯一约束 ``uniq_folder_name_per_parent``（非根层）+ §4.1.3 迁移
     RunSQL 的 ``uniq_folder_name_root`` COALESCE 表达式唯一索引（根层——
     PG 视 NULL 互异，偏条件约束对 ``(NULL, name)`` 不去重）。

- UploadSession（FILE-003 §4.1.1）：分片上传会话——真相在 S3 multipart（§1.2），
  本表只记元数据与已传片号（断点续传索引 + 24h TTL 孤儿清理）。
  **不建独立 FileChunk 表**：片的真相由 MinIO 管理，续传核对用 ListParts。
- FileVersion（FILE-003 §4.1.1）：文件版本——只增账本，回滚 = 复用旧对象的新行
  （BR-09 零拷贝），淘汰仅限最旧非当前版本且过引用检查（BR-08）。
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


class UploadSession(BaseModel):
    """分片上传会话 —— 真相在 S3 multipart；本表记元数据与已传片号（FILE-003 §4.1.1）。

    - 会话 ↔ 文件关系由本表 ``asset`` FK 承载（非 OneToOne）：会话是历史账本——
      completed/expired/aborted 保留不释放、也不阻塞新会话（BR-15；FILE-002 原预留
      ``file_assets.upload_session`` OneToOne 列已撤销，§4.1.2 注 2）。
    - ``uploaded_chunks`` 只记「片号 + etag」断点索引，续传核对以 ListParts 为准
      （§6.3 决策 1：真相在 S3，表是索引——双写不一致类 bug 结构性消失）。
    """

    class Status(models.TextChoices):
        UPLOADING = "uploading", "上传中"
        COMPLETED = "completed", "已完成"
        ABORTED = "aborted", "已取消"
        EXPIRED = "expired", "已过期"

    project = models.ForeignKey(
        "db.Project",
        on_delete=models.CASCADE,
        related_name="upload_sessions",
    )
    asset = models.ForeignKey(
        "db.FileAsset",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="upload_sessions",
        verbose_name="目标文件（BR-07 同名匹配既有行，可空）",
        help_text="FK 而非 OneToOne：会话是历史账本——"
                  "completed/expired 不释放、也不阻塞新会话（BR-15）",
    )
    s3_upload_id = models.CharField(max_length=128, verbose_name="S3 multipart id")
    object_key = models.TextField(verbose_name="目标对象键")
    file_name = models.CharField(max_length=255)
    file_size = models.BigIntegerField(verbose_name="总大小")
    content_type = models.CharField(max_length=128)
    content_md5 = models.CharField(
        max_length=32, null=True, blank=True,
        verbose_name="整件 MD5",
        help_text="init 登记留痕 → complete 落 FileVersion.attributes.md5"
                  "（版本元数据对比/审计，§4.2.1）；不做秒传索引（§6.2）",
    )
    chunk_size = models.PositiveIntegerField(default=8 * 1024 * 1024)
    total_chunks = models.PositiveIntegerField()
    uploaded_chunks = models.JSONField(
        default=list,
        verbose_name="已完成片号",
        help_text='[{"n":1,"etag":"…","size":8388608}]',
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.UPLOADING,
        db_index=True,
    )

    class Meta(BaseModel.Meta):
        db_table = "upload_sessions"
        constraints = [
            # 同一文件同时至多一个 uploading 会话；历史会话（completed/expired/aborted）
            # 保留不删、不阻塞新会话——R1 版 OneToOne 会在同名二次上传时必撞唯一约束（BR-15）
            models.UniqueConstraint(
                fields=["asset"],
                condition=models.Q(asset__isnull=False, status="uploading"),
                name="uniq_active_session_per_asset",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "status"], name="idx_us_project_status"),
        ]

    def __str__(self) -> str:
        return f"{self.file_name} #{self.s3_upload_id[:8]} ({self.status})"


class FileVersion(BaseModel):
    """文件版本 —— 只增账本：上传/回滚各一行；对象只增不删（淘汰除外，FILE-003 §1.3）。

    - ``asset.storage_path`` / ``size`` / ``attributes`` 恒镜像 ``current_version``
      （行级镜像不变量，§4.3.2 注：列表页与下载换发免 JOIN）。
    - 回滚 = 新行 ``source_version`` 指向目标、复用其对象键（零拷贝，BR-09）。
    - 淘汰 = 最旧非当前版本置软删，其对象若无他引用（回滚链/双挂）才物理删（BR-08）。
    """

    asset = models.ForeignKey(
        "db.FileAsset",
        on_delete=models.CASCADE,
        related_name="versions",
        verbose_name="所属文件",
    )
    version_number = models.PositiveIntegerField(verbose_name="版本号（文件内递增）")
    object_key = models.TextField(verbose_name="对象键（版本独立或回滚复用）")
    attributes = models.JSONField(
        default=dict, verbose_name="name/size/content_type/md5",
        help_text="衍生物登记也挂本列：derivatives.{kind} = {key,status,generated_at,last_access_at}",
    )
    source_version = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="rollback_children",
        verbose_name="回滚来源",
    )
    uploaded_by = models.ForeignKey(
        "db.User",
        on_delete=models.SET_NULL,
        null=True,
        verbose_name="上传人（complete/rollback 操作者留痕，§4.3.2）",
    )

    class Meta(BaseModel.Meta):
        db_table = "file_versions"
        constraints = [
            models.UniqueConstraint(
                fields=["asset", "version_number"],
                condition=models.Q(deleted_at__isnull=True),
                name="uniq_version_per_asset",
            ),
        ]
        indexes = [
            models.Index(fields=["asset", "-created_at"], name="idx_version_asset_recent"),
        ]

    def __str__(self) -> str:
        return f"{self.asset_id} v{self.version_number}"
