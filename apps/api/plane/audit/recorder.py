"""审计埋点唯一入口（AUTH-010 §4.3，Sprint-8 R4）。

record()：显式 event_key（业务侧可控幂等语义）+ 注册表校验 + 黑名单过滤
（BR-11）+ on_commit 投递（BR-09）。
audited()：装饰器形态（事件源接入零样板）。
兼容桥 record_audit：R1~R3 占位签名（event 字符串 + actor_id/object_id）
——内部生成唯一 event_key 转发 record()，全部既有埋点自动接入真管道。
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid
from functools import wraps

from celery import shared_task
from django.db import transaction

logger = logging.getLogger("plane.audit")

#: BR-11 黑名单（唯一过滤口径）：键名不区分大小写命中即整键剔除并告警
SENSITIVE_KEYS = re.compile(r"password|secret|token|assertion|private_key|webhook_url", re.I)


class UnregisteredEvent(Exception):
    """未注册事件（BR-05）——worker 拒写直接入 DLQ。"""


def blacklist_filter(detail: dict) -> dict:
    cleaned: dict = {}
    for k, v in detail.items():
        if SENSITIVE_KEYS.search(str(k)):
            logger.warning("audit.br11_violation key=%s", k)
            continue
        cleaned[k] = v
    return cleaned


def _snapshot_actor(actor) -> dict:
    if actor is None:
        return {}
    if isinstance(actor, str):
        return {"id": actor, "name": actor}
    if isinstance(actor, dict):
        return actor
    return {"id": str(actor.id), "name": getattr(actor, "display_name", ""),
            "email": getattr(actor, "email", "")}


def _client_ip(request) -> str | None:
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _user_agent(request) -> str:
    if request is None:
        return ""
    return str(request.META.get("HTTP_USER_AGENT", ""))[:255]


def record(event_key: str, *, category: str, action: str,
           workspace_id=None, actor=None, obj=None,
           detail: dict | None = None, request=None) -> None:
    """业务侧唯一入口；必须在事务 on_commit 语境（装饰器保证 / 调用方自律）。

    workspace_id=None 仅限系统级事件（BR-15/16）。
    """
    from plane.audit.registry import validate_registered

    validate_registered(category, action)  # BR-05（未注册即抛，测试期暴露）
    actor_snapshot = _snapshot_actor(actor)
    payload = {
        "event_key": event_key,
        "category": category,
        "action": action,
        "workspace_id": str(workspace_id) if workspace_id else None,
        "actor": actor_snapshot,
        "actor_id": actor_snapshot.get("id"),
        "object": obj or {},
        "detail": blacklist_filter(detail or {}),  # BR-11
        "ip": _client_ip(request),
        "user_agent": _user_agent(request),
    }
    transaction.on_commit(lambda: _enqueue(payload))


def _enqueue(payload: dict) -> None:
    from plane.bgtasks.audit_record import audit_record

    try:
        audit_record.delay(payload)
    except Exception as exc:  # noqa: BLE001 —— broker 不可达不阻塞业务（BR-10）
        logger.error("audit.enqueue_failed key=%s err=%s", payload["event_key"], exc)


def audited(category: str, action: str, *, event_key=None,
            object=None, detail=None):  # noqa: A002 —— 对齐规格 §4.3 签名
    """埋点装饰器：函数返回后 on_commit 投递（事务回滚不产生事件，BR-09）。"""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            key = event_key or hashlib.sha256(
                f"{category}.{action}:{uuid.uuid4()}".encode()).hexdigest()[:80]
            record(key, category=category, action=action,
                   workspace_id=kwargs.get("workspace_id") or _ws_id_from(result),
                   actor=kwargs.get("actor"), obj=object(**kwargs) if callable(object) else None,
                   detail=detail(**kwargs) if callable(detail) else None)
            return result

        return wrapper

    return decorator


def _ws_id_from(result) -> str | None:
    ws = getattr(result, "workspace_id", None) or getattr(result, "workspace", None)
    if ws is None:
        return None
    return str(getattr(ws, "id", ws))


@shared_task(ignore_result=True)
def record_audit(event: str, *, actor_id: str,
                 object_id: str | None = None,
                 workspace_id=None, **extra) -> None:
    """兼容桥（task 形态）：R1~R3 占位签名 → 真管道（.delay 调用点零改动）。

    event 形如 "department.created"（category.action）；event_key 每次唯一
    （兼容埋点语义为「每次发生各落一行」，非幂等去重对象）。
    """
    category, _, action = event.partition(".")
    action = action or "generic"
    from plane.audit.registry import EVENT_REGISTRY

    if category not in EVENT_REGISTRY or action not in EVENT_REGISTRY.get(category, {}):
        # 兼容面未注册的事件按 system.generic 兜底（不丢事件），并告警登记
        logger.warning("audit.compat_unregistered event=%s", event)
        category, action = "system", "maintenance"
    record(
        hashlib.sha256(f"{event}:{actor_id}:{object_id}:{uuid.uuid4()}".encode())
        .hexdigest()[:80],
        category=category, action=action,
        workspace_id=workspace_id,
        actor={"id": actor_id},
        obj=({"id": str(object_id)} if object_id else {}),
        detail=dict(extra) or None,
    )
