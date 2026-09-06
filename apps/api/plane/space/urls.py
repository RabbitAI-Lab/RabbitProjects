"""space 公开路由（/api/v1/public/ 前缀，api-conventions.md §2.1）。"""
from django.urls import path

from plane.space.views.share import (
    ShareContentView,
    ShareMetaView,
    ShareUnlockView,
)

urlpatterns = [
    # GET 链接元信息（匿名；密码门语义——未解锁仅 requires_password）
    path("shares/<str:slug>/", ShareMetaView.as_view(), name="share-meta"),
    # POST 密码校验 → 2h HMAC cookie（公开分组唯一匿名 POST，§4.2 豁免注）
    path("shares/<str:slug>/unlock/", ShareUnlockView.as_view(), name="share-unlock"),
    # GET 预览调度（200/202）/ 下载（?download=1 → 302 五分钟预签名）
    path("shares/<str:slug>/content/", ShareContentView.as_view(), name="share-content"),
]
