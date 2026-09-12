"""FileAsset —— 全系统唯一文件通道（FILE-001 §4.1）。

P1 挂载点：issue（任务附件）、avatar（AUTH-004 头像）、comment_image（COLLAB-002）。
P2+ 通过 entity_type 注册制扩展，零 DDL；FILE-002 注册 ``project_file``
（文件库，entity_id = FileFolder.id），并按 FILE-002 §1.7 第 1 行登记新增
``folder`` / ``issue`` 两个可空外键作为多态列之上的冗余读列（双挂反查与目录树联查）。
"""

from django.db import models

from plane.db.models.base import BaseModel
from plane.db.models.file import FileFolder


class FileAsset(BaseModel):
    """文件资产 —— 对标 Plane FileAsset，参见 `docs/sprint-1-mvp/FILE-001-task-attachment.md` §4.1。

    FILE-001 §1.4 两条协议锁定原样遵守：
      ① 存储键 {workspace_id}/{project_id}/{entity_type}/{entity_id}/{ulid}.{ext}；
      ② 状态机五态（uploading/uploaded/abandoned + deleted_at 软删 + 硬删 purged 终态）。
    """

    class Status(models.TextChoices):
        UPLOADING = "uploading", "直传中"
        UPLOADED = "uploaded", "已上传"
        ABANDONED = "abandoned", "已弃置"

    class EntityType(models.TextChoices):
        """注册制（FILE-001 §2.4 BR-12）：新增宿主须在 §1.4 矩阵登记并经架构评审。"""

        ISSUE = "issue", "任务"
        AVATAR = "avatar", "头像"
        # COLLAB-002 评论图片挂载点（FILE-001 §1.4 注册位）：entity_id 落当前 issue，
        # 不占单任务 20 附件配额、不入附件区列表。
        COMMENT_IMAGE = "comment_image", "评论图片"
        # FILE-002 §1.4 注册位：文件库文件——entity_id = FileFolder.id（§1.2 双重身份表）。
        PROJECT_FILE = "project_file", "项目文件库"

    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="assets",
        null=True,  # 头像域无工作空间归属（AUTH-004 §4.2.2；0020 收口——此前服务层
        # 写 None 而列 NOT NULL，avatar presign 必 IntegrityError，冒烟 S17 暴露）
        blank=True,
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
    entity_type = models.CharField(max_length=32, choices=EntityType.choices, verbose_name="宿主类型")
    entity_id = models.UUIDField(verbose_name="宿主实体 ID")

    attributes = models.JSONField(
        default=dict,
        verbose_name="原始属性",
        help_text='{"name":"error-500.png","size":2097152,"mime":"image/png","ext":".png"}',
    )
    size = models.BigIntegerField(default=0, verbose_name="字节数", db_index=True)
    # FILE-006（P4 R6）：DLP 命中快照与 purge 标记（迁移 AddField 承载）
    dlp_hits = models.JSONField(default=list, blank=True, verbose_name="DLP 命中规则名")
    purged_at = models.DateTimeField(null=True, blank=True, verbose_name="合规 purge 标记时间")
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

    # ── FILE-002 §4.1.1 扩展（§1.7 第 1 行：P2 迭代内演进，冗余读外键）────
    folder = models.ForeignKey(
        FileFolder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="files",
        verbose_name="所属目录（文件库身份，= entity_id）",
        help_text="entity_type=project_file 时与 entity_id 同值同步（写入侧保证）",
    )
    issue = models.ForeignKey(
        "db.Issue",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dual_mount_files",
        verbose_name="双挂任务（第二视图入口，可空）",
    )
    visibility = models.CharField(
        max_length=16,
        choices=FileFolder.Visibility.choices,
        default=FileFolder.Visibility.ALL,
        verbose_name="可见性（默认随目录，可独立收紧）",
    )
    allowed_members = models.JSONField(
        default=list,
        blank=True,
        verbose_name="指定可见成员（UUID 列表，members 态生效）",
    )
    download_count = models.PositiveIntegerField(default=0, verbose_name="下载次数")

    # ── FILE-003 §4.1.1/§4.1.2：当前版本指针（真 FK，迁移 0010 AlterField 升级）──
    # T4-03 曾以 UUID 预留列落库（current_version），FILE-003 建 FileVersion 表后
    # AlterField 为 FK 仅改列名 + 追加引用约束。原 ``upload_session`` OneToOne 预留
    # 列撤销不建（§4.1.2 注 2）：会话↔文件关系由 UploadSession.asset FK 承载，
    # OneToOne 不释放会撞同名二次上传（BR-15）。
    # 行级镜像不变量（§4.3.2 注）：storage_path/size/attributes 恒等于
    # current_version.object_key/attributes["size"]/attributes —— _new_version 单点维护。
    current_version = models.ForeignKey(
        "db.FileVersion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="asset_current",
        verbose_name="当前版本指针（FILE-003）",
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
            # ── FILE-002 §4.1.1 ──
            # 目录文件列表取数：WHERE project=? AND folder=? AND deleted_at IS NULL
            models.Index(fields=["project", "folder"], name="idx_asset_project_folder"),
            # 双挂反查：任务附件区列表 = entity_type=issue 行 ∪ issue 外键非空行（§1.2）
            models.Index(fields=["issue"], name="idx_asset_issue"),
            # 名称/上传人/时间筛选取数（GIN trgm 名称模糊索引在 §4.1.3 迁移 RunSQL 落表）
            models.Index(
                fields=["project", "uploaded_by", "created_at"],
                name="idx_asset_project_uploader",
            ),
        ]
        # 白名单双层防御：DB CheckConstraint 由 §0003 迁移通过 RunSQL 以原生 PG `~*` 落表
        # （Django ORM 的 __regex 仅桥接 PG `~`，不区分大小写在原生层表达）。
