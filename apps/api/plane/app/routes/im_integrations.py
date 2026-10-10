"""im_integrations 域路由片段（INTG-005，P4 R5b）。"""

from django.urls import path

from plane.app.views.im_integrations import (
    ImActionView,
    ImChannelView,
    ImSubscriptionView,
    ImTestView,
)

urlpatterns = [
    path("workspaces/<slug:slug>/integrations/im/channels/", ImChannelView.as_view(), name="im-channels"),
    path(
        "workspaces/<slug:slug>/integrations/im/channels/<uuid:channel_id>/",
        ImChannelView.as_view(),
        name="im-channel-detail",
    ),
    path("workspaces/<slug:slug>/integrations/im/subscriptions/", ImSubscriptionView.as_view(), name="im-subs"),
    path(
        "workspaces/<slug:slug>/integrations/im/subscriptions/<uuid:sub_id>/",
        ImSubscriptionView.as_view(),
        name="im-sub-detail",
    ),
    path("integrations/im/actions/", ImActionView.as_view(), name="im-actions"),
    path("workspaces/<slug:slug>/integrations/im/test/", ImTestView.as_view(), name="im-test"),
]
