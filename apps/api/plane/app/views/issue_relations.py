"""任务关联端点（TASK-005 §4.2）。

GET    /workspaces/{slug}/projects/{pid}/issues/{iid}/relations/            全部关联（契约冻结，GANTT-001 数据源）
POST   /workspaces/{slug}/projects/{pid}/issues/{iid}/relations/            创建（成对两行，201+Location）
DELETE /workspaces/{slug}/projects/{pid}/issues/{iid}/relations/{link_id}/  删除（镜像行同事务，204）
"""

from __future__ import annotations

from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated
from plane.app.views._access import get_project_or_404
from plane.base.exception import AppException
from plane.base.response import created_response, success_response
from plane.db.models import Issue, ProjectRole
from plane.db.services.issue_link import (
    AlreadyExistsError,
    CircularDependencyError,
    DirtyDependencyGraphError,
    RelationValidationError,
    create_relation,
    delete_relation,
    relations_of,
)

VALID_RELATION_TYPES = {"blocks", "is_blocked_by", "relates_to", "duplicates"}


def _get_issue_or_404(kwargs, project) -> Issue:
    issue = Issue.objects.filter(id=kwargs["issue_id"], project_id=project.id, deleted_at__isnull=True).first()
    if issue is None:
        raise NotFound("RESOURCE_NOT_FOUND") from None
    return issue


class IssueRelationListCreateView(APIView):
    """GET/POST relations/ —— GET 为契约冻结列表（50 上限天然有界，分页豁免）；
    POST {related_issue_id, relation_type}（当前任务为主语，is_blocked_by 归一化）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        issue = _get_issue_or_404(kwargs, project)
        data = relations_of(issue.id)
        return success_response(data, meta={"count": len(data)})

    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        if project.current_user_role < ProjectRole.CONTRIBUTOR:
            raise AppException("PERM_ROLE_INSUFFICIENT")
        issue = _get_issue_or_404(kwargs, project)
        related_id = request.data.get("related_issue_id")
        relation_type = request.data.get("relation_type")
        errors = []
        if not related_id:
            errors.append({"field": "related_issue_id", "code": "REQUIRED", "message": "目标任务为必填项"})
        if relation_type not in VALID_RELATION_TYPES:
            errors.append({"field": "relation_type", "code": "NOT_A_CHOICE", "message": "relation_type 取值非法"})
        if errors:
            raise AppException("VALIDATION_ERROR", details=errors)
        try:
            forward, mirror = create_relation(
                issue_id=issue.id, related_issue_id=related_id, relation_type=relation_type, actor_id=request.user.id
            )
        except RelationValidationError as e:
            raise AppException(
                "VALIDATION_ERROR",
                details=[{"field": e.field, "code": "DOES_NOT_EXIST", "message": e.message}],
            ) from None
        except AlreadyExistsError:
            raise AppException(
                "RESOURCE_ALREADY_EXISTS",
                message="两个任务之间已存在该关联",
                details=[{"field": "related_issue_id", "code": "UNIQUE", "message": "已存在同向或镜像关联"}],
            ) from None
        except CircularDependencyError as e:
            raise AppException(
                "RESOURCE_CIRCULAR_DEPENDENCY",
                message="该依赖会构成循环依赖",
                details=[{"field": "related_issue_id", "code": "CYCLE", "message": e.args[0]}],
            ) from None
        except DirtyDependencyGraphError:
            raise AppException("SERVER_ERROR", message="依赖图数据异常，已记录告警") from None
        return created_response(
            {
                "id": str(forward.id),
                "issue_id": str(forward.issue_id),
                "related_issue_id": str(forward.related_issue_id),
                "relation_type": forward.relation_type,
                "is_blocking": forward.relation_type in ("blocks", "is_blocked_by"),
                "mirror_id": str(mirror.id),
            },
            location=f"/api/v1/workspaces/{kwargs['slug']}/projects/"
            f"{kwargs['project_id']}/issues/{kwargs['issue_id']}/relations/{forward.id}/",
        )


class IssueRelationDeleteView(APIView):
    """DELETE relations/{link_id}/ —— 镜像行同事务软删，204。"""

    permission_classes = [IsAuthenticated]

    def delete(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        if project.current_user_role < ProjectRole.CONTRIBUTOR:
            raise AppException("PERM_ROLE_INSUFFICIENT")
        _get_issue_or_404(kwargs, project)
        link = delete_relation(link_id=kwargs["link_id"], actor_id=request.user.id)
        if link is None:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        return Response(status=204)
