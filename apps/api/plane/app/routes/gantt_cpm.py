"""gantt_cpm 域路由片段（GANTT-003 — 关键路径，Sprint-9）：3 端点。"""
from django.urls import path

from plane.app.views.gantt_cpm import (
    GanttCPMConfigView,
    GanttCriticalPathRecomputeView,
    GanttCriticalPathView,
)

urlpatterns = [
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/gantt/critical-path/",
        GanttCriticalPathView.as_view(),
        name="gantt-critical-path",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/gantt/critical-path/recompute/",
        GanttCriticalPathRecomputeView.as_view(),
        name="gantt-critical-path-recompute",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/gantt/cpm-config/",
        GanttCPMConfigView.as_view(),
        name="gantt-cpm-config",
    ),
]
