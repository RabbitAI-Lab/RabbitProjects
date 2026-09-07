"""integrations 域路由（INTG-001 §4.2——Sprint-5 T5-04）。"""
from django.urls import path

from plane.app.views.integrations_github import (
    GitHubAppInstallView,
    GitHubBindingDetailView,
    GitHubBindingListCreateView,
    GitHubCallbackView,
    GitHubQuotaStatusView,
    GitHubRepositoriesView,
    GitHubSyncLogsView,
    GitHubWebhookView,
)

urlpatterns = [
    # 速率预算只读状态（INTG-002 交接项 3：degraded 旗标暴露；顶层路径，
    # 权限经 installation → 绑定项目解析 PROJ_ADMIN+）
    path(
        "integrations/<int:installation_id>/quota-status/",
        GitHubQuotaStatusView.as_view(),
        name="github-quota-status",
    ),
    # 入站 Webhook（GitHub 调用，无登录态，HMAC 验签）
    path(
        "integrations/github/webhook/",
        GitHubWebhookView.as_view(),
        name="github-webhook-inbound",
    ),
    # 安装入口 / 回调（WS 级 integration.manage）
    path(
        "workspaces/<slug:slug>/integrations/github/app/",
        GitHubAppInstallView.as_view(),
        name="github-app-install",
    ),
    path(
        "workspaces/<slug:slug>/integrations/github/callback/",
        GitHubCallbackView.as_view(),
        name="github-app-callback",
    ),
    # 项目级绑定（integration.config）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/integrations/github/repositories/",
        GitHubRepositoriesView.as_view(),
        name="github-repositories",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/integrations/github/bindings/",
        GitHubBindingListCreateView.as_view(),
        name="github-bindings-list-create",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/integrations/github/bindings/<uuid:binding_id>/",
        GitHubBindingDetailView.as_view(),
        name="github-bindings-detail",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/integrations/github/sync-logs/",
        GitHubSyncLogsView.as_view(),
        name="github-sync-logs",
    ),
]
