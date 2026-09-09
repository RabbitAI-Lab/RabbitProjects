"""SSO 单点登录模型（AUTH-009，Sprint-8 R3）。

- IdentityProvider：Workspace 级 IdP 配置（OneToOne——每空间一个启用中
  IdP）；OIDC/SAML 双协议字段并存；密钥列 Fernet 加密（BR-14 永不回显）。
- SSOAccount：用户-IdP 绑定锚（idp, subject）唯一且不可变（BR-01）。
"""
import uuid

from django.db import models


class IdentityProvider(models.Model):
    """Workspace 的身份提供方配置（§4.1 逐字实现）。"""

    PROTOCOL_OIDC, PROTOCOL_SAML = "oidc", "saml"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.OneToOneField("Workspace", on_delete=models.CASCADE,
                                     related_name="identity_provider")
    protocol = models.CharField(max_length=8,
                                choices=[(PROTOCOL_OIDC, "OIDC"),
                                         (PROTOCOL_SAML, "SAML")])
    is_enabled = models.BooleanField(default=False)
    # OIDC
    issuer = models.URLField(blank=True)
    client_id = models.CharField(max_length=200, blank=True)
    client_secret_enc = models.BinaryField(null=True)      # Fernet 加密，永不回显
    jwks_url = models.URLField(blank=True)                 # 发现元数据解析缓存
    # SAML
    idp_entity_id = models.CharField(max_length=300, blank=True)
    idp_sso_url = models.URLField(blank=True)
    idp_slo_url = models.URLField(blank=True)
    idp_x509_cert = models.TextField(blank=True)
    sp_entity_id = models.CharField(max_length=300, blank=True)
    sp_private_key_enc = models.BinaryField(null=True)
    sp_x509_cert = models.TextField(blank=True)
    # 映射与策略
    claim_department = models.CharField(max_length=64, blank=True, default="department")
    claim_role = models.CharField(max_length=64, blank=True, default="groups")
    jit_default_role = models.CharField(max_length=20, default="WS_MEMBER")
    sync_profile_on_login = models.BooleanField(default=True)
    enforce_sso = models.BooleanField(default=False)
    last_test_passed_at = models.DateTimeField(null=True)
    created_by = models.ForeignKey("User", on_delete=models.PROTECT,
                                   related_name="created_idps")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "identity_provider"


class SSOAccount(models.Model):
    """用户-IdP 绑定锚：subject（sub/NameID）首次绑定不可变。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    idp = models.ForeignKey(IdentityProvider, on_delete=models.CASCADE,
                            related_name="accounts")
    user = models.ForeignKey("User", on_delete=models.CASCADE,
                             related_name="sso_accounts")
    subject = models.CharField(max_length=255)
    email_at_binding = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sso_account"
        constraints = [
            models.UniqueConstraint("idp", "subject", name="uq_sso_subject"),
        ]
        indexes = [models.Index("user", name="idx_sso_account_user")]
