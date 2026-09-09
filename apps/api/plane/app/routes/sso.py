"""sso 域路由片段（AUTH-009 — SSO 单点登录，Sprint-8 R3）。

15 端点：管理面 6（配置读改/干跑/强制开关/绑定清单）+ 认证流 7
（route/OIDC sign-in+callback/claim/SAML sign-in+acs+metadata）+ 本人绑定 2。
"""
from django.urls import path

from plane.app.views.sso import (
    MySSOBindingDeleteView,
    MySSOBindingsView,
    SAMLACSView,
    SAMLMetadataView,
    SAMLSignInView,
    SSOBindingsAdminView,
    SSOCallbackView,
    SSOClaimView,
    SSOConfigView,
    SSOConnectionCheckView,
    SSOEnforceView,
    SSORouteView,
    SSOSignInView,
)

urlpatterns = [
    # ── 管理面（WS_OWNER）──
    path("workspaces/<slug:slug>/sso/", SSOConfigView.as_view(), name="sso-config"),
    path("workspaces/<slug:slug>/sso/connection-check/",
         SSOConnectionCheckView.as_view(), name="sso-connection-check"),
    path("workspaces/<slug:slug>/sso/enforce/",
         SSOEnforceView.as_view(), name="sso-enforce"),
    path("workspaces/<slug:slug>/sso/bindings/",
         SSOBindingsAdminView.as_view(), name="sso-bindings-admin"),
    # ── 认证流（公开）──
    path("auth/sso/route/", SSORouteView.as_view(), name="sso-route"),
    path("auth/sso/<slug:slug>/sign-in/", SSOSignInView.as_view(), name="sso-sign-in"),
    path("auth/sso/callback/", SSOCallbackView.as_view(), name="sso-callback"),
    path("auth/sso/claim/", SSOClaimView.as_view(), name="sso-claim"),
    path("auth/sso/<slug:slug>/saml/sign-in/", SAMLSignInView.as_view(),
         name="sso-saml-sign-in"),
    path("auth/sso/saml/acs/", SAMLACSView.as_view(), name="sso-saml-acs"),
    path("auth/sso/<slug:slug>/metadata/", SAMLMetadataView.as_view(),
         name="sso-metadata"),
    # ── 本人绑定 ──
    path("users/me/sso/bindings/", MySSOBindingsView.as_view(),
         name="my-sso-bindings"),
    path("users/me/sso/bindings/<uuid:binding_id>/",
         MySSOBindingDeleteView.as_view(), name="my-sso-binding-delete"),
]
