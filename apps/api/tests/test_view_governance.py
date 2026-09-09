"""视图治理测试（BOARD-005，Sprint-8 R5 门禁）。

覆盖：三形态状态机（共享/锁定/默认）、BR-02 锁定拦截优先、BR-03 删除
保护 + 订阅级联、BR-04 默认原子替换、BR-05 先锁后默认、BR-06 副本、
BR-14 内置限制、BR-15 两步解锁、BR-16 订阅计数口径、新成员自动订阅、
移出级联、shared 应用面放开（个人他人仍 404）、二维矩阵（计数对账/
同维拒绝/降级）、未配置行为零差异（P2 一致）。
"""
from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from plane.base.exception import AppException
from plane.db.models import (
    Issue,
    IssueView,
    Project,
    ProjectMember,
    ProjectRole,
    User,
    UserViewPreference,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.roles import WorkspaceRole
from plane.db.seeds.project_states import seed_project_states
from plane.db.services import view_governance as vg

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="vg-owner@rabbit.dev",
                                     password="Rabbit123!", display_name="主")
    member = User.objects.create_user(email="vg-member@rabbit.dev",
                                      password="Rabbit123!", display_name="员")
    ws = Workspace.objects.create(name="V", slug=f"w-vg-{owner.id.hex[:8]}",
                                  owner=owner, created_by=owner)
    for u, r in ((owner, WorkspaceRole.OWNER), (member, WorkspaceRole.MEMBER)):
        WorkspaceMember.objects.create(workspace=ws, member=u, role=r,
                                       created_by=owner)
    proj = Project.objects.create(name="P", identifier="VGP", workspace=ws,
                                  created_by=owner)
    seed_project_states(proj)
    ProjectMember.objects.create(project=proj, member=owner,
                                 role=ProjectRole.ADMIN, created_by=owner)
    ProjectMember.objects.create(project=proj, member=member,
                                 role=ProjectRole.VIEWER, created_by=owner)
    return {"owner": owner, "member": member, "ws": ws, "proj": proj}


def _mk_view(env, name="我的视图", owner=None, **kw) -> IssueView:
    kw.setdefault("filters", {"op": "AND", "conditions": []})
    kw.setdefault("display_props", {})
    return IssueView.objects.create(
        workspace=env["ws"], project=env["proj"],
        owner=owner or env["member"], name=name,
        created_by=owner or env["member"], **kw)


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _base(env) -> str:
    return f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/views/"


