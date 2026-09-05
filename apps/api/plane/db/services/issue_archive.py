"""任务归档 / 恢复服务（TASK-009 §4.3.2）+ 归档写保护统一断言（§4.3.3 收口）。

归档语义（§1.1）：``archived_at`` 置位、整树级联（BR-07）、默认查询天然排除
（BR-11 排除方向命中偏索引 ``idx_issue_active_by_project``）、归档视图
``?archived=true`` 反向可查、可恢复。与删除（软删进回收站）正交：归档可恢复
且在归档视图可见，删除在默认 / 归档视图均不可见。

ARCHIVE_TARGET_CTE 与 delete_subtree（issue_hierarchy §4.3.4）同款范式：
仅排除软删、**不排除已归档后代**——重复 POST 必须能触达整树（幂等 BR-09），
恢复也以根为准整树放行（含此前分别归档的后代，§4.3.2）。

写保护（BR-08）：归档任务只读，任何写 409 ``RESOURCE_STATE_INVALID``
（details 子码 ``STATE``——任务资源**状态类**冲突归 409，api-conventions §8.5；
项目已归档的 403 ``PERM_PROJECT_ARCHIVED`` 是另一维度，BR-13）。收口原则：
视图 / 服务各写路径统一调 ``assert_issue_writable``，不散落内联判定；
TASK-007 的 ``IssueArchivedForAssignment`` 与 TASK-004 的 ``StateInvalidError``
保留各自异常名（既有 pytest 锚定 isinstance），但前者已改为本模块
``IssueArchivedError`` 子类——同根同码同子码语义。
"""
from __future__ import annotations

import logging
import time
import uuid

from django.db import connection, transaction
from django.utils import timezone

from plane.base.exception import AppException
from plane.db.models import Issue

logger = logging.getLogger(__name__)

#: 整树目标集（BR-07/09）：递归 CTE，仅排除软删——含已归档后代保证幂等可达。
#: 与 delete_subtree 同款（无展示侧截断、无 LIMIT）；环状脏数据属
#: CTE_GUARD_DEPTH 保险丝范畴（issue_hierarchy §4.3.1），本 CTE 消费上游
#: 已校验过的 parent 树，不重复设防。
ARCHIVE_TARGET_CTE = """
    WITH RECURSIVE target AS (
        SELECT id FROM issues
         WHERE id = %(root)s AND deleted_at IS NULL
        UNION ALL
        SELECT i.id FROM issues i JOIN target t ON i.parent_id = t.id
         WHERE i.deleted_at IS NULL
    )
"""


# ─────────────────────────────────────────────────────────────────────
# 归档写保护（§4.3.3）—— 全系统唯一判定 helper
# ─────────────────────────────────────────────────────────────────────
class IssueArchivedError(AppException):
    """BR-08：对已归档任务的一切写操作 —— 409 RESOURCE_STATE_INVALID（子码 STATE）。"""

    def __init__(self, *, field: str = "__all__", message: str | None = None, detail: str | None = None):
        text = message or "任务已归档，恢复后才能编辑"
        super().__init__(
            "RESOURCE_STATE_INVALID",
            message=text,
            details=[{"field": field, "code": "STATE", "message": detail or text}],
        )


def assert_issue_writable(
    issue: Issue, *, field: str = "__all__", message: str | None = None, detail: str | None = None
) -> None:
    """归档写保护统一断言（§4.3.3 收口点）。

    ``archived_at`` 非空即拒。允许的动作仅：GET、DELETE archive/（恢复）、
    DELETE（删除，归档态也可删除，§2.3 状态机）——即**调用方在恢复与删除
    路径上不调本函数**。T7-18~21（TASK-007 归档四路径）与 TASK-004 BR-13
    依赖同码同子码语义，收敛至此不改变任何既有行为。
    """
    if getattr(issue, "archived_at", None):
        raise IssueArchivedError(field=field, message=message, detail=detail)


# ─────────────────────────────────────────────────────────────────────
# Activity 投递（on_commit；broker 不可用时记日志不阻塞已提交事务）
# ─────────────────────────────────────────────────────────────────────
def _safe_delay(task, *args) -> None:
    try:
        task.delay(*args)
    except Exception as exc:  # noqa: BLE001 —— on_commit 回调抛错会打穿已提交请求（issue_assignee._dispatch 同款）
        logger.warning("issue_archive.activity_delivery_failed task=%s exc=%s", getattr(task, "name", task), exc)


# ─────────────────────────────────────────────────────────────────────
# 归档 / 恢复（§4.3.2）
# ─────────────────────────────────────────────────────────────────────
@transaction.atomic
def archive_subtree(*, issue_id: uuid.UUID, actor_id: uuid.UUID) -> dict:
    """整树归档（BR-07/09/10）。

    UPDATE 仅触及 ``archived_at IS NULL`` 的行——各节点**首次归档时间不可变**
    （BR-10：「什么时候归档的」是审计事实，恢复-再归档不覆写）。重复 POST
    时全部行已置位 → rowcount=0，幂等返回 200（api-conventions §2.6）。
    部分归档树（先归档子、再归档父）只补齐未归档行，计数即增量。
    """
    from plane.bgtasks.issue_hierarchy import record_archive

    now = timezone.now()
    epoch = time.time() * 1000  # TASK-010 BR-04：epoch 在动作入口生成（毫秒）
    with connection.cursor() as cursor:
        cursor.execute(
            ARCHIVE_TARGET_CTE
            + "UPDATE issues SET archived_at = %(now)s, updated_by_id = %(actor)s "
            " WHERE id IN (SELECT id FROM target) AND archived_at IS NULL",
            {"root": issue_id, "now": now, "actor": actor_id},
        )
        count = cursor.rowcount
    transaction.on_commit(
        lambda: _safe_delay(record_archive, str(issue_id), str(actor_id), int(count), now.isoformat(), epoch)
    )
    return {"archived_count": int(count), "archived_at": now}


@transaction.atomic
def restore_subtree(*, issue_id: uuid.UUID, actor_id: uuid.UUID) -> dict:
    """整树恢复（对称，§2.2）：以根为准整树放行，含此前分别归档的后代。

    仅清空已归档行（rowcount=0 → 幂等 200）；恢复后各节点保持归档前状态与
    字段（§2.6——本操作只动 ``archived_at`` / ``updated_by``）。
    """
    from plane.bgtasks.issue_hierarchy import record_restore

    epoch = time.time() * 1000
    with connection.cursor() as cursor:
        cursor.execute(
            ARCHIVE_TARGET_CTE
            + "UPDATE issues SET archived_at = NULL, updated_by_id = %(actor)s "
            " WHERE id IN (SELECT id FROM target) AND archived_at IS NOT NULL",
            {"root": issue_id, "actor": actor_id},
        )
        count = cursor.rowcount
    transaction.on_commit(
        lambda: _safe_delay(record_restore, str(issue_id), str(actor_id), int(count), epoch)
    )
    return {"restored_count": int(count)}
