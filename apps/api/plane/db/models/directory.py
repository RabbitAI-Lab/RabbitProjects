"""目录同步模型（AUTH-011 LDAP/SCIM，P4 R2）。

两条企业身份源通道（LDAP 拉取 / SCIM 2.0 推送）共用映射表与冲突裁决：
LdapDirectoryConfig 拉取配置、ScimConnector 推送令牌、DirectoryUserMapping
身份映射与复活快照、DirectorySyncRun 台账、DirectoryPendingAction 待办队列
（席位满待开通 / 邮箱变更人工裁决）。

BR 要点：单通道互斥（BR-01 偏条件唯一）、邮箱主键归并（BR-02）、双确认
缺席禁用（BR-04，absence_count）、禁用不删除（BR-05）、保护名单
（BR-06，is_sync_protected + WS_OWNER 判定）、凭证最小化（BR-08 句柄非密文）。
"""

from django.db import models

from plane.db.models.base import BaseModel


class DirectoryChannel(models.TextChoices):
    LDAP = "ldap", "LDAP/AD"
    SCIM = "scim", "SCIM 2.0"


class LdapDirectoryConfig(BaseModel):
    """LDAP 拉取通道配置；每工作空间至多一条 enabled（BR-01）。"""

    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="ldap_configs",
    )
    name = models.CharField(max_length=64)
    server_uri = models.CharField(max_length=255)  # ldaps://ad.corp:636
    bind_dn = models.CharField(max_length=255)
    bind_secret_ref = models.CharField(max_length=128)  # 密保库句柄，非密文本身（BR-08）
    base_dn = models.CharField(max_length=255)
    user_filter = models.CharField(
        max_length=512,
        default="(&(objectClass=user)(mail=*))",
    )
    attribute_map = models.JSONField(default=dict)  # {"mail": "email", ...}
    group_map = models.JSONField(default=list)  # [{"dn":..., "department":..., "role":...}]
    sync_interval_minutes = models.PositiveSmallIntegerField(default=15)
    use_starttls = models.BooleanField(default=False)
    sync_cursor = models.CharField(max_length=64, blank=True)  # uSNChanged 水位线（跨批只进不退）
    is_enabled = models.BooleanField(default=False)

    class Meta(BaseModel.Meta):
        db_table = "directory_ldap_config"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace"],
                condition=models.Q(is_enabled=True),
                name="uq_ldap_enabled_per_workspace",
            ),
        ]


class ScimConnector(BaseModel):
    """SCIM 2.0 推送通道；每工作空间至多一条 enabled（BR-01）。"""

    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="scim_connectors",
    )
    name = models.CharField(max_length=64)
    token_hash = models.CharField(max_length=64, unique=True)  # SHA-256（BR-08：不存明文）
    token_prefix = models.CharField(max_length=8)  # 展示用 scim_Ab3x
    attribute_map = models.JSONField(default=dict)
    group_map = models.JSONField(default=list)
    is_enabled = models.BooleanField(default=False)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        db_table = "directory_scim_connector"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace"],
                condition=models.Q(is_enabled=True),
                name="uq_scim_enabled_per_workspace",
            ),
        ]


class DirectoryUserMapping(BaseModel):
    """目录身份 ↔ 本地账号 的唯一映射与复活快照（BR-02/05/06/10）。"""

    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE)
    user = models.ForeignKey(
        "db.User",
        on_delete=models.CASCADE,
        related_name="directory_mappings",
    )
    channel = models.CharField(max_length=8, choices=DirectoryChannel.choices)
    external_id = models.CharField(max_length=128)  # objectGUID / SCIM externalId
    email_snapshot = models.EmailField()  # 归并时邮箱（变更检测用）
    absence_count = models.PositiveSmallIntegerField(default=0)
    disabled_at_source = models.CharField(
        max_length=16,
        blank=True,
    )  # ldap_absent / scim_inactive / manual / ""
    identity_source = models.CharField(
        max_length=16,
        default="directory",
    )  # directory / merged / sso_merge —— BR-10 来源标记（落映射表，不改 User）
    is_sync_protected = models.BooleanField(default=False)  # BR-06 保护标记
    restore_snapshot = models.JSONField(default=dict)  # {"department_id": ..., "roles": [...]}

    class Meta(BaseModel.Meta):
        db_table = "directory_user_mapping"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "channel", "external_id"],
                name="uq_directory_identity",
            ),
        ]
        indexes = [
            models.Index(fields=["workspace", "email_snapshot"], name="idx_dir_mapping_email"),
        ]


class DirectorySyncRun(BaseModel):
    """每次同步（含干跑）的台账头；明细存 detail JSONB（BR-09）。"""

    class Status(models.TextChoices):
        RUNNING = "running", "执行中"
        SUCCESS = "success", "成功"
        FAILED = "failed", "失败"
        DRY_RUN = "dry_run", "干跑"
        CONFIRMED = "confirmed", "干跑已确认"

    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE)
    channel = models.CharField(max_length=8, choices=DirectoryChannel.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RUNNING)
    is_dry_run = models.BooleanField(default=False)
    full_sync = models.BooleanField(default=False)  # 全量批=缺席判定唯一入口（BR-04）
    triggered_by = models.CharField(max_length=16, default="beat")  # beat/manual/dry_run
    counts = models.JSONField(default=dict)  # {"created":3,"updated":12,"disabled":0,"skipped":1,"failed":0}
    detail = models.JSONField(default=list)  # 逐条 {email, action, reason}
    error = models.TextField(blank=True)
    confirmed_by = models.ForeignKey(
        "db.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    expires_at = models.DateTimeField(null=True, blank=True)  # 干跑 24h 过期

    class Meta(BaseModel.Meta):
        db_table = "directory_sync_run"
        indexes = [
            models.Index(fields=["workspace", "-created_at"], name="idx_dir_run_ws_created"),
        ]


class DirectoryPendingAction(BaseModel):
    """待开通 / 人工裁决队列（BR-03 / §2.6）。

    状态机：pending → resolved / dismissed / expired（30 天或引用干跑过期）。
    同 (workspace, kind, dedup_key) 仅一条 pending（幂等复用）。
    """

    class Kind(models.TextChoices):
        PENDING_PROVISION = "pending_provision", "待开通（席位满）"
        MANUAL_REVIEW = "manual_review", "人工裁决（疑似同人）"

    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    status = models.CharField(max_length=12, default="pending")
    dedup_key = models.CharField(max_length=200)
    # 待开通 = 目录 external_id；人工裁决 = "old_email→new_email"
    payload = models.JSONField(default=dict)
    # 待开通：{external_id, email, display_name, attrs}；裁决：{old_email, new_email, mapping_id}
    source_run = models.ForeignKey(
        "DirectorySyncRun",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    resolved_by = models.ForeignKey(
        "db.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    resolved_action = models.CharField(max_length=32, blank=True)  # provision/merge/dismiss/expand_seats

    class Meta(BaseModel.Meta):
        db_table = "directory_pending_action"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "kind", "dedup_key"],
                condition=models.Q(status="pending"),
                name="uq_pending_action_dedup",
            ),
        ]
        indexes = [
            models.Index(fields=["workspace", "kind", "status"], name="idx_dir_pending_ws"),
        ]
