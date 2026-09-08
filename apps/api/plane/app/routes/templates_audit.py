"""模板与审计域路由（WF-005 §4.4 / WF-006 §4.5）。"""
from django.urls import path

from plane.app.views.templates_audit import (
    ApprovalAuditExportView,
    ApprovalAuditVerifyView,
    ApprovalAuditView,
    TemplateDistributeView,
    TemplateUnlockRequestView,
    WorkflowTemplateDetailView,
    WorkflowTemplateListCreateView,
)

urlpatterns = [
    # ── WF-005：WS 级模板库 ──
    path("workspaces/<slug:slug>/workflow-templates/",
         WorkflowTemplateListCreateView.as_view(), name="workflow-templates-list-create"),
    path("workspaces/<slug:slug>/workflow-templates/<uuid:tid>/",
         WorkflowTemplateDetailView.as_view(), name="workflow-templates-detail"),
    # ── WF-005：项目域下发与申请 ──
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/workflow-templates/",
         TemplateDistributeView.as_view(), name="template-distribute"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/workflow-templates/unlock-requests/",
         TemplateUnlockRequestView.as_view(), name="template-unlock-requests"),
    # ── WF-006：审计检索 / 导出 / 链校验 ──
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/approval-audit/",
         ApprovalAuditView.as_view(), name="approval-audit"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/approval-audit/export/",
         ApprovalAuditExportView.as_view(), name="approval-audit-export"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/approval-audit/verify/",
         ApprovalAuditVerifyView.as_view(), name="approval-audit-verify"),
]
