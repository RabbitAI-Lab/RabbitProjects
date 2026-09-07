"""多执行人 / 转交 / 认领服务（TASK-007 §4.3）—— ``sync_assignees_full`` 全系统唯一写入口。

收敛关系（TASK-007 §1.2）：``PUT …/assignees/``、``PATCH …/issues/{id}/`` 的
``assignee_ids`` 字段、``POST …/assignees/claim/``、``DELETE …/assignees/{uid}/``
自退、创建路径首派，全部经 ``sync_assignees_full`` 落库——不存在第二套写逻辑，
PUT 只是「意图更明确的外观」。

业务规则锚点（TASK-007 §2.3）：
- BR-01 上限 10 人（去重后）→ 409 ``RESOURCE_LIMIT_EXCEEDED``（details 子码 ``LIMIT``）
- BR-02 候选必须本项目 active ``ProjectMember`` 且角色 ≥ CONTRIBUTOR
  （COMMENTER/VIEWER 同样排除）→ 400 ``VALIDATION_ERROR``（子码 ``DOES_NOT_EXIST``）
- BR-03 ``dict.fromkeys`` 去重保序（响应 ``assignee_ids`` = 去重后请求顺序）
- BR-04 added 行 ``assigned_by`` = 实际操作者（认领场景 assignee=assigned_by=自己）
- BR-05 认领仅当当前集合为空，否则 409 ``RESOURCE_STATE_INVALID``（子码 ``STATE``）
- BR-06 ``assignee_ids: []`` 清空合法（无人负责是合法中间态）
- BR-09 操作者本人新增/认领自己不通知（bgtask 内抑制）
- BR-10 每次集合变更逐人写 ``IssueActivity(field='assignees')`` 共享同一 epoch（bgtask）
- BR-11 中间表全程**物理删除**——``uniq_issue_assignee`` 不带 ``deleted_at`` 偏条件
  正以此为前提（删后重加不撞唯一约束，UT-09；注意 ``SoftDeleteQuerySet.delete()``
  默认软删，必须显式 ``delete(soft=False)``）
- §2.4 已归档任务 409 STATE / BR-13 归档项目 403 —— 统一入口兜底
  （DELETE 自退不经过 ProjectEntityPermission 拦截器，正是靠这层复用兜住）
- BR-12 成员移出项目/工作空间 → 同事务物理删除其全部指派行（§4.3.3）
"""
from __future__ import annotations

import logging
import uuid

from django.db import transaction

from plane.base.exception import AppException
from plane.db.models import Issue, IssueAssignee, ProjectMember, User
from plane.db.models.roles import ProjectRole
from plane.utils.exceptions import AppValidationError, field_error

logger = logging.getLogger("plane.db.services.issue_assignee")

#: BR-01：单个任务执行人上限（P0 单人 → P2 放开到 10）
MAX_ASSIGNEES = 10


# ─────────────────────────────────────────────────────────────────────
# 异常（全部用既有注册码；LIMIT/STATE 为 details 字段级子码，不占注册表）
# ─────────────────────────────────────────────────────────────────────
class AssigneesLimitExceeded(AppException):
    """BR-01：去重后人数 > 10 —— 409 RESOURCE_LIMIT_EXCEEDED（子码 LIMIT）。"""

    def __init__(self, got: int) -> None:
        super().__init__(
            "RESOURCE_LIMIT_EXCEEDED",
            message=f"执行人最多 {MAX_ASSIGNEES} 人",
            details=[
                {"field": "assignee_ids", "code": "LIMIT", "message": f"当前提交 {got} 人"}
            ],
        )


class IssueArchivedForAssignment(AppException):
    """§2.4：已归档任务上的一切集合变更 —— 409 STATE（统一入口兜底）。"""

    def __init__(self) -> None:
        super().__init__(
            "RESOURCE_STATE_INVALID",
            message="任务已归档，恢复后才能编辑",
            details=[
                {"field": "assignee_ids", "code": "STATE", "message": "任务已归档，不能变更执行人"}
            ],
        )


class IssueAlreadyClaimedError(AppException):
    """BR-05：认领时集合非空 —— 409 STATE（不静默合并：认领是补位不是加入）。"""

    def __init__(self) -> None:
        super().__init__(
            "RESOURCE_STATE_INVALID",
            message="该任务已有执行人，无需认领",
            details=[
                {
                    "field": "assignee_ids",
                    "code": "STATE",
                    "message": "当前执行人集合非空（认领仅限未指派任务）",
                }
            ],
        )


