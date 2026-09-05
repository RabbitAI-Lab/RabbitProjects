"""层级服务（TASK-004 §4.3）——递归 CTE 防环 / 深度校验 / 子树查询 / 级联软删。

三层防线（§1.3，互不替代）：
  1. 写入层业务深度 ≤ MAX_ISSUE_DEPTH（创建与移动，含被移子树高度整体校验）；
  2. 防环 CTE（上行祖先链：挂到自身后代 → 409 CYCLE，环路径人类可读）；
  3. 查询侧 CTE_GUARD_DEPTH 保险丝——脏数据告警线，触达即 500 + logger.error，
     绝不静默截断后错算（§4.3.1 注：被截断的链会让环判定与深度计算双双漏判）。

行锁语义（BR-10，§4.3.2）：「锁行 → 校验」的顺序使两个「互换父子」请求被串行化，
后到者校验时能看到前者已提交的 parent_id，环被正确拒绝——校验必须发生在
select_for_update 之后。
"""

from __future__ import annotations

import logging
import time
import uuid

from django.conf import settings as dj_settings
from django.db import connection, transaction
from django.db.models import Count, Q
from django.utils import timezone

from plane.db.models import Issue

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# 业务异常（view 层捕获后转 AppException；错误码 / 子码见 §2.6 异常表）
# ─────────────────────────────────────────────────────────────────────
class CircularDependencyError(Exception):
    """挂到自身后代之下 → 409 RESOURCE_CIRCULAR_DEPENDENCY（携带环路径）。"""

    def __init__(self, path: str):
        self.path = path
        super().__init__(path)


class DepthLimitExceeded(Exception):
    """业务深度超 MAX_ISSUE_DEPTH → 409 RESOURCE_LIMIT_EXCEEDED（子码 DEPTH）。"""

    def __init__(self, current_depth: int, limit: int):
        self.current_depth = current_depth
        self.limit = limit
        super().__init__(f"层级已达 {limit} 层上限")


class SubtreeDepthGuardError(Exception):
    """CTE 保险丝触达（链长/子树深 ≥ CTE_GUARD_DEPTH = 脏数据）→ 500 + 告警。"""


class StateInvalidError(Exception):
    """挂到已归档父（BR-13）→ 409 RESOURCE_STATE_INVALID（子码 STATE）。"""


# ─────────────────────────────────────────────────────────────────────
# 列表计数过滤器（§4.3.5：TASK-002 表达式 + archived_at 增量，全站唯一来源）
# ─────────────────────────────────────────────────────────────────────
SUBTREE_COUNT_FILTER = Q(sub_issues__deleted_at__isnull=True, sub_issues__archived_at__isnull=True) & ~Q(
    sub_issues__state__group="cancelled"
)
COMPLETED_COUNT_FILTER = Q(
    sub_issues__deleted_at__isnull=True,
    sub_issues__archived_at__isnull=True,
    sub_issues__state__group="completed",
)


def issue_count_annotations() -> dict:
    """sub_issues_count / completed_sub_issues_count 的统一 annotate 装配。

    distinct=True 防与其他 JOIN（assignees 预取）笛卡尔放大；cancelled 既不在
    分子也不在分母（BR-05「有效子任务完成率」）。所有视图（列表 / group /
    详情 / sub-issues）必须经此装配，禁止各处手写表达式（口径漂移源头）。
    """
    return {
        "sub_issues_count": Count("sub_issues", filter=SUBTREE_COUNT_FILTER, distinct=True),
        "completed_sub_issues_count": Count("sub_issues", filter=COMPLETED_COUNT_FILTER, distinct=True),
    }


