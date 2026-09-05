"""指派差异化通知 / BR-12 级联移出通知（TASK-007 §4.3.2 / §4.3.3）。

事件键（COLLAB-001 §2.3 补登 issue.unassigned）：
- ``issue.assigned``   集合新增成员 → 「{actor} 将 {KEY} 指派给你」
- ``issue.unassigned`` 集合移除成员 → 「{actor} 将你移出 {KEY}」（转交场景附 comment 引用）

约定：
- BR-09 操作者本人抑制（新增/认领/移除自己均不通知操作者）；
- BR-10 逐人写 ``IssueActivity(field='assignees')`` 共享同一 epoch（TASK-010 管道聚合）；
- 幂等：Notification.dedup_key 偏条件唯一兜 worker 重投；Activity 先查后写兜重试。
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from celery import shared_task

from plane.db.models import IssueActivity, Notification, User

logger = logging.getLogger("plane.bgtasks.issue_assignee")

#: 与视图侧 _current_epoch 同源：毫秒时间戳（IssueActivity.epoch 口径）
def _epoch_ms() -> float:
    return time.time() * 1000.0


def _base_data(*, issue, actor_name: str, actor_id: str) -> dict:
    return {
        "issue_id": str(issue.id),
        "project_id": str(issue.project_id),
        "workspace_slug": issue.project.workspace.slug,
        "issue_key": f"{issue.project.identifier}-{issue.sequence_id}",
        "actor": actor_name,
        "actor_id": actor_id,
    }


def _notify(receiver_id: str, *, event: str, title: str, data: dict, issue, actor_id: str) -> None:
    epoch = str(int(datetime.now(tz=UTC).timestamp()))  # 秒级 epoch：同秒重投去重（容忍）
    try:
        Notification.objects.create(
            receiver_id=receiver_id,
            event=event,
            title=title[:200],
            data=data,
            dedup_key=Notification.build_dedup_key(
                event=event, issue_id=issue.id, actor_id=actor_id,
                epoch=epoch, receiver_id=receiver_id,
            ),
        )
    except Exception as exc:  # noqa: BLE001 —— 偏条件唯一冲突视为已投递，幂等跳过
        if "uniq_notif_dedup" not in str(exc):
            logger.warning("assignee.notify.retry receiver=%s event=%s exc=%s",
                           receiver_id, event, exc)
            raise


def _activity(issue, actor_id, *, new_identifier=None, old_identifier=None,
              value: str, comment: str, epoch: float) -> None:
    """BR-10：逐人一条 IssueActivity(field='assignees')，先查后写兜重试幂等。"""
    exists_kw: dict[str, object] = {}
    if new_identifier is not None:
        exists_kw["new_identifier"] = new_identifier
    if old_identifier is not None:
        exists_kw["old_identifier"] = old_identifier
    if IssueActivity.objects.filter(
        issue=issue, actor_id=actor_id, verb="updated", field="assignees", epoch=epoch, **exists_kw
    ).exists():
        return
    IssueActivity.objects.create(
        issue=issue,
        actor_id=actor_id,
        verb="updated",
        field="assignees",
        new_identifier=new_identifier,
        old_identifier=old_identifier,
        new_value=value if new_identifier else None,
        old_value=value if old_identifier else None,
        comment=comment,
        epoch=epoch,
    )


@shared_task(
    bind=True,
    max_retries=3,
    retry_backoff=True,
    name="plane.bgtasks.issue_assignee.dispatch_assignment_events",
)
def dispatch_assignment_events(
    self, *, issue_id: str, actor_id: str, changes: dict, comment: str = ""
) -> bool:
    """差异化通知 + 逐人 Activity（BR-09/BR-10）：

    - added 每人一条 issue.assigned（操作者本人跳过）；
    - removed 每人一条 issue.unassigned（含转交说明引用；操作者本人跳过）；
    - 全部 Activity 行共享同一 epoch。
    """
    from plane.db.models import Issue

    try:
        issue = (
            Issue.objects.select_related("project", "project__workspace")
            .filter(pk=issue_id)
            .first()
        )
        if issue is None:
            logger.warning("assignee.event.issue_missing issue=%s", issue_id)
            return False
        actor = User.objects.filter(pk=actor_id).first()
        actor_name = actor.display_name if actor else "系统"
        issue_key = f"{issue.project.identifier}-{issue.sequence_id}"
        epoch = _epoch_ms()
        comment_ref = f"（转交说明：{comment}）" if comment else ""

        for person in changes.get("added") or []:
            uid = str(person["id"])
            name = person.get("display_name") or uid
            _activity(
                issue, actor_id, new_identifier=uid, value=name,
                comment=f"指派了 {name}{comment_ref}", epoch=epoch,
            )
            if uid == actor_id:  # BR-09 操作者本人抑制
                continue
            data = _base_data(issue=issue, actor_name=actor_name, actor_id=actor_id)
            if comment:
                data["comment"] = comment
            _notify(
                uid,
                event="issue.assigned",
                title=f"{actor_name} 将 {issue_key} 指派给你",
                data=data,
                issue=issue,
                actor_id=actor_id,
            )
        for person in changes.get("removed") or []:
            uid = str(person["id"])
            name = person.get("display_name") or uid
            _activity(
                issue, actor_id, old_identifier=uid, value=name,
                comment=f"移出了 {name}{comment_ref}", epoch=epoch,
            )
            if uid == actor_id:  # BR-09 操作者本人抑制
                continue
            data = _base_data(issue=issue, actor_name=actor_name, actor_id=actor_id)
            if comment:
                data["comment"] = comment
            _notify(
                uid,
                event="issue.unassigned",
                title=f"{actor_name} 将你移出 {issue_key}{comment_ref}",
                data=data,
                issue=issue,
                actor_id=actor_id,
            )
        return True
    except Exception as exc:  # noqa: BLE001 —— TASK-010 接 DLQ
        raise self.retry(countdown=4**self.request.retries) from exc


@shared_task(
    bind=True,
    max_retries=3,
    retry_backoff=True,
    name="plane.bgtasks.issue_assignee.notify_assignments_purged",
)
def notify_assignments_purged(
    self, *, issue_ids: list[str], member_id: str, actor_id: str | None
) -> bool:
    """BR-12 级联（§4.3.3）：被移除人逐任务收 issue.unassigned。

    操作者 = 执行移除的管理员；接收人（被移除者）≠ 操作者，BR-09 不抑制。
    """
    from plane.db.models import Issue

    try:
        if not issue_ids:
            return True
        issues = list(
            Issue.objects.select_related("project", "project__workspace")
            .filter(id__in=issue_ids)
        )
        actor = User.objects.filter(pk=actor_id).first() if actor_id else None
        actor_name = actor.display_name if actor else "系统"
        for issue in issues:
            issue_key = f"{issue.project.identifier}-{issue.sequence_id}"
            _notify(
                member_id,
                event="issue.unassigned",
                title=f"{actor_name} 将你移出 {issue_key}（成员移出项目，指派已级联清空）",
                data=_base_data(issue=issue, actor_name=actor_name,
                                actor_id=actor_id or "system"),
                issue=issue,
                actor_id=actor_id or "system",
            )
        return True
    except Exception as exc:  # noqa: BLE001 —— TASK-010 接 DLQ
        raise self.retry(countdown=4**self.request.retries) from exc
