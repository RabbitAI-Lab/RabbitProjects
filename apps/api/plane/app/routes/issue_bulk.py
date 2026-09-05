"""任务批量操作路由片段（BOARD-004 §4.2 四端点）。

urls.py 通过 import_module 把本模块的 urlpatterns 自动追加
（FEATURE_MODULES = (..., "issue_bulk", ...)）。

挂载与 api-conventions §2.5 工作项层级端点清单的 PATCH/DELETE ``issues/bulk/``
两行一致（同路径双方法 → 同一视图类分派）；``bulk/archive/`` 为 §2.6 动作子
资源批量变体；``bulk/preview/`` 为本迭代新增动作子资源（架构文档待回改登记，
BOARD-004 §4.2 注）。
"""

from django.urls import path

from plane.app.views.issue_bulk import (
    IssueBulkArchiveView,
    IssueBulkPreviewView,
    IssueBulkView,
)

urlpatterns = [
    # #1 PATCH（动作 1-4 批量更新）+ #3 DELETE（动作 6 批量删除）——同路径双方法
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issues/bulk/",
        IssueBulkView.as_view(),
        name="issue-bulk",
    ),
    # #2 POST（动作 5 批量归档）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issues/bulk/archive/",
        IssueBulkArchiveView.as_view(),
        name="issue-bulk-archive",
    ),
    # #4 POST（危险动作预检，只读）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issues/bulk/preview/",
        IssueBulkPreviewView.as_view(),
        name="issue-bulk-preview",
    ),
]
