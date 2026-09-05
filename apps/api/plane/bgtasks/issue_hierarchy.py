"""层级操作 Activity 任务（TASK-004 §4.3.6；TASK-010 幂等管道的前置钩子）。

event_key = sha256(verb + issue_id + actor_id + epoch) 与 TASK-010 BR-07 同键空间。
本迭代 Worker 端以 DB 同键查询兜底幂等（(verb, issue, actor, epoch) 命中即跳过）；
Redis SETNX 快路径与 DLQ 兜底随 TASK-010 §4.3.2 统一接入。
epoch 由 Service 在动作入口生成并随载荷传入（BR-04：Worker 不得自行取值）。
"""

from __future__ import annotations

import hashlib
import logging
import uuid

from celery import shared_task

from plane.db.models import Issue, IssueActivity

logger = logging.getLogger(__name__)


def _event_key(verb: str, issue_id: str, actor_id: str, epoch: float) -> str:
    return hashlib.sha256(f"{verb}|{issue_id}|{actor_id}|{epoch}".encode()).hexdigest()


def _parent_label(pid: str) -> str | None:
    if not pid:
        return None
    row = Issue.objects.filter(pk=pid).values_list("name", "sequence_id", "project__identifier").first()
    return f"{row[0]} {row[2]}-{row[1]}" if row else None


@shared_task(bind=True, max_retries=3, retry_backoff=True)
def record_parent_change(
    self,
    issue_id: str,
    actor_id: str,
    epoch: float,
    old_parent_id: str = "",
    new_parent_id: str = "",
) -> None:
    """挂载/移动的 Activity（verb=updated、field=parent，old/new_identifier 落库）。"""
    actor_uuid = uuid.UUID(actor_id)  # Celery 只传 ID（§5 统一约束）；DB 查询需 UUID
    if IssueActivity.objects.filter(verb="updated", issue_id=issue_id, actor_id=actor_uuid, epoch=epoch).exists():
        return
    try:
        issue = Issue.objects.filter(pk=issue_id).first()
        if issue is None:
            logger.warning("issue_hierarchy.record_parent_change issue gone id=%s", issue_id)
            return
        IssueActivity.objects.create(
            issue=issue,
            actor_id=actor_uuid,
            verb="updated",
            field="parent",
            old_value=_parent_label(old_parent_id),
            new_value=_parent_label(new_parent_id),
            old_identifier=old_parent_id or None,
            new_identifier=new_parent_id or None,
            comment="更新了 父工作项",
            epoch=epoch,
        )
    except Exception as exc:  # noqa: BLE001 —— worker 侧重试语义，TASK-010 接 DLQ
        raise self.retry(countdown=4**self.request.retries) from exc


@shared_task(bind=True, max_retries=3)
def record_delete(self, issue_id: str, actor_id: str, deleted_count: int, epoch: float) -> None:
    """删除 Activity（verb=deleted，comment 携带级联数量）。issue 已软删，仅存 ID。"""
    actor_uuid = uuid.UUID(actor_id)
    if IssueActivity.objects.filter(verb="deleted", issue_id=issue_id, actor_id=actor_uuid, epoch=epoch).exists():
        return
    try:
        IssueActivity.objects.create(
            issue_id=issue_id,
            actor_id=actor_uuid,
            verb="deleted",
            field="parent",
            comment=(f"删除了任务（含 {deleted_count - 1} 个子任务）" if deleted_count > 1 else "删除了任务"),
            epoch=epoch,
        )
    except Exception as exc:  # noqa: BLE001
        raise self.retry(countdown=4**self.request.retries) from exc


@shared_task(bind=True, max_retries=3, retry_backoff=True)
def record_duplicate(
    self, root_id: str, source_id: str, actor_id: str, total: int, source_key: str, epoch: float
) -> None:
    """复制创建的系统事件（TASK-009 §4.3.1）：「由 RBT-12 复制创建（共 N 个任务）」。"""
    if IssueActivity.objects.filter(
        issue_id=root_id, actor_id=uuid.UUID(actor_id), verb="created", field="parent", epoch=epoch
    ).exists():
        return
    try:
        IssueActivity.objects.create(
            issue_id=root_id,
            actor_id=uuid.UUID(actor_id),
            verb="created",
            field="parent",
            comment=f"由 {source_key} 复制创建（共 {total} 个任务）",
            epoch=epoch,
        )
    except Exception as exc:  # noqa: BLE001 —— TASK-010 接 DLQ
        raise self.retry(countdown=4**self.request.retries) from exc


@shared_task(bind=True, max_retries=3, retry_backoff=True)
def record_archive(
    self, issue_id: str, actor_id: str, count: int, archived_at: str, epoch: float
) -> None:
    """归档 Activity（TASK-009 §4.3.2；幂等：count=0 的重复归档不重复投递——视图层跳过）。"""
    if IssueActivity.objects.filter(
        issue_id=issue_id, actor_id=uuid.UUID(actor_id), verb="updated", field="archived_at", epoch=epoch
    ).exists():
        return
    try:
        IssueActivity.objects.create(
            issue_id=issue_id,
            actor_id=uuid.UUID(actor_id),
            verb="updated",
            field="archived_at",
            old_value=None,
            new_value=archived_at,
            comment=f"归档了任务（含 {count - 1} 个子任务）" if count > 1 else "归档了任务",
            epoch=epoch,
        )
    except Exception as exc:  # noqa: BLE001
        raise self.retry(countdown=4**self.request.retries) from exc


@shared_task(bind=True, max_retries=3, retry_backoff=True)
def record_restore(self, issue_id: str, actor_id: str, count: int, epoch: float) -> None:
    """恢复 Activity（对称）。"""
    if IssueActivity.objects.filter(
        issue_id=issue_id, actor_id=uuid.UUID(actor_id), verb="updated", field="archived_at", epoch=epoch
    ).exists():
        return
    try:
        IssueActivity.objects.create(
            issue_id=issue_id,
            actor_id=uuid.UUID(actor_id),
            verb="updated",
            field="archived_at",
            old_value="archived",
            new_value=None,
            comment=f"恢复了任务（含 {count - 1} 个子任务）" if count > 1 else "恢复了任务",
            epoch=epoch,
        )
    except Exception as exc:  # noqa: BLE001
        raise self.retry(countdown=4**self.request.retries) from exc
