"""users 域路由片段（AUTH-004 §4.2）。

端点全部挂 Session 认证（除 ``/public/users/<id>/avatar/``）。

聚合：``plane.app.urls`` 按 FEATURE_MODULES 列表自动 load。
本模块兼挂 forgot/reset 密码端点（路径在 ``/auth/*`` 前缀，与 users 域同属
account 域管理，不另起 auth 路由文件以保持聚合器文件数最少——CLAUDE.md monorepo
约定）。

路由冲突避免：``users/me/`` 已在 ``urls.py`` 由 MeView 注册（取自 sprint-0 的
「GET 当前用户」），本模块不重复注册。PATCH /users/me/ 由 MeView 接管（auth.py
的 MeView 已扩展支持 PATCH）。
"""
from django.urls import path

from plane.app.views.auth import ForgotPasswordView, ResetPasswordView
from plane.app.views.users import (
    AvatarCompleteView,
    AvatarDeleteView,
    AvatarPresignView,
    ChangePasswordView,
    public_avatar_svg,
)

urlpatterns = [
    # POST /api/v1/users/me/avatar/presign/
    path(
        "users/me/avatar/presign/",
        AvatarPresignView.as_view(),
        name="users-avatar-presign",
    ),
    # POST /api/v1/users/me/avatar/complete/
    path(
        "users/me/avatar/complete/",
        AvatarCompleteView.as_view(),
        name="users-avatar-complete",
    ),
    # DELETE /api/v1/users/me/avatar/
    path(
        "users/me/avatar/",
        AvatarDeleteView.as_view(),
        name="users-avatar-delete",
    ),
    # POST /api/v1/users/me/change-password/
    path(
        "users/me/change-password/",
        ChangePasswordView.as_view(),
        name="users-change-password",
    ),
    # GET /api/v1/public/users/{user_id}/avatar/  (匿名)
    path(
        "public/users/<uuid:user_id>/avatar/",
        public_avatar_svg,
        name="public-user-avatar",
    ),
    # ── AUTH-004 §4.2.7 / §4.2.8（forgot / reset 密码）─────
    path(
        "auth/forgot-password/",
        ForgotPasswordView.as_view(),
        name="auth-forgot-password",
    ),
    path(
        "auth/reset-password/",
        ResetPasswordView.as_view(),
        name="auth-reset-password",
    ),
]
