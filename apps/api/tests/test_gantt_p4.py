"""跨项目甘特三件套测试（GANTT-004/005/006，P4 R15）。"""

from __future__ import annotations

import datetime as dt

import pytest
from rest_framework.test import APIClient

from plane.db.models import (
    Issue,
    IssueCPMCache,
    Portfolio,
    PortfolioMilestone,
    PortfolioProject,
    Project,
    User,
    WorkLogSummary,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.roles import WorkspaceRole

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="pg-owner@rabbit.dev", password="Rabbit123!", display_name="主")
    ws = Workspace.objects.create(name="PG", slug=f"w-pg-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=owner, role=WorkspaceRole.OWNER, created_by=owner)
    proj = Project.objects.create(workspace=ws, name="P1", identifier="PG1", created_by=owner)
    proj2 = Project.objects.create(workspace=ws, name="P2", identifier="PG2", created_by=owner)
    pf = Portfolio.objects.create(workspace=ws, name="组合", manager=owner, created_by=owner)
    PortfolioProject.objects.create(portfolio=pf, project=proj, created_by=owner)
    PortfolioProject.objects.create(portfolio=pf, project=proj2, created_by=owner)
    return {"owner": owner, "ws": ws, "proj": proj, "proj2": proj2, "pf": pf}


def _c(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_portfolio_gantt_range_and_milestone(env):
    Issue.objects.create(
        project=env["proj"],
        name="a",
        sequence_id=1,
        start_date=dt.date(2026, 9, 1),
        target_date=dt.date(2026, 9, 10),
        created_by=env["owner"],
    )
    Issue.objects.create(
        project=env["proj"], name="b", sequence_id=2, target_date=dt.date(2026, 9, 20), created_by=env["owner"]
    )
    PortfolioMilestone.objects.create(
        portfolio=env["pf"], name="M1", target_date=dt.date(2026, 9, 25), created_by=env["owner"]
    )
    r = _c(env["owner"]).get(f"/api/v1/workspaces/{env['ws'].slug}/portfolios/{env['pf'].id}/gantt/")
    assert r.status_code == 200
    data = r.json()["data"]
    p1 = next(p for p in data["projects"] if p["name"] == "P1")
    assert p1["range"]["start"] == "2026-09-01"
    assert p1["range"]["target"] == "2026-09-20"  # min/max 聚合（BR-01）
    assert data["milestones"][0]["late"] is True  # 9/25 > 9/20 超期（BR-02）


def test_resource_load_bands(env):

    week = dt.date(2026, 9, 7)  # 周一
    WorkLogSummary.objects.create(
        project=env["proj"], actor=env["owner"], week_start=week, total_minutes=2600, created_by=env["owner"]
    )
    r = _c(env["owner"]).get(f"/api/v1/workspaces/{env['ws'].slug}/resource-load/?weeks=2")
    assert r.status_code == 200
    rows = r.json()["data"]
    assert rows, "至少一行"
    hot = [w for w in rows[0]["weeks"] if w["minutes"]]
    assert hot[0]["ratio"] == round(2600 / 2400, 3)  # 缺省容量（BR-01）
    assert hot[0]["band"] == "red"  # >100% 红（BR-02）


def test_cp_lock_snapshot_not_drifting(env):
    issue = Issue.objects.create(project=env["proj"], name="关键任务", sequence_id=1, created_by=env["owner"])
    IssueCPMCache.objects.create(
        project=env["proj"],
        issue=issue,
        es=dt.date(2026, 9, 1),
        ef=dt.date(2026, 9, 5),
        ls=dt.date(2026, 9, 1),
        lf=dt.date(2026, 9, 5),
        float_days=0,
        is_critical=True,
        created_by=env["owner"],
    )
    c = _c(env["owner"])
    base = f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/gantt/cp-lock/"
    r = c.post(base, {}, format="json")
    assert r.status_code == 201 and r.json()["data"]["chain_len"] == 1
    # 重复锁定 409（BR-01）
    r_dup = c.post(base, {}, format="json")
    assert r_dup.status_code == 409
    # 任务改期——快照不漂移
    IssueCPMCache.objects.filter(issue=issue).update(ef=dt.date(2026, 10, 1))
    snap = c.get(base).json()["data"]["snapshot"]
    assert snap["chain"][0]["ef"] == "2026-09-05"  # 锁定值（BR-02）
    # 解锁 → 回实时
    c.delete(base)
    assert c.get(base).json()["data"]["locked"] is False
