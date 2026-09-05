"""任务评论视图（COLLAB-001 §4.3 + COLLAB-002 §4.2 楼中楼 / 表情）。

端点（嵌套在 issue 下，全部强制尾斜杠）：
  GET    …/issues/{id}/comments/                                两层列表（正序游标分页）
  POST   …/issues/{id}/comments/                                发表评论 / 回复（含图片）
  PATCH  …/issues/{id}/comments/{comment_id}/                   编辑（15 分钟窗口）
  DELETE …/issues/{id}/comments/{comment_id}/                   软删占位
  POST   …/issues/{id}/comments/{comment_id}/reactions/         添加表情（幂等）
  DELETE …/issues/{id}/comments/{comment_id}/reactions/         撤销表情（幂等；emoji 走请求体）

权限：comment.create（COMMENTER+）+ 对象级（本人或项目管理员，rbac §5.3）；
表情与评论同级（BR-01）。列表读权限 = project.read（VIEWER+）。
"""
from __future__ import annotations

import base64

from django.db.models import Count, Q
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.comments.reaction_service import ReactionService
from plane.app.permissions import IsAuthenticated
from plane.app.serializers.comment import CommentSerializer, CommentWriteSerializer
from plane.app.views._access import get_project_or_404
from plane.base.exception import AppException
from plane.base.response import created_response, success_response
from plane.db.models import Issue, IssueComment, ProjectRole, User
from plane.db.services.comment import CommentService

#: 顶层分页默认页大小（COLLAB-002 §4.2.2 契约要点 1：30 顶层/页）
TOP_LEVEL_PER_PAGE = 30


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
    """COMMENTER+ 门槛（rbac §2.5 + PERMISSION_MATRIX.project.comment.create）。

    评论与表情同级（COLLAB-002 BR-01：点表情是轻量发言）。
    """
    role = getattr(project, "current_user_role", None) or 0
    if role < ProjectRole.COMMENTER:
        raise AppException("PERM_ROLE_INSUFFICIENT", message="当前角色权限不足")


def _decode_cursor(raw: str | None) -> int:
    """游标解码：``{value}:{offset}:{is_prev}`` Base64 → 偏移量（INFRA-003 格式）。"""
    if not raw:
        return 0
    try:
        return max(int(base64.b64decode(raw).decode().split(":")[1]), 0)
    except Exception:                                            # noqa: BLE001
        raise AppException("VALIDATION_INVALID_CURSOR") from None


def _encode_cursor(offset: int) -> str:
    return base64.b64encode(f"c:{offset}:0".encode()).decode()


def _build_reactions_map(comment_ids, viewer) -> dict:
    """页面评论集的表情聚合（单查询，§4.1.4 idx_reaction_comment_emoji）。

    返回 ``comment_id → [ {emoji, count, reacted_by_me, _uids} ]``（内部键
    ``_uids`` 由下发前按 expand 决定剥离或转出）；emoji 顺序按首次出现
    （created_at 正序——chips 的自然浮现序）。
    """
    from plane.db.models import CommentReaction

    reactions_map: dict[str, list[dict]] = {}
    rows = (
        CommentReaction.objects
        .filter(comment_id__in=list(comment_ids))
        .order_by("created_at")
        .values_list("comment_id", "emoji", "actor_id")
    )
    for cid, emoji, actor_id in rows:
        bucket = reactions_map.setdefault(str(cid), [])
        entry = next((e for e in bucket if e["emoji"] == emoji), None)
        if entry is None:
            entry = {"emoji": emoji, "count": 0, "reacted_by_me": False, "_uids": []}
            bucket.append(entry)
        entry["count"] += 1
        entry["_uids"].append(str(actor_id))
        if str(actor_id) == str(viewer.id):
            entry["reacted_by_me"] = True
    return reactions_map


def _build_reply_to_map(comments) -> dict:
    """accessory.reply_to → 被回复人展示闭包（§4.2.2 replies[].reply_to_actor）。"""
    actor_ids = {
        (c.accessory or {}).get("reply_to", {}).get("actor_id")
        for c in comments
    } - {None}
    users = {
        str(u.id): {"id": str(u.id), "display_name": u.display_name}
        for u in User.objects.filter(id__in=list(actor_ids))
    }
    out: dict[str, dict] = {}
    for c in comments:
        actor_id = (c.accessory or {}).get("reply_to", {}).get("actor_id")
        entry = users.get(str(actor_id)) if actor_id else None
        if entry is not None:
            out[str(c.id)] = entry
    return out


def _strip_private_entry(reactions_map: dict) -> dict:
    """去掉聚合内部的 ``_uids`` 中间键（user_ids 仅 expand 时下发）。"""
    return {
        cid: [
            {k: v for k, v in e.items() if k != "_uids"}
            for e in entries
        ]
        for cid, entries in reactions_map.items()
    }


