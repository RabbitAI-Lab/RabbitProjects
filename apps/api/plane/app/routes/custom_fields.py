"""自定义字段路由片段（TASK-008 §4.2）。

urls.py 通过 import_module 把本模块的 urlpatterns 自动追加（FEATURE_MODULES）。
"""

from django.urls import path

from plane.app.views.custom_fields import (
    FieldSchemaView,
    IssuePropertyDetailView,
    IssuePropertyListCreateView,
    IssuePropertySortOrderView,
    WorkspaceIssuePropertyListCreateView,
)

urlpatterns = [
    # Schema API（ETag/304 协商缓存；TASK-011 筛选器 / 动态表单三方依赖的冻结契约）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/field-schema/",
        FieldSchemaView.as_view(),
        name="project-field-schema",
    ),
    # 字段管理：list / create（PROJ_ADMIN；?scope=all|global|project）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issue-properties/",
        IssuePropertyListCreateView.as_view(),
        name="project-issue-properties-list-create",
    ),
    # 字段管理：patch（不可变保护）/ delete（202 异步清理）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issue-properties/<uuid:property_id>/",
        IssuePropertyDetailView.as_view(),
        name="project-issue-properties-detail",
    ),
    # 拖拽排序（prev_id/next_id 浮点插值，BOARD-001 同算法）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issue-properties/<uuid:property_id>/sort-order/",
        IssuePropertySortOrderView.as_view(),
        name="project-issue-properties-sort-order",
    ),
    # WS 全局字段管理（WS Admin，BR-15）
    path(
        "workspaces/<slug:slug>/issue-properties/",
        WorkspaceIssuePropertyListCreateView.as_view(),
        name="workspace-issue-properties-list-create",
    ),
]
