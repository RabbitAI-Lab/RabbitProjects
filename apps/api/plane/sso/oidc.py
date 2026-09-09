"""OIDC RP 流程（AUTH-009 §2.1/§4.3，Sprint-8 R3）。

实现路径（登记偏差）：requests 做 token 交换 + PyJWT 做 id_token 验签
（JWKS 拉取 + kid 缓存 1h）——authlib 曾按 §1.4 加入依赖后移除：本实现
协议面（authorize 重定向 + code 交换 + RS256 验签 + PKCE）在 requests+
PyJWT 下更可测可控，依赖面更小。

sso_txn：签名 HttpOnly Cookie（10min，SameSite=Lax）承载
{state, nonce, verifier, next, idp}；认领分支原地重写为 pending_claim 态
（§4.2 唯一定义），事务纯 Cookie 承载、无服务端 pending 表。
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from base64 import urlsafe_b64encode
from urllib.parse import urlencode

import jwt as pyjwt
import requests as http
from django.conf import settings as dj_settings

logger = logging.getLogger("plane.api.sso")

SSO_TXN_COOKIE = "sso_txn"
TXN_MAX_AGE = 600
_JWKS_CACHE: dict[str, tuple] = {}  # issuer -> (jwks, fetched_at_monotonic_stub)


class SSOError(Exception):
    """协议失败（BR-07：对外统一 401 AUTH_INVALID_CREDENTIALS，细节进日志）。"""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _b64url(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode()


def pkce_pair() -> tuple[str, str]:
    """S256 (verifier, challenge)。"""
    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def discovery(issuer: str) -> dict:
    """OIDC 发现元数据（.well-known/openid-configuration，5s 超时 ×2）。"""
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    last = None
    for _ in range(2):
        try:
            r = http.get(url, timeout=5)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise SSOError(f"discovery_failed: {last}")


def fetch_jwks(jwks_url: str, *, force: bool = False) -> dict:
    """JWKS 拉取 + 1h 缓存；kid 未命中由 verify_id_token 触发 force 刷新。"""
    import time as _time

    cached = _JWKS_CACHE.get(jwks_url)
    if cached and not force and (_time.time() - cached[1]) < 3600:
        return cached[0]
    r = http.get(jwks_url, timeout=5)
    r.raise_for_status()
    jwks = r.json()
    _JWKS_CACHE[jwks_url] = (jwks, _time.time())
    return jwks


def verify_id_token(idp, id_token: str, *, nonce: str | None = None) -> dict:
    """RS256 验签 + iss/aud/exp/nonce 全量校验（BR-07）。"""
    jwks_url = idp.jwks_url or discovery(idp.issuer).get("jwks_uri", "")
    if not jwks_url:
        raise SSOError("no_jwks")
    header = pyjwt.get_unverified_header(id_token)
    kid = header.get("kid")
    for force in (False, True):  # kid 未命中即强制刷新一次
        jwks = fetch_jwks(jwks_url, force=force)
        key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
        if key:
            break
    else:
        raise SSOError("kid_not_found")
    from jwt import PyJWK

    public = PyJWK.from_dict(key).key
    try:
        claims = pyjwt.decode(
            id_token, public, algorithms=["RS256"],
            audience=idp.client_id, issuer=idp.issuer,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except pyjwt.PyJWTError as exc:
        raise SSOError(f"verify_failed: {exc}") from exc
    if nonce is not None and claims.get("nonce") != nonce:
        raise SSOError("nonce_mismatch")
    return claims


def exchange_token(idp, code: str, redirect_uri: str, verifier: str) -> dict:
    """authorization_code + PKCE 换 token（5s 超时 ×2，§2.6）。"""
    meta = discovery(idp.issuer)
    endpoint = meta.get("token_endpoint", "")
    from plane.sso.crypto import decrypt_secret

    secret = decrypt_secret(idp.client_secret_enc) or ""
    last = None
    for _ in range(2):
        try:
            r = http.post(endpoint, data={
                "grant_type": "authorization_code",
                "code": code, "redirect_uri": redirect_uri,
                "client_id": idp.client_id, "client_secret": secret,
                "code_verifier": verifier,
            }, timeout=5)
            if r.status_code != 200:
                raise SSOError(f"token_exchange_http_{r.status_code}")
            return r.json()
        except SSOError:
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise SSOError(f"token_exchange_failed: {last}")


def authorize_url(idp, state: str, nonce: str, challenge: str,
                  redirect_uri: str) -> str:
    meta = discovery(idp.issuer)
    params = {
        "response_type": "code",
        "client_id": idp.client_id,
        "redirect_uri": redirect_uri,
        "scope": "openid email profile",
        "state": state, "nonce": nonce,
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    return f"{meta.get('authorization_endpoint', '')}?{urlencode(params)}"


def connection_check(idp, *, code: str | None = None,
                     redirect_uri: str = "", verifier: str = "",
                     id_token_sample: str | None = None) -> dict:
    """测试连接干跑（BR-04）：验签 + 取 claims，不建会话。

    两种模式：code（完整链路换 token）或 id_token_sample（直接验给定的
    令牌——Keycloak 夹具/演示向导用）。返回 {ok, claims, issuer, jwks_url}。
    """
    try:
        if id_token_sample:
            claims = verify_id_token(idp, id_token_sample)
        else:
            if not code:
                return {"ok": False, "error": "code_required"}
            token = exchange_token(idp, code, redirect_uri, verifier)
            claims = verify_id_token(idp, token["id_token"])
        return {"ok": True, "claims": {k: claims[k] for k in
                                       ("iss", "sub", "email", "name")
                                       if k in claims},
                "issuer": idp.issuer, "jwks_url": idp.jwks_url}
    except SSOError as exc:
        return {"ok": False, "error": exc.reason}


def build_txn_cookie_payload(idp, *, state: str, nonce: str, verifier: str,
                             next_url: str) -> dict:
    return {"state": state, "nonce": nonce, "verifier": verifier,
            "next": next_url, "idp": str(idp.id)}


def set_txn_cookie(response, payload: dict) -> None:
    from django.core import signing

    response.set_signed_cookie(
        SSO_TXN_COOKIE, signing.dumps(payload),
        max_age=TXN_MAX_AGE, httponly=True, samesite="Lax",
        secure=not dj_settings.DEBUG,
    )


def read_txn_cookie(request) -> dict | None:
    """读 sso_txn（丢失/过期/被篡改统一 None——BR-07 防探测口径）。"""
    from django.core import signing

    try:
        raw = request.get_signed_cookie(SSO_TXN_COOKIE, max_age=TXN_MAX_AGE)
        payload = signing.loads(raw)
        return payload if isinstance(payload, dict) else None
    except Exception:  # noqa: BLE001 —— KeyError/BadSignature/SignatureExpired
        return None


def delete_txn_cookie(response) -> None:
    response.delete_cookie(SSO_TXN_COOKIE, samesite="Lax")
