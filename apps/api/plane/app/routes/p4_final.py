"""p4_final 域路由片段（BOARD-006/007 + WF-007 + PROJ-005 + COLLAB-005，R16~R18）。"""

from django.urls import path

from plane.app.views.p4_final import (
    GlobalBoardView,
    PushPreferencesView,
    ResourceSchedulingView,
    TimeoutRuleView,
    ViewTemplateApplyView,
    ViewTemplateView,
)

urlpatterns = [
    path("workspaces/<slug:slug>/global-board/", GlobalBoardView.as_view(), name="global-board"),
    path("workspaces/<slug:slug>/view-templates/", ViewTemplateView.as_view(), name="view-templates"),
    path(
        "workspaces/<slug:slug>/view-templates/<str:template_id>/apply/",
        ViewTemplateApplyView.as_view(),
        name="view-template-apply",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/workflow/timeout-rules/",
        TimeoutRuleView.as_view(),
        name="wf-timeout-rules",
    ),
    path("workspaces/<slug:slug>/resource-scheduling/", ResourceSchedulingView.as_view(), name="resource-scheduling"),
    path("users/me/push-preferences/", PushPreferencesView.as_view(), name="push-preferences"),
]
