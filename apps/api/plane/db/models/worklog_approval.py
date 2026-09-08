"""工时审批三表（TASK-013 §4.2）——Sprint-7 R3 轻量单级审批载体。

- WorkLogApproval：批次（actor × project × week_start），四态（draft/submitted/
  approved/rejected），ALLOWED_TRANSITIONS 表驱动状态机
- WorkLogSummary：人×周聚合快照（BR-13 只增改不删），台账与 RPT-004 负载唯一源
- ProjectWorklogConfig：项目级 OneToOne 配置（BR-08 软上限/粒度/BR-10 预警比/BR-12 周容量）——
  weekly_capacity_minutes 为负载°分母唯一配置源（RPT-004 改待办登记）
"""
from __future__ import annotations

from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from plane.db.models.base import BaseModel


class WorkLogApproval(BaseModel):
    """工时审批批次 —— 成员 × 项目 × 自然周（BR-01）。

    轻量单级审批：刻意不复用 WF-002 ApprovalFlow 引擎（会签/逐级对周工时属
    过度设计），但状态机语义与审批留痕规范与 WF-002/WF-006 对齐。
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "待提交"
        SUBMITTED = "submitted", "待审批"
        APPROVED = "approved", "已通过"
        REJECTED = "rejected", "已驳回"

    ALLOWED_TRANSITIONS: dict[str, set[str]] = {
        Status.DRAFT: {Status.SUBMITTED},
        Status.SUBMITTED: {Status.APPROVED, Status.REJECTED, Status.DRAFT},
        Status.REJECTED: {Status.SUBMITTED},
        Status.APPROVED: {Status.REJECTED},  # BR-07 撤销审批（仅负责人，必填意见）
    }

    project = models.ForeignKey(
        "db.Project", on_delete=models.CASCADE, related_name="worklog_approvals", verbose_name="所属项目")
    actor = models.ForeignKey(
        "db.User", on_delete=models.CASCADE, related_name="worklog_approvals", verbose_name="被审人")
    week_start = models.DateField(verbose_name="周起始日（周一）")
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True, verbose_name="状态")
    review_note = models.CharField(
        max_length=500, blank=True, verbose_name="审批意见（驳回/撤销必填，BR-05/07）")
    reviewer = models.ForeignKey(
        "db.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reviewed_worklog_approvals", verbose_name="审批人")
    submitted_at = models.DateTimeField(null=True, blank=True, verbose_name="最近提交时间")
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name="最近审批时间")

    class Meta(BaseModel.Meta):
        db_table = "worklog_approvals"
        verbose_name = "工时审批批次"
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=["actor", "project", "week_start"],
                condition=models.Q(deleted_at__isnull=True),
                name="uniq_worklog_approval_batch"),
        ]
        indexes = [models.Index(fields=["project", "week_start", "status"], name="idx_wla_project_week")]

    def __str__(self) -> str:
        return f"wla({self.project_id}:{self.actor_id}:{self.week_start})"


class WorkLogSummary(BaseModel):
    """人×周工时快照（BR-13 只增改不删）——台账与 RPT-004 负载的唯一数据源。"""

    project = models.ForeignKey(
        "db.Project", on_delete=models.CASCADE, related_name="worklog_summaries", verbose_name="所属项目")
    actor = models.ForeignKey(
        "db.User", on_delete=models.CASCADE, related_name="worklog_summaries", verbose_name="成员")
    week_start = models.DateField(verbose_name="周起始日")
    total_minutes = models.PositiveIntegerField(default=0, verbose_name="总工时（分钟）")
    task_count = models.PositiveIntegerField(default=0, verbose_name="涉及任务数")
    approved_minutes = models.PositiveIntegerField(default=0, verbose_name="已审批工时")
    over_8h_days = models.PositiveIntegerField(
        default=0, verbose_name="单日超 480 分钟的天数（BR-11，refresh 时日级聚合派生）")
    is_frozen = models.BooleanField(default=False, verbose_name="是否冻结（批次通过）")

    class Meta(BaseModel.Meta):
        db_table = "worklog_summaries"
        verbose_name = "工时聚合快照"
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=["project", "actor", "week_start"],
                name="uniq_worklog_summary_cell"),
        ]
        indexes = [
            models.Index(fields=["project", "week_start"], name="idx_wls_project_week"),
            models.Index(fields=["actor", "week_start"], name="idx_wls_actor_week"),
        ]

    def __str__(self) -> str:
        return f"wls({self.project_id}:{self.actor_id}:{self.week_start})"


class ProjectWorklogConfig(BaseModel):
    """工时管控项目级配置（BR-08/10/12）——项目级唯一配置行。

    数值域由 DB CheckConstraint 兜底，配置变更经 BaseModel 留审计。
    """

    GRANULARITY_CHOICES = ((15, "15 分钟"), (30, "30 分钟"), (60, "60 分钟"))

    project = models.OneToOneField(
        "db.Project", on_delete=models.CASCADE, related_name="worklog_config", verbose_name="所属项目")
    approval_enabled = models.BooleanField(default=True, verbose_name="启用周审批")
    daily_soft_limit_minutes = models.PositiveIntegerField(
        default=480, verbose_name="单日软上限（分钟，超出仅警告，BR-08）")
    granularity_minutes = models.PositiveSmallIntegerField(
        default=15, choices=GRANULARITY_CHOICES, verbose_name="单笔填报粒度（分钟，BR-08）")
    warn_ratio = models.DecimalField(
        max_digits=3, decimal_places=2, default=Decimal("0.80"),
        verbose_name="任务超额预警阈值（BR-10）")
    weekly_capacity_minutes = models.PositiveIntegerField(
        default=2400, verbose_name="周容量（分钟，30-60h 可配——负载°分母唯一配置源）",
        validators=[MinValueValidator(1800), MaxValueValidator(3600)])

    class Meta(BaseModel.Meta):
        db_table = "project_worklog_configs"
        verbose_name = "工时项目配置"
        verbose_name_plural = verbose_name
        constraints = [
            models.CheckConstraint(
                check=models.Q(warn_ratio__gte=Decimal("0.5")) & models.Q(warn_ratio__lte=Decimal("1.5")),  # type: ignore[call-arg]  # stub 声明过窄（同 Issue 先例）
                name="chk_worklog_warn_ratio_range"),
            models.CheckConstraint(
                check=models.Q(weekly_capacity_minutes__gte=1800)
                      & models.Q(weekly_capacity_minutes__lte=3600),  # type: ignore[call-arg]  # 同上
                name="chk_worklog_weekly_capacity_range"),
        ]

    def __str__(self) -> str:
        return f"worklog-config({self.project_id})"
