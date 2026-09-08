"""流转守卫矩阵测试（WF-004，Sprint-7 R2 门禁）。

覆盖：四守卫语义与主码分流（§2.5）、guard_payload 单请求补齐与写入域白名单
（BR-05/16）、force 强制通道（BR-06）、字段锁定读时派生与豁免（§2.3/§4.4）、
available 预览 blocked_by/deny_reason（§4.6）、PATCH state_id 旁路收口
（ADR-0028 #5）、graph 保存守卫校验（BR-02/03/09）。
"""
from __future__ import annotations

import pytest

from plane.db.models import (
    Issue,
    IssueLink,
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
from plane.workflow.services import TransitionError, WorkflowService

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="g-owner@rabbit.dev", password="Rabbit123!",
                                     display_name="管理员")
    member = User.objects.create_user(email="g-member@rabbit.dev", password="Rabbit123!",
                                      display_name="成员")
    ws = Workspace.objects.create(name="W", slug=f"w-g-{owner.id.hex[:8]}",
                                  owner=owner, created_by=owner)
    for u, r in ((owner, WorkspaceRole.OWNER), (member, WorkspaceRole.MEMBER)):
        WorkspaceMember.objects.create(workspace=ws, member=u, role=r, created_by=owner)
    proj = Project.objects.create(name="P", identifier="GPR", workspace=ws, created_by=owner)
    ProjectMember.objects.create(project=proj, member=owner, role=ProjectRole.ADMIN,
                                 created_by=owner)
    ProjectMember.objects.create(project=proj, member=member, role=ProjectRole.CONTRIBUTOR,
                                 created_by=owner)
    seed_project_states(proj)
    from plane.db.models import IssueType

    IssueType.objects.create(workspace=ws, name="任务", is_default=True, created_by=owner)
    states = {s.name: s for s in State.objects.filter(project=proj)}
    return {"owner": owner, "member": member, "ws": ws, "proj": proj, "states": states}


def _mk_issue(env, name="任务", state=None, actor=None, **kw):
    return Issue.objects.create(
        project=env["proj"], name=name,
        state=state or env["states"]["待办"],
        created_by=actor or env["owner"], **kw)


def _graph(env, guards=None, locks=None, name="流程"):
    """草稿+发布：待办 --[开始]--> 进行中 --[完成]--> 已完成；guards 挂「开始」边，
    locks 挂「进行中」节点。"""
    wf = Workflow.objects.create(project=env["proj"], name=name, created_by=env["owner"])
    s = {}
    for n in ("待办", "进行中", "已完成"):
        s[n] = WorkflowState.objects.create(
            workflow=wf, state=env["states"][n], is_initial=(n == "待办"),
            field_locks=[{"field": f} for f in (locks or [])] if n == "进行中" else [])
    WorkflowTransition.objects.create(
        workflow=wf, from_state=s["待办"], to_state=s["进行中"], name="开始",
        guards=guards or [])
    WorkflowTransition.objects.create(
        workflow=wf, from_state=s["进行中"], to_state=s["已完成"], name="完成")
    WorkflowService().publish(wf, env["owner"])
    wf.refresh_from_db()
    return wf


