"""cycles 域路由片段（RPT-003 — 敏捷报表迭代，Sprint-9）。

10 端点：迭代 CRUD / start / complete / 整批设置任务 / 燃尽 / 速率 / CFD /
度量配置 / CSV 导出。
"""
from django.urls import path

from plane.app.views.cycles import (
    CumulativeFlowView,
    CycleBurndownExportView,
    CycleBurndownView,
    CycleCompleteView,
    CycleDetailView,
    CycleIssuesBatchView,
    CycleListCreateView,
    CycleStartView,
    ReportConfigView,
    VelocityView,
)

urlpatterns = [
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/cycles/",
        CycleListCreateView.as_view(),
        name="cycles-list-create",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/cycles/<uuid:cycle_id>/",
        CycleDetailView.as_view(),
        name="cycles-detail",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/cycles/<uuid:cycle_id>/start/",
        CycleStartView.as_view(),
        name="cycles-start",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/cycles/<uuid:cycle_id>/complete/",
        CycleCompleteView.as_view(),
        name="cycles-complete",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/cycles/<uuid:cycle_id>/issues/",
        CycleIssuesBatchView.as_view(),
        name="cycles-issues-batch",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/cycles/<uuid:cycle_id>/burndown/",
        CycleBurndownView.as_view(),
        name="cycles-burndown",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/cycles/<uuid:cycle_id>/burndown/export/",
        CycleBurndownExportView.as_view(),
        name="cycles-burndown-export",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/reports/velocity/",
        VelocityView.as_view(),
        name="reports-velocity",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/reports/cumulative-flow/",
        CumulativeFlowView.as_view(),
        name="reports-cumulative-flow",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/reports/config/",
        ReportConfigView.as_view(),
        name="reports-config",
    ),
]
