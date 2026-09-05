"""activity_stream 域路由片段（COLLAB-003 §4.2）。

嵌套在 project 下：
  …/workspaces/<slug>/projects/<uuid>/activities/          动态流（?epoch= 明细同端点分支）

任务级时间线（…/issues/<uuid>/activities/，TASK-010 冻结表面）不在本文件——
两级动态按消费场景分工（COLLAB-003 §1.2），互不替代。
"""
from django.urls import path

from plane.app.views.activity_stream import ProjectActivityStreamView

urlpatterns = [
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/activities/",
        ProjectActivityStreamView.as_view(),
        name="project-activities",
    ),
]
