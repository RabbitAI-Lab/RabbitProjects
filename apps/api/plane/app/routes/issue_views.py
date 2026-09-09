"""保存的视图路由片段（BOARD-003 §4.2）。

urls.py 通过 import_module 把本模块的 urlpatterns 自动追加（FEATURE_MODULES）。
TASK-011 消费同一端点族（views/ CRUD 以 BOARD-003 为唯一事实源，不另建）。
"""

from django.urls import path

from plane.app.views.issue_views import IssueViewDetailView, IssueViewListCreateView
from plane.app.views.view_governance import (
    MyViewPreferencesView,
    ViewDuplicateView,
    ViewLockView,
    ViewPinView,
)

urlpatterns = [
    # 视图 Tabs 列表（内置+本人，全量无分页）/ 创建（view.create.own 全员）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/views/",
        IssueViewListCreateView.as_view(),
        name="project-views-list-create",
    ),
    # 详情 / PATCH（本人或 board.manage）/ 软删（204，is_system 拒绝）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/views/<uuid:view_id>/",
        IssueViewDetailView.as_view(),
        name="project-views-detail",
    ),
]


# ── BOARD-005（Sprint-8 R5）：视图治理 ──
urlpatterns += [
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/views/<uuid:view_id>/lock/",
        ViewLockView.as_view(), name="view-lock",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/views/<uuid:view_id>/duplicate/",
        ViewDuplicateView.as_view(), name="view-duplicate",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/views/<uuid:view_id>/pin/",
        ViewPinView.as_view(), name="view-pin",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/views/preferences/",
        MyViewPreferencesView.as_view(), name="my-view-preferences",
    ),
]
