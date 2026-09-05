"""labels + 工作项子资源路由片段（sprint-1 TASK-002 §4.3 + TASK-003 §4.2 + BOARD-002 §4.2.1）。

urls.py 通过 import_module 把本模块的 urlpatterns 自动追加（FEATURE_MODULES = (..., "labels", ...)）。
"""
from django.urls import path

from plane.app.views.issues import (
    IssueActivityListView,
    IssueLabelsView,
    IssueSubIssueListCreateView,
    IssueTypeListView,
)
from plane.app.views.labels import LabelDetailView, LabelListCreateView

urlpatterns = [
    # 类型（= Workspace active）—— 仅 GET，TASK-002 §4.3.1 第 12 行
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issue-types/",
        IssueTypeListView.as_view(),
        name="project-issue-types",
    ),
    # 标签：list / create
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/labels/",
        LabelListCreateView.as_view(),
        name="project-labels-list-create",
    ),
    # 标签：patch / delete
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/labels/<uuid:label_id>/",
        LabelDetailView.as_view(),
        name="project-labels-detail",
    ),
    # 工作项标签集合替换（TASK-002 §4.3.3 PUT 白名单）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/labels/",
        IssueLabelsView.as_view(),
        name="issue-labels",
    ),
    # 工作项子任务（TASK-002 §4.3.4/5）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/sub-issues/",
        IssueSubIssueListCreateView.as_view(),
        name="issue-sub-issues",
    ),
    # 工作项操作日志（TASK-002 §4.3.6）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/activities/",
        IssueActivityListView.as_view(),
        name="issue-activities",
    ),
]
