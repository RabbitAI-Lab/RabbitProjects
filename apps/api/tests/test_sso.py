"""SSO 单点登录测试（AUTH-009，Sprint-8 R3 门禁）。

OIDC 全链路以自造 RSA 密钥对 + 自签 id_token 模拟 IdP（monkeypatch
discovery/exchange_token——HTTP 边界不打真网）；SAML 覆盖 settings 严格
构建、SP 元数据与无效断言拒绝（真 Keycloak 断言消费随 R6 验收联调）。
覆盖：BR-01 唯一锚 / BR-02 认领验密 / BR-04 干跑门槛 / BR-05 防自锁 /
BR-06 逃生名单 / BR-07 状态校验 / BR-09 部门映射 / BR-11 解绑 / 强制门
状态码裁定（§2.4）。
"""

from __future__ import annotations

import uuid

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from rest_framework.test import APIClient

from plane.db.models import (
    Department,
    IdentityProvider,
    SSOAccount,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)
from plane.sso import oidc

pytestmark = pytest.mark.django_db


# ── 密钥与令牌工厂 ────────────────────────────────────────
_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk() -> dict:
    import json as _json

    from jwt.algorithms import RSAAlgorithm

    return _json.loads(RSAAlgorithm.to_jwk(_RSA_KEY.public_key()))


def _make_id_token(claims: dict) -> str:
    return pyjwt.encode(claims, _RSA_KEY, algorithm="RS256", headers={"kid": "test-kid"})


_FAKE_META = {
    "authorization_endpoint": "https://idp.test/authorize",
    "token_endpoint": "https://idp.test/token",
    "jwks_uri": "https://idp.test/jwks",
}


