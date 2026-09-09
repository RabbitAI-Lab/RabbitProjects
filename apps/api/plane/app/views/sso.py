"""SSO 域视图（AUTH-009 §4.2，Sprint-8 R3）—— 15 端点。

管理面（6）：配置读 / 部分更新 / 测试连接干跑 / 强制开关（POST 开 DELETE
关）/ 绑定清单——权限 workspace.sso.manage（WS_OWNER，BR-03：403
PERM_WORKSPACE_OWNER_REQUIRED）。
认证流（7）：route / OIDC sign-in+callback / claim / SAML sign-in+acs+metadata
——公开（登录档限流）。
本人绑定（2）：列表 + 解绑（BR-11）。
"""
from __future__ import annotations

import logging
from functools import wraps

from django.contrib.auth import login
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticatedAndActive
from plane.app.views._access import get_workspace_or_404
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.bgtasks.audit import record_audit
from plane.db.models import IdentityProvider, SSOAccount
from plane.db.models.roles import WorkspaceRole
from plane.sso import oidc, saml_flow
from plane.sso.crypto import encrypt_secret
from plane.sso.gate import find_enforced_idp_for_email
from plane.sso.provision import ClaimRequired, resolve_user

logger = logging.getLogger("plane.api.sso")


def _sso_manage(view_method):
    """WS_OWNER 门槛（BR-03：403 PERM_WORKSPACE_OWNER_REQUIRED）。"""

    @wraps(view_method)
    def wrapper(self, request, *args, **kwargs):
        from plane.app.permissions import _resolve_workspace_role

        actual = _resolve_workspace_role(request, self)
        if actual is None or actual < WorkspaceRole.OWNER:
            raise AppException("PERM_WORKSPACE_OWNER_REQUIRED",
                               message="需要工作空间所有者权限")
        return view_method(self, request, *args, **kwargs)

    return wrapper


def _get_idp(ws) -> IdentityProvider:
    idp = getattr(ws, "identity_provider", None)
    if idp is None:
        raise NotFound("RESOURCE_NOT_FOUND")
    return idp


def _audit(event: str, *, actor_id, object_id=None, **extra) -> None:
    from django.db import transaction

    transaction.on_commit(
        lambda: record_audit.delay(event, actor_id=str(actor_id),
                                   object_id=str(object_id) if object_id else None,
                                   **extra))


# ── 管理面：配置 ──────────────────────────────────────────

_IDP_READ_FIELDS = (
    "id", "protocol", "is_enabled", "issuer", "client_id", "jwks_url",
    "idp_entity_id", "idp_sso_url", "idp_slo_url", "sp_entity_id",
    "claim_department", "claim_role", "jit_default_role",
    "sync_profile_on_login", "enforce_sso", "last_test_passed_at",
    "created_at",
)


def _idp_payload(idp: IdentityProvider) -> dict:
    data = {f: getattr(idp, f) for f in _IDP_READ_FIELDS}
    data["secret_set"] = bool(idp.client_secret_enc)
    data["sp_key_set"] = bool(idp.sp_private_key_enc)
    data["cert_expires_in_days"] = _cert_expiry_days(idp)
    return data


def _cert_expiry_days(idp) -> int | None:
    """IdP 证书剩余天数（<14 天前端横幅，§2.6）。"""
    cert = (idp.idp_x509_cert or "").strip()
    if not cert:
        return None
    try:
        from cryptography import x509
        from django.utils import timezone
        parsed = x509.load_pem_x509_certificate(cert.encode())
        delta = parsed.not_valid_after_utc - timezone.now()
        return delta.days if delta.days >= 0 else 0
    except Exception:  # noqa: BLE001 —— 非 PEM（DER 等）不展示
        return None


