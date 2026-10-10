"""Open API 路由（INTG-004，P4 R4）。

/api/v1/external/ 白名单（物理隔离——未注册路径对凭证天然 404，BR-03）+
管理面（api-tokens/oauth）。由 plane/urls.py 直接 include（独立聚合，
不经 FEATURE_MODULES——本包自成命名空间）。
"""

from django.urls import path

from plane.api.oauth_views import (
    ApiTokenDetailView,
    ApiTokenListCreateView,
    ApiTokenLogsView,
    AuthorizedAppsView,
    OAuthApplicationView,
    OAuthAuthorizeView,
    OAuthIntrospectView,
    OAuthRevokeView,
    OAuthTokenView,
)
from plane.api.views import (
    ExternalIssueDetailView,
    ExternalIssuesListView,
    ExternalWorkspacesListView,
)

urlpatterns = [
    path("api-tokens/", ApiTokenListCreateView.as_view(), name="api-tokens"),
    path("api-tokens/<uuid:token_id>/", ApiTokenDetailView.as_view(), name="api-token-detail"),
    path("api-tokens/logs/", ApiTokenLogsView.as_view(), name="api-token-logs"),
    path("oauth/applications/", OAuthApplicationView.as_view(), name="oauth-apps"),
    path("oauth/applications/<uuid:app_id>/", OAuthApplicationView.as_view(), name="oauth-app-detail"),
    path("oauth/authorize/", OAuthAuthorizeView.as_view(), name="oauth-authorize"),
    path("oauth/token/", OAuthTokenView.as_view(), name="oauth-token"),
    path("oauth/revoke/", OAuthRevokeView.as_view(), name="oauth-revoke"),
    path("oauth/introspect/", OAuthIntrospectView.as_view(), name="oauth-introspect"),
    path("users/me/authorized-apps/", AuthorizedAppsView.as_view(), name="authorized-apps"),
    path("users/me/authorized-apps/<uuid:grant_id>/", AuthorizedAppsView.as_view(), name="authorized-app-detail"),
    path("external/issues/", ExternalIssuesListView.as_view(), name="ext-issues"),
    path("external/issues/<uuid:issue_id>/", ExternalIssueDetailView.as_view(), name="ext-issue-detail"),
    path("external/workspaces/", ExternalWorkspacesListView.as_view(), name="ext-workspaces"),
]