@pytest.fixture()
def idp_env(db, monkeypatch, settings):
    """OIDC IdP + mock discovery/JWKS/exchange。"""
    # JWKS 缓存指向本地伪造（不打网）
    monkeypatch.setattr(oidc, "discovery", lambda issuer: dict(_FAKE_META))
    monkeypatch.setattr(oidc, "fetch_jwks", lambda url, force=False: {"keys": [{**_jwk(), "kid": "test-kid"}]})
    owner = User.objects.create_user(email="sso-owner@rabbit.dev", password="Rabbit123!", display_name="主")
    ws = Workspace.objects.create(name="S", slug=f"w-sso-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=owner, role=WorkspaceRole.OWNER, created_by=owner)
    idp = IdentityProvider.objects.create(
        workspace=ws,
        protocol=IdentityProvider.PROTOCOL_OIDC,
        is_enabled=True,
        issuer="https://idp.test",
        client_id="rp-client",
        jwks_url="https://idp.test/jwks",
        created_by=owner,
    )
    settings.SSO_FERNET_KEY = ""  # 本测不用 secret
    return {"owner": owner, "ws": ws, "idp": idp}


def _claims(env, sub="user-1", email="sso-user@rabbit.dev", name="SSO 用户", extra=None):
    c = {
        "iss": env["idp"].issuer,
        "aud": env["idp"].client_id,
        "sub": sub,
        "email": email,
        "name": name,
        "exp": 9999999999,
        "iat": 1700000000,
    }
    if extra:
        c.update(extra)
    return c


def _begin(client: APIClient, env) -> tuple[str, str]:
    """走真实 sign-in 端点拿 302 与 sso_txn Cookie；state 从 Location 提取。"""
    from urllib.parse import parse_qs, urlparse

    r = client.get(f"/api/v1/auth/sso/{env['ws'].slug}/sign-in/", {"next": "/x/"})
    assert r.status_code == 302, getattr(r, "content", "")
    q = parse_qs(urlparse(r.url).query)
    return r.cookies[oidc.SSO_TXN_COOKIE].value, q["state"][0], q["nonce"][0]


def _complete(client: APIClient, env, claims: dict, txn: tuple | None):
    """模拟 callback（monkeypatch token 交换返回自签 id_token）。"""
    if txn:
        claims.setdefault("nonce", txn[2])  # txn nonce 注入（防恒 mismatch）
    token = {"id_token": _make_id_token(claims), "access_token": "at"}
    import plane.sso.oidc as oidc_mod

    orig = oidc_mod.exchange_token
    oidc_mod.exchange_token = lambda *a, **k: token
    try:
        if txn:
            client.cookies[oidc.SSO_TXN_COOKIE] = txn[0]
            state = txn[1]
        else:
            state = "whatever"
        return client.get("/api/v1/auth/sso/callback/", {"code": "c", "state": state})
    finally:
        oidc_mod.exchange_token = orig


# ── 模型 / 门 ──────────────────────────────────────────────
class TestModelAndGate:
    def test_br01_unique_anchor(self, idp_env):
        u = User.objects.create_user(email="dup@rabbit.dev", password="x")
        SSOAccount.objects.create(idp=idp_env["idp"], user=u, subject="s1", email_at_binding="dup@rabbit.dev")
        u2 = User.objects.create_user(email="dup2@rabbit.dev", password="x")
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            SSOAccount.objects.create(idp=idp_env["idp"], user=u2, subject="s1", email_at_binding="dup2@rabbit.dev")

    def test_gate_order_wrong_password_401(self, idp_env, settings):
        """§2.4 UT-20 口径：密码错误一律 401，不经强制 SSO 分支。"""
        idp_env["idp"].enforce_sso = True
        idp_env["idp"].save(update_fields=["enforce_sso"])
        c = APIClient()
        r = c.post("/api/v1/auth/sign-in/", {"email": "sso-owner@rabbit.dev", "password": "wrong"}, format="json")
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"

    def test_gate_correct_password_enforced_403(self, idp_env):
        idp_env["idp"].enforce_sso = True
        idp_env["idp"].save(update_fields=["enforce_sso"])
        c = APIClient()
        r = c.post("/api/v1/auth/sign-in/", {"email": "sso-owner@rabbit.dev", "password": "Rabbit123!"}, format="json")
        assert r.status_code == 403
        body = r.json()["error"]
        assert body["code"] == "PERM_SSO_REQUIRED"
        assert any(d.get("code") == "SSO_LOGIN_URL" for d in body["details"])

    def test_br06_break_glass(self, idp_env, settings):
        idp_env["idp"].enforce_sso = True
        idp_env["idp"].save(update_fields=["enforce_sso"])
        settings.SSO_BREAK_GLASS_EMAILS = ["sso-owner@rabbit.dev"]
        c = APIClient()
        r = c.post("/api/v1/auth/sign-in/", {"email": "sso-owner@rabbit.dev", "password": "Rabbit123!"}, format="json")
        assert r.status_code == 200  # 逃生名单放行

    def test_route_endpoint(self, idp_env):
        c = APIClient()
        r1 = c.post("/api/v1/auth/sso/route/", {"email": "sso-owner@rabbit.dev"}, format="json")
        assert r1.status_code == 200 and r1.json()["data"]["mode"] == "password"
        idp_env["idp"].enforce_sso = True
        idp_env["idp"].save(update_fields=["enforce_sso"])
        r2 = c.post("/api/v1/auth/sso/route/", {"email": "sso-owner@rabbit.dev"}, format="json")
        assert r2.json()["data"]["mode"] == "sso"
        assert "sign-in" in r2.json()["data"]["sso_login_url"]


# ── OIDC 全链路 ───────────────────────────────────────────
class TestOIDCFlow:
    def test_jit_provision_and_login(self, idp_env):
        c = APIClient()
        txn = _begin(c, idp_env)
        r = _complete(c, idp_env, _claims(idp_env), txn)
        assert r.status_code == 302, getattr(r, "content", b"")
        assert r.url.endswith("/x/")
        user = User.objects.get(email="sso-user@rabbit.dev")
        assert not user.has_usable_password()  # JIT 无密码
        assert SSOAccount.objects.filter(idp=idp_env["idp"], subject="user-1").exists()
        assert WorkspaceMember.objects.filter(
            workspace=idp_env["ws"], member=user, role=WorkspaceRole.MEMBER
        ).exists()  # BR-08 默认角色
        assert c.session.get("_auth_user_id") == str(user.id)  # 会话建立

    def test_state_mismatch_401(self, idp_env):
        c = APIClient()
        c.cookies[oidc.SSO_TXN_COOKIE] = _forge_txn({"state": "bad"})
        r = _complete(c, idp_env, _claims(idp_env), None)
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"

    def test_no_cookie_401(self, idp_env):
        c = APIClient()
        r = c.get("/api/v1/auth/sso/callback/", {"code": "c", "state": "s"})
        assert r.status_code == 401

    def test_nonce_mismatch_401(self, idp_env):
        c = APIClient()
        txn = _begin(c, idp_env)
        claims = _claims(idp_env, extra={"nonce": "wrong"})
        r = _complete(c, idp_env, claims, txn)
        assert r.status_code == 401

    def test_claim_flow(self, idp_env):
        # 邮箱已存在且设过密码 → 认领页
        User.objects.create_user(email="local@rabbit.dev", password="Local123!", display_name="本地")
        c = APIClient()
        txn = _begin(c, idp_env)
        r = _complete(c, idp_env, _claims(idp_env, sub="s-claim", email="local@rabbit.dev"), txn)
        assert r.status_code == 302 and "/sso/claim" in r.url
        # pending_claim txn 仍在（重写）
        assert "sso_txn" in r.cookies
        # 密码错 → 401（BR-02）
        r2 = c.post("/api/v1/auth/sso/claim/", {"password": "bad"}, format="json")
        assert r2.status_code == 401
        # 密码对 → 绑定 + 会话
        r3 = c.post("/api/v1/auth/sso/claim/", {"password": "Local123!"}, format="json")
        assert r3.status_code == 200
        assert SSOAccount.objects.filter(subject="s-claim", user__email="local@rabbit.dev").exists()
        # 重放：txn 已消费 → SSO_TXN_INVALID
        r4 = c.post("/api/v1/auth/sso/claim/", {"password": "Local123!"}, format="json")
        assert r4.status_code == 401
        assert r4.json()["error"]["code"] == "SSO_TXN_INVALID"

    def test_sync_profile_department(self, idp_env):
        dept = Department.objects.create(
            workspace=idp_env["ws"], name="平台组", path=f"/{uuid.uuid4()}/", created_by=idp_env["owner"]
        )
        # 首次登录（JIT）带部门
        c = APIClient()
        txn = _begin(c, idp_env)
        r = _complete(c, idp_env, _claims(idp_env, extra={"department": "平台组"}), txn)
        assert r.status_code == 302
        wm = WorkspaceMember.objects.get(workspace=idp_env["ws"], member__email="sso-user@rabbit.dev")
        assert wm.department_id == dept.id  # BR-09 名称精确匹配

    def test_sync_profile_unmapped_kept(self, idp_env):
        c = APIClient()
        txn = _begin(c, idp_env)
        _complete(c, idp_env, _claims(idp_env, extra={"department": "不存在的部门"}), txn)
        wm = WorkspaceMember.objects.get(workspace=idp_env["ws"], member__email="sso-user@rabbit.dev")
        assert wm.department_id is None  # 无匹配保持现值（BR-09）

    def test_signature_rejected(self, idp_env):
        """BR-07：验签失败（错密钥签发）→ 401。"""
        bad_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        claims = _claims(idp_env)
        token = pyjwt.encode(claims, bad_key, algorithm="RS256", headers={"kid": "test-kid"})
        c = APIClient()
        txn = _begin(c, idp_env)
        import plane.sso.oidc as oidc_mod

        orig = oidc_mod.exchange_token
        oidc_mod.exchange_token = lambda *a, **k: {"id_token": token}
        try:
            c.cookies[oidc.SSO_TXN_COOKIE] = txn[0]
            r = c.get("/api/v1/auth/sso/callback/", {"code": "c", "state": txn[1]})
        finally:
            oidc_mod.exchange_token = orig
        assert r.status_code == 401


def _forge_txn(payload: dict) -> str:
    from django.core import signing

    return signing.dumps(payload)


# ── 管理面 ────────────────────────────────────────────────
class TestAdminEndpoints:
    def _owner_client(self, env):
        c = APIClient()
        c.force_authenticate(user=env["owner"])
        return c

    def test_config_secret_not_echoed(self, idp_env, settings):
        settings.SSO_FERNET_KEY = _test_fernet_key()
        c = self._owner_client(idp_env)
        r = c.patch(f"/api/v1/workspaces/{idp_env['ws'].slug}/sso/", {"client_secret": "topsecret"}, format="json")
        assert r.status_code == 200, r.json()
        data = r.json()["data"]
        assert data["secret_set"] is True
        assert "topsecret" not in str(r.content)  # BR-14 永不回显
        idp_env["idp"].refresh_from_db()
        assert b"topsecret" not in bytes(idp_env["idp"].client_secret_enc)

    def test_patch_rejects_enforce_field(self, idp_env):
        c = self._owner_client(idp_env)
        r = c.patch(f"/api/v1/workspaces/{idp_env['ws'].slug}/sso/", {"enforce_sso": True}, format="json")
        assert r.status_code == 400

    def test_br04_enable_requires_test_pass(self, idp_env):
        c = self._owner_client(idp_env)
        idp_env["idp"].is_enabled = False
        idp_env["idp"].save(update_fields=["is_enabled"])
        r = c.patch(f"/api/v1/workspaces/{idp_env['ws'].slug}/sso/", {"is_enabled": True}, format="json")
        assert r.status_code == 409
        assert any(d.get("code") == "TEST_REQUIRED" for d in r.json()["error"]["details"])

    def test_br05_enforce_requires_owner_binding(self, idp_env):
        c = self._owner_client(idp_env)
        idp_env["idp"].last_test_passed_at = __import__("django.utils.timezone", fromlist=["timezone"]).now()
        idp_env["idp"].save(update_fields=["last_test_passed_at"])
        r = c.post(f"/api/v1/workspaces/{idp_env['ws'].slug}/sso/enforce/")
        assert r.status_code == 409
        assert any(d.get("code") == "OWNER_BINDING_REQUIRED" for d in r.json()["error"]["details"])
        # OWNER 完成 SSO 绑定后可开
        SSOAccount.objects.create(
            idp=idp_env["idp"], user=idp_env["owner"], subject="owner-sub", email_at_binding="sso-owner@rabbit.dev"
        )
        r2 = c.post(f"/api/v1/workspaces/{idp_env['ws'].slug}/sso/enforce/")
        assert r2.status_code == 200
        idp_env["idp"].refresh_from_db()
        assert idp_env["idp"].enforce_sso is True
        # 幂等
        assert c.post(f"/api/v1/workspaces/{idp_env['ws'].slug}/sso/enforce/").status_code == 200
        # DELETE 关
        assert c.delete(f"/api/v1/workspaces/{idp_env['ws'].slug}/sso/enforce/").status_code == 204

    def test_permission_owner_only(self, idp_env):
        member = User.objects.create_user(email="sso-mem@rabbit.dev", password="x")
        WorkspaceMember.objects.create(
            workspace=idp_env["ws"], member=member, role=WorkspaceRole.ADMIN, created_by=idp_env["owner"]
        )
        c = APIClient()
        c.force_authenticate(user=member)
        r = c.get(f"/api/v1/workspaces/{idp_env['ws'].slug}/sso/")
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "PERM_WORKSPACE_OWNER_REQUIRED"


# ── 本人绑定 ──────────────────────────────────────────────
class TestMyBindings:
    def test_list_and_unbind(self, idp_env):
        u = User.objects.create_user(email="bind@rabbit.dev", password="Bind123!")
        b = SSOAccount.objects.create(idp=idp_env["idp"], user=u, subject="b1", email_at_binding="bind@rabbit.dev")
        c = APIClient()
        c.force_authenticate(user=u)
        r = c.get("/api/v1/users/me/sso/bindings/")
        assert r.status_code == 200 and len(r.json()["data"]) == 1
        assert c.delete(f"/api/v1/users/me/sso/bindings/{b.id}/").status_code == 204

    def test_br11_unbind_requires_password(self, idp_env):
        u = User.objects.create_user(email="nopw@rabbit.dev", password=None)  # type: ignore[arg-type]
        b = SSOAccount.objects.create(idp=idp_env["idp"], user=u, subject="b2", email_at_binding="nopw@rabbit.dev")
        c = APIClient()
        c.force_authenticate(user=u)
        r = c.delete(f"/api/v1/users/me/sso/bindings/{b.id}/")
        assert r.status_code == 400
        assert any(d.get("code") == "REQUIRED" for d in r.json()["error"]["details"])


# ── SAML ──────────────────────────────────────────────────
class TestSAML:
    @pytest.fixture()
    def saml_env(self, idp_env):
        idp_env["idp"].protocol = IdentityProvider.PROTOCOL_SAML
        idp_env["idp"].issuer = ""
        idp_env["idp"].idp_entity_id = "https://idp.test/saml/metadata"
        idp_env["idp"].idp_sso_url = "https://idp.test/saml/sso"
        idp_env["idp"].idp_x509_cert = _self_signed_cert()
        idp_env["idp"].save()
        return idp_env

    def test_settings_strict_buildable(self, saml_env):
        from plane.sso.saml_flow import _build_auth

        auth = _build_auth({"get_data": {}, "post_data": {}}, saml_env["idp"])
        assert auth is not None

    def test_sp_metadata_xml(self, saml_env):
        c = APIClient()
        r = c.get(f"/api/v1/auth/sso/{saml_env['ws'].slug}/metadata/")
        assert r.status_code == 200
        assert b"EntityDescriptor" in r.content

    def test_acs_invalid_assertion_401(self, saml_env):
        import base64

        c = APIClient()
        r = c.post("/api/v1/auth/sso/saml/acs/", {"SAMLResponse": base64.b64encode(b"<not-saml/>").decode()})
        assert r.status_code == 401


# ── 工具 ──────────────────────────────────────────────────
def _test_fernet_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def _self_signed_cert() -> str:
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.x509.oid import NameOID

    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "idp.test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(_RSA_KEY.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2020, 1, 1))
        .not_valid_after(datetime.datetime(2040, 1, 1))
        .sign(_RSA_KEY, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()