# ── 四守卫语义与主码分流 ────────────────────────────────────────────
class TestGuardSemantics:
    def test_required_fields_missing(self, env):
        wf = _graph(env, guards=[{"type": "required_fields",
                                  "config": {"fields": ["assignees", "target_date"]}}])
        issue = _mk_issue(env)
        with pytest.raises(TransitionError) as ei:
            WorkflowService().transition(issue_id=issue.id, to_state_id=env["states"]["进行中"].id,
                                         actor=env["owner"],
                                         transition_id=str(wf.transitions.get(name="开始").id))
        assert ei.value.code == "VALIDATION_REQUIRED_FIELD_MISSING"
        assert ei.value.status == 400
        fields = {d["field"] for d in ei.value.details}
        assert fields == {"assignees", "target_date"}
        assert all(d["guard"] == "required_fields" and d["code"] == "REQUIRED"
                   for d in ei.value.details)
        assert all("meta" in d for d in ei.value.details)  # 补录控件元数据

    def test_estimate_required(self, env):
        wf = _graph(env, guards=[{"type": "estimate_required", "config": {"min_minutes": 30}}])
        issue = _mk_issue(env, estimate_minutes=10)
        with pytest.raises(TransitionError) as ei:
            WorkflowService().transition(issue_id=issue.id, to_state_id=env["states"]["进行中"].id,
                                         actor=env["owner"],
                                         transition_id=str(wf.transitions.get(name="开始").id))
        assert ei.value.code == "VALIDATION_ESTIMATE_REQUIRED"
        assert ei.value.details[0]["field"] == "estimate_minutes"

    def test_blocker_completed_on_edge(self, env):
        blocker = _mk_issue(env, name="前置")
        wf = _graph(env, guards=[{"type": "blocker_completed", "config": {}}])
        # 「开始」边迁入 started——判定域仅 completed，显式配置也不扩大（§2.1）
        issue = _mk_issue(env)
        WorkflowService().transition(issue_id=issue.id, to_state_id=env["states"]["进行中"].id,
                                     actor=env["owner"],
                                     transition_id=str(wf.transitions.get(name="开始").id))
        issue.refresh_from_db()
        assert issue.state.name == "进行中"  # started 边不拦
        # 「完成」边迁入 completed——隐式守卫拦截（无显式配置同款）
        IssueLink.objects.create(issue=issue, related_issue=blocker,
                                 relation_type="is_blocked_by", created_by=env["owner"])
        done_edge = wf.transitions.get(name="完成")
        with pytest.raises(TransitionError) as ei:
            WorkflowService().transition(issue_id=issue.id, to_state_id=env["states"]["已完成"].id,
                                         actor=env["owner"], transition_id=str(done_edge.id))
        assert ei.value.code == "RESOURCE_TRANSITION_BLOCKED"
        detail = ei.value.details[0]
        assert detail["code"] == "BLOCKED_BY" and detail["field"] == "blockers"
        assert detail["blockers"][0]["issue_key"].startswith("GPR-")

    def test_role_allowed(self, env):
        wf = _graph(env, guards=[{"type": "role_allowed", "config": {"roles": ["PROJ_ADMIN"]}}])
        issue = _mk_issue(env)
        with pytest.raises(TransitionError) as ei:
            WorkflowService().transition(issue_id=issue.id, to_state_id=env["states"]["进行中"].id,
                                         actor=env["member"],  # CONTRIBUTOR
                                         transition_id=str(wf.transitions.get(name="开始").id))
        assert ei.value.code == "PERM_TRANSITION_NOT_ALLOWED"
        assert ei.value.status == 403
        assert ei.value.details[0]["required_roles"] == ["PROJ_ADMIN"]

    def test_primary_code_precedence(self, env):
        """多守卫同败：role 在场 → 主码 403（details 全量含字段缺口）。"""
        wf = _graph(env, guards=[
            {"type": "required_fields", "config": {"fields": ["target_date"]}},
            {"type": "role_allowed", "config": {"roles": ["PROJ_ADMIN"]}}])
        issue = _mk_issue(env)
        with pytest.raises(TransitionError) as ei:
            WorkflowService().transition(issue_id=issue.id, to_state_id=env["states"]["进行中"].id,
                                         actor=env["member"],
                                         transition_id=str(wf.transitions.get(name="开始").id))
        assert ei.value.status == 403  # role 优先
        guards_seen = {d["guard"] for d in ei.value.details}
        assert guards_seen == {"role_allowed", "required_fields"}  # details 全量


