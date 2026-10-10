"""file_compliance 域路由片段（FILE-006，P4 R6）。"""

from django.urls import path

from plane.app.views.file_compliance import (
    ComplianceEffectiveView,
    CompliancePolicyView,
    DlpRuleView,
    LegalHoldView,
)

urlpatterns = [
    path("workspaces/<slug:slug>/file-compliance/policy/", CompliancePolicyView.as_view(), name="fcomp-policy"),
    path(
        "workspaces/<slug:slug>/file-compliance/effective/", ComplianceEffectiveView.as_view(), name="fcomp-effective"
    ),
    path("workspaces/<slug:slug>/file-compliance/legal-holds/", LegalHoldView.as_view(), name="fcomp-holds"),
    path(
        "workspaces/<slug:slug>/file-compliance/legal-holds/<uuid:hold_id>/",
        LegalHoldView.as_view(),
        name="fcomp-hold-detail",
    ),
    path("workspaces/<slug:slug>/file-compliance/dlp-rules/", DlpRuleView.as_view(), name="fcomp-dlp"),
    path(
        "workspaces/<slug:slug>/file-compliance/dlp-rules/<uuid:rule_id>/",
        DlpRuleView.as_view(),
        name="fcomp-dlp-detail",
    ),
]
