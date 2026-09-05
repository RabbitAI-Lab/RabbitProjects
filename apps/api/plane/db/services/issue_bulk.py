"""任务批量操作服务（BOARD-004 §4.3）——单事务全成败 + 逐条同源 + 单次投递。

批量是「N 次正规单条写的事务化打包」，不是新的写语义（§1.1 一句话边界）。
本模块只做三件事：**打包事务、共享 epoch、聚合响应**——每条的权限、校验、
守卫（流转拦截 / 归档保护）与单条操作完全同源（BR-03）：

- 状态 / 优先级 / 指派 / 标签：逐条复用单条 PATCH 同源写（``assert_completable``
  流转守卫 / ``sync_assignees_full`` 唯一写入口 / ``sync_labels`` 集合替换）；
- 归档：逐条走 ``archive_subtree``（整树级联 + 幂等，TASK-009）；
- 删除：逐条走 ``delete_subtree``（软删 + 中间表物理删除级联，TASK-004 唯一归属）。

事务纪律（api-conventions §10.5 / BR-01~05）：
- BR-01 上限 100（去重后计数，超限整体拒绝不静默截断）；
- BR-02 单事务全成败：任一条失败整批回滚，失败清单项级三键
  ``{field: "issue_ids[<0基索引>]", code, message 含 issue_key}``，
  **收集全部坏项后统一抛**（不首错即抛）；
- BR-04 同批一次性 ``select_for_update(nowait=True)`` 先锁后判；任一行被并发
  事务持有 → 409 RESOURCE_CONFLICT 快速失败不排队（OperationalError 捕获收窄转译）；
- BR-05 同批共享 epoch（入口 ``time.time() * 1000``，batch_id 同值）；逐条调用
  ``delete_subtree`` / ``archive_subtree`` 传 epoch + ``suppress_activity=True``
  （ADR-0017 兑现），出口统一 on_commit 单次投递 batch 载荷（comment 前缀
  ``batch:``），否则每任务落两份 Activity 且 epoch 分裂。

WS 扇出（BR-15）归 COLLAB-004（T3-09~11）：本模块出口的 batch 载荷即其
逐实体 ``issue.updated`` / ``issue.state.changed`` 事件的预留挂点（batch_id = epoch）。
"""

from __future__ import annotations

import logging
import time
import uuid

from django.conf import settings as dj_settings
from django.db import OperationalError, connection, transaction
from django.db.models import Q

from plane.base.exception import AppException
from plane.db.models import Issue, IssueAssignee, IssueComment, IssueLabel, IssueLink, WorkLog
from plane.db.services.issue_archive import archive_subtree
from plane.db.services.issue_assignee import sync_assignees_full
from plane.db.services.issue_hierarchy import delete_subtree
from plane.db.services.issue_link import TransitionBlockedError
from plane.db.services.issue_transition_guard import assert_completable
from plane.utils.exceptions import AppValidationError, field_error

logger = logging.getLogger(__name__)

#: BR-01 / §2.6：单批上限（去重后计数）
BULK_LIMIT = 100
#: BR-12 / §2.6：批量备注上限（随 Activity 落库）
MAX_BULK_COMMENT = 500

#: 集合运算三模式（§4.3.2）
SET_MODES = ("replace", "add", "remove")


# ─────────────────────────────────────────────────────────────────────
# 异常
# ─────────────────────────────────────────────────────────────────────
class BulkLimitExceeded(AppException):
    """BR-01：去重后条数 > 100 → 400 VALIDATION_BULK_LIMIT_EXCEEDED（整批拒绝）。"""

    def __init__(self, got: int, limit: int = BULK_LIMIT):
        super().__init__(
            "VALIDATION_BULK_LIMIT_EXCEEDED",
            message=f"批量操作一次最多 {limit} 条",
            details=[{"field": "issue_ids", "code": "LIMIT", "message": f"提交 {got} 条"}],
        )


class BulkActionError(Exception):
    """项级失败清单 → 视图层转 400 VALIDATION_ERROR（整批已回滚）。

    failures 逐项三键（BR-02，§2.5）：``field=issue_ids[<0基索引>]``、
    ``code`` 为守卫子码（DOES_NOT_EXIST / BLOCKED_BY / PERM_DENIED / LIMIT /
    STATE / TOO_LARGE / …）、``message`` 含 issue_key 与原因。
    """

    def __init__(self, failures: list[dict], batch_size: int):
        self.failures = failures
        self.batch_size = batch_size
        super().__init__(f"{len(failures)} 项未通过校验")


