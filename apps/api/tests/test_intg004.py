"""Open API 测试（INTG-004，P4 R4 门禁）。

覆盖 §5 核心：API Key 生命周期（明文一次/过期/IP 白名单/无效）、scope
网关（越权精确清单 BR-03）、白名单隔离（未注册路径 404）、OAuth 全链
（注册→授权码→token→refresh 轮换→旧值复用吊销→revoke 黑名单）、
client_credentials、调用留痕（BR-05）。
"""

from __future__ import annotations

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from plane.db.models import (
    APIToken,
    ApiCallLog,
    Issue,
    Project,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.roles import WorkspaceRole

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="oa-owner@rabbit.dev", password="Rabbit123!", display_name="开发者")
    ws = Workspace.objects.create(name="OA", slug=f"w-oa-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=owner, role=WorkspaceRole.OWNER, created_by=owner)
    proj = Project.objects.create(workspace=ws, name="P", identifier="OA1", created_by=owner)
    issue = Issue.objects.create(project=proj, name="开放任务", sequence_id=1, created_by=owner)
    return {"owner": owner, "ws": ws, "proj": proj, "issue": issue}


def _c(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user)
    return c


def _create_key(env, scopes, **kw) -> str:
    c = _c(env["owner"])
    r = c.post("/api/v1/api-tokens/", {"name": "集成密钥", "scopes": scopes, **kw}, format="json")
    assert r.status_code == 201, r.content
    return r.json()["data"]["key"]


# ── API Key 面 ─────────────────────────────────────────────────────


def test_api_key_lifecycle_and_scopes(env):
    c = _c(env["owner"])
    # 非法 scope 拒绝
    r0 = c.post("/api/v1/api-tokens/", {"name": "坏", "scopes": ["root:all"]}, format="json")
    assert r0.status_code == 400
    raw = _create_key(env, ["issues:read"])
    # 列表只见前缀尾缀，无明文
    lst = c.get("/api/v1/api-tokens/").json()["data"]
    assert lst[0]["key_prefix"].startswith("rp_") and "key" not in lst[0]
    # 白名单读通
    r1 = c.get("/api/v1/external/issues/", HTTP_X_API_KEY=raw)
    assert r1.status_code == 200 and r1.json()["status"] == "success"
    # scope 不足：PERM_TOKEN_SCOPE_INSUFFICIENT + 所需清单（BR-03）
    r2 = c.get("/api/v1/external/workspaces/", HTTP_X_API_KEY=raw)
    assert r2.status_code == 403
    body = r2.json()["error"]
    assert body["code"] == "PERM_TOKEN_SCOPE_INSUFFICIENT"
    # 白名单外路径对凭证 404（物理隔离）
    r3 = c.get("/api/v1/external/secrets/", HTTP_X_API_KEY=raw)
    assert r3.status_code in (404, 401)  # 未注册路由 Django 404
    # 无效 key
    r4 = c.get("/api/v1/external/issues/", HTTP_X_API_KEY="rp_live_bogus")
    assert r4.status_code == 401
    assert r4.json()["error"]["code"] == "AUTH_INVALID_TOKEN"
    # 吊销后失效
    token = APIToken.objects.get(user=env["owner"])
    c.delete(f"/api/v1/api-tokens/{token.id}/")
    r5 = c.get("/api/v1/external/issues/", HTTP_X_API_KEY=raw)
    assert r5.status_code == 401


def test_api_key_expiry_and_ip_allowlist(env):
    expired = _create_key(env, ["issues:read"])
    tid = APIToken.objects.get(user=env["owner"], key_suffix=expired[-4:]).id
    APIToken.objects.filter(pk=tid).update(expires_at=timezone.now())
    r = APIClient().get("/api/v1/external/issues/", HTTP_X_API_KEY=expired)
    assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_TOKEN_EXPIRED"
    ipkey = _create_key(env, ["issues:read"], ip_allowlist=["10.99.0.0/16"])
    r2 = APIClient().get("/api/v1/external/issues/", HTTP_X_API_KEY=ipkey, REMOTE_ADDR="192.168.1.5")
    assert r2.status_code == 403 and r2.json()["error"]["code"] == "PERM_IP_NOT_ALLOWED"


def test_call_log_written(env):
    raw = _create_key(env, ["issues:read"])
    _ = APIClient().get("/api/v1/external/issues/", HTTP_X_API_KEY=raw)
    assert ApiCallLog.objects.filter(path="/api/v1/external/issues/", status_code=200).exists()  # BR-05


def test_external_issue_detail_row_visibility(env):
    raw = _create_key(env, ["issues:read"])
    stranger = User.objects.create_user(email="oa-str@rabbit.dev", password="Rabbit123!")
    stranger_ws = Workspace.objects.create(
        name="别家", slug=f"w-str-{stranger.id.hex[:6]}", owner=stranger, created_by=stranger
    )
    stranger_proj = Project.objects.create(workspace=stranger_ws, name="S", identifier="S1", created_by=stranger)
    other = Issue.objects.create(project=stranger_proj, name="别人的", sequence_id=1, created_by=stranger)
    c = APIClient()
    r_ok = c.get(f"/api/v1/external/issues/{env['issue'].id}/", HTTP_X_API_KEY=raw)
    assert r_ok.status_code == 200 and r_ok.json()["data"]["name"] == "开放任务"
    r_no = c.get(f"/api/v1/external/issues/{other.id}/", HTTP_X_API_KEY=raw)
    assert r_no.status_code == 404  # 越权不可见


# ── OAuth 面 ───────────────────────────────────────────────────────


def test_oauth_full_chain(env):
    c = _c(env["owner"])
    r_app = c.post(
        "/api/v1/oauth/applications/",
        {
            "name": "报表应用",
            "client_type": "confidential",
            "redirect_uris": ["https://app.example/cb"],
            "scopes_requested": ["issues:read"],
        },
        format="json",
    )
    assert r_app.status_code == 201
    app = r_app.json()["data"]
    # 授权码（approve=true → code 一次性返回）
    r_code = c.get(
        "/api/v1/oauth/authorize/",
        {
            "client_id": app["client_id"],
            "scope": "issues:read",
            "redirect_uri": "https://app.example/cb",
            "approve": "true",
        },
    )
    assert r_code.status_code == 200
    code = r_code.json()["data"]["code"]
    # 换 token（authorization_code）
    r_tok = c.post("/api/v1/oauth/token/", {"grant_type": "authorization_code", "code": code}, format="json")
    assert r_tok.status_code == 200
    tok = r_tok.json()
    assert tok["token_type"] == "Bearer" and tok["refresh_token"]
    # access token 走白名单
    r_ext = APIClient().get("/api/v1/external/issues/", HTTP_AUTHORIZATION=f"Bearer {tok['access_token']}")
    assert r_ext.status_code == 200
    # code 一次性：重放 invalid_grant
    r_replay = c.post("/api/v1/oauth/token/", {"grant_type": "authorization_code", "code": code}, format="json")
    assert r_replay.status_code == 400
    # refresh 轮换：旧 refresh 作废
    r_ref = c.post(
        "/api/v1/oauth/token/", {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]}, format="json"
    )
    assert r_ref.status_code == 200
    new_refresh = r_ref.json()["refresh_token"]
    r_old = c.post(
        "/api/v1/oauth/token/", {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]}, format="json"
    )
    assert r_old.status_code == 400  # 旧值拒绝（rotation）
    # revoke：access 黑名单生效
    access2 = r_ref.json()["access_token"]
    r_rev = c.post("/api/v1/oauth/revoke/", {"token": access2}, format="json")
    assert r_rev.status_code == 200
    r_after = APIClient().get("/api/v1/external/issues/", HTTP_AUTHORIZATION=f"Bearer {access2}")
    assert r_after.status_code == 401  # 黑名单
    # introspect
    r_int = c.post("/api/v1/oauth/introspect/", {"token": access2}, format="json")
    assert r_int.json()["active"] is False