# ─────────────────────────────────────────────────────────────────────
# 上行：祖先链 / 深度 / 防环（§4.3.1）
# ─────────────────────────────────────────────────────────────────────
def _ancestor_chain(issue_id: uuid.UUID) -> list:
    """祖先链（含节点自身，自身 depth=0），一次上行 CTE。

    保险丝在 SELECT 之后判定而非 SQL 侧静默截断：链长触达 CTE_GUARD_DEPTH
    即脏数据（人为改库成环），抛 SubtreeDepthGuardError 快速失败并告警。
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            WITH RECURSIVE chain(id, parent_id, depth) AS (
                SELECT id, parent_id, 0 FROM issues WHERE id = %(start)s
                UNION ALL
                SELECT i.id, i.parent_id, c.depth + 1
                  FROM issues i JOIN chain c ON i.id = c.parent_id
                 WHERE c.depth < %(guard)s
            )
            SELECT id FROM chain ORDER BY depth DESC""",
            {"start": issue_id, "guard": dj_settings.CTE_GUARD_DEPTH},
        )
        chain = [row[0] for row in cursor.fetchall()]
    if len(chain) >= dj_settings.CTE_GUARD_DEPTH:
        logger.error(
            "issue_hierarchy.guard_triggered op=ancestor_chain issue_id=%s guard=%d",
            issue_id,
            dj_settings.CTE_GUARD_DEPTH,
        )
        raise SubtreeDepthGuardError(f"祖先链长度触达保险丝 {dj_settings.CTE_GUARD_DEPTH}，疑似环状脏数据")
    return chain


def depth_of(issue_id: uuid.UUID) -> int:
    """节点业务深度（根=1）= 祖先链长度（链首即自身，UT 对照 §4.3.1）。"""
    return len(_ancestor_chain(issue_id))


def _is_descendant(candidate_id: uuid.UUID, of_id: uuid.UUID) -> bool:
    """candidate 是否为 of 的后代（任意深度）——复用上行链，先判等短路覆盖自引用。"""
    if candidate_id == of_id:
        return True
    return of_id in _ancestor_chain(candidate_id)


def _render_cycle(issue: Issue, parent: Issue) -> str:
    """环路径人类可读渲染：目标父上行链 + 被移节点首尾相接（409 details 直出）。"""
    names = {issue.id: f"{issue.name} {issue.project.identifier}-{issue.sequence_id}"}
    chain_ids = _ancestor_chain(parent.id)
    for iid in chain_ids:
        row = Issue.objects.filter(id=iid).values_list("name", "sequence_id", "project__identifier").first()
        if row:
            names[iid] = f"{row[0]} {row[2]}-{row[1]}"
    segs = [names.get(iid, str(iid)) for iid in chain_ids]
    return "环路径：" + " → ".join([names[issue.id], *segs])


# ─────────────────────────────────────────────────────────────────────
# 下行：子树高度 / 移动校验（§4.3.2）
# ─────────────────────────────────────────────────────────────────────
def _subtree_height(issue_id: uuid.UUID) -> int:
    """被移子树高度（子树根自身=1）——下行 CTE max(depth)+1。

    不做 archived_at 过滤：校验面向全量数据，已归档后代同样不能越限落地。
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            WITH RECURSIVE subtree(id, depth) AS (
                SELECT id, 0 FROM issues WHERE id = %(root)s
                UNION ALL
                SELECT i.id, st.depth + 1 FROM issues i
                  JOIN subtree st ON i.parent_id = st.id
                 WHERE st.depth < %(guard)s
            )
            SELECT max(depth) FROM subtree""",
            {"root": issue_id, "guard": dj_settings.CTE_GUARD_DEPTH},
        )
        max_depth = cursor.fetchone()[0] or 0
    if max_depth >= dj_settings.CTE_GUARD_DEPTH:
        logger.error(
            "issue_hierarchy.guard_triggered op=subtree_height issue_id=%s guard=%d",
            issue_id,
            dj_settings.CTE_GUARD_DEPTH,
        )
        raise SubtreeDepthGuardError(f"子树深度触达保险丝 {dj_settings.CTE_GUARD_DEPTH}，疑似环状脏数据")
    return max_depth + 1


