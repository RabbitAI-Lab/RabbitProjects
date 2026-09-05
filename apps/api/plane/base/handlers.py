"""全局异常处理器 —— 一切异常收敛为 §4.2 错误信封，无例外。"""
from __future__ import annotations

import logging
import re
from typing import Any

from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.db import DatabaseError, IntegrityError, OperationalError
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import (
    AuthenticationFailed,
    MethodNotAllowed,
    NotAuthenticated,
    NotFound,
    ParseError,
    PermissionDenied,
    Throttled,
    UnsupportedMediaType,
    ValidationError,
)
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from plane.base.error_codes import DEFAULT_MESSAGES
from plane.base.request_context import current_request_id

logger = logging.getLogger("plane.api.errors")

#: IntegrityError 约束名 → (错误码, 冲突字段) 映射；新增约束在此登记
CONSTRAINT_MAP = {
    "uniq_workspace_slug_alive":        ("RESOURCE_ALREADY_EXISTS", "slug"),
    "uniq_project_identifier_per_workspace": ("RESOURCE_ALREADY_EXISTS", "identifier"),
    "uniq_issue_sequence_per_project":  ("RESOURCE_CONFLICT", "sequence_id"),
    "uniq_issue_assignee":              ("VALIDATION_ERROR", "assignee_ids"),
    "uniq_issue_label":                 ("VALIDATION_ERROR", "label_ids"),
    "uniq_state_name_per_project_type": ("RESOURCE_ALREADY_EXISTS", "name"),
    "chk_issue_start_before_target":    ("VALIDATION_INVALID_DATE_RANGE", "target_date"),
    "chk_issue_link_no_self":           ("VALIDATION_ERROR", "related_issue_id"),
}


