"""工作流引擎与审批测试（WF-001/WF-002，Sprint-7 R1 门禁）。

覆盖：模型约束（BR-02/03/06/07/10）、兜底链解析（resolve_workflow/
resolve_initial_state）、V1.0 零行为变化、受控流转（匹配边/守卫/多边）、
发布校验五项 + 两行模型版本轮转、审批五场景（会签/或签/逐级/驳回/撤回）、
终审回填三文档时序闭环（approved 迁移 / guard_failed 复位 terminated）、
单事务回滚（守卫失败状态不变）、终止触发钩子（state_changed/issue_deleted/
issue_archived）。夹具风格对照 test_comment_thread.py。
"""
from __future__ import annotations

import uuid

import pytest
from django.db import IntegrityError
from django.utils import timezone

from plane.db.models import (
    ApprovalFlow,
    ApprovalInstance,
    ApprovalNode,
    Issue,
    IssueLink,
    IssueType,
    Project,
    ProjectMember,
    ProjectRole,
    State,
    User,
    Workflow,
    WorkflowState,
    WorkflowTransition,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)
from plane.db.seeds.project_states import seed_project_states
from plane.workflow.approval import ApprovalError, ApprovalService
from plane.workflow.services import TransitionError, WorkflowService

pytestmark = pytest.mark.django_db


# ────────────────────────────────────────────────────────────────
# 夹具
# ────────────────────────────────────────────────────────────────
@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="wf-owner@rabbit.dev", password="Rabbit123!",
                                     display_name="管理者")
    member = User.objects.create_user(email="wf-member@rabbit.dev", password="Rabbit123!",
                                      display_name="成员甲")
    approver2 = User.objects.create_user(email="wf-appr2@rabbit.dev", password="Rabbit123!",
                                         display_name="审批乙")
    ws = Workspace.objects.create(name="W", slug=f"w-wf-{owner.id.hex[:8]}",
                                  owner=owner, created_by=owner)
    for u, r in ((owner, WorkspaceRole.OWNER), (member, WorkspaceRole.MEMBER),
                 (approver2, WorkspaceRole.MEMBER)):
        WorkspaceMember.objects.create(workspace=ws, member=u, role=r, created_by=owner)
    proj = Project.objects.create(name="P", identifier="WFP", workspace=ws, created_by=owner)
    ProjectMember.objects.create(project=proj, member=owner, role=ProjectRole.ADMIN,
                                 created_by=owner)
    ProjectMember.objects.create(project=proj, member=member, role=ProjectRole.CONTRIBUTOR,
                                 created_by=owner)
    ProjectMember.objects.create(project=proj, member=approver2, role=ProjectRole.CONTRIBUTOR,
                                 created_by=owner)
    seed_project_states(proj)
    states = {s.name: s for s in State.objects.filter(project=proj)}
    return {
        "owner": owner, "member": member, "approver2": approver2,
        "ws": ws, "proj": proj, "states": states,
    }


def _mk_issue(env, name="任务A", state=None, actor=None, issue_type=None):
    return Issue.objects.create(
        project=env["proj"], name=name,
        state=state or env["states"]["待办"],
        issue_type=issue_type,
        created_by=actor or env["owner"],
    )


def _mk_graph(env, *, issue_type=None, name="流程X", with_edge=True,
              approval_flow=None, guards=None):
    """建草稿 + 三节点两边的最小可发布图（初始=待办 → 进行中 → 已完成）。"""
    wf = Workflow.objects.create(project=env["proj"], issue_type=issue_type,
                                 name=name, created_by=env["owner"])
    s = {n: WorkflowState.objects.create(
        workflow=wf, state=env["states"][n], is_initial=(n == "待办")) for n in ("待办", "进行中", "已完成")}
    if with_edge:
        WorkflowTransition.objects.create(
            workflow=wf, from_state=s["待办"], to_state=s["进行中"],
            name="开始", guards=guards or [], approval_flow=approval_flow)
        WorkflowTransition.objects.create(
            workflow=wf, from_state=s["进行中"], to_state=s["已完成"], name="完成",
            approval_flow=approval_flow)
    return wf, s


def _publish(env, wf):
    return WorkflowService().publish(wf, env["owner"])