# ── 状态机与 BR ───────────────────────────────────────────
class TestGovernance:
    def test_share_then_lock_then_default(self, env):
        v = _mk_view(env)
        # 共享（owner 本人）
        vg.share_view(actor=env["member"], view=v, access="shared")
        v.refresh_from_db()
        assert v.access == "shared"
        assert vg.subscriber_count(v) == 0  # 共享不加订阅（BR-16）
        # 锁定 + 设默认（admin）
        vg.lock_view(actor=env["owner"], view=v, is_locked=True,
                     is_project_default=True)
        v.refresh_from_db()
        assert v.is_locked and v.is_project_default
        assert v.locked_by == env["owner"] and v.locked_at is not None
        # 存量成员（owner+member）批量订阅（BR-16 +1）
        assert vg.subscriber_count(v) == 2

    def test_br02_lock_interception_precedes_permissions(self, env):
        v = _mk_view(env, owner=env["member"])
        vg.lock_view(actor=env["owner"], view=v, is_locked=True,
                     is_project_default=None)
        # owner 本人（也是 board.lock 持有者）改锁定视图 → 仍 409 RESOURCE_LOCKED
        r = _client(env["member"]).patch(f"{_base(env)}{v.id}/",
                                         {"name": "改名"}, format="json")
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "RESOURCE_LOCKED"

    def test_br03_locked_delete_rejected(self, env):
        v = _mk_view(env)
        vg.lock_view(actor=env["owner"], view=v, is_locked=True,
                     is_project_default=None)
        r = _client(env["owner"]).delete(f"{_base(env)}{v.id}/")
        assert r.status_code == 409
        # 解锁后可删
        vg.lock_view(actor=env["owner"], view=v, is_locked=False,
                     is_project_default=False)
        assert _client(env["owner"]).delete(
            f"{_base(env)}{v.id}/").status_code == 204

    def test_br04_default_atomic_replacement(self, env):
        v1 = _mk_view(env, "标准一")
        v2 = _mk_view(env, "标准二")
        for v in (v1, v2):
            vg.share_view(actor=env["owner"], view=v, access="shared")
            vg.lock_view(actor=env["owner"], view=v, is_locked=True,
                         is_project_default=True)
        v1.refresh_from_db()
        assert not v1.is_project_default  # 原子替换
        assert IssueView.objects.filter(project=env["proj"],
                                        is_project_default=True).count() == 1

    def test_br05_default_requires_locked(self, env):
        v = _mk_view(env)
        with pytest.raises(AppException) as ei:
            vg.lock_view(actor=env["owner"], view=v, is_locked=False,
                         is_project_default=True)
        assert ei.value.error_code == "VALIDATION_ERROR"

    def test_br06_duplicate_personal_fork(self, env):
        v = _mk_view(env, "复杂视图", filters={"op": "AND",
                                               "conditions": [{"field": "priority",
                                                               "operator": "eq",
                                                               "value": "urgent"}]})
        vg.share_view(actor=env["member"], view=v, access="shared")
        fork = vg.duplicate_view(actor=env["owner"], view=v)
        assert fork.access == "personal" and fork.owner == env["owner"]
        assert fork.name == "复杂视图（副本）"
        assert fork.filters == v.filters and fork.filters is not v.filters

    def test_br14_system_view_restricted(self, env):
        v = _mk_view(env, "内置", owner=env["owner"], is_system=True)
        with pytest.raises(AppException):
            vg.share_view(actor=env["owner"], view=v, access="shared")
        with pytest.raises(AppException):
            vg.lock_view(actor=env["owner"], view=v, is_locked=True,
                         is_project_default=None)

    def test_br15_unlock_two_step(self, env):
        v = _mk_view(env)
        vg.share_view(actor=env["member"], view=v, access="shared")
        vg.lock_view(actor=env["owner"], view=v, is_locked=True,
                     is_project_default=True)
        with pytest.raises(AppException) as ei:
            vg.lock_view(actor=env["owner"], view=v, is_locked=False,
                         is_project_default=None)
        assert ei.value.error_code == "RESOURCE_STATE_INVALID"
        # 两步：先取消默认 → 再解锁
        vg.lock_view(actor=env["owner"], view=v, is_locked=True,
                     is_project_default=False)
        vg.lock_view(actor=env["owner"], view=v, is_locked=False,
                     is_project_default=False)

    def test_br16_unshare_cascades_subscription_soft_delete(self, env):
        v = _mk_view(env)
        vg.share_view(actor=env["member"], view=v, access="shared")
        vg.pin_view(actor=env["owner"], view=v)
        assert vg.subscriber_count(v) == 1
        vg.share_view(actor=env["member"], view=v, access="personal")  # 收回
        assert vg.subscriber_count(v) == 0

    def test_pin_only_shared(self, env):
        v = _mk_view(env)  # personal
        with pytest.raises(AppException) as ei:
            vg.pin_view(actor=env["owner"], view=v)
        assert ei.value.error_code == "VALIDATION_ERROR"

    def test_new_member_auto_subscribe(self, env):
        v = _mk_view(env)
        vg.share_view(actor=env["member"], view=v, access="shared")
        vg.lock_view(actor=env["owner"], view=v, is_locked=True,
                     is_project_default=True)
        # 新成员加入 → 自动订阅
        newbie = User.objects.create_user(email="vg-new@rabbit.dev",
                                          password="x", display_name="新")
        WorkspaceMember.objects.create(workspace=env["ws"], member=newbie,
                                       role=WorkspaceRole.MEMBER,
                                       created_by=env["owner"])
        from django.test.testcases import TestCase

        from plane.db.services.project_member import ProjectMemberService
        with TestCase.captureOnCommitCallbacks(execute=True):
            ProjectMemberService().add_members(
                project=env["proj"], actor=env["owner"],
                member_ids=[str(newbie.id)], role=ProjectRole.VIEWER)
        assert UserViewPreference.objects.filter(
            user=newbie, view=v, pinned=True,
            deleted_at__isnull=True).exists()

    def test_remove_member_cascades_preferences(self, env):
        v = _mk_view(env)
        vg.share_view(actor=env["member"], view=v, access="shared")
        vg.pin_view(actor=env["member"], view=v)
        pm = ProjectMember.objects.get(project=env["proj"],
                                       member=env["member"])
        from plane.db.services.project_member import ProjectMemberService
        ProjectMemberService().remove_member(project=env["proj"], member=pm,
                                             actor=env["owner"])
        assert not UserViewPreference.objects.filter(
            user=env["member"], deleted_at__isnull=True).exists()


