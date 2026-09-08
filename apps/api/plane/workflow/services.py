"""工作流引擎（WF-001 §4.4-§4.7）——单事务状态机执行器与解析缓存。

引擎三入口：
- ``transition()``：流转执行单事务（守卫 → 审批挂接 → 副作用 → 状态更新 → Activity）；
- ``resolve_workflow()`` / ``resolve_initial_state()``：三级兜底链解析（缓存）；
- ``validate_for_publish()`` / ``publish()``：发布校验（五项图校验 + 在用状态阻断）与两行模型版本轮转。

零迁移兜底（V1.0 兼容）：无 published 工作流的项目走 V1.0 自由流转 +
TASK-005 完成守卫——行为与标准版完全一致。
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from plane.bgtasks.issue_activity import enqueue_activity
from plane.db.models import Issue, State, Workflow, WorkflowState, WorkflowTransition
from plane.db.models.roles import ProjectRole

logger = logging.getLogger(__name__)

RESOLVED_TTL = 3600  # 秒；失效以信号为准，TTL 仅兜底（WF-001 §4.5）

# WF-001 §4.7 冻结协议的 type 枚举（保存白名单）；执行器按轮次注册：
# R1 仅隐式 blocker_completed，required_fields/estimate_required/role_allowed
# 的执行器随 WF-004（R2）注册，set_field/assign/notify 随 WF-003（R3）注册。
GUARD_TYPES = ("required_fields", "estimate_required", "blocker_completed", "role_allowed")
EFFECT_TYPES = ("set_field", "assign", "notify")


class TransitionError(Exception):
    """携带结构化 details 的流转错误（视图层转信封，WF-001 §4.8③ 矩阵）。"""

    def __init__(self, code: str, status: int, details: list[dict] | None = None,
                 message: str = ""):
        self.code = code
        self.status = status
        self.details = details or []
        self.message = message or code
        super().__init__(self.message)


@dataclass
class PublishIssue:
    """发布校验问题项（WF-001 §4.6）：code + 描述 + 节点/边定位或受影响任务数。"""

    code: str
    message: str
    node_ids: list[str] = field(default_factory=list)
    affected: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.node_ids:
            out["node_ids"] = self.node_ids
        if self.affected:
            out["affected"] = self.affected
        return out


@dataclass
class TransitionResult:
    """transition() 出口：正常完成（issue+edge）或审批挂起（pending_approval）。"""

    issue: Issue | None = None
    edge: WorkflowTransition | None = None
    pending_approval: Any | None = None  # ApprovalInstance（避免循环导入，WF-002 域）


class GuardRegistry:
    """守卫执行器注册表（R2 起委托 plane.workflow.guards 的四类执行器；BR-10 只读）。

    run_all 全量求值 + guard_error 主码分流（§2.5：403 > 400 必填 > 409）；
    隐式 blocker_completed 仅注入迁入 completed 组的边（判定域与 TASK-005 一致）。
    """

    def run_all(self, guards: list[dict], *, issue, actor, to_state: State,
                payload: dict | None) -> None:
        from plane.db.services.field_schema import resolve_fields
        from plane.workflow.guards import guard_error, run_guards

        definitions = {d.field_key: d for d in resolve_fields(issue.project, issue.issue_type_id)}
        failures = run_guards(guards, issue=issue, actor=actor, to_state=to_state,
                              payload=payload, definitions=definitions)
        if failures:
            raise guard_error(failures)


class SideEffectRegistry:
    """副作用执行器注册表（R1 空；set_field/assign/notify 随 WF-003 注册）。"""

    def __init__(self) -> None:
        self._executors: dict[str, Any] = {}

    def register(self, type_: str, executor: Any) -> None:
        self._executors[type_] = executor

    def apply_all(self, effects: list[dict], *, issue, actor) -> None:
        for e in effects or []:
            type_ = e.get("type")
            if type_ not in EFFECT_TYPES:
                raise TransitionError("VALIDATION_ERROR", 400, details=[
                    {"field": "side_effects", "code": "NOT_A_CHOICE",
                     "message": f"未知副作用类型 {type_!r}，合法枚举 {list(EFFECT_TYPES)}"}])
            executor = self._executors.get(type_)
            if executor is None:
                raise TransitionError("VALIDATION_ERROR", 400, details=[
                    {"field": "side_effects", "code": "NOT_A_CHOICE",
                     "message": f"副作用类型 {type_} 暂未启用（随 WF-003 交付）"}])
            executor(e.get("config") or {}, issue=issue, actor=actor)


class WorkflowService:
    """状态机执行器（WF-001 §4.4）——所有流转写路径的唯一事务入口。"""

    def __init__(self) -> None:
        self.guard_registry = GuardRegistry()        # WF-004 注册四类守卫执行器
        self.effect_registry = SideEffectRegistry()  # WF-003 注册副作用执行器

    # ── 解析（兜底链 + 缓存）────────────────────────────────────────

    def _resolve_published(self, project, issue_type_id) -> Workflow | None:
        return (
            Workflow.objects.filter(project=project, status=Workflow.Status.PUBLISHED)
            .filter(Q(issue_type_id=issue_type_id) | Q(issue_type__isnull=True))
            .order_by(F("issue_type_id").desc(nulls_last=True))  # 类型专属优先（§2.2）
            .first()
        )

    def resolve_workflow(self, issue) -> Workflow | None:
        """§4.5：类型专属 published → 项目默认 published → None（V1.0 自由流转）。

        解析结果（含 None 哨兵）整体缓存，publish/archive 信号失效（signals.py）。
        """
        key = f"wf:resolved:{issue.project_id}:{issue.issue_type_id}"
        cached = cache.get(key)
        if cached is not None:
            if cached == "NONE":
                return None
            # 防陈旧：命中 id 必须仍是 published（归档/替换竞态下缓存残留时
            # 以 DB 为准穿透回源——dev LocMem 无 TTL 抖动的兜底，prod Redis 同益）
            wf = Workflow.objects.filter(
                pk=cached, status=Workflow.Status.PUBLISHED).first()
            if wf is not None:
                return wf
            cache.delete(key)
        wf = self._resolve_published(issue.project, issue.issue_type_id)
        cache.set(key, str(wf.id) if wf else "NONE", RESOLVED_TTL)
        return wf

    def resolve_initial_state(self, project, issue_type) -> State:
        """BR-16 创建接线：新建任务初始状态解析（IssueService.create 单事务内调用）。

        兜底链与 resolve_workflow 同形：类型专属 published 图的 is_initial →
        项目默认 published 图的 is_initial → State.is_default（两级回落）。
        """
        wf = self._resolve_published(project, getattr(issue_type, "id", None))
        if wf is not None:
            node = wf.wf_states.filter(is_initial=True).select_related("state").first()
            if node is not None:
                return node.state  # 发布校验保证存在且 = 对应 is_default
        return (
            State.objects.filter(project=project, issue_type=issue_type, is_default=True).first()
            or State.objects.get(project=project, issue_type__isnull=True, is_default=True)
        )

    # ── 流转执行（单事务）───────────────────────────────────────────

    @transaction.atomic
    def transition(self, *, issue_id, to_state_id, actor, transition_id=None,
                   guard_payload: dict | None = None,
                   approval_instance_id=None,
                   force: bool = False, force_comment: str = "") -> TransitionResult:
        epoch = time.time() * 1000  # TASK-010 BR-04：动作入口毫秒时间戳
        issue = (
            Issue.objects.select_for_update(of=("self",))  # nullable FK 的 LEFT JOIN 不可锁，仅锁 issues 行
            .select_related("state", "project")
            .get(pk=issue_id)
        )
        to_state = State.objects.get(pk=to_state_id, project=issue.project)

        wf = self.resolve_workflow(issue)
        edge_guards: list[dict] = []
        if wf is None:
            # 2a. V1.0 兜底：自由流转 + 隐式完成守卫（run_guards 空 guards 注入，
            # 判定域与 TASK-005 完全一致——仅迁入 completed 拦截）
            matched_edge = None
        else:
            # 2b. 受控流转：匹配边（无 → 409；多边未指定 → 400）
            matched_edge = self._match_edge(wf, issue.state_id, to_state_id, transition_id)
            if matched_edge is None:
                raise TransitionError("RESOURCE_TRANSITION_INVALID", 409, details=[
                    {"field": "from_state_id", "code": "INVALID", "message": str(issue.state_id)},
                    {"field": "to_state_id", "code": "INVALID",
                     "message": "与当前状态间不存在流转边，请刷新可用流转列表"},
                ])
            edge_guards = matched_edge.guards or []

        # 3. 守卫（WF-004 §4.3）：全量求值 → 失败按主码分流抛出；force 通道 BR-06
        #    （PROJ_ADMIN 有效角色 + comment 必填；兜底/受控两路径统一）。
        try:
            self.guard_registry.run_all(edge_guards, issue=issue, actor=actor,
                                        to_state=to_state, payload=guard_payload)
        except TransitionError:
            if not force:
                raise
            from plane.workflow.guards import effective_project_role

            _eff_role = effective_project_role(actor, issue.project_id)
            if _eff_role is None or _eff_role < ProjectRole.ADMIN:
                raise TransitionError("PERM_ROLE_INSUFFICIENT", 403,
                                      message="仅项目管理员可强制流转") from None
            if not (force_comment or "").strip():
                raise TransitionError("VALIDATION_ERROR", 400, details=[
                    {"field": "comment", "code": "REQUIRED",
                     "message": "强制流转必须填写说明（BR-06）"}], ) from None

        if wf is not None:
            # 4. 审批挂接（WF-002）：发起（202 挂起）/ 终审回填（跳过二次挂起直执行）
            assert matched_edge is not None
            if matched_edge.approval_flow_id and approval_instance_id is None:
                from plane.workflow.approval import ApprovalService  # 防循环导入

                instance = ApprovalService().start(matched_edge, issue=issue, actor=actor)
                return TransitionResult(pending_approval=instance)
            if approval_instance_id is not None:
                # 终审回填守门（三文档时序闭环，WF-001 §4.4）：
                # 仅「approved 终态」或「is_terminal_passed=True 且 pending」放行。
                from plane.db.models import ApprovalInstance

                ok = ApprovalInstance.objects.filter(
                    id=approval_instance_id, transition_id=matched_edge.id
                ).filter(
                    Q(status=ApprovalInstance.Status.APPROVED)
                    | Q(is_terminal_passed=True, status=ApprovalInstance.Status.PENDING)
                ).exists()
                if not ok:
                    raise TransitionError("RESOURCE_STATE_INVALID", 409, details=[
                        {"field": "approval_instance_id", "code": "INVALID",
                         "message": "审批实例未终审通过，不可回填执行流转"}])
            # 5. 副作用（WF-003）：同事务执行，失败回滚（半完成态防御）
            self.effect_registry.apply_all(matched_edge.side_effects, issue=issue, actor=actor)

        # 5b. guard_payload 单请求补齐落库（WF-004 §4.3/BR-16 白名单 + 值层校验）：
        #     与状态迁移同一事务，任一步失败全回滚。
        payload_fields: list[str] = []
        if guard_payload:
            payload_fields = self._apply_guard_payload(issue, guard_payload, edge_guards)

        # 6. 状态更新 + Activity（TASK-010 管道，BR-13/14：不引入状态历史表）
        old_state = issue.state
        issue.state = to_state
        update_fields = list({f for f in ["state", "updated_at", *payload_fields]
                              if f != "assignees"})  # M2M 已即时写
        issue.save(update_fields=update_fields)
        edge_name = matched_edge.name if matched_edge else None
        enqueue_activity(
            issue_id=issue.id, actor_id=actor.id, verb="updated", epoch=epoch,
            before={"state": str(old_state.id), "transition": None},
            after={"state": str(to_state.id), "transition": edge_name},
        )
        if force and old_state.id != to_state.id:
            # BR-06：强制跳过守卫的事实留痕（field="state.force" 单字段特例，
            # TASK-010 verb 冻结不扩展——WF-004 §2.2 范式）
            enqueue_activity(
                issue_id=issue.id, actor_id=actor.id, verb="updated",
                epoch=epoch + 1,
                before={"state.force": None}, after={"state.force": True},
                comment=f"管理员强制流转，跳过守卫：{force_comment}",
            )
        if old_state.id != to_state.id:
            # WF-002 §2.3 终止触发 state_changed：状态已变 → 该任务其余 pending
            # 审批实例作废（终审回填路径排除自身实例——is_terminal_passed 瞬态位
            # 期间本实例仍为 pending）。on_commit 保证仅在事务提交后生效。

            def _on_state_changed() -> None:
                from plane.db.models import ApprovalInstance

                qs = ApprovalInstance.objects.filter(
                    issue_id=issue.id, status=ApprovalInstance.Status.PENDING)
                if approval_instance_id is not None:
                    qs = qs.exclude(id=approval_instance_id)
                qs.update(status=ApprovalInstance.Status.TERMINATED,
                          terminal_reason="state_changed",
                          completed_at=timezone.now(), updated_at=timezone.now())

            transaction.on_commit(_on_state_changed)
        return TransitionResult(issue=issue, edge=matched_edge)

    def _match_edge(self, wf, from_state_id, to_state_id, transition_id):
        qs = WorkflowTransition.objects.filter(
            workflow=wf, from_state__state_id=from_state_id, to_state__state_id=to_state_id
        )
        if transition_id:
            return qs.filter(pk=transition_id).first()
        edges = list(qs.order_by("sort_order"))
        if len(edges) > 1:
            raise TransitionError("VALIDATION_ERROR", 400, details=[
                {"field": "transition_id", "code": "REQUIRED",
                 "message": "存在多条同名流转路径，须指定 transition_id"}])
        return edges[0] if edges else None

    @staticmethod
    def _apply_guard_payload(issue: Issue, payload: dict,
                             edge_guards: list[dict]) -> list[str]:
        """WF-004 BR-16 写入域白名单 + §4.2 注值层校验（迁移事务内联，非 PATCH 路径）。

        白名单 = required_fields.fields ∪ {estimate_minutes}；域外键 400 NOT_A_CHOICE。
        值层校验：assignees 成员资格 / target_date 日期格式 / cf_* 走
        validate_field_value 原函数；assignees/target_date 为内置字段直写。
        """
        allowed: set[str] = set()
        for g in edge_guards:
            if g.get("type") == "required_fields":
                allowed.update((g.get("config") or {}).get("fields") or [])
            if g.get("type") == "estimate_required":
                allowed.add("estimate_minutes")
        extra = set(payload) - allowed
        if extra:
            raise TransitionError("VALIDATION_ERROR", 400, details=[
                {"field": k, "code": "NOT_A_CHOICE",
                 "message": f"guard_payload 键 {k} 不在当前边守卫声明字段集内（BR-16）"}
                for k in sorted(extra)])

        touched: list[str] = []
        for key, value in payload.items():
            if key == "assignees":
                from plane.db.models import ProjectMember

                ids = list(dict.fromkeys(value or []))
                bad = [u for u in ids if not ProjectMember.objects.filter(
                    project_id=issue.project_id, member_id=u, is_active=True).exists()]
                if bad:
                    raise TransitionError("VALIDATION_ERROR", 400, details=[
                        {"field": "assignees", "code": "DOES_NOT_EXIST",
                         "message": f"非项目成员：{', '.join(map(str, bad))}"}])
                issue.assignees.set(ids)
                touched.append("assignees")  # M2M 已即时写；登记供审计
            elif key == "estimate_minutes":
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise TransitionError("VALIDATION_ERROR", 400, details=[
                        {"field": "estimate_minutes", "code": "INVALID",
                         "message": "预估工时须为非负整数（分钟）"}])
                issue.estimate_minutes = value or None
                touched.append("estimate_minutes")
            elif key == "target_date":
                from datetime import date as _date

                try:
                    _date.fromisoformat(str(value))
                except (TypeError, ValueError):
                    raise TransitionError("VALIDATION_ERROR", 400, details=[
                        {"field": "target_date", "code": "INVALID_DATE",
                         "message": "日期格式须为 YYYY-MM-DD"}]) from None
                issue.target_date = value
                touched.append("target_date")
            elif key.startswith("cf_"):
                from plane.db.services.custom_fields import CustomFieldValidationError, validate_field_value
                from plane.db.services.field_schema import resolve_fields

                defs = {d.field_key: d for d in resolve_fields(issue.project, issue.issue_type_id)}
                d = defs.get(key)
                if d is None:
                    raise TransitionError("VALIDATION_ERROR", 400, details=[
                        {"field": key, "code": "DOES_NOT_EXIST",
                         "message": f"字段 {key} 不存在于项目字段集"}])
                try:
                    cleaned = validate_field_value(d, value, project=issue.project)
                except CustomFieldValidationError as exc:
                    raise TransitionError("VALIDATION_CUSTOM_FIELD_INVALID", 400,
                                          details=exc.details,
                                          message="补齐值校验失败") from None
                cf = dict(issue.custom_fields or {})
                if cleaned is None:
                    cf.pop(key, None)
                else:
                    cf[key] = cleaned
                issue.custom_fields = cf
                touched.append("custom_fields")
        return touched

    # ── 发布校验与版本轮转（WF-001 §4.6）────────────────────────────

    def validate_for_publish(self, wf: Workflow) -> list[PublishIssue]:
        """BR-08 五项图校验 + BR-09 在用状态迁移阻断（发布事务内执行）。"""
        states = list(wf.wf_states.select_related("state"))
        edges = list(wf.transitions.select_related("from_state", "to_state"))
        issues: list[PublishIssue] = []

        # ① 恰一个初始态
        initials = [s for s in states if s.is_initial]
        if len(initials) != 1:
            issues.append(PublishIssue(
                "NON_SINGLE_INITIAL", "工作流须恰好一个初始状态",
                node_ids=[str(s.id) for s in initials]))

        # ② 自初始态 BFS 可达性
        reachable: set = set()
        if initials:
            adj: dict = defaultdict(list)
            for e in edges:
                adj[e.from_state_id].append(e.to_state_id)
            queue = deque([initials[0].id])
            reachable = {initials[0].id}
            while queue:
                cur = queue.popleft()
                for nxt in adj[cur]:
                    if nxt not in reachable:
                        reachable.add(nxt)
                        queue.append(nxt)
            for s in states:
                if s.id not in reachable:
                    issues.append(PublishIssue(
                        "UNREACHABLE", f"状态「{s.state.name}」无法从初始状态到达",
                        node_ids=[str(s.id)]))

        # ③ completed 组节点 ≥1 且可达
        completed = [s for s in states if s.state.group == State.Group.COMPLETED]
        if not completed or (initials and not any(s.id in reachable for s in completed)):
            issues.append(PublishIssue("NO_COMPLETED", "缺少可达的「已完成」组状态节点"))

        # ③b MISSING_REF：边引用不在图内的节点
        known = {s.id for s in states}
        for e in edges:
            if e.from_state_id not in known or e.to_state_id not in known:
                issues.append(PublishIssue(
                    "MISSING_REF", "边引用了不在图内的状态节点",
                    node_ids=[str(e.from_state_id), str(e.to_state_id)]))

        # ⑤ BR-16：is_initial.state 与绑定维度默认状态一致（两级回落判定）
        default_state = (
            State.objects.filter(project=wf.project, issue_type_id=wf.issue_type_id,
                                 is_default=True).first()
            or State.objects.get(project=wf.project, issue_type__isnull=True, is_default=True)
        )
        initial = next((st for st in states if st.is_initial), None)
        if initial is None or initial.state_id != default_state.id:
            issues.append(PublishIssue(
                "INITIAL_MISMATCH",
                "初始状态节点须与该绑定维度的默认状态一致（BR-16）",
                node_ids=[str(initial.id) if initial else ""]))

        # ④ BR-09：在用状态必须在新图状态集内（409 优先级高于 400，视图层分流）
        state_ids = {s.state_id for s in states}
        in_use = (
            Issue.objects.filter(project=wf.project, deleted_at__isnull=True)
            .filter(Q(issue_type_id=wf.issue_type_id) if wf.issue_type_id else Q())
            .exclude(state_id__in=state_ids)
            .values("state_id").annotate(n=Count("id"))
        )
        if in_use:
            issues.append(PublishIssue(
                "STATE_IN_USE", "存在任务的状态不在新图中，须先迁移",
                affected=[{"state_id": str(r["state_id"]), "count": r["n"]} for r in in_use]))
        return issues

    @transaction.atomic
    def publish(self, wf: Workflow, actor) -> tuple[Workflow, str | None, int | None]:
        """发布（两行模型）：旧 published 翻 archived、本草稿翻 published（version+1）。

        返回 (wf, archived_previous_id, archived_previous_version)；
        项目默认工作流（issue_type=NULL）的并存约束走本方法行锁兜底（§4.2 注）。
        """
        if wf.status != Workflow.Status.DRAFT:
            raise TransitionError("RESOURCE_STATE_INVALID", 409, details=[
                {"field": "status", "code": "INVALID", "message": "仅草稿可发布"}])
        # 项目默认工作流：锁项目行串行化并发发布（NULL 不受部分唯一索引约束）
        if wf.issue_type_id is None:
            from plane.db.models import Project

            Project.objects.select_for_update().get(pk=wf.project_id)

        issues = self.validate_for_publish(wf)
        struct = [i for i in issues if i.code != "STATE_IN_USE"]
        if struct:
            raise TransitionError("VALIDATION_ERROR", 400,
                                  details=[i.as_dict() for i in struct],
                                  message="发布校验未通过")
        in_use = [i for i in issues if i.code == "STATE_IN_USE"]
        if in_use:
            raise TransitionError("RESOURCE_STATE_INVALID", 409,
                                  details=[i.as_dict() for i in in_use],
                                  message="存在任务的状态不在新图中，须先迁移")

        old = (
            Workflow.objects.select_for_update()
            .filter(project=wf.project, issue_type=wf.issue_type,
                    status=Workflow.Status.PUBLISHED)
            .first()
        )
        prev_id, prev_version = (str(old.id), old.version) if old else (None, None)
        if old is not None:
            old.status = Workflow.Status.ARCHIVED
            old.save(update_fields=["status", "updated_at"])
        wf.status = Workflow.Status.PUBLISHED
        wf.version = (prev_version or 0) + 1
        wf.published_at = timezone.now()
        wf.published_by = actor
        wf.save(update_fields=["status", "version", "published_at", "published_by", "updated_at"])
        return wf, prev_id, prev_version

    @transaction.atomic
    def archive(self, wf: Workflow, actor) -> Workflow:
        """归档（BR-10：只读保留，该维度任务回落兜底链下一级；状态翻转唯一入口
        在服务层——AC-06 禁视图直改 .status 同款纪律）。"""
        if wf.status != Workflow.Status.PUBLISHED:
            raise TransitionError("RESOURCE_STATE_INVALID", 409, details=[
                {"field": "status", "code": "INVALID", "message": "仅已发布工作流可归档"}])
        wf.status = Workflow.Status.ARCHIVED
        wf.updated_by = actor
        wf.save(update_fields=["status", "updated_at"])
        return wf


def invalidate_workflow_cache(instance: Workflow) -> None:
    """发布/归档/保存/删除时失效解析缓存（范式同 TASK-008 字段定义缓存）。

    改绑类型（PATCH issue_type）的旧 (project, 旧类型) 键由 view 层 pre_save
    快照旧值显式删除（信号触发时 instance 已持新值，删不到旧键）。
    """
    cache.delete(f"wf:resolved:{instance.project_id}:{instance.issue_type_id}")
    cache.delete(f"wf:graph:{instance.id}")


def current_field_locks(issue: Issue) -> list[dict]:
    """字段锁定读时派生（WF-004 §4.4/BR-07）：先按兜底链选定当前生效工作流，
    再取该图内当前状态节点的 field_locks——禁止跨工作流混合取值；
    无工作流项目 → 空锁（V1.0 零行为变化）。"""
    wf = WorkflowService().resolve_workflow(issue)  # 缓存复用，不新增解析查询
    if wf is None:
        return []
    node = (WorkflowState.objects.filter(workflow=wf, state=issue.state)
            .only("field_locks").order_by("pk").first())
    # (workflow, state) 唯一约束下至多一行；order_by("pk") 保证取值确定性
    return node.field_locks if node else []