# ────────────────────────────────────────────────────────────────
# 1. 模型约束
# ────────────────────────────────────────────────────────────────
class TestModelConstraints:
    def test_br02_published_unique_per_type(self, env):
        wf1, _ = _mk_graph(env, name="流程一")
        _publish(env, wf1)
        wf2, _ = _mk_graph(env, name="流程二")
        _publish(env, wf2)  # wf1 翻 archived，wf2 成为唯一 published
        wf3, _ = _mk_graph(env, name="流程三")
        _publish(env, wf3)
        assert Workflow.objects.filter(project=env["proj"],
                                       status=Workflow.Status.PUBLISHED).count() == 1
        assert Workflow.objects.filter(project=env["proj"],
                                       status=Workflow.Status.ARCHIVED).count() == 2

    def test_br03_state_unique_per_workflow(self, env):
        wf, s = _mk_graph(env, with_edge=False)
        with pytest.raises(IntegrityError):
            WorkflowState.objects.create(workflow=wf, state=env["states"]["待办"])

    def test_br07_self_loop_rejected(self, env):
        wf, s = _mk_graph(env, with_edge=False)
        with pytest.raises(IntegrityError):
            WorkflowTransition.objects.create(
                workflow=wf, from_state=s["待办"], to_state=s["待办"], name="自环")

    def test_br10_pending_unique_per_edge(self, env):
        flow = ApprovalFlow.objects.create(project=env["proj"], name="审批F",
                                           created_by=env["owner"])
        ApprovalNode.objects.create(flow=flow, level=1, pass_mode="any",
                                    approver_type="users",
                                    approver_config={"user_ids": [str(env["member"].id)]})
        wf, s = _mk_graph(env, approval_flow=flow)
        _publish(env, wf)
        issue = _mk_issue(env)
        ApprovalInstance.objects.create(
            issue=issue, transition=wf.transitions.get(name="开始"), initiator=env["member"],
            flow_snapshot={"name": "F", "nodes": [], "forbid_self_approve": True},
            from_state=issue.state)
        with pytest.raises(IntegrityError):
            ApprovalInstance.objects.create(
                issue=issue, transition=wf.transitions.get(name="开始"), initiator=env["member"],
                flow_snapshot={"name": "F", "nodes": [], "forbid_self_approve": True},
                from_state=issue.state)


# ────────────────────────────────────────────────────────────────
# 2. 兜底链与零行为变化
# ────────────────────────────────────────────────────────────────
class TestResolveAndV10Fallback:
    def test_resolve_none_when_no_workflow(self, env):
        issue = _mk_issue(env)
        assert WorkflowService().resolve_workflow(issue) is None

    def test_v10_free_transition_with_blocker(self, env):
        """无工作流 = V1.0 自由流转 + TASK-005 完成守卫（零行为变化锚）。"""
        blocker = _mk_issue(env, name="前置任务")
        issue = _mk_issue(env, name="被阻塞任务")
        IssueLink.objects.create(issue=issue, related_issue=blocker,
                                 relation_type="is_blocked_by",
                                 created_by=env["owner"])
        svc = WorkflowService()
        # 自由流转：待办 → 进行中 无边也放行
        r = svc.transition(issue_id=issue.id, to_state_id=env["states"]["进行中"].id,
                           actor=env["owner"])
        assert r.issue.state_id == env["states"]["进行中"].id
        # 迁入完成被 BLOCKER 拦截（409 BLOCKED）
        from plane.db.services.issue_link import TransitionBlockedError
        with pytest.raises(TransitionBlockedError):
            svc.transition(issue_id=issue.id, to_state_id=env["states"]["已完成"].id,
                           actor=env["owner"])
        issue.refresh_from_db()
        assert issue.state_id == env["states"]["进行中"].id  # 状态未变（单事务回滚）

    def test_resolve_initial_state_fallback_chain(self, env):
        svc = WorkflowService()
        st = svc.resolve_initial_state(env["proj"], None)
        assert st.is_default is True  # 无工作流 → State.is_default（V1.0 行为）

    def test_type_specific_priority(self, env):
        it = IssueType.objects.create(workspace=env["ws"], name="缺陷",
                                      created_by=env["owner"])
        default_wf, _ = _mk_graph(env, name="项目默认流程")
        _publish(env, default_wf)
        type_wf, _ = _mk_graph(env, issue_type=it, name="缺陷专属流程")
        _publish(env, type_wf)
        issue = _mk_issue(env, issue_type=it)
        assert WorkflowService().resolve_workflow(issue).id == type_wf.id
        issue2 = _mk_issue(env)  # 无类型 → 项目默认
        assert WorkflowService().resolve_workflow(issue2).id == default_wf.id


