"""备份执行记录（INFRA-005 §4.1——Sprint-6 T6-03）。

一张运维表：限流不建表（L1 在 Nginx 共享内存、L2/L3 在 Redis，可观测面走
结构化日志 BR-14）；备份因需要「连续失败告警 / 演练留痕 / admin 列表」三态
跨请求聚合，落库。
"""
from __future__ import annotations

from django.db import models

from .base import BaseModel


class BackupRun(BaseModel):
    """备份执行记录 —— 状态/产物/校验/演练关联。"""

    class Status(models.TextChoices):
        RUNNING = "running", "进行中"
        SUCCESS = "success", "成功"
        FAILED = "failed", "失败"

    class Kind(models.TextChoices):
        DAILY = "daily", "每日全量"
        MANUAL = "manual", "手动"
        DRILL = "drill", "恢复演练"          # 演练也入账（BR-09 可追溯）

    kind = models.CharField(max_length=8, choices=Kind.choices,
                            default=Kind.DAILY, verbose_name="类型")
    status = models.CharField(max_length=8, choices=Status.choices,
                              default=Status.RUNNING, db_index=True,
                              verbose_name="状态")
    started_at = models.DateTimeField(verbose_name="开始时刻")
    finished_at = models.DateTimeField(null=True, blank=True,
                                       verbose_name="结束时刻")
    dump_size_bytes = models.BigIntegerField(null=True, blank=True,
                                             verbose_name="产物大小")
    checksum_sha256 = models.CharField(max_length=64, blank=True,
                                       default="", verbose_name="SHA-256")
    object_key = models.TextField(blank=True, default="",
                                  verbose_name="MinIO 对象键")
    drill_report = models.JSONField(default=dict, blank=True,
                                    verbose_name="演练报告",
                                    help_text="{rto_seconds, smoke_passed, "
                                              "smoke_total, notes}")
    error = models.TextField(blank=True, default="", verbose_name="失败原因")

    class Meta(BaseModel.Meta):
        db_table = "backup_runs"
        verbose_name = "备份执行记录"
        ordering = ("-started_at",)
        indexes = [models.Index(fields=["status", "-started_at"],
                                name="idx_backup_recent")]

    def __str__(self) -> str:
        return f"[{self.kind}] {self.status} {self.started_at:%Y-%m-%d %H:%M}"

    # ── 状态迁移（任务侧唯一写入口；UT-08/09 断言留痕字段）──

    def finish_success(self, size_bytes: int, sha256: str, object_key: str) -> None:
        from django.utils import timezone

        self.status = self.Status.SUCCESS
        self.finished_at = timezone.now()
        self.dump_size_bytes = size_bytes
        self.checksum_sha256 = sha256
        self.object_key = object_key
        self.save(update_fields=["status", "finished_at", "dump_size_bytes",
                                 "checksum_sha256", "object_key", "updated_at"])

    def finish_failure(self, error: str) -> None:
        from django.utils import timezone

        self.status = self.Status.FAILED
        self.finished_at = timezone.now()
        self.error = error[:2000]
        self.save(update_fields=["status", "finished_at", "error", "updated_at"])
