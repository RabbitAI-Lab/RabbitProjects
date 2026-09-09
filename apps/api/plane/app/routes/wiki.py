"""wiki 域路由片段（FILE-005 — 知识库，Sprint-9）。

13 端点：空间 CRUD / 页面 CRUD（树）/ 草稿 / 发布 / 移动 / 版本台账与详情 /
回滚 / 回收站 / 恢复 / 知识检索。
"""
from django.urls import path

from plane.app.views.wiki import (
    WikiPageDetailView,
    WikiPageDraftView,
    WikiPageListCreateView,
    WikiPageMoveView,
    WikiPagePublishView,
    WikiPageRollbackView,
    WikiPageVersionDetailView,
    WikiPageVersionsView,
    WikiRestoreView,
    WikiSearchView,
    WikiSpaceDetailView,
    WikiSpaceListCreateView,
    WikiTrashView,
)

urlpatterns = [
    path("workspaces/<slug:slug>/wiki/spaces/", WikiSpaceListCreateView.as_view(),
         name="wiki-spaces-list-create"),
    path("workspaces/<slug:slug>/wiki/spaces/<uuid:space_id>/", WikiSpaceDetailView.as_view(),
         name="wiki-spaces-detail"),
    path("workspaces/<slug:slug>/wiki/pages/", WikiPageListCreateView.as_view(),
         name="wiki-pages-list-create"),
    path("workspaces/<slug:slug>/wiki/pages/trash/", WikiTrashView.as_view(),
         name="wiki-pages-trash"),
    path("workspaces/<slug:slug>/wiki/pages/<uuid:page_id>/", WikiPageDetailView.as_view(),
         name="wiki-pages-detail"),
    path("workspaces/<slug:slug>/wiki/pages/<uuid:page_id>/draft/", WikiPageDraftView.as_view(),
         name="wiki-pages-draft"),
    path("workspaces/<slug:slug>/wiki/pages/<uuid:page_id>/publish/", WikiPagePublishView.as_view(),
         name="wiki-pages-publish"),
    path("workspaces/<slug:slug>/wiki/pages/<uuid:page_id>/move/", WikiPageMoveView.as_view(),
         name="wiki-pages-move"),
    path("workspaces/<slug:slug>/wiki/pages/<uuid:page_id>/versions/", WikiPageVersionsView.as_view(),
         name="wiki-pages-versions"),
    path("workspaces/<slug:slug>/wiki/pages/<uuid:page_id>/versions/<uuid:version_id>/",
         WikiPageVersionDetailView.as_view(), name="wiki-pages-version-detail"),
    path("workspaces/<slug:slug>/wiki/pages/<uuid:page_id>/rollback/", WikiPageRollbackView.as_view(),
         name="wiki-pages-rollback"),
    path("workspaces/<slug:slug>/wiki/pages/<uuid:page_id>/restore/", WikiRestoreView.as_view(),
         name="wiki-pages-restore"),
    path("workspaces/<slug:slug>/wiki/search/", WikiSearchView.as_view(),
         name="wiki-search"),
]
