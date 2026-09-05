import logging

from django.contrib.auth import authenticate, login, logout
from django.db import transaction
from django.utils.text import slugify
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.account.services import PasswordService, ProfileService
from plane.account.sessions import track_session
from plane.app.permissions import IsAuthenticated
from plane.app.serializers.auth import (
    MeSerializer,
    SignInSerializer,
    SignUpSerializer,
)
from plane.app.serializers.user import (
    ForgotPasswordSerializer,
    ProfileUpdateSerializer,
    ResetPasswordSerializer,
)
from plane.base.exception import AppException
from plane.base.response import created_response, success_response
from plane.db.models import User, Workspace, WorkspaceMember
from plane.db.models.roles import WorkspaceRole
from plane.db.seeds.issue_types import seed_issue_types


def _envelope_user_with_workspaces(user):
    """注册/登录响应：当前用户 + 他所属的工作空间列表 + 默认团队 slug"""
    memberships = (
        WorkspaceMember.objects.filter(member=user, is_active=True).select_related("workspace").order_by("created_at")
    )
    workspaces = []
    default_slug = None
    for m in memberships:
        workspaces.append(
            {
                "id": str(m.workspace_id),
                "name": m.workspace.name,
                "slug": m.workspace.slug,
                "logo": m.workspace.logo,
                "role": m.role,
                "current_user_role": m.role,
                "total_projects": 0,
            }
        )
        if default_slug is None:
            default_slug = m.workspace.slug
    return {
        "user": MeSerializer(user).data,
        "workspaces": workspaces,
        "default_workspace_slug": default_slug,
    }


class SignUpView(APIView):
    """POST /api/v1/auth/sign-up/ —— 注册 + 自动初始化个人默认团队（事务原子）。"""

    permission_classes = [AllowAny]

    @transaction.atomic
    def post(self, request):
        s = SignUpSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        email = s.validated_data["email"].strip().lower()
        password = s.validated_data["password"]
        display_name = s.validated_data.get("display_name") or email.split("@")[0]

        if User.objects.filter(email=email).exists():
            # sprint-0 用 ad-hoc 码 AUTH_EMAIL_EXISTS；INFRA-004 收口后统一映射到
            # RESOURCE_ALREADY_EXISTS + details[].UNIQUE（与 §4.2 / §8.5 一致）。
            raise AppException(
                "RESOURCE_ALREADY_EXISTS",
                message="该邮箱已注册",
                details=[{"field": "email", "code": "UNIQUE", "message": "该邮箱已注册"}],
            )

        user = User.objects.create_user(email=email, password=password, display_name=display_name)

        # 默认 Workspace：slug 冲突自动后缀（TEAM-001 §2.2 行为）
        base = slugify(display_name) or "workspace"
        slug, n = base, 2
        while Workspace.objects.filter(slug=slug, deleted_at__isnull=True).exists():
            slug = f"{base}-{n}"
            n += 1
        ws = Workspace.objects.create(name=f"{display_name} 的工作空间", slug=slug, owner=user, created_by=user)
        WorkspaceMember.objects.create(workspace=ws, member=user, role=WorkspaceRole.OWNER, created_by=user)
        seed_issue_types(ws, actor_id=user.id)
        # TEAM-002 §4.3.2 / §2.2：注册钩子自动接受面向该邮箱的 pending 邀请。
        # 调用顺序硬性约定：在 create_default_workspace 之后、提交前。多空间邀请逐一接受。
        from plane.db.services.workspace_member import MemberService

        accepted = MemberService.accept_pending_invites(user)
        # 注册即登录（Session 保持，SESSION_SAVE_EVERY_REQUEST 由 settings 控制）
        login(request, user)
        if accepted:
            logging.getLogger("plane.api.auth").info(
                "signup.accepted_invites user=%s count=%s", user.id, len(accepted),
            )
        # AUTH-004 §4.3.5：登录后维护用户会话索引（吊销工具依赖）
        if request.session.session_key:
            transaction.on_commit(
                lambda: track_session(user.id, request.session.session_key)
            )
        return created_response(
            _envelope_user_with_workspaces(user),
            location=request.build_absolute_uri("/api/v1/users/me/"),
        )


