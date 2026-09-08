"""工时审批域路由（TASK-013 §4.6）——8 端点（提交/审批/驳回/撤销/队列/台账/配置）。"""
from django.urls import path

from plane.app.views.worklog_approval import (
    WorkLogApprovalListView,
    WorkLogApproveView,
    WorkLogConfigView,
    WorkLogLedgerView,
    WorkLogRejectView,
    WorkLogRevokeView,
    WorkLogSubmitView,
)

urlpatterns = [
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/worklog-approvals/submit/",
         WorkLogSubmitView.as_view(), name="worklog-submit"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/worklog-approvals/<uuid:aid>/approve/",
         WorkLogApproveView.as_view(), name="worklog-approve"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/worklog-approvals/<uuid:aid>/reject/",
         WorkLogRejectView.as_view(), name="worklog-reject"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/worklog-approvals/<uuid:aid>/revoke/",
         WorkLogRevokeView.as_view(), name="worklog-revoke"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/worklog-approvals/",
         WorkLogApprovalListView.as_view(), name="worklog-approvals-list"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/worklog-ledger/",
         WorkLogLedgerView.as_view(), name="worklog-ledger"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/worklog-config/",
         WorkLogConfigView.as_view(), name="worklog-config"),
]
