"""任务生命周期端点（TASK-009 §4.2）：复制 / 归档 / 恢复。

  POST   /workspaces/{slug}/projects/{pid}/issues/{iid}/duplicate/  五选项深拷贝（201+Location）
  POST   /workspaces/{slug}/projects/{pid}/issues/{iid}/archive/    整树幂等归档（200）
  DELETE /workspaces/{slug}/projects/{pid}/issues/{iid}/archive/    整树对称恢复（200）
"""
from __future__ import annotations

from rest_framework.exceptions import NotFound
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated
from plane.app.views._access import get_project_or_404, require_project_writable
from plane.base.exception import AppException
from plane.base.response import created_response, success_response
from plane.db.models import Issue, ProjectRole
from plane.db.services.issue_archive import IssueArchivedError, archive_subtree, restore_subtree
from plane.db.services.issue_copy import DuplicateOptions, duplicate_issue
from plane.db.services.issue_hierarchy import SubtreeDepthGuardError


def _issue_or_404(kwargs, project, *, allow_archived=True) -> Issue:
    issue = Issue.objects.filter(
        id=kwargs["issue_id"], project_id=project.id, deleted_at__isnull=True).first()
    if issue is None or (not allow_archived and issue.archived_at):
        raise NotFound("RESOURCE_NOT_FOUND") from None
    return issue


class IssueDuplicateView(APIView):
    """POST duplicate/ —— 五选项，默认 子✓执行人✗标签✓字段✓日期✗（§4.2.1）。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        if project.current_user_role < ProjectRole.CONTRIBUTOR:
            raise AppException("PERM_ROLE_INSUFFICIENT")
        require_project_writable(project)  # archived/closed 只读（PROJ-003）
        issue = _issue_or_404(kwargs, project)
        options = DuplicateOptions(
            include_subtrees=bool(request.data.get("include_subtrees", True)),
            include_assignees=bool(request.data.get("include_assignees", False)),
            include_labels=bool(request.data.get("include_labels", True)),
            include_custom_fields=bool(request.data.get("include_custom_fields", True)),
            include_dates=bool(request.data.get("include_dates", False)),
        )
        try:
            result = duplicate_issue(
                issue_id=issue.id, actor_id=request.user.id, options=options)
        except IssueArchivedError:
            raise AppException(
                "RESOURCE_STATE_INVALID",
                message="任务已归档，恢复后才能复制",
                details=[{"field": "source_issue_id", "code": "STATE",
                          "message": "源任务已归档"}],
            ) from None
        except SubtreeDepthGuardError:
            raise AppException("SERVER_ERROR", message="层级数据异常，已记录告警") from None
        root = result["root"]
        return created_response(
            {
                "id": str(root.id),
                "issue_key": f"{project.identifier}-{root.sequence_id}",
                "name": root.name,
                "parent_id": None,
                "total_created": result["total_created"],
            },
            location=f"/api/v1/workspaces/{kwargs['slug']}/projects/"
                     f"{kwargs['project_id']}/issues/{root.id}/",
        )


class IssueArchiveView(APIView):
    """POST archive/（ADMIN 或创建者）——整树幂等；DELETE archive/ 恢复对称。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        issue = _issue_or_404(kwargs, project)
        if project.current_user_role < ProjectRole.ADMIN and issue.created_by_id != request.user.id:
            raise AppException("PERM_ROLE_INSUFFICIENT", message="只能归档自己创建的任务")
        out = archive_subtree(issue_id=issue.id, actor_id=request.user.id)
        return success_response(out)

    def delete(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        issue = _issue_or_404(kwargs, project)
        if project.current_user_role < ProjectRole.ADMIN and issue.created_by_id != request.user.id:
            raise AppException("PERM_ROLE_INSUFFICIENT", message="只能恢复自己创建的任务")
        out = restore_subtree(issue_id=issue.id, actor_id=request.user.id)
        return success_response(out)
