"""health_reports 域路由片段（RPT-004 — 项目健康度，Sprint-9）：6+1 端点。"""
from django.urls import path

from plane.app.views.health import (
    ExportTaskStatusView,
    HealthConfigView,
    HealthDrilldownView,
    HealthReportView,
    HealthTrendView,
    WorkloadExportView,
    WorkloadView,
)

urlpatterns = [
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/reports/health/",
        HealthReportView.as_view(),
        name="reports-health",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/reports/health/drilldown/",
        HealthDrilldownView.as_view(),
        name="reports-health-drilldown",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/reports/health/trend/",
        HealthTrendView.as_view(),
        name="reports-health-trend",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/reports/health/config/",
        HealthConfigView.as_view(),
        name="reports-health-config",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/reports/workload/",
        WorkloadView.as_view(),
        name="reports-workload",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/reports/workload/export/",
        WorkloadExportView.as_view(),
        name="reports-workload-export",
    ),
    path(
        "workspaces/<slug:slug>/exports/<uuid:task_id>/",
        ExportTaskStatusView.as_view(),
        name="export-task-status",
    ),
]