# ────────────────────────────────────────────────────────────────
# 3. 受控流转
# ────────────────────────────────────────────────────────────────
class TestControlledTransition:
    def test_no_edge_409_invalid(self, env):
        wf, s = _mk_graph(env)
        _publish(env, wf)
        issue = _mk_issue(env)
        with pytest.raises(TransitionError) as ei:
            WorkflowService().transition(issue_id=issue.id,
                                         to_state_id=env["states"]["已完成"].id,
                                         actor=env["owner"])
        assert ei.value.code == "RESOURCE_TRANSITION_INVALID"
        assert ei.value.status == 409

    def test_edge_ok_and_activity_queued(self, env):
        wf, s = _mk_graph(env)
        _publish(env, wf)
        issue = _mk_issue(env)
        r = WorkflowService().transition(issue_id=issue.id,
                                         to_state_id=env["states"]["进行中"].id,
                                         actor=env["member"],
                                         transition_id=str(wf.transitions.get(name="开始").id))
        assert r.edge.name == "开始"
        issue.refresh_from_db()
        assert issue.state_id == env["states"]["进行中"].id

    def test_multi_edge_requires_transition_id(self, env):
        wf, s = _mk_graph(env)
        WorkflowTransition.objects.create(
            workflow=wf, from_state=s["待办"], to_state=s["进行中"], name="特批开始",
            sort_order=500)
        _publish(env, wf)
        issue = _mk_issue(env)
        with pytest.raises(TransitionError) as ei:
            WorkflowService().transition(issue_id=issue.id,
                                         to_state_id=env["states"]["进行中"].id,
                                         actor=env["owner"])
        assert ei.value.code == "VALIDATION_ERROR"
        assert ei.value.details[0]["field"] == "transition_id"

    def test_blocker_guard_on_controlled_path(self, env):
        """受控路径隐式 blocker_completed 守卫（迁入完成被拦，状态不变）。"""
        wf, s = _mk_graph(env)
        WorkflowTransition.objects.create(
            workflow=wf, from_state=s["待办"], to_state=s["已完成"], name="直接完成")
        _publish(env, wf)
        blocker = _mk_issue(env, name="前置")
        issue = _mk_issue(env, name="被阻")
        IssueLink.objects.create(issue=issue, related_issue=blocker,
                                 relation_type="is_blocked_by", created_by=env["owner"])
        from plane.db.services.issue_link import TransitionBlockedError
        with pytest.raises(TransitionBlockedError):
            WorkflowService().transition(issue_id=issue.id,
                                         to_state_id=env["states"]["已完成"].id,
                                         actor=env["owner"])
        issue.refresh_from_db()
        assert issue.state_id == env["states"]["待办"].id

    def test_state_changed_terminates_other_pending(self, env, django_capture_on_commit_callbacks):
        """§2.3：经其他边流转成功 → 本任务 pending 审批实例 terminated(state_changed)。"""
        flow = ApprovalFlow.objects.create(project=env["proj"], name="挂起审批",
                                           created_by=env["owner"])
        ApprovalNode.objects.create(flow=flow, level=1, pass_mode="all",
                                    approver_type="users",
                                    approver_config={"user_ids": [str(env["member"].id)]})
        wf, s = _mk_graph(env, approval_flow=flow)
        # 另加一条免审批边（取消）
        WorkflowTransition.objects.create(
            workflow=wf, from_state=s["待办"], to_state=s["进行中"], name="跳过审批")
        _publish(env, wf)
        issue = _mk_issue(env, actor=env["member"])
        edge_approval = wf.transitions.get(name="开始")
        inst = ApprovalService().start(edge_approval, issue=issue, actor=env["member"])
        assert inst.status == ApprovalInstance.Status.PENDING
        # 走免审批边 → state_changed 终止
        edge_skip = wf.transitions.get(name="跳过审批")
        # pytest-django 的 django_capture_on_commit_callbacks：在事务内捕获并立即执行
        # on_commit 回调（禁止 django_db(transaction=True)——其 teardown 会
        # flush 整个共享 dev 库，清掉迁移种子与演示数据）。
        with django_capture_on_commit_callbacks(execute=True):
            WorkflowService().transition(issue_id=issue.id,
                                         to_state_id=env["states"]["进行中"].id,
                                         actor=env["member"], transition_id=str(edge_skip.id))
        inst.refresh_from_db()
        assert inst.status == ApprovalInstance.Status.TERMINATED
        assert inst.terminal_reason == "state_changed"


