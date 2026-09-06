"""file_versions 域路由片段（FILE-003 §4.2 端点表 #1~#11）。

端点全部强制尾斜杠；会话族挂 projects 三层，版本/预览族与 FILE-002 files/
同基座（chunks/{n} 为 int 段——动作子资源，api-conventions §3.2）。
"""
from django.urls import path

from plane.app.views.file_versions import (
    FileDerivativeView,
    FilePreviewView,
    FileVersionContentView,
    FileVersionRollbackView,
    FileVersionsListView,
    UploadSessionChunkView,
    UploadSessionCompleteView,
    UploadSessionDetailView,
    UploadSessionInitView,
)

urlpatterns = [
    # POST …/projects/{project_id}/upload-sessions/（#1 发起）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/upload-sessions/",
        UploadSessionInitView.as_view(),
        name="file-sessions-init",
    ),
    # GET（#2 状态）/ DELETE（#6 取消）…/upload-sessions/{session_id}/
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/upload-sessions/<uuid:session_id>/",
        UploadSessionDetailView.as_view(),
        name="file-session-detail",
    ),
    # POST（#3 换发片预签名）/ PATCH（#4 登记片完成）…/chunks/{n}/
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/upload-sessions/"
        "<uuid:session_id>/chunks/<int:chunk_number>/",
        UploadSessionChunkView.as_view(),
        name="file-session-chunk",
    ),
    # POST …/upload-sessions/{session_id}/complete/（#5 合并 + 落库）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/upload-sessions/"
        "<uuid:session_id>/complete/",
        UploadSessionCompleteView.as_view(),
        name="file-session-complete",
    ),
    # GET …/files/{asset_id}/versions/（#7 版本列表）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/files/<uuid:asset_id>/versions/",
        FileVersionsListView.as_view(),
        name="file-versions-list",
    ),
    # POST …/files/{asset_id}/versions/{version_id}/rollback/（#8 回滚）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/files/<uuid:asset_id>/"
        "versions/<uuid:version_id>/rollback/",
        FileVersionRollbackView.as_view(),
        name="file-version-rollback",
    ),
    # GET …/files/{asset_id}/versions/{version_id}/content/（#9 版本内容换发 302）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/files/<uuid:asset_id>/"
        "versions/<uuid:version_id>/content/",
        FileVersionContentView.as_view(),
        name="file-version-content",
    ),
    # GET …/files/{asset_id}/preview/（#10 预览调度 200/202）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/files/<uuid:asset_id>/preview/",
        FilePreviewView.as_view(),
        name="file-asset-preview",
    ),
    # GET …/files/{asset_id}/derivatives/{kind}/（#11 衍生物换发 302；kind 无扩展名）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/files/<uuid:asset_id>/"
        "derivatives/<str:kind>/",
        FileDerivativeView.as_view(),
        name="file-derivative",
    ),
]