class SSOConfigView(APIView):
    """GET / PATCH .../workspaces/{slug}/sso/（PATCH 白名单不含 enforce_sso）。"""

    permission_classes = [IsAuthenticatedAndActive]

    @_sso_manage
    def get(self, request, slug):
        ws, _ = get_workspace_or_404(slug, request.user)
        return success_response(_idp_payload(_get_idp(ws)))

    _PATCHABLE = {
        "protocol", "is_enabled", "issuer", "client_id", "client_secret",
        "jwks_url", "idp_entity_id", "idp_sso_url", "idp_slo_url",
        "idp_x509_cert", "sp_entity_id", "sp_private_key", "sp_x509_cert",
        "claim_department", "claim_role", "jit_default_role",
        "sync_profile_on_login",
    }

    @_sso_manage
    def patch(self, request, slug):
        ws, _ = get_workspace_or_404(slug, request.user)
        idp = _get_idp(ws)
        unknown = set(request.data) - self._PATCHABLE
        if unknown:
            raise AppException(
                "VALIDATION_ERROR",
                message=f"不可写字段：{sorted(unknown)}",
                details=[{"field": f, "code": "INVALID",
                          "message": "不在可更新字段集（enforce_sso 走动作端点）"}
                         for f in sorted(unknown)],
            )
        # 启用前置（BR-04）：干跑必须已通过
        enabling = request.data.get("is_enabled")
        if enabling is True and not idp.last_test_passed_at:
            raise AppException(
                "RESOURCE_STATE_INVALID",
                message="启用前必须通过测试连接干跑",
                details=[{"field": "is_enabled", "code": "TEST_REQUIRED",
                          "message": "先调用 connection-check 通过后再启用"}],
            )
        for field, value in request.data.items():
            if field == "client_secret":
                idp.client_secret_enc = encrypt_secret(value) if value else None
            elif field == "sp_private_key":
                idp.sp_private_key_enc = encrypt_secret(value) if value else None
            else:
                setattr(idp, field, value)
        idp.updated_by_id = request.user.id if hasattr(idp, "updated_by_id") else None
        idp.save()
        _audit("sso.config_updated", actor_id=request.user.id, object_id=idp.id)
        return success_response(_idp_payload(idp))


class SSOConnectionCheckView(APIView):
    """POST .../sso/connection-check/ —— 干跑（同步返回；BR-04 硬门槛）。"""

    permission_classes = [IsAuthenticatedAndActive]

    @_sso_manage
    def post(self, request, slug):
        ws, _ = get_workspace_or_404(slug, request.user)
        idp = _get_idp(ws)
        if idp.protocol == IdentityProvider.PROTOCOL_OIDC:
            result = oidc.connection_check(
                idp,
                code=request.data.get("code"),
                redirect_uri=request.data.get("redirect_uri", ""),
                verifier=request.data.get("code_verifier", ""),
                id_token_sample=request.data.get("id_token"),
            )
        else:
            result = saml_connection_check(idp, request)
        if result.get("ok"):
            from django.utils import timezone
            idp.last_test_passed_at = timezone.now()
            idp.save(update_fields=["last_test_passed_at", "updated_at"])
        _audit("sso.connection_check", actor_id=request.user.id, object_id=idp.id,
               ok=result.get("ok"))
        return success_response(result)


def saml_connection_check(idp, request) -> dict:
    """SAML 干跑：验证给定 SAMLResponse（向导演示流）或仅验配置/证书可解析。"""
    saml_response = request.data.get("saml_response")
    if saml_response:
        try:
            claims = saml_flow.consume_assertion(
                {"post_data": {"SAMLResponse": saml_response,
                               "RelayState": ""}}, idp)
            return {"ok": True, "claims": claims, "initiated_by":
                    claims.get("initiated_by")}
        except saml_flow.SAMLError as exc:
            return {"ok": False, "error": exc.reason}
    # 无断言样本：配置级校验（settings 严格模式可构建 + 证书可解析）
    try:
        saml_flow._build_auth({"get_data": {}, "post_data": {}}, idp)
        return {"ok": True, "claims": None, "note": "config_only"}
    except saml_flow.SAMLError as exc:
        return {"ok": False, "error": exc.reason}


