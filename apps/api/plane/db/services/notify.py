"""通知扇出服务（COLLAB-001 §4.4.3 / RPT-001 §4.2 摘要）。

四个核心动作：
  1. ``fanout_comment`` —— 新评论：mentioned ∪ commented 分派（去重）
  2. ``fanout_issue_event`` —— 单 issue 事件（assigned / updated / 描述新增 mention）
  3. ``mark_read`` / ``read_all`` —— 通知已读动作（user 域内）

设计要点：
- 所有动作一律 ``bulk_create(ignore_conflicts=True)``，兜住重试 / MQ 重投（BR-08）
- 接收域恒过项目成员 + WS_ADMIN+ 隐式成员（rbac §7.4），非域内接收人一律丢弃
- ``actor_id`` 可为 None（系统操作），不抛错
- 失败重投由 ``Notification`` 的 ``uniq_notif_dedup`` 唯一约束兜底
"""
from __future__ import annotations

import logging

from django.utils import timezone

from plane.db.models import Issue, IssueAssignee, Notification, ProjectMember
from plane.db.models.roles import WorkspaceRole

logger = logging.getLogger("plane.db.services.notify")

# 单条评论 @ 上限（COLLAB-001 §2.7）
MAX_MENTIONS_PER_COMMENT = 20


# ── 域成员解析（评论接收人 = 项目成员 ∪ WS_ADMIN+，rbac §7.4）──
def _member_ids_for_issue(issue: Issue) -> set[str]:
    """通知接收域 = 项目 active 成员 ∪ WS_ADMIN+（隐式成员）。

    与 IssueCommentService.fanout 共享同一计算（避免逻辑漂移）。
    """
    members = set(
        ProjectMember.objects.filter(
            project=issue.project, is_active=True, deleted_at__isnull=True,
        ).values_list("member_id", flat=True)
    )
    ws_admins = set(
        issue.project.workspace.workspace_member
        .filter(role__gte=WorkspaceRole.ADMIN, is_active=True, deleted_at__isnull=True)
        .values_list("member_id", flat=True)
    )
    return members | ws_admins


def _assignee_ids(issue: Issue) -> set[str]:
    return set(
        IssueAssignee.objects.filter(issue=issue).values_list("assignee_id", flat=True)
    )


def _title_for(event: str, *, actor_name: str, issue_key: str, summary: str = "") -> str:
    """事件 → 中文 title 文案（200 字符内）。"""
    if event == Notification.Event.ISSUE_ASSIGNED:
        return f"{actor_name} 将 {issue_key} 指派给你"[:200]
    if event == Notification.Event.ISSUE_MENTIONED:
        return f"{actor_name} 在 {issue_key} 中提到了你"[:200]
    if event == Notification.Event.ISSUE_COMMENTED:
        return f"{actor_name} 评论了 {issue_key}"[:200]
    if event == Notification.Event.ISSUE_UPDATED:
        suffix = f"：{summary}" if summary else ""
        return f"{actor_name} 更新了 {issue_key}{suffix}"[:200]
    return f"{actor_name} 触发了 {event}"[:200]


def _epoch_seconds(value) -> str:
    """同源 epoch —— 用 UTC 秒级整数去重。"""
    return str(int(value.timestamp()))


def _build_rows(*, event: str, issue: Issue, actor, comment_id: str | None,
                receiver_ids: set[str], epoch: str,
                summary: str = "", changes: list | None = None,
                merged_count: int | None = None,
                merged_keys: list[str] | None = None) -> list[Notification]:
    """构造 Notification 列表（不落库）。"""
    actor_id = str(actor.id) if actor else "system"
    actor_name = actor.display_name if actor else "系统"
    data_base = {
        "issue_id": str(issue.id),
        "project_id": str(issue.project_id),
        "workspace_slug": issue.project.workspace.slug,
        "issue_key": f"{issue.project.identifier}-{issue.sequence_id}",
        "actor": actor_name,
        "actor_id": actor_id,
    }
    if comment_id:
        data_base["comment_id"] = str(comment_id)
    if changes:
        data_base["changes"] = changes
    if merged_count:
        data_base["merged_count"] = merged_count
        data_base["merged_keys"] = merged_keys or []

    rows: list[Notification] = []
    for rid in receiver_ids:
        rows.append(
            Notification(
                receiver_id=rid,
                event=event,
                title=_title_for(event, actor_name=actor_name,
                                  issue_key=data_base["issue_key"], summary=summary),
                data=data_base,
                dedup_key=Notification.build_dedup_key(
                    event=event, issue_id=issue.id,
                    actor_id=actor_id, epoch=epoch, receiver_id=str(rid),
                ),
            )
        )
    return rows