def check_move(locked_issue: Issue, new_parent_id: uuid.UUID | None) -> None:
    """在 select_for_update 行锁内做移动全部校验（BR-01/02/03/13）。

    仅校验不落库——赋值与保存由调用方（PATCH 组合请求）统一处理，保证与其他
    字段变更同一事务；独立的移动语义（校验+保存+投递）见 move_subtree。
    """
    if new_parent_id is None:
        return  # 摘出为顶层：无深度/环/归档约束
    if new_parent_id == locked_issue.id:
        raise CircularDependencyError(f"环路径：{locked_issue.name}（不能移动到自己之下）")
    parent = Issue.objects.select_related("project").filter(id=new_parent_id, deleted_at__isnull=True).first()
    if parent is None or parent.project_id != locked_issue.project_id:  # BR-01
        raise ValueError("父子工作项必须属于同一项目")
    if parent.archived_at is not None:  # BR-13
        raise StateInvalidError("目标任务已归档，恢复后才能挂子任务")
    if _is_descendant(candidate_id=parent.id, of_id=locked_issue.id):  # BR-03
        raise CircularDependencyError(_render_cycle(locked_issue, parent))
    # BR-02：整体校验被移子树——depth(新父)+1 = 被移根新深度，+height-1 = 最深节点新深度
    new_deepest = depth_of(parent.id) + 1 + _subtree_height(locked_issue.id) - 1
    if new_deepest > dj_settings.MAX_ISSUE_DEPTH:
        raise DepthLimitExceeded(new_deepest, dj_settings.MAX_ISSUE_DEPTH)


def move_subtree(issue_id: uuid.UUID, new_parent_id: uuid.UUID | None, actor_id: uuid.UUID) -> Issue:
    """独立移动入口（行锁 + 全校验 + 单行更新 + on_commit 投递；§4.3.2）。"""
    from plane.bgtasks.issue_hierarchy import record_parent_change

    epoch = time.time() * 1000  # TASK-010 BR-04：epoch 在动作入口生成（毫秒）
    with transaction.atomic():
        issue = Issue.objects.select_for_update().select_related("project").get(id=issue_id)
        check_move(issue, new_parent_id)
        old_parent_id = issue.parent_id
        issue.parent_id = new_parent_id
        issue.updated_by_id = actor_id
        issue.save(update_fields=["parent", "updated_by", "updated_at"])
    transaction.on_commit(
        lambda: record_parent_change.delay(
            str(issue_id), str(actor_id), epoch, str(old_parent_id or ""), str(new_parent_id or "")
        )
    )
    return issue


# ─────────────────────────────────────────────────────────────────────
# 子树展示查询（§4.3.3）
# ─────────────────────────────────────────────────────────────────────
SUBTREE_SQL = """
    WITH RECURSIVE subtree(id, parent_id, sequence_id, name, state_group, assignees, depth) AS (
        SELECT i.id, i.parent_id, i.sequence_id, i.name, s."group",
               (SELECT array_agg(a.assignee_id) FROM issue_assignees a
                 WHERE a.issue_id = i.id), 0
          FROM issues i LEFT JOIN states s ON s.id = i.state_id
         WHERE i.id = %(root)s AND i.deleted_at IS NULL AND i.archived_at IS NULL
        UNION ALL
        SELECT i.id, i.parent_id, i.sequence_id, i.name, s."group",
               (SELECT array_agg(a.assignee_id) FROM issue_assignees a
                 WHERE a.issue_id = i.id), st.depth + 1
          FROM issues i
          JOIN subtree st ON i.parent_id = st.id
          LEFT JOIN states s ON s.id = i.state_id
         WHERE i.deleted_at IS NULL
           AND i.archived_at IS NULL            -- BR-09：归档树整体不可见
           AND st.depth < %(guard)s             -- 保险丝：查询侧静默截断防无限递归
    )
    SELECT * FROM subtree ORDER BY depth, sequence_id LIMIT %(fetch_limit)s
"""
# 截断在外层 SELECT 判定——PostgreSQL 递归项内不支持 LIMIT，故取 limit+1 行探测。


