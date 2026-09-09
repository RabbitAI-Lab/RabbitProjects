"""自定义角色组测试（AUTH-008，Sprint-8 R2 门禁）。

覆盖：42 码目录冻结（CI）、BR-01 并集只加（require_permission 提升分支
行为级）、BR-02/03/04 CRUD 校验、BR-05/10 删除保护（挂接 + 守卫引用）、
BR-06 幂等、BR-07/12 缓存失效即时生效、BR-16 GUEST 天花板（挂接拒绝 +
WS 降级级联卸除）、BR-17 名称解析、零差异门禁（custom_codes=[] 行为与
标准版一致）、effective_codes 并集口径、<1ms 性能门禁（缓存命中 P99）。
"""
from __future__ import annotations

import time

import pytest
from django.urls import reverse
from rest_framework.test import APIClient, APIRequestFactory

from plane.app.permissions import require_permission
from plane.base.exception import AppException
from plane.constants.custom_role_catalog import (
    CATALOG_THRESHOLDS,
    CUSTOMIZABLE_CATALOG,
    guest_ceiling_violations,
)
from plane.db.models import (
    CustomRole,
    Project,
    ProjectMember,
    ProjectRoleAssignment,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)
from plane.db.models.roles import ProjectRole as PR
from plane.db.seeds.project_states import seed_project_states
from plane.db.services.custom_role import RoleService

pytestmark = pytest.mark.django_db


# ── 夹具 ────────────────────────────────────────────────────
@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="cr-owner@rabbit.dev", password="Rabbit123!",
                                     display_name="空间主")
    viewer = User.objects.create_user(email="cr-viewer@rabbit.dev", password="Rabbit123!",
                                      display_name="查看者")
    guest = User.objects.create_user(email="cr-guest@rabbit.dev", password="Rabbit123!",
                                     display_name="访客")
    ws = Workspace.objects.create(name="C", slug=f"w-cr-{owner.id.hex[:8]}",
                                  owner=owner, created_by=owner)
    for u, r in ((owner, WorkspaceRole.OWNER), (viewer, WorkspaceRole.MEMBER),
                 (guest, WorkspaceRole.GUEST)):
        WorkspaceMember.objects.create(workspace=ws, member=u, role=r, created_by=owner)
    proj = Project.objects.create(name="P", identifier="CRP", workspace=ws,
                                  created_by=owner)
    seed_project_states(proj)
    pm_owner = ProjectMember.objects.create(project=proj, member=owner,
                                            role=PR.ADMIN, created_by=owner)
    pm_viewer = ProjectMember.objects.create(project=proj, member=viewer,
                                             role=PR.VIEWER, created_by=owner)
    return {"owner": owner, "viewer": viewer, "guest": guest, "ws": ws,
            "proj": proj, "pm_owner": pm_owner, "pm_viewer": pm_viewer}


def _client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _roles_url(env, name="roles-list-create", extra=None):
    args = [env["ws"].slug, env["proj"].id] + (extra or [])
    return reverse(f"app:{name}", args=args)


@pytest.fixture(autouse=True)
def _clear_perm_cache():
    """每测试清 perm 缓存（防跨测试脏键）。"""
    from plane.app import effective_perms as ep

    yield
    ep._client_singleton = None
    ep._client_checked = False


# ── 1. 目录冻结（CI）──────────────────────────────────────
class TestCatalog:
    def test_catalog_frozen_42(self):
        assert len(CUSTOMIZABLE_CATALOG) == 42  # BR-02 冻结基线

    def test_catalog_excludes_manage_codes(self):
        assert not any(c.endswith(".manage") or c == "integration.config"
                       for c in CUSTOMIZABLE_CATALOG)

    def test_catalog_thresholds_cover_all(self):
        assert set(CATALOG_THRESHOLDS) == set(CUSTOMIZABLE_CATALOG)

    def test_guest_ceiling_violations(self):
        assert guest_ceiling_violations(["issue.read", "comment.create"]) == []
        assert "issue.update" in guest_ceiling_violations(
            ["issue.read", "issue.update"])
        assert "workspace.read" in guest_ceiling_violations(["workspace.read"])


