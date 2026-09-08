"""审批执行引擎（WF-002 §4.5）——审批执行唯一入口。

所有写路径必须经 ApprovalService（BR-14 留痕完整性依赖唯一入口）。
会签/或签/逐级/驳回/撤回/终止六态由 act() 的 L1-L9 边界表承载；
终审通过经 _complete_via_engine 回填 WF-001 引擎单事务入口（三文档时序闭环）。
"""
from __future__ import annotations

import logging
import time

from django.db import transaction
from django.utils import timezone

from plane.bgtasks.issue_activity import enqueue_activity
from plane.db.models import (
    ApprovalFlow,
    ApprovalInstance,
    ApprovalNode,
    ApprovalRecord,
    Issue,
    ProjectMember,
    User,
    WorkflowTransition,
)
from plane.db.models.roles import ProjectRole
from plane.db.services.issue_link import TransitionBlockedError
from plane.workflow.services import TransitionError

logger = logging.getLogger(__name__)


class ApprovalError(Exception):
    """审批域错误（视图层转信封；与引擎 TransitionError 分域）。"""

    def __init__(self, code: str, status: int, sub: str | None = None,
                 field: str | None = None, message: str = ""):
        self.code, self.status, self.sub, self.field = code, status, sub, field
        self.message = message or code
        super().__init__(self.message)


def _project_member_ids(project_id) -> set:
    """活跃项目成员 ID 集（BR-02 审批人成员过滤与 role 展开的共同底座）。"""
    return set(
        ProjectMember.objects.filter(
            project_id=project_id, is_active=True, deleted_at__isnull=True
        ).values_list("member_id", flat=True)
    )


def resolve_approvers(node_cfg: dict, *, issue: Issue) -> list[User]:
    """三源审批人解析（WF-002 §2.1）：users / role / field（快照语义 BR-11）。"""
    source, config = node_cfg.get("approver_type"), node_cfg.get("approver_config") or {}
    if source == ApprovalNode.ApproverType.USERS:
        ids = [u for u in config.get("user_ids") or [] if u]
        return list(User.objects.filter(id__in=ids))
    if source == ApprovalNode.ApproverType.ROLE:
        role_name = config.get("role") or ""
        try:
            role_value = ProjectRole(role_name).value
        except ValueError:
            return []
        member_ids = set(
            ProjectMember.objects.filter(
                project_id=issue.project_id, role=role_value,
                is_active=True, deleted_at__isnull=True,
            ).values_list("member_id", flat=True)
        )
        return list(User.objects.filter(id__in=member_ids))
    if source == ApprovalNode.ApproverType.FIELD:
        fname = config.get("field")
        if fname == "reporter":
            uid = issue.created_by_id
            return list(User.objects.filter(id=uid)) if uid else []
        if fname == "assignees":
            ids = list(issue.assignees.values_list("id", flat=True))
            return list(User.objects.filter(id__in=ids))
    return []


def _snapshot_flow(flow: ApprovalFlow) -> dict:
    """立案时刻定义快照（BR-11：定义后续修改不影响在途实例）。"""
    nodes = [
        {
            "level": n.level,
            "pass_mode": n.pass_mode,
            "approver_type": n.approver_type,
            "approver_config": n.approver_config,
            "timeout_hours": n.timeout_hours,
        }
        for n in flow.nodes.all().order_by("level")
    ]
    return {
        "id": str(flow.id), "name": flow.name,
        "forbid_self_approve": flow.forbid_self_approve, "nodes": nodes,
    }


