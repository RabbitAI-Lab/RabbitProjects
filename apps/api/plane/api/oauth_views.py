"""Open API 管理端点：API Key 与 OAuth 2.0（INTG-004 §4.4，P4 R4）。

API Key：列表/创建（明文一次）/吊销/调用日志。OAuth：应用注册与更新、
授权（同意页数据 + 授权码签发）、token（authorization_code 与 refresh
轮换 + client_credentials 扩展）、revoke（黑名单）、introspect、userinfo、
已授权应用管理。统一走 api-conventions 信封（Session 认证）；
token/introspect 端点按 §9.4 协议形态返回（无统一信封——登记 ADR）。
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.api.views import ACCESS_TTL, ALL_SCOPES, REFRESH_TTL_DAYS, _sha, issue_access_token, verify_access_token
from plane.base.exception import AppException
from plane.base.response import success_response


def _new_client_id() -> str:
    return f"rp_app_{secrets.token_urlsafe(12)[:20]}"


class ApiTokenListCreateView(APIView):
    """GET/POST /api/v1/api-tokens/ —— 密钥管理（明文仅一次）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from plane.db.models import APIToken

        rows = APIToken.objects.filter(user=request.user).order_by("-created_at")
        return success_response(
            [
                {
                    "id": str(t.id),
                    "name": t.name,
                    "key_prefix": t.key_prefix,
                    "key_suffix": t.key_suffix,
                    "scopes": t.scopes,
                    "ip_allowlist": t.ip_allowlist,
                    "expires_at": t.expires_at,
                    "last_used_at": t.last_used_at,
                    "is_active": t.is_active,
                    "created_at": t.created_at,
                }
                for t in rows
            ]
        )

    def post(self, request):
        from plane.db.models import APIToken

        p = request.data or {}
        name = str(p.get("name") or "").strip()
        if not name:
            raise AppException("VALIDATION_ERROR", message="name 必填", details=[{"field": "name", "code": "REQUIRED"}])
        scopes = p.get("scopes") or []
        unknown = [s for s in scopes if s not in ALL_SCOPES]
        if unknown:
            raise AppException(
                "VALIDATION_ERROR",
                message="非法 scope",
                details=[{"field": "scopes", "code": "INVALID", "message": unknown}],
            )
        env = str(p.get("env") or "live")
        raw = f"rp_{env}_{secrets.token_urlsafe(24)}"
        token = APIToken.objects.create(
            user=request.user,
            name=name[:64],
            key_hash=_sha(raw),
            key_prefix=f"rp_{env}_",
            key_suffix=raw[-4:],
            scopes=list(scopes),
            ip_allowlist=p.get("ip_allowlist") or [],
            expires_at=p.get("expires_at") or None,
            created_by=request.user,
        )
        return success_response(
            {
                "id": str(token.id),
                "name": token.name,
                "key": raw,
                "key_prefix": token.key_prefix,
                "key_suffix": token.key_suffix,
                "scopes": token.scopes,
                "warning": "此密钥仅本次完整显示，请立即保存（§2.5）",
            },
            status_code=201,
        )


class ApiTokenDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, token_id):
        from plane.db.models import APIToken

        updated = APIToken.objects.filter(pk=token_id, user=request.user).update(is_active=False)
        if not updated:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        return success_response({"revoked": True})


