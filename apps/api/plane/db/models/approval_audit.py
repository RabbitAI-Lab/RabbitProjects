"""审批留痕模型（WF-006 §4.1）——append-only 审计事件 + 哈希链。

不可变由 PG 触发器承载（迁移内 DDL，§4.2）：
- approval_records_guard：records 仅 action/comment/acted_at 列级白名单可写、
  直连 DELETE 拒绝（pg_trigger_depth 级联放行）
- approval_instance_guard：实例终态后锁身份/锁定/终态字段
- approval_audit_events_guard：审计事件仅 INSERT（BR-01 只增）
"""
from django.conf import settings
from django.db import models


class ApprovalAuditEvent(models.Model):
    """审批域审计事件（BR-01 只增；哈希链篡改检测，§2.2）。"""

    id = models.BigAutoField(primary_key=True)
    project = models.ForeignKey("db.Project", on_delete=models.CASCADE, related_name="+")
    type = models.CharField(max_length=32, verbose_name="事件类型（§2.1 清单）")
    instance = models.ForeignKey(
        "db.ApprovalInstance", null=True, on_delete=models.SET_NULL, related_name="+",
        verbose_name="审批实例")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+",
        verbose_name="操作者", help_text="NULL=系统（超时/跳票/归档）")
    payload = models.JSONField(default=dict, verbose_name="载荷（含 comment_sha256，BR-04）")
    request_id = models.CharField(max_length=32, blank=True, default="", verbose_name="请求追踪（BR-09）")
    prev_hash = models.CharField(max_length=64, verbose_name="链上前事件哈希")
    event_hash = models.CharField(max_length=64, verbose_name="本事件哈希")
    created_at = models.DateTimeField(verbose_name="时间")  # 服务端显式赋值（哈希链输入一致性，§4.2）

    class Meta:
        db_table = "approval_audit_events"
        verbose_name = "审批审计事件"
        verbose_name_plural = verbose_name
        ordering = ("id",)
        indexes = [
            models.Index(fields=["project", "created_at"], name="idx_aae_project_time"),
            models.Index(fields=["project", "actor", "created_at"], name="idx_aae_actor"),
            models.Index(fields=["instance"], name="idx_aae_instance"),
            models.Index(fields=["created_at"], name="idx_aae_retention"),
        ]

    def __str__(self) -> str:
        return f"aae({self.id}:{self.type})"
