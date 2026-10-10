"""文件合规模型（FILE-006，P4 R6）。

CompliancePolicy 单表多态四级（workspace/project/folder/asset——偏条件
唯一一域一策）、LegalHold 法务保留（双人确认）、DlpRule 轻量内容识别。
FileAsset 增列 dlp_hits/purged_at 经本文迁移承载。
"""

from django.db import models

from plane.db.models.base import BaseModel


class CompliancePolicy(BaseModel):
    """四级策略单表多态；scope 四选一非空（CHECK）+ 每域偏条件唯一。"""

    workspace = models.ForeignKey("db.Workspace", null=True, on_delete=models.CASCADE)
    project = models.ForeignKey("db.Project", null=True, on_delete=models.CASCADE)
    folder = models.ForeignKey("db.FileFolder", null=True, on_delete=models.CASCADE)
    asset = models.ForeignKey("db.FileAsset", null=True, on_delete=models.CASCADE)
    watermark = models.CharField(max_length=24, null=True)
    # off / preview_only / preview_and_download
    download = models.CharField(max_length=16, null=True)
    # allow / deny / desensitized
    share_link = models.CharField(max_length=20, null=True)  # allow/deny/password_required
    retention_days = models.PositiveIntegerField(null=True)
    dark_watermark = models.BooleanField(null=True)  # 仅旗舰档（BR-10）
    version = models.PositiveIntegerField(default=0)  # 乐观锁（§4.8）

    class Meta(BaseModel.Meta):
        db_table = "file_compliance_policy"
        constraints = [
            models.CheckConstraint(
                check=(  # type: ignore[call-arg]  # Django 5.1 现行签名（stubs 误报，全仓同款）
                    models.Q(workspace__isnull=False, project__isnull=True, folder__isnull=True, asset__isnull=True)
                    | models.Q(project__isnull=False, folder__isnull=True, asset__isnull=True)
                    | models.Q(folder__isnull=False, asset__isnull=True)
                    | models.Q(asset__isnull=False)
                ),
                name="ck_policy_exactly_one_scope",
            ),
            # 一域一策：四级偏条件唯一（PG NULL 互异——condition 收窄非空域；
            # 软删行让位可重建；恢复继承 = 软删该层行）
            models.UniqueConstraint(
                fields=["workspace"],
                condition=models.Q(workspace__isnull=False, deleted_at__isnull=True),
                name="uq_policy_workspace",
            ),
            models.UniqueConstraint(
                fields=["project"],
                condition=models.Q(project__isnull=False, deleted_at__isnull=True),
                name="uq_policy_project",
            ),
            models.UniqueConstraint(
                fields=["folder"],
                condition=models.Q(folder__isnull=False, deleted_at__isnull=True),
                name="uq_policy_folder",
            ),
            models.UniqueConstraint(
                fields=["asset"],
                condition=models.Q(asset__isnull=False, deleted_at__isnull=True),
                name="uq_policy_asset",
            ),
        ]
        indexes = [
            models.Index(fields=["workspace", "project", "folder"], name="idx_policy_ws_overrides"),
        ]


class LegalHold(BaseModel):
    """法务保留（双人 BR-07；与留存清理对冲——sweeper 跳过持有行）。"""

    asset = models.ForeignKey("db.FileAsset", on_delete=models.CASCADE, related_name="legal_holds")
    reason = models.CharField(max_length=255)
    case_ref = models.CharField(max_length=64, blank=True)  # 案件号
    placed_by = models.ForeignKey("db.User", related_name="+", on_delete=models.PROTECT)
    confirmed_by = models.ForeignKey("db.User", related_name="+", on_delete=models.PROTECT)  # 双人
    released_at = models.DateTimeField(null=True, blank=True)
    released_confirmed_by = models.ForeignKey(
        "db.User", null=True, blank=True, related_name="+", on_delete=models.PROTECT
    )  # 解除第二确认

    class Meta(BaseModel.Meta):
        db_table = "file_legal_hold"
        constraints = [
            models.UniqueConstraint(
                fields=["asset"], condition=models.Q(released_at__isnull=True), name="uq_legal_hold_active"
            ),
        ]


class DlpRule(BaseModel):
    """工作空间 DLP 规则（≤20 条；regex 编译验证 + 回溯检测在 Service 层）。"""

    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="dlp_rules")
    name = models.CharField(max_length=64)
    pattern = models.CharField(max_length=512)
    is_enabled = models.BooleanField(default=True)
    created_by = models.ForeignKey("db.User", null=True, on_delete=models.PROTECT, related_name="+")

    class Meta(BaseModel.Meta):
        db_table = "file_dlp_rule"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"], condition=models.Q(deleted_at__isnull=True), name="uq_dlp_rule_ws_name"
            ),
        ]
        indexes = [
            models.Index(fields=["workspace", "is_enabled"], name="idx_dlp_rule_ws_enabled"),
        ]
