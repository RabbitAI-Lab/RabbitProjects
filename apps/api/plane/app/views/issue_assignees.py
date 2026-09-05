"""多执行人端点（TASK-007 §4.2）。

PUT    /workspaces/{slug}/projects/{pid}/issues/{iid}/assignees/            全量替换（转交）
POST   /workspaces/{slug}/projects/{pid}/issues/{iid}/assignees/claim/      认领（空集合才可）
DELETE /workspaces/{slug}/projects/{pid}/issues/{iid}/assignees/{user_id}/  自退（仅 user_id=自己）

三条路径全部收敛 ``sync_assignees_full``（唯一写入口，§4.3.1）——PUT 是「意图更明确
的外观」，不存在第二套写逻辑。
"""

from __future__ import annotations

import uuid

from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated
from plane.app.views._access import get_project_or_404
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.db.models import Issue, ProjectRole
from plane.db.services.issue_assignee import (
    claim_issue,
    remove_self,
    sync_assignees_full,
)
from plane.utils.exceptions import field_error

#: BR-08 转交说明上限（随 Activity 落库、通知正文引用）
MAX_TRANSFER_COMMENT_LENGTH = 500


def _issue_or_404(project, issue_id) -> Issue:
    issue = Issue.objects.filter(
        id=issue_id, project_id=project.id, deleted_at__isnull=True
    ).first()
    if issue is None:
        raise NotFound("RESOURCE_NOT_FOUND") from None
    return issue


def _ensure_contributor(project) -> None:
    """issue.assign（CONTRIBUTOR+）：PUT / claim 的写门槛（rbac §8）。"""
    if project.current_user_role < ProjectRole.CONTRIBUTOR:
        raise AppException("PERM_ROLE_INSUFFICIENT")


class IssueAssigneesView(APIView):
    """PUT …/issues/{iid}/assignees/ —— 全量替换执行人集合（§4.2.1）。

    body: ``{"assignee_ids": [...], "comment?": "≤500 字转交说明"}``；
    响应 200：``data.{issue_id, assignee_ids, changes.added/removed}`` +
    ``meta.assigned_by``（BR-04「谁转的」）。``changes`` 回传让前端零本地 diff。
    """

    permission_classes = [IsAuthenticated]

    def put(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        _ensure_contributor(project)
        issue = _issue_or_404(project, kwargs["issue_id"])

        payload = request.data or {}
        if "assignee_ids" not in payload:
            raise AppException(
                "VALIDATION_ERROR",
                details=[{"field": "assignee_ids", "code": "REQUIRED",
                          "message": "assignee_ids 为必填"}],
            )
        raw = payload["assignee_ids"]
        if not isinstance(raw, list):
            raise AppException(
                "VALIDATION_ERROR",
                details=[{"field": "assignee_ids", "code": "INVALID",
                          "message": "assignee_ids 必须为 UUID 列表"}],
            )
        new_ids: list[uuid.UUID] = []
        for x in raw:
            try:
                new_ids.append(uuid.UUID(str(x)))
            except (ValueError, AttributeError, TypeError) as err:
                raise AppException(
                    "VALIDATION_ERROR",
                    details=[field_error("assignee_ids", "INVALID_UUID",
                                         f"UUID 格式非法：{x}")],
                ) from err
        comment = str(payload.get("comment") or "").strip()
        if len(comment) > MAX_TRANSFER_COMMENT_LENGTH:  # BR-08
            raise AppException(
                "VALIDATION_ERROR",
                details=[{"field": "comment", "code": "TOO_LONG",
                          "message": f"转交说明最长 {MAX_TRANSFER_COMMENT_LENGTH} 字"}],
            )

        result = sync_assignees_full(
            issue_id=issue.id, new_ids=new_ids, actor_id=request.user.id, comment=comment
        )
        return success_response(
            {
                "issue_id": str(issue.id),
                "assignee_ids": result["assignee_ids"],
                "changes": result["changes"],
            },
            meta={"assigned_by": str(request.user.id)},
        )


class IssueAssigneeClaimView(APIView):
    """POST …/issues/{iid}/assignees/claim/ —— 认领（§4.2.2）。

    空集合才可（BR-05，行锁判空防并发：恰一人 200、其余 409 STATE）；
    成功 200 回新集合与 changes（assignee=assigned_by=自己，BR-04）。
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        _ensure_contributor(project)
        issue = _issue_or_404(project, kwargs["issue_id"])
        result = claim_issue(issue_id=issue.id, actor_id=request.user.id)
        return success_response(
            {
                "issue_id": str(issue.id),
                "assignee_ids": result["assignee_ids"],
                "changes": result["changes"],
            }
        )


class IssueAssigneeDeleteView(APIView):
    """DELETE …/issues/{iid}/assignees/{user_id}/ —— 自退（§4.2.3）。

    BR-07 仅 user_id == 操作者本人（删他人 = 转交语义，走 PUT）→ 403 PERM_DENIED；
    行为收敛为「移除自己的集合替换」（remove_self → sync，最后一人退出 = 合法清空 BR-06）。
    已归档任务 409 STATE 由 sync 统一入口兜底（DELETE 不在 Permission 拦截器覆盖内，§2.4）。
    """

    permission_classes = [IsAuthenticated]

    def delete(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        issue = _issue_or_404(project, kwargs["issue_id"])
        if str(kwargs["user_id"]) != str(request.user.id):
            raise AppException(
                "PERM_DENIED",
                message="只能移除自己的执行人身份；调整他人请使用转交（PUT …/assignees/）",
            )
        remove_self(issue_id=issue.id, user_id=request.user.id)
        return Response(status=204)
