"""跨中间件共享的请求上下文（contextvar）。

gunicorn gthread / async 安全；定义在框架层以便 handlers 与中间件
共享同一份绑定键（避免在 handlers 里再次独立初始化）。具体写入时机
见 plane.base.middleware（① 号 RequestIDMiddleware / ④ 号 AuditContextMiddleware）。
"""
from __future__ import annotations

import contextvars
from typing import Any

# ── ① 号 RequestIDMiddleware 写入；handlers / 日志读取 ──
_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)

# ── ④ 号 AuditContextMiddleware 写入；模型 save() / Celery 读取审计字段 ──
_actor_var: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "actor", default=None
)


def current_request_id() -> str | None:
    """当前请求的 ULID request_id；未绑定时返回 None。"""
    return _request_id_var.get() or None


def current_actor() -> dict[str, Any]:
    """当前请求的 actor 上下文（user_id / ip / user_agent）。"""
    return _actor_var.get() or {}


def bind_request_id(value: str) -> contextvars.Token:
    """由 RequestIDMiddleware 调用；外部测试可借此手工注入。"""
    return _request_id_var.set(value)


def bind_actor(value: dict[str, Any]) -> contextvars.Token:
    """由 AuditContextMiddleware 调用。"""
    return _actor_var.set(value)


def reset_request_id(token: contextvars.Token) -> None:
    _request_id_var.reset(token)


def reset_actor(token: contextvars.Token) -> None:
    _actor_var.reset(token)
