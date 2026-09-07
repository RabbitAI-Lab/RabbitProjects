"""发布门禁数据模型（QA-001 §4.1——Sprint-6 T6-06）。

一次发布尝试 = 一行 ReleaseGate（四门禁快照 + checklist 签署快照）；
一切状态变化 append-only 追加 ReleaseGateEvent（审计纪律同 TASK-010 Activity）。
"""
from __future__ import annotations

import uuid

from django.db import models

from .base import BaseModel


class ReleaseGate(BaseModel):
    """一次发布尝试的门禁与签署留痕（快照 + 事件流双轨，§4.1）。"""

    class Status(models.TextChoices):
        PENDING = "pending", "未开始"
        RUNNING = "running", "进行中"
        PASSED = "passed", "通过"
        BLOCKED = "blocked", "阻塞"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.CharField(max_length=32, verbose_name="语义化版本")
    commit_sha = models.CharField(max_length=40, verbose_name="提交哈希")
    gate_defects = models.CharField(max_length=16, choices=Status.choices,
                                    default=Status.PENDING, verbose_name="缺陷门禁")
    gate_perf = models.CharField(max_length=16, choices=Status.choices,
                                 default=Status.PENDING, verbose_name="压测门禁")
    gate_security = models.CharField(max_length=16, choices=Status.choices,
                                     default=Status.PENDING, verbose_name="安全门禁")
    gate_compat = models.CharField(max_length=16, choices=Status.choices,
                                   default=Status.PENDING, verbose_name="兼容门禁")
    artifacts = models.JSONField(default=dict, blank=True,
                                 verbose_name="门禁产物指针")
    checklist = models.JSONField(default=list, blank=True,
                                 verbose_name="签署快照",
                                 help_text="[{key, signed_by, signed_at, note, revoked?}]")
    verdict = models.CharField(max_length=16, choices=[
        ("none", "未裁决"), ("approved", "放行"), ("rejected", "打回")],
        default="none", verbose_name="评审裁决")

    class Meta(BaseModel.Meta):
        db_table = "release_gates"
        verbose_name = "发布门禁"
        ordering = ("-created_at",)
        constraints = [models.UniqueConstraint(fields=["version", "commit_sha"],
                                               name="uniq_release_gate")]

    def __str__(self) -> str:
        return f"{self.version}@{self.commit_sha[:8]}"


class ReleaseGateEvent(models.Model):
    """门禁状态变迁与签署事件（append-only；actor 空值 = CI 机器用户）。

    不继承 BaseModel（§4.1 原样：BigAutoField 主键 + 仅 created_at——审计流
    无软删/无 updated_at，append-only 纪律反而禁止 update/delete 语义字段）。
    """

    id = models.BigAutoField(primary_key=True)
    gate = models.ForeignKey(ReleaseGate, related_name="events",
                             on_delete=models.CASCADE, verbose_name="发布尝试")
    event_type = models.CharField(max_length=32, verbose_name="事件类型")
    payload = models.JSONField(default=dict, blank=True, verbose_name="载荷")
    actor = models.ForeignKey("db.User", null=True, blank=True,
                              on_delete=models.SET_NULL,
                              related_name="release_gate_events",
                              verbose_name="操作者")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True,
                                      verbose_name="事件时刻")

    class Meta:
        db_table = "release_gate_events"
        verbose_name = "发布门禁事件"
        ordering = ("id",)
        indexes = [models.Index(fields=["gate", "id"], name="idx_gate_event")]