# ── 端点 ──────────────────────────────────────────────────
class TestEndpoints:
    def test_lock_endpoint_permissions(self, env):
        v = _mk_view(env)
        # VIEWER 成员锁 → 403（board.lock）
        r = _client(env["member"]).post(
            f"{_base(env)}{v.id}/lock/",
            {"is_locked": True, "is_project_default": False}, format="json")
        assert r.status_code == 403
        # ADMIN 锁 → 200
        r2 = _client(env["owner"]).post(
            f"{_base(env)}{v.id}/lock/",
            {"is_locked": True, "is_project_default": False}, format="json")
        assert r2.status_code == 200
        assert r2.json()["data"]["locked_by"]["id"] == str(env["owner"].id)

    def test_duplicate_and_pin_endpoints(self, env):
        v = _mk_view(env, "协作视图")
        vg.share_view(actor=env["member"], view=v, access="shared")
        r = _client(env["owner"]).post(f"{_base(env)}{v.id}/duplicate/")
        assert r.status_code == 201
        assert r.json()["data"]["name"] == "协作视图（副本）"
        r2 = _client(env["owner"]).post(f"{_base(env)}{v.id}/pin/")
        assert r2.status_code == 200
        prefs = _client(env["owner"]).get(
            f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/"
            f"views/preferences/")
        assert prefs.status_code == 200
        assert any(p["view_id"] == str(v.id) for p in prefs.json()["data"])

    def test_lock_endpoint_br15_response(self, env):
        v = _mk_view(env)
        vg.share_view(actor=env["member"], view=v, access="shared")
        _client(env["owner"]).post(f"{_base(env)}{v.id}/lock/",
                                   {"is_locked": True,
                                    "is_project_default": True}, format="json")
        r = _client(env["owner"]).post(
            f"{_base(env)}{v.id}/lock/", {"is_locked": False}, format="json")
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "RESOURCE_STATE_INVALID"


# ── 应用面：shared 放开 / 零差异 ─────────────────────────
class TestApplicationSurface:
    def _mk_issues(self, env, n=4):
        state = env["proj"].states.first()
        issues = []
        for i in range(n):
            issue = Issue.objects.create(
                project=env["proj"], name=f"任务{i}",
                state=state, created_by=env["owner"])
            issues.append(issue)
        return issues

    def test_shared_view_visible_to_all_members(self, env):
        self._mk_issues(env)
        v = _mk_view(env, "共享高优", filters={
            "op": "AND",
            "conditions": [{"field": "name", "operator": "contains",
                            "value": "任务"}]})
        # 未共享：他人用 view_id → 404（P2 行为保持）
        r = _client(env["owner"]).get(
            f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/"
            f"issues/", {"view_id": str(v.id)})
        assert r.status_code == 404
        # 共享后：全员可用（BR-01 放开）
        vg.share_view(actor=env["member"], view=v, access="shared")
        r2 = _client(env["owner"]).get(
            f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/"
            f"issues/", {"view_id": str(v.id)})
        assert r2.status_code == 200

    def test_zero_diff_personal_views_unchanged(self, env):
        """零差异门禁（§7.2.6）：未共享/未锁定时行为与 P2 完全一致。"""
        self._mk_issues(env)
        v = _mk_view(env)  # 本人 personal 视图
        r = _client(env["member"]).get(
            f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/"
            f"issues/", {"view_id": str(v.id)})
        assert r.status_code == 200  # 本人可用（P2 原行为）
        # PATCH 本人视图照常（无锁定拦截路径）
        r2 = _client(env["member"]).patch(f"{_base(env)}{v.id}/",
                                          {"name": "改名"}, format="json")
        assert r2.status_code == 200

    def test_matrix_response_and_counts(self, env):
        issues = self._mk_issues(env, 6)
        states = list(env["proj"].states.all())
        # 分两状态 + 两负责人
        issues[0].state, issues[1].state = states[0], states[0]
        issues[2].state, issues[3].state = states[1], states[1]
        for i in issues:
            i.save()
        issues[0].assignees.add(env["owner"])
        issues[2].assignees.add(env["owner"])
        issues[4].assignees.add(env["member"])
        r = _client(env["owner"]).get(
            f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/"
            f"issues/",
            {"group_by": "state_id", "sub_group_by": "assignee_id"})
        assert r.status_code == 200, r.json()
        body = r.json()
        assert body["meta"]["sub_grouped_by"] == "assignee_id"
        cells = {(c["col"], c["row"]): c["count"] for c in body["data"]["matrix"]}
        assert sum(cells.values()) == 6  # 对账恒等式（§7.2）
        assert cells[(str(states[0].id), str(env["owner"].id))] == 1

    def test_matrix_same_dimension_rejected(self, env):
        r = _client(env["owner"]).get(
            f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/"
            f"issues/",
            {"group_by": "state_id", "sub_group_by": "state_id"})
        assert r.status_code == 400  # BR-07
