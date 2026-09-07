"""webhooks 域路由（INTG-002 §4.2——Sprint-5 T5-05）。"""
from django.urls import path

from plane.app.views.webhooks import (
    WebhookDeliveriesView,
    WebhookDeliveryDetailView,
    WebhookDetailView,
    WebhookDisableView,
    WebhookEnableView,
    WebhookListCreateView,
    WebhookPingView,
)

urlpatterns = [
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/webhooks/",
         WebhookListCreateView.as_view(), name="webhooks-list-create"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/webhooks/<uuid:endpoint_id>/",
         WebhookDetailView.as_view(), name="webhooks-detail"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/webhooks/<uuid:endpoint_id>/disable/",
         WebhookDisableView.as_view(), name="webhooks-disable"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/webhooks/<uuid:endpoint_id>/enable/",
         WebhookEnableView.as_view(), name="webhooks-enable"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/webhooks/<uuid:endpoint_id>/ping/",
         WebhookPingView.as_view(), name="webhooks-ping"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/webhooks/<uuid:endpoint_id>/deliveries/",
         WebhookDeliveriesView.as_view(), name="webhooks-deliveries"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/webhooks/<uuid:endpoint_id>/deliveries/<uuid:delivery_id>/",
         WebhookDeliveryDetailView.as_view(), name="webhooks-delivery-detail"),
]
