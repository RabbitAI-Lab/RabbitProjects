"""workflow 域路由（WF-001 §4.8 / WF-002 §4.6——Sprint-7 R1）。

WF-001 工作流配置 7 端点 + 流转 2 端点；WF-002 审批流 CRUD 与审批中心。
"""
from django.urls import path

from plane.app.views.approvals import (
    ApprovalFlowDetailView,
    ApprovalFlowListCreateView,
    ApprovalInstanceActionView,
    ApprovalInstanceDetailView,
    ApprovalsActedView,
    ApprovalsMineView,
    ApprovalsPendingView,
    IssueApprovalsView,
)
from plane.app.views.workflows import (
    IssueTransitionExecuteView,
    IssueTransitionsAvailableView,
    WorkflowArchiveView,
    WorkflowDetailView,
    WorkflowGraphView,
    WorkflowListCreateView,
    WorkflowPublishView,
)

urlpatterns = [
    # ── WF-001 §4.8：工作流配置面 ──
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/workflows/",
         WorkflowListCreateView.as_view(), name="workflows-list-create"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/workflows/<uuid:wf_id>/",
         WorkflowDetailView.as_view(), name="workflows-detail"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/workflows/<uuid:wf_id>/graph/",
         WorkflowGraphView.as_view(), name="workflows-graph"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/workflows/<uuid:wf_id>/publish/",
         WorkflowPublishView.as_view(), name="workflows-publish"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/workflows/<uuid:wf_id>/archive/",
         WorkflowArchiveView.as_view(), name="workflows-archive"),
    # ── WF-001 §4.8：任务流转面 ──
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/transitions/",
         IssueTransitionExecuteView.as_view(), name="issue-transitions-execute"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/transitions/available/",
         IssueTransitionsAvailableView.as_view(), name="issue-transitions-available"),
    # ── WF-002 §4.6：审批流定义 ──
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/approval-flows/",
         ApprovalFlowListCreateView.as_view(), name="approval-flows-list-create"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/approval-flows/<uuid:fid>/",
         ApprovalFlowDetailView.as_view(), name="approval-flows-detail"),
    # ── WF-002 §4.6：审批中心三 Tab（WS 级）──
    path("workspaces/<slug:slug>/approvals/pending/",
         ApprovalsPendingView.as_view(), name="approvals-pending"),
    path("workspaces/<slug:slug>/approvals/acted/",
         ApprovalsActedView.as_view(), name="approvals-acted"),
    path("workspaces/<slug:slug>/approvals/mine/",
         ApprovalsMineView.as_view(), name="approvals-mine"),
    # ── WF-002 §4.6：实例详情 / 动作 / 任务实例列表 ──
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/approval-instances/<uuid:aid>/",
         ApprovalInstanceDetailView.as_view(), name="approval-instance-detail"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/approval-instances/<uuid:aid>/actions/",
         ApprovalInstanceActionView.as_view(), name="approval-instance-actions"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/approvals/",
         IssueApprovalsView.as_view(), name="issue-approvals"),
]
