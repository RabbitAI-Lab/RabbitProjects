"""文件分享链接模型（FILE-004 §4.1.1）。

``FileShareLink`` 是文件体系唯一暴露给匿名互联网的资源——「链接即能力」：
slug（22 位 base64url，128bit 熵）+（密码）= 声明权限，不与账号体系绑定。
``FileShareAccess`` 匿名留痕（IP/UA/动作，不留身份，BR-09）。
"""
import secrets

from django.db import models

from plane.db.models.base import BaseModel


def generate_share_slug() -> str:
    """BR-02：16 字节熵源 → 恰 22 字符 base64url（``[A-Za-z0-9_-]``）。"""
    return secrets.token_urlsafe(16)


class FileShareLink(BaseModel):
    """文件分享链接 —— 链接即能力：slug + （密码）= 声明权限"""

    class Permission(models.TextChoices):
        VIEW = "view", "仅预览"
        DOWNLOAD = "download", "预览+下载"

    class Status(models.TextChoices):
        ACTIVE = "active", "有效"
        REVOKED = "revoked", "已吊销"
        EXPIRED = "expired", "已过期"
        INVALIDATED = "invalidated", "源失效"

    asset = models.ForeignKey(
        "db.FileAsset",
        on_delete=models.CASCADE,
        related_name="share_links",
        verbose_name="分享文件",
    )
    slug = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        default=generate_share_slug,
        verbose_name="分享标识",
    )
    permission = models.CharField(
        max_length=16,
        choices=Permission.choices,
        default=Permission.DOWNLOAD,
        verbose_name="权限",
    )
    password_hash = models.CharField(
        max_length=128,
        blank=True,
        verbose_name="密码哈希（Argon2id，空=无密码）",
    )
    expires_at = models.DateTimeField(
        null=True, blank=True, verbose_name="过期时间（空=永久）"
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    created_by = models.ForeignKey(
        "db.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="file_share_links",
    )
    access_count = models.PositiveIntegerField(
        default=0, verbose_name="成功访问次数"
    )  # 仅计成功 view/download（BR-09）

    class Meta(BaseModel.Meta):
        db_table = "file_share_links"
        indexes = [
            models.Index(fields=["asset", "status"], name="idx_share_asset_status"),
            models.Index(
                fields=["expires_at"],
                name="idx_share_expires",
                condition=models.Q(status="active"),
            ),
        ]

    def __str__(self) -> str:
        return f"/s/{self.slug}({self.status})"


class FileShareAccess(BaseModel):
    """分享访问留痕 —— 只记 IP/UA/动作，不留身份（BR-09）"""

    class Action(models.TextChoices):
        UNLOCK = "unlock", "解锁"
        VIEW = "view", "预览"
        DOWNLOAD = "download", "下载"
        UNLOCK_FAILED = "unlock_failed", "密码错误"

    share = models.ForeignKey(
        FileShareLink, on_delete=models.CASCADE, related_name="accesses"
    )
    action = models.CharField(max_length=16, choices=Action.choices)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=256, blank=True)
    success = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        db_table = "file_share_accesses"
        indexes = [models.Index(fields=["share", "created_at"], name="idx_shareacc_time")]

    def __str__(self) -> str:
        return f"{self.share_id}:{self.action}"