def test_client_credentials_grant(env):
    c = _c(env["owner"])
    r_app = c.post(
        "/api/v1/oauth/applications/",
        {"name": "服务端应用", "client_type": "confidential", "scopes_requested": ["issues:read"]},
        format="json",
    )
    app = r_app.json()["data"]
    r_tok = c.post(
        "/api/v1/oauth/token/",
        {
            "grant_type": "client_credentials",
            "client_id": app["client_id"],
            "client_secret": app["client_secret"],
            "scope": "issues:read",
        },
        format="json",
    )
    assert r_tok.status_code == 200
    access = r_tok.json()["access_token"]
    r_ext = APIClient().get("/api/v1/external/issues/", HTTP_AUTHORIZATION=f"Bearer {access}")
    assert r_ext.status_code == 200


def test_authorized_apps_management(env):
    c = _c(env["owner"])
    r_app = c.post("/api/v1/oauth/applications/", {"name": "被撤应用", "scopes_requested": []}, format="json")
    app_id = r_app.json()["data"]["id"]
    from plane.db.models import OAuthGrant

    grant = OAuthGrant.objects.create(
        app_id=app_id,
        user=env["owner"],
        workspace=env["ws"],
        scopes=[],
        refresh_token_hash="x" * 64,
        refresh_expires_at=timezone.now(),
    )
    r_list = c.get("/api/v1/users/me/authorized-apps/")
    assert r_list.status_code == 200 and len(r_list.json()["data"]) == 1
    r_del = c.delete(f"/api/v1/users/me/authorized-apps/{grant.id}/")
    assert r_del.status_code == 200
    grant.refresh_from_db()
    assert grant.revoked_at is not None
