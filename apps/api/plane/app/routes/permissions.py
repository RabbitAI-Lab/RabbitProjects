"""permissions 域路由片段（AUTH-005 §4.2 权限快照下发）。"""
from django.urls import path

from plane.app.views.permissions_api import UserPermissionsView

urlpatterns: list = [
    path("users/me/permissions/", UserPermissionsView.as_view(), name="users-me-permissions"),
]