# ─────────────────────────────────────────────────────────────────────
# 支撑件
# ─────────────────────────────────────────────────────────────────────
def dedupe_ids(issue_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    """BR-07：去重保序（执行序 = 首现序）。"""
    return list(dict.fromkeys(issue_ids))


def _fail(index: int, code: str, message: str) -> dict:
    """项级失败条目（field 承载 0 基请求内索引）。"""
    return {"field": f"issue_ids[{index}]", "code": code, "message": message}


def _issue_key(project, issue: Issue) -> str:
    return f"{project.identifier}-{issue.sequence_id}"


def _subcode_of(exc: Exception) -> str:
    """AppException / AppValidationError → details[0].code 子码（缺省 INVALID）。"""
    details = getattr(exc, "extra_details", None) or []
    if details and isinstance(details[0], dict):
        return str(details[0].get("code") or "INVALID")
    return "INVALID"


def _assert_bulk_limit(ids: list[uuid.UUID]) -> None:
    if len(ids) > BULK_LIMIT:  # BR-01（服务端双保险；视图层已预拦截）
        raise BulkLimitExceeded(len(ids))


def _lock_batch(project_id: uuid.UUID, ids: list[uuid.UUID]) -> dict[uuid.UUID, Issue]:
    """BR-04：一次锁全批（先锁后判；不 select_related——FOR UPDATE 带 JOIN 会连表锁行）。

    nowait：任一行被并发事务持有 → OperationalError 收窄转译 409 RESOURCE_CONFLICT
    快速失败（不排队，杜绝两批互等的长事务）；非锁冲突的 OperationalError 原样
    上抛（连接级问题 → 500）。
    """
    try:
        rows = list(
            Issue.objects.select_for_update(nowait=True)
            .filter(id__in=ids, project_id=project_id, deleted_at__isnull=True)
        )
    except OperationalError as exc:
        if "could not obtain lock" in str(exc):
            raise AppException(
                "RESOURCE_CONFLICT",
                message="部分任务正被其他操作修改，请稍后重试",
                details=[
                    {
                        "field": "issue_ids",
                        "code": "LOCKED",
                        "message": "目标行被并发事务持有（nowait 即刻失败，BR-04）",
                    }
                ],
            ) from None
        raise
    return {r.id: r for r in rows}


#: 多根子树展开（只读）：仅排除软删、不排除已归档——与 delete_subtree /
#: archive_subtree 的目标集 CTE 同口径（级联必须含已归档后代，TASK-004 §4.3.4）。
SUBTREE_MULTIROOT_SQL = """
    WITH RECURSIVE subtree(id, depth) AS (
        SELECT id, 0 FROM issues WHERE id = ANY(%(roots)s) AND deleted_at IS NULL
        UNION ALL
        SELECT i.id, st.depth + 1 FROM issues i
          JOIN subtree st ON i.parent_id = st.id
         WHERE i.deleted_at IS NULL AND st.depth < %(guard)s
    )
    SELECT id FROM subtree
"""


def _subtree_ids(root_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """选中集 + 级联子树总 id 集（preview 与 affected_total 统一口径）。"""
    if not root_ids:
        return set()
    with connection.cursor() as cursor:
        cursor.execute(
            SUBTREE_MULTIROOT_SQL, {"roots": list(root_ids), "guard": dj_settings.CTE_GUARD_DEPTH}
        )
        return {row[0] for row in cursor.fetchall()}


def _can_admin_or_own(role: int, issue: Issue, actor_id: uuid.UUID, *, admin_level: int) -> bool:
    """R1 对象级：ADMIN 任意 / 其余仅本人创建（rbac §5.3 destroy 与 issue.archive 交付口径）。"""
    return role >= admin_level or issue.created_by_id == actor_id


def _enqueue_batch(rows: list[dict], comment: str) -> None:
    """BR-05：出口统一 on_commit 单次投递 batch 载荷（本函数在事务内调用）。"""
    from plane.bgtasks.issue_activity import enqueue_activity_rows

    transaction.on_commit(lambda: enqueue_activity_rows(rows=rows, comment=comment))


def _batch_comment(comment: str, summary: str) -> str:
    """批量摘要（BR-05 / §4.1：comment 前缀 ``batch:`` 即聚合依据）。"""
    text = comment.strip() or summary
    return f"batch: {text}"[:1000]


# ─────────────────────────────────────────────────────────────────────
# 动作 1-4：批量更新（PATCH）
# ─────────────────────────────────────────────────────────────────────
def _apply_assignees_set_op(
    issue: Issue, spec: dict, actor_id: uuid.UUID
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """§4.3.2：add/remove/replace 三模式收敛到 sync_assignees 唯一入口（BR-03）。

    返回 (before_ids, after_ids)（供 Activity 摘要行）；≤10 / 成员校验在入口内。
    """
    mode, incoming = spec["mode"], list(spec["assignee_ids"])
    current = list(
        IssueAssignee.objects.filter(issue_id=issue.id)
        .order_by("created_at", "id")
        .values_list("assignee_id", flat=True)
    )
    if mode == "replace":  # PUT 语义
        target = incoming
    elif mode == "add":  # 并集：保 current 序，新者按 incoming 序追加
        target = current + [u for u in incoming if u not in current]
    else:  # remove：差集
        drop = set(incoming)
        target = [u for u in current if u not in drop]
    sync_assignees_full(issue_id=issue.id, new_ids=target, actor_id=actor_id)
    return current, target


def _apply_labels_set_op(issue: Issue, spec: dict, actor_id: uuid.UUID) -> tuple[list, list]:
    """标签三模式（与指派同构）：差分写——移除行物理删除 + 新增行 bulk_create。

    与单条 PUT 的 sync_labels 同语义（全量替换外观），但不直接复用其
    「软删全部 + 重建」实现：``uniq_issue_label`` 是全量唯一约束（无
    ``deleted_at`` 偏条件），集合重叠（add 模式恒重叠 / replace 保留交集）时
    重建必撞约束——批量按 TASK-007 中间表物理删除同款口径（BR-11）做精确
    差分：仅删移除行、仅建新增行，幂等且零多余写。
    """
    mode, incoming = spec["mode"], list(spec["label_ids"])
    current = list(IssueLabel.objects.filter(issue_id=issue.id).values_list("label_id", flat=True))
    if mode == "replace":
        target = incoming
    elif mode == "add":
        target = current + [u for u in incoming if u not in current]
    else:
        drop = set(incoming)
        target = [u for u in current if u not in drop]
    removed = [u for u in current if u not in set(target)]
    added = [u for u in target if u not in set(current)]
    if removed:
        IssueLabel.objects.filter(issue_id=issue.id, label_id__in=removed).delete(soft=False)  # type: ignore[call-arg]
    if added:
        IssueLabel.objects.bulk_create(
            [IssueLabel(issue_id=issue.id, label_id=lid, created_by_id=actor_id) for lid in added]
        )
    return current, target


def bulk_update(
    *,
    project,
    actor,
    issue_ids: list[uuid.UUID],
    patch: dict | None = None,
    assignees: dict | None = None,
    labels: dict | None = None,
    label_names: dict[uuid.UUID, str] | None = None,
    comment: str = "",
    is_admin: bool = False,
) -> dict:
    """动作 1-4：单事务逐条同源写；任一失败整批回滚（BR-02）。

    载荷级校验（枚举 / state 归属 / 标签归属）已在视图层完成；本层只做
    逐条守卫（流转拦截 / 归档保护 / 成员合法 / ≤10）。
    返回 ``{"data": {...}, "meta": {...}}``（§4.2.1 契约）。
    """
    ids = dedupe_ids(issue_ids)
    _assert_bulk_limit(ids)
    if patch:
        action = "state" if "state_id" in patch else "priority"
    elif assignees:
        action = "assignees"
    else:
        action = "labels"
    epoch = time.time() * 1000  # BR-05：批次唯一 epoch（入口单点生成）
    batch_comment = _batch_comment(comment, f"批量更新 {len(ids)} 个任务")
    failures: list[dict] = []
    activity_rows: list[dict] = []
    label_names = label_names or {}

    with transaction.atomic():
        locked = _lock_batch(project.id, ids)  # BR-04：先锁后判
        from plane.db.services.issue_archive import assert_issue_writable

        for index, iid in enumerate(ids):
            issue = locked.get(iid)
            if issue is None:  # BR-07：项级不可见（含跨项目 id → 不 404 整批）
                failures.append(_fail(index, "DOES_NOT_EXIST", "任务不存在或不可见"))
                continue
            issue.project = project  # 内存装配（守卫与编号渲染用；不落库）
            key = _issue_key(project, issue)
            rows: list[dict] = []
            try:
                assert_issue_writable(issue)  # 单条同源：归档任务只读（§4.3.3）
                if patch and "state_id" in patch:
                    rows += _update_state(issue, patch["state"], actor, epoch, is_admin=is_admin)
                if patch and "priority" in patch:
                    before_p, after_p = issue.priority, patch["priority"]
                    issue.priority = after_p
                    if before_p != after_p:
                        rows.append(
                            _row(issue, actor, epoch, field="priority", old=before_p,
                                 new=after_p, comment="更新了 优先级")
                        )
                if assignees:
                    before_a, after_a = _apply_assignees_set_op(issue, assignees, actor.id)
                    if set(before_a) != set(after_a):
                        rows.append(
                            _row(issue, actor, epoch, field="assignees",
                                 old=",".join(str(u) for u in sorted(before_a, key=str)) or "previous",
                                 new=",".join(str(u) for u in sorted(after_a, key=str)),
                                 comment="更新了 负责人")
                        )
                if labels:
                    from plane.app.serializers.issue import MAX_LABELS_PER_ISSUE

                    before_l, after_l = _apply_labels_set_op(issue, labels, actor.id)
                    if len(after_l) > MAX_LABELS_PER_ISSUE:  # 集合运算结果上限（逐条）
                        raise AppException(
                            "VALIDATION_ERROR",
                            message=f"标签最多 {MAX_LABELS_PER_ISSUE} 个",
                            details=[field_error("labels.label_ids", "TOO_LARGE",
                                                 f"集合运算后 {len(after_l)} 个超过上限")],
                        )
                    rows += _label_rows(issue, actor, epoch, before_l, after_l, label_names)
                issue.updated_by_id = actor.id
                issue.save()  # completed_at 派生随行（Issue.save 钩子）
            except TransitionBlockedError as e:  # BR-11：流转守卫逐条（含阻塞源）
                blockers = "、".join(f"{b['issue_key']} {b['name']}" for b in e.blockers)
                failures.append(_fail(index, "BLOCKED_BY", f"{key}：前置任务未完成（{blockers}）"))
                continue
            except (AppException, AppValidationError) as e:
                # 项级业务失败（权限/校验/守卫子码同源透传；AppValidationError 为
                # sync_assignees_full 入口的多字段校验异常，同样收敛为项级）
                details = getattr(e, "extra_details", None) or [{}]
                inner = details[0].get("message") if isinstance(details[0], dict) else None
                outer = getattr(e, "detail_message", None) or str(e)
                failures.append(_fail(index, _subcode_of(e), f"{key} {inner or outer}"))
                continue
            if rows:
                rows[0]["comment"] = batch_comment  # 每任务首行落批量摘要（BR-05）
                activity_rows.extend(rows)
        if failures:  # BR-02：收集全部坏项后统一抛（整批回滚）
            raise BulkActionError(failures, len(ids))
        _enqueue_batch(activity_rows, batch_comment)  # BR-05：单次投递
    return {
        "data": {
            "updated": len(ids),
            "epoch": epoch,
            "action": action,
            "comment": comment or "",
        },
        "meta": {"batch_size": len(ids)},
    }


def _row(issue: Issue, actor, epoch: float, *, field: str, comment: str,
         old=None, new=None, old_identifier=None, new_identifier=None,
         verb: str = "updated") -> dict:
    """record_activity_row 同款单行 kwargs（batch 载荷行）。"""
    return {
        "issue_id": str(issue.id),
        "actor_id": str(actor.id),
        "verb": verb,
        "epoch": epoch,
        "field": field,
        "old_value": None if old is None else str(old),
        "new_value": None if new is None else str(new),
        "old_identifier": None if old_identifier is None else str(old_identifier),
        "new_identifier": None if new_identifier is None else str(new_identifier),
        "comment": comment,
    }


def _update_state(issue: Issue, new_state, actor, epoch: float, *, is_admin: bool) -> list[dict]:
    """状态变更：流转守卫逐条（TASK-005 同源；批量无 force 通道）+ completed_at 随行。

    同态重设（old == new）不产生 diff 行（单条 PATCH 同款判定）。
    """
    old_state = issue.state
    old_group = old_state.group if old_state else None
    if new_state.group == "completed" and old_group != "completed":
        assert_completable(issue=issue, to_state=new_state, force=False, is_admin=is_admin)
    issue.state = new_state
    if old_state is not None and str(old_state.id) == str(new_state.id):
        return []
    return [
        _row(issue, actor, epoch, field="state",
             old=old_state.name if old_state else None, new=new_state.name,
             old_identifier=old_state.id if old_state else None,
             new_identifier=new_state.id, comment="更新了 状态")
    ]


def _label_rows(issue: Issue, actor, epoch: float, before_l: list, after_l: list,
                label_names: dict) -> list[dict]:
    """标签 diff 行（单条 PUT 同款：添加/移除逐标签一行）。epoch 由调用方覆写。"""
    before_set, after_set = set(before_l), set(after_l)
    rows = []
    for lid in after_set - before_set:
        rows.append(_row(issue, actor, epoch, field="labels",
                         new_identifier=lid, new=label_names.get(lid),
                         comment=f"添加了 标签 {label_names.get(lid, '')}"))
    for lid in before_set - after_set:
        rows.append(_row(issue, actor, epoch, field="labels",
                         old_identifier=lid, old=label_names.get(lid),
                         comment=f"移除了 标签 {label_names.get(lid, '')}"))
    return rows


# ─────────────────────────────────────────────────────────────────────
# 动作 5：批量归档（POST bulk/archive/）
# ─────────────────────────────────────────────────────────────────────
def bulk_archive(*, project, actor, issue_ids: list[uuid.UUID], comment: str = "",
                 role: int = 0) -> dict:
    """BR-09：逐条走 archive_subtree（整树级联）；同批父子同选幂等（仅 NULL 行被触及）。

    计数口径（§4.2.2）：archived_count = 本批实际新置 archived_at 行数（幂等重提交
    不重复计）；affected_total = 选中 + 级联子树总行数（含已归档行，与 preview 一致）。
    """
    from plane.db.models.roles import ProjectRole

    ids = dedupe_ids(issue_ids)
    _assert_bulk_limit(ids)
    epoch = time.time() * 1000
    batch_comment = _batch_comment(comment, f"批量归档 {len(ids)} 个任务")
    failures: list[dict] = []
    activity_rows: list[dict] = []
    archived_count = 0
    with transaction.atomic():
        locked = _lock_batch(project.id, ids)  # BR-04
        affected = _subtree_ids([i for i in ids if i in locked])  # 可见集快照（含已归档行）
        for index, iid in enumerate(ids):
            issue = locked.get(iid)
            if issue is None:
                failures.append(_fail(index, "DOES_NOT_EXIST", "任务不存在或不可见"))
                continue
            key = _issue_key(project, issue)
            if not _can_admin_or_own(role, issue, actor.id, admin_level=ProjectRole.ADMIN):
                # issue.archive 口径（TASK-009 交付：ADMIN 任意 / 其余仅创建者）
                failures.append(_fail(index, "PERM_DENIED", f"{key} 仅创建者可归档"))
                continue
            # ADR-0017：共享 epoch + 抑制内建投递 → 出口单次投递
            result = archive_subtree(issue_id=iid, actor_id=actor.id,
                                     epoch=epoch, suppress_activity=True)
            count = int(result["archived_count"])
            archived_count += count
            if count > 0:  # 幂等重提交（count=0）不重复留痕
                cascade = count - 1
                tail = f"（含 {cascade} 个子任务）" if cascade > 0 else ""
                activity_rows.append(
                    _row(issue, actor, epoch, field="archived_at", verb="updated",
                         new=result["archived_at"].isoformat(),
                         comment=f"{batch_comment}{tail}")
                )
        if failures:
            raise BulkActionError(failures, len(ids))
        _enqueue_batch(activity_rows, batch_comment)
    affected_total = len(affected | set(i for i in ids if i in locked))
    return {
        "data": {
            "archived_count": archived_count,
            "affected_total": affected_total,
            "epoch": epoch,
        },
        "meta": {"batch_size": len(ids), "cascade": affected_total - len(ids)},
    }


# ─────────────────────────────────────────────────────────────────────
# 动作 6：批量删除（DELETE bulk/）
# ─────────────────────────────────────────────────────────────────────
def bulk_delete(*, project, actor, issue_ids: list[uuid.UUID], confirm_count: int,
                comment: str = "", role: int = 0) -> dict:
    """§4.3.4：confirm_count 错配 400；逐条 delete_subtree（软删 + 级联，TASK-004 唯一归属）。

    失败清单口径同 bulk_update：收集全部坏项（不可见 / 权限）后统一抛（BR-02）。
    """
    from plane.db.models.roles import ProjectRole

    ids = dedupe_ids(issue_ids)
    _assert_bulk_limit(ids)
    if confirm_count != len(ids):  # 防「确认后又改选择」的错配
        raise AppException(
            "VALIDATION_ERROR",
            message="确认数量与提交数量不一致",
            details=[{
                "field": "confirm_count", "code": "INVALID",
                "message": f"确认 {confirm_count} 项，实际提交 {len(ids)} 项",
            }],
        )
    epoch = time.time() * 1000
    batch_comment = _batch_comment(comment, f"批量删除 {len(ids)} 个任务")
    failures: list[dict] = []
    activity_rows: list[dict] = []
    with transaction.atomic():
        locked = _lock_batch(project.id, ids)  # BR-04
        affected = _subtree_ids([i for i in ids if i in locked])  # 可见集快照（含已归档后代）
        for index, iid in enumerate(ids):
            issue = locked.get(iid)
            if issue is None:
                failures.append(_fail(index, "DOES_NOT_EXIST", "任务不存在或不可见"))
                continue
            key = _issue_key(project, issue)
            if not _can_admin_or_own(role, issue, actor.id, admin_level=ProjectRole.ADMIN):
                failures.append(_fail(index, "PERM_DENIED", f"{key} 仅创建者可删除"))
                continue
            # ADR-0017：共享 epoch + 抑制内建投递 → 出口单次投递
            result = delete_subtree(iid, actor.id, epoch=epoch, suppress_activity=True)
            cascade = int(result["deleted_count"]) - 1
            tail = f"（含 {cascade} 个子任务）" if cascade > 0 else ""
            activity_rows.append(
                _row(issue, actor, epoch, field="parent", verb="deleted",
                     comment=f"{batch_comment}{tail}")
            )
        if failures:
            raise BulkActionError(failures, len(ids))
        _enqueue_batch(activity_rows, batch_comment)
    visible_ids = [i for i in ids if i in locked]
    affected_total = len(affected | set(visible_ids))
    return {
        "data": {"deleted": len(visible_ids), "affected_total": affected_total, "epoch": epoch},
        "meta": {"batch_size": len(visible_ids), "cascade": affected_total - len(visible_ids)},
    }


# ─────────────────────────────────────────────────────────────────────
# 危险动作预检（POST bulk/preview/）——只读、无锁、近似统计
# ─────────────────────────────────────────────────────────────────────
def bulk_preview(*, project, actor, issue_ids: list[uuid.UUID], action: str,
                 role: int = 0) -> dict:
    """§4.2.4：失败发现前移到确认阶段（权限失败项提前暴露供剔除）。"""
    from plane.db.models.roles import ProjectRole

    ids = dedupe_ids(issue_ids)
    _assert_bulk_limit(ids)
    issues = {
        i.id: i
        for i in Issue.objects.filter(id__in=ids, project_id=project.id, deleted_at__isnull=True)
    }
    denied: list[dict] = []
    for index, iid in enumerate(ids):
        issue = issues.get(iid)
        if issue is None:
            denied.append({"index": index, "issue_key": None, "reason": "任务不存在或不可见"})
            continue
        key = _issue_key(project, issue)
        if action == "delete" and not _can_admin_or_own(
            role, issue, actor.id, admin_level=ProjectRole.ADMIN
        ):
            denied.append({"index": index, "issue_key": key, "reason": "仅创建者可删除"})
        elif action == "archive" and not _can_admin_or_own(
            role, issue, actor.id, admin_level=ProjectRole.ADMIN
        ):
            denied.append({"index": index, "issue_key": key, "reason": "仅创建者可归档"})
    visible_ids = [i for i in ids if i in issues]
    affected_ids = _subtree_ids(visible_ids)
    with_subtree = (
        Issue.objects.filter(parent_id__in=visible_ids, deleted_at__isnull=True)
        .values_list("parent_id", flat=True)
        .distinct()
        .count()
    )
    return {
        "selected": len(ids),
        "with_subtree": with_subtree,
        "cascade_total": max(len(affected_ids) - len(visible_ids), 0),
        "affected_total": len(affected_ids),
        "links": IssueLink.objects.filter(
            Q(issue_id__in=affected_ids) | Q(related_issue_id__in=affected_ids),
            deleted_at__isnull=True,
        ).count(),
        "worklogs": WorkLog.objects.filter(
            issue_id__in=affected_ids, deleted_at__isnull=True
        ).count(),
        "comments": IssueComment.objects.filter(
            issue_id__in=affected_ids, deleted_at__isnull=True
        ).count(),
        "denied": denied,
    }
