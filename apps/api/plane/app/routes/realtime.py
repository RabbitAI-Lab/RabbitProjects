"""realtime 域路由片段（COLLAB-004 §4.2）。

三端点分两组挂载：
  - 用户态（Session + CSRF）：换票（项目域嵌套）与续签（/users/me/ 自资源）；
  - 服务态（X-Internal-Key，§9.7）：verify-rooms 内部复核——proxy 对
    /api/v1/internal/ 前缀不路由（第二道防线），仅 compose 内网可达。
"""
from django.urls import path

from plane.app.views.realtime import (
    InternalVerifyRoomsView,
    RealtimeTokenRenewView,
    RealtimeTokenView,
)

urlpatterns = [
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/realtime-token/",
        RealtimeTokenView.as_view(),
        name="realtime-token",
    ),
    path(
        "users/me/realtime-token/renew/",
        RealtimeTokenRenewView.as_view(),
        name="realtime-token-renew",
    ),
    path(
        "internal/realtime/verify-rooms/",
        InternalVerifyRoomsView.as_view(),
        name="internal-realtime-verify-rooms",
    ),
]
