"""URL 根路由 —— 三套分组前缀由 api-conventions.md §3 定义，业务端点随功能文档接入。"""

from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView

urlpatterns = [
    path("admin/", admin.site.urls),
    # /api/v1/ 内部 API · /api/v1/public/ 公开 · /god-mode/api/ 实例管理（INFRA-003 起挂载）
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/v1/", include(("plane.app.urls", "app"), namespace="app")),
]
