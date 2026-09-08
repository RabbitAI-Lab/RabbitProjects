"""自动化域路由（WF-003 §4.5）——5 端点（CRUD + dry-run + settings）。"""
from django.urls import path

from plane.app.views.automation import (
    AutomationRuleDetailView,
    AutomationRuleDryRunView,
    AutomationRuleListCreateView,
    AutomationSettingView,
)

urlpatterns = [
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/automation-rules/",
         AutomationRuleListCreateView.as_view(), name="automation-rules-list-create"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/automation-rules/<uuid:rule_id>/",
         AutomationRuleDetailView.as_view(), name="automation-rules-detail"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/automation-rules/<uuid:rule_id>/dry-run/",
         AutomationRuleDryRunView.as_view(), name="automation-rules-dry-run"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/automation-settings/",
         AutomationSettingView.as_view(), name="automation-settings"),
]
