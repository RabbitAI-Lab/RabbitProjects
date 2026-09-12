"""企业大屏与自定义报表测试（RPT-005，P4 R7 门禁）。"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from plane.db.models import (
    Dashboard,
    Issue,
    Project,
    Report,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.roles import WorkspaceRole
from plane.db.services.report_query import DATASETS, ReportQueryEngine

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="rpt-owner@rabbit.dev", password="Rabbit123!", display_name="主")
    member = User.objects.create_user(email="rpt-m@rabbit.dev", password="Rabbit123!", display_name="员")
    ws = Workspace.objects.create(name="RPT", slug=f"w-rp-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=owner, role=WorkspaceRole.OWNER, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=member, role=WorkspaceRole.MEMBER, created_by=member)
    proj = Project.objects.create(workspace=ws, name="P", identifier="RP1", created_by=owner)
    for i in range(3):
        Issue.objects.create(project=proj, name=f"t{i}", sequence_id=i + 1, created_by=owner)
    Issue.objects.filter(sequence_id=1).update(completed_at="2026-09-01")
    return {"owner": owner, "member": member, "ws": ws, "proj": proj}


def _c(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_registry_and_issue_live_engine(env):
    assert set(DATASETS) >= {"ds_issue_live", "ds_cycle", "ds_worklog", "ds_health"}
    engine = ReportQueryEngine()
    r = engine.execute(
        env["ws"], {"dataset": "ds_issue_live", "metrics": ["m_issue_count", "m_done_count"]}, viewer=env["owner"]
    )
    assert r["rows"][0]["m_issue_count"] == 3
    assert r["rows"][0]["m_done_count"] == 1
    assert r["data_until"] == "realtime"
    r_month = engine.execute(
        env["ws"],
        {"dataset": "ds_issue_live", "metrics": ["m_issue_count"], "dimensions": ["d_month"]},
        viewer=env["owner"],
    )
    assert r_month["rows"] and "key" in r_month["rows"][0]
    with pytest.raises(ValueError):
        engine.execute(env["ws"], {"dataset": "ds_bogus", "metrics": ["x"]})


def test_report_crud_version_lock_and_preview(env):
    c = _c(env["owner"])
    base = f"/api/v1/workspaces/{env['ws'].slug}/reports"
    r_member = _c(env["member"]).post(f"{base}/", {"name": "x"}, format="json")
    assert r_member.status_code == 403  # 写面 WS_ADMIN+
    r = c.post(
        f"{base}/",
        {"name": "完成趋势", "config": {"dataset": "ds_issue_live", "metrics": ["m_done_count"]}},
        format="json",
    )
    assert r.status_code == 201
    rid = r.json()["data"]["id"]
    r_dup = c.post(f"{base}/", {"name": "完成趋势"}, format="json")
    assert r_dup.status_code == 409
    # 乐观锁
    detail = c.patch(f"{base}/{rid}/", {"config": {"metrics": []}}, format="json")
    v = detail.json()["data"]["version"]
    r_conflict = c.patch(f"{base}/{rid}/", {"version": 0}, format="json")
    assert r_conflict.status_code == 409
    # 预览
    r_prev = c.post(
        f"{base}/preview/", {"config": {"dataset": "ds_issue_live", "metrics": ["m_done_count"]}}, format="json"
    )
    assert r_prev.status_code == 200 and r_prev.json()["data"]["rows"][0]["m_done_count"] == 1
    r_bad = c.post(f"{base}/preview/", {"config": {"dataset": "x"}}, format="json")
    assert r_bad.status_code == 400
    # 注册表端点
    r_reg = c.get(f"{base}/metrics/")
    assert "ds_issue_live" in r_reg.json()["data"]


def test_dashboard_and_display_token_chain(env):
    c = _c(env["owner"])
    base = f"/api/v1/workspaces/{env['ws'].slug}/dashboards"
    r = c.post(
        f"{base}/",
        {
            "name": "作战室",
            "theme": "dark",
            "layout": {"items": [{"report_id": "r1", "x": 0, "y": 0, "w": 12, "h": 6}]},
        },
        format="json",
    )
    assert r.status_code == 201
    bid = r.json()["data"]["id"]
    r_tok = c.post(f"{base}/{bid}/display-tokens/", {}, format="json")
    assert r_tok.status_code == 201
    token = r_tok.json()["data"]["token"]
    # 播放面匿名可达
    anon = APIClient()
    r_screen = anon.get(f"/api/v1/display/{token}/screen/0/")
    assert r_screen.status_code == 200 and r_screen.json()["data"]["screens"] == 1
    r_hb = anon.post(f"/api/v1/display/{token}/heartbeat/", {}, format="json")
    assert r_hb.json()["data"]["alive"] is True
    # 吊销后 404
    tid = r_tok.json()["data"]["id"]
    c.delete(f"{base}/{bid}/display-tokens/{tid}/")
    r_after = anon.get(f"/api/v1/display/{token}/screen/0/")
    assert r_after.status_code == 404
    # 越界屏 404
    r_oob = anon.get(f"/api/v1/display/{token}/screen/9/")
    assert r_oob.status_code == 404


def test_subscription_created(env):
    report = Report.objects.create(
        workspace=env["ws"], name="订阅对象", config={}, owner=env["owner"], created_by=env["owner"]
    )
    c = _c(env["owner"])
    r = c.post(
        f"/api/v1/workspaces/{env['ws'].slug}/reports/{report.id}/subscriptions/",
        {"schedule": "weekly", "channel": {"type": "email", "recipients": ["a@b.c"]}},
        format="json",
    )
    assert r.status_code == 201
    r_bad = c.post(
        f"/api/v1/workspaces/{env['ws'].slug}/reports/{report.id}/subscriptions/", {"schedule": "hourly"}, format="json"
    )
    assert r_bad.status_code == 400
