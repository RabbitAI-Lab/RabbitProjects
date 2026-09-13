"""baseline 域路由片段（TASK-015，P4 R3）。"""

from django.urls import path

from plane.app.views.baselines import (
    BaselineCompareView,
    BaselineDetailView,
    BaselineExportView,
    BaselineListCreateView,
    BaselineStatsView,
)

urlpatterns = [
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/baselines/",
        BaselineListCreateView.as_view(),
        name="baselines",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/baselines/<uuid:baseline_id>/",
        BaselineDetailView.as_view(),
        name="baseline-detail",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/baselines/<uuid:baseline_id>/compare/",
        BaselineCompareView.as_view(),
        name="baseline-compare",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/baselines/<uuid:baseline_id>/stats/",
        BaselineStatsView.as_view(),
        name="baseline-stats",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/baselines/<uuid:baseline_id>/export/",
        BaselineExportView.as_view(),
        name="baseline-export",
    ),
]
