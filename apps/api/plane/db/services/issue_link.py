"""依赖关联服务（TASK-005 §4.3）——成对存储 / 可达性环检测 / advisory lock 串行化。

成对存储（§1.3）：一条业务事实落两行（正向 + 镜像），读取按 relation_type 还原
用户视角；环检测 CTE **只沿 blocks 正向边走**——沿镜像边同走会把每条边走两次且
方向恰好构成「往返」，任何图都会误报成环（§4.3.2 注，UT-03/04/05 锚定）。

并发正确性（§4.3.2 注）：READ COMMITTED 下 CTE 看不到并发事务未提交的新边，
两条长链并发合围会双双通过后落库成环——「查重 → 环检测 → 成对写入」临界区以
项目级 advisory lock 串行化（acquire_project_lock，与序列号生成同款锁空间）。
"""

from __future__ import annotations

import logging
import time
import uuid

from django.conf import settings as dj_settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from plane.db.models import Issue, IssueLink
from plane.db.services.issue_sequence import acquire_project_lock

logger = logging.getLogger(__name__)

#: 单任务关联上限（正反合并计数，BR-08；≥40 前端计数变 amber 预警）
MAX_LINKS_PER_ISSUE = 50


class RelationValidationError(Exception):
    """BR-01/BR-02 校验失败 → 400 VALIDATION_ERROR（details 携带字段）。"""

    def __init__(self, field: str, message: str):
        self.field = field
        self.message = message
        super().__init__(message)


class AlreadyExistsError(Exception):
    """BR-04 业务事实唯一性（含镜像方向）→ 409 RESOURCE_ALREADY_EXISTS。"""


class CircularDependencyError(Exception):
    """可达性环命中 → 409 RESOURCE_CIRCULAR_DEPENDENCY（依赖链人类可读）。"""


class DirtyDependencyGraphError(Exception):
    """保险丝深度内回到起点（环链 ≥101 边）= 脏数据 → 500 + ERROR 告警。"""


class TransitionBlockedError(Exception):
    """未完成前置拦截迁入 completed → 409 RESOURCE_TRANSITION_BLOCKED。"""

    def __init__(self, blockers: list[dict]):
        self.blockers = blockers
        super().__init__(f"存在 {len(blockers)} 个未完成前置任务")


# ─────────────────────────────────────────────────────────────────────
# 可达性环检测（§4.3.2）——回传命中深度而非布尔
# ─────────────────────────────────────────────────────────────────────
BLOCKS_REACH_SQL = """
    WITH RECURSIVE reachable(id, depth) AS (
        SELECT related_issue_id, 0 FROM issue_links
         WHERE issue_id = %(source)s AND relation_type = 'blocks' AND deleted_at IS NULL
        UNION ALL
        SELECT l.related_issue_id, r.depth + 1
          FROM issue_links l JOIN reachable r ON l.issue_id = r.id
         WHERE l.relation_type = 'blocks' AND l.deleted_at IS NULL
           AND r.depth < %(guard)s
    )
    SELECT COALESCE(MAX(depth), -1) FROM reachable WHERE id = %(target)s
"""


def _reaches(*, source: uuid.UUID, target: uuid.UUID) -> int:
    """source 沿 blocks 边是否可达 target：-1 不可达（含合法深链截断放行）；
    0 ≤ d < guard = 正常环（409）；d ≥ guard = 脏数据（500）。"""
    with connection.cursor() as cursor:
        cursor.execute(BLOCKS_REACH_SQL, {"source": source, "target": target, "guard": dj_settings.CTE_GUARD_DEPTH})
        return cursor.fetchone()[0]


