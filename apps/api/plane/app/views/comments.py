"""任务评论视图（COLLAB-001 §4.3）。

端点（嵌套在 issue 下，全部强制尾斜杠）：
  GET    …/issues/{id}/comments/                   列表（正序）
  POST   …/issues/{id}/comments/                   发表
  PATCH  …/issues/{id}/comments/{comment_id}/     编辑（15 分钟窗口）
  DELETE …/issues/{id}/comments/{comment_id}/     软删占位

权限：comment.create（PROJ_COMMENTER+）+ 对象级（本人或项目管理员，rbac §5.3）。
"""
from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from plane.app.permissions import IsAuthenticated
from plane.app.serializers.comment import CommentSerializer, CommentWriteSerializer
from plane.app.views._access import get_project_or_404
from plane.base.exception import AppException
from plane.base.response import created_response, success_response
from plane.db.models import Issue, IssueComment
from plane.db.models.roles import ProjectRole
from plane.db.services.comment import CommentService


def _get_issue(slug, project_id, issue_id, user):
    project, _, _ = get_project_or_404(slug, project_id, user)
    try:
        issue = Issue.objects.select_related("project", "state").get(
            id=issue_id,
            project_id=project.id,
            deleted_at__isnull=True,
        )
    except Issue.DoesNotExist:
        raise NotFound("RESOURCE_NOT_FOUND") from None
    # 在 issue 上挂 current_user_role 供 Service 对象级校验复用
    issue.current_user_role = getattr(project, "current_user_role", None)
    return issue, project


def _require_comment_create(project, user) -> None:
    """PROJ_COMMENTER+ 门槛（rbac §2.5 + PERMISSION_MATRIX.project.comment.create）。"""
    role = getattr(project, "current_user_role", None) or 0
    if role < ProjectRole.COMMENTER:
        raise AppException("PERM_ROLE_INSUFFICIENT", message="当前角色权限不足")


# ─────────────────────────────────────────────────────────────────
# 列表 + 发表
# ─────────────────────────────────────────────────────────────────
class CommentListCreateView(GenericAPIView):
    """GET / POST …/issues/{issue_id}/comments/"""

    permission_classes = [IsAuthenticated]
    serializer_class = CommentSerializer

    def get(self, request, *args, **kwargs):
        issue, project = _get_issue(kwargs["slug"], kwargs["project_id"],
                                       kwargs["issue_id"], request.user)
        # 评论读权限 = project.read（PROJ_VIEWER+）
        if (getattr(project, "current_user_role", None) or 0) < ProjectRole.VIEWER:
            raise AppException("PERM_ROLE_INSUFFICIENT", message="当前角色权限不足")
        qs = (IssueComment.objects
                .filter(issue=issue, deleted_at__isnull=True)
                .select_related("actor")
                .order_by("created_at", "id"))
        try:
            per_page = min(int(request.query_params.get("per_page", 50)), 100)
        except (TypeError, ValueError):
            per_page = 50
        page_qs = qs[:per_page]
        data = CommentSerializer(page_qs, many=True).data
        return success_response(
            data,
            meta={
                "count": len(data),
                "total_count": qs.count(),
                "per_page": per_page,
            },
        )

    def post(self, request, *args, **kwargs):
        issue, project = _get_issue(kwargs["slug"], kwargs["project_id"],
                                       kwargs["issue_id"], request.user)
        _require_comment_create(project, request.user)
        s = CommentWriteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        comment, mentions = CommentService().create(
            issue=issue, actor=request.user, payload=s.validated_data
        )
        return created_response(
            CommentSerializer(comment).data,
            location=request.build_absolute_uri(
                f"/api/v1/workspaces/{kwargs['slug']}/projects/{project.id}/issues/{issue.id}/comments/{comment.id}/"
            ),
        )


# ─────────────────────────────────────────────────────────────────
# 详情（PATCH / DELETE）
# ─────────────────────────────────────────────────────────────────
class CommentDetailView(GenericAPIView):
    """PATCH / DELETE …/issues/{issue_id}/comments/{comment_id}/"""

    permission_classes = [IsAuthenticated]
    serializer_class = CommentSerializer

    def _get_comment(self, slug, project_id, issue_id, comment_id, user):
        issue, project = _get_issue(slug, project_id, issue_id, user)
        try:
            comment = IssueComment.objects.select_related("issue").get(
                id=comment_id, issue_id=issue.id,
            )
        except IssueComment.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        return comment, project

    def patch(self, request, *args, **kwargs):
        comment, project = self._get_comment(
            kwargs["slug"], kwargs["project_id"],
            kwargs["issue_id"], kwargs["comment_id"], request.user,
        )
        s = CommentWriteSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        updated = CommentService().update(
            comment=comment, actor=request.user, payload=s.validated_data,
            project_role=getattr(project, "current_user_role", None),
        )
        return success_response(CommentSerializer(updated).data)

    def delete(self, request, *args, **kwargs):
        comment, project = self._get_comment(
            kwargs["slug"], kwargs["project_id"],
            kwargs["issue_id"], kwargs["comment_id"], request.user,
        )
        deleted = CommentService().soft_delete(
            comment=comment, actor=request.user,
            project_role=getattr(project, "current_user_role", None),
        )
        return Response(
            {
                "status": "success",
                "data": {
                    "id": str(deleted.id),
                    "is_deleted": True,
                },
            },
            status=status.HTTP_200_OK,
        )