def fetch_subtree(root_id: uuid.UUID) -> dict:
    """整棵子树：root 单列 + nodes 平铺（相对根 depth，根=0）+ stats（含根口径）。

    truncated=true 时 nodes 恰 node_limit 条且不装配 stats（不完整数据不出统计）。
    """
    limit = dj_settings.SUBTREE_NODE_LIMIT
    with connection.cursor() as cursor:
        cursor.execute(SUBTREE_SQL, {"root": root_id, "guard": dj_settings.CTE_GUARD_DEPTH, "fetch_limit": limit + 1})
        rows = cursor.fetchall()
    truncated = len(rows) > limit
    rows = rows[:limit]

    key = _issue_key_of(root_id)
    direct = [r for r in rows[1:] if r[1] == root_id]
    direct_done = [r for r in direct if r[4] == "completed" or r[4] == "cancelled"]

    def _node(row, *, is_root=False):
        out = {
            "id": str(row[0]),
            "parent_id": str(row[1]) if row[1] else None,
            "issue_key": key if is_root else _issue_key_of(row[0]),
            "sequence_id": row[2],
            "name": row[3],
            "state_group": row[4],
            "assignee_ids": [str(x) for x in (row[5] or [])],
            "depth": row[6],
        }
        if is_root:
            out["sub_issues_count"] = len(direct)
            out["completed_sub_issues_count"] = len(direct_done)
        return out

    root = _node(rows[0], is_root=True) if rows else None
    data = {"root": root, "nodes": [_node(r) for r in rows[1:]]}
    if not truncated and rows:
        groups = [r[4] for r in rows]
        data["stats"] = {
            "total": len(rows),
            "completed": sum(1 for g in groups if g == "completed"),
            "cancelled": sum(1 for g in groups if g == "cancelled"),
            "max_depth": max(r[6] for r in rows),
        }
    return data


def _issue_key_of(root_id: uuid.UUID) -> str | None:
    row = Issue.objects.filter(id=root_id).values_list("project__identifier", "sequence_id").first()
    return f"{row[0]}-{row[1]}" if row else None


# ─────────────────────────────────────────────────────────────────────
# 级联软删（§4.3.4）
# ─────────────────────────────────────────────────────────────────────
@transaction.atomic
def delete_subtree(issue_id: uuid.UUID, actor_id: uuid.UUID) -> dict:
    """整树软删 + 中间表物理删除（BR-06/BR-15），回传受影响数。

    级联必须包含已归档后代（否则残留指向软删父的孤儿）；archived_at 过滤只
    用于可见性查询。附件由 FILE-001 purge_deleted_assets 延迟回收（30 天窗）。
    """
    from plane.bgtasks.issue_hierarchy import record_delete

    now = timezone.now()
    epoch = time.time() * 1000
    with connection.cursor() as cursor:
        cursor.execute(
            """
            WITH RECURSIVE target AS (
                SELECT id FROM issues WHERE id = %(root)s AND deleted_at IS NULL
                UNION ALL
                SELECT i.id FROM issues i JOIN target t ON i.parent_id = t.id
                 WHERE i.deleted_at IS NULL
            ),
            marked AS (
                UPDATE issues SET deleted_at = %(now)s, updated_by_id = %(actor)s
                 WHERE id IN (SELECT id FROM target) RETURNING id
            ),
            purged_assignees AS (
                DELETE FROM issue_assignees
                 WHERE issue_id IN (SELECT id FROM target) RETURNING 1
            ),
            purged_labels AS (
                DELETE FROM issue_labels
                 WHERE issue_id IN (SELECT id FROM target) RETURNING 1
            ),
            purged_links AS (
                UPDATE issue_links SET deleted_at = %(now)s
                 WHERE (issue_id IN (SELECT id FROM target)
                        OR related_issue_id IN (SELECT id FROM target))
                   AND deleted_at IS NULL RETURNING 1
            )
            SELECT (SELECT count(*) FROM marked),
                   (SELECT array_agg(id) FROM marked)""",
            {"root": issue_id, "now": now, "actor": actor_id},
        )
        deleted_count, ids = cursor.fetchone()
    transaction.on_commit(lambda: record_delete.delay(str(issue_id), str(actor_id), int(deleted_count), epoch))
    return {"deleted_count": int(deleted_count), "descendant_ids": [str(x) for x in (ids or [])[1:]]}
