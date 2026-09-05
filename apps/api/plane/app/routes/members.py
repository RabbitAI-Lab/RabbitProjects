"""members 域路由片段（TEAM-002 — 工作空间成员 / 邀请）。

10 个端点：成员列表 / 详情 / 退出 / 邀请 / 邀请列表 / 撤销 / 转让 / 预检 / 接受。
"""
from django.urls import path

from plane.app.views.workspace_members import (
    InvitationAcceptView,
    InvitationPrecheckView,
    WorkspaceInvitationDetailView,
    WorkspaceInvitationListCreateView,
    WorkspaceLeaveView,
    WorkspaceMemberDetailView,
    WorkspaceMemberListView,
    WorkspaceOwnershipTransferView,
)

urlpatterns = [
    # 成员列表（GET）
    path(
        "workspaces/<slug:slug>/members/",
        WorkspaceMemberListView.as_view(),
        name="workspace-members-list",
    ),
    # 成员详情（PATCH / DELETE）
    path(
        "workspaces/<slug:slug>/members/<uuid:member_id>/",
        WorkspaceMemberDetailView.as_view(),
        name="workspace-members-detail",
    ),
    # 退出团队（动作子资源）
    path(
        "workspaces/<slug:slug>/members/leave/",
        WorkspaceLeaveView.as_view(),
        name="workspace-members-leave",
    ),
    # 所有权转让（动作子资源）
    path(
        "workspaces/<slug:slug>/ownership/transfer/",
        WorkspaceOwnershipTransferView.as_view(),
        name="workspace-ownership-transfer",
    ),
    # 批量邀请 + 待接受邀请列表（POST / GET）
    path(
        "workspaces/<slug:slug>/invitations/",
        WorkspaceInvitationListCreateView.as_view(),
        name="workspace-invitations-list-create",
    ),
    # 撤销邀请（DELETE）
    path(
        "workspaces/<slug:slug>/invitations/<uuid:invite_id>/",
        WorkspaceInvitationDetailView.as_view(),
        name="workspace-invitations-detail",
    ),
    # 邀请预检（GET）
    path(
        "invitations/<str:token>/",
        InvitationPrecheckView.as_view(),
        name="invitation-precheck",
    ),
    # 邀请接受（POST）
    path(
        "invitations/<str:token>/accept/",
        InvitationAcceptView.as_view(),
        name="invitation-accept",
    ),
]
