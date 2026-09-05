"""任务评论服务（COLLAB-001 §4.4.2 + COLLAB-002 §4.3.1 楼中楼扩展）。

收口：
  - 净化（sanitize）：服务端唯一可信边界（BR-03），COLLAB-002 起 img 白名单 +
    src 受控重写 + 评论图域校验（BR-07/BR-15），accessory.images 服务端聚合
  - 长度校验（stripped 1~5000，BR-02）
  - @ 上限（BR：单条 ≤ 20）
  - 楼中楼（COLLAB-002）：parent 同任务存活校验（BR-02）→ 两级归并（BR-03）
    → 单线程回复上限 100（BR-05）→ parent 契约翻转（BR-14：P1 强制 NULL 解锁）
  - 图片上限：单条评论 ≤ 9 张（BR-08 发表期计数）
  - 编辑窗口（15 分钟，BR-05；超窗 → RESOURCE_STATE_INVALID + EDIT_WINDOW_EXPIRED 子码）
  - 编辑 / 删除权限（本人或项目管理员，对象级 rbac §5.3）
  - on_commit → notify_comment.delay()（异步扇出落库；分派规则见 notify.py 三互斥）
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.html import strip_tags

from plane.app.comments.sanitize import extract_mention_ids, sanitize_comment
from plane.base.exception import AppException
from plane.db.models import FileAsset, IssueComment
from plane.db.models.roles import ProjectRole

logger = logging.getLogger("plane.db.services.comment")

EDIT_WINDOW = timedelta(minutes=15)
MIN_STRIPPED_LEN = 1
MAX_STRIPPED_LEN = 5000
MAX_MENTIONS = 20
# COLLAB-002 BR-05：单顶层评论回复数上限
MAX_REPLIES_PER_COMMENT = 100
# COLLAB-002 BR-08：单条评论图片数上限（发表期计数）
MAX_IMAGES_PER_COMMENT = 9


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


def _comment_image_asset_ids(issue, actor) -> set[str]:
    """当前任务评论图域的合法 asset_id 集（BR-07）。

    FileAsset 多态挂载无 issue_id 列，判定为
    ``entity_type='comment_image' ∧ entity_id=issue_id ∧ status='uploaded'
    ∧ uploaded_by=当前用户`` —— 防跨任务 asset ID 盗链与旧值冒用。
    """
    return {
        str(aid) for aid in
        FileAsset.objects.filter(
            entity_type=FileAsset.EntityType.COMMENT_IMAGE,
            entity_id=issue.id,
            status=FileAsset.Status.UPLOADED,
            uploaded_by=actor,
        ).values_list("id", flat=True)
    }


def _resolve_parent(*, issue, parent_id) -> tuple[IssueComment, IssueComment]:
    """解析归并后的挂载父评论与原始回复目标（BR-02 同任务存活 / BR-03 两级归并）。

    返回 ``(parent, target)``：target 为用户请求指向的评论（reply_to_actor 来源），
    parent 为归并后的顶层根（恒与 target 同线程）；目标无效一律抛 AppException。
    """
    target = IssueComment.objects.filter(
        id=parent_id, issue_id=issue.id, deleted_at__isnull=True,   # BR-02 同任务存活
    ).first()
    if target is None:
        raise AppException(
            "VALIDATION_ERROR",
            message="请求参数校验失败",
            details=[{"field": "parent_id", "code": "DOES_NOT_EXIST",
                      "message": "回复目标不存在或已删除"}],
        )
    if target.parent_id:                                            # BR-03 两级归并
        root = IssueComment.objects.filter(id=target.parent_id).first()
        if root is None:
            # 数据异常（回复行指向的根已被物理清除）——按目标无效拒绝
            raise AppException(
                "VALIDATION_ERROR",
                message="请求参数校验失败",
                details=[{"field": "parent_id", "code": "DOES_NOT_EXIST",
                          "message": "回复目标不存在或已删除"}],
            )
        return root, target
    return target, target


def _reply_actor_payload(comment: IssueComment | None) -> dict | None:
    """reply_to_actor 展示闭包（§4.2.1：前端渲染「回复 @xx」徽标）。"""
    if comment is None or not comment.actor_id:
        return None
    return {
        "id": str(comment.actor_id),
        "display_name": comment.actor.display_name if comment.actor else "已注销用户",
    }


class CommentService:
    """评论生命周期服务 —— view 层只做参数解析与权限接线。"""

    # ── 创建（含回复 / 图片，COLLAB-002）──
    def create(self, *, issue, actor, payload: dict) -> tuple[IssueComment, dict]:
        parent: IssueComment | None = None
        target: IssueComment | None = None
        parent_id = payload.get("parent_id")
        if parent_id:                                               # BR-14：P2 起合法
            parent, target = _resolve_parent(issue=issue, parent_id=parent_id)
            if (IssueComment.objects
                    .filter(parent_id=parent.id, deleted_at__isnull=True).count()
                    >= MAX_REPLIES_PER_COMMENT):                   # BR-05
                raise AppException(
                    "RESOURCE_LIMIT_EXCEEDED",
                    message="该评论回复已达上限，请直接发表新评论",
                    details=[{"field": "parent_id", "code": "LIMIT",
                              "message": f"单条评论最多 {MAX_REPLIES_PER_COMMENT} 条回复"}],
                )

        raw_html = payload.get("comment_html", "")
        allowed = _comment_image_asset_ids(issue, actor)            # BR-07 评论图域
        html, images = sanitize_comment(
            raw_html, allowed_asset_ids=allowed, issue_id=str(issue.id),
        )
        if len(images) > MAX_IMAGES_PER_COMMENT:                    # BR-08 发表期计数
            raise AppException(
                "RESOURCE_LIMIT_EXCEEDED",
                message="单条评论最多插入 9 张图片",
                details=[{"field": "comment_html", "code": "LIMIT",
                          "message": f"单条评论最多 {MAX_IMAGES_PER_COMMENT} 张图片"}],
            )
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
        mentions = extract_mention_ids(html)
        if len(mentions) > MAX_MENTIONS:
            raise AppException(
                "RESOURCE_LIMIT_EXCEEDED",
                message=f"单条评论最多 @ {MAX_MENTIONS} 人",
            )

        # accessory 服务端聚合（UT-18：客户端直传 accessory 被忽略）
        accessory: dict = {}
        if images:
            accessory["images"] = images
        if target is not None and parent is not None and target.id != parent.id:
            accessory["reply_to"] = {
                "comment_id": str(target.id),
                "actor_id": str(target.actor_id) if target.actor_id else None,
            }

        with transaction.atomic():
            comment = IssueComment.objects.create(
                issue=issue,
                actor=actor,
                parent=parent,
                comment_html=html,
                comment_json=payload.get("comment_json", {}) or {},
                accessory=accessory,
                created_by=actor,
                updated_by=actor,
            )
            transaction.on_commit(
                lambda: _safe_delay(_get_notify_task(), str(comment.id), str(issue.id))
            )
        return comment, {
            "mention_ids": sorted(mentions),
            "root_id": str(parent.id) if parent else None,
            "reply_to_actor": _reply_actor_payload(target if target is not None else None),
        }

    # ── 编辑（15 分钟窗口；图片同域重校验）──
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
        allowed = _comment_image_asset_ids(comment.issue, actor)    # BR-07 重校验
        html, images = sanitize_comment(
            raw_html,
            allowed_asset_ids=allowed,
            issue_id=str(comment.issue_id),
        )
        if len(images) > MAX_IMAGES_PER_COMMENT:
            raise AppException(
                "RESOURCE_LIMIT_EXCEEDED",
                message="单条评论最多插入 9 张图片",
                details=[{"field": "comment_html", "code": "LIMIT",
                          "message": f"单条评论最多 {MAX_IMAGES_PER_COMMENT} 张图片"}],
            )
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
        # accessory 重建：images 服务端聚合；reply_to（若原为归并回复）保留
        accessory: dict = dict(comment.accessory or {})
        accessory.pop("images", None)
        if images:
            accessory["images"] = images
        comment.comment_html = html
        comment.comment_json = payload.get("comment_json", comment.comment_json) or {}
        comment.accessory = accessory
        comment.is_edited = True
        comment.updated_by = actor
        comment.save(update_fields=["comment_html", "comment_json", "comment_stripped",
                                     "accessory", "is_edited", "updated_by", "updated_at"])
        # 编辑不重发通知（BR-05）
        return comment

    # ── 软删（占位行；父删子留 BR-06 由两级列表装配承载）──
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
