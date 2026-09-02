from django.contrib.auth import authenticate, login, logout
from django.db import transaction
from django.utils.text import slugify
from rest_framework.response import Response
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated
from plane.app.serializers.auth import (
    MeSerializer,
    SignInSerializer,
    SignUpSerializer,
)
from plane.app.serializers.common import envelope
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
            return envelope(False, None, {"code": "AUTH_EMAIL_EXISTS", "message": "该邮箱已注册"}, http_status=409)

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
        # 注册即登录（Session 保持，SESSION_SAVE_EVERY_REQUEST 由 settings 控制）
        login(request, user)
        return envelope(True, _envelope_user_with_workspaces(user), None, http_status=201)


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
            return envelope(
                False, None, {"code": "AUTH_INVALID_CREDENTIALS", "message": "邮箱或密码错误"}, http_status=401
            )
        if not user.is_active:
            return envelope(
                False, None, {"code": "AUTH_ACCOUNT_DISABLED", "message": "账号已被禁用，请联系管理员"}, http_status=401
            )
        login(request, user)
        # 记住我：滑动 30 天；否则默认 14 天（Django Session 默认）
        request.session.set_expiry(60 * 60 * 24 * 30 if remember else 0)
        return envelope(True, _envelope_user_with_workspaces(user))


class SignOutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)
        return Response(status=204)  # 204 禁带 body


class MeView(APIView):
    """GET /api/v1/users/me/ —— 路由守卫判定源：200 已登录 / 401 未登录。"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return envelope(True, _envelope_user_with_workspaces(request.user))


@api_view(["GET"])
@permission_classes([AllowAny])
def csrf_token(request):
    from django.middleware.csrf import get_token

    return envelope(True, {"csrf_token": get_token(request)})
