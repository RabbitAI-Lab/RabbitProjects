"""项目健康度模型（RPT-004 §4.1，Sprint-9）。

HealthSnapshot 日快照（config 快照保证历史不重算，BR-02/03）+
HealthConfig 项目级阈值/权重（周容量分母不落本表——唯一配置源
ProjectWorklogConfig.weekly_capacity_minutes，TASK-013 §4.2 归属裁定）。
"""
from django.db import models

from plane.db.models.base import BaseModel


def default_health_weights() -> dict:
    return dict(DEFAULT_HEALTH_WEIGHTS)


def default_health_thresholds() -> dict:
    return dict(DEFAULT_HEALTH_THRESHOLDS)


DEFAULT_HEALTH_WEIGHTS = {"progress": 0.25, "overdue": 0.25, "effort": 0.25, "blocked": 0.25}
DEFAULT_HEALTH_THRESHOLDS = {"progress": 0.05, "overdue": 0.10, "effort": 0.20, "blocked": 0.05}


class HealthSnapshot(BaseModel):
    """健康度日快照 —— BR-03 趋势数据源；config 快照保证历史不重算。"""

    project = models.ForeignKey("db.Project", on_delete=models.CASCADE,
                                related_name="health_snapshots", verbose_name="项目")
    snapshot_date = models.DateField(verbose_name="快照日期")
    dimensions = models.JSONField(verbose_name="四维明细",
        help_text='{"progress": {"value": -0.07, "score": 86, "n": 128, "drilldown_count": 38}, …}'
                  '——维度为 null 即样本不足（BR-05）')
    total_score = models.FloatField(null=True, verbose_name="总评（null = 数据不足，BR-10）")
    band = models.CharField(max_length=16, verbose_name="分档",
                            help_text="green/yellow/red/insufficient——16 位容纳 insufficient（BR-10）")
    config_snapshot = models.JSONField(verbose_name="阈值权重快照")

    class Meta(BaseModel.Meta):
        db_table = "health_snapshots"
        constraints = [models.UniqueConstraint(fields=["project", "snapshot_date"],
                                               name="uniq_health_snapshot_day")]
        indexes = [models.Index(fields=["project", "snapshot_date"], name="idx_hs_project_day")]

    def __str__(self) -> str:
        return f"hs({self.project_id}:{self.snapshot_date}:{self.total_score})"


class HealthConfig(BaseModel):
    """健康度阈值/权重项目级配置（BR-02）——每项目一行，读侧 of() get_or_create 兜底。"""

    project = models.OneToOneField("db.Project", on_delete=models.CASCADE,
                                   related_name="health_config", verbose_name="项目")
    weights = models.JSONField(default=default_health_weights,
                               verbose_name="四维权重（和恒为 1，序列化层校验）")
    thresholds = models.JSONField(default=default_health_thresholds,
                                  verbose_name="四维参考阈值（维度卡参考线/超标角标，不入评分——BR-02）",
                                  help_text="effort 键为允许偏差半宽（默认 0.20 即 0.8~1.2）")

    class Meta(BaseModel.Meta):
        db_table = "health_configs"

    @classmethod
    def of(cls, project) -> "HealthConfig":
        cfg, _ = cls.objects.get_or_create(project=project)
        return cfg

    def as_dict(self) -> dict:           # 快照 config_snapshot 载荷（BR-02/03）
        return {"weights": self.weights, "thresholds": self.thresholds}

    def __str__(self) -> str:
        return f"hc({self.project_id})"


class ExportTask(BaseModel):
    """异步导出任务（S7 A#1 + S8 A#1 债统一接入，GANTT-002 §4.5 两段式范式）。

    大容量报表/审计导出：POST 触发 → 202 {task_id, status_url} → worker 生成
    文件落 MinIO → 轮询 status=pending/running/succeeded/failed → succeeded 时
    下发 presigned URL（短时效）。小请求（行数阈值内）仍同步流式，不经本表。
    """

    class Status(models.TextChoices):
        PENDING = "pending", "排队中"
        RUNNING = "running", "进行中"
        SUCCEEDED = "succeeded", "已完成"
        FAILED = "failed", "失败"

    EXPORT_TYPES = ("workload_csv", "audit_csv", "burndown_csv")

    export_type = models.CharField(max_length=32, verbose_name="导出类型",
                                   help_text="workload_csv / audit_csv / burndown_csv")
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE,
                                  related_name="export_tasks", verbose_name="工作空间")
    project = models.ForeignKey("db.Project", on_delete=models.CASCADE, null=True, blank=True,
                                related_name="export_tasks", verbose_name="项目（可空）")
    params = models.JSONField(default=dict, verbose_name="导出参数（from/to/筛选）")
    status = models.CharField(max_length=16, choices=Status.choices,
                              default=Status.PENDING, db_index=True, verbose_name="状态")
    file_key = models.CharField(max_length=256, blank=True, verbose_name="MinIO 对象键")
    download_url = models.TextField(blank=True, verbose_name="预签名下载 URL（生成时写入）")
    error = models.TextField(blank=True, verbose_name="失败原因")
    expires_at = models.DateTimeField(null=True, blank=True, verbose_name="URL 过期时间")

    class Meta(BaseModel.Meta):
        db_table = "export_tasks"
        indexes = [models.Index(fields=["workspace", "status"], name="idx_export_ws_status"),
                   models.Index(fields=["created_by", "-created_at"], name="idx_export_creator")]

    def __str__(self) -> str:
        return f"export({self.export_type}:{self.status})"
