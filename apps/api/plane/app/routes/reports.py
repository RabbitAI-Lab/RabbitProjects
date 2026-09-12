"""reports 域路由片段（RPT-005，P4 R7）。"""

from django.urls import path

from plane.app.views.reports import (
    DashboardView,
    DisplayHeartbeatView,
    DisplayScreenView,
    DisplayTokenView,
    ReportDetailView,
    ReportListCreateView,
    ReportMetricsView,
    ReportPreviewView,
    SubscriptionView,
)

urlpatterns = [
    path("workspaces/<slug:slug>/reports/", ReportListCreateView.as_view(), name="rpt-reports"),
    path("workspaces/<slug:slug>/reports/preview/", ReportPreviewView.as_view(), name="rpt-preview"),
    path("workspaces/<slug:slug>/reports/metrics/", ReportMetricsView.as_view(), name="rpt-metrics"),
    path("workspaces/<slug:slug>/reports/<uuid:report_id>/", ReportDetailView.as_view(), name="rpt-report-detail"),
    path(
        "workspaces/<slug:slug>/reports/<uuid:report_id>/subscriptions/",
        SubscriptionView.as_view(),
        name="rpt-subscriptions",
    ),
    path("workspaces/<slug:slug>/dashboards/", DashboardView.as_view(), name="rpt-dashboards"),
    path(
        "workspaces/<slug:slug>/dashboards/<uuid:dashboard_id>/", DashboardView.as_view(), name="rpt-dashboard-detail"
    ),
    path(
        "workspaces/<slug:slug>/dashboards/<uuid:dashboard_id>/display-tokens/",
        DisplayTokenView.as_view(),
        name="rpt-display-tokens",
    ),
    path(
        "workspaces/<slug:slug>/dashboards/<uuid:dashboard_id>/display-tokens/<uuid:token_id>/",
        DisplayTokenView.as_view(),
        name="rpt-display-token-detail",
    ),
    path("display/<str:token>/screen/<int:screen_index>/", DisplayScreenView.as_view(), name="rpt-display-screen"),
    path("display/<str:token>/heartbeat/", DisplayHeartbeatView.as_view(), name="rpt-display-heartbeat"),
]
