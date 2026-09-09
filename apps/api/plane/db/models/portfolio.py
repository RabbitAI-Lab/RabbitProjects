"""项目集组合树模型（PROJ-004 §4.2，Sprint-9）。

Portfolio 自引用树（深度 ≤3）+ 项目挂载（至多一）+ 里程碑 + 贡献项。
MilestoneItem.weight 为 estimate_minutes 快照——避免后续估算编辑篡改历史完成度（BR-05）。
"""
import uuid

from django.db import models
from django.db.models import Q

from plane.db.models.base import BaseModel

#: 根组合 parent 归一零值（PG NULL 不参与复合唯一，COALESCE 表达式约束用）
ZERO_UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")


class Portfolio(BaseModel):
    """项目集组合树 —— 深度 ≤3（BR-01），项目挂载叶子（BR-02）"""

    MAX_DEPTH = 3

    workspace = models.ForeignKey(
        "db.Workspace", on_delete=models.CASCADE, related_name="portfolios", verbose_name="所属工作空间"
    )
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True,
        related_name="children", verbose_name="父节点", help_text="NULL = 根组合",
    )
    name = models.CharField(max_length=128, verbose_name="名称")
    description = models.TextField(blank=True, verbose_name="说明")
    manager = models.ForeignKey(
        "db.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="managed_portfolios", verbose_name="项目集负责人",
        help_text="BR-03 数据支撑：manager（含祖先节点 manager）与 WS_ADMIN+ 共同持有管理权；NULL 时仅 WS_ADMIN+",
    )
    depth = models.PositiveSmallIntegerField(default=1, verbose_name="层级（冗余）")
    sort_order = models.FloatField(default=65535.0, verbose_name="排序值")

    class Meta(BaseModel.Meta):
        db_table = "portfolios"
        constraints = [
            # parent 为 NULL 时普通 (parent, name) 唯一约束不判重（PG NULL ≠ NULL），
            # 用 COALESCE 表达式把根组合的 parent 归一到零值 UUID；叠加 workspace 列——
            # 同空间内判重（跨空间允许同名根）；非根节点已由 parent 隐含 workspace，不受影响
            models.UniqueConstraint(
                "workspace",
                models.functions.Coalesce("parent", models.Value(ZERO_UUID)),
                "name",
                condition=Q(deleted_at__isnull=True),
                name="uniq_portfolio_name_per_parent"),
            models.CheckConstraint(condition=Q(depth__gte=1, depth__lte=3),
                                   name="chk_portfolio_depth"),
        ]
        indexes = [models.Index(fields=["workspace", "parent"], name="idx_portfolio_ws_parent")]

    def __str__(self) -> str:
        return f"{self.name}(d{self.depth})"


class PortfolioProject(BaseModel):
    """项目挂载关系 —— 一个项目至多一个项目集（BR-02）"""

    portfolio = models.ForeignKey(
        Portfolio, on_delete=models.CASCADE, related_name="mounted_projects", verbose_name="项目集"
    )
    project = models.ForeignKey(
        "db.Project", on_delete=models.CASCADE, related_name="portfolio_mount", verbose_name="项目"
    )

    class Meta(BaseModel.Meta):
        db_table = "portfolio_projects"
        constraints = [
            models.UniqueConstraint(fields=["project"], condition=Q(deleted_at__isnull=True),
                                    name="uniq_project_single_portfolio"),
        ]

    def __str__(self) -> str:
        return f"{self.portfolio_id}:{self.project_id}"


class PortfolioMilestone(BaseModel):
    portfolio = models.ForeignKey(
        Portfolio, on_delete=models.CASCADE, related_name="milestones", verbose_name="所属项目集"
    )
    name = models.CharField(max_length=128, verbose_name="里程碑名称")
    description = models.TextField(blank=True, verbose_name="说明")
    target_date = models.DateField(db_index=True, verbose_name="截止日期")
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name="完成时间")

    class Meta(BaseModel.Meta):
        db_table = "portfolio_milestones"
        indexes = [models.Index(fields=["portfolio", "target_date"], name="idx_pm_portfolio_target")]

    def __str__(self) -> str:
        return f"{self.name}@{self.target_date}"


class MilestoneItem(BaseModel):
    """里程碑贡献项 —— 权重快照避免 estimate 后续变更篡改历史完成度（BR-05）"""

    milestone = models.ForeignKey(
        PortfolioMilestone, on_delete=models.CASCADE, related_name="items", verbose_name="里程碑"
    )
    issue = models.ForeignKey(
        "db.Issue", on_delete=models.CASCADE, related_name="milestone_items", verbose_name="贡献任务"
    )
    weight = models.PositiveIntegerField(default=1, verbose_name="权重快照")

    class Meta(BaseModel.Meta):
        db_table = "milestone_items"
        constraints = [
            models.UniqueConstraint(fields=["milestone", "issue"],
                                    condition=Q(deleted_at__isnull=True),
                                    name="uniq_milestone_issue"),
        ]

    def __str__(self) -> str:
        return f"{self.milestone_id}:{self.issue_id}"
