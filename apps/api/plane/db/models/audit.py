"""全站审计日志模型（AUTH-010，Sprint-8 R4）。

audit_log 为 PG 月分区表（PARTITION BY RANGE created_at）——分区 DDL 经
RunSQL 管理（Django 原生不支持），ORM 层 managed=False：索引与约束在
迁移 SQL 中显式创建（分区本地索引）。零外键对象引用（对象可删不级联）。
应用层只增（BR-01）：无 UPDATE/DELETE 代码路径。
"""
import uuid

from django.db import models
from django.utils import timezone


class AuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_key = models.CharField(max_length=80)  # 幂等锚（sha256 hex；全局幂等由应用层三层去重保证，BR-06）
    workspace = models.ForeignKey("Workspace", on_delete=models.CASCADE,
                                  null=True, related_name="+")  # 仅租户边界；NULL=系统级
    # AUTH-012（P4 R1）§4.1：风控 ingest 直读依赖的租户列——列 DDL 由 0039 迁移
    # RunSQL 交付（分区表 managed=False，无 ORM 迁移）；不建 FK 系 AUTH-010 零外键
    # 原则之引申。recorder 写入侧为 AUTH-010 待回改项，过渡期按 workspace→tenant 映射兜底。
    tenant_id = models.UUIDField(null=True, editable=False)
    category = models.CharField(max_length=24)
    action = models.CharField(max_length=48)
    actor_id = models.CharField(max_length=64, null=True)  # UUID v4 / "system" / None
    actor_snapshot = models.JSONField(default=dict)        # {name, email}
    object_type = models.CharField(max_length=32, null=True)
    object_id = models.CharField(max_length=64, null=True)  # 零外键（对象可删）
    object_snapshot = models.JSONField(default=dict)        # {name, ...}
    detail = models.JSONField(default=dict)                 # 黑名单过滤后差量（BR-11）
    ip = models.GenericIPAddressField(null=True)
    user_agent = models.CharField(max_length=255, blank=True, default="")
    prev_hash = models.CharField(max_length=64)
    hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)  # 分区键

    class Meta:
        db_table = "audit_log"
        managed = False  # 分区 DDL 经 RunSQL/运维任务管理（§4.1 迁移要点）
        # 索引实际由迁移 RunSQL 在分区表上创建（分区本地索引）；此处声明
        # 仅为 ORM 检查与文档双源（managed=False 不生成迁移）
        indexes = [
            models.Index(fields=["workspace", "-created_at", "-id"],
                         name="idx_audit_scan"),
            models.Index(fields=["workspace", "category", "action", "-created_at"],
                         name="idx_audit_event"),
            models.Index(fields=["workspace", "actor_id", "-created_at"],
                         name="idx_audit_actor"),
            models.Index(fields=["workspace", "object_type", "-created_at"],
                         name="idx_audit_object_type"),
            models.Index(fields=["workspace", "ip", "-created_at"],
                         name="idx_audit_ip"),
        ]