def envelope_exception_handler(exc: Exception, context: dict[str, Any]) -> Response:
    request_id = current_request_id() or "unknown"

    # ── 第 10 步前置判定：DRF 不处理的异常（未捕获 Exception、IntegrityError、
    #    OperationalError、Django 原生异常）drf_handler 返回 None ──
    response = drf_exception_handler(exc, context)

    # ── 第 1 步：AppException（BusinessError）──
    if getattr(exc, "error_code", None):
        response = Response(status=getattr(exc, "http_status", status.HTTP_400_BAD_REQUEST))
        response.data = _error_body(
            exc.error_code, request_id,
            message=getattr(exc, "detail_message", None),
            details=getattr(exc, "extra_details", None),
            doc_url=getattr(exc, "doc_url", None),
        )

    # ── 第 2 步：DRF ValidationError → details[] 平铺（嵌套点号路径）──
    elif isinstance(exc, ValidationError):
        response = Response(status=status.HTTP_400_BAD_REQUEST)
        response.data = _error_body(
            "VALIDATION_ERROR", request_id,
            message=DEFAULT_MESSAGES["VALIDATION_ERROR"],
            details=_flatten_validation_detail(exc.detail),
        )

    elif isinstance(exc, ParseError):  # 第 2 步姊妹：JSON 解析失败
        response = Response(status=status.HTTP_400_BAD_REQUEST)
        response.data = _error_body("VALIDATION_INVALID_JSON", request_id)

    # ── 第 3-4 步：认证类 ──
    elif isinstance(exc, NotAuthenticated):
        response = Response(status=status.HTTP_401_UNAUTHORIZED)
        response.data = _error_body("AUTH_REQUIRED", request_id)
    elif isinstance(exc, AuthenticationFailed):
        # 认证层（AUTH-001）在异常上挂 error_code 指定子码
        code = getattr(exc, "error_code", "AUTH_INVALID_CREDENTIALS")
        response = Response(status=status.HTTP_401_UNAUTHORIZED)
        response.data = _error_body(code, request_id)

    # ── 第 5 步：权限类（DRF / Django 原生含 CSRF）──
    elif isinstance(exc, PermissionDenied):
        # DRF SessionAuthentication.enforce_csrf 失败时抛 exceptions.PermissionDenied
        # （rest_framework.exceptions，区别于 django.core.exceptions.PermissionDenied），
        # detail 形如 "CSRF Failed: ..."；其余业务级越权走默认 PERM_DENIED。
        detail = getattr(exc, "detail", None)
        detail_str = str(detail) if detail is not None else ""
        if detail_str.startswith("CSRF Failed"):
            response = Response(status=status.HTTP_403_FORBIDDEN)
            response.data = _error_body("AUTH_CSRF_FAILED", request_id,
                                        message=detail_str)
        else:
            code = getattr(exc, "error_code", "PERM_DENIED")  # Permission 类可附带具体码
            response = Response(status=status.HTTP_403_FORBIDDEN)
            response.data = _error_body(code, request_id)
    elif isinstance(exc, DjangoPermissionDenied):  # CSRF 中间件抛的是 Django 原生类
        response = Response(status=status.HTTP_403_FORBIDDEN)
        response.data = _error_body("AUTH_CSRF_FAILED", request_id)

    # ── 第 6 步：404（资源不存在 / 权限不可见，二者同构）──
    # NotFound 是 DRF APIException 的子类，既不是 Django 的 Http404 也不是
    # ObjectDoesNotExist——漏判会让它保持 drf_exception_handler 产出的
    # {"detail": ...} 原样返回，违反 C1。而 _access.py 的越权 404 正是走这条路，
    # 是全 API 命中率最高的错误出口。
    elif isinstance(exc, (NotFound, Http404, ObjectDoesNotExist)):
        response = Response(status=status.HTTP_404_NOT_FOUND)
        response.data = _error_body("RESOURCE_NOT_FOUND", request_id)

    # ── 第 7 步：方法 / 媒体类型 ──
    elif isinstance(exc, MethodNotAllowed):
        response = Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)
        response.data = _error_body("VALIDATION_ERROR", request_id,
                                    details=[{"field": "__method__",
                                              "code": "METHOD_NOT_ALLOWED",
                                              "message": str(exc.detail)}])
    elif isinstance(exc, UnsupportedMediaType):
        response = Response(status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
        response.data = _error_body("VALIDATION_UNSUPPORTED_MEDIA_TYPE", request_id)

    # ── 第 8 步：限流 ──
    elif isinstance(exc, Throttled):
        wait = int(getattr(exc, "wait", 1) or 1)
        response = Response(status=status.HTTP_429_TOO_MANY_REQUESTS)
        response.headers["Retry-After"] = str(wait)
        response.data = _error_body(
            "RATE_LIMIT_EXCEEDED", request_id,
            message=f"请求过于频繁，请在 {wait} 秒后重试",
            details=[{"field": "retry_after", "code": "RETRY_AFTER", "message": str(wait)}],
        )

    # ── 第 9 步：数据库完整性 / 连接 ──
    elif isinstance(exc, IntegrityError):
        code, field = _parse_constraint(str(exc))
        http = status.HTTP_409_CONFLICT if code == "RESOURCE_ALREADY_EXISTS" else status.HTTP_400_BAD_REQUEST
        response = Response(status=http)
        response.data = _error_body(code, request_id,
                                    details=[{"field": field, "code": "UNIQUE" if http == 409 else "INVALID",
                                              "message": DEFAULT_MESSAGES.get(code, "数据约束冲突")}])
        logger.warning("integrity_error request_id=%s constraint=%s", request_id, _constraint_name(str(exc)))
    elif isinstance(exc, (OperationalError, DatabaseError)):
        # OperationalError 是连接级（连接池耗尽、服务不可达），DatabaseError 是其父类（含编程错误）
        logger.exception("database_error request_id=%s", request_id)
        response = Response(status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        response.data = _error_body("SERVER_DATABASE_ERROR", request_id)

    # ── 第 10 步：其余未捕获异常 → 500（堆栈只进日志，UT-04 脱敏断言）──
    elif response is None:
        logger.exception("unhandled_exception request_id=%s path=%s",
                         request_id, getattr(context.get("request"), "path", "?"))
        response = Response(status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        response.data = _error_body("SERVER_ERROR", request_id,
                                    message=DEFAULT_MESSAGES["SERVER_ERROR"])

    response.headers["X-Request-Id"] = request_id
    return response


def _error_body(code: str, request_id: str, *, message: str | None = None,
                details: list[dict] | None = None,
                doc_url: str | None = None) -> dict:
    body = {"status": "error", "error": {"code": code,
                                         "message": message or DEFAULT_MESSAGES.get(code, "请求失败"),
                                         "request_id": request_id}}
    if details:
        body["error"]["details"] = details[:20]        # BR 截断上限
    if doc_url:
        body["error"]["doc_url"] = doc_url
    return body


def _flatten_validation_detail(detail, prefix: str = "") -> list[dict]:
    """DRF ValidationError.detail → [{field, code, message}]，嵌套用点号路径。

    例：{"a": {"0": [{"b": [ErrorDetail(...REQUIRED)]}]}}
      → [{"field": "a.0.b", "code": "REQUIRED", "message": "该项为必填项"}]
    """
    from rest_framework.exceptions import ErrorDetail

    items: list[dict] = []
    if isinstance(detail, dict):
        for key, value in detail.items():
            items += _flatten_validation_detail(value, f"{prefix}{key}" if not prefix else f"{prefix}.{key}")
    elif isinstance(detail, list):
        for i, value in enumerate(detail):
            if isinstance(value, ErrorDetail):          # 非 positional 情形直接是错误串
                items += _flatten_validation_detail(value, prefix)
            else:
                items += _flatten_validation_detail(value, f"{prefix}.{i}" if prefix else str(i))
    else:  # ErrorDetail
        items.append({"field": prefix or "__all__",
                      "code": getattr(detail, "code", "INVALID"),
                      "message": str(detail)})
    return items


def _constraint_name(db_message: str) -> str | None:
    matched = re.search(r'constraint "(\w+)"', db_message) or re.search(r"ON CONSTRAINT (\w+)", db_message)
    return matched.group(1) if matched else None


def _parse_constraint(db_message: str) -> tuple[str, str]:
    name = _constraint_name(db_message)
    if name and name in CONSTRAINT_MAP:
        return CONSTRAINT_MAP[name]
    return ("VALIDATION_ERROR", "__all__") if name else ("VALIDATION_ERROR", "__all__")
