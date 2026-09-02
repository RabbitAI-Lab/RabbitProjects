from django.urls import path

from plane.app.views.auth import MeView, SignInView, SignOutView, SignUpView, csrf_token
from plane.app.views.issues import IssueDetailView, IssueListCreateView
from plane.app.views.projects import ProjectDetailView, ProjectListCreateView, ProjectStateListView
from plane.app.views.workspaces import WorkspaceDetailView, WorkspaceListCreateView

urlpatterns = [
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
