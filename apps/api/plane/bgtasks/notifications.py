"""站内通知落库任务（TEAM-002 §4.4 + COLLAB-001 §4.1.2）。

约定：
- 任务名统一 ``send_workspace_notification``，按 ``event`` 与 ``context`` 区分
  业务事件；不在本模块定义 ``notify_role_changed`` / ``notify_ownership_transferred``
  等独立任务（TEAM-002 §4.3 注释显式禁止）。
- 仅传 ID（receiver_id / actor_id）+ context 字典；任务内重新查询。
- 幂等：dedup_key 在 receiver 维度去重（Notification 模型自带偏条件唯一约束）。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from celery import shared_task

logger = logging.getLogger("plane.bgtasks.notifications")


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    name="plane.bgtasks.notifications.send_workspace_notification",
)
def send_workspace_notification(self, *, receiver_id: str, event: str, context: dict | None = None,
                               title: str | None = None) -> bool:
    """落库一条站内通知 —— receiver 维度的去重由 Notification.dedup_key 兜底。

    失败重试由 Celery 自带 max_retries 兜 3 次；最终失败入日志，不抛错
    阻塞业务事务（on_commit 已提交）。
    """
    from plane.db.models import Notification, User

    try:
        receiver = User.objects.get(id=receiver_id)
    except User.DoesNotExist:
        logger.warning("notification.receiver_missing receiver_id=%s event=%s", receiver_id, event)
        return False

    ctx = context or {}
    # dedup_key：sha256(event|workspace|actor|epoch|receiver)；actor 可缺省为 system
    actor_id = ctx.get("actor_id", "system")
    workspace_slug = ctx.get("workspace_slug", "")
    # epoch 取 UTC 秒 —— 同一秒内的同事件同 receiver 视为同一条（容忍重投）
    epoch = int(datetime.now(tz=UTC).timestamp())
    dedup_key = Notification.build_dedup_key(
        event=event, issue_id=workspace_slug or "workspace", actor_id=str(actor_id),
        epoch=str(epoch), receiver_id=str(receiver_id),
    )

    title_text = title or _render_title(event=event, ctx=ctx)
    # payload：data 字段直接落 context（COLLAB-001 通知中心的跳转载荷）
    try:
        Notification.objects.create(
            receiver=receiver,
            event=event,
            title=title_text,
            data=ctx,
            dedup_key=dedup_key,
            created_by=receiver,
        )
    except Exception as exc:
        # 偏条件唯一冲突视为已投递，幂等跳过；其余重试
        if "uniq_notif_dedup" in str(exc):
            logger.info("notification.dedup_skip receiver=%s event=%s", receiver_id, event)
            return True
        logger.warning("notification.retry receiver=%s event=%s exc=%s", receiver_id, event, exc)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.exception("notification.giveup receiver=%s event=%s", receiver_id, event)
            return False
    return True


def _render_title(*, event: str, ctx: dict) -> str:
    """事件→中文文案。客户端禁止按文案分支；仅 UI 直接展示。"""
    actor = ctx.get("actor", "系统")
    workspace_slug = ctx.get("workspace_slug", "")
    if event == "workspace.member.added":
        return f"{actor} 邀请你加入了工作空间"
    if event == "workspace.member.removed":
        return "你已被移出工作空间"
    if event == "workspace.member.role_changed":
        old = ctx.get("old_role")
        new = ctx.get("new_role")
        if ctx.get("ownership_transferred"):
            return f"{actor} 将工作空间所有权转让给了你"
        return f"{actor} 将你在工作空间中的角色从 {old} 调整为 {new}"
    return f"工作空间 {workspace_slug} 发生了 {event}"
