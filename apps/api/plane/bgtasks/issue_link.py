"""关联变更 Activity 任务（TASK-005 §4.3.5；两端共享同一 epoch）。"""

from __future__ import annotations

import logging
import uuid

from celery import shared_task

from plane.db.models import IssueActivity, IssueLink

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, retry_backoff=True)
def record_relation_change(self, forward_id: str, mirror_id: str | None, actor_id: str, epoch: float) -> None:
    """关系变更 Activity（verb=updated、field=relation）——两端各一条，共享 epoch。"""
    actor_uuid = uuid.UUID(actor_id)
    for lid in (forward_id, mirror_id):
        if not lid:
            continue
        try:
            link = (
                IssueLink.objects.select_related("issue", "related_issue", "issue__project", "related_issue__project")
                .filter(pk=lid)
                .first()
            )
            if link is None:
                continue
            if IssueActivity.objects.filter(
                issue=link.issue,
                actor_id=actor_uuid,
                verb="updated",
                field="relation",
                epoch=epoch,
            ).exists():
                continue

            def key(it):
                return f"{it.name} {it.project.identifier}-{it.sequence_id}"

            IssueActivity.objects.create(
                issue=link.issue,
                actor_id=actor_uuid,
                verb="updated",
                field="relation",
                old_value=None,
                new_value=key(link.related_issue),
                new_identifier=str(link.related_issue_id),
                comment=f"添加了关联（{link.get_relation_type_display()}）",
                epoch=epoch,
            )
        except Exception as exc:  # noqa: BLE001 —— TASK-010 接 DLQ
            raise self.retry(countdown=4**self.request.retries) from exc
