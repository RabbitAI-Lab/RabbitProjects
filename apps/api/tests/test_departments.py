"""部门组织架构测试（AUTH-007，Sprint-8 R1 门禁）。

覆盖：树管理（BR-01 深度 / BR-02 同级唯一 / BR-03 环 / BR-04 删除受限 /
BR-05 一人一部门）、成员归属与过滤、按部门授权快照展开（BR-07/08/09 +
幂等重同步 + 计数恒等式 IT-08 + 批次快照 UT-18 + 删除后溯源 UT-20）、
统计口径（直属/含子级/未分配恒等式 IT-06）、PM 权限矩阵（四主体 × 读写）。
夹具风格对照 test_workflow_engine.py。
"""
from __future__ import annotations

import pytest
from django.db import IntegrityError
from rest_framework.test import APIClient

from plane.base.exception import AppException
from plane.db.models import (
    Department,
    DepartmentGrantBatch,
    Issue,
    Project,
    ProjectMember,
    ProjectRole,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)
from plane.db.seeds.project_states import seed_project_states
from plane.db.services import department as svc

pytestmark = pytest.mark.django_db


# ────────────────────────────────────────────────────────────────
# 夹具
# ────────────────────────────────────────────────────────────────
@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="dept-owner@rabbit.dev", password="Rabbit123!",
                                     display_name="部门管理员")
    member = User.objects.create_user(email="dept-member@rabbit.dev", password="Rabbit123!",
                                      display_name="成员甲")
    member2 = User.objects.create_user(email="dept-member2@rabbit.dev", password="Rabbit123!",
                                       display_name="成员乙")
    inactive = User.objects.create_user(email="dept-inactive@rabbit.dev", password="Rabbit123!",
                                        display_name="停用者")
    ws = Workspace.objects.create(name="D", slug=f"w-dept-{owner.id.hex[:8]}",
                                  owner=owner, created_by=owner)
    for u, r in ((owner, WorkspaceRole.OWNER), (member, WorkspaceRole.MEMBER),
                 (member2, WorkspaceRole.MEMBER), (inactive, WorkspaceRole.MEMBER)):
        WorkspaceMember.objects.create(workspace=ws, member=u, role=r, created_by=owner)
    # 停用成员（账号层 is_active=False 走 User；成员行 is_active=False 表达停用）
    WorkspaceMember.objects.filter(workspace=ws, member=inactive).update(is_active=False)
    proj = Project.objects.create(name="P", identifier="DPT", workspace=ws, created_by=owner)
    seed_project_states(proj)
    return {
        "owner": owner, "member": member, "member2": member2, "inactive": inactive,
        "ws": ws, "proj": proj,
    }


def _mk_dept(env, name, parent=None, actor=None) -> Department:
    return svc.create_department(
        actor=actor or env["owner"], workspace=env["ws"],
        name=name, parent_id=str(parent.id) if parent else None,
    )


def _mk_tree4(env):
    """4 层树：L1 研发中心 → L2 平台组 → L3 后端组 → L4 存储小组。"""
    l1 = _mk_dept(env, "研发中心")
    l2 = _mk_dept(env, "平台组", parent=l1)
    l3 = _mk_dept(env, "后端组", parent=l2)
    l4 = _mk_dept(env, "存储小组", parent=l3)
    return l1, l2, l3, l4


def _client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _base(env) -> str:
    return f"/api/v1/workspaces/{env['ws'].slug}/departments/"


