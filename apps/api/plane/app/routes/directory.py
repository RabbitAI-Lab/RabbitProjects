"""directory 域路由片段（AUTH-011 LDAP/SCIM 目录同步，P4 R2）。

管理端点挂 workspaces/{slug}/directory/（统一信封）；SCIM 协议端点挂根级
/scim/v2/（信封豁免，plane/urls.py 直接 include）。信号（manual 盖章）经
本模块导入保证注册。
"""

from django.urls import path

import plane.directory.signals  # noqa: F401 —— manual 盖章钩子（urls 装载链注册）
from plane.app.views.directory import (
    DirectoryOverviewView,
    DirectoryRunConfirmView,
    DirectoryRunDetailView,
    DirectoryRunsView,
    DirectorySyncView,
    LdapConfigView,
    LdapTestView,
    MappingProtectView,
    PendingActionResolveView,
    PendingActionsView,
    ScimSetupView,
    ScimTokenRevokeView,
)

urlpatterns = [
    path("workspaces/<slug:slug>/directory/", DirectoryOverviewView.as_view(), name="dir-overview"),
    path("workspaces/<slug:slug>/directory/ldap/", LdapConfigView.as_view(), name="dir-ldap-config"),
    path("workspaces/<slug:slug>/directory/ldap/test/", LdapTestView.as_view(), name="dir-ldap-test"),
    path("workspaces/<slug:slug>/directory/sync/", DirectorySyncView.as_view(), name="dir-sync"),
    path("workspaces/<slug:slug>/directory/runs/", DirectoryRunsView.as_view(), name="dir-runs"),
    path(
        "workspaces/<slug:slug>/directory/runs/<uuid:run_id>/", DirectoryRunDetailView.as_view(), name="dir-run-detail"
    ),
    path(
        "workspaces/<slug:slug>/directory/runs/<uuid:run_id>/confirm/",
        DirectoryRunConfirmView.as_view(),
        name="dir-run-confirm",
    ),
    path("workspaces/<slug:slug>/directory/pending-actions/", PendingActionsView.as_view(), name="dir-pending"),
    path(
        "workspaces/<slug:slug>/directory/pending-actions/<uuid:action_id>/resolve/",
        PendingActionResolveView.as_view(),
        name="dir-pending-resolve",
    ),
    path(
        "workspaces/<slug:slug>/directory/mappings/<uuid:mapping_id>/",
        MappingProtectView.as_view(),
        name="dir-mapping-protect",
    ),
    path("workspaces/<slug:slug>/directory/scim/", ScimSetupView.as_view(), name="dir-scim-setup"),
    path("workspaces/<slug:slug>/directory/scim/token/", ScimTokenRevokeView.as_view(), name="dir-scim-revoke"),
]
