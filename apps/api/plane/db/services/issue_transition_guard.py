"""流转拦截钩子（TASK-005 §4.3.3）——迁入 completed 前的依赖检查。

挂 TASK-001 更新路径：目标 state.group == 'completed' 且原 group != 'completed'
时调用；force 通道仅 PROJ_ADMIN（403 同口径），comment 必填校验在视图层。
cancelled 前置 = 解除阻塞（BR-07：NOT IN ('completed','cancelled')）。
"""

from __future__ import annotations

import uuid

from django.db import connection

from plane.db.models import Issue, State
from plane.db.services.issue_link import TransitionBlockedError

BLOCKER_SQL = """
    SELECT i.id, i.sequence_id, i.name, s."group"
      FROM issue_links l
      JOIN issues i ON i.id = l.related_issue_id
      LEFT JOIN states s ON s.id = i.state_id
     WHERE l.issue_id = %(me)s
       AND l.relation_type = 'is_blocked_by'
       AND l.deleted_at IS NULL
       AND i.deleted_at IS NULL
       AND i.project_id = %(proj)s
       AND COALESCE(s."group", 'unstarted') NOT IN ('completed', 'cancelled')
"""
# PROJ-004 §4.4（BR-07 软策略，Sprint-9）：仅同项目阻塞源参与完成拦截——
# 跨项目依赖只做可视化与统计（组合树上下文不同，硬拦易误伤）；
# 环检测（issue_link._reaches）不加项目过滤：环就是环，跨项目同样禁止（BR-09）。


def assert_completable(*, issue: Issue, to_state: State, force: bool, is_admin: bool) -> None:
    """不通过则抛 TransitionBlockedError（409）或 PermissionError（403 force 非管理员）。"""
    if to_state.group != "completed":
        return
    with connection.cursor() as cursor:
        cursor.execute(BLOCKER_SQL, {"me": issue.id, "proj": issue.project_id})
        blockers = cursor.fetchall()
    if not blockers:
        return
    if force:
        if not is_admin:
            raise PermissionError("仅项目管理员可强制完成")
        return
    proj_identifier = (
        issue.project.identifier
        if getattr(issue, "project", None)
        else (Issue.objects.filter(pk=issue.pk).values_list("project__identifier", flat=True).first() or "")
    )
    raise TransitionBlockedError(
        blockers=[
            {
                "id": str(b[0]),
                "issue_key": f"{proj_identifier}-{b[1]}",
                "name": b[2],
                "state_group": b[3] or "unstarted",
            }
            for b in blockers
        ]
    )


def open_blocker_count(issue_id: uuid.UUID) -> int:
    """?blocked=true 筛选与看板角标共用：未完成前置数（0 = 未被阻塞）。

    口径与 assert_completable 一致（同项目阻塞源，PROJ-004 BR-07）。"""
    proj_id = Issue.objects.filter(pk=issue_id).values_list("project_id", flat=True).first()
    with connection.cursor() as cursor:
        cursor.execute(BLOCKER_SQL, {"me": issue_id, "proj": proj_id})
        return len(cursor.fetchall())
