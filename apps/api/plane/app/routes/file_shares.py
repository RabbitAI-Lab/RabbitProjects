"""file_shares 域路由片段（FILE-004 §4.2 内部 API 表 #1~#4）。

创建/列表挂 files 子资源（…/files/{asset_id}/share-links/）；延期/吊销按
动作子资源直挂项目层（…/share-links/{link_id}/…）——link 自身携带 asset
归属，管理弹层操作不必回带 asset_id 路径段（§2.6 动作子资源先例）。
"""
from django.urls import path

from plane.app.views.file_shares import (
    FileShareExtendView,
    FileShareListCreateView,
    FileShareRevokeView,
)

urlpatterns = [
    # POST（创建）/ GET（列表）…/projects/{project_id}/files/{asset_id}/share-links/
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/files/<uuid:asset_id>/share-links/",
        FileShareListCreateView.as_view(),
        name="file-share-links",
    ),
    # DELETE（吊销）…/projects/{project_id}/share-links/{link_id}/
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/share-links/<uuid:link_id>/",
        FileShareRevokeView.as_view(),
        name="file-share-detail",
    ),
    # POST（延期）…/projects/{project_id}/share-links/{link_id}/extend/
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/share-links/<uuid:link_id>/extend/",
        FileShareExtendView.as_view(),
        name="file-share-extend",
    ),
]