def _expand_user_ids(reactions_map: dict) -> dict:
    """expand=reactions：追加 user_ids（名单浮层数据，§3.2）。"""
    return {
        cid: [
            {**{k: v for k, v in e.items() if k != "_uids"},
             "user_ids": e["_uids"]}
            for e in entries
        ]
        for cid, entries in reactions_map.items()
    }


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
        try:
            per_page = min(max(int(request.query_params.get("per_page", TOP_LEVEL_PER_PAGE), 1)), 100)
        except (TypeError, ValueError):
            per_page = TOP_LEVEL_PER_PAGE
        offset = _decode_cursor(request.query_params.get("cursor"))

        # 顶层线程集：游标按顶层分页；父删子留——软删父仅在其仍有存活回复时
        # 以占位行出现（BR-06/BR-13），无回复的软删评论维持 COLLAB-001 的消失语义。
        # 注意必须走 all_objects：默认软删管理器会先吞掉占位父行。
        top_qs = (
            IssueComment.all_objects
            .filter(issue=issue, parent__isnull=True)
            .annotate(_live_replies=Count(
                "replies", filter=Q(replies__deleted_at__isnull=True)))
            .filter(Q(deleted_at__isnull=True) | Q(_live_replies__gt=0))
            .order_by("created_at", "id")
        )
        total_count = top_qs.count()
        tops = list(top_qs.select_related("actor")[offset:offset + per_page])

        # replies 全量内联（≤100，正序）——两层一次取齐，无二次请求
        replies = list(
            IssueComment.objects
            .filter(parent_id__in=[t.id for t in tops], deleted_at__isnull=True)
            .select_related("actor")
            .order_by("created_at", "id")
        )
        replies_map: dict[str, list] = {}
        for r in replies:
            replies_map.setdefault(str(r.parent_id), []).append(r)

        all_comments = tops + replies
        reactions_map = _build_reactions_map([c.id for c in all_comments], request.user)
        expand = request.query_params.get("expand", "") == "reactions"
        final_reactions = _expand_user_ids(reactions_map) if expand else _strip_private_entry(reactions_map)
        reply_to_map = _build_reply_to_map(all_comments)

        data = CommentSerializer(
            tops, many=True,
            context={
                "replies_map": replies_map,
                "reactions_map": final_reactions,
                "reply_to_map": reply_to_map,
            },
        ).data

        next_cursor = _encode_cursor(offset + per_page) if offset + per_page < total_count else None
        prev_cursor = _encode_cursor(offset - per_page) if offset > 0 else None
        return success_response(
            data,
            meta={
                "next_cursor": next_cursor,
                "prev_cursor": prev_cursor,
                "next_page_results": next_cursor is not None,
                "prev_page_results": prev_cursor is not None,
                "count": len(data),
                "total_count": total_count,
                "total_pages": (total_count + per_page - 1) // per_page,
                "page": offset // per_page + 1,
                "per_page": per_page,
            },
        )

    def post(self, request, *args, **kwargs):
        issue, project = _get_issue(kwargs["slug"], kwargs["project_id"],
                                       kwargs["issue_id"], request.user)
        _require_comment_create(project, request.user)
        if project.status == "archived":
            raise AppException("PERM_PROJECT_ARCHIVED", message="项目已归档，评论只读")
        s = CommentWriteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        comment, extras = CommentService().create(
            issue=issue, actor=request.user, payload=s.validated_data
        )
        reply_to_map = {}
        if extras.get("reply_to_actor"):
            reply_to_map[str(comment.id)] = extras["reply_to_actor"]
        return created_response(
            CommentSerializer(comment, context={"reply_to_map": reply_to_map}).data,
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


# ─────────────────────────────────────────────────────────────────
# 表情 toggle（COLLAB-002 §4.2.3）
# ─────────────────────────────────────────────────────────────────
class CommentReactionToggleView(APIView):
    """POST / DELETE …/comments/{comment_id}/reactions/

    emoji 走请求体（路径参数仅 UUID/slug，api-conventions §2.3）；两次调用
    均幂等（changed 标识是否实际变更）。
    """

    permission_classes = [IsAuthenticated]

    def _get_comment(self, slug, project_id, issue_id, comment_id, user):
        issue, project = _get_issue(slug, project_id, issue_id, user)
        _require_comment_create(project, user)
        if project.status == "archived":
            raise AppException("PERM_PROJECT_ARCHIVED", message="项目已归档，评论只读")
        try:
            comment = IssueComment.objects.get(
                id=comment_id, issue_id=issue.id, deleted_at__isnull=True,
            )
        except IssueComment.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        return comment

    @staticmethod
    def _emoji_from(request) -> str:
        emoji = request.data.get("emoji")
        if not isinstance(emoji, str) or not emoji:
            raise AppException(
                "VALIDATION_ERROR",
                message="请求参数校验失败",
                details=[{"field": "emoji", "code": "REQUIRED",
                          "message": "emoji 为必填"}],
            )
        return emoji

    def post(self, request, *args, **kwargs):
        comment = self._get_comment(
            kwargs["slug"], kwargs["project_id"], kwargs["issue_id"],
            kwargs["comment_id"], request.user,
        )
        data = ReactionService().toggle_on(
            comment=comment, actor=request.user, emoji=self._emoji_from(request),
        )
        return success_response(data)

    def delete(self, request, *args, **kwargs):
        comment = self._get_comment(
            kwargs["slug"], kwargs["project_id"], kwargs["issue_id"],
            kwargs["comment_id"], request.user,
        )
        data = ReactionService().toggle_off(
            comment=comment, actor=request.user, emoji=self._emoji_from(request),
        )
        return success_response(data)
