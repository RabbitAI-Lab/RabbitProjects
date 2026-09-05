"""账户扩展模型 —— AUTH-004 §4.1 个人资料修改与密码重置相关表。

`PasswordResetToken` 一次性、短时效、存哈希不存明文。
"""
import hashlib
import secrets
from datetime import timedelta

from django.db import models
from django.utils import timezone

from plane.db.models.base import BaseModel


class PasswordResetToken(BaseModel):
    """密码重置令牌 —— 一次性、短时效、存哈希不存明文。

    设计要点（BR-06 / BR-09 / BR-12）：
    - 明文 token 仅存在于邮件链接与内存，落库前 SHA-256；
    - token_hash 全局 unique；
    - 一次性由「消费时 UPDATE used_at WHERE used_at IS NULL」的行锁语义保证；
    - 每用户任意时刻至多一枚活令牌（签发前作废旧令牌）。
    """

    TOKEN_BYTES = 64  # 64B → token_urlsafe 输出 86 字符
    TTL_MINUTES = 30  # BR-06

    user = models.ForeignKey(
        "db.User",
        on_delete=models.CASCADE,
        related_name="reset_tokens",
        verbose_name="所属用户",
    )
    token_hash = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        verbose_name="SHA-256(令牌)",
    )
    expires_at = models.DateTimeField(verbose_name="过期时间")
    used_at = models.DateTimeField(null=True, blank=True, verbose_name="使用时间")
    requested_ip = models.GenericIPAddressField(
        null=True, blank=True, verbose_name="申请来源 IP"
    )

    class Meta(BaseModel.Meta):
        db_table = "password_reset_tokens"
        verbose_name = "密码重置令牌"
        ordering = ("-created_at",)
        constraints = [
            # 活令牌每用户至多一枚的数据库级兜底（Service 层 BR-09 为主判定）
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(used_at__isnull=True),
                name="uniq_one_live_reset_token_per_user",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "expires_at"], name="idx_prt_user_exp"),
        ]

    @classmethod
    def issue(cls, *, user, ip: str | None = None) -> str:
        """签发一枚新令牌并返回明文（仅此一处产生明文）。

        必须在 transaction.atomic() 内调用：作废旧令牌 + 写入新令牌为同一事务（BR-09/BR-12）。
        并发场景下第二个签发事务会被 uniq_one_live_reset_token_per_user 拦截并重试。
        """
        cls.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
        token = secrets.token_urlsafe(cls.TOKEN_BYTES)
        cls.objects.create(
            user=user,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=timezone.now() + timedelta(minutes=cls.TTL_MINUTES),
            requested_ip=ip,
            created_by=user,
            updated_by=user,
        )
        return token