# ── guard_payload 单请求补齐（BR-05/16）─────────────────────────────
class TestGuardPayload:
    def test_payload_completes_in_one_request(self, env):
        wf = _graph(env, guards=[{"type": "required_fields",
                                  "config": {"fields": ["target_date"]}}])
        issue = _mk_issue(env)
        WorkflowService().transition(
            issue_id=issue.id, to_state_id=env["states"]["进行中"].id, actor=env["owner"],
            transition_id=str(wf.transitions.get(name="开始").id),
            guard_payload={"target_date": "2026-09-30"})
        issue.refresh_from_db()
        assert issue.state.name == "进行中"
        assert str(issue.target_date) == "2026-09-30"  # 同事务落库

    def test_payload_domain_whitelist_br16(self, env):
        wf = _graph(env, guards=[{"type": "required_fields",
                                  "config": {"fields": ["target_date"]}}])
        issue = _mk_issue(env, target_date="2026-09-10")  # 守卫可过——孤立验证白名单
        with pytest.raises(TransitionError) as ei:
            WorkflowService().transition(
                issue_id=issue.id, to_state_id=env["states"]["进行中"].id, actor=env["owner"],
                transition_id=str(wf.transitions.get(name="开始").id),
                guard_payload={"priority": "urgent"})  # 域外键
        assert ei.value.code == "VALIDATION_ERROR"
        assert ei.value.details[0]["code"] == "NOT_A_CHOICE"
        assert ei.value.details[0]["field"] == "priority"

    def test_payload_assignees_membership(self, env):
        outsider = User.objects.create_user(email="g-out@rabbit.dev", password="Rabbit123!",
                                            display_name="外人")
        wf = _graph(env, guards=[{"type": "required_fields",
                                  "config": {"fields": ["assignees"]}}])
        issue = _mk_issue(env)
        with pytest.raises(TransitionError) as ei:
            WorkflowService().transition(
                issue_id=issue.id, to_state_id=env["states"]["进行中"].id, actor=env["owner"],
                transition_id=str(wf.transitions.get(name="开始").id),
                guard_payload={"assignees": [str(outsider.id)]})
        assert ei.value.details[0]["code"] == "DOES_NOT_EXIST"


# ── force 强制通道（BR-06）─────────────────────────────────────────
class TestForceChannel:
    """force 场景基座：「开始」边挂 estimate 守卫（min 30），任务 estimate=10 必被拦。"""

    def _setup(self, env, **kw):
        wf = _graph(env, guards=[{"type": "estimate_required",
                                  "config": {"min_minutes": 30}}])
        issue = _mk_issue(env, estimate_minutes=10, **kw)
        return wf, issue

    def test_force_requires_admin(self, env):
        wf, issue = self._setup(env)
        with pytest.raises(TransitionError) as ei:
            WorkflowService().transition(
                issue_id=issue.id, to_state_id=env["states"]["进行中"].id,
                actor=env["member"], transition_id=str(wf.transitions.get(name="开始").id),
                force=True, force_comment="x")
        assert ei.value.status == 403

    def test_force_requires_comment(self, env):
        wf, issue = self._setup(env)
        with pytest.raises(TransitionError) as ei:
            WorkflowService().transition(
                issue_id=issue.id, to_state_id=env["states"]["进行中"].id,
                actor=env["owner"], transition_id=str(wf.transitions.get(name="开始").id),
                force=True, force_comment=" ")
        assert ei.value.code == "VALIDATION_ERROR"
        assert ei.value.details[0]["field"] == "comment"

    def test_force_admin_with_comment(self, env):
        wf, issue = self._setup(env)
        WorkflowService().transition(
            issue_id=issue.id, to_state_id=env["states"]["进行中"].id,
            actor=env["owner"], transition_id=str(wf.transitions.get(name="开始").id),
            force=True, force_comment="风险已评估")
        issue.refresh_from_db()
        assert issue.state.name == "进行中"


