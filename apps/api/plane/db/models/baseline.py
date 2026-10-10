"""任务基线模型（TASK-015，P4 R3）。

项目计划的时间胶囊：Baseline 头（多套并存 ≤11，对齐 MS Project）+
BaselineItem 快照行（计划字段冻结副本，只写一次 BR-01）。对比/统计/
导出全为读路径（§2.1）；「REVOKE UPDATE/DELETE 于应用账号」的 DB 层
只读强化为部署期动作（dev 单账号部署无独立 rp_app 角色，登记 §7.1
部署清单——模型侧以「API 无写路径」承载 BR-01 语义）。
"""

from django.db import models

from plane.db.models.base import BaseModel


class Baseline(BaseModel):
    """基线头（一套）；配额仅计 building/ready（BR-02，failed 不占）。"""

    class Status(models.TextChoices):
        BUILDING = "building", "创建中"
        READY = "ready", "就绪"
        FAILED = "failed", "失败"

    project = models.ForeignKey("db.Project", on_delete=models.CASCADE, related_name="baselines")
    name = models.CharField(max_length=32)
    # 需求文档 §3.4「变更原因必填」的落地（BR-13）：序列化器 required，缺失 400
    reason = models.CharField(max_length=255, verbose_name="变更原因")
    note = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.BUILDING)
    issue_count = models.PositiveIntegerField(default=0)
    stats_cache = models.JSONField(default=dict)  # 创建时刻的基准统计

    class Meta(BaseModel.Meta):
        db_table = "baseline"
        constraints = [
            models.UniqueConstraint(fields=["project", "name"], name="uq_baseline_project_name"),
        ]
        indexes = [
            models.Index(fields=["project", "-created_at"], name="idx_baseline_project"),
        ]

    def __str__(self) -> str:
        return f"{self.name}({self.status})"


class BaselineItem(BaseModel):
    """单行快照；只写一次（BR-01）。API 无单行写路径。

    issue 侧 SET_NULL 为防御性兜底（BR-04/05 行保留承诺）：上游现无 issue
    物理删除路径；若未来启用，快照行保留、issue_id 置 NULL → 对比「已彻底
    清除」（§4.3 purged）。禁用 CASCADE：级联物理删快照行违背不可变承诺。
    """

    baseline = models.ForeignKey(Baseline, on_delete=models.CASCADE, related_name="items")
    issue = models.ForeignKey(
        "db.Issue", on_delete=models.SET_NULL, null=True, blank=True, related_name="baseline_items"
    )
    sequence_id = models.PositiveIntegerField()  # 冗余编号，删后仍可辨认
    name_snapshot = models.CharField(max_length=512)  # 对齐 Issue.name varchar(512)
    start_date = models.DateField(null=True)
    target_date = models.DateField(null=True)  # Issue.target_date 同名快照
    estimate_minutes = models.PositiveIntegerField(null=True)
    assignee_ids = models.JSONField(default=list)
    state_group = models.CharField(max_length=16)
    is_critical = models.BooleanField(default=False)
    total_float_days = models.IntegerField(null=True)  # 快照自 GANTT-003 float_days

    class Meta(BaseModel.Meta):
        db_table = "baseline_item"
        constraints = [
            models.UniqueConstraint(fields=["baseline", "issue"], name="uq_baseline_item_issue"),
        ]
        indexes = [
            models.Index(fields=["baseline", "issue"], name="idx_baseline_item_pair"),
        ]

    def __str__(self) -> str:
        return f"{self.baseline_id}#{self.sequence_id}"
