"""comments 域路由片段（COLLAB-001 §4.3 + COLLAB-002 §4.2 表情 toggle）。

嵌套在 issue 下：
  …/workspaces/<slug>/projects/<uuid>/issues/<uuid>/comments/
  …/workspaces/<slug>/projects/<uuid>/issues/<uuid>/comments/<uuid>/
  …/workspaces/<slug>/projects/<uuid>/issues/<uuid>/comments/<uuid>/reactions/

reactions 为第 5 层资源（reactions 系叶子资源评论的直接子资源，
api-conventions §2.4 项目层以下不设嵌套限制，COLLAB-002 §4.2 路径深度锚定）。
"""
from django.urls import path

from plane.app.views.comments import (
    CommentDetailView,
    CommentListCreateView,
    CommentReactionToggleView,
)

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
    # POST / DELETE …/comments/<uuid>/reactions/（emoji 走请求体，幂等 toggle）
    path(
        "workspaces/<slug:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/"
        "comments/<uuid:comment_id>/reactions/",
        CommentReactionToggleView.as_view(),
        name="comment-reactions-toggle",
    ),
]