class SignInView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        s = SignInSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        email = s.validated_data["email"].strip().lower()
        password = s.validated_data["password"]
        remember = s.validated_data.get("remember", False)
        user = authenticate(request, email=email, password=password)
        if user is None:
            raise AppException("AUTH_INVALID_CREDENTIALS", message="邮箱或密码错误")
        if not user.is_active:
            raise AppException(
                "AUTH_ACCOUNT_DISABLED",
                message="账号已被禁用，请联系管理员",
            )
        login(request, user)
        # 记住我：滑动 30 天；否则默认 14 天（Django Session 默认）
        request.session.set_expiry(60 * 60 * 24 * 30 if remember else 0)
        # AUTH-004 §4.3.5：登录即建立索引（reset/change 才能吊销其他设备）
        if request.session.session_key:
            track_session(user.id, request.session.session_key)
        return success_response(_envelope_user_with_workspaces(user))


class SignOutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)  # 204 禁带 body（C1 例外）


class MeView(APIView):
    """GET / PATCH /api/v1/users/me/ —— 路由守卫判定源 + 资料修改（AUTH-004 §4.2.1）。

    GET 保留 sprint-0 的 ``_envelope_user_with_workspaces``（apps/web AuthStore 直接消费）；
    PATCH 走 ``ProfileService.update_profile``（白名单 + 安全字段显式拒绝，§4.3.1）。
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return success_response(_envelope_user_with_workspaces(request.user))

    def patch(self, request):
        # BR-02 / §1.2：传 ``request.data`` 而非 ``validated_data``，让
        # ``ProfileService`` 同时做「白名单 + 安全字段显式拒绝」——
        # 若传 validated_data，安全字段已被 serializer 静默丢弃，
        # ``set(payload) & FORBIDDEN_ON_PROFILE`` 永远为空，BR-02 失效。
        if not isinstance(request.data, dict):
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"__all__": "请求体必须是对象"})
        s = ProfileUpdateSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        user = ProfileService.update_profile(
            user=request.user, payload=request.data,
        )
        return success_response(ProfileService.serialize(user))


# ──────────────────────────────────────────────────────────────────────
# AUTH-004 §4.2.7 / §4.2.8 — 忘记密码 + 重置密码
# ──────────────────────────────────────────────────────────────────────
class ForgotPasswordView(APIView):
    """POST /api/v1/auth/forgot-password/ —— 防枚举 202（§2.3 / BR-08）。

    邮件投递由 PasswordService.forgot_password 内部 on_commit → send_reset_email.delay。
    broker 不可达或 SMTP 未配置均降级为日志（不重抛、不阻塞 202，§4.4 / IT-01 / EC-12）。
    """

    permission_classes = [AllowAny]

    def post(self, request):
        s = ForgotPasswordSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        ip = _client_ip(request)
        try:
            PasswordService.forgot_password(
                email=s.validated_data["email"], ip=ip,
            )
        except Exception as exc:
            # 防枚举：兜底吞掉所有非 AppException（不应发生，仅双保险）
            logging.getLogger("plane.api.auth").warning(
                "forgot_password.unexpected %s", exc,
            )
        return success_response(
            data=None,
            meta={"message": "若该邮箱存在，重置邮件已发送，请查收（含垃圾箱）"},
            status_code=status.HTTP_202_ACCEPTED,
        )


class ResetPasswordView(APIView):
    """POST /api/v1/auth/reset-password/ —— 令牌消费 + 全部会话吊销（§4.2.8）。"""

    permission_classes = [AllowAny]

    def post(self, request):
        s = ResetPasswordSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        data = PasswordService.reset_password(
            token=s.validated_data["token"],
            new_password=s.validated_data["new_password"],
            new_password_confirm=s.validated_data["new_password_confirm"],
        )
        return success_response(data)


@api_view(["GET"])
@permission_classes([AllowAny])
def csrf_token(request):
    from django.middleware.csrf import get_token

    return success_response({"csrf_token": get_token(request)})


def _client_ip(request) -> str | None:
    """取 client IP（X-Forwarded-For 优先于 REMOTE_ADDR）"""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None
