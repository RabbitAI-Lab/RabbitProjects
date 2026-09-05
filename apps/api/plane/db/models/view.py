"""保存的视图（BOARD-003 §4.1.1，落地架构 dynamic-fields-design.md §5.6）。

IssueView 是视图体系唯一事实源：BOARD-003 定义并交付，TASK-011 仅消费不另定义
（sprint-overview 风险 #6 的收口）。四大布局共用 filters 与 display_props，
切换布局只改 layout，条件不丢（§1.2 正交原则）。
"""
from django.db import models

from plane.db.models.base import BaseModel


class IssueView(BaseModel):
    """筛选 + 排序 + 分组 + 显示列 + 布局的组合。

    access=shared 与 is_locked 的 UI/权限面归 P3 BOARD-005，列本迭代建好。
    """

    class Access(models.TextChoices):
        PERSONAL = "personal", "个人视图"
        SHARED = "shared", "共享视图"  # P3 开放

    class Layout(models.TextChoices):
        LIST = "list", "列表"
        KANBAN = "kanban", "看板"
        GANTT = "gantt", "甘特图"
        TABLE = "table", "表格"

    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="views",
        verbose_name="所属工作空间",
    )
    project = models.ForeignKey(
        "db.Project",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="views",
        verbose_name="所属项目",
        help_text="为空表示跨项目全局视图（P3）",
    )
    owner = models.ForeignKey(
        "db.User", on_delete=models.CASCADE, related_name="views", verbose_name="创建者"
    )
    name = models.CharField(max_length=128, verbose_name="视图名称")
    description = models.TextField(blank=True, verbose_name="视图说明")
    access = models.CharField(
        max_length=16,
        choices=Access.choices,
        default=Access.PERSONAL,
        verbose_name="访问范围",
    )
    layout = models.CharField(
        max_length=16,
        choices=Layout.choices,
        default=Layout.LIST,
        verbose_name="布局",
        help_text="架构 §5.6 默认 list",
    )
    filters = models.JSONField(
        default=dict,
        verbose_name="筛选条件树（DSL）",
        help_text='扁平形态：{"op":"AND","conditions":[{"field":"priority","operator":"in","value":["urgent"]}]}',
    )
    display_props = models.JSONField(
        default=dict,
        verbose_name="展示配置",
        help_text=(
            '{"icon":"🚒","group_by":"priority","order_by":"sort_order",'
            '"columns":["issue_key","name","state_id","assignee_ids","target_date"],'
            '"card_fields":{"labels":true,"sub_issues":true,"attachments":true,'
            '"estimate":false,"priority":true,"timer":true,"target_date":true,'
            '"cf_severity":true},"show_empty_groups":true,"sub_group_by":null}'
        ),
    )
    is_system = models.BooleanField(
        default=False, verbose_name="内置视图", help_text="需求池/缺陷列表等，不可删除、filters 锁定"
    )
    is_locked = models.BooleanField(default=False, verbose_name="管理员锁定（P3）")
    sort_order = models.FloatField(default=65535.0, verbose_name="显示排序")

    class Meta(BaseModel.Meta):
        db_table = "issue_views"
        verbose_name = "任务视图"
        verbose_name_plural = "任务视图"
        ordering = ("sort_order", "created_at")  # type: ignore[assignment]  # stub 声明过窄（同 CustomFieldDefinition）
        indexes = [
            models.Index(fields=["project", "access", "sort_order"], name="idx_view_proj_access"),
            models.Index(fields=["owner", "access"], name="idx_view_owner_access"),
        ]

    def __str__(self) -> str:
        return f"{self.name}（{'内置' if self.is_system else '个人'}）"