# ─────────────────────────────────────────────────────────────────────
# 支撑件
# ─────────────────────────────────────────────────────────────────────
def _display_names(user_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """候选显示名一次取齐（错误信息与 changes 明细共用；缺失回退 id 字符串）。"""
    if not user_ids:
        return {}
    rows = User.objects.filter(id__in=user_ids).values_list("id", "display_name")
    return {uid: (name or str(uid)) for uid, name in rows}


def _assert_assignable(project_id: uuid.UUID, user_ids: list[uuid.UUID]) -> None:
    """BR-02：候选全部是本项目可指派成员（active 且 ≥ CONTRIBUTOR）。

    一次 IN 查询取全部命中行再比对差集——N 个候选恒为 1 条 SQL，
    不随人数放大（UT-05/06 安全锚定 / UT-17 单查询锚定）。
    """
    if not user_ids:
        return
    eligible = set(
        ProjectMember.objects.filter(
            project_id=project_id,
            member_id__in=user_ids,
            is_active=True,
            deleted_at__isnull=True,
            role__gte=ProjectRole.CONTRIBUTOR,
        ).values_list("member_id", flat=True)
    )
    invalid = [u for u in user_ids if u not in eligible]
    if invalid:
        names = _display_names(invalid)
        label = ", ".join(names.get(u, str(u)) for u in invalid)
        raise AppValidationError(
            [
                field_error(
                    "assignee_ids",
                    "DOES_NOT_EXIST",
                    f"{label} 不是本项目成员或为评论者/查看者，不能被指派",
                )
            ]
        )


def _dispatch(issue_id, actor_id, changes: dict, comment: str) -> None:
    """on_commit 内投递差异化通知任务；broker 不可用时记日志不阻塞已提交事务。"""
    from plane.bgtasks.issue_assignee import dispatch_assignment_events

    try:
        dispatch_assignment_events.delay(
            issue_id=str(issue_id), actor_id=str(actor_id), changes=changes, comment=comment
        )
    except Exception as exc:  # noqa: BLE001 —— 与 workspace_member._safe_delay 同款（on_commit 回调抛错会打穿已提交请求）
        logger.warning(
            "assignee.notify.delivery_failed issue=%s actor=%s exc=%s", issue_id, actor_id, exc
        )


# ─────────────────────────────────────────────────────────────────────
# 唯一写入口
# ─────────────────────────────────────────────────────────────────────
@transaction.atomic
def sync_assignees_full(
    *,
    issue_id: uuid.UUID,
    new_ids: list[uuid.UUID],
    actor_id: uuid.UUID,
    comment: str = "",
    enforce: bool = True,
) -> dict:
    """执行人集合全量替换 —— 全系统唯一写入口（PUT / PATCH / claim / 自退 / 创建首派）。

    - Issue 行 ``select_for_update`` 开事务：claim 与 PUT 竞争同一把行锁，
      并发语义被一条规则说清（§2.2.1）；
    - 归档项目 403（BR-13）/ 已归档任务 409 STATE（§2.4）先于任何集合变更；
    - ``enforce=False`` 跳过成员资格校验（BR-02）——供自退/移除路径使用：
      remaining 是既有执行人，其中可能有人已被降级为 COMMENTER/VIEWER，
      若照常校验会把「已被降权的人」锁死在集合里，与「移除即失权」相悖。
      上限与归档判定对全部调用方恒生效。

    返回 ``{"assignee_ids": [...], "changes": {"added": [...], "removed": [...]}}``；
    ``assignee_ids`` 顺序 = 去重后请求顺序（BR-03 服务端回显去重结果）。
    """
    issue = (
        Issue.objects.select_for_update()
        .select_related("project")
        .get(id=issue_id, deleted_at__isnull=True)
    )
    from plane.app.views._access import require_project_writable

    require_project_writable(issue.project)  # archived/closed（PROJ-003 §4.3.3）
    if issue.archived_at:  # §2.4 统一入口兜底（DELETE 自退不在拦截器覆盖内）
        raise IssueArchivedForAssignment()

    new_ids = list(dict.fromkeys(new_ids))  # BR-03 去重保序
    if len(new_ids) > MAX_ASSIGNEES:  # BR-01
        raise AssigneesLimitExceeded(len(new_ids))
    if enforce:  # BR-02
        _assert_assignable(issue.project_id, new_ids)

    current = list(
        IssueAssignee.objects.filter(issue_id=issue_id)
        .select_related("assignee")
        .order_by("created_at", "id")
    )
    # 中间表物理删除口径（TASK-001 §4.1.2）：任何路径不写 deleted_at → 全量即活跃集合。
    # 保序读取（BR-03）：created_at 首键 + id 升序次级键——id 仅保证同 created_at 碰撞时
    # 排序结果确定不抖动（TASK-003 BR-07 同款论证），不作「展示顺序=加入顺序」语义保证。
    current_ids = {a.assignee_id for a in current}
    new_id_set = set(new_ids)
    added_ids = [i for i in new_ids if i not in current_ids]
    removed_rows = [a for a in current if a.assignee_id not in new_id_set]

    if removed_rows:
        # BR-11 物理删除（历史经 IssueActivity 逐人留痕，BR-10）——
        # 注意 SoftDeleteQuerySet.delete() 默认软删，会撞 uniq_issue_assignee（UT-09）；
        # django-stubs 未见 SoftDeleteQuerySet 自定义签名，soft= 需 ignore
        IssueAssignee.objects.filter(
            issue_id=issue_id, assignee_id__in=[a.assignee_id for a in removed_rows]
        ).delete(soft=False)  # type: ignore[call-arg]
    if added_ids:
        IssueAssignee.objects.bulk_create(  # BR-04：assigned_by = 实际操作者
            [
                IssueAssignee(issue_id=issue_id, assignee_id=uid, assigned_by_id=actor_id)
                for uid in added_ids
            ]
        )

    added_names = _display_names(added_ids)
    changes = {
        "added": [
            {"id": str(u), "display_name": added_names.get(u, str(u))} for u in added_ids
        ],
        "removed": [
            {
                "id": str(a.assignee_id),
                "display_name": (a.assignee.display_name if a.assignee_id else None)
                or str(a.assignee_id),
            }
            for a in removed_rows
        ],
    }
    if added_ids or removed_rows:
        # BR-09/BR-10：on_commit 投递差异化通知（added→issue.assigned /
        # removed→issue.unassigned，操作者本人抑制）+ 逐人 Activity 共享 epoch
        transaction.on_commit(
            lambda: _dispatch(issue_id, actor_id, changes, comment)
        )
    return {"assignee_ids": [str(i) for i in new_ids], "changes": changes}


# ─────────────────────────────────────────────────────────────────────
# 认领 / 自退
# ─────────────────────────────────────────────────────────────────────
def claim_issue(*, issue_id: uuid.UUID, actor_id: uuid.UUID) -> dict:
    """认领（BR-05）：空集合判定 + 写入在同一临界区（Issue 行锁，§2.2.1）。

    自己给自己：BR-04 assignee=assigned_by=自己；BR-09 通知侧抑制。
    """
    with transaction.atomic():
        Issue.objects.select_for_update().get(id=issue_id, deleted_at__isnull=True)
        # 中间表无软删行（BR-11 物理删除口径）：存在任何行即非空
        if IssueAssignee.objects.filter(issue_id=issue_id).exists():
            raise IssueAlreadyClaimedError()
        return sync_assignees_full(
            issue_id=issue_id, new_ids=[actor_id], actor_id=actor_id
        )


def remove_self(*, issue_id: uuid.UUID, user_id: uuid.UUID) -> dict:
    """自退（§4.3.4）：等价于「保留其余成员、移除自己」的集合替换，收敛 sync。

    - 「最后一人退出」天然合法（BR-06 清空中间态）；
    - enforce=False：既有执行人可能已被降级（见 sync_assignees_full 注释），
      降权不应锁死退出；
    - 已归档任务 409（§2.4）与归档项目 403（BR-13）经 sync 统一入口兜底。
    """
    with transaction.atomic():
        Issue.objects.select_for_update().get(id=issue_id, deleted_at__isnull=True)
        remaining = list(
            IssueAssignee.objects.filter(issue_id=issue_id)
            .exclude(assignee_id=user_id)
            .order_by("created_at", "id")
            .values_list("assignee_id", flat=True)
        )
        return sync_assignees_full(
            issue_id=issue_id,
            new_ids=remaining,
            actor_id=user_id,  # 操作者 = 退出者；无 comment
            enforce=False,
        )


# ─────────────────────────────────────────────────────────────────────
# BR-12：成员移出项目 / 工作空间的级联钩子
# ─────────────────────────────────────────────────────────────────────
def purge_member_assignments(
    *,
    member_id: uuid.UUID,
    project_id: uuid.UUID | None = None,
    workspace_id: uuid.UUID | None = None,
    actor=None,
) -> int:
    """BR-12（§4.3.3）：物理删除该成员在指定项目（或工作空间全部项目）的指派行。

    必须在成员移除的同一事务内调用（PROJ-002 remove_member /
    TEAM-002 _soft_delete_with_cascade）；受影响任务进入「未指派」；
    on_commit 逐任务投 issue.unassigned 移出通知（操作者 = 执行移除的管理员，
    接收人非操作者，BR-09 不抑制）。

    返回受影响任务数。
    """
    qs = IssueAssignee.objects.filter(assignee_id=member_id)
    if project_id is not None:
        qs = qs.filter(issue__project_id=project_id)
    if workspace_id is not None:
        qs = qs.filter(issue__project__workspace_id=workspace_id)
    issue_ids = list(qs.values_list("issue_id", flat=True))
    if not issue_ids:
        return 0
    qs.delete(soft=False)  # type: ignore[call-arg]  # 物理删除（BR-11 口径；软删行会挡住重新指派）

    def _notify() -> None:
        from plane.bgtasks.issue_assignee import notify_assignments_purged

        try:
            notify_assignments_purged.delay(
                issue_ids=[str(i) for i in issue_ids],
                member_id=str(member_id),
                actor_id=str(actor.id) if actor is not None else None,
            )
        except Exception as exc:  # noqa: BLE001 —— on_commit 回调抛错会打穿已提交请求
            logger.warning(
                "assignee.purge.notify_failed member=%s issues=%s exc=%s",
                member_id, len(issue_ids), exc,
            )

    transaction.on_commit(_notify)
    return len(issue_ids)
