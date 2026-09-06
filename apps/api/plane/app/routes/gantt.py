"""gantt 域路由片段（GANTT-001 §4.2）。

挂在项目子资源下（视窗行取数 / 连线批量 / 未排期列表——甘特渲染只读地基，
交互层（拖拽改期/导出）归 GANTT-002，届时复用本前缀）。
"""
from django.urls import path

from plane.app.views.gantt import (
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
]