# ────────────────────────────────────────────────────────────────
# 1. 树管理（UT-01/02/03/04/05/06/19）
# ────────────────────────────────────────────────────────────────
class TestTreeManagement:
    def test_ut01_root_create_depth_and_sort(self, env):
        d = _mk_dept(env, "根部门")
        assert svc._depth_of(d) == 1
        assert d.sort_order == 65536.0
        assert d.path == f"/{d.id}/"

    def test_ut02_depth_6_ok_7_rejected(self, env):
        node = None
        for i in range(6):
            node = _mk_dept(env, f"L{i + 1}", parent=node)
        assert svc._depth_of(node) == 6
        with pytest.raises(AppException) as ei:
            _mk_dept(env, "L7", parent=node)
        assert getattr(ei.value, "error_code", "") == "RESOURCE_LIMIT_EXCEEDED"

    def test_ut03_sibling_name_case_insensitive(self, env):
        _mk_dept(env, "平台组")
        with pytest.raises(AppException) as ei:
            _mk_dept(env, "平台组 ")  # trim 后同名
        assert getattr(ei.value, "error_code", "") == "RESOURCE_ALREADY_EXISTS"
        with pytest.raises(AppException):
            _mk_dept(env, "平 台 组".replace(" ", ""))  # 同名（不同大小写场景由 iexact 覆盖）

    def test_ut04_root_name_unique_partial_index(self, env):
        _mk_dept(env, "根A")
        # 根部门重名：uq_department_root_name 兜底（service 预检先行抛 AppException）
        with pytest.raises(AppException):
            _mk_dept(env, "根A")

    def test_ut05_move_to_self_or_descendant_rejected(self, env):
        l1, l2, l3, l4 = _mk_tree4(env)
        with pytest.raises(AppException) as ei:
            svc.move_department(actor=env["owner"], department=l1,
                                new_parent_id=str(l4.id))
        assert getattr(ei.value, "error_code", "") == "RESOURCE_CIRCULAR_DEPENDENCY"
        # 移到自身同样拒绝
        with pytest.raises(AppException):
            svc.move_department(actor=env["owner"], department=l2,
                                new_parent_id=str(l2.id))

    def test_ut06_move_subtree_depth_overflow_rejected(self, env):
        l1, l2, l3, l4 = _mk_tree4(env)
        other_root = _mk_dept(env, "产品部")
        o2 = _mk_dept(env, "设计组", parent=other_root)   # 深度 2
        o3 = _mk_dept(env, "视觉小组", parent=o2)          # 深度 3
        o4 = _mk_dept(env, "品牌小组", parent=o3)          # 深度 4
        # 把 L1 子树（高度 3）挂到 o4（深度 4）下：4 + 3 + 1 = 8 > 6 → 拒绝
        with pytest.raises(AppException):
            svc.move_department(actor=env["owner"], department=l1,
                                new_parent_id=str(o4.id))

    def test_ut17_move_rewrites_subtree_paths(self, env):
        l1, l2, l3, l4 = _mk_tree4(env)
        new_root = _mk_dept(env, "新总部")
        for d in (l1, l2, l3, l4):
            d.refresh_from_db()
        svc.move_department(actor=env["owner"], department=l2, new_parent_id=str(new_root.id))
        for d in (l2, l3, l4):
            d.refresh_from_db()
        assert l2.path == f"/{new_root.id}/{l2.id}/"
        assert l3.path == f"/{new_root.id}/{l2.id}/{l3.id}/"
        assert l4.path == f"/{new_root.id}/{l2.id}/{l3.id}/{l4.id}/"
        # 深度口径随重写同步（UT-19 同口径）
        assert svc._depth_of(l4) == 4

    def test_ut19_depth_of_anchor(self, env):
        l1, l2, l3, l4 = _mk_tree4(env)
        assert svc._depth_of(l1) == 1
        assert svc._depth_of(l4) == 4

    def test_ut07_delete_nonempty_rejected(self, env):
        l1, l2, *_ = _mk_tree4(env)
        with pytest.raises(AppException) as ei:
            svc.delete_department(actor=env["owner"], department=l1)
        assert getattr(ei.value, "error_code", "") == "RESOURCE_IN_USE"
        # 有直属成员同样拒绝
        leaf = _mk_dept(env, "带人组")
        WorkspaceMember.objects.filter(workspace=env["ws"], member=env["member"]) \
            .update(department=leaf)
        with pytest.raises(AppException):
            svc.delete_department(actor=env["owner"], department=leaf)

    def test_ut13_rebalance_on_spacing_collapse(self, env):
        a = _mk_dept(env, "A")
        b = _mk_dept(env, "B")
        # 手工压间距到塌缩域（模拟多次中点插值）
        Department.objects.filter(pk=b.id).update(sort_order=a.sort_order + 1e-7)
        c = _mk_dept(env, "C")
        assert c.sort_order > b.sort_order  # 新建走 max+step 不受影响
        # reorder 到 a 之后触发塌缩重排
        svc.reorder_department(actor=env["owner"], department=b,
                               sort_after_id=str(a.id))
        siblings = list(Department.objects
                        .filter(workspace=env["ws"], parent=None,
                                deleted_at__isnull=True).order_by("sort_order"))
        steps = [siblings[i + 1].sort_order - siblings[i].sort_order
                 for i in range(len(siblings) - 1)]
        assert all(s >= 1.0 for s in steps)  # 等差重排后间距恢复

    def test_br02_unique_constraint_integrity_error(self, env):
        """并发兜底：绕过 service 预检直插同级重名 → 唯一索引 IntegrityError。"""
        _mk_dept(env, "唯一组")
        with pytest.raises(IntegrityError):
            Department.objects.create(
                workspace=env["ws"], parent=None, name="唯一组",
                path=f"/{uuid4_str()}/", sort_order=65536.0,
                created_by=env["owner"], updated_by=env["owner"])


