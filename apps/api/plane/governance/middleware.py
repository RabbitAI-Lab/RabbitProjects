"""租户治理快路径中间件（AUTH-012 §4.3/§4.5，P4 R1）。

对齐 WorkspaceArchiveMiddleware 范式（路径解析 workspace → 短路信封），
三件事：
  ① R-01 IP 段封禁（Redis risk:R-01:ipban:{cidr}，TTL 30min——引擎触发即写，
    与 rbac §11.5 IP 白名单分域）→ 403 PERM_DENIED；
  ② 冻结写拒（frozen:{tenant}，BR-09：生效时刻起拒绝新写，读放行——
    15 分钟缓冲后的只读缓存视图属部署期演进）→ 409 RESOURCE_STATE_INVALID；
  ③ 租户 API 速率桶（第 3 强制点：tq:{tid}:rpm:{bucket} 固定窗口，
    用户级 Throttle 之后更粗一层；限流处置标记 risk:throttle:{tid}
    存在时降至 10% 配额）→ 429 RATE_LIMIT_EXCEEDED + Retry-After。

BR-11 门控：TENANT_GOVERNANCE_ENABLED=False 整条快路径直通（零开销）。
性能注（§4.6 预算 <0.7ms）：当前为每请求 1 次 WS 查询 + 1 次 Tenant 查询 +
2~3 次 Redis 往返；进程内 1s 负缓存与 slug→tenant 映射缓存随压测收口。
"""

from __future__ import annotations

import re
import time

from django.conf import settings as dj_settings
from django.core.cache import cache
from django.http import JsonResponse

_WS_PATH_RE = re.compile(r"^/api/v1/workspaces/(?P<slug>[^/]+)(/.*)?$")
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_RPM_WINDOW = 60


def _client_ip(request) -> str | None:
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None


def _cidr(ip: str | None) -> str | None:
    if not ip or "." not in ip:
        return ip
    return f"{ip.rsplit('.', 1)[0]}.0/24"


def _error_response(
    code: str, message: str, http_status: int, *, details: list | None = None, headers: dict | None = None
):
    from plane.base.middleware import current_request_id, ulid_new

    resp = JsonResponse(
        {
            "status": "error",
            "error": {
                "code": code,
                "message": message,
                "details": details or [],
                "request_id": current_request_id() or ulid_new(),
            },
        },
        status=http_status,
        content_type="application/json",
    )
    for k, v in (headers or {}).items():
        resp[k] = v
    return resp


class GovernanceMiddleware:
    """治理快路径（冻结写拒 / IP 段封禁 / 租户速率桶）。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(dj_settings, "TENANT_GOVERNANCE_ENABLED", False):
            return self.get_response(request)

        # ① R-01 IP 段封禁（全站路径，不限 workspace）
        try:
            cidr = _cidr(_client_ip(request))
            if cidr and cache.get(f"risk:R-01:ipban:{cidr}"):
                return _error_response("PERM_DENIED", "来源 IP 段处于临时封禁期（风控处置）", 403)
        except Exception:  # noqa: BLE001 —— Redis 失联放行
            pass

        m = _WS_PATH_RE.match(request.path)
        if not m:
            return self.get_response(request)
        from plane.db.models import Workspace

        ws = Workspace.objects.filter(slug=m.group("slug"), deleted_at__isnull=True).only("id", "tenant_id").first()
        if ws is None or ws.tenant_id is None:
            return self.get_response(request)  # 未治理租户直通（BR-11）
        tid = str(ws.tenant_id)

        # ② 冻结写拒（BR-09）
        try:
            frozen = cache.get(f"frozen:{tid}")
        except Exception:  # noqa: BLE001
            frozen = False
        if frozen and request.method not in _SAFE_METHODS:
            return _error_response(
                "RESOURCE_STATE_INVALID",
                "租户处于安全审查期，写操作暂时受限",
                409,
                details=[{"field": "frozen", "code": "READ_ONLY", "message": "数据完整保留；审查结束后由平台运营解除"}],
            )

        # ③ 租户速率桶（第 3 强制点；用户级 Throttle 在 DRF 层先行）
        try:
            limit = self._tenant_rpm(tid)
            if limit:
                bucket = int(time.time()) // _RPM_WINDOW
                key = f"tq:{tid}:rpm:{bucket}"
                if cache.add(key, 1, timeout=_RPM_WINDOW):
                    hits = 1
                else:
                    hits = cache.incr(key)
                if hits > limit:
                    retry_after = (bucket + 1) * _RPM_WINDOW - int(time.time())
                    return _error_response(
                        "RATE_LIMIT_EXCEEDED",
                        "租户 API 请求速率超限",
                        429,
                        details=[{"field": "api_rate", "code": "LIMIT", "message": f"{limit} req/min（租户层）"}],
                        headers={"Retry-After": str(max(1, retry_after))},
                    )
        except Exception:  # noqa: BLE001 —— fail-open 同限流域
            pass
        response = self.get_response(request)
        # 冻结横幅数据通道（§3.4：租户成员视角）：读响应携带标记头，
        # web 端 axios 拦截器读取后置全局横幅（写请求已在上方 409 短路）
        if frozen:
            response["X-Tenant-Frozen"] = "1"
        return response

    @staticmethod
    def _tenant_rpm(tid: str) -> int | None:
        """租户生效 rpm（限流处置标记存在时降至 10%）。"""
        from plane.db.models import Tenant
        from plane.governance.risk_engine import tier_quota

        tenant = Tenant.objects.filter(pk=tid).first()
        if tenant is None:
            return None
        limit = tier_quota(tenant)["api_rate_per_minute"] or 0
        if cache.get(f"risk:throttle:{tid}"):
            limit = int(limit * 0.1)
        return limit or None
