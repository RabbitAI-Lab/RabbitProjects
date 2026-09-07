"""stats 域路由片段（RPT-001 §4.2）。

挂在 /users/me/ 下（用户自资源；workspace 经 query 参数限定）。
"""
from django.urls import path

from plane.app.views.stats import (
    MyIssuesListView,
    PersonalStatsView,
    ProjectMemberStatsView,
    ProjectStatsView,
)

urlpatterns = [
    # 四卡 + 7 日趋势
    path(
        "users/me/issues/stats/",
        PersonalStatsView.as_view(),
        name="users-me-issues-stats",
    ),
    # 我的待办列表（与统计卡共用基座，BR-01）
    path(
        "users/me/issues/",
        MyIssuesListView.as_view(),
        name="users-me-issues",
    ),
    # ── Sprint-5（RPT-002 §4.2）：项目维度双端点 ──
    # 项目进度（五组/完成率/逾期/工时/趋势）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/stats/",
        ProjectStatsView.as_view(),
        name="project-stats",
    ),
    # 成员任务量（分组聚合 + 未指派 + 合计）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/stats/members/",
        ProjectMemberStatsView.as_view(),
        name="project-stats-members",
    ),
]
