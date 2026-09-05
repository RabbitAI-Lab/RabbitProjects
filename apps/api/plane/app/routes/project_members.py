"""project_members 域路由片段（PROJ-002）。"""
from django.urls import path

from plane.app.views.project_member import (
    ProjectArchiveView,
    ProjectFavoriteView,
    ProjectMemberDetailView,
    ProjectMemberListCreateView,
)

urlpatterns = [
    # 项目成员列表 + 批量添加
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/members/",
        ProjectMemberListCreateView.as_view(),
        name="project-members-list-create",
    ),
    # 项目成员调整 / 移除
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/members/<uuid:member_id>/",
        ProjectMemberDetailView.as_view(),
        name="project-members-detail",
    ),
    # 收藏 / 取消收藏
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/favorite/",
        ProjectFavoriteView.as_view(),
        name="project-favorite",
    ),
    # 归档 / 恢复
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/archive/",
        ProjectArchiveView.as_view(),
        name="project-archive",
    ),
]
