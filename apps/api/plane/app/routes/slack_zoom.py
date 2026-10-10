"""slack_zoom 域路由片段（INTG-003，P4 R5）。"""

from django.urls import path

from plane.app.views.slack_zoom import (
    SlackActionView,
    SlackSubscriptionView,
    SlackUserMapView,
    ZoomMeetingView,
    ZoomMinutesInboundView,
)

urlpatterns = [
    path(
        "workspaces/<slug:slug>/integrations/slack/subscriptions/", SlackSubscriptionView.as_view(), name="slack-subs"
    ),
    path(
        "workspaces/<slug:slug>/integrations/slack/subscriptions/<uuid:sub_id>/",
        SlackSubscriptionView.as_view(),
        name="slack-sub-detail",
    ),
    path("workspaces/<slug:slug>/integrations/slack/user-map/", SlackUserMapView.as_view(), name="slack-user-map"),
    path("integrations/slack/actions/", SlackActionView.as_view(), name="slack-actions"),
    path("workspaces/<slug:slug>/integrations/zoom/meetings/", ZoomMeetingView.as_view(), name="zoom-meetings"),
    path("integrations/zoom/minutes/", ZoomMinutesInboundView.as_view(), name="zoom-minutes"),
]
