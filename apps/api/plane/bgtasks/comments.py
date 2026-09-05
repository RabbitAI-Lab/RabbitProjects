"""评论事件 Celery 任务（COLLAB-001 §4.4.3）。

约定：
- 任务只接受 ID，不接受 ORM 对象（api-conventions §10.5）
- broker 不可达时由 CommentService._safe_delay 同步回退调用（本地无 MQ 验证环境）
- ``max_retries=3`` 配合 ``ignore_conflicts`` 兜住重试零重复（BR-08）
"""
from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger("plane.bgtasks.comments")


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    name="plane.bgtasks.comments.notify_comment_task",
)
def notify_comment_task(self, comment_id: str, issue_id: str) -> int:
    """新评论 → mentioned / commented 扇出。

    返回值仅供日志；幂等靠 ``Notification.uniq_notif_dedup`` + ``ignore_conflicts``。
    """
    from plane.app.comments.sanitize import extract_mention_ids
    from plane.db.models import IssueComment
    from plane.db.services.notify import fanout_comment

    try:
        comment = (
            IssueComment.objects
            .select_related("actor")
            .get(id=comment_id)
        )
    except IssueComment.DoesNotExist:
        logger.warning("notify_comment.comment_missing id=%s", comment_id)
        return 0

    # 解析净化后 HTML 的 @ 锚点
    mention_ids = extract_mention_ids(comment.comment_html)
    try:
        count = fanout_comment(
            comment_id=comment_id,
            issue_id=issue_id,
            actor=comment.actor,
            mention_ids=mention_ids,
        )
        return count
    except Exception as exc:                              # noqa: BLE001
        logger.warning("notify_comment.retry comment=%s exc=%s", comment_id, exc)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.exception("notify_comment.giveup comment=%s", comment_id)
            return 0
