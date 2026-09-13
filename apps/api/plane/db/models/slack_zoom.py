"""Slack/Zoom 集成模型（INTG-003，P4 R5）。

SlackChannelSubscription（频道订阅——COALESCE 表达式唯一索引承载全项目
行去重，普通约束对 NULL 互异失效）、SlackUserMap（身份映射）、
SlackMessageAnchor（卡片锚点：chat.update 合并与线程定位）、IssueMeeting
（Zoom 会议关联）、ZoomConnector（实例级单例）。安装载体复用扩展
IntegrationInstallation（provider 枚举 + 可空演进列，INTG-001 待回改
登记——§4.1 演进⑤）。
"""

from django.db import models

from plane.db.models.base import BaseModel
from plane.db.models.integration import IntegrationInstallation


class SlackChannelSubscription(BaseModel):
    """频道订阅（installation × channel × project 三元；null=全项目）。"""

    installation = models.ForeignKey(
        IntegrationInstallation,
        on_delete=models.CASCADE,
        related_name="slack_subscriptions",
        limit_choices_to={"provider": "slack"},
    )
    channel_id = models.CharField(max_length=32)  # C…
    channel_name = models.CharField(max_length=128)
    project = models.ForeignKey("db.Project", null=True, blank=True, on_delete=models.CASCADE)  # null=全项目
    event_types = models.JSONField(default=list)  # ["created",...]
    thread_sync = models.BooleanField(default=False)  # BR-07 线程同步
    is_active = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        db_table = "integration_slack_subscriptions"
        # 唯一性经迁移 RunSQL 表达式索引（COALESCE + WHERE deleted_at IS NULL，
        # §4.1.1）——普通约束对可空 project 行不去重


class SlackUserMap(BaseModel):
    """Slack 用户 ↔ 系统用户映射（null=未映射，按邮箱自动建议）。"""

    installation = models.ForeignKey(
        IntegrationInstallation, on_delete=models.CASCADE, limit_choices_to={"provider": "slack"}
    )
    slack_user_id = models.CharField(max_length=32)
    user = models.ForeignKey("db.User", null=True, blank=True, on_delete=models.SET_NULL)  # null=未映射
    slack_email = models.EmailField()
    slack_display_name = models.CharField(max_length=128)

    class Meta(BaseModel.Meta):
        db_table = "integration_slack_user_maps"
        constraints = [
            models.UniqueConstraint(fields=["installation", "slack_user_id"], name="uq_slack_user_map"),
        ]


class SlackMessageAnchor(BaseModel):
    """任务卡片锚点（chat.update 合并 BR-04 与线程同步定位）。"""

    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="slack_anchors")
    channel_id = models.CharField(max_length=32)
    message_ts = models.CharField(max_length=32)
    thread_ts = models.CharField(max_length=32, blank=True, default="")

    class Meta(BaseModel.Meta):
        db_table = "integration_message_anchors"
        constraints = [
            models.UniqueConstraint(fields=["issue", "channel_id"], name="uq_slack_anchor_channel"),
        ]


class IssueMeeting(BaseModel):
    """Zoom 会议关联（纪要回挂 COLLAB-001/002 载体）。"""

    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="meetings")
    meeting_id = models.CharField(max_length=32, db_index=True)
    join_url = models.URLField(max_length=512)
    topic = models.CharField(max_length=255)
    start_time = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey("db.User", on_delete=models.PROTECT)

    class Meta(BaseModel.Meta):
        db_table = "integration_issue_meetings"
        indexes = [models.Index(fields=["meeting_id"], name="idx_issue_meeting_mid")]


class ZoomConnector(BaseModel):
    """Zoom 实例级单例（Account-level App：client 凭据为实例资产）。"""

    account_id = models.CharField(max_length=64)
    client_id = models.CharField(max_length=64)
    client_secret_ref = models.CharField(max_length=128)  # 密保库句柄
    webhook_secret_ref = models.CharField(max_length=128)
    ai_minutes_enabled = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        db_table = "integration_zoom_connectors"
        constraints = [
            models.UniqueConstraint(
                fields=["account_id"], condition=models.Q(is_active=True), name="uq_zoom_connector_active"
            ),
        ]