def validate_flow_definition(nodes_payload: list[dict]) -> None:
    """定义保存侧校验（BR-02 前置）：nodes 数量 1..10、level 连续 1..N、
    三源配置形态与审批人规模（users ≤20、role 展开 ≤50）。"""
    if not nodes_payload:
        raise ApprovalError("VALIDATION_ERROR", 400, sub="REQUIRED", field="nodes",
                            message="审批流至少一个节点")
    if len(nodes_payload) > 10:
        raise ApprovalError("RESOURCE_LIMIT_EXCEEDED", 409, sub="LIMIT", field="nodes",
                            message="单流节点数上限 10 级")
    for idx, n in enumerate(nodes_payload, start=1):
        if n.get("level") != idx:
            raise ApprovalError("VALIDATION_ERROR", 400, sub="INVALID", field="nodes",
                                message=f"节点 level 须从 1 连续递增（第 {idx} 位不合法）")
        if n.get("pass_mode") not in (ApprovalNode.PassMode.ANY, ApprovalNode.PassMode.ALL):
            raise ApprovalError("VALIDATION_ERROR", 400, sub="NOT_A_CHOICE", field="nodes",
                                message="pass_mode ∈ {any, all}")
        if n.get("approver_type") not in (
            ApprovalNode.ApproverType.USERS, ApprovalNode.ApproverType.ROLE,
            ApprovalNode.ApproverType.FIELD,
        ):
            raise ApprovalError("VALIDATION_ERROR", 400, sub="NOT_A_CHOICE", field="nodes",
                                message="approver_type ∈ {users, role, field}")
        cfg = n.get("approver_config") or {}
        if n.get("approver_type") == ApprovalNode.ApproverType.USERS:
            ids = cfg.get("user_ids") or []
            if not ids or len(ids) > 20:
                raise ApprovalError("RESOURCE_LIMIT_EXCEEDED", 409, sub="LIMIT",
                                    field="user_ids", message="指定成员 1..20 人")
            if len(set(ids)) != len(ids):
                raise ApprovalError("VALIDATION_ERROR", 400, sub="INVALID",
                                    field="user_ids", message="成员不得重复")