# ── 字段锁定（§2.3/§4.4）──────────────────────────────────────────
class TestFieldLocks:
    def test_locked_field_patch_rejected(self, env):
        from rest_framework.test import APIClient


        _graph(env, locks=["target_date"])
        issue = _mk_issue(env, state=env["states"]["进行中"])
        c = APIClient()
        c.force_authenticate(env["member"])
        resp = c.patch(
            f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/issues/{issue.id}/",
            {"target_date": "2026-10-01"}, format="json")
        assert resp.status_code == 400  # AppException 经全局信封处理器转响应（不向调用方抛）
        assert resp.json()["error"]["details"][0]["code"] == "FIELD_LOCKED"

    def test_admin_bypass_and_unlock_on_transition(self, env):
        from rest_framework.test import APIClient

        wf = _graph(env, locks=["target_date"])
        issue = _mk_issue(env, state=env["states"]["进行中"])
        c = APIClient()
        c.force_authenticate(env["owner"])  # ADMIN 豁免
        resp = c.patch(
            f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/issues/{issue.id}/",
            {"target_date": "2026-10-01"}, format="json")
        assert resp.status_code == 200
        # 流转离开「进行中」→ 锁自动解除（读时派生）
        WorkflowService().transition(issue_id=issue.id, to_state_id=env["states"]["已完成"].id,
                                     actor=env["owner"],
                                     transition_id=str(wf.transitions.get(name="完成").id))
        issue.refresh_from_db()
        from plane.workflow.services import current_field_locks
        assert current_field_locks(issue) == []

    def test_detail_locked_fields(self, env):
        from rest_framework.test import APIClient

        _graph(env, locks=["target_date", "cf_none"])
        issue = _mk_issue(env, state=env["states"]["进行中"])
        c = APIClient()
        c.force_authenticate(env["member"])
        resp = c.get(
            f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/issues/{issue.id}/")
        assert resp.status_code == 200
        assert resp.json()["data"]["locked_fields"] == ["target_date", "cf_none"]


# ── PATCH state_id 旁路收口（ADR-0028 #5）──────────────────────────
class TestPatchStateBypass:
    def test_controlled_project_state_patch_rejected(self, env):
        from rest_framework.test import APIClient

        _graph(env)
        issue = _mk_issue(env)
        c = APIClient()
        c.force_authenticate(env["member"])
        resp = c.patch(
            f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/issues/{issue.id}/",
            {"state_id": str(env["states"]["已完成"].id)}, format="json")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "RESOURCE_STATE_INVALID"

    def test_free_project_state_patch_still_works(self, env):
        """无工作流项目 PATCH state_id 照旧（V1.0 零行为变化锚）。"""
        from rest_framework.test import APIClient

        issue = _mk_issue(env)
        c = APIClient()
        c.force_authenticate(env["member"])
        resp = c.patch(
            f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/issues/{issue.id}/",
            {"state_id": str(env["states"]["进行中"].id)}, format="json")
        assert resp.status_code == 200, resp.json()
        issue.refresh_from_db()
        assert issue.state.name == "进行中"


# ── available 预览（§4.6）──────────────────────────────────────────
class TestAvailablePreview:
    def test_blocked_by_and_deny_reason(self, env):
        from rest_framework.test import APIClient

        _graph(env, guards=[
            {"type": "required_fields", "config": {"fields": ["target_date"]}},
            {"type": "role_allowed", "config": {"roles": ["PROJ_ADMIN"]}}])
        issue = _mk_issue(env)
        c = APIClient()
        c.force_authenticate(env["member"])  # CONTRIBUTOR：role 不满足
        resp = c.get(
            f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/"
            f"issues/{issue.id}/transitions/available/")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["fallback"] is False
        edge = data["available"][0]
        assert edge["allowed"] is False
        assert edge["deny_reason"] == "PERM_TRANSITION_NOT_ALLOWED"
        assert {"type": "required_fields", "count": 1} in edge["blocked_by"]
        assert edge["requires_payload"] == ["target_date"]  # 配置态预聚合


# ── graph 保存守卫校验（BR-02/03/09）───────────────────────────────
class TestGuardConfigValidation:
    def test_engine_field_rejected(self, env):
        from plane.workflow.guards import guard_config_issues

        details = guard_config_issues(
            [{"type": "required_fields", "config": {"fields": ["parent"]}}], env["proj"])
        assert any(d["code"] == "INVALID" and "parent" in d["message"] for d in details)

    def test_unknown_cf_field_rejected(self, env):
        from plane.workflow.guards import guard_config_issues

        details = guard_config_issues(
            [{"type": "required_fields", "config": {"fields": ["cf_nope"]}}], env["proj"])
        assert any(d["code"] == "DOES_NOT_EXIST" for d in details)

    def test_guard_limit(self, env):
        from plane.workflow.guards import guard_config_issues

        guards = [{"type": "estimate_required", "config": {}} for _ in range(9)]
        details = guard_config_issues(guards, env["proj"])
        assert any(d["code"] == "LIMIT" for d in details)