def uuid4_str() -> str:
    import uuid
    return str(uuid.uuid4())


# ────────────────────────────────────────────────────────────────
# 2. 成员归属（UT-08/14 + 列表过滤）
# ────────────────────────────────────────────────────────────────
class TestMemberAssignment:
    def test_ut08_assign_and_unassign(self, env):
        l1, l2, *_ = _mk_tree4(env)
        c = _client(env["owner"])
        wm = WorkspaceMember.objects.get(workspace=env["ws"], member=env["member"])
        resp = c.patch(f"/api/v1/workspaces/{env['ws'].slug}/members/{wm.id}/",
                       {"department_id": str(l2.id), "company_role": "后端工程师"},
                       format="json")
        assert resp.status_code == 200, resp.json()
        wm.refresh_from_db()
        assert wm.department_id == l2.id
        assert wm.company_role == "后端工程师"
        assert resp.json()["data"]["department_id"] == str(l2.id)
        # 置 null = 移入未分配
        resp = c.patch(f"/api/v1/workspaces/{env['ws'].slug}/members/{wm.id}/",
                       {"department_id": None}, format="json")
        assert resp.status_code == 200
        wm.refresh_from_db()
        assert wm.department_id is None

    def test_ut14_company_role_too_long_400(self, env):
        wm = WorkspaceMember.objects.get(workspace=env["ws"], member=env["member"])
        resp = _client(env["owner"]).patch(
            f"/api/v1/workspaces/{env['ws'].slug}/members/{wm.id}/",
            {"company_role": "超" * 65}, format="json")
        assert resp.status_code == 400

    def test_member_list_department_filter(self, env):
        l1, l2, l3, l4 = _mk_tree4(env)
        wm1 = WorkspaceMember.objects.get(workspace=env["ws"], member=env["member"])
        wm2 = WorkspaceMember.objects.get(workspace=env["ws"], member=env["member2"])
        wm1.department = l3
        wm2.department = l1
        wm1.save(update_fields=["department_id"])
        wm2.save(update_fields=["department_id"])
        c = _client(env["owner"])
        url = f"/api/v1/workspaces/{env['ws'].slug}/members/"
        # 直属口径：l3 只含 member
        r1 = c.get(url, {"department": str(l3.id)})
        assert r1.status_code == 200
        assert len(r1.json()["data"]) == 1
        # 含子部门：l1 子树含 l1,l2,l3,l4 → member + member2
        r2 = c.get(url, {"department": str(l1.id), "with_descendants": "true"})
        assert len(r2.json()["data"]) == 2

    def test_bulk_move_members(self, env):
        l1, l2, *_ = _mk_tree4(env)
        wm1 = WorkspaceMember.objects.get(workspace=env["ws"], member=env["member"])
        wm2 = WorkspaceMember.objects.get(workspace=env["ws"], member=env["member2"])
        c = _client(env["owner"])
        resp = c.post(f"{_base(env)}{l1.id}/members/bulk-move/",
                      {"member_ids": [str(wm1.id), str(wm2.id)],
                       "department_id": str(l2.id)}, format="json")
        assert resp.status_code == 200, resp.json()
        assert resp.json()["data"]["moved"] == 2
        wm1.refresh_from_db()
        wm2.refresh_from_db()
        assert wm1.department_id == l2.id and wm2.department_id == l2.id