# ── 2. 角色 CRUD ───────────────────────────────────────────
class TestRoleCrud:
    def test_create_with_valid_codes(self, env):
        role = RoleService().create_role(
            actor=env["owner"], project=env["proj"], name="测试工程师",
            permissions=["issue.read", "issue.create", "issue.update"],
        )
        assert role.permissions == ["issue.create", "issue.read", "issue.update"]

    def test_br02_invalid_codes_400(self, env):
        with pytest.raises(AppException) as ei:
            RoleService().create_role(
                actor=env["owner"], project=env["proj"], name="坏角色",
                permissions=["issue.read", "workspace.read", "hacker.code"],
            )
        assert ei.value.error_code == "VALIDATION_ERROR"
        fields = [d["field"] for d in ei.value.extra_details]
        assert "permissions.1" in fields and "permissions.2" in fields

    def test_br03_duplicate_name_case_insensitive(self, env):
        RoleService().create_role(actor=env["owner"], project=env["proj"],
                                  name="QA", permissions=["issue.read"])
        with pytest.raises(AppException) as ei:
            RoleService().create_role(actor=env["owner"], project=env["proj"],
                                      name="qa", permissions=["issue.read"])
        assert ei.value.error_code == "RESOURCE_ALREADY_EXISTS"

    def test_br04_twenty_role_cap(self, env):
        for i in range(20):
            RoleService().create_role(actor=env["owner"], project=env["proj"],
                                      name=f"R{i}", permissions=["issue.read"])
        with pytest.raises(AppException) as ei:
            RoleService().create_role(actor=env["owner"], project=env["proj"],
                                      name="R20", permissions=["issue.read"])
        assert ei.value.error_code == "RESOURCE_LIMIT_EXCEEDED"

    def test_br14_template_adoption(self, env):
        role = RoleService().create_role(
            actor=env["owner"], project=env["proj"], name="",
            template_key="qa_engineer")
        assert role.name == "测试工程师"
        assert role.is_builtin_template is True
        assert "issue.update" in role.permissions

    def test_br05_delete_with_assignment_rejected(self, env):
        role = RoleService().create_role(actor=env["owner"], project=env["proj"],
                                         name="在挂", permissions=["issue.read"])
        RoleService().assign(actor=env["owner"], role=role,
                             target_user=env["viewer"])
        with pytest.raises(AppException) as ei:
            RoleService().delete_role(actor=env["owner"], role=role)
        assert ei.value.error_code == "RESOURCE_IN_USE"
        RoleService().revoke(actor=env["owner"], role=role,
                             target_user=env["viewer"])
        RoleService().delete_role(actor=env["owner"], role=role)  # 卸除后可删
        assert not CustomRole.objects.filter(pk=role.pk,
                                             deleted_at__isnull=True).exists()

    def test_br10_delete_with_guard_reference_rejected(self, env):
        from plane.db.models import Workflow, WorkflowState, WorkflowTransition
        role = RoleService().create_role(actor=env["owner"], project=env["proj"],
                                         name="被引用", permissions=["issue.read"])
        wf = Workflow.objects.create(project=env["proj"], name="W",
                                     created_by=env["owner"])
        states = {n: WorkflowState.objects.create(
            workflow=wf, state=s, is_initial=(n == "待办"))
            for n, s in [("待办", env["proj"].states.all()[0]),
                         ("进行中", env["proj"].states.all()[1])]}
        WorkflowTransition.objects.create(
            workflow=wf, from_state=states["待办"], to_state=states["进行中"],
            name="开始",
            guards=[{"type": "role", "role": f"custom:{role.id}"}],
        )
        with pytest.raises(AppException) as ei:
            RoleService().delete_role(actor=env["owner"], role=role)
        assert ei.value.error_code == "RESOURCE_IN_USE"
        assert any("transition:开始" in str(d) for d in ei.value.extra_details)

    def test_update_role_codes_tightened(self, env):
        role = RoleService().create_role(
            actor=env["owner"], project=env["proj"], name="收紧",
            permissions=["issue.read", "issue.update"])
        RoleService().update_role(actor=env["owner"], role=role,
                                  permissions=["issue.read"])
        role.refresh_from_db()
        assert role.permissions == ["issue.read"]