# ── 公开入口 ───────────────────────────────────────────────────────

def fanout_comment(*, comment_id: str, issue_id: str, actor,
                   mention_ids: set[str]) -> int:
    """新评论扇出：mentioned / commented 两类事件（BR-06 互斥分派）。

    接收人集合 = (mentions ∪ assignees ∪ creator) − actor − 域外。
    """
    try:
        issue = Issue.objects.select_related("project", "project__workspace").get(
            id=issue_id, deleted_at__isnull=True
        )
    except Issue.DoesNotExist:
        logger.warning("notify.fanout_comment.issue_missing comment=%s issue=%s",
                       comment_id, issue_id)
        return 0

    # 重读 comment：评论可能已被删除（删除任务超时），被删则静默跳过
    from plane.db.models import IssueComment
    try:
        cm = IssueComment.objects.only("id", "deleted_at", "created_at").get(id=comment_id)
    except IssueComment.DoesNotExist:
        return 0
    if cm.deleted_at:
        return 0

    if actor is None:
        return 0
    actor_id = str(actor.id)

    member_ids = _member_ids_for_issue(issue)
    # 域内 @（BR-04：域外锚点保留原文但不触发通知 —— 服务端解析后已过滤）。
    # 注意：mention_ids 来自 Service 传过来的字符串集合；member_ids / actor_id 是 UUID；
    # 为避免混型比较失败，统一转字符串后比对。
    str_member_ids = {str(m) for m in member_ids}
    safe_mentions = {m for m in mention_ids if m in str_member_ids and m != actor_id}
    if len(safe_mentions) > MAX_MENTIONS_PER_COMMENT:
        # 二次校验：序列化层已拦一次；这里兜底
        return 0

    if issue.created_by_id:
        fanout_base = _assignee_ids(issue) | {str(issue.created_by_id)}
    else:
        fanout_base = _assignee_ids(issue)
    # str-化所有参与集合（UUID/str 混型比对一致）
    fanout_base = {str(x) for x in fanout_base}
    others = (fanout_base - safe_mentions - {actor_id}) & str_member_ids

    epoch = _epoch_seconds(cm.created_at)
    rows: list[Notification] = []
    rows += _build_rows(event=Notification.Event.ISSUE_MENTIONED, issue=issue,
                         actor=actor, comment_id=comment_id,
                         receiver_ids=safe_mentions, epoch=epoch)
    rows += _build_rows(event=Notification.Event.ISSUE_COMMENTED, issue=issue,
                         actor=actor, comment_id=comment_id,
                         receiver_ids=others, epoch=epoch)
    if not rows:
        return 0
    # bulk_create + ignore_conflicts：worker 重投零重复（BR-08）
    Notification.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


def fanout_issue_event(*, event: str, issue_id: str, actor,
                       changes: list | None = None,
                       new_assignee_ids: list[str] | None = None) -> int:
    """单 issue 事件扇出：assigned / updated / 描述新增 mention。

    - assigned：仅「新增」被指派人（new_assignee_ids 差集，移除不通知）
    - updated：指派人 ∪ 创建者收 changes 摘要
    """
    try:
        issue = Issue.objects.select_related("project", "project__workspace").get(
            id=issue_id, deleted_at__isnull=True
        )
    except Issue.DoesNotExist:
        return 0

    member_ids = _member_ids_for_issue(issue)
    str_member_ids = {str(m) for m in member_ids}
    actor_id = str(actor.id) if actor else None
    epoch = _epoch_seconds(timezone.now())
    summary = "；".join(f"{c.get('field', '?')} {c.get('old', '?')} → {c.get('new', '?')}"
                        for c in (changes or []))[:160]

    rows: list[Notification] = []
    if event == Notification.Event.ISSUE_ASSIGNED:
        receivers = {str(a) for a in (new_assignee_ids or [])} & str_member_ids
        if actor_id is not None:
            receivers.discard(actor_id)
        rows = _build_rows(event=event, issue=issue, actor=actor,
                           comment_id=None, receiver_ids=receivers, epoch=epoch)
    elif event == Notification.Event.ISSUE_UPDATED:
        if issue.created_by_id:
            fanout = {str(x) for x in _assignee_ids(issue)} | {str(issue.created_by_id)}
        else:
            fanout = {str(x) for x in _assignee_ids(issue)}
        receivers = (fanout - {actor_id}) & str_member_ids
        receivers.discard(None)  # type: ignore[arg-type]
        rows = _build_rows(event=event, issue=issue, actor=actor,
                           comment_id=None, receiver_ids=receivers,
                           epoch=epoch, summary=summary, changes=changes or [])
    if not rows:
        return 0
    Notification.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