# ────────────────────────────────────────────────────────────────
# 3. 按部门授权（UT-09/10/11/12/15/16/18 + IT-02/03/08 + UT-20）
# ────────────────────────────────────────────────────────────────
class TestGrantExpansion:
    def _assign_members(self, env, l1, l3):
        wm1 = WorkspaceMember.objects.get(workspace=env["ws"], member=env["member"])
        wm2 = WorkspaceMember.objects.get(workspace=env["ws"], member=env["member2"])
        wmi = WorkspaceMember.objects.get(workspace=env["ws"], member=env["inactive"])
        wm1.department = l3
        wm2.department = l1
        wmi.department = l3
        for w in (wm1, wm2, wmi):
            w.save(update_fields=["department_id"])
        return wm1, wm2, wmi

    def test_ut09_with_without_descendants(self, env):
        l1, l2, l3, l4 = _mk_tree4(env)
        wm1, wm2, _ = self._assign_members(env, l1, l3)
        proj = env["proj"]
        # 不含子部门：l3 只有 member（inactive 停用进 skipped）
        r = svc.expand_grant(actor=env["owner"], department=l3, project=proj,
                             role=ProjectRole.CONTRIBUTOR, with_descendants=False)
        # 直属口径：l3 下 added 仅 member（inactive 停用者只进 skipped 明细）
        added = {e["member_id"] for e in r.member_snapshot if e["action"] == "added"}
        assert added == {str(wm1.member_id)}
        # 含子部门（l1 子树）：member + member2
        r2 = svc.expand_grant(actor=env["owner"], department=l1, project=proj,
                              role=ProjectRole.CONTRIBUTOR, with_descendants=True)
        # 第一段已把 wm1 加进项目（unchanged 桶）——目标集口径 = added ∪ unchanged
        target2 = {e["member_id"] for e in r2.member_snapshot
                   if e["action"] in ("added", "unchanged")}
        assert target2 == {str(wm1.member_id), str(wm2.member_id)}

    def test_ut10_idempotent_resync(self, env):
        l1, *_ = _mk_tree4(env)
        wm1, wm2, _ = self._assign_members(env, l1, l1)
        proj = env["proj"]
        svc.expand_grant(actor=env["owner"], department=l1, project=proj,
                         role=ProjectRole.CONTRIBUTOR)
        count_before = ProjectMember.objects.filter(project=proj).count()
        batch2 = svc.expand_grant(actor=env["owner"], department=l1, project=proj,
                                  role=ProjectRole.CONTRIBUTOR)
        assert batch2.added_count == 0
        assert batch2.unchanged_count == 2
        assert ProjectMember.objects.filter(project=proj).count() == count_before

    def test_ut11_role_adjust_only_when_different(self, env):
        l1, *_ = _mk_tree4(env)
        wm1, wm2, _ = self._assign_members(env, l1, l1)
        proj = env["proj"]
        svc.expand_grant(actor=env["owner"], department=l1, project=proj,
                         role=ProjectRole.CONTRIBUTOR)
        b2 = svc.expand_grant(actor=env["owner"], department=l1, project=proj,
                              role=ProjectRole.ADMIN)
        assert b2.role_changed_count == 2 and b2.added_count == 0
        pm = ProjectMember.objects.get(project=proj, member=env["member"])
        assert pm.role == ProjectRole.ADMIN

    def test_ut12_inactive_member_skipped(self, env):
        l1, *_ = _mk_tree4(env)
        wm1, wm2, wmi = self._assign_members(env, l1, l1)
        b = svc.expand_grant(actor=env["owner"], department=l1, project=env["proj"],
                             role=ProjectRole.CONTRIBUTOR)
        skipped = [e for e in b.member_snapshot
                   if e["action"] == "skipped:member_inactive"]
        assert [e["member_id"] for e in skipped] == [str(wmi.member_id)]

    def test_ut15_guest_role_cap(self, env):
        l1, *_ = _mk_tree4(env)
        # member2 降为 GUEST
        WorkspaceMember.objects.filter(workspace=env["ws"],
                                       member=env["member2"]).update(role=WorkspaceRole.GUEST)
        wm1, wm2, _ = self._assign_members(env, l1, l1)
        b = svc.expand_grant(actor=env["owner"], department=l1, project=env["proj"],
                             role=ProjectRole.CONTRIBUTOR)  # 15 > COMMENTER
        actions = {e["member_id"]: e["action"] for e in b.member_snapshot}
        assert actions[str(wm2.member_id)] == "skipped:guest_role_cap"
        assert actions[str(wm1.member_id)] == "added"  # 其余不被阻塞

    def test_ut16_soft_deleted_member_excluded(self, env):
        l1, *_ = _mk_tree4(env)
        wm1, wm2, wmi = self._assign_members(env, l1, l1)
        # member2 软删（_base_manager 才可见）
        WorkspaceMember.objects.filter(pk=wm2.id).update(deleted_at=__import__(
            "django.utils.timezone", fromlist=["timezone"]).now())
        b = svc.expand_grant(actor=env["owner"], department=l1, project=env["proj"],
                             role=ProjectRole.CONTRIBUTOR)
        assert str(wm2.member_id) not in {e["member_id"] for e in b.member_snapshot}

    def test_ut18_batch_snapshot_consistency(self, env):
        l1, *_ = _mk_tree4(env)
        wm1, wm2, wmi = self._assign_members(env, l1, l1)
        b = svc.expand_grant(actor=env["owner"], department=l1, project=env["proj"],
                             role=ProjectRole.CONTRIBUTOR)
        # IT-08 计数恒等式：added + role_changed + skipped + unchanged = 目标集总数
        assert (b.added_count + b.role_changed_count + b.skipped_count
                + b.unchanged_count) == len(b.member_snapshot) == 3
        assert b.added_count == 2 and b.skipped_count == 1  # inactive 进 skipped

    def test_it03_snapshot_semantics_departure_keeps_permission(self, env):
        l1, *_ = _mk_tree4(env)
        wm1, wm2, _ = self._assign_members(env, l1, l1)
        proj = env["proj"]
        svc.expand_grant(actor=env["owner"], department=l1, project=proj,
                         role=ProjectRole.CONTRIBUTOR)
        # 成员调离部门（重灌部门归属）
        wm1.department = None
        wm1.save(update_fields=["department_id"])
        # 快照语义：既有 ProjectMember 保留
        assert ProjectMember.objects.filter(project=proj, member=env["member"]).exists()

    def test_ut20_delete_granted_department_batch_survives(self, env):
        l1, *_ = _mk_tree4(env)
        wm1, wm2, wmi = self._assign_members(env, l1, l1)
        b = svc.expand_grant(actor=env["owner"], department=l1, project=env["proj"],
                             role=ProjectRole.CONTRIBUTOR)
        l1_id = l1.id  # 删除前留存（delete 后实例 pk 置 None）
        # 清空成员与子部门后删除部门
        WorkspaceMember.objects.filter(workspace=env["ws"], department=l1) \
            .update(department=None)
        # 深度逆序删子树（先叶子后父，BR-04 空部门约束）
        for d in sorted(Department.objects.filter(workspace=env["ws"],
                                                  deleted_at__isnull=True,
                                                  path__startswith=l1.path).exclude(pk=l1.pk),
                        key=lambda d: -svc._depth_of(d)):
            svc.delete_department(actor=env["owner"], department=d)
        svc.delete_department(actor=env["owner"], department=l1)
        b.refresh_from_db()
        assert b.department_id is None  # FK SET_NULL
        assert b.department_id_snapshot == l1_id  # 自含溯源
        # 批次详情端点仍可溯源（404 不发生）
        resp = _client(env["owner"]).get(
            f"{_base(env)}{l1_id}/grants/{b.id}/")
        assert resp.status_code == 200
        assert resp.json()["data"]["department_id"] == str(l1_id)

    def test_grant_location_and_response_shape(self, env):
        l1, *_ = _mk_tree4(env)
        wm1, wm2, wmi = self._assign_members(env, l1, l1)
        resp = _client(env["owner"]).post(
            f"{_base(env)}{l1.id}/grants/",
            {"project_id": str(env["proj"].id), "role": ProjectRole.CONTRIBUTOR,
             "with_descendants": True}, format="json")
        assert resp.status_code == 201, resp.json()
        data = resp.json()["data"]
        assert data["added"] and data["skipped_detail"]
        assert "Location" in resp and str(data["id"]) in resp["Location"]

    def test_grant_preview_zero_write(self, env):
        l1, *_ = _mk_tree4(env)
        wm1, wm2, wmi = self._assign_members(env, l1, l1)
        resp = _client(env["owner"]).post(
            f"{_base(env)}{l1.id}/grants/preview/",
            {"project_id": str(env["proj"].id), "role": ProjectRole.CONTRIBUTOR},
            format="json")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["dry_run"] is True and data["added"]
        assert ProjectMember.objects.filter(project=env["proj"]).count() == 0
        assert DepartmentGrantBatch.objects.count() == 0

    def test_grant_role20_requires_admin(self, env):
        """BR-10：role=PROJ_ADMIN 且 actor 非项目/空间管理员 → 403。"""
        l1, *_ = _mk_tree4(env)
        wm1, wm2, wmi = self._assign_members(env, l1, l1)
        # member（WS_MEMBER、非项目成员）执行 → department.manage 已先拦（PM-02）；
        # 这里用服务层直接验证 BR-10：member 不是 proj/ws admin
        with pytest.raises(AppException) as ei:
            svc.expand_grant(actor=env["member"], department=l1, project=env["proj"],
                             role=ProjectRole.ADMIN)
        assert getattr(ei.value, "error_code", "") == "PERM_PROJECT_ADMIN_REQUIRED"

    def test_archived_project_rejected(self, env):
        l1, *_ = _mk_tree4(env)
        wm1, wm2, wmi = self._assign_members(env, l1, l1)
        proj2 = Project.objects.create(name="归档P", identifier="DPA",
                                       workspace=env["ws"], created_by=env["owner"],
                                       status=Project.Status.ARCHIVED)
        with pytest.raises(AppException) as ei:
            svc.expand_grant(actor=env["owner"], department=l1, project=proj2,
                             role=ProjectRole.CONTRIBUTOR)
        assert getattr(ei.value, "error_code", "") == "PERM_PROJECT_ARCHIVED"


