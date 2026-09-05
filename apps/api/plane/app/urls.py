"""统一响应信封（api-conventions.md §4）。"""
from importlib import import_module

from django.db import connection
from django.urls import path
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from plane.app.views.auth import MeView, SignInView, SignOutView, SignUpView, csrf_token
from plane.app.views.issues import IssueDetailView, IssueListCreateView
from plane.app.views.projects import ProjectDetailView, ProjectListCreateView, ProjectStateListView
from plane.app.views.workspaces import WorkspaceDetailView, WorkspaceListCreateView
from plane.base.exception import AppException
from plane.base.response import success_response


class HealthView(APIView):
    """INFRA-002 §4.10 健康检查端点（db 连接探针）。

    注意：celery/redis 连接故意不在此检查——服务组件健康由 compose depends_on 链表达，
    health 端点仅作为容器级可用性的真实探针（避免假就绪）。

    响应同样走 C1 信封（INFRA-004 §4.6 强制：不存在第三种结构）。
    compose ``curl -f`` 只看 HTTP 状态码，所以 ``data.checks.db`` 改名为
    ``data.checks.db==ok`` 不影响探针判定。
    """

    permission_classes = [AllowAny]

    def get(self, request):
        try:
            with connection.cursor() as cur:
                cur.execute("SELECT 1")
        except Exception as exc:
            raise AppException(
                "SERVER_DATABASE_ERROR",
                message=str(exc)[:200] or "数据库探针失败",
            ) from exc
        return success_response({"checks": {"db": "ok"}})


urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("auth/sign-up/", SignUpView.as_view(), name="auth-signup"),
    path("auth/sign-in/", SignInView.as_view(), name="auth-signin"),
    path("auth/sign-out/", SignOutView.as_view(), name="auth-signout"),
    path("auth/csrf-token/", csrf_token, name="auth-csrf"),
    path("users/me/", MeView.as_view(), name="users-me"),
    path("workspaces/", WorkspaceListCreateView.as_view(), name="workspaces-list-create"),
    path("workspaces/<slug:slug>/", WorkspaceDetailView.as_view(), name="workspaces-detail"),
    path("workspaces/<slug:slug>/projects/", ProjectListCreateView.as_view(), name="projects-list-create"),
    path("workspaces/<slug:slug>/projects/<uuid:project_id>/", ProjectDetailView.as_view(), name="projects-detail"),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/states/",
        ProjectStateListView.as_view(),
        name="project-states",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issues/",
        IssueListCreateView.as_view(),
        name="issues-list-create",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/",
        IssueDetailView.as_view(),
        name="issues-detail",
    ),
]

#: sprint-1 按功能域拆分的路由片段（plane/app/routes/*.py 各自导出 urlpatterns）。
#: 拆分原因见 plane/app/routes/__init__.py：避免并行开发在单一 urls.py 上互相踩踏。
FEATURE_MODULES = (
    "permissions",      # AUTH-005 权限下发
    "users",            # AUTH-004 资料 / 密码 / 重置
    "members",          # TEAM-002 团队成员与邀请
    "project_members",  # PROJ-002 项目成员 / 收藏
    "labels",           # TASK-002 项目标签
    "attachments",      # FILE-001 任务附件
    "comments",         # COLLAB-001 评论
    "notifications",    # COLLAB-001 通知中心
    "stats",            # RPT-001 个人统计
)

for _name in FEATURE_MODULES:
    _module = import_module(f"plane.app.routes.{_name}")
    urlpatterns += _module.urlpatterns