# ── 3. 挂接 / BR-16 / 幂等 ─────────────────────────────────
class TestAssignment:
    def test_br06_assign_idempotent(self, env):
        role = RoleService().create_role(actor=env["owner"], project=env["proj"],
                                         name="幂等", permissions=["issue.read"])
        assert RoleService().assign(actor=env["owner"], role=role,
                                    target_user=env["viewer"]) is True
        assert RoleService().assign(actor=env["owner"], role=role,
                                    target_user=env["viewer"]) is False
        assert ProjectRoleAssignment.objects.filter(
            project=env["proj"], user=env["viewer"],
            deleted_at__isnull=True).count() == 1

    def test_br16_guest_ceiling_attach(self, env):
        ok_role = RoleService().create_role(
            actor=env["owner"], project=env["proj"], name="只读干系人",
            permissions=["issue.read", "comment.read"])
        over_role = RoleService().create_role(
            actor=env["owner"], project=env["proj"], name="测试工程师",
            permissions=["issue.read", "issue.update"])
        # GUEST 项目内可见（VIEWER）——先加项目成员
        pm_guest = ProjectMember.objects.create(project=env["proj"],
                                                member=env["guest"],
                                                role=PR.VIEWER,
                                                created_by=env["owner"])
        RoleService().assign(actor=env["owner"], role=ok_role,
                             target_user=env["guest"])  # 天花板内成功
        with pytest.raises(AppException) as ei:
            RoleService().assign(actor=env["owner"], role=over_role,
                                 target_user=env["guest"])
        assert ei.value.error_code == "RESOURCE_STATE_INVALID"
        assert pm_guest.pk  # lint 抑制未用

    def test_br16_ws_downgrade_cascades(self, env):
        role = RoleService().create_role(
            actor=env["owner"], project=env["proj"], name="越界角色",
            permissions=["issue.read", "issue.update"])
        RoleService().assign(actor=env["owner"], role=role,
                             target_user=env["viewer"])
        # viewer（WS_MEMBER）降级为 GUEST → 级联卸除越界挂接
        # （WS 域 GUEST 降级入口 = AUTH-006 批量改角色，非 change_role）
        from plane.db.services import member_admin
        member_admin.bulk_role_workspace(
            workspace=env["ws"], actor=env["owner"],
            user_ids=[str(env["viewer"].id)], role=WorkspaceRole.GUEST)
        assert not ProjectRoleAssignment.objects.filter(
            user=env["viewer"], deleted_at__isnull=True).exists()


