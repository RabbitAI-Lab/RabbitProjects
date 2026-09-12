"""企业数据大屏与自定义报表模型（RPT-005，P4 R7）。

Report（自定义报表——config 声明数据集/指标/维度/过滤）、Dashboard
（大屏布局 24 栅格）、DisplayToken（大屏播放匿名令牌——256-bit 哈希
落库）、ReportSubscription（周期订阅）。数据底座零新建聚合表——指标
全部映射上游既有产出（§4.7 注册表，Issue 实时聚合为基座）。
"""

from django.db import models

from plane.db.models.base import BaseModel


class Report(BaseModel):
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="reports")
    name = models.CharField(max_length=64)
    description = models.CharField(max_length=255, blank=True)
    config = models.JSONField(default=dict)
    # {"dataset": "ds_issue_live", "metrics": ["m_done_count"],
    #  "dimensions": ["d_month"], "filters": {}, "chart_type": "bar"}
    owner = models.ForeignKey("db.User", on_delete=models.PROTECT, related_name="reports_owned")
    is_shared = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)  # 乐观锁

    class Meta(BaseModel.Meta):
        db_table = "rpt_report"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"], condition=models.Q(deleted_at__isnull=True), name="uq_report_ws_name"
            ),
        ]


class Dashboard(BaseModel):
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="dashboards")
    name = models.CharField(max_length=64)
    layout = models.JSONField(default=dict)
    # {"grid": 24, "items": [{"report_id": …, "x":0,"y":0,"w":8,"h":6,
    #                          "refresh_s": 300}], "params": {"range":"90d"}}
    theme = models.CharField(max_length=12, default="dark")
    owner = models.ForeignKey("db.User", on_delete=models.PROTECT, related_name="dashboards_owned")

    class Meta(BaseModel.Meta):
        db_table = "rpt_dashboard"


class DisplayToken(BaseModel):
    """大屏播放令牌（匿名 BR-07——256-bit token 仅哈希落库防枚举）。"""

    dashboard = models.ForeignKey(Dashboard, on_delete=models.CASCADE, related_name="tokens")
    token_hash = models.CharField(max_length=64, unique=True)
    token_prefix = models.CharField(max_length=12)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)  # 心跳

    class Meta(BaseModel.Meta):
        db_table = "rpt_display_token"


class ReportSubscription(BaseModel):
    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name="subscriptions")
    schedule = models.CharField(max_length=8)  # daily/weekly
    channel = models.JSONField(default=dict)
    # {"type":"webhook","subscription_id":…} / {"type":"email","recipients":[…]}
    is_active = models.BooleanField(default=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        db_table = "rpt_subscription"
