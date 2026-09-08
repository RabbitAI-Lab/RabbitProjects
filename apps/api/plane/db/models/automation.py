"""自动化规则三表（WF-003 §4.1）——Sprint-7 R3 引擎载体。

AutomationRule：项目级规则定义（trigger/conditions/actions 三个 JSONB 协议字段）
+ 启用熔断计数（BR-13）。
AutomationRun：执行日志（BigAutoField 主键保时序，BR-12 90 天清理）。
AutomationSetting：项目级 OneToOne，allow_rule_chain 默认 False（BR-07 闸 1 放开开关）。

偏差登记（统一继承原则的显式例外）：AutomationRun 不继承 BaseModel——
日志表瘦身（仅 created_at，不参与软删与审计字段，90 天物理清理即生命周期）。
AutomationRule/Setting 均正常继承 BaseModel。
"""
from django.db import models

from plane.db.models.base import BaseModel


class AutomationRule(BaseModel):
    """项目级自动化规则——四触发器×五动作的受限 DSL（WF-003 §2.1/§2.2）。"""

    class TriggerType(models.TextChoices):
        STATE_CHANGED = "state_changed", "状态变更"
        ISSUE_CREATED = "issue_created", "任务创建"
        FIELD_CHANGED = "field_changed", "字段变更"
        DUE_APPROACHING = "due_approaching", "截止临近"

    project = models.ForeignKey(
        "db.Project", on_delete=models.CASCADE, related_name="automation_rules", verbose_name="所属项目")
    name = models.CharField(max_length=64, verbose_name="规则名称")
    trigger = models.JSONField(
        verbose_name="触发器",
        help_text='{"type": "state_changed|issue_created|due_approaching|field_changed", "config": {…}}')
    conditions = models.JSONField(
        default=list, verbose_name="条件（AND 组合，FilterCompiler 子集）",
        help_text="条件数 ≤ 20（继承 TASK-011 冻结，§2.7）")
    actions = models.JSONField(
        verbose_name="动作（1..5 个，顺序执行）",
        help_text="复用 WF-001 §4.7 动作协议 + transition/add_label")
    dedup_window_minutes = models.PositiveIntegerField(default=60, verbose_name="去重窗口（BR-09）")
    is_active = models.BooleanField(default=True, verbose_name="启用")
    consecutive_failures = models.PositiveIntegerField(default=0, verbose_name="BR-13 连续失败熔断计数")

    class Meta(BaseModel.Meta):
        db_table = "automation_rules"
        verbose_name = "自动化规则"
        verbose_name_plural = verbose_name
        constraints = [
            # 软删偏条件唯一：已删规则名可复用（与 issues.uniq_issue_sequence_per_project 同形）
            models.UniqueConstraint(
                fields=["project", "name"], condition=models.Q(deleted_at__isnull=True),
                name="uniq_rule_name_per_project"),
        ]
        indexes = [models.Index(fields=["project", "is_active"], name="idx_rule_active")]

    def __str__(self) -> str:
        return f"{self.project_id}:{self.name}({'on' if self.is_active else 'off'})"


class AutomationRun(models.Model):
    """执行日志（BR-12 90 天留存；成员可读透明）。"""

    class Status(models.TextChoices):
        RUNNING = "running", "执行中"
        SUCCESS = "success", "成功"
        FAILED = "failed", "失败"
        SKIPPED = "skipped", "跳过"

    id = models.BigAutoField(primary_key=True)
    rule = models.ForeignKey(AutomationRule, on_delete=models.CASCADE, related_name="runs", verbose_name="规则")
    issue = models.ForeignKey(
        "db.Issue", null=True, on_delete=models.SET_NULL, related_name="+", verbose_name="任务")
    event = models.JSONField(verbose_name="事件快照（type/payload/origin/chain_depth）")
    status = models.CharField(max_length=8, choices=Status.choices, verbose_name="状态")
    skip_reason = models.CharField(
        max_length=32, blank=True, default="",
        verbose_name="跳过原因",
        help_text="dedup|origin|chain_depth")
    action_results = models.JSONField(
        default=list, verbose_name='动作明细（[{"type":"assign","ok":true,"detail":{…}}, …]）')
    duration_ms = models.PositiveIntegerField(default=0, verbose_name="执行耗时（毫秒）")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        db_table = "automation_runs"
        verbose_name = "自动化执行日志"
        verbose_name_plural = verbose_name
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["rule", "created_at"], name="idx_run_rule"),
            models.Index(fields=["created_at"], name="idx_run_retention"),
        ]

    def __str__(self) -> str:
        return f"run {self.id}:{self.status}"


class AutomationSetting(BaseModel):
    """项目级自动化设置（BR-07 闸 1：allow_rule_chain 默认 False）。"""

    project = models.OneToOneField(
        "db.Project", on_delete=models.CASCADE, related_name="automation_setting", verbose_name="所属项目")
    allow_rule_chain = models.BooleanField(default=False, verbose_name="允许规则级联触发（默认关）")

    class Meta(BaseModel.Meta):
        db_table = "automation_settings"
        verbose_name = "自动化设置"
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return f"automation-setting({self.project_id})"