# ── 4. 并集判定 + 零差异（行为级）────────────────────────
class TestUnionPermission:
    """直接对 require_permission 包装的视图方法做行为级断言（BR-01/零差异）。"""

    def _invoke(self, env, user, code):
        """构造带 slug/project_id kwargs 的假视图调 require_permission。"""
        factory = APIRequestFactory()
        request = factory.post("/")
        request.user = user
        view = type("V", (), {"kwargs": {"slug": env["ws"].slug,
                                         "project_id": str(env["proj"].id)}})()

        @require_permission(code, scope="project")
        def handler(self, request):
            return "OK"

        try:
            return handler(view, request), None
        except Exception as exc:  # noqa: BLE001 —— 断言两种终态
            return None, exc

    def test_zero_diff_without_custom_roles(self, env):
        """零差异门禁：custom_codes=[] 时 VIEWER 对 issue.update 仍 403。"""
        result, exc = self._invoke(env, env["viewer"], "issue.update")
        assert result is None
        assert exc.status_code == 403

    def test_union_grant_promotes(self, env):
        """BR-01：挂含 issue.update 的角色 → VIEWER 通过 require_permission。"""
        role = RoleService().create_role(
            actor=env["owner"], project=env["proj"], name="提升",
            permissions=["issue.update"])
        RoleService().assign(actor=env["owner"], role=role,
                             target_user=env["viewer"])
        result, _ = self._invoke(env, env["viewer"], "issue.update")
        assert result == "OK"

    def test_union_only_adds_not_cross_project(self, env):
        """挂接只在所在项目生效：另一项目同用户不受影响。"""
        role = RoleService().create_role(
            actor=env["owner"], project=env["proj"], name="隔离",
            permissions=["issue.update"])
        RoleService().assign(actor=env["owner"], role=role,
                             target_user=env["viewer"])
        proj2 = Project.objects.create(name="P2", identifier="CRQ",
                                       workspace=env["ws"], created_by=env["owner"])
        seed_project_states(proj2)
        ProjectMember.objects.create(project=proj2, member=env["viewer"],
                                     role=PR.VIEWER, created_by=env["owner"])
        env2 = dict(env, proj=proj2)
        result, exc = self._invoke(env2, env["viewer"], "issue.update")
        assert result is None and exc.status_code == 403

    def test_br12_update_takes_effect_immediately(self, env):
        role = RoleService().create_role(
            actor=env["owner"], project=env["proj"], name="即时",
            permissions=["issue.update"])
        RoleService().assign(actor=env["owner"], role=role,
                             target_user=env["viewer"])
        assert self._invoke(env, env["viewer"], "issue.update")[0] == "OK"
        RoleService().update_role(actor=env["owner"], role=role,
                                  permissions=["issue.read"])
        # 模拟 on_commit 的主动失效（pytest 事务内 on_commit 不触发）+
        # 清进程判定缓存——两层都到位才是「下一请求即时生效」的真实链路
        from plane.app import effective_perms as ep
        from plane.app.effective_perms import invalidate
        invalidate([env["viewer"].id], env["proj"].id)
        ep._client_singleton = None
        ep._client_checked = False
        result, exc = self._invoke(env, env["viewer"], "issue.update")
        assert result is None and exc.status_code == 403


