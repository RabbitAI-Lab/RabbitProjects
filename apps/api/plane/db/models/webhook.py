"""出站 Webhook 模型（INTG-002 §4.1——Sprint-5 T5-05）。

死信 = ``WebhookDelivery.status=dead`` 单表设计（无独立死信表）；连败计数为
无时间窗的终态计数器（dead +1 / success −1 钳位 ≥0，≥50 → auto_disabled）。
secret 应用层 Fernet 密文（复用 integration 模块的密钥派生与加解密）。
"""
from __future__ import annotations

import uuid

from django.db import models

from plane.db.models.base import BaseModel


class WebhookEndpoint(BaseModel):
    """项目级出站端点；软删后同 URL 可重建（BR-11）。"""

    class Status(models.TextChoices):
        ACTIVE = "active", "启用"
        DISABLED = "disabled", "手动停用"
        AUTO_DISABLED = "auto_disabled", "自动停用"

    project = models.ForeignKey("db.Project", on_delete=models.CASCADE,
                                related_name="webhook_endpoints", verbose_name="项目")
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE,
                                  related_name="webhook_endpoints", verbose_name="工作空间")
    url = models.URLField(max_length=2048, verbose_name="回调地址")
    secret_encrypted = models.TextField(verbose_name="secret（Fernet 密文）")
    events = models.JSONField(default=list, verbose_name="订阅事件面",
                              help_text="§2.3 十二事件闭集子集（webhook.ping 免勾选）")
    is_active = models.CharField(max_length=16, choices=Status.choices,
                                 default=Status.ACTIVE, db_index=True,
                                 verbose_name="端点状态")
    consecutive_failures = models.PositiveIntegerField(default=0,
                                                       verbose_name="终态连败计数")
    created_by = models.ForeignKey("db.User", on_delete=models.SET_NULL, null=True,
                                   related_name="+", verbose_name="创建者")

    class Meta(BaseModel.Meta):
        db_table = "webhook_endpoints"
        verbose_name = "Webhook 端点"
        indexes = [
            models.Index(fields=["project", "is_active"], name="idx_webhook_fanout",
                         condition=models.Q(deleted_at__isnull=True)),
        ]
        constraints = [
            models.UniqueConstraint(fields=["project", "url"],
                                    name="uniq_webhook_project_url",
                                    condition=models.Q(deleted_at__isnull=True)),
        ]

    def __str__(self) -> str:
        return f"webhook:{self.url[:60]}@{self.project_id}"


class WebhookDelivery(BaseModel):
    """单端点单事件投递记录：重试追加 Attempt JSON（非新行）；重放新建行指回原 dead。"""

    class Status(models.TextChoices):
        PENDING = "pending", "待投递"
        SUCCESS = "success", "成功"
        RETRYING = "retrying", "退避中"
        DEAD = "dead", "死信"
        CANCELLED = "cancelled", "已终止"

    endpoint = models.ForeignKey(WebhookEndpoint, on_delete=models.CASCADE,
                                 related_name="deliveries", verbose_name="端点")
    event = models.CharField(max_length=64, verbose_name="事件名")
    event_id = models.UUIDField(default=uuid.uuid4, verbose_name="事件去重锚")
    payload = models.JSONField(verbose_name="冻结载荷（重放原样重发）")
    status = models.CharField(max_length=16, choices=Status.choices,
                              default=Status.PENDING, verbose_name="投递状态")
    attempts = models.JSONField(default=list, verbose_name="尝试明细")
    next_retry_at = models.DateTimeField(null=True, blank=True, verbose_name="下次重试时刻")
    replay_of = models.UUIDField(null=True, blank=True, verbose_name="重放来源 dead 行")

    class Meta(BaseModel.Meta):
        db_table = "webhook_deliveries"
        verbose_name = "Webhook 投递"
        indexes = [
            models.Index(fields=["endpoint", "-created_at"],
                         name="idx_delivery_endpoint_time"),
            models.Index(fields=["status", "next_retry_at"],
                         name="idx_delivery_retry_scan",
                         condition=models.Q(status="retrying")),
            models.Index(fields=["endpoint", "status"], name="idx_delivery_dead",
                         condition=models.Q(status="dead")),
        ]
        constraints = [
            # 幂等锚（重放行豁免）
            models.UniqueConstraint(fields=["endpoint", "event_id"],
                                    name="uniq_delivery_endpoint_event",
                                    condition=models.Q(replay_of__isnull=True)),
        ]
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"delivery:{self.event}#{self.status}"
