"""comments 域路由片段（COLLAB-001 §4.3）。

嵌套在 issue 下：
  …/workspaces/<slug>/projects/<uuid>/issues/<uuid>/comments/
  …/workspaces/<slug>/projects/<uuid>/issues/<uuid>/comments/<uuid>/
"""
from django.urls import path

from plane.app.views.comments import CommentDetailView, CommentListCreateView

urlpatterns = [
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/comments/",
        CommentListCreateView.as_view(),
        name="comments-list-create",
    ),
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/comments/<uuid:comment_id>/",
        CommentDetailView.as_view(),
        name="comments-detail",
    ),
]
