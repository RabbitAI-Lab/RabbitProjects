"""工时服务（TASK-006 §4.3）——分钟制 + 主动填报 + 30 天补填窗口。

窗口校验作用于**本次写入值**（BR-04：编辑 29 天前的记录把日期改成 31 天前 → 400，
UT-16）；actor 服务端注入（BR-03）；汇总实时聚合非物化（BR-09 禁冗余列）。
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import date, timedelta

from django.db import connection, transaction
from django.db.models import Sum
from django.utils import timezone

from plane.db.models import ProjectMember, ProjectRole, WorkLog
from plane.db.models.issue import Issue

logger = logging.getLogger(__name__)

MAX_ESTIMATE_MINUTES = 525600  # 一年分钟数上限（§2.7 边界）


class WorklogValidationError(Exception):
    """400 VALIDATION_ERROR（details 携带字段与子码）。"""

    def __init__(self, field: str, code: str, message: str):
        self.field, self.code, self.message = field, code, message
        super().__init__(message)


class WorklogPermissionError(Exception):
    """403：仅本人或 PROJ_ADMIN 可管理（BR-05）/ 归档项目禁写（BR-11）。"""


def _check_window(worked_on: date) -> None:
    today = timezone.localdate()
    earliest = today - timedelta(days=30)
    if worked_on > today:
        raise WorklogValidationError("worked_on", "INVALID_DATE", "不能填报未来日期")
    if worked_on < earliest:
        raise WorklogValidationError("worked_on", "INVALID_DATE", f"仅可补填最近 30 天（最早 {earliest}）")


def _check_minutes(minutes: int) -> None:
    if not (1 <= minutes <= 1440):
        raise WorklogValidationError(
            "minutes", "TOO_LARGE" if minutes > 1440 else "TOO_SMALL", "时长须为 1~1440 的整数分钟"
        )


def assert_can_worklog(project_id: uuid.UUID, actor_id: uuid.UUID) -> None:
    """Service 层双保险：填报人须为 ≥CONTRIBUTOR 的 active 成员（VIEWER/COMMENTER 只读）。"""
    ok = ProjectMember.objects.filter(
        project_id=project_id,
        member_id=actor_id,
        is_active=True,
        role__in=[ProjectRole.ADMIN, ProjectRole.CONTRIBUTOR],
    ).exists()
    if not ok:
        raise WorklogPermissionError("仅项目协作者及以上角色可填报工时")


def spent_minutes_of(issue_id: uuid.UUID) -> int:
    return WorkLog.objects.filter(issue_id=issue_id, deleted_at__isnull=True).aggregate(s=Sum("minutes"))["s"] or 0


def _get_owned_log(*, log_id: uuid.UUID, issue_id: uuid.UUID, actor_id: uuid.UUID, is_admin: bool) -> WorkLog | None:
    log = WorkLog.objects.select_for_update().filter(id=log_id, issue_id=issue_id, deleted_at__isnull=True).first()
    if log is None:
        return None
    if log.actor_id != actor_id and not is_admin:
        raise WorklogPermissionError("只能管理自己填报的工时")
    return log


@transaction.atomic
def log_work(
    *, issue: Issue, actor_id: uuid.UUID, minutes: int, worked_on: date, note: str = ""
) -> tuple[WorkLog, int]:
    from plane.bgtasks.worklog import record_worklog

    if issue.project.status == "archived":  # BR-11（项目归档禁写的 Service 侧补位）
        raise WorklogPermissionError("项目已归档，不允许填报工时")
    _check_minutes(minutes)
    _check_window(worked_on)
    log = WorkLog.objects.create(
        issue=issue,
        actor_id=actor_id,
        worked_on=worked_on,
        minutes=minutes,
        note=(note or "")[:2000],
        created_by_id=actor_id,
    )
    spent = spent_minutes_of(issue.id)
    epoch = time.time() * 1000
    transaction.on_commit(lambda: record_worklog.delay(str(log.id), "created", epoch))
    return log, spent


@transaction.atomic
def update_worklog(
    *,
    log_id: uuid.UUID,
    issue_id: uuid.UUID,
    actor_id: uuid.UUID,
    is_admin: bool,
    minutes: int | None = None,
    worked_on: date | None = None,
    note: str | None = None,
) -> tuple[WorkLog | None, int]:
    """字段级可选更新；minutes/worked_on 变更时对**新值**重校验（BR-04/UT-16）。"""
    from plane.bgtasks.worklog import record_worklog

    log = _get_owned_log(log_id=log_id, issue_id=issue_id, actor_id=actor_id, is_admin=is_admin)
    if log is None:
        return None, 0
    if minutes is not None:
        _check_minutes(minutes)
        log.minutes = minutes
    if worked_on is not None:
        _check_window(worked_on)  # 编辑时对新值重校验 30 天窗口
        log.worked_on = worked_on
    if note is not None:
        log.note = note[:2000]
    log.updated_by_id = actor_id
    log.save()
    epoch = time.time() * 1000
    transaction.on_commit(lambda: record_worklog.delay(str(log.id), "updated", epoch))
    return log, spent_minutes_of(issue_id)


@transaction.atomic
def delete_worklog(*, log_id: uuid.UUID, issue_id: uuid.UUID, actor_id: uuid.UUID, is_admin: bool) -> int:
    from plane.bgtasks.worklog import record_worklog

    log = _get_owned_log(log_id=log_id, issue_id=issue_id, actor_id=actor_id, is_admin=is_admin)
    if log is None:
        return -1
    log.soft_delete(actor_id=actor_id)
    epoch = time.time() * 1000
    transaction.on_commit(lambda: record_worklog.delay(str(log.id), "deleted", epoch))
    return spent_minutes_of(issue_id)


# ─────────────────────────────────────────────────────────────────────
# 子树上卷（TASK-006 §4.3.2：复用 TASK-004 CTE 目标集范式）
# ─────────────────────────────────────────────────────────────────────
SUBTREE_WORKLOG_SQL = """
    WITH RECURSIVE target AS (
        SELECT id, estimate_minutes FROM issues
         WHERE id = %(root)s AND deleted_at IS NULL AND archived_at IS NULL
        UNION ALL
        SELECT i.id, i.estimate_minutes FROM issues i
          JOIN target t ON i.parent_id = t.id
         WHERE i.deleted_at IS NULL AND i.archived_at IS NULL
    )
    SELECT COALESCE((SELECT SUM(w.minutes) FROM work_logs w
                      WHERE w.issue_id IN (SELECT id FROM target)
                        AND w.deleted_at IS NULL), 0) AS spent,
           COALESCE((SELECT SUM(t2.estimate_minutes) FROM target t2
                      WHERE t2.estimate_minutes IS NOT NULL), 0) AS estimated
"""


def subtree_worklog_summary(root_id: uuid.UUID) -> dict[str, int]:
    """整树工时上卷：一次 CTE + 一次聚合。estimate NULL 不计分母（估了的达成率语义）。"""
    with connection.cursor() as cursor:
        cursor.execute(SUBTREE_WORKLOG_SQL, {"root": root_id})
        spent, estimated = cursor.fetchone()
    return {"subtree_spent_minutes": spent, "subtree_estimate_minutes": estimated}
