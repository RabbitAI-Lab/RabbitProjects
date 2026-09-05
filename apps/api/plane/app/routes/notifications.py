"""notifications 域路由片段（COLLAB-001 §4.3）。

挂在 /users/me/notifications/ 下（用户自资源，跨项目聚合）。
"""
from django.urls import path

from plane.app.views.notifications import (
    NotificationListView,
    NotificationMarkReadView,
    NotificationReadAllView,
    NotificationUnreadCountView,
)

urlpatterns = [
    # 列表（?unread=true|false；与 RPT-001 通知摘要卡参数对齐）
    path(
        "users/me/notifications/",
        NotificationListView.as_view(),
        name="notifications-list",
    ),
    # 未读计数（30s 轮询）
    path(
        "users/me/notifications/unread-count/",
        NotificationUnreadCountView.as_view(),
        name="notifications-unread-count",
    ),
    # 全部已读（本人域 BR-12）
    path(
        "users/me/notifications/read-all/",
        NotificationReadAllView.as_view(),
        name="notifications-read-all",
    ),
    # 单条已读
    path(
        "users/me/notifications/<uuid:notification_id>/read/",
        NotificationMarkReadView.as_view(),
        name="notifications-mark-read",
    ),
]
