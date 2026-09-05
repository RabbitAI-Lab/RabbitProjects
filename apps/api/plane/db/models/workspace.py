import hashlib
import secrets

from django.db import models
from django.utils import timezone

from plane.db.models.base import BaseModel
from plane.db.models.roles import WorkspaceRole


class WorkspaceMemberInvite(BaseModel):
    """工作空间邀请 —— token 制，对标 Plane WorkspaceMemberInvite（TEAM-002 §4.1.1）。

    安全设计：
    - token 明文仅出现在邮件链接与创建响应的 meta（降级模式）中，
      库中只存 SHA-256 哈希（token_hash），库泄露不可反推可用邀请；
    - 邮箱归一小写存储；
    - 同邮箱同空间同时只允许一条 pending（偏条件唯一约束），
      「重发邀请」语义 = 顺延既有 pending 的 expires_at 并重发邮件。
    """

    class Status(models.TextChoices):
        PENDING = "pending", "待接受"
        ACCEPTED = "accepted", "已接受"
        REVOKED = "revoked", "已撤销"
        EXPIRED = "expired", "已过期"

    INVITE_TTL_DAYS = 7

    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="invites",
        verbose_name="目标工作空间",
    )
    email = models.EmailField(verbose_name="被邀邮箱（归一小写）")
    role = models.IntegerField(
        choices=WorkspaceRole.choices,
        default=WorkspaceRole.MEMBER,
        verbose_name="预设角色",
        help_text="仅允许 MEMBER(10) / ADMIN(15)，Service 层校验（BR-02）",
    )
    token_hash = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        verbose_name="SHA-256(token)",
        help_text="接受端点按 hash 检索，token 明文不落库",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        verbose_name="状态",
    )
    expires_at = models.DateTimeField(verbose_name="过期时间")
    accepted_at = models.DateTimeField(null=True, blank=True, verbose_name="接受时间")
    accepted_by = models.ForeignKey(
        "db.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="accepted_workspace_invites",
        verbose_name="接受人",
    )
    invited_by = models.ForeignKey(
        "db.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="workspace_invites",
        verbose_name="邀请人",
    )

    class Meta(BaseModel.Meta):
        db_table = "workspace_member_invites"
        verbose_name = "工作空间邀请"
        ordering = ("-created_at",)
        constraints = [
            # 同邮箱同空间同时只允许一条 pending（BR-13）
            models.UniqueConstraint(
                fields=["workspace", "email"],
                condition=models.Q(status="pending", deleted_at__isnull=True),
                name="uniq_pending_invite_per_email",
            ),
        ]
        indexes = [
            models.Index(fields=["workspace", "status"], name="idx_invite_ws_status"),
            models.Index(fields=["status", "expires_at"], name="idx_invite_status_expiry"),
        ]

    @classmethod
    def issue_token(cls) -> tuple[str, str]:
        """生成 (token 明文, SHA-256 哈希)。明文只在调用栈内存与邮件/降级回显中存在。"""
        token = secrets.token_urlsafe(32)
        return token, hashlib.sha256(token.encode()).hexdigest()

    @property
    def is_consumable(self) -> bool:
        """有效性实时判定（不依赖 beat：过期在读取时即可判死）。"""
        return self.status == self.Status.PENDING and self.expires_at > timezone.now()


class Workspace(BaseModel):
    name = models.CharField(max_length=255, verbose_name="名称")
    slug = models.SlugField(max_length=48, db_index=True, verbose_name="URL 标识")
    description = models.TextField(blank=True, verbose_name="描述")
    logo = models.URLField(max_length=800, blank=True, null=True, verbose_name="Logo 地址")
    owner = models.ForeignKey(
        "db.User", on_delete=models.CASCADE, related_name="owner_workspaces", verbose_name="所有者"
    )

    class Meta(BaseModel.Meta):
        db_table = "workspaces"
        verbose_name = "工作空间"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["slug"], condition=models.Q(deleted_at__isnull=True), name="uniq_workspace_slug_alive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.slug})"


class WorkspaceMember(BaseModel):
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="workspace_member")
    member = models.ForeignKey("db.User", on_delete=models.CASCADE, related_name="member_workspace")
    role = models.IntegerField(choices=WorkspaceRole.choices, default=WorkspaceRole.MEMBER)
    is_active = models.BooleanField(default=True)
    company_role = models.TextField(null=True, blank=True, verbose_name="公司内职务")

    class Meta(BaseModel.Meta):
        db_table = "workspace_members"
        verbose_name = "工作空间成员"
        unique_together = ("workspace", "member")
        indexes = [
            models.Index(fields=["member", "workspace", "role"]),
            models.Index(fields=["workspace", "role"]),
        ]
