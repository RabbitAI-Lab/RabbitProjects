"""敏捷报表迭代测试（RPT-003，Sprint-9 R2 门禁）。

覆盖：Cycle CRUD（时间盒倒挂/唯一 active/仅 planned 删）、整批换绑（BR-01
+ cycles 事件埋点）、快照管道（口径单源/缺日补跑/恒等式）、complete（终版
快照 is_final + 结转/移回 + **归档不可篡改红线**）、三图表（燃尽实时点/
速率终版锚/CFD 空窗）、度量配置（BR-07）、导出权限。夹具风格对照
test_portfolio.py。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from plane.db.models import (
    Cycle,
    CycleSnapshot,
    DailyGroupSnapshot,
    Issue,
    IssueActivity,
    Project,
    ProjectMember,
    ProjectRole,
    State,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)
from plane.db.seeds.project_states import seed_project_states
from plane.db.services.agile_reports import (
    AgileReportService,
    backfill_cycle_snapshots,
    compute_cycle_snapshot,
    cycle_daily_snapshot,
)
from plane.db.services.issue_sequence import next_sequence_id

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="cyc-owner@rabbit.dev", password="Rabbit123!", display_name="空间管理员")
    ws = Workspace.objects.create(name="CY", slug="w-cyc-test", owner=owner, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=owner, role=WorkspaceRole.OWNER, created_by=owner)
    proj = Project.objects.create(name="电商重构", identifier="RBT", workspace=ws, created_by=owner)
    seed_project_states(proj)
    ProjectMember.objects.create(project=proj, member=owner, role=ProjectRole.ADMIN, created_by=owner)
    viewer = User.objects.create_user(email="cyc-viewer@rabbit.dev", password="Rabbit123!")
    WorkspaceMember.objects.create(workspace=ws, member=viewer, role=WorkspaceRole.MEMBER)
    ProjectMember.objects.create(project=proj, member=viewer, role=ProjectRole.VIEWER)
    return {"owner": owner, "viewer": viewer, "ws": ws, "proj": proj}


def _client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _base(env) -> str:
    return f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/cycles/"


def _mk_cycle(env, name="Sprint 24", start=None, end=None, status=Cycle.Status.PLANNED) -> Cycle:
    today = timezone.localdate()
    return Cycle.objects.create(
        project=env["proj"],
        name=name,
        start_date=start or today - timedelta(days=2),
        end_date=end or today + timedelta(days=11),
        status=status,
        created_by=env["owner"],
    )


def _mk_issue(env, name, *, group="unstarted", estimate=240) -> Issue:
    state = State.objects.filter(project=env["proj"], group=group).first()
    return Issue.objects.create(
        project=env["proj"],
        name=name,
        state=state,
        sequence_id=next_sequence_id(env["proj"].pk),
        estimate_minutes=estimate,
        created_by=env["owner"],
    )


# ────────────────────────────────────────────────────────────────
# 1. Cycle CRUD（UT-01~05）
# ────────────────────────────────────────────────────────────────
class TestCycleCRUD:
    def test_date_range_inverted(self, env):
        today = timezone.localdate()
        resp = _client(env["owner"]).post(
            _base(env), {"name": "倒挂", "start_date": today + timedelta(days=10), "end_date": today}, format="json"
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_INVALID_DATE_RANGE"

    def test_duplicate_name(self, env):
        _mk_cycle(env, "Sprint 24")
        today = timezone.localdate()
        resp = _client(env["owner"]).post(
            _base(env),
            {"name": "Sprint 24", "start_date": today, "end_date": today + timedelta(days=14)},
            format="json",
        )
        assert resp.status_code == 409

    def test_start_unique_active(self, env):
        _mk_cycle(env, "S1", status=Cycle.Status.ACTIVE)
        c2 = _mk_cycle(env, "S2")
        resp = _client(env["owner"]).post(f"{_base(env)}{c2.id}/start/")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "RESOURCE_STATE_INVALID"

    def test_delete_only_planned(self, env):
        active = _mk_cycle(env, "SA", status=Cycle.Status.ACTIVE)
        resp = _client(env["owner"]).delete(f"{_base(env)}{active.id}/")
        assert resp.status_code == 409
        planned = _mk_cycle(env, "SP")
        assert _client(env["owner"]).delete(f"{_base(env)}{planned.id}/").status_code == 200

    def test_viewer_cannot_manage(self, env):
        """cycle.manage 默认 PROJ_ADMIN（rbac §8.2 可配置列按默认）；viewer 403。"""
        c = _mk_cycle(env)
        resp = _client(env["viewer"]).post(f"{_base(env)}{c.id}/start/")
        assert resp.status_code == 403
        # 读面 report.read（VIEWER）放行
        assert _client(env["viewer"]).get(_base(env)).status_code == 200


# ────────────────────────────────────────────────────────────────
# 2. 整批换绑 + cycles 事件（UT-06/07）
# ────────────────────────────────────────────────────────────────
class TestBatchAssign:
    def test_put_batch_and_events(self, env):
        c1 = _mk_cycle(env, "S1", status=Cycle.Status.ACTIVE)
        i1 = _mk_issue(env, "t1")
        i2 = _mk_issue(env, "t2")
        resp = _client(env["owner"]).put(
            f"{_base(env)}{c1.id}/issues/", {"issue_ids": [str(i1.id), str(i2.id)]}, format="json"
        )
        assert resp.status_code == 200
        assert set(c1.issues.values_list("id", flat=True)) == {i1.id, i2.id}
        assert IssueActivity.objects.filter(field="cycles", new_identifier=c1.id).count() == 2
        # 再整批去掉 i2 → 移出事件（old_identifier）
        resp = _client(env["owner"]).put(f"{_base(env)}{c1.id}/issues/", {"issue_ids": [str(i1.id)]}, format="json")
        assert resp.status_code == 200
        assert IssueActivity.objects.filter(field="cycles", old_identifier=c1.id, new_identifier=None).count() == 1
        i2.refresh_from_db()
        assert i2.cycle_id is None

    def test_cross_project_issue_rejected(self, env):
        proj2 = Project.objects.create(name="他项目", identifier="OTH", workspace=env["ws"], created_by=env["owner"])
        seed_project_states(proj2)
        other = Issue.objects.create(
            project=proj2, name="外部任务", sequence_id=next_sequence_id(proj2.pk), created_by=env["owner"]
        )
        c = _mk_cycle(env)
        resp = _client(env["owner"]).put(f"{_base(env)}{c.id}/issues/", {"issue_ids": [str(other.id)]}, format="json")
        assert resp.status_code == 400


# ────────────────────────────────────────────────────────────────
# 3. 快照管道（UT-08~11）
# ────────────────────────────────────────────────────────────────
class TestSnapshotPipeline:
    def test_compute_today_group_breakdown(self, env):
        c = _mk_cycle(env, status=Cycle.Status.ACTIVE)
        _mk_issue(env, "a", group="unstarted", estimate=300)
        _mk_issue(env, "b", group="started", estimate=180)
        _mk_issue(env, "done", group="completed", estimate=240)
        c.issues.set(Issue.objects.filter(project=env["proj"]))
        snap = compute_cycle_snapshot(c, timezone.localdate(), "count")
        assert snap["remaining_by_group"]["unstarted"] == 1
        assert snap["remaining_by_group"]["started"] == 1
        assert snap["remaining_by_group"]["completed"] == 1
        assert snap["remaining_total"] == 2  # count 度量：backlog/unstarted/started 各 1
        assert snap["scope_total"] == 3

    def test_backfill_idempotent(self, env):
        c = _mk_cycle(env, start=timezone.localdate() - timedelta(days=4), status=Cycle.Status.ACTIVE)
        _mk_issue(env, "x", group="started")
        c.issues.set(Issue.objects.filter(project=env["proj"]))
        n1 = backfill_cycle_snapshots(c, timezone.localdate(), "count")
        n2 = backfill_cycle_snapshots(c, timezone.localdate(), "count")
        assert n1 == 5 and n2 == 5  # 逐日 update_or_create 幂等（含当日）
        assert CycleSnapshot.objects.filter(cycle=c).count() == 5

    def test_daily_group_snapshot_beat(self, env):
        result = cycle_daily_snapshot()
        assert result["projects"] >= 1
        assert DailyGroupSnapshot.objects.filter(project=env["proj"]).count() == 1


# ────────────────────────────────────────────────────────────────
# 4. complete：终版快照 + 结转/移回 + **不可篡改红线**（UT-12~14 / 验收第 3 条）
# ────────────────────────────────────────────────────────────────
class TestCompleteAndImmutability:
    def _complete(self, env, carry="backlog"):
        c = _mk_cycle(env, start=timezone.localdate() - timedelta(days=3), status=Cycle.Status.ACTIVE)
        i1 = _mk_issue(env, "done", group="completed")
        i2 = _mk_issue(env, "unfinished", group="started")
        c.issues.set([i1, i2])
        backfill_cycle_snapshots(c, timezone.localdate(), "count")
        resp = _client(env["owner"]).post(f"{_base(env)}{c.id}/complete/", {"carry_over": carry}, format="json")
        return c, i1, i2, resp

    def test_complete_backlog(self, env):
        c, i1, i2, resp = self._complete(env, "backlog")
        assert resp.status_code == 200 and resp.json()["data"]["moved_back"] == 1
        c.refresh_from_db()
        assert c.status == Cycle.Status.COMPLETED
        final = CycleSnapshot.objects.get(cycle=c, is_final=True)
        assert final.frozen is True
        assert CycleSnapshot.objects.filter(cycle=c, is_final=True).count() == 1
        i2.refresh_from_db()
        assert i2.cycle_id is None  # 移回待规划

    def test_complete_carry_next(self, env):
        c, i1, i2, resp = self._complete(env, "next")
        assert resp.status_code == 200
        # 无 planned 迭代 → 结转目标缺失，等效移回（不失败）
        i2.refresh_from_db()
        assert i2.cycle_id is None

    def test_archived_report_immutable(self, env):
        """验收硬红线：归档后修改历史任务不改变已归档报表（BR-06）。"""
        c, i1, i2, resp = self._complete(env, "backlog")
        final_before = CycleSnapshot.objects.get(cycle=c, is_final=True)
        remaining_before = final_before.remaining_total
        # 历史任务后续修改（完成/移出）——归档快照不受影响
        done_state = State.objects.get(project=env["proj"], group="completed")
        Issue.objects.filter(pk=i2.pk).update(state=done_state)
        payload = AgileReportService().burndown(c.id)
        assert payload.frozen is True
        final_after = CycleSnapshot.objects.get(cycle=c, is_final=True)
        assert final_after.remaining_total == remaining_before
        assert final_after.pk == final_before.pk

    def test_completed_cycle_rejects_changes(self, env):
        c, *_, resp = self._complete(env, "backlog")
        resp = _client(env["owner"]).patch(f"{_base(env)}{c.id}/", {"name": "改名"}, format="json")
        assert resp.status_code == 409


# ────────────────────────────────────────────────────────────────
# 5. 三图表（UT-15~18）
# ────────────────────────────────────────────────────────────────
class TestCharts:
    def _archive_cycle(self, env, name, done, total, end_days=6):
        c = Cycle.objects.create(
            project=env["proj"],
            name=name,
            start_date=timezone.localdate() - timedelta(days=20),
            end_date=timezone.localdate() - timedelta(days=end_days),
            status=Cycle.Status.ACTIVE,
            created_by=env["owner"],
        )
        for i in range(total):
            group = "completed" if i < done else "started"
            issue = _mk_issue(env, f"{name}-{i}", group=group)
            issue.cycle = c
            issue.save()
        backfill_cycle_snapshots(c, c.end_date, "count")
        c.status = Cycle.Status.COMPLETED
        c.save()
        from plane.db.services.agile_reports import on_cycle_completed

        on_cycle_completed(c, list(c.issues.filter(state__group__in=["started"])))
        return c

    def test_burndown_active_has_realtime_point(self, env):
        c = _mk_cycle(env, status=Cycle.Status.ACTIVE)
        _mk_issue(env, "t", group="started")
        c.issues.set(Issue.objects.filter(project=env["proj"]))
        payload = AgileReportService().burndown(c.id)
        assert payload.frozen is False
        assert payload.points and payload.points[-1].get("provisional") is True

    def test_velocity_final_anchor_and_planned(self, env):
        self._archive_cycle(env, "S22", done=8, total=10, end_days=13)
        self._archive_cycle(env, "S23", done=6, total=10)
        payload = AgileReportService().velocity(env["proj"].id)
        assert [b["cycle"] for b in payload["bars"]] == ["S22", "S23"]
        assert payload["bars"][0]["completed"] == 8  # count 度量：completed 组任务数
        assert payload["bars"][0]["planned"] == 10  # 首日快照 scope_total
        assert payload["moving_avg"][-1] == 7.0  # (8+6)/2
        # BR-04 唯一锚：complete 后 beat 迟到补跑多出更新快照——velocity 仍取
        # is_final 终版而非 last()（对 last() 取值突变红，UT-14 变体）
        later = AgileReportService()._first_day_snapshot.__self__  # noqa: F841 语义锚注释
        CycleSnapshot.objects.create(
            cycle=payload["bars"][0] and Cycle.objects.get(name="S22"),
            snapshot_date=timezone.localdate(),  # 迟到补跑：比终版更新的日快照
            measure="count",
            remaining_by_group={"backlog": 0, "unstarted": 0, "started": 3, "completed": 99, "cancelled": 0},
            remaining_total=3,
            scope_total=102,
        )
        payload2 = AgileReportService().velocity(env["proj"].id)
        assert payload2["bars"][0]["completed"] == 8  # 终版锚值不变，非 last() 的 99

    def test_cfd_reads_snapshots(self, env):
        cycle_daily_snapshot()
        svc = AgileReportService()
        from datetime import date

        payload = svc.cumulative_flow(env["proj"].id, date(2026, 1, 1), timezone.localdate(), "count")
        assert len(payload["series"]) >= 1

    def test_config_patch_and_validation(self, env):
        base = f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/reports/"
        assert _client(env["owner"]).get(f"{base}config/").json()["data"] == {"report_measure": "count"}
        resp = _client(env["owner"]).patch(f"{base}config/", {"report_measure": "estimate_minutes"}, format="json")
        assert resp.status_code == 200
        assert resp.json()["data"]["report_measure"] == "estimate_minutes"
        bad = _client(env["owner"]).patch(f"{base}config/", {"report_measure": "bogus"}, format="json")
        assert bad.status_code == 400

    def test_export_requires_permission(self, env):
        """report.export 默认 PROJ_ADMIN（viewer 403）——owner 200。"""
        c = _mk_cycle(env, status=Cycle.Status.ACTIVE)
        c.issues.set([])
        resp_v = _client(env["viewer"]).get(f"{_base(env)}{c.id}/burndown/export/")
        assert resp_v.status_code == 403
        resp_o = _client(env["owner"]).get(f"{_base(env)}{c.id}/burndown/export/")
        assert resp_o.status_code == 200
        assert resp_o["Content-Type"].startswith("text/csv")