class SSOEnforceView(APIView):
    """POST 开 / DELETE 关（api-conventions §2.6 归档类动作范式）。"""

    permission_classes = [IsAuthenticatedAndActive]

    @_sso_manage
    def post(self, request, slug):
        ws, _ = get_workspace_or_404(slug, request.user)
        idp = _get_idp(ws)
        if idp.enforce_sso:
            return success_response({"enforce_sso": True})  # 幂等 200
        if not (idp.is_enabled and idp.last_test_passed_at):  # BR-04 前置
            raise AppException(
                "RESOURCE_STATE_INVALID",
                message="强制 SSO 前必须启用 IdP 且通过测试连接",
                details=[{"field": "enforce_sso", "code": "TEST_REQUIRED",
                          "message": "先启用并通过 connection-check"}],
            )
        # BR-05 防自锁：≥1 名 WS_OWNER 已完成 SSO 登录绑定
        owner_bound = SSOAccount.objects.filter(
            idp=idp,
            user__member_workspace__workspace=ws,
            user__member_workspace__role=WorkspaceRole.OWNER,
        ).exists()
        if not owner_bound:
            raise AppException(
                "RESOURCE_STATE_INVALID",
                message="开启前至少一名所有者需完成 SSO 登录绑定",
                details=[{"field": "enforce_sso", "code": "OWNER_BINDING_REQUIRED",
                          "message": "所有者先经 IdP 登录一次以建立绑定"}],
            )
        idp.enforce_sso = True
        idp.save(update_fields=["enforce_sso", "updated_at"])
        _audit("sso.enforce_on", actor_id=request.user.id, object_id=idp.id)
        return success_response({"enforce_sso": True})

    @_sso_manage
    def delete(self, request, slug):
        ws, _ = get_workspace_or_404(slug, request.user)
        idp = _get_idp(ws)
        idp.enforce_sso = False
        idp.save(update_fields=["enforce_sso", "updated_at"])
        _audit("sso.enforce_off", actor_id=request.user.id, object_id=idp.id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SSOBindingsAdminView(APIView):
    """GET .../sso/bindings/ —— 绑定成员清单（管理面）。"""

    permission_classes = [IsAuthenticatedAndActive]

    @_sso_manage
    def get(self, request, slug):
        ws, _ = get_workspace_or_404(slug, request.user)
        idp = _get_idp(ws)
        rows = [{
            "id": str(a.id),
            "user_id": str(a.user_id),
            "email": a.email_at_binding,
            "subject": a.subject,
            "created_at": a.created_at,
        } for a in idp.accounts.select_related("user").order_by("-created_at")]
        return success_response(rows)


# ── 认证流（公开）────────────────────────────────────────

def _establish_session(request, user) -> None:
    """复用 AUTH-001 口径：login + 14 天滑动（不设 remember）。"""
    login(request, user)
    request.session.set_expiry(0)


class SSORouteView(APIView):
    """POST /api/v1/auth/sso/route/ —— 邮箱 → sso / password（登录前置发现）。"""

    permission_classes = [AllowAny]

    def post(self, request):
        email = str(request.data.get("email") or "").strip().lower()
        if not email or "@" not in email:
            raise AppException(
                "VALIDATION_ERROR", message="email 必填",
                details=[{"field": "email", "code": "REQUIRED",
                          "message": "email 必填"}],
            )
        idp = find_enforced_idp_for_email(email)
        if idp:
            return success_response({
                "mode": "sso",
                "sso_login_url": f"/api/v1/auth/sso/{idp.workspace.slug}/sign-in/",
            })
        return success_response({"mode": "password"})


class SSOSignInView(APIView):
    """GET /api/v1/auth/sso/{slug}/sign-in/ —— OIDC 发起（302 IdP）。"""

    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.conf import settings as dj_settings
        from django.http import HttpResponseRedirect

        from plane.db.models import Workspace

        try:
            ws = Workspace.objects.get(slug=slug)
        except Workspace.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        idp = getattr(ws, "identity_provider", None)
        if idp is None or not idp.is_enabled:
            raise NotFound("RESOURCE_NOT_FOUND")
        if idp.protocol == IdentityProvider.PROTOCOL_SAML:
            # SAML 发起走专用端点（协议族分野）
            raise AppException("RESOURCE_STATE_INVALID",
                               message="该组织配置为 SAML 协议，请用 /saml/sign-in/")
        next_url = request.GET.get("next") or "/"
        verifier, challenge = oidc.pkce_pair()
        state, nonce = oidc._b64url(__import__("secrets").token_bytes(18)), \
            oidc._b64url(__import__("secrets").token_bytes(18))
        redirect_uri = (getattr(dj_settings, "SSO_BASE_URL", "")
                        or request.build_absolute_uri("/").rstrip("/")) \
            + "/api/v1/auth/sso/callback/"
        url = oidc.authorize_url(idp, state, nonce, challenge, redirect_uri)
        resp = HttpResponseRedirect(url)
        oidc.set_txn_cookie(resp, oidc.build_txn_cookie_payload(
            idp, state=state, nonce=nonce, verifier=verifier, next_url=next_url))
        return resp


class SSOCallbackView(APIView):
    """GET /api/v1/auth/sso/callback/ —— OIDC 回调（302 next / 认领页）。"""

    permission_classes = [AllowAny]

    def get(self, request):
        from django.conf import settings as dj_settings
        from django.http import HttpResponseRedirect

        from plane.sso.oidc import SSOError

        txn = oidc.read_txn_cookie(request)
        state = request.GET.get("state", "")
        if (not txn or txn.get("pending_claim") or txn.get("state") != state):
            # Cookie 缺失/过期/已消费/pending 态复用/state 不匹配：同码 401
            raise AppException("AUTH_INVALID_CREDENTIALS",
                               message="SSO 登录状态校验失败，请重新发起登录") from None
        try:
            idp = IdentityProvider.objects.get(pk=txn["idp"], is_enabled=True)
        except IdentityProvider.DoesNotExist:
            raise AppException("AUTH_INVALID_CREDENTIALS",
                               message="SSO 登录状态校验失败，请重新发起登录") from None
        try:
            token = oidc.exchange_token(
                idp, request.GET["code"],
                redirect_uri=((getattr(dj_settings, "SSO_BASE_URL", "")
                               or request.build_absolute_uri("/").rstrip("/"))
                              + "/api/v1/auth/sso/callback/"),
                verifier=txn["verifier"])
            claims = oidc.verify_id_token(idp, token["id_token"],
                                          nonce=txn["nonce"])
        except (SSOError, KeyError) as exc:
            logger.warning("sso.callback.failed reason=%s", exc)
            raise AppException("AUTH_INVALID_CREDENTIALS",
                               message="身份提供方校验失败，请重新发起登录") from exc
        try:
            user = resolve_user(idp, claims)
        except ClaimRequired as cr:
            # sso_txn 原地重写为 pending_claim 态（§4.2 唯一定义，仍 600s）
            resp = HttpResponseRedirect("/sso/claim?next=" + (txn.get("next") or "/"))
            oidc.set_txn_cookie(resp, {
                "pending_claim": True, "idp": str(idp.id),
                "sub": str(claims["sub"]), "email": str(claims["email"]).lower(),
                "claim_user_id": str(cr.user.id), "next": txn.get("next") or "/",
            })
            return resp
        _establish_session(request, user)
        _audit("sso.login", actor_id=user.id, object_id=idp.id, protocol="oidc")
        resp = HttpResponseRedirect(txn.get("next") or "/")
        oidc.delete_txn_cookie(resp)
        resp["Cache-Control"] = "no-store"
        return resp


class SSOClaimView(APIView):
    """POST /api/v1/auth/sso/claim/ —— 认领：验密一次 → 建绑定 + 建会话。"""

    permission_classes = [AllowAny]

    def post(self, request):
        from django.contrib.auth import authenticate

        txn = oidc.read_txn_cookie(request)
        if not txn or not txn.get("pending_claim"):
            raise AppException("SSO_TXN_INVALID",
                               message="登录事务已失效，请重新发起 SSO 登录")
        password = str(request.data.get("password") or "")
        user_id = str(txn.get("claim_user_id") or "")
        from plane.db.models import User

        user = User.objects.filter(pk=user_id, is_active=True).first()
        if user is None or authenticate(request, email=user.email,
                                        password=password) is None:
            raise AppException("AUTH_INVALID_CREDENTIALS", message="邮箱或密码错误")
        try:
            idp = IdentityProvider.objects.get(pk=txn["idp"])
        except IdentityProvider.DoesNotExist:
            raise AppException("SSO_TXN_INVALID",
                               message="登录事务已失效，请重新发起 SSO 登录") from None
        if SSOAccount.objects.filter(idp=idp, subject=txn["sub"]).exists():
            raise AppException("RESOURCE_ALREADY_EXISTS",
                               message="该 IdP 身份已绑定账号",
                               details=[{"field": "subject", "code": "UNIQUE",
                                         "message": "请直接用 SSO 登录"}])
        SSOAccount.objects.create(idp=idp, user=user, subject=txn["sub"],
                                  email_at_binding=txn["email"])
        _establish_session(request, user)
        _audit("sso.claim", actor_id=user.id, object_id=idp.id)
        resp = Response({"status": "success",
                         "data": {"next": txn.get("next") or "/"}},
                        status=status.HTTP_200_OK)
        oidc.delete_txn_cookie(resp)
        return resp


# ── SAML 认证流 ───────────────────────────────────────────

def _saml_request_data(request) -> dict:
    return {
        "http_host": request.get_host(),
        "script_name": request.path,
        "get_data": request.GET,
        "post_data": request.POST,
        "https": "on" if request.is_secure() else "off",
    }


class SAMLSignInView(APIView):
    """GET /api/v1/auth/sso/{slug}/saml/sign-in/ —— AuthnRequest 发起。"""

    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.http import HttpResponseRedirect

        from plane.db.models import Workspace

        try:
            ws = Workspace.objects.get(slug=slug)
        except Workspace.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        idp = getattr(ws, "identity_provider", None)
        if idp is None or not idp.is_enabled or \
                idp.protocol != IdentityProvider.PROTOCOL_SAML:
            raise NotFound("RESOURCE_NOT_FOUND")
        try:
            url = saml_flow.begin_sso(_saml_request_data(request), idp,
                                      request.GET.get("next") or "/")
        except saml_flow.SAMLError as exc:
            logger.warning("sso.saml.begin_failed %s", exc)
            raise AppException("SERVER_EXTERNAL_SERVICE_ERROR",
                               message="身份提供方暂时不可用") from exc
        return HttpResponseRedirect(url)


class SAMLACSView(APIView):
    """POST /api/v1/auth/sso/saml/acs/ —— 断言消费（全局端点，按 Issuer 解析）。"""

    permission_classes = [AllowAny]

    def post(self, request):
        from django.http import HttpResponseRedirect

        saml_response = request.POST.get("SAMLResponse", "")
        if not saml_response:
            raise AppException("AUTH_INVALID_CREDENTIALS",
                               message="SSO 登录状态校验失败，请重新发起登录") from None
        idp = self._resolve_idp(saml_response)
        if idp is None or not idp.is_enabled:
            raise AppException("AUTH_INVALID_CREDENTIALS",
                               message="SSO 登录状态校验失败，请重新发起登录") from None
        try:
            claims = saml_flow.consume_assertion(_saml_request_data(request), idp)
        except saml_flow.SAMLError as exc:
            logger.warning("sso.saml.acs_failed %s", exc)
            raise AppException("AUTH_INVALID_CREDENTIALS",
                               message="身份提供方校验失败，请重新发起登录") from exc
        try:
            user = resolve_user(idp, claims)
        except ClaimRequired:
            # SAML 认领：无标准 Cookie 流，直接 401 引导（登记偏差——完整
            # SAML 认领向导随 R6 前端补）
            raise AppException("AUTH_INVALID_CREDENTIALS",
                               message="该邮箱已存在本地账号，请联系管理员绑定") from None
        _establish_session(request, user)
        _audit("sso.login", actor_id=user.id, object_id=idp.id, protocol="saml",
               initiated_by=claims.get("initiated_by"))
        resp = HttpResponseRedirect(request.POST.get("RelayState") or "/")
        resp["Cache-Control"] = "no-store"
        return resp

    @staticmethod
    def _resolve_idp(saml_response_b64: str):
        import base64

        from plane.db.models import IdentityProvider as IDP

        try:
            xml = base64.b64decode(saml_response_b64).decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            return None
        # 按 Issuer 粗筛（精确校验由 SDK 在 consume 内完成）
        candidates = IDP.objects.filter(
            protocol=IDP.PROTOCOL_SAML, is_enabled=True)
        for idp in candidates:
            if idp.idp_entity_id and idp.idp_entity_id in xml:
                return idp
        return None


class SAMLMetadataView(APIView):
    """GET /api/v1/auth/sso/{slug}/metadata/ —— SP 元数据 / OIDC 摘要。"""

    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.http import HttpResponse

        from plane.db.models import Workspace

        try:
            ws = Workspace.objects.get(slug=slug)
        except Workspace.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        idp = getattr(ws, "identity_provider", None)
        if idp is None:
            raise NotFound("RESOURCE_NOT_FOUND")
        if idp.protocol == IdentityProvider.PROTOCOL_SAML:
            try:
                xml = saml_flow.build_metadata(idp)
            except saml_flow.SAMLError as exc:
                logger.warning("sso.saml.metadata_failed %s", exc)
                raise AppException("SERVER_STORAGE_ERROR", message="元数据生成失败") from exc
            return HttpResponse(xml, content_type="application/xml")
        return success_response({
            "protocol": "oidc", "issuer": idp.issuer,
            "client_id": idp.client_id,
            "redirect_uris": ["/api/v1/auth/sso/callback/"],
        })


# ── 本人绑定 ──────────────────────────────────────────────

class MySSOBindingsView(APIView):
    """GET /api/v1/users/me/sso/bindings/ —— 本人绑定列表。"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        rows = [{
            "id": str(a.id),
            "provider": a.idp.workspace.slug,
            "protocol": a.idp.protocol,
            "created_at": a.created_at,
        } for a in request.user.sso_accounts.select_related(
            "idp__workspace").order_by("-created_at")]
        return success_response(rows)


class MySSOBindingDeleteView(APIView):
    """DELETE /api/v1/users/me/sso/bindings/{binding_id}/ —— 解绑（BR-11）。"""

    permission_classes = [IsAuthenticated]

    def delete(self, request, binding_id):
        try:
            binding = request.user.sso_accounts.get(pk=binding_id)
        except SSOAccount.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        if not request.user.has_usable_password():  # BR-11
            raise AppException(
                "VALIDATION_ERROR",
                message="未设置本地密码，须先设置密码再解绑",
                details=[{"field": "password", "code": "REQUIRED",
                          "message": "先设置本地密码再解绑"}],
            )
        binding.delete()
        _audit("sso.unbind", actor_id=request.user.id, object_id=binding_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