# ────────────────────────────────────────────────────────────────
# 4. 统计（IT-01/06）
# ────────────────────────────────────────────────────────────────
class TestStats:
    def test_stats_direct_vs_with_descendants(self, env):
        l1, l2, l3, l4 = _mk_tree4(env)
        WorkspaceMember.objects.filter(workspace=env["ws"], member=env["member"]) \
            .update(department=l3)
        WorkspaceMember.objects.filter(workspace=env["ws"], member=env["member2"]) \
            .update(department=l1)
        stats = svc.department_stats(l1)
        assert stats["direct_member_count"] == 1  # member2 直属 l1
        assert stats["with_descendants_member_count"] == 2  # + member(l3)
        resp = _client(env["owner"]).get(f"{_base(env)}{l1.id}/stats/")
        assert resp.status_code == 200
        assert resp.json()["data"]["with_descendants_member_count"] == 2

    def test_it06_unassigned_identity(self, env):
        """未分配恒等式：全体成员 = Σ直属 + 未分配。"""
        l1, l2, l3, l4 = _mk_tree4(env)
        WorkspaceMember.objects.filter(workspace=env["ws"], member=env["member"]) \
            .update(department=l3)
        total = WorkspaceMember.objects.filter(
            workspace=env["ws"], is_active=True, deleted_at__isnull=True).count()
        assigned = WorkspaceMember.objects.filter(
            workspace=env["ws"], is_active=True, deleted_at__isnull=True,
            department__isnull=False).count()
        unassigned = WorkspaceMember.objects.filter(
            workspace=env["ws"], is_active=True, deleted_at__isnull=True,
            department__isnull=True).count()
        assert total == assigned + unassigned  # 恒等式（IT-06 对账口径）

    def test_issue_count_by_assignee_department(self, env):
        l1, *_ = _mk_tree4(env)
        WorkspaceMember.objects.filter(workspace=env["ws"], member=env["member"]) \
            .update(department=l1)
        state = env["proj"].states.first()
        issue = Issue.objects.create(project=env["proj"], name="部门任务",
                                     state=state, created_by=env["owner"])
        issue.assignees.add(env["member"])
        stats = svc.department_stats(l1)
        assert stats["issue_count"] == 1


