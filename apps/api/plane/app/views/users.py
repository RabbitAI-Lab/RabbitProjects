"""Account 域视图（AUTH-004 §4.2 端点表）。

端点：
  PATCH  /api/v1/users/me/                                资料修改
  POST   /api/v1/users/me/avatar/presign/                头像直传凭证
  POST   /api/v1/users/me/avatar/complete/               头像完成确认
  DELETE /api/v1/users/me/avatar/                        恢复默认头像
  GET    /api/v1/public/users/{user_id}/avatar/          默认头像 SVG
  POST   /api/v1/users/me/change-password/              改密
"""
from __future__ import annotations

from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from plane.account.avatar import render_avatar_svg
from plane.account.services import (
    AvatarService,
    PasswordService,
    ProfileService,
)
from plane.app.permissions import IsAuthenticated
from plane.app.serializers.user import (
    AvatarCompleteSerializer,
    AvatarPresignSerializer,
    ChangePasswordSerializer,
    ProfileUpdateSerializer,
)
from plane.base.response import success_response


class ProfileView(APIView):
    """PATCH /api/v1/users/me/ —— 资料修改（§4.2.1）。

    GET 一并支持；GET 返回值与登录响应 ``data.user`` 同构（apps/web ProfileStore
    直接消费）。
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return success_response(ProfileService.serialize(request.user))

    def patch(self, request):
        s = ProfileUpdateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = ProfileService.update_profile(
            user=request.user, payload=s.validated_data,
        )
        return success_response(ProfileService.serialize(user))


class AvatarPresignView(APIView):
    """POST /api/v1/users/me/avatar/presign/ —— §4.2.2"""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        s = AvatarPresignSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        data = AvatarService.presign(
            user=request.user,
            file_name=s.validated_data["file_name"],
            file_size=s.validated_data["file_size"],
            content_type=s.validated_data["content_type"],
        )
        return success_response(
            data,
            status_code=status.HTTP_201_CREATED,
            headers={"Location": request.build_absolute_uri("/api/v1/users/me/avatar/")},
        )


class AvatarCompleteView(APIView):
    """POST /api/v1/users/me/avatar/complete/ —— §4.2.3"""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        s = AvatarCompleteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        data = AvatarService.complete(
            user=request.user,
            asset_id=s.validated_data["asset_id"],
        )
        return success_response(data)


class AvatarDeleteView(APIView):
    """DELETE /api/v1/users/me/avatar/ —— §4.2.4 恢复默认"""

    permission_classes = [IsAuthenticated]

    def delete(self, request):
        data = AvatarService.delete(user=request.user)
        return success_response(data)


class ChangePasswordView(APIView):
    """POST /api/v1/users/me/change-password/ —— §4.2.6"""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        s = ChangePasswordSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        # request.session.session_key 在未登录时为 None；permission_classes 已守住
        session_key = request.session.session_key or ""
        data = PasswordService.change_password(
            user=request.user,
            old_password=s.validated_data["old_password"],
            new_password=s.validated_data["new_password"],
            new_password_confirm=s.validated_data["new_password_confirm"],
            request_session_key=session_key,
        )
        return success_response(data)


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def public_avatar_svg(request, user_id):
    """GET /api/v1/public/users/{user_id}/avatar/ —— §4.2.5 默认头像服务端正点。

    ST-08：任意 user_id（含不存在）都必须返回合法 SVG（固定灰配色 + ``?``），不 404。
    """
    name = (request.GET.get("name") or "")[:1]
    svg = render_avatar_svg(user_id=str(user_id), name=name)
    return HttpResponse(
        svg,
        content_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


# ──────────────────────────────────────────────────────────────────────
# 兜底：patched_app_exception —— AvatarService.complete 可能传 None 之类的坑。
# DRF ValidationError 已在 handler 第 2 步收敛，AppException 走第 1 步。
# ──────────────────────────────────────────────────────────────────────
__all__ = [
    "ProfileView",
    "AvatarPresignView",
    "AvatarCompleteView",
    "AvatarDeleteView",
    "ChangePasswordView",
    "public_avatar_svg",
]
