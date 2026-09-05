"""Notification —— 站内通知，参见 `docs/sprint-1-mvp/COLLAB-001-comment-notify.md` §4.1.2。

行级隔离：QuerySet 恒 filter(receiver=user)。
通道演进：P1 轮询消费 → P2 COLLAB-004 WebSocket 推送（本表不动）。
"""
import hashlib

from django.db import models

from plane.db.models.base import BaseModel


class Notification(BaseModel):
    """站内通知 —— 行级隔离：QuerySet 恒 filter(receiver=user)（rbac §5.5）。"""

    class Event(models.TextChoices):
        ISSUE_ASSIGNED = "issue.assigned", "被指派"
        ISSUE_MENTIONED = "issue.mentioned", "被提及"
        ISSUE_COMMENTED = "issue.commented", "任务被评论"
        ISSUE_UPDATED = "issue.updated", "任务被更新"

    receiver = models.ForeignKey(
        "db.User",
        on_delete=models.CASCADE,
        related_name="notifications",
        verbose_name="接收人",
    )
    event = models.CharField(
        max_length=32,
        choices=Event.choices,
        db_index=True,
        verbose_name="事件类型",
    )
    title = models.CharField(
        max_length=200,
        verbose_name="标题",
        help_text="可直接展示的完整文案",
    )
    data = models.JSONField(
        default=dict,
        verbose_name="跳转载荷",
        help_text="必含 issue_id/project_id/workspace_slug/issue_key/actor + 事件特有键",
    )
    read_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="已读时间",
    )
    dedup_key = models.CharField(
        max_length=64,
        blank=True,
        default="",
        verbose_name="幂等键",
        help_text="sha256(event+issue+actor+epoch+receiver)——worker 重试/MQ 重投零重复",
    )

    class Meta(BaseModel.Meta):
        db_table = "notifications"
        verbose_name = "站内通知"
        ordering = ("-created_at",)
        constraints = [
            # 偏条件唯一：仅当 dedup_key 非空时约束；空 dedup_key 不参与去重（兼容性）
            models.UniqueConstraint(
                fields=["receiver", "dedup_key"],
                condition=~models.Q(dedup_key=""),
                name="uniq_notif_dedup",
            ),
        ]
        indexes = [
            # 未读计数（O(1)）+ 未读列表过滤：WHERE receiver=? AND read_at IS NULL
            models.Index(
                fields=["receiver", "read_at", "created_at"],
                name="idx_notif_receiver_unread",
            ),
            # 清理任务扫描：WHERE read_at < now-90d OR (read_at IS NULL AND created_at < now-180d)
            models.Index(fields=["read_at", "created_at"], name="idx_notif_retention"),
        ]

    @staticmethod
    def build_dedup_key(*, event: str, issue_id, actor_id, epoch: str, receiver_id) -> str:
        """生成幂等键（COLLAB-001 §4.1.2 注释）。

        SHA-256(event|issue|actor|epoch|receiver) —— worker 重试/MQ 重投零重复。
        """
        raw = f"{event}|{issue_id}|{actor_id}|{epoch}|{receiver_id}"
        return hashlib.sha256(raw.encode()).hexdigest()
