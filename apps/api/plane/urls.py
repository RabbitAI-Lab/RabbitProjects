"""URL 根路由 —— 三套分组前缀由 api-conventions.md §3 定义，业务端点随功能文档接入。"""

from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView

from plane.app.views.scim import (  # noqa: E402
    ScimGroupsView,
    ScimUserDetailView,
    ScimUsersView,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    # /api/v1/ 内部 API · /api/v1/public/ 公开 · /god-mode/api/ 实例管理（INFRA-003 起挂载）
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/v1/", include(("plane.app.urls", "app"), namespace="app")),
    # space 公开分组（FILE-004 首个落地：文件分享匿名面）
    path("api/v1/public/", include(("plane.space.urls", "space"), namespace="space")),
    # Open API 管理面 + external/ 白名单（INTG-004，P4 R4）
    path("api/v1/", include("plane.api.urls")),
    # SCIM 2.0 协议端点（AUTH-011 §2.5：Bearer Token + 信封豁免，P4 R2）
    path("scim/v2/Users/", ScimUsersView.as_view(), name="scim-users"),
    path("scim/v2/Users/<uuid:scim_id>/", ScimUserDetailView.as_view(), name="scim-user-detail"),
    path("scim/v2/Groups/", ScimGroupsView.as_view(), name="scim-groups"),
]
