"""项目集组合树测试（PROJ-004，Sprint-9 R2 门禁）。

覆盖：树管理（BR-01 深度 / 移动环 / BR-02 挂载唯一与叶子口径 / BR-11 非空
阻断）、聚合口径（BR-13 加权 / BR-05 权重快照 / BR-12 资源矩阵）、跨项目
依赖放开（BR-08 三路径 / BR-07 守卫项目过滤 / 跨项目环 BR-09）、预警幂等
（BR-06）、贡献项归属（BR-04）、manager 祖先链判定（BR-03）、五主体权限
矩阵（BR-14）。夹具风格对照 test_departments.py。
"""

from __future__ import annotations

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from plane.bgtasks.portfolio_alerts import milestone_due_alerts
from plane.db.models import (
    Issue,
    MilestoneItem,
    Notification,
    Portfolio,
    PortfolioMilestone,
    PortfolioProject,
    Project,
    ProjectMember,
    ProjectRole,
    State,
    User,
    WorkLogSummary,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)
from plane.db.seeds.project_states import seed_project_states
from plane.db.services import portfolio as psvc
from plane.db.services.issue_link import (
    CircularDependencyError,
    RelationValidationError,
    create_relation,
)
from plane.db.services.issue_sequence import next_sequence_id
from plane.db.services.issue_transition_guard import assert_completable

pytestmark = pytest.mark.django_db


# ────────────────────────────────────────────────────────────────
# 夹具
# ────────────────────────────────────────────────────────────────
@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="pf-owner@rabbit.dev", password="Rabbit123!", display_name="空间管理员")
    ws = Workspace.objects.create(name="PF", slug="w-pf-test", owner=owner, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=owner, role=WorkspaceRole.OWNER, created_by=owner)
    for email, role in (
        ("pf-manager@rabbit.dev", WorkspaceRole.MEMBER),
        ("pf-member@rabbit.dev", WorkspaceRole.MEMBER),
        ("pf-guest@rabbit.dev", WorkspaceRole.GUEST),
    ):
        u = User.objects.create_user(email=email, password="Rabbit123!", display_name=email[:9])
        WorkspaceMember.objects.create(workspace=ws, member=u, role=role, created_by=owner)
    manager = User.objects.get(email="pf-manager@rabbit.dev")
    proj = Project.objects.create(name="App 重构", identifier="APP", workspace=ws, created_by=owner)
    proj2 = Project.objects.create(name="中台网关", identifier="GW", workspace=ws, created_by=owner)
    seed_project_states(proj)
    seed_project_states(proj2)
    ProjectMember.objects.create(project=proj, member=owner, role=ProjectRole.ADMIN, created_by=owner)
    ProjectMember.objects.create(project=proj2, member=owner, role=ProjectRole.ADMIN, created_by=owner)
    return {"owner": owner, "ws": ws, "manager": manager, "proj": proj, "proj2": proj2}


def _client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _base(env) -> str:
    return f"/api/v1/workspaces/{env['ws'].slug}/portfolios/"


def _mk_pf(env, name, parent=None, manager=None) -> Portfolio:
    return Portfolio.objects.create(
        workspace=env["ws"],
        name=name,
        parent_id=parent.id if parent else None,
        depth=1 if parent is None else parent.depth + 1,
        manager_id=manager.id if manager else None,
        created_by=env["owner"],
    )


def _mk_issue(proj, name, *, group="unstarted", estimate=None, actor) -> Issue:
    state = State.objects.filter(project=proj, group=group).first()
    return Issue.objects.create(
        project=proj,
        name=name,
        state=state,
        sequence_id=next_sequence_id(proj.pk),
        estimate_minutes=estimate,
        created_by=actor,
    )


