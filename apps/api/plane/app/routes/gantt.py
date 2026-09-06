"""gantt 域路由片段（GANTT-001 §4.2 + GANTT-002 §4.2.1）。

挂在项目子资源下（视窗行取数 / 连线批量 / 未排期列表——甘特渲染只读地基；
延期概览聚合 + 端点级限流归 GANTT-002 §4.2.1）。拖拽写通道零新端点（Issue PATCH 既有）。
"""
from django.urls import path

from plane.app.views.gantt import (
    GanttOverdueSummaryView,
    GanttRelationsBulkView,
    GanttRowsView,
    GanttUnscheduledView,
)

urlpatterns = [
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/gantt/",
        GanttRowsView.as_view(),
        name="project-gantt-rows",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/gantt/relations/bulk/",
        GanttRelationsBulkView.as_view(),
        name="project-gantt-relations-bulk",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/gantt/unscheduled/",
        GanttUnscheduledView.as_view(),
        name="project-gantt-unscheduled",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/gantt/overdue-summary/",
        GanttOverdueSummaryView.as_view(),
        name="project-gantt-overdue-summary",
    ),
]
