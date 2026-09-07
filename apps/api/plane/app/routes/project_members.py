"""project_members 域路由片段（PROJ-002）。"""
from django.urls import path

from plane.app.views.project_member import (
    ProjectArchiveView,
    ProjectFavoriteView,
    ProjectMemberBulkRoleView,
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
    # 批量改角色（AUTH-006 §4.4.3；部分成功语义）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/members/bulk-role/",
        ProjectMemberBulkRoleView.as_view(),
        name="project-members-bulk-role",
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