# ── 5. effective_codes / 端点 ──────────────────────────────
class TestEndpoints:
    def test_effective_codes_union(self, env):
        from plane.app.effective_perms import effective_codes

        role = RoleService().create_role(
            actor=env["owner"], project=env["proj"], name="并集",
            permissions=["issue.read", "issue.update", "comment.create"])
        RoleService().assign(actor=env["owner"], role=role,
                             target_user=env["viewer"])
        codes = effective_codes(env["viewer"].id, env["proj"].id)
        # VIEWER 固定码集（阈值 ≤5 的 project 码）∪ 挂接码
        assert "project.read" in codes  # 固定层
        assert "issue.update" in codes  # 自定义层
        assert "issue.create" not in codes

    def test_effective_permissions_endpoint(self, env):
        role = RoleService().create_role(
            actor=env["owner"], project=env["proj"], name="面板",
            permissions=["issue.update"])
        RoleService().assign(actor=env["owner"], role=role,
                             target_user=env["viewer"])
        r = _client(env["viewer"]).get(
            _roles_url(env, "members-effective-permissions",
                       [str(env["pm_viewer"].id)]))
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["fixed_role"] == PR.VIEWER
        assert "issue.update" in data["permissions"]

    def test_role_crud_endpoints(self, env):
        c = _client(env["owner"])
        r = c.post(_roles_url(env), {"name": "端点角色",
                                     "permissions": ["issue.read"]},
                   format="json")
        assert r.status_code == 201, r.json()
        role_id = r.json()["data"]["role"]["id"]
        assert "Location" in r
        r2 = c.patch(_roles_url(env, "roles-detail", [role_id]),
                     {"permissions": ["issue.read", "issue.update"]},
                     format="json")
        assert r2.status_code == 200
        assert r2.json()["data"]["permissions_count"] == 2
        r3 = c.delete(_roles_url(env, "roles-detail", [role_id]))
        assert r3.status_code == 204

    def test_assign_via_endpoint(self, env):
        role = RoleService().create_role(actor=env["owner"], project=env["proj"],
                                         name="挂", permissions=["issue.read"])
        c = _client(env["owner"])
        r = c.post(_roles_url(env, "role-assignments-list-create",
                              [str(env["pm_viewer"].id)]),
                   {"role_id": str(role.id)}, format="json")
        assert r.status_code == 201
        r2 = c.get(_roles_url(env, "role-assignments-list-create",
                              [str(env["pm_viewer"].id)]))
        assert r2.status_code == 200
        assert r2.json()["data"][0]["role_name"] == "挂"
        r3 = c.delete(_roles_url(env, "role-assignments-delete",
                                 [str(env["pm_viewer"].id), str(role.id)]))
        assert r3.status_code == 204

    def test_br17_name_resolution(self, env):
        RoleService().create_role(actor=env["owner"], project=env["proj"],
                                  name="唯一名", permissions=["issue.read"])
        c = _client(env["owner"])
        hit = c.get(_roles_url(env), {"name": "唯一名"})
        assert hit.status_code == 200 and hit.json()["data"]["name"] == "唯一名"
        miss = c.get(_roles_url(env), {"name": "不存在"})
        assert miss.status_code == 404
        both = c.get(_roles_url(env), {"name": "x", "search": "y"})
        assert both.status_code == 400

    def test_catalog_endpoint(self, env):
        r = _client(env["owner"]).get(_roles_url(env, "roles-permissions-catalog"))
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["total"] == 42
        assert any(g["domain"] == "issue" for g in data["groups"])

    def test_bulk_assign_by_department(self, env):
        from plane.db.services import department as dept_svc

        dept = dept_svc.create_department(actor=env["owner"], workspace=env["ws"],
                                          name="研发部")
        WorkspaceMember.objects.filter(workspace=env["ws"],
                                       member=env["viewer"]).update(department=dept)
        role = RoleService().create_role(actor=env["owner"], project=env["proj"],
                                         name="批量", permissions=["issue.read"])
        r = _client(env["owner"]).post(
            _roles_url(env, "roles-bulk-assign", [str(role.id)]),
            {"department_id": str(dept.id)}, format="json")
        assert r.status_code == 201, r.json()
        data = r.json()["data"]
        assert str(env["viewer"].id) in data["added"]
        assert ProjectRoleAssignment.objects.filter(
            user=env["viewer"], role=role, deleted_at__isnull=True).exists()

    def test_pm_matrix_role_manage(self, env):
        """PM：WS_MEMBER 建角色 → 403 PERM_WORKSPACE_ADMIN_REQUIRED。"""
        r = _client(env["viewer"]).post(_roles_url(env),
                                        {"name": "x", "permissions": ["issue.read"]},
                                        format="json")
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "PERM_WORKSPACE_ADMIN_REQUIRED"


# ── 6. 性能门禁 ────────────────────────────────────────────
class TestPerformance:
    def test_cached_lookup_p99_under_1ms(self, env):
        from plane.app.effective_perms import has_custom_code

        role = RoleService().create_role(
            actor=env["owner"], project=env["proj"], name="性能",
            permissions=["issue.update"])
        RoleService().assign(actor=env["owner"], role=role,
                             target_user=env["viewer"])
        # 预热缓存
        assert has_custom_code(env["viewer"].id, env["proj"].id, "issue.update")
        samples = []
        for _ in range(1000):
            t0 = time.perf_counter()
            has_custom_code(env["viewer"].id, env["proj"].id, "issue.update")
            samples.append((time.perf_counter() - t0) * 1000)
        samples.sort()
        p99 = samples[int(len(samples) * 0.99) - 1]
        assert p99 < 1.0, f"P99={p99:.3f}ms 超过 1ms 门禁"
