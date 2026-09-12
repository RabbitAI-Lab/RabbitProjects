"""R16~R18 收口轮测试（BOARD-006/007 + WF-007 + PROJ-005 + COLLAB-005）。"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from plane.app.views.p4_final import route_push, wf_timeout_sweep
from plane.db.models import (
    Issue,
    Project,
    ProjectMember,
    State,
    User,
    WorkLogSummary,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.roles import ProjectRole, WorkspaceRole

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="pf-owner@rabbit.dev", password="Rabbit123!", display_name="主")
    ws = Workspace.objects.create(name="PF", slug=f"w-pf-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=owner, role=WorkspaceRole.OWNER, created_by=owner)
    proj = Project.objects.create(workspace=ws, name="P", identifier="PF1", created_by=owner)
    return {"owner": owner, "ws": ws, "proj": proj}


def _c(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user)
    return c


def _state(env, group, name=None):
    state, _ = State.objects.get_or_create(
        project=env["proj"], name=name or group, defaults={"group": group, "created_by": env["owner"]}
    )
    return state


def test_global_board_lanes_and_visibility(env):
    s_todo = _state(env, "unstarted", "待办")
    s_done = _state(env, "completed", "完成")
    Issue.objects.create(project=env["proj"], name="t1", sequence_id=1, state=s_todo, created_by=env["owner"])
    Issue.objects.create(project=env["proj"], name="t2", sequence_id=2, state=s_done, created_by=env["owner"])
    r = _c(env["owner"]).get(f"/api/v1/workspaces/{env['ws'].slug}/global-board/")
    assert r.status_code == 200
    lanes = {l["key"]: l["count"] for l in r.json()["data"]["lanes"]}
    assert lanes["unstarted"] == 1 and lanes["completed"] == 1  # UT-01
    # UT-02 不可见项目不进聚合：WS_MEMBER 且无项目成员行 → accessible_by
    # 项目集为空（AUTH-006 行级矩阵口径）——聚合为 0
    stranger = User.objects.create_user(email="pf-str@rabbit.dev", password="Rabbit123!")
    WorkspaceMember.objects.create(workspace=env["ws"], member=stranger, role=WorkspaceRole.MEMBER, created_by=stranger)
    r2 = _c(stranger).get(f"/api/v1/workspaces/{env['ws'].slug}/global-board/")
    lanes2 = {l["key"]: l["count"] for l in r2.json()["data"]["lanes"]}
    assert sum(lanes2.values()) == 0  # 行级隔离生效
    # WS_ADMIN+ 隐式可见全项目（rbac §7.4）
    admin = User.objects.create_user(email="pf-adm@rabbit.dev", password="Rabbit123!", display_name="管")
    WorkspaceMember.objects.create(workspace=env["ws"], member=admin, role=WorkspaceRole.ADMIN, created_by=admin)
    r3 = _c(admin).get(f"/api/v1/workspaces/{env['ws'].slug}/global-board/")
    lanes3 = {l["key"]: l["count"] for l in r3.json()["data"]["lanes"]}
    assert lanes3["unstarted"] + lanes3["completed"] == 2


def test_view_template_publish_and_apply(env):
    c = _c(env["owner"])
    base = f"/api/v1/workspaces/{env['ws'].slug}/view-templates"
    r = c.post(
        f"{base}/", {"name": "我的工序", "filter_json": {"state": "x"}, "columns_json": ["id", "name"]}, format="json"
    )
    assert r.status_code == 201 and r.json()["data"]["version"] == 1  # UT-01
    r2 = c.post(f"{base}/", {"name": "我的工序", "filter_json": {"state": "y"}}, format="json")
    assert r2.json()["data"]["version"] == 2  # 版本递增
    tid = r.json()["data"]["id"]
    r_apply = c.post(f"{base}/{tid}/apply/?project_id={env['proj'].id}", {}, format="json")
    assert r_apply.status_code == 201  # UT-02
    from plane.db.models import IssueView

    view = IssueView.objects.get(pk=r_apply.json()["data"]["view_id"])
    assert view.filters == {"state": "y"}  # 应用最新版快照（BR-02 拷贝）


def test_timeout_rule_and_sweep(env):
    from_state = _state(env, "started", "进行中")
    to_state = _state(env, "unstarted", "退回待办")
    c = _c(env["owner"])
    r = c.post(
        f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/workflow/timeout-rules/",
        {"from_state_id": str(from_state.id), "to_state_id": str(to_state.id), "hours": 72},
        format="json",
    )
    assert r.status_code == 201
    issue = Issue.objects.create(
        project=env["proj"], name="滞留", sequence_id=9, state=from_state, created_by=env["owner"]
    )
    Issue.objects.filter(pk=issue.pk).update(
        updated_at=__import__("django.utils.timezone", fromlist=["timezone"]).now() - dt.timedelta(hours=80)
    )
    result = wf_timeout_sweep()
    assert result["moved"] >= 1  # UT-01 流转
    issue.refresh_from_db()
    assert issue.state_id == to_state.id


def test_resource_scheduling(env):
    week = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())
    idle = User.objects.create_user(email="pf-idle@rabbit.dev", password="Rabbit123!", display_name="闲置")
    WorkspaceMember.objects.create(workspace=env["ws"], member=idle, role=WorkspaceRole.MEMBER, created_by=idle)
    WorkLogSummary.objects.create(
        project=env["proj"], actor=env["owner"], week_start=week, total_minutes=3000, created_by=env["owner"]
    )  # >100% 超载
    WorkLogSummary.objects.create(
        project=env["proj"], actor=idle, week_start=week, total_minutes=300, created_by=idle
    )  # <50% 闲置
    r = _c(env["owner"]).get(f"/api/v1/workspaces/{env['ws'].slug}/resource-scheduling/")
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data["overloaded"]) == 1 and len(data["underutilized"]) == 1  # UT-01
    assert (
        data["suggestions"]
        and data["suggestions"][0]["from"] == str(env["owner"].id)
        and data["suggestions"][0]["to"] == str(idle.id)
    )  # UT-02 平衡对


def test_push_preferences_and_router(env):
    c = _c(env["owner"])
    cache.delete(f"pushpref:{env['owner'].id}")
    r = c.get("/api/v1/users/me/push-preferences/")
    assert r.status_code == 200 and "im" in r.json()["data"]["channel_weights"]
    # DND 静默（UT-01）
    c.patch("/api/v1/users/me/push-preferences/", {"dnd": {"start": 0, "end": 23}}, format="json")
    silent = route_push(str(env["owner"].id), "comment", {"x": 1})
    assert silent["action"] == "silent"
    # P1 绕过（UT-03）
    p1 = route_push(str(env["owner"].id), "approval", {"x": 1})
    assert p1["action"] == "send"
    # 关 DND + 聚合窗口合并（UT-02）
    c.patch(
        "/api/v1/users/me/push-preferences/", {"dnd": {"start": None, "end": None}, "digest_minutes": 10}, format="json"
    )
    cache.delete(f"pushdigest:{env['owner'].id}:comment")
    first = route_push(str(env["owner"].id), "comment", {"n": 1})
    second = route_push(str(env["owner"].id), "comment", {"n": 2})
    assert first["action"] == "send" and second["action"] == "digest"
