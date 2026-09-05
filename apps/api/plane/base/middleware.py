"""六件套中间件 —— 顺序敏感，见 api-conventions.md §10.4。

contextvar 与访问器集中定义于 plane.base.request_context；本模块只负责
在合适生命周期调用 bind / reset。
"""
from __future__ import annotations

import logging
import re
import time

import structlog
from django.conf import settings as dj_settings
from django.http import JsonResponse
from rest_framework.status import HTTP_503_SERVICE_UNAVAILABLE
from ulid import ULID

from plane.base.error_codes import DEFAULT_MESSAGES
from plane.base.request_context import (
    bind_actor,
    bind_request_id,
    current_request_id,
    reset_actor,
    reset_request_id,
)

# ── ① RequestIDMiddleware（最外层）────────────────────────


def ulid_new() -> str:
    """生成 26 位 Crockford Base32 ULID —— RequestIDMiddleware 与 §2.6 边界表共用。"""
    return str(ULID())


def settings_debug() -> bool:
    """dev/prod 一行判断；ResponseEnvelopeMiddleware 据此切换严格模式（开发态裸 2xx 抛错）。"""
    return bool(getattr(dj_settings, "DEBUG", False))


def _error_code_of(response) -> str | None:
    """从 DRF Response.data 中提取 envelope 的 error.code（access 日志维度，§13.5）。"""
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            return err.get("code")
    return None


ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")   # Crockford Base32，排除 I/L/O/U


class RequestIDMiddleware:
    """ULID 请求追踪：透传合法外部 X-Request-Id，否则生成；非法值重生成（防日志注入）。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get("X-Request-Id", "")
        request.request_id = incoming if ULID_RE.fullmatch(incoming) else ulid_new()
        token = bind_request_id(request.request_id)
        try:
            response = self.get_response(request)
        finally:
            reset_request_id(token)      # worker 线程复用，必须清理
        response.headers["X-Request-Id"] = request.request_id   # 成功响应也带（C3）
        return response


# ── ② StructuredLoggingMiddleware ─────────────────────────
class StructuredLoggingMiddleware:
    """每请求一行结构化 access 日志；携带 §13.5 全部字段。"""

    SLOW_REQUEST_WARN_MS = 1000     # > 1s 记 WARN
    SLOW_REQUEST_ERROR_MS = 3000    # > 3s 记 ERROR
    QUERY_COUNT_WARN = 30           # 单请求查询数预警（N+1 早期信号）

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.perf_counter()
        from django.db import connection, reset_queries
        if settings_debug():
            reset_queries()
        response = self.get_response(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        log = structlog.get_logger("plane.api.access")
        level = "error" if duration_ms > self.SLOW_REQUEST_ERROR_MS else \
                "warning" if duration_ms > self.SLOW_REQUEST_WARN_MS else "info"
        # path 用路由模板而非实际 URL（避免 ID 爆炸日志基数，§13.5）
        route_template = getattr(request.resolver_match, "route", request.path)
        # workspace_id 取自 URL 路径参数（slash 前缀的 URL 解析）；命中后写入日志维度
        workspace_id = None
        if request.resolver_match is not None:
            workspace_id = request.resolver_match.kwargs.get("slug")
        # 查询数预警：DEBUG 开启连接追踪时记录实际值，超阈值则字段额外标记 WARN
        query_count = len(connection.queries) if settings_debug() else None
        if query_count is not None and query_count > self.QUERY_COUNT_WARN:
            log = log.bind(query_count_warn=True)
        log.log(getattr(logging, level.upper()), "http_request",
                method=request.method,
                path="/" + route_template,
                status=response.status_code,
                error_code=_error_code_of(response),
                duration_ms=duration_ms,
                db_query_count=query_count,
                user_id=_actor_var_user_id(),
                workspace_id=workspace_id)
        return response


def _actor_var_user_id():
    """辅助读取：避免 StructuredLoggingMiddleware 内重复 import contextvar。

    必须走 current_actor() 访问器——contextvar 默认值为 None（ruff B039 禁止
    可变对象作 default），直接 _actor_var.get().get() 在未绑定时会 AttributeError。
    """
    from plane.base.request_context import current_actor
    return current_actor().get("user_id")


# ── ③ RateLimitHeaderMiddleware（P1 空实现，INFRA-005 填充）──
class RateLimitHeaderMiddleware:
    """为所有响应注入 X-RateLimit-Limit / -Remaining / -Reset 三件套（§4.4）。

    P1 仅注入占位值（limit=-1 表示未启用）；INFRA-005 接入 Redis 计数后
    替换为真实配额。位置必须在 Logging 之内——被限流拒绝的请求也要留日志。
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.headers.setdefault("X-RateLimit-Limit", "-1")
        response.headers.setdefault("X-RateLimit-Remaining", "-1")
        response.headers.setdefault("X-RateLimit-Reset", "-1")
        return response


# ── ④ AuditContextMiddleware ──────────────────────────────
class AuditContextMiddleware:
    """user / IP / UA 写入 contextvar，供模型 save() 审计字段与（P3）审计日志消费。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        token = bind_actor({
            "user_id": str(user.id) if getattr(user, "is_authenticated", False) else None,
            "ip": request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
                  or request.META.get("REMOTE_ADDR"),
            "user_agent": request.META.get("HTTP_USER_AGENT", "")[:256],
        })
        try:
            return self.get_response(request)
        finally:
            reset_actor(token)


# ── ⑤ ResponseEnvelopeMiddleware（兜底 + 开发态抛错）────────
class ResponseEnvelopeMiddleware:
    """捕获未经 success_response 的 2xx 响应并补齐信封（防止漏包装）。

    - 204 / 304：显式放行（C1 例外，BR-02）
    - 已是 {status:"success"} 结构：原样通过（防重复包装）
    - 流式 / 文件响应（FileResponse、StreamingHttpResponse）：放行
    - 开发态发现裸 2xx dict/list：直接抛错，尽早暴露（C1 守护）
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.status_code in (204, 304) or getattr(response, "streaming", False):
            return response
        if 200 <= response.status_code < 300:
            content_type = response.headers.get("Content-Type", "")
            if "application/json" not in content_type:      # 非 JSON（健康检查等）放行
                return response
            body = getattr(response, "data", None)
            if isinstance(body, dict) and body.get("status") == "success":
                return response                              # 已包装
            if settings_debug() and body is not None:
                raise RuntimeError(
                    f"[Envelope] {request.method} {request.path} 返回了未包装的 2xx JSON："
                    f"请使用 plane.base.response.success_response（C1）")
            if isinstance(body, (dict, list)) or body is None:
                response.data = {"status": "success", "data": body}
        return response


# ── ⑥ MaintenanceModeMiddleware（最内层，P2 启用开关）────────
class MaintenanceModeMiddleware:
    """维护模式下白名单外统一 503 SERVER_MAINTENANCE（§4.3 / §8.6）。"""

    WHITELIST = ("/api/v1/health/",)

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings as dj_settings
        if getattr(dj_settings, "MAINTENANCE_MODE", False) \
                and not request.path.startswith(self.WHITELIST):
            req_id = current_request_id() or "unknown"
            return JsonResponse(
                {"status": "error",
                 "error": {"code": "SERVER_MAINTENANCE",
                           "message": DEFAULT_MESSAGES["SERVER_MAINTENANCE"],
                           "request_id": req_id}},
                status=HTTP_503_SERVICE_UNAVAILABLE,
                headers={"Retry-After": "300", "X-Request-Id": req_id},
            )
        return self.get_response(request)
