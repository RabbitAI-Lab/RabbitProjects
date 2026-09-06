"""file_library 域路由片段（FILE-002 §4.2）。

端点全部强制尾斜杠。路径层级说明：projects(3)/folders(4)/files(5) 的五段
嵌套是规格 §4.2 端点表的契约路径（api-conventions §2.4 常规上限 4 的显式
豁免——files 是 folders 的叶子子资源，与 attachments 前例同判定方式）。
files/trash/ 与 files/storage/ 为字面量段，先于 <uuid:asset_id> 注册。
"""
from django.urls import path

from plane.app.views.file_library import (
    FileCompleteView,
    FileDetailView,
    FileDownloadUrlView,
    FilePurgeView,
    FileRestoreView,
    FileStorageView,
    FileTrashListView,
    FolderDetailView,
    FolderFilePresignView,
    FolderFilesListView,
    FolderListCreateView,
)

urlpatterns = [
    # GET（目录树）/ POST（新建）…/projects/{project_id}/folders/
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/folders/",
        FolderListCreateView.as_view(),
        name="file-folders-list-create",
    ),
    # PATCH（改名/移动/可见性）/ DELETE（整树软删）…/folders/{folder_id}/
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/folders/<uuid:folder_id>/",
        FolderDetailView.as_view(),
        name="file-folder-detail",
    ),
    # GET …/folders/{folder_id}/files/
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/folders/<uuid:folder_id>/files/",
        FolderFilesListView.as_view(),
        name="file-folder-files",
    ),
    # POST …/folders/{folder_id}/files/presign/
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/folders/<uuid:folder_id>/files/presign/",
        FolderFilePresignView.as_view(),
        name="file-files-presign",
    ),
    # 字面量段先于 <uuid:asset_id>（uuid 转换器不冲突，显式排序便于排查）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/files/trash/",
        FileTrashListView.as_view(),
        name="file-trash-list",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/files/storage/",
        FileStorageView.as_view(),
        name="file-storage",
    ),
    # GET …/files/{asset_id}/download-url/
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/files/<uuid:asset_id>/download-url/",
        FileDownloadUrlView.as_view(),
        name="file-asset-download-url",
    ),
    # PATCH（重命名/移动/双挂/可见性）/ DELETE（软删）…/files/{asset_id}/
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/files/<uuid:asset_id>/",
        FileDetailView.as_view(),
        name="file-asset-detail",
    ),
    # POST …/files/{asset_id}/restore/
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/files/<uuid:asset_id>/restore/",
        FileRestoreView.as_view(),
        name="file-asset-restore",
    ),
    # DELETE …/files/{asset_id}/purge/
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/files/<uuid:asset_id>/purge/",
        FilePurgeView.as_view(),
        name="file-asset-purge",
    ),
    # POST …/files/{asset_id}/complete/
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/files/<uuid:asset_id>/complete/",
        FileCompleteView.as_view(),
        name="file-asset-complete",
    ),
]