# ────────────────────────────────────────────────────────────────
# 4. 发布校验与两行模型
# ────────────────────────────────────────────────────────────────
class TestPublish:
    def _issues_codes(self, wf):
        return [i.code for i in WorkflowService().validate_for_publish(wf)]

    def test_non_single_initial(self, env):
        wf, s = _mk_graph(env)
        WorkflowState.objects.filter(workflow=wf).update(is_initial=False)
        assert "NON_SINGLE_INITIAL" in self._issues_codes(wf)

    def test_unreachable_and_no_completed(self, env):
        wf, s = _mk_graph(env, with_edge=False)
        s["待办"].is_initial = True
        WorkflowState.objects.filter(workflow=wf).update(is_initial=False)
        WorkflowState.objects.filter(pk=s["待办"].pk).update(is_initial=True)
        codes = self._issues_codes(wf)
        assert "UNREACHABLE" in codes and "NO_COMPLETED" in codes

    def test_initial_mismatch_br16(self, env):
        wf, s = _mk_graph(env)
        WorkflowState.objects.filter(workflow=wf).update(is_initial=False)
        WorkflowState.objects.filter(pk=s["进行中"].pk).update(is_initial=True)
        assert "INITIAL_MISMATCH" in self._issues_codes(wf)

    def test_state_in_use_br09(self, env):
        wf, s = _mk_graph(env)
        _mk_issue(env, state=env["states"]["已取消"])  # 已取消不在新图（在用状态）
        codes = self._issues_codes(wf)
        assert "STATE_IN_USE" in codes

    def test_publish_two_row_model(self, env):
        wf1, _ = _mk_graph(env, name="v1")
        _publish(env, wf1)
        wf2, _ = _mk_graph(env, name="v2")
        wf2, prev_id, prev_version = _publish(env, wf2)
        assert wf2.status == Workflow.Status.PUBLISHED
        assert wf2.version == 2
        assert prev_id == str(wf1.id) and prev_version == 1
        wf1.refresh_from_db()
        assert wf1.status == Workflow.Status.ARCHIVED

    def test_publish_archives_and_falls_back(self, env):
        """BR-10：归档后任务回落兜底链下一级（V1.0 自由流转）。"""
        wf, _ = _mk_graph(env)
        _publish(env, wf)
        issue = _mk_issue(env, state=env["states"]["进行中"])
        assert WorkflowService().resolve_workflow(issue) is not None
        wf.status = Workflow.Status.ARCHIVED
        wf.save(update_fields=["status"])
        from django.core.cache import cache
        cache.delete(f"wf:resolved:{env['proj'].id}:None")
        issue.refresh_from_db()
        assert WorkflowService().resolve_workflow(issue) is None


# ────────────────────────────────────────────────────────────────
# 5. 审批五场景（WF-002）
# ────────────────────────────────────────────────────────────────
def _mk_approval_env(env, *, pass_mode="all", levels=1, approvers=None,
                     forbid_self=True):
    flow = ApprovalFlow.objects.create(
        project=env["proj"], name=f"流程-{pass_mode}-{levels}-{uuid.uuid4().hex[:6]}",
        forbid_self_approve=forbid_self, created_by=env["owner"])
    ids = [str(u.id) for u in (approvers or [env["member"], env["approver2"]])]
    for lv in range(1, levels + 1):
        ApprovalNode.objects.create(
            flow=flow, level=lv, pass_mode=pass_mode, approver_type="users",
            approver_config={"user_ids": ids})
    wf, s = _mk_graph(env, approval_flow=flow)
    _publish(env, wf)
    issue = _mk_issue(env, actor=env["owner"])
    return flow, wf, issue, wf.transitions.get(name="开始")


