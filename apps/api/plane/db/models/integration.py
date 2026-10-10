"""集成模型（INTG-001 §4.1——Sprint-5 T5-04）。

- IntegrationInstallation：GitHub App 安装 + 项目×仓库绑定（≤5/项目，条件唯一）；
  webhook_secret 落库为 Fernet 密文（HMAC 验签须用原文，Service 层 decrypt）。
- SyncConflictLog：双向同步「后写胜出」冲突日志（BR-07——败方快照，管理页可见）。

Issue.external_*（幂等锚）与 Issue.github_context（PR/Commit 内联聚合）随
0017 迁移点亮（unified-issue-model §7.1 预留落地）。
"""

from __future__ import annotations

import uuid

from django.db import models

from plane.db.models.base import BaseModel


class IntegrationInstallation(BaseModel):
    """GitHub App 安装与项目×仓库绑定（对标 Plane GITHUB_CONFIGURATION 收敛）。"""

    class Provider(models.TextChoices):
        GITHUB = "github", "GitHub"
        # INTG-003（P4 R5）§4.1 演进①：Slack/Zoom 通道
        SLACK = "slack", "Slack"
        ZOOM = "zoom", "Zoom"

    class SyncStatus(models.TextChoices):
        SYNCING = "syncing", "同步中"
        PAUSED = "paused", "已暂停"  # 速率耗尽 / 人工暂停
        STALE = "stale", "仓库失效"  # GitHub 404 / installation 删除
        UNBOUND = "unbound", "已解绑"

    provider = models.CharField(
        max_length=16, choices=Provider.choices, default=Provider.GITHUB, verbose_name="集成提供方"
    )
    # INTG-003（P4 R5）§4.1 演进②⑤：installation_id 与 GitHub 三列可空化
    # （Slack workspace 级 / Zoom 实例级行无合法值）；GitHub 侧唯一约束经
    # 迁移改条件约束补偿（condition=provider="github"，INTG-001 待回改登记）
    installation_id = models.BigIntegerField(
        db_index=True, null=True, blank=True, verbose_name="GitHub installation id"
    )
    project = models.ForeignKey(
        "db.Project", on_delete=models.CASCADE, null=True, blank=True, related_name="integrations", verbose_name="项目"
    )
    repository_full_name = models.CharField(
        max_length=200, blank=True, default="", verbose_name="仓库全名", help_text="owner/repo，小写（GitHub 行专用）"
    )
    repository_node_id = models.CharField(max_length=64, blank=True, default="", verbose_name="仓库 node_id")
    # ── INTG-003（P4 R5）演进③：Slack/Zoom 专用可空列（GitHub 行恒 NULL）──
    workspace = models.ForeignKey(
        "db.Workspace", null=True, blank=True, on_delete=models.CASCADE, related_name="integrations"
    )
    team_id = models.CharField(max_length=32, blank=True, default="", verbose_name="Slack team id")
    team_name = models.CharField(max_length=128, blank=True, default="")
    bot_token_ref = models.CharField(max_length=128, blank=True, default="", verbose_name="密保库 bot token 句柄")
    bot_user_id = models.CharField(max_length=32, blank=True, default="")
    credentials_ref = models.CharField(max_length=128, blank=True, default="")
    unbound_at = models.DateTimeField(null=True, blank=True)
    webhook_secret = models.TextField(verbose_name="Webhook 验签密钥（Fernet 密文；HMAC 用 Service 层解密原文）")
    default_issue_type = models.ForeignKey(
        "db.IssueType", null=True, blank=True, on_delete=models.SET_NULL, verbose_name="入站建任务默认类型"
    )
    sync_status = models.CharField(
        max_length=16, choices=SyncStatus.choices, default=SyncStatus.SYNCING, db_index=True, verbose_name="同步状态"
    )
    last_synced_at = models.DateTimeField(null=True, blank=True, verbose_name="最近同步心跳")
    token_cache = models.JSONField(default=dict, blank=True, verbose_name="installation token 缓存")

    class Meta(BaseModel.Meta):
        db_table = "integration_installations"
        verbose_name = "集成安装绑定"
        constraints = [
            # INTG-003（P4 R5）§4.1 演进⑤：GitHub 侧条件约束补偿（与软删条件
            # 叠加）——Slack/Zoom 行 project/repo 无值不进约束辖域
            models.UniqueConstraint(
                fields=["project", "repository_full_name"],
                condition=models.Q(deleted_at__isnull=True, provider="github"),
                name="uniq_binding_project_repo_active",
            ),
            models.CheckConstraint(
                condition=~models.Q(repository_full_name="") | ~models.Q(provider="github"), name="chk_repo_name"
            ),
            # Slack team ↔ 工作空间一对一（BR-01；偏条件唯一）
            models.UniqueConstraint(
                fields=["team_id"],
                condition=models.Q(team_id__gt="", provider="slack", deleted_at__isnull=True),
                name="uq_slack_team_ws",
            ),
        ]
        indexes = [
            models.Index(fields=["installation_id", "repository_full_name"], name="idx_install_repo"),
        ]

    def __str__(self) -> str:
        return f"{self.provider}:{self.repository_full_name}@{self.project_id}"


