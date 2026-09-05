"""Activity 幂等消费 Worker（TASK-010 §4.3.2）——at-least-once 下的三层去重。

去重序：① Redis 完成标记（24h 快路径）→ ② DB 同键查询（终局事实源）→
③ Redis 处理锁（占用失败 retry(310) 等锁过期——worker 硬崩溃锁残留窗口下
消息不得丢）；落库成功后才写 ①（占位绝不先于落库）。
"""
from __future__ import annotations

import hashlib
import logging
import uuid

from celery import shared_task
from django.core.cache import cache
from kombu.exceptions import OperationalError  # noqa: F401 —— 供调用方按类型重试

from plane.db.models import IssueActivity

logger = logging.getLogger(__name__)

LOCK_TTL = 300
DONE_TTL = 86400


def build_event_key(payload: dict) -> str:
    """sha256(verb|issue_id|actor_id|epoch)——COLLAB-001 dedup_key 同范式。"""
    raw = "|".join(str(payload[k]) for k in ("verb", "issue_id", "actor_id", "epoch"))
    return hashlib.sha256(raw.encode()).hexdigest()


@shared_task(bind=True, max_retries=3, acks_late=True, acks_on_failure_or_timeout=False)
def issue_activity(self, payload: dict) -> None:
    """payload = {issue_id, actor_id, verb, epoch, before, after, comment?}。

    失败不 ack（reject 交 DLX 路由 activity.dlq，BR-08）；退避 1s/4s/16s 显式
    countdown（celery retry_backoff 不接受序列）。
    """
    event_key = build_event_key(payload)
    lock_key, done_key = f"activity-lock:{event_key}", f"activity-dedup:{event_key}"
    if cache.get(done_key):  # ① 完成标记（读）
        return
    if IssueActivity.objects.filter(  # ② DB 同键：终局事实源（Redis 过期后仍准确）
            issue_id=payload["issue_id"], actor_id=payload["actor_id"],
            epoch=payload["epoch"], verb=payload["verb"]).exists():
        return
    if not cache.add(lock_key, 1, timeout=LOCK_TTL):  # ③ 处理锁
        raise self.retry(countdown=LOCK_TTL + 10)
    try:
        from plane.db.services.activity_builder import build_activities

        rows = build_activities(
            issue_id=payload["issue_id"], actor_id=payload["actor_id"],
            before=payload.get("before") or {}, after=payload.get("after") or {},
            epoch=payload["epoch"])
        if payload.get("comment") and rows:
            rows[0].comment = payload["comment"]
        if rows:
            IssueActivity.objects.bulk_create(rows, batch_size=100)
        cache.set(done_key, 1, timeout=DONE_TTL)  # 仅落库成功后写 ①
    except Exception as exc:  # noqa: BLE001
        cache.delete(lock_key)  # 失败释放锁：重试/重放可重入
        if self.request.retries >= self.max_retries:
            raise  # 轨道走完 → reject 入 dlq（task_failure 信号落元数据）
        raise self.retry(countdown=4**self.request.retries, exc=exc) from exc


def enqueue_activity(*, issue_id: uuid.UUID, actor_id: uuid.UUID, verb: str,
                     epoch: float, before: dict, after: dict, comment: str = "") -> None:
    """写路径投递入口（on_commit 回调中调用；broker 不可用时降级直写不阻塞主请求）。"""
    payload = {
        "issue_id": str(issue_id), "actor_id": str(actor_id), "verb": verb,
        "epoch": epoch, "before": before, "after": after, "comment": comment,
    }
    try:
        issue_activity.delay(payload)
    except Exception:  # noqa: BLE001 —— 降级：同步落库（保「业务成功可追溯」底线）
        from plane.db.services.activity_builder import build_activities

        rows = build_activities(
            issue_id=issue_id, actor_id=actor_id, before=before, after=after, epoch=epoch)
        if comment and rows:
            rows[0].comment = comment
        if rows:
            IssueActivity.objects.bulk_create(rows, batch_size=100)
        logger.warning("issue_activity.dispatch_fallback_sync issue_id=%s", issue_id)


@shared_task(bind=True, max_retries=3, retry_backoff=True)
def record_activity_row(
    self,
    issue_id: str,
    actor_id: str | None,
    verb: str,
    epoch: float,
    field: str | None = None,
    old_value: str | None = None,
    new_value: str | None = None,
    old_identifier: str | None = None,
    new_identifier: str | None = None,
    comment: str = "",
) -> None:
    """单行 Activity 异步落库（主写路径统一管道，用户裁决 2026-09-05 全量异步化）。

    行级幂等：(issue, actor, verb, epoch, field, old_id, new_id) 全键 exists 跳过
    ——at-least-once 重投不产生重复行。
    """
    actor_uuid = uuid.UUID(actor_id) if actor_id else None
    if IssueActivity.objects.filter(
        issue_id=issue_id, actor_id=actor_uuid, verb=verb, epoch=epoch,
        field=field, old_identifier=old_identifier, new_identifier=new_identifier,
    ).exists():
        return
    try:
        IssueActivity.objects.create(
            issue_id=issue_id, actor_id=actor_uuid, verb=verb, field=field,
            old_value=old_value, new_value=new_value,
            old_identifier=old_identifier, new_identifier=new_identifier,
            comment=comment or "", epoch=epoch)
    except Exception as exc:  # noqa: BLE001 —— TASK-010 DLQ 兜底
        raise self.retry(countdown=4**self.request.retries, exc=exc) from exc


def enqueue_activity_row(*, issue_id, actor, verb, field=None, old=None, new=None,
                         old_identifier=None, new_identifier=None, comment="",
                         epoch=None) -> None:
    """同步落库点的统一投递入口：broker 可用走 Worker，不可用降级同步直写
    （保「业务成功可追溯」底线——降级仅在投递失败时发生，log warning）。"""
    import time as _time

    ep = epoch if epoch is not None else _time.time() * 1000
    kwargs = dict(
        issue_id=str(issue_id), actor_id=str(actor.id) if getattr(actor, "id", None) else None,
        verb=verb, epoch=ep, field=field,
        old_value=str(old) if old is not None else None,
        new_value=str(new) if new is not None else None,
        old_identifier=str(old_identifier) if old_identifier is not None else None,
        new_identifier=str(new_identifier) if new_identifier is not None else None,
        comment=comment or "")
    try:
        record_activity_row.delay(**kwargs)
    except Exception:  # noqa: BLE001 —— 降级同步
        record_activity_row(**kwargs)
        logger.warning("activity.dispatch_fallback_sync issue_id=%s field=%s", issue_id, field)