# ────────────────────────────────────────────────────────────────
# 1. 树管理（UT-01/02/03/04/16）
# ────────────────────────────────────────────────────────────────
class TestTreeManagement:
    def test_ut01_depth_limit(self, env):
        l1 = _mk_pf(env, "2026 战略")
        l2 = _mk_pf(env, "电商平台", parent=l1)
        l3 = _mk_pf(env, "交易域", parent=l2)
        assert l3.depth == 3
        resp = _client(env["owner"]).post(_base(env), {"name": "第四层", "parent_id": str(l3.id)}, format="json")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "RESOURCE_LIMIT_EXCEEDED"
        assert resp.json()["error"]["details"][0]["code"] == "DEPTH"

    def test_ut02_move_cycle_rejected(self, env):
        l1 = _mk_pf(env, "根A")
        l2 = _mk_pf(env, "子B", parent=l1)
        # A 移到 B 之下 → A→B→A 环（B.depth=2 不触发深度，纯环路径）
        resp = _client(env["owner"]).patch(f"{_base(env)}{l1.id}/", {"parent_id": str(l2.id)}, format="json")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "RESOURCE_CIRCULAR_DEPENDENCY"

    def test_ut03_duplicate_mount_rejected(self, env):
        leaf = _mk_pf(env, "叶子")
        PortfolioProject.objects.create(portfolio=leaf, project=env["proj"], created_by=env["owner"])
        other = _mk_pf(env, "另一叶子")
        resp = _client(env["owner"]).post(
            f"{_base(env)}{other.id}/projects/", {"project_id": str(env["proj"].id)}, format="json"
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "RESOURCE_ALREADY_EXISTS"

    def test_ut04_delete_nonempty_blocked(self, env):
        root = _mk_pf(env, "根")
        child = _mk_pf(env, "子", parent=root)
        leaf = _mk_pf(env, "叶子", parent=child)
        PortfolioProject.objects.create(portfolio=leaf, project=env["proj"], created_by=env["owner"])
        resp = _client(env["owner"]).delete(f"{_base(env)}{root.id}/")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "RESOURCE_IN_USE"
        codes = {d["field"] for d in resp.json()["error"]["details"]}
        assert codes == {"children", "projects"}
        # 清空叶子后：叶子可删、根可删
        assert _client(env["owner"]).delete(f"{_base(env)}{leaf.id}/").status_code == 409  # 仍挂项目
        PortfolioProject.objects.filter(portfolio=leaf).delete()
        assert _client(env["owner"]).delete(f"{_base(env)}{leaf.id}/").status_code == 200

    def test_ut16_mount_only_to_leaf(self, env):
        root = _mk_pf(env, "根")
        _mk_pf(env, "子", parent=root)
        resp = _client(env["owner"]).post(
            f"{_base(env)}{root.id}/projects/", {"project_id": str(env["proj"].id)}, format="json"
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_INVALID_PARAM"

    def test_tree_list_shape(self, env):
        root = _mk_pf(env, "根")
        child = _mk_pf(env, "子", parent=root)
        leaf = _mk_pf(env, "叶子", parent=child)
        PortfolioProject.objects.create(portfolio=leaf, project=env["proj"], created_by=env["owner"])
        data = _client(env["owner"]).get(_base(env)).json()["data"]
        assert len(data) == 1 and data[0]["name"] == "根"
        assert data[0]["children"][0]["children"][0]["project_count"] == 1
        assert data[0]["project_count"] == 0


# ────────────────────────────────────────────────────────────────
# 2. 聚合口径（UT-05/06/10 + IT-01 summary）
# ────────────────────────────────────────────────────────────────
class TestAggregation:
    def _setup_3projects(self, env):
        """三项目：71%/48%/80% 加权（规格 §3.1 示例数值：128/86/45 任务）。"""
        leaf = _mk_pf(env, "电商平台 2.0")
        proj3 = Project.objects.create(name="数据迁移", identifier="DM", workspace=env["ws"], created_by=env["owner"])
        seed_project_states(proj3)
        ProjectMember.objects.create(
            project=proj3, member=env["owner"], role=ProjectRole.ADMIN, created_by=env["owner"]
        )
        for pf, proj in ((leaf, env["proj"]), (leaf, env["proj2"]), (leaf, proj3)):
            PortfolioProject.objects.create(portfolio=pf, project=proj, created_by=env["owner"])
        return leaf, proj3

    def test_ut05_progress_weighted(self, env):
        leaf, proj3 = self._setup_3projects(env)
        # 128 任务 71% 完成 / 86 任务 48% / 45 任务 80%（cancelled 不计分母）
        for proj, total, done in ((env["proj"], 128, 91), (env["proj2"], 86, 41), (proj3, 45, 36)):
            for i in range(total):
                group = "completed" if i < done else ("cancelled" if i == total - 1 else "started")
                _mk_issue(proj, f"t{i}", group=group, actor=env["owner"])
        p = psvc.PortfolioService().progress(env["owner"], leaf.id)
        by_proj = {r["identifier"]: r["ratio"] for r in p.by_project}
        assert abs(by_proj["APP"] - 91 / 127) < 1e-6  # 128-1 cancelled
        assert abs(by_proj["GW"] - 41 / 85) < 1e-6
        expect = (91 + 41 + 36) / (127 + 85 + 44)
        assert abs(p.overall - expect) < 1e-6
        # 空项目集不除零
        empty = _mk_pf(env, "空")
        assert psvc.PortfolioService().progress(env["owner"], empty.id).overall == 0.0

    def test_ut06_milestone_weight_snapshot(self, env):
        leaf, _ = self._setup_3projects(env)
        ms = PortfolioMilestone.objects.create(
            portfolio=leaf, name="全量联调", target_date="2026-09-30", created_by=env["owner"]
        )
        i1 = _mk_issue(env["proj"], "贡献A", group="completed", estimate=480, actor=env["owner"])
        i2 = _mk_issue(env["proj2"], "贡献B", group="started", estimate=240, actor=env["owner"])
        MilestoneItem.objects.create(milestone=ms, issue=i1, weight=480)
        MilestoneItem.objects.create(milestone=ms, issue=i2, weight=240)
        svc = psvc.PortfolioService()
        assert abs(svc.milestone_progress(ms.id, actor=env["owner"]) - 480 / 720) < 1e-6
        # 估算编辑不影响历史完成度（BR-05 快照）
        i1.estimate_minutes = 999
        i1.save(update_fields=["estimate_minutes"])
        assert abs(svc.milestone_progress(ms.id, actor=env["owner"]) - 480 / 720) < 1e-6

    def test_ut10_resource_matrix_matches_worklog(self, env):
        leaf, _ = self._setup_3projects(env)
        WorkLogSummary.objects.create(
            project=env["proj"], actor=env["manager"], week_start="2026-09-07", total_minutes=1800
        )
        WorkLogSummary.objects.create(
            project=env["proj2"], actor=env["manager"], week_start="2026-09-07", total_minutes=720
        )
        rows = list(psvc.PortfolioService().resource_matrix(env["owner"], leaf.id, "2026-09-01", "2026-09-30"))
        assert len(rows) == 2
        assert {r["identifier"]: r["minutes"] for r in rows} == {"APP": 1800, "GW": 720}

    def test_it01_summary_payload(self, env):
        leaf, proj3 = self._setup_3projects(env)
        for proj, total, done in ((env["proj"], 10, 5), (env["proj2"], 10, 2), (proj3, 10, 8)):
            for i in range(total):
                group = "completed" if i < done else "started"
                _mk_issue(proj, f"t{i}", group=group, actor=env["owner"])
        data = _client(env["owner"]).get(f"{_base(env)}{leaf.id}/summary/").json()["data"]
        assert {"progress", "resource", "risks", "portfolio"} <= set(data)
        assert len(data["progress"]["by_project"]) == 3
        assert data["resource"]["weeks"] and len(data["resource"]["weeks"]) == 4


# ────────────────────────────────────────────────────────────────
# 3. 跨项目依赖放开（UT-07/08/09 + IT-02/03）
# ────────────────────────────────────────────────────────────────
class TestCrossProjectLinks:
    def _two_issues(self, env):
        a = _mk_issue(env["proj"], "A", actor=env["owner"])
        b = _mk_issue(env["proj2"], "B", actor=env["owner"])
        return a, b

    def test_ut07_three_paths(self, env, db):
        a, b = self._two_issues(env)
        # ① 同工作空间跨项目：放行
        fwd, mirror = create_relation(
            issue_id=a.id, related_issue_id=b.id, relation_type="blocks", actor_id=env["owner"].id
        )
        assert fwd.relation_type == "blocks"
        # ② 跨工作空间：拒绝
        other_owner = User.objects.create_user(email="pf-other@rabbit.dev", password="Rabbit123!")
        ws2 = Workspace.objects.create(name="W2", slug="w-pf-other", owner=other_owner, created_by=other_owner)
        proj_x = Project.objects.create(name="X", identifier="XX", workspace=ws2, created_by=other_owner)
        seed_project_states(proj_x)
        x = _mk_issue(proj_x, "X", actor=other_owner)
        with pytest.raises(RelationValidationError):
            create_relation(issue_id=a.id, related_issue_id=x.id, relation_type="blocks", actor_id=env["owner"].id)
        # ③ 不可见目标项目（ws2 对 owner 有 proj_x？——owner 非 ws2 成员 → 不可见）
        #    proj_x 属 ws2，owner 不是 ws2 成员，路径 ② 已拦工作空间；不可见路径用
        #    同空间 draft 项目表达：非成员非创建者不可见 draft
        draft = Project.objects.create(
            name="D", identifier="DR", workspace=env["ws"], status=Project.Status.DRAFT, created_by=env["manager"]
        )
        seed_project_states(draft)
        d = _mk_issue(draft, "D", actor=env["manager"])
        from plane.db.services.issue_link import TargetInvisibleError

        member = User.objects.get(email="pf-member@rabbit.dev")
        with pytest.raises(TargetInvisibleError):
            create_relation(issue_id=a.id, related_issue_id=d.id, relation_type="blocks", actor_id=member.id)

    def test_ut08_guard_project_filter(self, env):
        """跨项目 blocks 不拦完成（BR-07 软策略）；同项目仍硬拦（IT-03 回归）。"""
        a, b = self._two_issues(env)
        create_relation(
            issue_id=b.id, related_issue_id=a.id, relation_type="blocks", actor_id=env["owner"].id
        )  # B blocks A（跨项目）
        done = State.objects.filter(project=env["proj"], group="completed").first()
        assert_completable(issue=a, to_state=done, force=False, is_admin=False)  # 不抛
        # 同项目：C blocks a2 → 拦截
        a2 = _mk_issue(env["proj"], "A2", actor=env["owner"])
        c = _mk_issue(env["proj"], "C", actor=env["owner"])
        create_relation(issue_id=c.id, related_issue_id=a2.id, relation_type="blocks", actor_id=env["owner"].id)
        from plane.db.services.issue_link import TransitionBlockedError

        with pytest.raises(TransitionBlockedError):
            assert_completable(issue=a2, to_state=done, force=False, is_admin=False)

    def test_ut09_cross_project_cycle(self, env):
        a, b = self._two_issues(env)
        c = _mk_issue(env["proj"], "C2", actor=env["owner"])
        create_relation(
            issue_id=a.id, related_issue_id=b.id, relation_type="blocks", actor_id=env["owner"].id
        )  # A(p1)→B(p2)
        create_relation(
            issue_id=b.id, related_issue_id=c.id, relation_type="blocks", actor_id=env["owner"].id
        )  # B(p2)→C2(p1)
        # C2→A 闭环（跨项目环同样禁止，BR-09）
        with pytest.raises(CircularDependencyError):
            create_relation(issue_id=c.id, related_issue_id=a.id, relation_type="blocks", actor_id=env["owner"].id)

    def test_it02_dependency_graph_payload(self, env):
        a, b = self._two_issues(env)
        c = _mk_issue(env["proj"], "C", actor=env["owner"])
        create_relation(
            issue_id=c.id, related_issue_id=a.id, relation_type="blocks", actor_id=env["owner"].id
        )  # 同项目
        create_relation(
            issue_id=a.id, related_issue_id=b.id, relation_type="blocks", actor_id=env["owner"].id
        )  # 跨项目
        leaf = _mk_pf(env, "组合")
        for proj in (env["proj"], env["proj2"]):
            PortfolioProject.objects.create(portfolio=leaf, project=proj, created_by=env["owner"])
        data = _client(env["owner"]).get(f"{_base(env)}{leaf.id}/dependency-graph/").json()["data"]
        edges = data["edges"]
        cross = [e for e in edges if e["cross_project"]]
        assert len(cross) == 1
        assert not [e for e in edges if not e["cross_project"] and e["relation_type"] != "blocks"]
        assert len(data["nodes"]) == 3

    def test_relations_list_cross_marker(self, env):
        """relations_of 序列化供任务详情外部分组（前端按 related_issue.project 分桶）。"""
        a, b = self._two_issues(env)
        create_relation(issue_id=a.id, related_issue_id=b.id, relation_type="blocks", actor_id=env["owner"].id)
        from plane.db.services.issue_link import relations_of

        rows = relations_of(a.id)
        assert len(rows) == 1 and rows[0]["is_blocking"]


# ────────────────────────────────────────────────────────────────
# 4. 预警 / 贡献项 / manager 判定 / 权限矩阵（UT-11/12/15 + IT-05/06）
# ────────────────────────────────────────────────────────────────
class TestAlertsAndPermissions:
    def _milestone(self, env, days_left=5, manager=None):
        leaf = _mk_pf(env, "预警组合", manager=manager)
        today = timezone.localdate()
        ms = PortfolioMilestone.objects.create(
            portfolio=leaf,
            name="临期里程碑",
            target_date=today + timezone.timedelta(days=days_left),
            created_by=env["owner"],
        )
        i = _mk_issue(env["proj"], "未完成贡献", group="started", estimate=100, actor=env["owner"])
        PortfolioProject.objects.create(portfolio=leaf, project=env["proj"], created_by=env["owner"])
        MilestoneItem.objects.create(milestone=ms, issue=i, weight=100)
        return ms

    def test_ut11_alert_idempotent_per_day(self, env):
        self._milestone(env, days_left=5, manager=env["manager"])
        s1 = milestone_due_alerts()
        s2 = milestone_due_alerts()  # 同日重跑：SETNX/唯一键幂等
        assert s1["sent"] >= 1 and s2["sent"] == 0
        assert Notification.objects.filter(event=Notification.Event.MILESTONE_AT_RISK).count() == s1["sent"]

    def test_it04_alert_stops_at_full_progress(self, env):
        """贡献项全部完成 → 本里程碑不预警（BR-06 达标停发）。

        作用域断言（坑 18 同款）：beat 扫全库——演示种子等他人里程碑照常发，
        全局 sent==0 断言会被库内数据误红。"""
        ms = self._milestone(env, days_left=3)
        done = State.objects.filter(project=env["proj"], group="completed").first()
        Issue.objects.filter(milestone_items__milestone=ms).update(state=done)
        before = set(
            Notification.objects.filter(event=Notification.Event.MILESTONE_AT_RISK).values_list("dedup_key", flat=True)
        )
        milestone_due_alerts()
        after = set(
            Notification.objects.filter(event=Notification.Event.MILESTONE_AT_RISK).values_list("dedup_key", flat=True)
        )
        new_keys = after - before
        assert all(f"ms:alert:{ms.id}" not in k for k in new_keys)  # 本里程碑零新增

    def test_ut12_item_scope_validation(self, env):
        leaf = _mk_pf(env, "范围组合")
        PortfolioProject.objects.create(portfolio=leaf, project=env["proj"], created_by=env["owner"])
        ms = PortfolioMilestone.objects.create(
            portfolio=leaf, name="M", target_date="2026-10-31", created_by=env["owner"]
        )
        outsider = _mk_issue(env["proj2"], "不在项目集", actor=env["owner"])
        resp = _client(env["owner"]).post(
            f"{_base(env)}{leaf.id}/milestones/{ms.id}/items/", {"issue_id": str(outsider.id)}, format="json"
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["details"][0]["code"] == "DOES_NOT_EXIST"

    def test_ut15_manager_ancestor_chain(self, env):
        """三路径：叶子 manager / 祖先 manager（≤2 跳）/ 非 manager 403。"""
        root = _mk_pf(env, "根", manager=env["manager"])
        leaf = _mk_pf(env, "叶子", parent=root)
        # ① 祖先 manager（root 挂 manager，leaf 无）——manager 本人（WS_MEMBER）建里程碑 → 放行
        resp = _client(env["manager"]).post(
            f"{_base(env)}{leaf.id}/milestones/", {"name": "M", "target_date": "2026-10-31"}, format="json"
        )
        assert resp.status_code == 201
        # ② 非 manager（另一 MEMBER）：403
        other = User.objects.create_user(email="pf-m2@rabbit.dev", password="Rabbit123!")
        WorkspaceMember.objects.create(
            workspace=env["ws"], member=other, role=WorkspaceRole.MEMBER, created_by=env["owner"]
        )
        resp = _client(other).post(
            f"{_base(env)}{leaf.id}/milestones/", {"name": "M2", "target_date": "2026-10-31"}, format="json"
        )
        assert resp.status_code == 403

    def test_it06_permission_matrix(self, env):
        """五主体：WS_ADMIN 写✓/manager 写✓/MEMBER 写 403/GUEST 读 403/非成员 404。"""
        leaf = _mk_pf(env, "矩阵组合")
        guest = User.objects.get(email="pf-guest@rabbit.dev")
        member = User.objects.get(email="pf-member@rabbit.dev")
        owner, manager = env["owner"], env["manager"]
        leaf.manager = manager
        leaf.save(update_fields=["manager"])
        # 写端点：owner(ADMIN) ✓
        assert (
            _client(owner)
            .post(f"{_base(env)}{leaf.id}/milestones/", {"name": "A", "target_date": "2026-10-01"}, format="json")
            .status_code
            == 201
        )
        # 写端点：manager（祖先=自身）✓
        assert (
            _client(manager)
            .post(f"{_base(env)}{leaf.id}/milestones/", {"name": "B", "target_date": "2026-10-02"}, format="json")
            .status_code
            == 201
        )
        # 写端点：MEMBER → 403
        assert (
            _client(member)
            .post(f"{_base(env)}{leaf.id}/milestones/", {"name": "C", "target_date": "2026-10-03"}, format="json")
            .status_code
            == 403
        )
        # 读端点：MEMBER ✓（载荷按 accessible 收窄）
        assert _client(member).get(f"{_base(env)}{leaf.id}/summary/").status_code == 200
        # 读端点：GUEST → 403
        assert _client(guest).get(_base(env)).status_code == 403
        # 非成员 → 404 存在性隐藏
        outsider = User.objects.create_user(email="pf-out@rabbit.dev", password="Rabbit123!")
        assert _client(outsider).get(_base(env)).status_code == 404

    def test_it05_mount_requires_both_sides(self, env):
        """挂载双端权限：项目集管理权 + 项目 PROJ_ADMIN。"""
        leaf = _mk_pf(env, "双端组合")
        member = User.objects.get(email="pf-member@rabbit.dev")
        # member 是 WS_MEMBER：项目集端即 403
        assert (
            _client(member)
            .post(f"{_base(env)}{leaf.id}/projects/", {"project_id": str(env["proj"].id)}, format="json")
            .status_code
            == 403
        )
        # owner 两端皆 ADMIN：✓
        assert (
            _client(env["owner"])
            .post(f"{_base(env)}{leaf.id}/projects/", {"project_id": str(env["proj"].id)}, format="json")
            .status_code
            == 201
        )