class SyncConflictLog(BaseModel):
    """双向同步冲突日志（BR-07）：败方快照 + 胜方参照，人工决策，不自动合并。"""

    class Scope(models.TextChoices):
        TITLE = "title", "标题"
        DESCRIPTION = "description", "描述"
        STATE = "state", "状态"
        COMMENT = "comment", "评论"

    class Direction(models.TextChoices):
        INBOUND = "inbound", "入站"
        OUTBOUND = "outbound", "出站"

    binding = models.ForeignKey(
        "db.IntegrationInstallation", on_delete=models.CASCADE, related_name="conflict_logs", verbose_name="绑定"
    )
    issue = models.ForeignKey(
        "db.Issue", on_delete=models.CASCADE, related_name="github_conflict_logs", verbose_name="任务"
    )
    scope = models.CharField(max_length=16, choices=Scope.choices, db_index=True, verbose_name="冲突面")
    direction = models.CharField(max_length=16, choices=Direction.choices, db_index=True, verbose_name="方向")
    winner_side = models.CharField(
        max_length=8, choices=[("github", "GitHub"), ("system", "系统")], verbose_name="时间戳裁决胜出方"
    )
    winner_payload = models.JSONField(verbose_name="胜方字段值")
    loser_payload = models.JSONField(verbose_name="败方字段值（覆盖前快照）")
    delivery_id = models.CharField(max_length=64, blank=True, default="", verbose_name="X-GitHub-Delivery 溯源")
    occurred_at = models.DateTimeField(db_index=True, verbose_name="冲突时刻")

    class Meta(BaseModel.Meta):
        db_table = "integration_sync_conflict_logs"
        verbose_name = "同步冲突日志"
        indexes = [
            models.Index(fields=["binding", "-occurred_at"], name="idx_conflict_binding_time"),
            models.Index(fields=["issue", "-occurred_at"], name="idx_conflict_issue_time"),
        ]
        ordering = ("-occurred_at",)


def new_integration_secret() -> str:
    """绑定用 webhook secret 原文（展示一次给管理员；落库走 encrypt_secret）。"""
    return uuid.uuid4().hex + uuid.uuid4().hex


def encrypt_secret(plaintext: str) -> str:
    """Fernet 对称加密（密钥 = settings.INTEGRATION_SECRET_KEY，缺省从
    SECRET_KEY 派生——dev/CI 可零配置启动，生产注入独立密钥）。"""
    import base64
    import hashlib

    from cryptography.fernet import Fernet
    from django.conf import settings as dj_settings

    key = getattr(dj_settings, "INTEGRATION_SECRET_KEY", None)
    if not key:
        key = base64.urlsafe_b64encode(hashlib.sha256(dj_settings.SECRET_KEY.encode()).digest()).decode()
    return Fernet(key if isinstance(key, str) else key).encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    import base64
    import hashlib

    from cryptography.fernet import Fernet, InvalidToken
    from django.conf import settings as dj_settings

    key = getattr(dj_settings, "INTEGRATION_SECRET_KEY", None)
    if not key:
        key = base64.urlsafe_b64encode(hashlib.sha256(dj_settings.SECRET_KEY.encode()).digest()).decode()
    try:
        return Fernet(key).decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        return ""  # 密钥轮换残留——验签必然失败，由调用方 403 收口