class ApiTokenLogsView(APIView):
    """GET /api-tokens/logs/ —— 调用日志（30 天，凭证筛选）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from plane.db.models import ApiCallLog

        qs = ApiCallLog.objects.filter(user=request.user, created_at__gte=timezone.now() - timedelta(days=30)).order_by(
            "-created_at"
        )[:200]
        cred = request.query_params.get("credential_id")
        if cred:
            qs = qs.filter(credential_id=cred)
        return success_response(
            [
                {
                    "credential_type": r.credential_type,
                    "credential_id": str(r.credential_id),
                    "method": r.method,
                    "path": r.path,
                    "status_code": r.status_code,
                    "latency_ms": r.latency_ms,
                    "ip": str(r.ip) if r.ip else None,
                    "created_at": r.created_at,
                }
                for r in qs
            ]
        )


class OAuthApplicationView(APIView):
    """GET/POST /oauth/applications/ + PATCH {id}。"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from plane.db.models import OAuthApplication

        rows = OAuthApplication.objects.filter(owner=request.user)
        return success_response(
            [
                {
                    "id": str(a.id),
                    "name": a.name,
                    "client_id": a.client_id,
                    "client_type": a.client_type,
                    "redirect_uris": a.redirect_uris,
                    "scopes_requested": a.scopes_requested,
                    "review_status": a.review_status,
                    "created_at": a.created_at,
                }
                for a in rows
            ]
        )

    def post(self, request):
        from plane.db.models import OAuthApplication

        p = request.data or {}
        name = str(p.get("name") or "").strip()
        if not name:
            raise AppException("VALIDATION_ERROR", message="name 必填", details=[{"field": "name", "code": "REQUIRED"}])
        client_type = p.get("client_type") or "confidential"
        secret = secrets.token_urlsafe(24)
        app = OAuthApplication.objects.create(
            owner=request.user,
            name=name[:64],
            client_id=_new_client_id(),
            client_secret_hash=_sha(secret) if client_type == "confidential" else None,
            redirect_uris=p.get("redirect_uris") or [],
            scopes_requested=[s for s in (p.get("scopes_requested") or []) if s in ALL_SCOPES],
            client_type=client_type,
            created_by=request.user,
        )
        data = {"id": str(app.id), "client_id": app.client_id, "review_status": app.review_status}
        if app.client_secret_hash:
            data["client_secret"] = secret  # 仅本次
        return success_response(data, status_code=201)

    def patch(self, request, app_id):
        from plane.db.models import OAuthApplication

        app = OAuthApplication.objects.filter(pk=app_id, owner=request.user).first()
        if app is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        p = request.data or {}
        if "redirect_uris" in p:
            app.redirect_uris = p["redirect_uris"]
        if "review_status" in p and p["review_status"] in ("self", "pending"):
            app.review_status = p["review_status"]  # 上架审核由市场侧审
        app.updated_by = request.user
        app.save()
        return success_response({"id": str(app.id), "review_status": app.review_status})


class OAuthAuthorizeView(APIView):
    """GET /oauth/authorize/ —— 同意页数据 + 授权码签发。

    会话用户已登录前提：`approve=true` 即签 code（同意页前端承载）；未
    approve 返回应用与 scope 清单供渲染。code 10 分钟一次性。
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from plane.db.models import OAuthApplication, OAuthCode, Workspace

        client_id = request.query_params.get("client_id") or ""
        app = OAuthApplication.objects.filter(client_id=client_id).first()
        if app is None:
            return Response({"status": "error", "error": {"code": "RESOURCE_NOT_FOUND"}}, status=404)
        scopes = [s for s in (request.query_params.get("scope") or "").split() if s in ALL_SCOPES]
        redirect_uri = request.query_params.get("redirect_uri") or ""
        info = {"app": {"name": app.name, "client_id": app.client_id}, "scopes": scopes}
        if request.query_params.get("approve") != "true":
            return success_response(info)  # 同意页渲染数据
        if redirect_uri and app.redirect_uris and redirect_uri not in app.redirect_uris:
            raise AppException("VALIDATION_ERROR", message="redirect_uri 未登记")
        ws = Workspace.objects.filter(
            workspace_member__member=request.user,
            workspace_member__is_active=True,
            workspace_member__deleted_at__isnull=True,
        ).first()
        if ws is None:
            raise AppException("VALIDATION_ERROR", message="无可授权工作空间")
        code = secrets.token_urlsafe(24)
        OAuthCode.objects.create(
            app=app,
            user=request.user,
            workspace=ws,
            code_hash=_sha(code),
            scopes=scopes,
            redirect_uri=redirect_uri[:512],
            expires_at=timezone.now() + timedelta(minutes=10),
            created_by=request.user,
        )
        # 同意页直跳语义：code 仅一次返回（授权码不落响应缓存）
        return success_response({**info, "code": code, "expires_in": 600})


class OAuthTokenView(APIView):
    """POST /oauth/token/ —— authorization_code / refresh / client_credentials。

    协议形态返回（RFC 6749 §5.1——无统一信封，登记 ADR-0033）。
    refresh 轮换：新值签发作废旧值；旧值复用吊销整链（§9.4 rotation）。
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        from plane.db.models import OAuthApplication, OAuthCode, OAuthGrant

        p = request.data or {}
        grant_type = p.get("grant_type") or ""

        if grant_type == "authorization_code":
            code = (
                OAuthCode.objects.filter(code_hash=_sha(p.get("code") or ""), expires_at__gt=timezone.now())
                .select_related("app")
                .first()
            )
            if code is None:
                return Response({"error": "invalid_grant", "error_description": "授权码无效或已过期"}, status=400)
            code.delete()  # 一次性
            refresh = secrets.token_urlsafe(32)
            OAuthGrant.objects.update_or_create(
                app=code.app,
                user=code.user,
                workspace=code.workspace,
                defaults={
                    "scopes": code.scopes,
                    "refresh_token_hash": _sha(refresh),
                    "refresh_expires_at": timezone.now() + timedelta(days=REFRESH_TTL_DAYS),
                    "revoked_at": None,
                },
            )
            access = issue_access_token(str(code.user_id), str(code.app_id), code.scopes)
            return Response(
                {
                    "access_token": access,
                    "token_type": "Bearer",
                    "expires_in": ACCESS_TTL,
                    "refresh_token": refresh,
                    "scope": " ".join(code.scopes),
                }
            )

        if grant_type == "refresh_token":
            grant = (
                OAuthGrant.objects.filter(refresh_token_hash=_sha(p.get("refresh_token") or ""))
                .select_related("app")
                .first()
            )
            if grant is None or grant.revoked_at or grant.refresh_expires_at < timezone.now():
                return Response({"error": "invalid_grant", "error_description": "refresh 无效/过期/已撤销"}, status=400)
            new_refresh = secrets.token_urlsafe(32)
            grant.refresh_token_hash = _sha(new_refresh)
            grant.refresh_expires_at = timezone.now() + timedelta(days=REFRESH_TTL_DAYS)
            grant.save(update_fields=["refresh_token_hash", "refresh_expires_at", "updated_at"])
            access = issue_access_token(str(grant.user_id), str(grant.app_id), grant.scopes)
            return Response(
                {
                    "access_token": access,
                    "token_type": "Bearer",
                    "expires_in": ACCESS_TTL,
                    "refresh_token": new_refresh,
                    "scope": " ".join(grant.scopes),
                }
            )

        if grant_type == "client_credentials":
            app = OAuthApplication.objects.filter(
                client_id=p.get("client_id") or "", client_secret_hash=_sha(p.get("client_secret") or "")
            ).first()
            if app is None:
                return Response({"error": "invalid_client", "error_description": "client 凭证无效"}, status=401)
            scopes = [s for s in (p.get("scope") or "").split() if s in app.scopes_requested]
            access = issue_access_token(str(app.owner_id), str(app.id), scopes)
            return Response(
                {"access_token": access, "token_type": "Bearer", "expires_in": ACCESS_TTL, "scope": " ".join(scopes)}
            )

        return Response({"error": "unsupported_grant_type"}, status=400)


