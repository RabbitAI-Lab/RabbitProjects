"""attachments 域路由片段（FILE-001 §4.3）。

端点全部强制尾斜杠；路径嵌套层级 = 4（issues 是第 3 层资源，attachments 是
其叶子子资源，符合 api-conventions.md §2.4 上限）。
"""
from django.urls import path

from plane.app.views.assets import (
    AttachmentCompleteView,
    AttachmentDeleteView,
    AttachmentDownloadView,
    AttachmentListView,
    AttachmentPresignView,
)

urlpatterns = [
    # POST .../issues/{issue_id}/attachments/presign/
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/attachments/presign/",
        AttachmentPresignView.as_view(),
        name="attachments-presign",
    ),
    # POST .../attachments/{asset_id}/complete/
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/attachments/<uuid:asset_id>/complete/",
        AttachmentCompleteView.as_view(),
        name="attachments-complete",
    ),
    # GET .../attachments/
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/attachments/",
        AttachmentListView.as_view(),
        name="attachments-list",
    ),
    # GET .../attachments/{asset_id}/download/
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/attachments/<uuid:asset_id>/download/",
        AttachmentDownloadView.as_view(),
        name="attachments-download",
    ),
    # DELETE .../attachments/{asset_id}/
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/attachments/<uuid:asset_id>/",
        AttachmentDeleteView.as_view(),
        name="attachments-delete",
    ),
]
