"""工时 Activity 任务（TASK-006 §4.3.4）——comment 形如「张三 填报了 2h（2026-09-01）」。"""

from __future__ import annotations

import logging

from celery import shared_task

from plane.db.models import IssueActivity, WorkLog

logger = logging.getLogger(__name__)


def _fmt(minutes: int) -> str:
    return f"{minutes}m" if minutes < 60 else f"{minutes / 60:g}h"


@shared_task(bind=True, max_retries=3, retry_backoff=True)
def record_worklog(self, log_id: str, verb: str, epoch: float) -> None:
    try:
        log = WorkLog.objects.select_related("issue", "actor").filter(pk=log_id).first()
        if log is None:
            return
        verb_out = {"created": "填报了", "updated": "更新了", "deleted": "删除了"}.get(verb, "更新了")
        comment = f"{log.actor.display_name} {verb_out} {_fmt(log.minutes)} 工时（{log.worked_on}）"
        if verb == "deleted" and log.deleted_at is None:
            comment = f"{log.actor.display_name} 删除了工时记录"
        if IssueActivity.objects.filter(
            issue_id=log.issue_id,
            actor_id=log.actor_id,
            verb="updated",
            field="worklog",
            epoch=epoch,
        ).exists():
            return
        IssueActivity.objects.create(
            issue=log.issue,
            actor_id=log.actor_id,
            verb="updated",
            field="worklog",
            new_value=str(log.minutes),
            new_identifier=str(log.id),
            comment=comment,
            epoch=epoch,
        )
    except Exception as exc:  # noqa: BLE001 —— TASK-010 接 DLQ
        raise self.retry(countdown=4**self.request.retries) from exc