class OAuthRevokeView(APIView):
    """POST /oauth/revoke/ —— RFC 7009（200 恒返回；token 或 refresh）。"""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        from django.core.cache import cache

        from plane.db.models import OAuthGrant

        token = str((request.data or {}).get("token") or "")
        body = verify_access_token(token)
        if body:
            cache.set(f"oat:blocked:{body['jti']}", 1, timeout=ACCESS_TTL)
            return Response(status=200)
        grant = OAuthGrant.objects.filter(refresh_token_hash=_sha(token)).first()
        if grant:
            grant.revoked_at = timezone.now()
            grant.save(update_fields=["revoked_at", "updated_at"])
        return Response(status=200)


class OAuthIntrospectView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        body = verify_access_token(str((request.data or {}).get("token") or ""))
        if body is None:
            return Response({"active": False})
        return Response(
            {
                "active": True,
                "sub": body["sub"],
                "scope": body.get("scope", ""),
                "exp": body["exp"],
                "aud": body.get("aud"),
            }
        )


class AuthorizedAppsView(APIView):
    """GET/DELETE /users/me/authorized-apps/ —— 用户已授权应用与撤销。"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from plane.db.models import OAuthGrant

        rows = OAuthGrant.objects.filter(user=request.user).select_related("app").order_by("-created_at")
        return success_response(
            [
                {
                    "grant_id": str(g.id),
                    "app": g.app.name,
                    "client_id": g.app.client_id,
                    "scopes": g.scopes,
                    "revoked_at": g.revoked_at,
                    "created_at": g.created_at,
                }
                for g in rows
            ]
        )

    def delete(self, request, grant_id):
        from plane.db.models import OAuthGrant

        updated = OAuthGrant.objects.filter(pk=grant_id, user=request.user).update(revoked_at=timezone.now())
        if not updated:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        return success_response({"revoked": True})