def fanout_issue_event_batch(*, event: str, issue_ids: list[str], actor,
                              changes: list | None = None) -> int:
    """批量事件扇出：同 actor 跨 N issue 共享 title（COLLAB-001 §2.3 epoch 合并）。

    落 N 条 Notification，每条独立指向一个 issue；title 跨 issue 共享「更新了 N 个任务」。
    """
    if not issue_ids:
        return 0
    issues = list(Issue.objects.select_related("project", "project__workspace")
                  .filter(id__in=issue_ids, deleted_at__isnull=True))
    if not issues:
        return 0
    actor_id = str(actor.id) if actor else None
    actor_name = actor.display_name if actor else "系统"
    merged_count = len(issues)
    keys = [f"{i.project.identifier}-{i.sequence_id}" for i in issues[:10]]
    suffix = f" 等 {merged_count} 个" if merged_count > 10 else ""
    summary = "；".join(f"{c.get('field', '?')} {c.get('old', '?')} → {c.get('new', '?')}"
                        for c in (changes or []))[:120]
    base_title = f"{actor_name} 更新了 {merged_count} 个任务{suffix}：{summary}"[:200]
    epoch = _epoch_seconds(timezone.now())

    rows: list[Notification] = []
    for issue in issues:
        member_ids = _member_ids_for_issue(issue)
        str_member_ids = {str(m) for m in member_ids}
        if issue.created_by_id:
            fanout = {str(x) for x in _assignee_ids(issue)} | {str(issue.created_by_id)}
        else:
            fanout = {str(x) for x in _assignee_ids(issue)}
        receivers = (fanout - {actor_id}) & str_member_ids
        receivers.discard(None)  # type: ignore[arg-type]
        for rid in receivers:
            data = {
                "issue_id": str(issue.id),
                "project_id": str(issue.project_id),
                "workspace_slug": issue.project.workspace.slug,
                "issue_key": f"{issue.project.identifier}-{issue.sequence_id}",
                "actor": actor_name,
                "actor_id": actor_id or "system",
                "merged_count": merged_count,
                "merged_keys": keys,
                "changes": changes or [],
            }
            rows.append(
                Notification(
                    receiver_id=rid,
                    event=event,
                    title=base_title,
                    data=data,
                    dedup_key=Notification.build_dedup_key(
                        event=event, issue_id=issue.id,
                        actor_id=actor_id or "system", epoch=epoch,
                        receiver_id=str(rid),
                    ),
                )
            )
    if not rows:
        return 0
    Notification.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


# ── 已读动作（本人域 BR-12）───────────────────────────────────────

def mark_read(*, user, notification_id: str) -> bool:
    """单条标记已读（幂等）。返回是否有变化。"""
    from plane.db.models import Notification
    qs = Notification.objects.filter(id=notification_id, receiver=user,
                                      read_at__isnull=True)
    updated = qs.update(read_at=timezone.now())
    return updated > 0


def read_all(*, user) -> int:
    """全部已读 —— 仅本人域（receiver=user）。"""
    from plane.db.models import Notification
    return Notification.objects.filter(
        receiver=user, read_at__isnull=True,
    ).update(read_at=timezone.now())


def unread_count(*, user) -> int:
    from plane.db.models import Notification
    return Notification.objects.filter(receiver=user, read_at__isnull=True).count()
