"""死信补偿（TASK-010 §4.3.4）：task_failure 信号落 Redis hash 元数据 + 重放/drain。"""
from __future__ import annotations

import json
import logging
import uuid

from celery.exceptions import MaxRetriesExceededError
from celery.signals import task_failure

from plane.bgtasks.issue_activity import build_event_key, issue_activity

logger = logging.getLogger(__name__)

DLQ_META_TTL = 7 * 86400
DLQ_KEY_PREFIX = "activity:dlq:"
#: 堆积告警阈值（§4.2.2：>100 条触发 SERVER_QUEUE_ERROR 口径）
DLQ_ALERT_THRESHOLD = 100


def _redis():
    import django_redis  # type: ignore[import-not-found]

    return django_redis.get_redis_connection("default")


def _default_redis():
    """django_redis 未配置时直连 REDIS_URL（dev 环境 cache 后端可能是 LocMem）。"""
    import os

    import redis

    return redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))


def dlq_client():
    try:
        return _redis()
    except Exception:  # noqa: BLE001
        return _default_redis()


@task_failure.connect
def record_dead_letter(sender=None, task_id=None, exception=None, args=None, **kwargs):
    """任务最终失败被 reject 入 activity.dlq 时同步写元数据（TTL 7 天，零 DDL）。

    retries 由异常类型推导（信号载荷不含该值）：MaxRetriesExceededError=3（轨道
    走完），其余（不可恢复快速失败）=0。
    """
    if getattr(sender, "name", None) != "plane.bgtasks.issue_activity.issue_activity":
        return
    (payload,) = args or (None,)
    if not payload:
        return
    root = getattr(exception, "__cause__", None) or exception
    from django.utils import timezone

    try:
        r = dlq_client()
        message_id = str(task_id or uuid.uuid4())
        r.hset(f"{DLQ_KEY_PREFIX}{message_id}", mapping={
            "event_key": build_event_key(payload),
            "payload": json.dumps(payload, ensure_ascii=False),
            "error_summary": f"{type(root).__name__}: {root}",
            "retries": getattr(sender, "max_retries", 3)
            if isinstance(exception, MaxRetriesExceededError) else 0,
            "first_failed_at": timezone.now().isoformat(),
        })
        r.expire(f"{DLQ_KEY_PREFIX}{message_id}", DLQ_META_TTL)
    except Exception:  # noqa: BLE001 —— 元数据落库失败不放大故障，log 告警
        logger.exception("activity_dlq.record_failed task_id=%s", task_id)


def list_dead_letters(limit: int = 100) -> list[dict]:
    r = dlq_client()
    out = []
    for key in r.scan_iter(match=f"{DLQ_KEY_PREFIX}*"):
        mid = key.decode().rsplit(":", 1)[-1] if isinstance(key, bytes) else str(key).rsplit(":", 1)[-1]
        h = r.hgetall(key)
        if not h:
            continue
        dec = {k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
               for k, v in h.items()}
        out.append({
            "id": mid,
            "event_key": dec.get("event_key", "")[:12] + "…",
            "queue": "activity.dlq",
            "error_summary": dec.get("error_summary", ""),
            "retries": int(dec.get("retries", 0)),
            "first_failed_at": dec.get("first_failed_at"),
            "_payload": dec.get("payload", ""),
        })
    out.sort(key=lambda x: x["first_failed_at"] or "", reverse=True)
    return out[:limit]


def replay_dead_letter(message_id: str) -> dict | None:
    """重放：hash 读 payload → 三层去重前置判定（已落库则 dedup_skipped）→ re-dispatch。

    hash 先删（列表即刻不再显示）；队列本体消息经 drain 异步清除（最终一致）。
    """
    r = dlq_client()
    key = f"{DLQ_KEY_PREFIX}{message_id}"
    h = r.hgetall(key)
    if not h:
        return None
    dec = {k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
           for k, v in h.items()}
    payload = json.loads(dec.get("payload") or "{}")
    r.delete(key)
    from plane.db.models import IssueActivity

    dedup_skipped = IssueActivity.objects.filter(
        issue_id=payload.get("issue_id"), actor_id=payload.get("actor_id"),
        epoch=payload.get("epoch"), verb=payload.get("verb")).exists()
    if not dedup_skipped:
        issue_activity.delay(payload)
    return {"message_id": message_id, "replayed": not dedup_skipped, "dedup_skipped": dedup_skipped}


def discard_dead_letter(message_id: str) -> bool:
    r = dlq_client()
    key = f"{DLQ_KEY_PREFIX}{message_id}"
    existed = bool(r.exists(key))
    r.delete(key)
    if existed:  # 丢弃留痕（P2 结构化日志；P3 迁 AUTH-010 AuditLog）
        logger.warning("activity_dlq.discarded message_id=%s", message_id)
    return existed