def _render_chain(source: Issue, target: Issue) -> str:
    """依赖链人类可读渲染（409 details 直出；§4.2.2 例「依赖链：A → B → C」）。"""
    ids = {source.id: source, target.id: target}

    def label(iid):
        it = ids.get(iid) or Issue.objects.select_related("project").filter(id=iid).first()
        return f"{it.name} {it.project.identifier}-{it.sequence_id}" if it else str(iid)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            WITH RECURSIVE path(id, next_id, depth, chain) AS (
                SELECT related_issue_id, related_issue_id, 0,
                       ARRAY[%(src)s::uuid, related_issue_id]
                  FROM issue_links
                 WHERE issue_id = %(src)s AND relation_type = 'blocks' AND deleted_at IS NULL
                UNION ALL
                SELECT l.related_issue_id, l.related_issue_id, p.depth + 1,
                       p.chain || l.related_issue_id
                  FROM issue_links l JOIN path p ON l.issue_id = p.id
                 WHERE l.relation_type = 'blocks' AND l.deleted_at IS NULL
                   AND p.depth < %(guard)s
            )
            SELECT chain FROM path WHERE id = %(tgt)s ORDER BY depth LIMIT 1""",
            {"src": source.id, "tgt": target.id, "guard": dj_settings.CTE_GUARD_DEPTH},
        )
        row = cursor.fetchone()
    chain = list(row[0]) if row else [source.id, target.id]
    return "依赖链：" + " → ".join(label(i) for i in chain)


def _assert_link_budget(issue_id: uuid.UUID) -> None:
    n = IssueLink.objects.filter(Q(issue_id=issue_id) | Q(related_issue_id=issue_id), deleted_at__isnull=True).count()
    if n >= MAX_LINKS_PER_ISSUE:
        raise RelationValidationError("related_issue_id", f"单个任务的关联数已达上限 {MAX_LINKS_PER_ISSUE}")


# ─────────────────────────────────────────────────────────────────────
# 创建 / 删除（§4.3.1 / §4.3.4）——全系统唯一写入口（BR-03）
# ─────────────────────────────────────────────────────────────────────
@transaction.atomic
def create_relation(
    *,
    issue_id: uuid.UUID,
    related_issue_id: uuid.UUID,
    relation_type: str,
    actor_id: uuid.UUID,
    epoch: float | None = None,
) -> tuple[IssueLink, IssueLink]:
    """成对两行同事务。is_blocked_by 传入即交换两端归一化为 blocks 正向建模。

    返回 (正向行, 镜像行)；409/400 语义异常由视图层转 AppException。
    """
    from plane.bgtasks.issue_link import record_relation_change

    if issue_id == related_issue_id:  # BR-01
        raise RelationValidationError("related_issue_id", "不能与自身建立关联")
    issue = Issue.objects.select_related("project").filter(id=issue_id, deleted_at__isnull=True).first()
    related = Issue.objects.select_related("project").filter(id=related_issue_id, deleted_at__isnull=True).first()
    if issue is None or related is None or issue.project_id != related.project_id:  # BR-02
        raise RelationValidationError("related_issue_id", "关联双方必须属于同一项目")

    if relation_type == "is_blocked_by":                                    # 归一化为正向
        issue_id, related_issue_id = related_issue_id, issue_id
        issue, related = related, issue
        relation_type = "blocks"

    # BR-05 前置：项目级事务咨询锁串行化临界区（READ COMMITTED 下 CTE 的盲区，§4.3.2 注）
    acquire_project_lock(issue.project_id)

    # BR-04：正反两向查重（DB 约束仅兜底单方向三元组）
    dup = (
        IssueLink.objects.filter(deleted_at__isnull=True)
        .filter(
            Q(issue_id=issue_id, related_issue_id=related_issue_id)
            | Q(issue_id=related_issue_id, related_issue_id=issue_id)
        )
        .filter(relation_type__in=(relation_type, IssueLink.INVERSE_MAP[relation_type]))
        .first()
    )
    if dup is not None:
        raise AlreadyExistsError()

    _assert_link_budget(issue_id)
    _assert_link_budget(related_issue_id)

    if relation_type == "blocks":  # BR-05 防环
        hit = _reaches(source=related_issue_id, target=issue_id)
        if hit >= dj_settings.CTE_GUARD_DEPTH:
            logger.error("issue_link.dirty_graph source=%s target=%s depth=%d", related_issue_id, issue_id, hit)
            raise DirtyDependencyGraphError()
        if hit >= 0:
            raise CircularDependencyError(_render_chain(related, issue))

    epoch = epoch if epoch is not None else time.time() * 1000
    forward = IssueLink.objects.create(
        issue_id=issue_id, related_issue_id=related_issue_id, relation_type=relation_type, created_by_id=actor_id
    )
    mirror = IssueLink.objects.create(
        issue_id=related_issue_id,
        related_issue_id=issue_id,
        relation_type=IssueLink.INVERSE_MAP[relation_type],
        created_by_id=actor_id,
    )
    transaction.on_commit(lambda: record_relation_change.delay(str(forward.id), str(mirror.id), str(actor_id), epoch))
    return forward, mirror


@transaction.atomic
def delete_relation(*, link_id: uuid.UUID, actor_id: uuid.UUID, epoch: float | None = None) -> IssueLink | None:
    """正反两行同事务软删；镜像缺失容错（历史脏数据不阻断）。返回正向行。"""
    from plane.bgtasks.issue_link import record_relation_change

    link = IssueLink.objects.select_for_update().filter(id=link_id, deleted_at__isnull=True).first()
    if link is None:
        return None
    mirror = (
        IssueLink.objects.select_for_update()
        .filter(
            issue_id=link.related_issue_id,
            related_issue_id=link.issue_id,
            relation_type=IssueLink.INVERSE_MAP[link.relation_type],
            deleted_at__isnull=True,
        )
        .first()
    )
    now = timezone.now()
    link.deleted_at = now
    link.save(update_fields=["deleted_at"])
    if mirror:
        mirror.deleted_at = now
        mirror.save(update_fields=["deleted_at"])
    epoch = epoch if epoch is not None else time.time() * 1000
    transaction.on_commit(
        lambda: record_relation_change.delay(str(link.id), str(mirror.id) if mirror else None, str(actor_id), epoch)
    )
    return link


def relations_of(issue_id: uuid.UUID) -> list[dict]:
    """某任务全部关联的序列化（GET relations/，契约冻结供 GANTT-001）。

    内联 related_issue（id/issue_key/name/state_id/state_group/start_date/
    target_date——甘特连线必需字段）；创建时间倒序；data[] 恒数组（50 上限天然有界，
    api-conventions §6 分页豁免）。
    """
    rows = (
        IssueLink.objects.filter(issue_id=issue_id, deleted_at__isnull=True)
        .select_related("related_issue", "related_issue__state", "related_issue__project")
        .order_by("-created_at")
    )
    out = []
    for link in rows:
        r = link.related_issue
        out.append(
            {
                "id": str(link.id),
                "issue_id": str(issue_id),
                "related_issue_id": str(r.id),
                "relation_type": link.relation_type,
                "is_blocking": link.relation_type in ("blocks", "is_blocked_by"),
                "related_issue": {
                    "id": str(r.id),
                    "issue_key": f"{r.project.identifier}-{r.sequence_id}",
                    "name": r.name,
                    "state_id": str(r.state_id) if r.state_id else None,
                    "state_group": (r.state.group if r.state else None) or "unstarted",
                    "start_date": r.start_date,
                    "target_date": r.target_date,
                },
            }
        )
    return out
