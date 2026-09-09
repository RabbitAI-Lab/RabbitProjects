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
    try:
        _write_activity_row(
            issue_id=issue_id, actor_id=actor_id, verb=verb, epoch=epoch, field=field,
            old_value=old_value, new_value=new_value,
            old_identifier=old_identifier, new_identifier=new_identifier,
            comment=comment)
        if field == "state" and new_identifier:
            _dispatch_automation_state_changed(
                issue_id=issue_id, actor_id=actor_id, epoch=epoch,
                old_identifier=old_identifier, new_identifier=new_identifier)
    except Exception as exc:  # noqa: BLE001 —— TASK-010 DLQ 兜底
        raise self.retry(countdown=4**self.request.retries, exc=exc) from exc


def _dispatch_automation_state_changed(
    *, issue_id: str, actor_id: str | None, epoch: float,
    old_identifier: str | None, new_identifier: str | None) -> None:
    """S7 A#3 债收口：state_changed 生产事件源投递（WF-003 §2.2 唯一缺口）。

    行已落库（幂等键去重后）才投递；引擎侧防循环三闸 + event_gate 已备
    （sprint-7 R3 35 断言）。任何投递异常不得影响 Activity 主路径。
    """
    try:
        from plane.db.models import Issue, State
        issue = Issue.objects.select_related("project").filter(pk=issue_id).first()
        if issue is None:
            return
        to_state = State.objects.filter(pk=new_identifier).values_list("group", flat=True).first()
        from_group = (State.objects.filter(pk=old_identifier).values_list("group", flat=True).first()
                      if old_identifier else None)
        from plane.workflow.automation_tasks import automation_match
        automation_match.delay({
            "type": "state_changed",
            "project_id": str(issue.project_id),
            "issue_id": str(issue.id),
            "epoch": epoch,
            "payload": {
                "from_state_id": old_identifier,
                "to_state_id": new_identifier,
                "from_group": from_group,
                "to_group": to_state,
                "actor_id": actor_id,
            },
        })
    except Exception:  # noqa: BLE001 —— 总线投递失败不阻断 Activity 主路径
        import logging
        logging.getLogger(__name__).warning(
            "automation_dispatch_failed issue=%s", issue_id, exc_info=True)


def _write_activity_row(
    *, issue_id: str, actor_id: str | None, verb: str, epoch: float,
    field: str | None = None, old_value: str | None = None, new_value: str | None = None,
    old_identifier: str | None = None, new_identifier: str | None = None,
    comment: str = "", batch: bool = False,
) -> None:
    """行级幂等基座（BOARD-004 提取为模块级共享）：全键 exists 跳过后单行落库。

    COLLAB-004 T3-11 Worker 尾部扇出：落库成功即按 field 映射发布实时事件
    （issue.updated / issue.state.changed / board.moved + activity.created 水位锚），
    ``batch=True`` 时逐实体附 payload.batch_id=epoch（BR-15）。扇出尽力而为——
    内部吞异常，不阻断落库；重复投递（exists 命中）不重发。
    """
    from plane.bgtasks.event_publisher import publish_activity_events

    actor_uuid = uuid.UUID(actor_id) if actor_id else None
    if IssueActivity.objects.filter(
        issue_id=issue_id, actor_id=actor_uuid, verb=verb, epoch=epoch,
        field=field, old_identifier=old_identifier, new_identifier=new_identifier,
    ).exists():
        return
    row = IssueActivity.objects.create(
        issue_id=issue_id, actor_id=actor_uuid, verb=verb, field=field,
        old_value=old_value, new_value=new_value,
        old_identifier=old_identifier, new_identifier=new_identifier,
        comment=comment or "", epoch=epoch)
    publish_activity_events(
        issue_id=issue_id, actor_id=actor_id, verb=verb, field=field,
        activity_id=str(row.id), activity_created_at=row.created_at,
        old_identifier=str(old_identifier) if old_identifier else None,
        new_identifier=str(new_identifier) if new_identifier else None,
        batch_id=epoch if batch else None)


@shared_task(bind=True, max_retries=3, retry_backoff=True)
def record_activity_batch(self, payload: dict) -> None:
    """批量载荷（BOARD-004 BR-05）：``{"batch": [单行 kwargs, ...], "comment": str}``。

    同批共享 epoch、逐条落库（每任务各有时间线）；行级幂等基座不动——每行独立
    走 :func:`_write_activity_row` 的全键 exists 检查，幂等键 sha256(verb|issue|
    actor|epoch) 逐行独立天然成立（TASK-010 BR-07）。顶层 ``comment``（批量摘要，
    前缀 ``batch:``）填充未带 comment 的行。失败整批重试：已落库行被行级幂等
    跳过，at-least-once 不产生重复。
    """
    fallback_comment = payload.get("comment") or ""
    try:
        for row in payload.get("batch") or []:
            _write_activity_row(**{**row, "comment": row.get("comment") or fallback_comment},
                                batch=True)
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


def enqueue_activity_rows(*, rows: list[dict], comment: str = "") -> None:
    """批量投递入口（BOARD-004 BR-05：出口统一单次投递 batch 载荷）。

    ``rows`` 为 :func:`record_activity_row` 同款单行 kwargs（同批共享 epoch，
    由批量入口生成）；broker 不可用时降级逐行同步直写（保「业务成功可追溯」
    底线，与单行入口同口径）。
    """
    if not rows:
        return
    payload = {"batch": rows, "comment": comment}
    try:
        record_activity_batch.delay(payload)
    except Exception:  # noqa: BLE001 —— 降级同步
        record_activity_batch(payload)
        logger.warning("activity.batch.dispatch_fallback_sync rows=%d", len(rows))