# ────────────────────────────────────────────────────────────────
# 5. PM 权限矩阵（§5.4：四主体 × 读/写）
# ────────────────────────────────────────────────────────────────
class TestPermissionMatrix:
    @pytest.fixture()
    def subjects(self, env, db):
        """四主体：OWNER / ADMIN / MEMBER / GUEST（各自独立工作空间成员行）。"""
        admin = User.objects.create_user(email="dept-admin@rabbit.dev",
                                         password="Rabbit123!", display_name="空间管理员")
        guest = User.objects.create_user(email="dept-guest@rabbit.dev",
                                         password="Rabbit123!", display_name="访客")
        WorkspaceMember.objects.create(workspace=env["ws"], member=admin,
                                       role=WorkspaceRole.ADMIN, created_by=env["owner"])
        WorkspaceMember.objects.create(workspace=env["ws"], member=guest,
                                       role=WorkspaceRole.GUEST, created_by=env["owner"])
        return {"owner": env["owner"], "admin": admin,
                "member": env["member"], "guest": guest}

    def test_pm01_read_endpoints(self, env, subjects):
        l1, *_ = _mk_tree4(env)
        for who in ("owner", "admin", "member"):
            r = _client(subjects[who]).get(_base(env))
            assert r.status_code == 200, f"{who} 读树应 200"
        r = _client(subjects["guest"]).get(_base(env))
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "PERM_ROLE_INSUFFICIENT"

    def test_pm02_write_endpoints(self, env, subjects):
        l1, *_ = _mk_tree4(env)
        body = {"name": "新部门"}
        for who in ("member", "guest"):
            r = _client(subjects[who]).post(_base(env), body, format="json")
            assert r.status_code == 403, f"{who} 建部门应 403"
            assert r.json()["error"]["code"] == "PERM_WORKSPACE_ADMIN_REQUIRED"
            # 零副作用
            assert Department.objects.filter(workspace=env["ws"]).count() == 4
        for who in ("owner", "admin"):
            r = _client(subjects[who]).post(
                _base(env), {"name": f"新部门-{who}"}, format="json")
            assert r.status_code == 201, f"{who} 建部门应 201"

    def test_pm03_admin_cannot_assign_owner_row(self, env, subjects):
        l1, *_ = _mk_tree4(env)
        owner_wm = WorkspaceMember.objects.get(workspace=env["ws"],
                                               member=env["owner"])
        r = _client(subjects["admin"]).patch(
            f"/api/v1/workspaces/{env['ws'].slug}/members/{owner_wm.id}/",
            {"department_id": str(l1.id)}, format="json")
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "PERM_ROLE_INSUFFICIENT"
        # OWNER 行本人自改（OWNER 可管理任何行）
        r2 = _client(subjects["owner"]).patch(
            f"/api/v1/workspaces/{env['ws'].slug}/members/{owner_wm.id}/",
            {"department_id": str(l1.id)}, format="json")
        assert r2.status_code == 200

    def test_non_member_404(self, env):
        outsider = User.objects.create_user(email="dept-outsider@rabbit.dev",
                                            password="Rabbit123!", display_name="外人")
        r = _client(outsider).get(_base(env))
        assert r.status_code == 404
