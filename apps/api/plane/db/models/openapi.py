"""Open API 模型（INTG-004，P4 R4）。

API Key（APIToken——sha256 哈希 + rp_<env>_ 前缀 + scope 数组 + IP 白名单）、
OAuth 2.0 应用/授权（机密与公共客户端、refresh 轮换哈希）、调用日志
（30 天保留——分区表为部署期动作，模型侧 managed 语义同 audit_log 先例）。
"""

from django.db import models

from plane.db.models.base import BaseModel


class APIToken(BaseModel):
    """API Key（X-API-Key 头；哈希落库，明文仅创建时返回一次）。"""

    user = models.ForeignKey("db.User", on_delete=models.CASCADE, related_name="api_tokens")
    name = models.CharField(max_length=64)
    key_hash = models.CharField(max_length=64, unique=True)
    key_prefix = models.CharField(max_length=16)  # 如 rp_live_（展示段）
    key_suffix = models.CharField(max_length=8, default="")  # 尾 4 位展示
    scopes = models.JSONField(default=list)  # ["issues:read", ...]
    ip_allowlist = models.JSONField(default=list)  # CIDR 列表（BR-11）
    expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        db_table = "openapi_api_token"
        indexes = [models.Index(fields=["user", "is_active"])]

    def __str__(self) -> str:
        return f"{self.key_prefix}…{self.key_suffix}({self.name})"


class OAuthApplication(BaseModel):
    """OAuth 2.0 应用（client_credentials/authorization_code）。"""

    class ClientType(models.TextChoices):
        CONFIDENTIAL = "confidential", "机密（服务端应用）"
        PUBLIC = "public", "公共（SPA/CLI）"

    owner = models.ForeignKey("db.User", on_delete=models.CASCADE, related_name="oauth_applications")
    name = models.CharField(max_length=64)
    client_id = models.CharField(max_length=32, unique=True)  # rp_app_…
    client_secret_hash = models.CharField(max_length=64, null=True, blank=True)
    redirect_uris = models.JSONField(default=list)
    scopes_requested = models.JSONField(default=list)
    client_type = models.CharField(max_length=12, choices=ClientType.choices, default=ClientType.CONFIDENTIAL)
    review_status = models.CharField(
        max_length=10,
        choices=[("self", "自用"), ("pending", "审核中"), ("approved", "已上架"), ("rejected", "已拒绝")],
        default="self",
    )  # BR-09 应用市场
    icon_asset = models.ForeignKey("db.FileAsset", null=True, on_delete=models.SET_NULL)

    class Meta(BaseModel.Meta):
        db_table = "openapi_oauth_app"


class OAuthGrant(BaseModel):
    """授权记录（用户×应用×工作空间；refresh 轮换仅存哈希）。"""

    app = models.ForeignKey(OAuthApplication, on_delete=models.CASCADE, related_name="grants")
    user = models.ForeignKey("db.User", on_delete=models.CASCADE, related_name="oauth_grants")
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE)
    scopes = models.JSONField(default=list)
    refresh_token_hash = models.CharField(max_length=64, unique=True)
    refresh_expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        db_table = "openapi_oauth_grant"
        constraints = [
            models.UniqueConstraint(fields=["app", "user", "workspace"], name="uq_oauth_grant"),
        ]


class OAuthCode(BaseModel):
    """授权码（10 分钟一次性；PKCE 本轮不做——公共客户端走 client_id 校验）。"""

    app = models.ForeignKey(OAuthApplication, on_delete=models.CASCADE)
    user = models.ForeignKey("db.User", on_delete=models.CASCADE)
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE)
    code_hash = models.CharField(max_length=64, unique=True)
    scopes = models.JSONField(default=list)
    redirect_uri = models.CharField(max_length=512, blank=True)
    expires_at = models.DateTimeField()

    class Meta(BaseModel.Meta):
        db_table = "openapi_oauth_code"


class ApiCallLog(models.Model):
    """外部凭证调用留痕（BR-05；30 天保留——清理归运维 beat，分区为部署期）。"""

    id = models.BigAutoField(primary_key=True)
    credential_type = models.CharField(max_length=8)  # key/token
    credential_id = models.UUIDField()
    user = models.ForeignKey("db.User", null=True, on_delete=models.SET_NULL)
    workspace = models.ForeignKey("db.Workspace", null=True, on_delete=models.SET_NULL)
    method = models.CharField(max_length=8)
    path = models.CharField(max_length=255)
    status_code = models.PositiveSmallIntegerField()
    latency_ms = models.PositiveIntegerField()
    ip = models.GenericIPAddressField(null=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "openapi_call_log"
        indexes = [models.Index(fields=["credential_id", "-created_at"], name="idx_calllog_cred")]
