"""企微/钉钉 IM 通道模型（INTG-005，P4 R5b）。

ImWebhookChannel（群机器人通道：webhook target + 钉钉加签句柄 +
degraded 健康位）+ ImSubscription（订阅——INTG-003 SlackChannelSubscription
同构范式：范围可空 + COALESCE 表达式唯一索引承载全项目行去重）。
"""

from django.db import models

from plane.db.models.base import BaseModel


class ImWebhookChannel(BaseModel):
    """群机器人通道（企微 webhook+key / 钉钉 webhook+加签）。"""

    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="im_channels")
    provider = models.CharField(max_length=12, choices=[("wecom", "企业微信"), ("dingtalk", "钉钉")])
    name = models.CharField(max_length=64)
    target = models.CharField(max_length=512)  # webhook URL
    secret_ref = models.CharField(max_length=128, blank=True, default="", verbose_name="钉钉加签 secret 句柄（企微空）")
    is_degraded = models.BooleanField(default=False)  # BR-03 连败标记

    class Meta(BaseModel.Meta):
        db_table = "integration_im_channels"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "provider", "target"],
                condition=models.Q(deleted_at__isnull=True),
                name="uq_im_channel_target",
            ),
        ]


class ImSubscription(BaseModel):
    """IM 订阅（通道 × 范围 × 事件；null=全项目——表达式索引去重）。"""

    channel = models.ForeignKey(ImWebhookChannel, on_delete=models.CASCADE, related_name="subscriptions")
    project = models.ForeignKey("db.Project", null=True, blank=True, on_delete=models.CASCADE)  # null=全项目
    event_types = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        db_table = "integration_im_subscriptions"
        # 唯一性经迁移表达式索引（COALESCE + WHERE deleted_at IS NULL——
        # 同 0045 RunSQL 范式，BR-01）
