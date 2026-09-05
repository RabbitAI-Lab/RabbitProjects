"""任务评论服务（COLLAB-001 §4.4.2）。

收口：
  - 净化（sanitize）：服务端唯一可信边界（BR-03）
  - 长度校验（stripped 1~5000，BR-02）
  - @ 上限（BR：单条 ≤ 20）
  - 编辑窗口（15 分钟，BR-05；超窗 → RESOURCE_STATE_INVALID + EDIT_WINDOW_EXPIRED 子码）
  - 编辑 / 删除权限（本人或项目管理员，对象级 rbac §5.3）
  - on_commit → notify_comment.delay()（异步扇出落库）
  - P1 强制 parent_id NULL（楼中楼 P2 启用）
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from plane.app.comments.sanitize import (
    extract_mention_ids,
    sanitize_comment_html,
)
from plane.base.exception import AppException
from plane.db.models import IssueComment
from plane.db.models.roles import ProjectRole

logger = logging.getLogger("plane.db.services.comment")

EDIT_WINDOW = timedelta(minutes=15)
MIN_STRIPPED_LEN = 1
MAX_STRIPPED_LEN = 5000
MAX_MENTIONS = 20


def _safe_delay(task, *args, **kwargs):
    """投递 Celery 任务，broker 不可达时回退同步执行（本地无 MQ 验证环境）。"""
    try:
        task.delay(*args, **kwargs)
        return "queued"
    except Exception as exc:                              # noqa: BLE001
        logger.warning("comment.delivery_failed task=%s exc=%s",
                       getattr(task, "name", task), exc)
        try:
            # broker 不可达：直接同步落库（failsafe —— 本地无 MQ 也能产生通知）
            task.apply(args=args, kwargs=kwargs)
            return "sync"
        except Exception as inner:                         # noqa: BLE001
            logger.exception("comment.failsafe_failed task=%s exc=%s",
                             getattr(task, "name", task), inner)
            return "failed"


class CommentService:
    """评论生命周期服务 —— view 层只做参数解析与权限接线。"""

    # ── 创建 ──
    def create(self, *, issue, actor, payload: dict) -> tuple[IssueComment, list[str]]:
        raw_html = payload.get("comment_html", "")
        html = sanitize_comment_html(raw_html)
        from django.utils.html import strip_tags
        stripped = strip_tags(html).strip() if html else ""
        if len(stripped) < MIN_STRIPPED_LEN:
            raise AppException(
                "VALIDATION_ERROR",
                message="请求参数校验失败",
                details=[{"field": "comment_html", "code": "REQUIRED",
                          "message": "评论不能为空"}],
            )
        if len(stripped) > MAX_STRIPPED_LEN:
            raise AppException(
                "VALIDATION_ERROR",
                message="请求参数校验失败",
                details=[{"field": "comment_html", "code": "TOO_LONG",
                          "message": f"评论最多 {MAX_STRIPPED_LEN} 字符"}],
            )
        # parent 强制 NULL（P1 锁定扁平语义）
        if payload.get("parent_id"):
            raise AppException(
                "VALIDATION_ERROR",
                message="请求参数校验失败",
                details=[{"field": "parent_id", "code": "NOT_NULL",
                          "message": "P1 评论暂不支持楼中楼"}],
            )
        mentions = extract_mention_ids(html)
        if len(mentions) > MAX_MENTIONS:
            raise AppException(
                "RESOURCE_LIMIT_EXCEEDED",
                message=f"单条评论最多 @ {MAX_MENTIONS} 人",
            )

        with transaction.atomic():
            comment = IssueComment.objects.create(
                issue=issue,
                actor=actor,
                comment_html=html,
                comment_json=payload.get("comment_json", {}) or {},
                accessory=payload.get("accessory", {}) or {},
                created_by=actor,
                updated_by=actor,
            )
            transaction.on_commit(
                lambda: _safe_delay(_get_notify_task(), str(comment.id), str(issue.id))
            )
        return comment, sorted(mentions)

    # ── 编辑（15 分钟窗口）──
    def update(self, *, comment: IssueComment, actor, payload: dict,
               project_role: int | None) -> IssueComment:
        if comment.deleted_at:
            raise AppException("RESOURCE_NOT_FOUND")
        # 对象级权限：本人或项目管理员（rbac §5.3）
        is_owner = comment.actor_id == actor.id
        is_admin = (project_role is not None and project_role >= ProjectRole.ADMIN)
        if not (is_owner or is_admin):
            raise AppException(
                "PERM_DENIED",
                message="只能编辑自己发表的评论，或项目管理员可编辑他人评论",
            )
        if timezone.now() - comment.created_at > EDIT_WINDOW:
            raise AppException(
                "RESOURCE_STATE_INVALID",
                message="评论已超过 15 分钟编辑窗口",
                details=[{"field": "comment_id", "code": "EDIT_WINDOW_EXPIRED",
                          "message": "已超过 15 分钟编辑窗口，可删除后重新发表"}],
            )
        raw_html = payload.get("comment_html", "")
        html = sanitize_comment_html(raw_html)
        from django.utils.html import strip_tags
        stripped = strip_tags(html).strip() if html else ""
        if len(stripped) < MIN_STRIPPED_LEN:
            raise AppException(
                "VALIDATION_ERROR",
                message="请求参数校验失败",
                details=[{"field": "comment_html", "code": "REQUIRED",
                          "message": "评论不能为空"}],
            )
        if len(stripped) > MAX_STRIPPED_LEN:
            raise AppException(
                "VALIDATION_ERROR",
                message="请求参数校验失败",
                details=[{"field": "comment_html", "code": "TOO_LONG",
                          "message": f"评论最多 {MAX_STRIPPED_LEN} 字符"}],
            )
        comment.comment_html = html
        comment.comment_json = payload.get("comment_json", comment.comment_json) or {}
        comment.is_edited = True
        comment.updated_by = actor
        comment.save(update_fields=["comment_html", "comment_json", "comment_stripped",
                                      "is_edited", "updated_by", "updated_at"])
        # 编辑不重发通知（BR-05）
        return comment

    # ── 软删（占位行）──
    def soft_delete(self, *, comment: IssueComment, actor,
                    project_role: int | None) -> IssueComment:
        if comment.deleted_at:
            raise AppException("RESOURCE_NOT_FOUND")
        is_owner = comment.actor_id == actor.id
        is_admin = (project_role is not None and project_role >= ProjectRole.ADMIN)
        if not (is_owner or is_admin):
            raise AppException(
                "PERM_DENIED",
                message="只能删除自己发表的评论，或项目管理员可删除他人评论",
            )
        comment.deleted_at = timezone.now()
        comment.updated_by = actor
        comment.save(update_fields=["deleted_at", "updated_by", "updated_at"])
        return comment


def _get_notify_task():
    """延迟导入避免循环依赖（bgtasks 自动发现 plane.bgtasks.*）。"""
    from plane.bgtasks.comments import notify_comment_task
    return notify_comment_task
