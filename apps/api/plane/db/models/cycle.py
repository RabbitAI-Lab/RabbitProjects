"""敏捷报表迭代模型（RPT-003 §4.2，Sprint-9）。

Cycle 时间盒 + CycleSnapshot 不可篡改快照（daily + 终版 is_final）+
ProjectReportConfig 度量单例 + DailyGroupSnapshot 项目级五组快照（CFD 源）。
任务归属不建中间表——Issue.cycle 单值外键（架构 §7.4 处置；R0 核对发现
「P0 预留列」仅为架构文档声明、物理列未落地——0033 补列，规格「零 issues
表 DDL」偏差登记 ADR-0033）。
"""
from django.db import models
from django.db.models import Q

from plane.db.models.base import BaseModel


class Cycle(BaseModel):
    """时间盒迭代 —— 落地架构文档 §7.4 处置（Plane 对标见 §7.2）"""

    class Status(models.TextChoices):
        PLANNED = "planned", "规划中"
        ACTIVE = "active", "进行中"
        COMPLETED = "completed", "已完成"

    project = models.ForeignKey(
        "db.Project", on_delete=models.CASCADE, related_name="cycles", verbose_name="所属项目"
    )
    name = models.CharField(max_length=64, verbose_name="迭代名称")
    start_date = models.DateField(verbose_name="开始日期")
    end_date = models.DateField(verbose_name="结束日期")
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PLANNED, db_index=True, verbose_name="状态"
    )

    class Meta(BaseModel.Meta):
        db_table = "cycles"
        constraints = [
            models.CheckConstraint(condition=Q(end_date__gt=models.F("start_date")),
                                   name="chk_cycle_date_range"),
            models.UniqueConstraint(fields=["project"], condition=Q(status="active"),
                                    name="uniq_active_cycle_per_project"),          # BR-03
            models.UniqueConstraint(fields=["project", "name"],
                                    condition=Q(deleted_at__isnull=True),
                                    name="uniq_cycle_name_per_project"),
        ]
        indexes = [models.Index(fields=["project", "status"], name="idx_cycle_project_status")]

    def __str__(self) -> str:
        return f"{self.name}({self.status})"


class CycleSnapshot(BaseModel):
    """迭代日快照 —— BR-05/06 不可篡改性的物理载体。

    数值口径：聚合字段用 FloatField——count/estimate_minutes 度量下取值恒为
    整数（工时以整数分钟存储），float 仅承载 cf_* 数字字段的小数故事点。
    """

    cycle = models.ForeignKey(Cycle, on_delete=models.CASCADE,
                              related_name="snapshots", verbose_name="迭代")
    snapshot_date = models.DateField(verbose_name="快照日期")
    measure = models.CharField(max_length=32, verbose_name="度量口径",
                               help_text="count/estimate_minutes/cf_<uuid>")
    remaining_total = models.FloatField(verbose_name="剩余总量",
        help_text="未达 completed 且未 cancelled 组的度量总和（cancelled 不计剩余）")
    remaining_by_group = models.JSONField(verbose_name="按组分解",
        help_text='{"backlog": 0, "unstarted": 12, "started": 8.5, "completed": 30, "cancelled": 0}')
    completed_delta = models.FloatField(default=0, verbose_name="当日净完成量",
        help_text="当日新进入 completed 的度量 − 当日流出 completed 的度量（重开为负项）；不含 scope change")
    scope_delta = models.FloatField(default=0, verbose_name="当日 scope change 净额",
        help_text="cycles 事件加入 +x / 移出 -x 的度量净额（展示口径，BR-09 ▲▼），不参与恒等式对账")
    cancelled_net_delta = models.FloatField(default=0, verbose_name="当日净转入 cancelled 量",
        help_text="转入 − 回流净口径；cancelled 不计剩余，不得并入展示 delta（恒等式修正项）")
    scope_remaining_delta = models.FloatField(default=0, verbose_name="scope change 计剩余折算净额",
        help_text="cycles 事件按任务当日所处组是否计入剩余折算（恒等式修正项）")
    adjustment_delta = models.FloatField(default=0, verbose_name="估算编辑/任务删除修正量",
        help_text="estimate_minutes 编辑净差（仅计剩余组）+ 任务删除的剩余修正（恒等式修正项）")
    scope_events = models.JSONField(default=list, blank=True, verbose_name="scope 事件清单",
        help_text="complete 时序列化进终版快照（BR-06 归档不可篡改）；日快照恒为空")
    scope_total = models.FloatField(default=0, verbose_name="迭代范围总量",
        help_text="迭代内全部任务度量合计（不含 cancelled）；首日快照即速率图 planned（BR-10）")
    frozen = models.BooleanField(default=False, verbose_name="是否冻结（迭代结束）")
    is_final = models.BooleanField(default=False, verbose_name="终版快照",
        help_text="complete 时落一行 is_final=true（BR-04），速率统计唯一锚")

    class Meta(BaseModel.Meta):
        db_table = "cycle_snapshots"
        constraints = [
            models.UniqueConstraint(fields=["cycle", "snapshot_date"], name="uniq_cycle_snapshot_day"),
            models.UniqueConstraint(fields=["cycle"], condition=Q(is_final=True),
                                    name="uniq_cycle_final_snapshot"),             # BR-04 终版唯一锚
        ]
        indexes = [models.Index(fields=["cycle", "snapshot_date"], name="idx_cs_cycle_day")]

    def __str__(self) -> str:
        return f"cs({self.cycle_id}:{self.snapshot_date})"


class ProjectReportConfig(BaseModel):
    """BR-07 度量口径的项目级单例配置。

    行供给时机：beat / velocity 首日兜底 / on_cycle_completed 三处消费点统一
    `get_or_create(defaults={"report_measure": "count"})` 惰性建行。
    """

    project = models.OneToOneField("db.Project", on_delete=models.CASCADE,
                                   related_name="report_config", verbose_name="项目")
    report_measure = models.CharField(max_length=32, default="count", verbose_name="报表度量口径",
        help_text="count / estimate_minutes / cf_<uuid>（数字类型自定义字段，BR-07）")

    class Meta(BaseModel.Meta):
        db_table = "project_report_configs"

    def __str__(self) -> str:
        return f"rc({self.project_id}:{self.report_measure})"


class DailyGroupSnapshot(BaseModel):
    """项目级每日五组快照 —— CFD 数据源（BR-12），与 Cycle 快照同管道"""

    project = models.ForeignKey("db.Project", on_delete=models.CASCADE,
                                related_name="daily_group_snapshots", verbose_name="项目")
    snapshot_date = models.DateField(verbose_name="快照日期")
    measure = models.CharField(max_length=32, verbose_name="度量口径")
    counts = models.JSONField(verbose_name="五组数量/度量",
        help_text='{"backlog": 42, "unstarted": 18, "started": 9, "completed": 130, "cancelled": 6}')

    class Meta(BaseModel.Meta):
        db_table = "daily_group_snapshots"
        constraints = [models.UniqueConstraint(fields=["project", "snapshot_date", "measure"],
                                               name="uniq_dgs_project_day_measure")]
        indexes = [models.Index(fields=["project", "snapshot_date"], name="idx_dgs_project_day")]

    def __str__(self) -> str:
        return f"dgs({self.project_id}:{self.snapshot_date})"
