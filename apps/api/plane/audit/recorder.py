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
    """演员快照：id + name + email（BR-02，前端展示唯一口径）。

    接受 User 实例 / dict / 字符串 ID（兼容老调用点）；字符串时按需查 DB
    补 name/email（事务内一致读，cache 是次轮 onboarding 才需要的优化）。
    """
    if actor is None:
        return {}
    if isinstance(actor, dict):
        return {"id": str(actor.get("id", "")), "name": actor.get("name", ""),
                "email": actor.get("email", "")}
    if not isinstance(actor, str):
        actor = str(actor)  # UUID 实例 → 字符串（同 DB pk 口径）
    # 兼容桥：按 ID 反查 User 拿 name/email（不引缓存，避免事务内一致性问题）
    try:
        from plane.db.models import User
        u = User.objects.only("id", "display_name", "email").filter(pk=actor).first()
        if u is not None:
            return {"id": str(u.id), "name": u.display_name, "email": u.email}
    except Exception:  # noqa: BLE001 —— DB 不达时降级到 ID
        pass
    return {"id": actor, "name": "", "email": ""}


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


def _enrich_object(obj: dict | None, object_id: str | None) -> dict:
    """对象快照：id + name/title（AUTH-010 §2.3 展示口径）。

    接受 dict / 带 name·title 的对象 / 字符串 ID；字符串时按常见 model
    路由表查 name（事务内一致读）。
    """
    if not obj and not object_id:
        return {}
    if obj and (obj.get("name") or obj.get("title")):
        return obj
    base = dict(obj or {})
    if object_id and not base.get("name") and not base.get("title"):
        from django.apps import apps
        # 逐表 try：某表无 name/title 字段（FieldError）不阻断后续表查询
        for label in ("db.IssueView", "db.Issue", "db.WorkspaceMember",
                      "db.Project", "db.Department", "db.CustomRole",
                      "db.IdentityProvider", "db.User"):
            try:
                M = apps.get_model(label)
                fields = {f.name for f in M._meta.get_fields()}
                wanted = [f for f in ("name", "title") if f in fields]
                if not wanted:
                    continue
                row = M.objects.only(*wanted).filter(pk=object_id).first()
                if row is not None:
                    base["name"] = getattr(row, wanted[0], None)
                    base["type"] = M._meta.label
                    break
            except Exception:  # noqa: BLE001 —— 单表失败继续下一表
                continue
    if "type" not in base:
        base["type"] = base.get("type", "")
    return base


def record(event_key: str, *, category: str, action: str,
           workspace_id=None, actor=None, obj=None, object_id=None,
           detail: dict | None = None, request=None) -> None:
    """业务侧唯一入口；必须在事务 on_commit 语境（装饰器保证 / 调用方自律）。

    workspace_id=None 仅限系统级事件（BR-15/16）。
    obj / object_id 二选一；都传时合并 id/name。
    """
    from plane.audit.registry import validate_registered

    validate_registered(category, action)  # BR-05（未注册即抛，测试期暴露）
    actor_snapshot = _snapshot_actor(actor)
    object_snapshot = _enrich_object(obj, object_id or (obj or {}).get("id"))
    payload = {
        "event_key": event_key,
        "category": category,
        "action": action,
        "workspace_id": str(workspace_id) if workspace_id else None,
        "actor": actor_snapshot,
        "actor_id": actor_snapshot.get("id"),
        "object": object_snapshot,
        "object_id": object_snapshot.get("id") or object_id,
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


def _record_audit_impl(event: str, *, actor_id: str,
                      object_id: str | None = None,
                      workspace_id=None, **extra) -> None:
    """实现体（web 进程同步执行：threadlocal ip/ua + DB 快照反查）。"""
    category, _, action = event.partition(".")
    action = action or "generic"
    from plane.audit.registry import EVENT_REGISTRY

    if category not in EVENT_REGISTRY or action not in EVENT_REGISTRY.get(category, {}):
        logger.warning("audit.compat_unregistered event=%s", event)
        category, action = "system", "maintenance"
    from plane.audit.middleware import current_request
    record(
        hashlib.sha256(f"{event}:{actor_id}:{object_id}:{uuid.uuid4()}".encode())
        .hexdigest()[:80],
        category=category, action=action,
        workspace_id=str(workspace_id) if workspace_id else None,
        actor=str(actor_id) if actor_id else None,
        obj=({"id": str(object_id)} if object_id else {}),
        object_id=str(object_id) if object_id else None,
        detail=dict(extra) or None,
        request=current_request(),
    )


class _DelayShim:
    """``record_audit.delay(...)`` 兼容层：on_commit 里调 ``.delay(...)`` 的
    既有调用点直接同步执行（web 进程，threadlocal/DB 可用）。"""
    def __call__(self, *args, **kwargs):
        return _record_audit_impl(*args, **kwargs)

    def delay(self, *args, **kwargs):
        return _record_audit_impl(*args, **kwargs)


record_audit = _DelayShim()  # type: ignore[assignment]
