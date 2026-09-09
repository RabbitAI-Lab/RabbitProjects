"""关键路径模型（GANTT-003 §4.2，Sprint-9）。

CPMAlertConfig 项目级预警配置（BR-14）；IssueCPMCache 非业务事实缓存表
（可整体重建，BR-07 input_hash 指纹命中零写）。
"""
from django.db import models

from plane.db.models.base import BaseModel


class CPMAlertConfig(BaseModel):
    """项目级 CPM 预警配置（BR-06/08/09/13）——每项目一行"""

    project = models.OneToOneField("db.Project", on_delete=models.CASCADE,
                                   related_name="cpm_alert_config", verbose_name="项目")
    overdue_alert_enabled = models.BooleanField(default=True, verbose_name="关键逾期预警（BR-08）")
    float_consumed_alert_enabled = models.BooleanField(default=True, verbose_name="浮动耗尽预警（BR-09）")
    target_completion_date = models.DateField(null=True, blank=True,
                                              verbose_name="项目目标完工日（BR-06 逆推锚点）")

    class Meta(BaseModel.Meta):
        db_table = "cpm_alert_config"
        constraints = [models.UniqueConstraint(fields=["project"],
                                               name="uniq_cpm_alert_config_project")]

    def __str__(self) -> str:
        return f"cpm-cfg({self.project_id})"


class IssueCPMCache(BaseModel):
    """CPM 结果缓存（BR-07）——非业务事实表，可整体重建"""

    project = models.ForeignKey("db.Project", on_delete=models.CASCADE,
                                related_name="cpm_cache", verbose_name="项目")
    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE,
                              related_name="cpm_row", verbose_name="任务")
    es = models.DateField(verbose_name="最早开始")
    ef = models.DateField(verbose_name="最早完成")
    ls = models.DateField(verbose_name="最晚开始")
    lf = models.DateField(verbose_name="最晚完成")
    float_days = models.IntegerField(verbose_name="浮动天数")
    is_critical = models.BooleanField(default=False, verbose_name="是否关键")
    has_external_preds = models.BooleanField(default=False, verbose_name="有外部前置（BR-03）")
    input_hash = models.CharField(max_length=32, verbose_name="输入指纹")
    computed_at = models.DateTimeField(auto_now=True, verbose_name="计算时间")

    class Meta(BaseModel.Meta):
        db_table = "issue_cpm_cache"
        constraints = [models.UniqueConstraint(fields=["project", "issue"],
                                               name="uniq_cpm_cache_issue")]
        indexes = [models.Index(fields=["project", "is_critical"], name="idx_cpm_critical")]

    def __str__(self) -> str:
        return f"cpm({self.issue_id}:f{self.float_days})"