class ApprovalService:
    """审批执行唯一入口（WF-002 §4.5）。"""

    @transaction.atomic
    def start(self, transition: WorkflowTransition, *, issue: Issue, actor) -> ApprovalInstance:
        """发起立案——WF-001 §4.4 审批挂接分支的唯一被调方。"""
        flow = transition.approval_flow
        if flow is None:  # 引擎仅在 approval_flow_id 非空分支调用——防御窄化
            raise ApprovalError("VALIDATION_ERROR", 400, message="流转边未挂审批流")
        # BR-10：同任务同边仅一个 pending——服务层显式 409（部分唯一索引兜底并发）
        if ApprovalInstance.objects.filter(
                issue=issue, transition=transition,
                status=ApprovalInstance.Status.PENDING).exists():
            raise ApprovalError("RESOURCE_ALREADY_EXISTS", 409,
                                message="已存在进行中的审批")
        if not flow.is_active:
            raise ApprovalError("RESOURCE_CONFLICT", 409, sub="DISABLED",
                                message="该审批流已停用")
        epoch = time.time() * 1000
        snapshot = _snapshot_flow(flow)
        instance = ApprovalInstance.objects.create(
            issue=issue, transition=transition, initiator=actor,
            flow_snapshot=snapshot, from_state=issue.state,
        )
        self._open_level(instance, level=1)  # 生成第 1 级审批票（INSERT pending）
        transaction.on_commit(
            lambda: self._enqueue_notify(str(instance.id), level=1))
        enqueue_activity(
            issue_id=issue.id, actor_id=actor.id, verb="updated", epoch=epoch,
            before={"approval": None},
            after={"approval": f"{flow.name} · 第 1 级"},
            comment=f"发起流转审批「{transition.name}」",
        )
        return instance

    @transaction.atomic
    def act(self, *, instance_id, actor, action: str, comment: str = "") -> ApprovalInstance:
        instance = (
            ApprovalInstance.objects.select_for_update(of=("self",))  # 仅锁实例行（join 侧不锁）
            .select_related("issue", "issue__project", "transition")
            .get(id=instance_id)
        )
        if instance.status != ApprovalInstance.Status.PENDING:
            raise ApprovalError("RESOURCE_CONFLICT", 409, sub="INVALID_STATE",
                                message="审批已结束")
        if action in ("withdraw", "terminate"):
            return self._lifecycle(instance, actor, action, comment)  # BR-07/BR-08
        if action not in ("approve", "reject"):
            raise ApprovalError("VALIDATION_ERROR", 400, sub="NOT_A_CHOICE",
                                field="action", message="action ∈ {approve, reject, withdraw, terminate}")
        record = self._current_record(instance, actor)  # 非当前级审批人 → 403
        if action == "reject" and not comment.strip():
            raise ApprovalError("VALIDATION_ERROR", 400, sub="REQUIRED", field="comment",
                                message="驳回意见必填")

        record.action = action
        record.comment = comment
        record.acted_at = timezone.now()
        record.save(update_fields=["action", "comment", "acted_at"])  # 唯一一次合法迁移（BR-09）

        self._emit_activity(instance, actor, action, comment)
        if action == "reject" or self._level_rejected(instance):
            return self._finalize(instance, ApprovalInstance.Status.REJECTED, actor)
        if self._level_passed(instance):
            nxt = instance.current_level + 1
            if nxt <= len(instance.flow_snapshot["nodes"]):
                instance.current_level = nxt
                instance.save(update_fields=["current_level", "updated_at"])
                self._open_level(instance, nxt)
                transaction.on_commit(
                    lambda: self._enqueue_notify(str(instance.id), level=nxt))
            else:
                return self._complete_via_engine(instance, actor)  # 终审：引擎补完迁移
        return instance

    # ── 终审回填（三文档时序闭环，WF-002 §4.5）──────────────────────

    @transaction.atomic
    def _complete_via_engine(self, instance: ApprovalInstance, actor) -> ApprovalInstance:
        """终审回填：①先置 is_terminal_passed=True（实例仍 pending）→ ②单事务内调
        引擎 transition() 重跑守卫（to_state 经边换算 State 主键）→ ③成功 finalize
        APPROVED / 失败复位 False + finalize TERMINATED(guard_failed_at_complete)。
        """
        from plane.workflow.services import WorkflowService  # 防循环导入（领域服务层）

        instance.is_terminal_passed = True
        instance.save(update_fields=["is_terminal_passed", "updated_at"])
        try:
            WorkflowService().transition(
                issue_id=instance.issue_id,
                to_state_id=str(instance.transition.to_state.state_id),
                actor=actor,
                transition_id=str(instance.transition_id),
                approval_instance_id=str(instance.id),
            )
        except (TransitionError, ApprovalError) as exc:
            # 守卫失败两类形态统一捕获（WF-001 §4.5 注 2 对齐）：WF-004 注册后的
            # guard_error → TransitionError；R1 隐式 blocker 守卫 → TASK-005 的
            # TransitionBlockedError（同归 TERMINATED(guard_failed_at_complete)）。
            instance.is_terminal_passed = False
            instance.save(update_fields=["is_terminal_passed", "updated_at"])
            return self._finalize(instance, ApprovalInstance.Status.TERMINATED, actor,
                                  reason="guard_failed_at_complete", detail=str(exc))
        except TransitionBlockedError as exc:
            instance.is_terminal_passed = False
            instance.save(update_fields=["is_terminal_passed", "updated_at"])
            return self._finalize(instance, ApprovalInstance.Status.TERMINATED, actor,
                                  reason="guard_failed_at_complete", detail=str(exc))
        return self._finalize(instance, ApprovalInstance.Status.APPROVED, actor)

    # ── 内部构件 ────────────────────────────────────────────────────

    def _open_level(self, instance: ApprovalInstance, level: int) -> None:
        node = instance.flow_snapshot["nodes"][level - 1]
        approvers = resolve_approvers(node, issue=instance.issue)
        member_ids = _project_member_ids(instance.issue.project_id)
        approvers = [u for u in approvers if u.id in member_ids]
        if not approvers:
            raise ApprovalError("VALIDATION_ERROR", 400, sub="EMPTY_APPROVERS",
                                field="approvers",
                                message="审批人集为空（成员变动或配置失效），请联系管理员")
        records = []
        for u in approvers:
            skipped = (
                instance.flow_snapshot.get("forbid_self_approve")
                and u.id == instance.initiator_id
            )
            records.append(ApprovalRecord(
                instance=instance, level=level, approver=u,
                action=ApprovalRecord.Action.SKIPPED if skipped else ApprovalRecord.Action.PENDING,
                comment="self" if skipped else "",
            ))
        ApprovalRecord.objects.bulk_create(records)
        if all(r.action == ApprovalRecord.Action.SKIPPED for r in records):
            self._escalate_to_proj_admin(instance, level)  # BR-12 全员被跳 → 转交管理员

    def _escalate_to_proj_admin(self, instance: ApprovalInstance, level: int) -> None:
        """禁自审后仅剩发起人（BR-12）：本级补开 PROJ_ADMIN 审批票。"""
        admin_ids = set(
            ProjectMember.objects.filter(
                project_id=instance.issue.project_id, role=ProjectRole.ADMIN,
                is_active=True, deleted_at__isnull=True,
            ).values_list("member_id", flat=True)
        )
        admins = [u for u in User.objects.filter(id__in=admin_ids)
                  if u.id != instance.initiator_id]
        if not admins:
            raise ApprovalError("VALIDATION_ERROR", 400, sub="EMPTY_APPROVERS",
                                field="approvers", message="或签仅剩发起人且无管理员可转交")
        ApprovalRecord.objects.bulk_create([
            ApprovalRecord(instance=instance, level=level, approver=u,
                           action=ApprovalRecord.Action.PENDING)
            for u in admins
        ])

    def _current_record(self, instance: ApprovalInstance, actor) -> ApprovalRecord:
        record = (
            instance.records.filter(level=instance.current_level, approver=actor,
                                    action=ApprovalRecord.Action.PENDING)
            .select_for_update().first()
        )
        if record is None:
            raise ApprovalError("PERM_APPROVAL_NOT_ASSIGNEE", 403,
                                message=f"当前审批级别为第 {instance.current_level} 级，您不是本级指定审批人")
        return record

    def _level_records(self, instance: ApprovalInstance, level: int | None = None):
        level = level if level is not None else instance.current_level
        return instance.records.filter(level=level)

    def _level_rejected(self, instance: ApprovalInstance) -> bool:
        return self._level_records(instance).filter(
            action=ApprovalRecord.Action.REJECT).exists()

    def _level_passed(self, instance: ApprovalInstance) -> bool:
        node = instance.flow_snapshot["nodes"][instance.current_level - 1]
        # skipped(self/offboarded) 票不计入会签分母（BR-12「不计入通过集」）
        qs = self._level_records(instance).exclude(action=ApprovalRecord.Action.SKIPPED)
        if node["pass_mode"] == ApprovalNode.PassMode.ALL:
            total = qs.count()
            approved = qs.filter(action=ApprovalRecord.Action.APPROVE).count()
            return total > 0 and approved == total
        return qs.filter(action=ApprovalRecord.Action.APPROVE).exists()

    def _lifecycle(self, instance: ApprovalInstance, actor, action: str,
                   comment: str) -> ApprovalInstance:
        """非票动作：withdraw（BR-07 仅发起人且无任何审批动作）/ terminate（BR-08）。"""
        if action == "withdraw":
            if instance.initiator_id != actor.id:
                raise ApprovalError("PERM_DENIED", 403, message="仅发起人可撤回")
            has_actions = instance.records.exclude(
                action=ApprovalRecord.Action.PENDING
            ).exclude(action=ApprovalRecord.Action.SKIPPED).exists()
            if has_actions:
                raise ApprovalError("RESOURCE_CONFLICT", 409, sub="HAS_ACTIONS",
                                    message="已有审批动作，不可撤回")
            self._emit_activity(instance, actor, "withdraw", comment)
            return self._finalize(instance, ApprovalInstance.Status.WITHDRAWN, actor)
        # terminate：PROJ_ADMIN + comment 必填（BR-08，审计落 comment）
        is_admin = ProjectMember.objects.filter(
            project_id=instance.issue.project_id, member=actor, is_active=True,
            deleted_at__isnull=True, role=ProjectRole.ADMIN
        ).exists() or instance.issue.project.created_by_id == actor.id
        if not is_admin:
            raise ApprovalError("PERM_DENIED", 403, message="仅项目管理员可终止")
        if not comment.strip():
            raise ApprovalError("VALIDATION_ERROR", 400, sub="REQUIRED", field="comment",
                                message="终止意见必填")
        self._emit_activity(instance, actor, "terminate", comment)
        return self._finalize(instance, ApprovalInstance.Status.TERMINATED, actor,
                              reason="admin")

    def _finalize(self, instance: ApprovalInstance, status: str, actor, *,
                  reason: str = "", detail: str = "") -> ApprovalInstance:
        instance.status = status
        instance.terminal_reason = reason if status == ApprovalInstance.Status.TERMINATED else ""
        instance.completed_at = timezone.now()
        save_fields = ["status", "terminal_reason", "completed_at", "updated_at"]
        instance.save(update_fields=save_fields)
        if status == ApprovalInstance.Status.TERMINATED and reason == "guard_failed_at_complete":
            enqueue_activity(
                issue_id=instance.issue_id, actor_id=actor.id, verb="updated",
                epoch=time.time() * 1000,
                before={"approval": f"{instance.flow_snapshot['name']} · 第 {instance.current_level} 级"},
                after={"approval": None},
                comment=f"终审校验失败，审批终止：{detail[:180]}",
            )
        return instance

    def _emit_activity(self, instance: ApprovalInstance, actor, action: str,
                       comment: str) -> None:
        """BR-14：审批全程动作落 Activity（field='approval' 单字段特例范式）。"""
        flow_name = instance.flow_snapshot["name"]
        level = instance.current_level
        before = {"approval": f"{flow_name} · 第 {level} 级"}
        after_map = {
            "approve": f"{flow_name} · 第 {level} 级（已通过）",
            "reject": f"{flow_name} · 已驳回",
            "withdraw": f"{flow_name} · 已撤回",
            "terminate": f"{flow_name} · 已终止",
        }
        enqueue_activity(
            issue_id=instance.issue_id, actor_id=actor.id, verb="updated",
            epoch=time.time() * 1000,
            before=before, after={"approval": after_map.get(action, None)},
            comment=comment or f"审批动作 {action}",
        )

    @staticmethod
    def _enqueue_notify(instance_id: str, level: int) -> None:
        from plane.workflow.tasks import notify_approvers  # 防循环导入

        try:
            notify_approvers.delay(instance_id, level)
        except Exception:  # noqa: BLE001 —— 通知尽力而为（投递失败不阻断审批主流程）
            logger.warning("approval.notify.dispatch_failed instance=%s level=%s",
                           instance_id, level)


def terminate_pending_instances(issue_id, reason: str) -> int:
    """终止触发五挂点共用 handler（WF-002 §2.3）：单 UPDATE 减少 IO。

    reason ∈ {state_changed, issue_deleted, issue_archived}（admin/
    guard_failed_at_complete 走 _finalize 直接路径）。
    """
    return ApprovalInstance.objects.filter(
        issue_id=issue_id, status=ApprovalInstance.Status.PENDING
    ).update(status=ApprovalInstance.Status.TERMINATED, terminal_reason=reason,
             completed_at=timezone.now(), updated_at=timezone.now())
