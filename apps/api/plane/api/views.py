"""Open API 认证/网关/外部端点（INTG-004 §4.2/§4.3，P4 R4）。

设计：external/ 命名空间自管凭证认证（X-API-Key 或 Bearer OAuth token——
与 SCIM 同款不进全局 DRF 配置），scope 网关（缺失 →
PERM_TOKEN_SCOPE_INSUFFICIENT + 所需清单），白名单物理隔离（未注册
路径对凭证天然 404，BR-03），凭证速率滑窗 + 调用留痕（同步写——
批量异步队列为部署期演进，登记 ADR）。

OAuth token 形态：HMAC 签名受保护载荷（sub/aud/scope/exp/jti）——
RS256 非对称密钥体系随 INFRA-006 私钥管理落地，本轮登记偏差（ADR-0033）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid as _uuid

from django.conf import settings as dj_settings
from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger("plane.api.openapi")

#: scope 全集（§2.3 二十四枚举——本轮核心子集，余量随白名单扩容登记）
ALL_SCOPES = frozenset(
    {
        "issues:read",
        "issues:write",
        "issues:relation",
        "comments:read",
        "comments:write",
        "comments:delete",
        "projects:read",
        "workspaces:read",
        "users:read",
        "labels:read",
        "states:read",
        "views:read",
        "cycles:read",
        "worklogs:read",
        "worklogs:write",
        "files:read",
        "files:write",
        "webhooks:manage",
        "notifications:read",
        "approvals:read",
        "activities:read",
        "reports:read",
    }
)

ACCESS_TTL = 3600  # 1h
REFRESH_TTL_DAYS = 30
RATE_PER_MIN = 60  # 凭证默认速率（企业 600 为租户配置演进）


# ── 工具 ────────────────────────────────────────────────────────────


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _hmac_b64(payload: bytes) -> str:
    key = (dj_settings.SECRET_KEY or "dev").encode()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def issue_access_token(user_id: str, app_id: str, scopes: list[str]) -> str:
    """HMAC 签名受保护载荷（§4.1 JWT 偏差登记）。"""
    header = {"alg": "HS256", "typ": "JWT"}
    body = {
        "sub": user_id,
        "aud": app_id,
        "scope": " ".join(scopes),
        "exp": int(time.time()) + ACCESS_TTL,
        "jti": secrets.token_hex(8),
    }
    raw = json.dumps(header, separators=(",", ":")) + "." + json.dumps(body, separators=(",", ":"))
    sig = _hmac_b64(raw.encode())
    return f"{raw}.{sig}"


def verify_access_token(token: str) -> dict | None:
    try:
        raw, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(_hmac_b64(raw.encode()), sig):
            return None
        body = json.loads(raw.split(".")[1])
        if body.get("exp", 0) < time.time():
            return None
        if cache.get(f"oat:blocked:{body.get('jti')}"):
            return None  # 撤销黑名单（TTL=ACCESS_TTL）
        return body
    except (ValueError, IndexError):
        return None


def ip_in_cidrs(ip: str | None, cidrs: list[str]) -> bool:
    import ipaddress

    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for cidr in cidrs:
        try:
            if addr in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


class OpenApiCredentialError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


def authenticate_credential(request):
    """X-API-Key / Bearer token → (user, scopes, (cred_type, cred_id))。

    白名单物理隔离：本函数仅由 external/ 白名单视图调用——未注册路径不经
    此处，对凭证天然 404（BR-03）。
    """
    from plane.db.models import APIToken, OAuthGrant

    raw_key = request.headers.get("X-API-Key", "")
    if raw_key:
        token = APIToken.objects.filter(key_hash=_sha(raw_key), is_active=True).select_related("user").first()
        if token is None:
            raise OpenApiCredentialError(401, "AUTH_INVALID_TOKEN", "API Key 无效或已吊销")
        if token.expires_at and token.expires_at < timezone.now():
            raise OpenApiCredentialError(401, "AUTH_TOKEN_EXPIRED", "API Key 已过期")
        ip = request.META.get("HTTP_X_FORWARDED_FOR", ",").split(",")[0].strip() or request.META.get("REMOTE_ADDR")
        if token.ip_allowlist and not ip_in_cidrs(ip, token.ip_allowlist):
            raise OpenApiCredentialError(403, "PERM_IP_NOT_ALLOWED", "来源 IP 不在密钥白名单（BR-11）")
        APIToken.objects.filter(pk=token.pk).update(last_used_at=timezone.now())
        return token.user, list(token.scopes or []), ("key", token.id)

    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        body = verify_access_token(header[7:].strip())
        if body is None:
            raise OpenApiCredentialError(401, "AUTH_INVALID_TOKEN", "access token 无效/过期/已撤销")
        from plane.db.models import User

        user = User.objects.filter(pk=body["sub"]).first()
        if user is None:
            raise OpenApiCredentialError(401, "AUTH_INVALID_TOKEN", "用户不存在")
        grant = OAuthGrant.objects.filter(app_id=body["aud"], user=user, revoked_at__isnull=True).first()
        if grant is None:
            # client_credentials 通道：无用户授权行，凭应用属主身份放行
            from plane.db.models import OAuthApplication

            app = OAuthApplication.objects.filter(pk=body["aud"], owner=user).first()
            if app is None:
                raise OpenApiCredentialError(401, "AUTH_INVALID_TOKEN", "授权已撤销")
        return user, body.get("scope", "").split(), ("token", _uuid.UUID(body["aud"]))

    raise OpenApiCredentialError(401, "AUTH_REQUIRED", "缺少凭证（X-API-Key 或 Bearer）")


def check_rate_and_log(request, cred, user, status_code: int, latency_ms: int, workspace_id=None):
    """凭证速率滑窗 + 调用留痕（同步写——批量队列为部署期演进）。"""
    from plane.db.models import ApiCallLog

    cred_type, cred_id = cred
    minute = int(time.time()) // 60
    key = f"orate:{cred_id}:{minute}"
    try:
        hits = cache.get(key) or 0
        if hits >= RATE_PER_MIN:
            raise OpenApiCredentialError(429, "RATE_LIMIT_EXCEEDED", f"凭证速率超限（{RATE_PER_MIN}/min）")
        cache.set(key, hits + 1, timeout=120)
    except OpenApiCredentialError:
        raise
    except Exception:  # noqa: BLE001 —— Redis 失联放行
        pass
    try:
        ApiCallLog.objects.create(
            credential_type=cred_type,
            credential_id=cred_id,
            user=user,
            workspace_id=workspace_id,
            method=request.method,
            path=request.path[:255],
            status_code=status_code,
            latency_ms=latency_ms,
            ip=request.META.get("REMOTE_ADDR"),
        )
    except Exception:  # noqa: BLE001 —— 留痕不阻塞
        logger.warning("openapi.calllog_failed path=%s", request.path)


class ExternalBase(APIView):
    """白名单视图基类：凭证认证 + scope 网关 + 404 隔离语义。"""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    required_scopes: list[str] = []

    def dispatch_with_credential(self, request, handler):
        import time as _time

        t0 = _time.time()
        try:
            user, scopes, cred = authenticate_credential(request)
            missing = [s for s in self.required_scopes if s not in scopes]
            if missing:
                raise OpenApiCredentialError(403, "PERM_TOKEN_SCOPE_INSUFFICIENT", f"缺少 scope：{missing}")
            request.scopes = scopes
            request.credential_user = user
            response = handler(request, user)
            check_rate_and_log(request, cred, user, response.status_code, int((_time.time() - t0) * 1000))
            return response
        except OpenApiCredentialError as exc:
            resp = JsonResponse(
                {"status": "error", "error": {"code": exc.code, "message": exc.message, "details": []}},
                status=exc.status,
            )
            try:
                check_rate_and_log(request, ("none", _uuid.uuid4()), None, exc.status, int((_time.time() - t0) * 1000))
            except Exception:  # noqa: BLE001
                pass
            return resp

    def get(self, request, **kwargs):
        return self.dispatch_with_credential(request, lambda req, user: self.handle_get(req, user, **kwargs))

    def post(self, request, **kwargs):
        return self.dispatch_with_credential(request, lambda req, user: self.handle_post(req, user, **kwargs))

    def handle_get(self, request, user):
        return Response({"status": "error", "error": {"code": "SERVER_NOT_IMPLEMENTED"}}, status=501)

    def handle_post(self, request, user):
        return Response({"status": "error", "error": {"code": "SERVER_NOT_IMPLEMENTED"}}, status=501)


def _user_ws_ids(user) -> list:
    from plane.db.models import Workspace

    return list(
        Workspace.objects.filter(
            workspace_member__member=user, workspace_member__is_active=True, workspace_member__deleted_at__isnull=True
        ).values_list("id", flat=True)
    )


def _envelope(data, meta=None):
    return Response({"status": "success", "data": data, **({"meta": meta} if meta else {})})


# ── 白名单只读端点（§2.2 精选：issues / projects / workspaces）──────


class ExternalIssuesListView(ExternalBase):
    required_scopes = ["issues:read"]

    def handle_get(self, request, user):
        from plane.db.models import Issue, Project, Workspace

        ws_ids = list(
            Workspace.objects.filter(
                workspace_member__member=user,
                workspace_member__is_active=True,
                workspace_member__deleted_at__isnull=True,
            ).values_list("id", flat=True)
        )
        project_ids: list = []
        for ws_id in ws_ids:  # accessible_in 逐空间（行级通道）
            project_ids += list(Project.objects.accessible_by(user, workspace_id=ws_id).values_list("id", flat=True))
        qs = Issue.objects.filter(project__in=project_ids, deleted_at__isnull=True)
        rows = [
            {
                "id": str(i.id),
                "name": i.name,
                "sequence_id": i.sequence_id,
                "state_id": str(i.state_id) if i.state_id else None,
                "priority": i.priority,
                "start_date": i.start_date,
                "target_date": i.target_date,
                "created_at": i.created_at,
            }
            for i in qs.order_by("-created_at")[:50]
        ]
        return _envelope(rows, {"count": len(rows)})


class ExternalIssueDetailView(ExternalBase):
    required_scopes = ["issues:read"]

    def handle_get(self, request, user, issue_id=None):
        from plane.db.models import Issue

        issue = Issue.objects.select_related("project").filter(pk=issue_id, deleted_at__isnull=True).first()
        if issue is None:
            return Response({"status": "error", "error": {"code": "RESOURCE_NOT_FOUND"}}, status=404)
        # 行级可见性：经项目可达性过滤（accessible_by 需逐空间）
        from plane.db.models import Project

        if not Project.objects.filter(
            pk=issue.project_id, workspace__in=_user_ws_ids(user), deleted_at__isnull=True
        ).exists():
            return Response({"status": "error", "error": {"code": "RESOURCE_NOT_FOUND"}}, status=404)
        return _envelope(
            {
                "id": str(issue.id),
                "name": issue.name,
                "sequence_id": issue.sequence_id,
                "description_stripped": (issue.description_stripped or "")[:500],
                "priority": issue.priority,
                "start_date": issue.start_date,
                "target_date": issue.target_date,
                "estimate_minutes": issue.estimate_minutes,
                "created_at": issue.created_at,
            }
        )


class ExternalWorkspacesListView(ExternalBase):
    required_scopes = ["workspaces:read"]

    def handle_get(self, request, user):
        from plane.db.models import Workspace

        rows = [
            {
                "id": str(w.id),
                "name": w.name,
                "slug": w.slug,
            }
            for w in Workspace.objects.filter(
                workspace_member__member=user,
                workspace_member__is_active=True,
                workspace_member__deleted_at__isnull=True,
            ).distinct()[:50]
        ]
        return _envelope(rows, {"count": len(rows)})
