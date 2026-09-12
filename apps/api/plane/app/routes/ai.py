"""ai 域路由片段（AI-001，P4 R8）。"""

from django.urls import path

from plane.ai.views import (
    AiConsentAdminView,
    AiFeedbackView,
    AiGenerateView,
    AiRiskScoreView,
    AiSimilarView,
    AiSummaryView,
)

urlpatterns = [
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/ai-summary/",
        AiSummaryView.as_view(),
        name="ai-summary",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issues/ai-generate/",
        AiGenerateView.as_view(),
        name="ai-generate",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issues/similar/", AiSimilarView.as_view(), name="ai-similar"
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/risk-score/",
        AiRiskScoreView.as_view(),
        name="ai-risk",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/ai-feedback/", AiFeedbackView.as_view(), name="ai-feedback"
    ),
    path("workspaces/<slug:slug>/ai/consent/", AiConsentAdminView.as_view(), name="ai-consent"),
]