class TestApprovalScenarios:
    def test_start_returns_202_like_pending(self, env):
        flow, wf, issue, edge_start = _mk_approval_env(env)
        r = WorkflowService().transition(
            issue_id=issue.id, to_state_id=env["states"]["进行中"].id,
            actor=env["owner"], transition_id=str(edge_start.id))
        assert r.pending_approval is not None
        issue.refresh_from_db()
        assert issue.state_id == env["states"]["待办"].id  # 挂起：状态不变

    def test_countersign_all(self, env):
        """会签：全员 approve 才过级。"""
        flow, wf, issue, edge_start = _mk_approval_env(env, pass_mode="all")
        svc = ApprovalService()
        inst = svc.start(edge_start, issue=issue, actor=env["owner"])
        svc.act(instance_id=inst.id, actor=env["member"], action="approve", comment="ok")
        inst.refresh_from_db()
        assert inst.status == ApprovalInstance.Status.PENDING  # 一人未投
        svc.act(instance_id=inst.id, actor=env["approver2"], action="approve", comment="ok")
        inst.refresh_from_db()
        assert inst.status == ApprovalInstance.Status.APPROVED  # 终审回填完成
        issue.refresh_from_db()
        assert issue.state_id == env["states"]["进行中"].id  # 引擎迁移

    def test_or_sign_any(self, env):
        """或签：任一人 approve 即过。"""
        flow, wf, issue, edge_start = _mk_approval_env(env, pass_mode="any")
        svc = ApprovalService()
        inst = svc.start(edge_start, issue=issue, actor=env["owner"])
        svc.act(instance_id=inst.id, actor=env["member"], action="approve", comment="ok")
        inst.refresh_from_db()
        assert inst.status == ApprovalInstance.Status.APPROVED
        issue.refresh_from_db()
        assert issue.state_id == env["states"]["进行中"].id

    def test_sequential_levels(self, env):
        """逐级：两级各一人，先 L1 后 L2。"""
        flow, wf, issue, edge_start = _mk_approval_env(env, pass_mode="any", levels=2,
                                           approvers=[env["member"]])
        svc = ApprovalService()
        inst = svc.start(edge_start, issue=issue, actor=env["owner"])
        assert inst.current_level == 1
        # 越级审批人动作 → 403（approver2 非本级）
        with pytest.raises(ApprovalError) as ei:
            svc.act(instance_id=inst.id, actor=env["approver2"], action="approve")
        assert ei.value.code == "PERM_APPROVAL_NOT_ASSIGNEE"
        svc.act(instance_id=inst.id, actor=env["member"], action="approve", comment="L1 过")
        inst.refresh_from_db()
        assert inst.current_level == 2
        svc.act(instance_id=inst.id, actor=env["member"], action="approve", comment="L2 过")
        inst.refresh_from_db()
        assert inst.status == ApprovalInstance.Status.APPROVED

    def test_reject_requires_comment_and_finalizes(self, env):
        flow, wf, issue, edge_start = _mk_approval_env(env, pass_mode="all")
        svc = ApprovalService()
        inst = svc.start(edge_start, issue=issue, actor=env["owner"])
        with pytest.raises(ApprovalError) as ei:
            svc.act(instance_id=inst.id, actor=env["member"], action="reject", comment="  ")
        assert ei.value.sub == "REQUIRED"
        svc.act(instance_id=inst.id, actor=env["member"], action="reject", comment="材料不足")
        inst.refresh_from_db()
        assert inst.status == ApprovalInstance.Status.REJECTED
        issue.refresh_from_db()
        assert issue.state_id == env["states"]["待办"].id  # 驳回回发起前状态（BR-06）

    def test_withdraw_only_initiator_no_actions(self, env):
        flow, wf, issue, edge_start = _mk_approval_env(env, pass_mode="all")
        svc = ApprovalService()
        inst = svc.start(edge_start, issue=issue, actor=env["owner"])
        # 非发起人 → 403
        with pytest.raises(ApprovalError) as ei:
            svc.act(instance_id=inst.id, actor=env["member"], action="withdraw")
        assert ei.value.code == "PERM_DENIED"
        svc.act(instance_id=inst.id, actor=env["owner"], action="withdraw")
        inst.refresh_from_db()
        assert inst.status == ApprovalInstance.Status.WITHDRAWN
        # 已有动作后再撤回 → 409 HAS_ACTIONS
        flow2, wf2, issue2, edge2 = _mk_approval_env(env, pass_mode="all")
        inst2 = svc.start(edge2, issue=issue2, actor=env["owner"])
        svc.act(instance_id=inst2.id, actor=env["member"], action="approve", comment="ok")
        with pytest.raises(ApprovalError) as ei2:
            svc.act(instance_id=inst2.id, actor=env["owner"], action="withdraw")
        assert ei2.value.sub == "HAS_ACTIONS"

    def test_self_approve_skipped_br12(self, env):
        """禁自审：发起人自己是审批人 → 其票 skipped(self)；或签仅剩自己转交管理员。"""
        ProjectMember.objects.filter(project=env["proj"], member=env["approver2"]) \
            .update(role=ProjectRole.ADMIN)
        flow, wf, issue, edge_start = _mk_approval_env(env, pass_mode="any",
                                           approvers=[env["owner"]], forbid_self=True)
        svc = ApprovalService()
        inst = svc.start(edge_start, issue=issue, actor=env["owner"])
        recs = list(inst.records.all())
        assert any(r.action == "skipped" and r.comment == "self" for r in recs)
        # 全员 skipped → 转交 PROJ_ADMIN（BR-12）：approver2（ADMIN）补票可审批
        assert inst.records.filter(level=1, action="pending",
                                    approver=env["approver2"]).exists()
        ApprovalService().act(instance_id=inst.id, actor=env["approver2"],
                              action="approve", comment="转交后通过")
        inst.refresh_from_db()
        assert inst.status == ApprovalInstance.Status.APPROVED

    def test_terminal_passed_three_doc_loop(self, env):
        """终审回填三态闭环：成功 → APPROVED+迁移；守卫失败 → 复位+TERMINATED。"""
        flow, wf, issue, edge_start = _mk_approval_env(env, pass_mode="all")
        svc = ApprovalService()
        inst = svc.start(edge_start, issue=issue, actor=env["owner"])
        svc.act(instance_id=inst.id, actor=env["member"], action="approve", comment="ok")
        # 终审前挂一个未完成前置 → 引擎守卫失败 → guard_failed_at_complete
        blocker = _mk_issue(env, name="终审前出现的前置")
        IssueLink.objects.create(issue=issue, related_issue=blocker,
                                 relation_type="is_blocked_by", created_by=env["owner"])
        # 目标态是进行中（非 completed）——blocker 守卫只拦 completed；换 completed 边测
        svc2 = ApprovalService()
        # 用「完成」边（进行中→已完成）构造守卫失败：把任务先正常推到进行中
        edge_done = wf.transitions.get(name="完成")
        inst2 = svc2.start(edge_done, issue=issue, actor=env["member"]) \
            if issue.state.name == "进行中" else None
        if inst2 is None:
            # 当前在待办：走「开始」审批通过后到进行中，再对完成边发起
            svc.act(instance_id=inst.id, actor=env["approver2"], action="approve", comment="ok")
            issue.refresh_from_db()
            assert issue.state_id == env["states"]["进行中"].id
            inst2 = svc2.start(edge_done, issue=issue, actor=env["member"])
        # 会签审批人 = member（发起人，票 skipped(self)）+ approver2（唯一 pending 票）
        svc2.act(instance_id=inst2.id, actor=env["approver2"], action="approve", comment="ok")
        inst2.refresh_from_db()
        assert inst2.status == ApprovalInstance.Status.TERMINATED
        assert inst2.terminal_reason == "guard_failed_at_complete"
        assert inst2.is_terminal_passed is False  # 复位
        issue.refresh_from_db()
        assert issue.state_id == env["states"]["进行中"].id  # 未迁移（BR-05）
        # 解除阻塞 → 重发起 → 通过（成功路径验证 approved + 迁移）
        IssueLink.objects.filter(issue=issue).update(deleted_at=timezone.now())
        inst3 = svc2.start(edge_done, issue=issue, actor=env["member"])
        svc2.act(instance_id=inst3.id, actor=env["approver2"], action="approve", comment="ok")
        inst3.refresh_from_db()
        assert inst3.status == ApprovalInstance.Status.APPROVED
        issue.refresh_from_db()
        assert issue.state_id == env["states"]["已完成"].id


# ────────────────────────────────────────────────────────────────
# 6. 终止钩子（issue_deleted / issue_archived）
# ────────────────────────────────────────────────────────────────
class TestTerminationHooks:
    def test_issue_deleted_hook(self, env):
        flow, wf, issue, edge_start = _mk_approval_env(env, pass_mode="all")
        inst = ApprovalService().start(wf.transitions.get(name="开始"), issue=issue, actor=env["owner"])
        issue.deleted_at = timezone.now()
        issue.save(update_fields=["deleted_at"])
        inst.refresh_from_db()
        assert inst.status == ApprovalInstance.Status.TERMINATED
        assert inst.terminal_reason == "issue_deleted"

    def test_issue_archived_hook(self, env):
        flow, wf, issue, edge_start = _mk_approval_env(env, pass_mode="all")
        inst = ApprovalService().start(wf.transitions.get(name="开始"), issue=issue, actor=env["owner"])
        issue.archived_at = timezone.now()
        issue.save(update_fields=["archived_at"])
        inst.refresh_from_db()
        assert inst.status == ApprovalInstance.Status.TERMINATED
        assert inst.terminal_reason == "issue_archived"
