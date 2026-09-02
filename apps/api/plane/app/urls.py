"""统一响应信封（api-conventions.md §4）。"""
from django.db import connection
from django.urls import path
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.views.auth import MeView, SignInView, SignOutView, SignUpView, csrf_token
from plane.app.views.issues import IssueDetailView, IssueListCreateView
from plane.app.views.projects import ProjectDetailView, ProjectListCreateView, ProjectStateListView
from plane.app.views.workspaces import WorkspaceDetailView, WorkspaceListCreateView


class HealthView(APIView):
    """INFRA-002 §4.10 健康检查端点（db 连接探针）。

    注意：celery/redis 连接故意不在此检查——服务组件健康由 compose depends_on 链表达，
    health 端点仅作为容器级可用性的真实探针（避免假就绪）。
    """
    permission_classes = [AllowAny]

    def get(self, request):
        try:
            with connection.cursor() as cur:
                cur.execute("SELECT 1")
            return Response({"status": "ok", "checks": {"db": "ok"}})
        except Exception as e:
            return Response({"status": "fail", "error": str(e)}, status=503)


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
