"""stats 域路由片段（RPT-001 §4.2）。

挂在 /users/me/ 下（用户自资源；workspace 经 query 参数限定）。
"""
from django.urls import path

from plane.app.views.stats import MyIssuesListView, PersonalStatsView

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
]
