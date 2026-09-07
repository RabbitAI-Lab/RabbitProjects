"""RPT-002 项目统计测试（T5-06）——项目进度 + 成员任务量。

覆盖：五组分布/完成率（剔 cancelled）/逾期口径/工时四数（remaining/overrun/
unestimated）/趋势补零与 days 参数；成员行/未指派/合计的口径一致性
（BR-01：与逐条筛选结果一致）；限流（BR-13：10/min·user → 第 11 次 429）；
参数校验（days/tz/role/order_by 白名单 400）；越权 404 存在性隐藏。
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from plane.db.models import (
    Issue,
    IssueAssignee,
    Project,
    ProjectMember,
    ProjectRole,
    State,
    User,
    WorkLog,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.roles import WorkspaceRole
from plane.db.seeds.project_states import seed_project_states

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="r2-owner@rabbit.dev", password="Rabbit123!",
                                     display_name="张三")
    zhang = User.objects.create_user(email="r2-zhang@rabbit.dev", password="Rabbit123!",
                                     display_name="李四")
    viewer = User.objects.create_user(email="r2-viewer@rabbit.dev", password="Rabbit123!",
                                      display_name="只读")
    outsider = User.objects.create_user(email="r2-out@rabbit.dev", password="Rabbit123!",
                                        display_name="外人")
    ws = Workspace.objects.create(name="W", slug=f"w-r2-{owner.id.hex[:8]}",
                                  owner=owner, created_by=owner)
    for u, r in ((owner, WorkspaceRole.OWNER), (zhang, WorkspaceRole.MEMBER),
                 (viewer, WorkspaceRole.MEMBER)):
        WorkspaceMember.objects.create(workspace=ws, member=u, role=r, created_by=owner)
    proj = Project.objects.create(name="P", identifier="R02", workspace=ws, created_by=owner)
    ProjectMember.objects.create(project=proj, member=zhang, role=ProjectRole.CONTRIBUTOR,
                                 created_by=owner)
    ProjectMember.objects.create(project=proj, member=viewer, role=ProjectRole.VIEWER,
                                 created_by=owner)
    seed_project_states(proj)
    if not State.objects.filter(project=proj, group=State.Group.BACKLOG).exists():
        State.objects.create(project=proj, name="待规划", group=State.Group.BACKLOG,
                             color="#94A3B8", sort_order=100.0, created_by=owner)
    states = {s.group: s for s in State.objects.filter(project=proj)}
    today = timezone.now().date()

    def mk(name, *, group="unstarted", assignee=None, target=None, estimate=None,
           completed_days_ago=None, archived=False):
        issue = Issue.objects.create(
            name=name, project=proj, state=states[group], priority="none",
            sequence_id=mk.seq, sort_order=mk.seq * 100, created_by=owner,
            target_date=target, estimate_minutes=estimate,
            archived_at=timezone.now() if archived else None,
            completed_at=(timezone.now() - timedelta(days=completed_days_ago))
            if completed_days_ago is not None else None,
        )
        mk.seq += 1
        if assignee is not None:
            IssueAssignee.objects.create(issue=issue, assignee=assignee, created_by=owner)
        return issue

    mk.seq = 1
    mk("t1", group="started", assignee=zhang, estimate=120)
    mk("t2", group="started", assignee=zhang, target=today - timedelta(days=3))  # 逾期
    mk("t3", group="completed", assignee=zhang, estimate=60,
       completed_days_ago=2)
    mk("t4", group="unstarted")                    # 未指派 + 无估算（backlog 非 open 口径）
    mk("t5", group="cancelled")
    mk("t6", group="completed", completed_days_ago=40)  # 30d 窗口外
    mk("t7", group="unstarted", archived=True)     # 归档不计
    # 作用域限定到本项目：dev 库可能有同名任务残留（jMeter flow 的 S5A3C 副本
    # 曾带 t1 打爆全局 get——坑 18 同族教训，2026-09-07 收口）
    t1 = Issue.objects.get(name="t1", project=proj)
    WorkLog.objects.create(issue=t1, actor=zhang,
                           minutes=90, worked_on=today, created_by=owner)
    WorkLog.objects.create(issue=t1, actor=zhang,
                           minutes=60, worked_on=today - timedelta(days=40),
                           created_by=owner)  # 30d 窗口外
    return {"owner": owner, "zhang": zhang, "viewer": viewer, "outsider": outsider,
            "ws": ws, "proj": proj, "today": today}


def _client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _url(env, suffix=""):
    return f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/stats/{suffix}"


@pytest.fixture(autouse=True)
def _clear_throttle():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


def test_progress_numbers(env):
    res = _client(env["viewer"]).get(_url(env), format="json")
    assert res.status_code == 200
    d = res.json()["data"]
    assert d["state_distribution"] == {"backlog": 0, "unstarted": 1, "started": 2,
                                       "completed": 2, "cancelled": 1}
    assert d["total"] == 6                      # 归档 t7 不计
    # 完成率剔 cancelled：2 / (6-1) = 0.4
    assert d["completion_rate"] == 0.4
    assert d["overdue_count"] == 1              # 仅 t2
    w = d["worklog_summary"]
    assert w["estimate_minutes"] == 120         # open 估算合计：仅 t1（t2/t4 无估算）
    assert w["logged_minutes"] == 150           # 全量登记（90 今日 + 60 窗口外，无 30d 口径）
    assert w["remaining_minutes"] == 0
    assert w["overrun_minutes"] == 30
    assert w["unestimated_count"] == 2          # t2/t4 open 且无估算
    assert d["trend"]["days"] == 30 and len(d["trend"]["created"]) == 30


def test_progress_days_whitelist_400(env):
    res = _client(env["viewer"]).get(_url(env) + "?days=15", format="json")
    assert res.status_code == 400
    res = _client(env["viewer"]).get(_url(env) + "?tz=Not/AZone", format="json")
    assert res.status_code == 400


def test_progress_headers_and_throttle(env):
    c = _client(env["viewer"])
    res = c.get(_url(env), format="json")
    assert res.headers.get("Cache-Control") == "no-store"
    assert res.headers.get("X-RateLimit-Limit") == "10"
    for _ in range(9):  # 首请求已耗 1——合计 10 次全在窗口内
        r = c.get(_url(env), format="json")
        assert r.status_code == 200
    r = c.get(_url(env), format="json")  # 第 11 次 → 429
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"


def test_progress_outsider_404(env):
    assert _client(env["outsider"]).get(_url(env), format="json").status_code == 404


def test_members_rows_and_consistency(env):
    res = _client(env["viewer"]).get(_url(env, "members/"), format="json")
    assert res.status_code == 200
    d = res.json()["data"]
    rows = {r["display_name"]: r for r in d["rows"]}
    z = rows["李四"]
    assert z["open_count"] == 2                 # t1 + t2
    assert z["done_count_30d"] == 1             # t3（t6 在窗口外）
    assert z["overdue_count"] == 1
    assert z["estimate_minutes_open"] == 120
    assert z["logged_minutes_30d"] == 90
    assert z["is_active"] is True
    # 未指派：t4（t5 cancelled、t6 completed、t7 归档均不计 open）
    assert d["unassigned"]["open_count"] == 1
    assert d["unassigned"]["estimate_minutes_open"] == 0
    # 口径一致性（BR-01）：合计 = 逐成员行之和 + 未指派
    assert d["totals"]["open_count"] == (
        sum(r["open_count"] for r in d["rows"]) + d["unassigned"]["open_count"])
    assert d["totals"]["logged_minutes_30d"] == sum(
        r["logged_minutes_30d"] for r in d["rows"])
    # 手工逐条筛选对照（口径单源验证）
    manual_open = Issue.objects.filter(
        project=env["proj"], archived_at__isnull=True,
        state__group__in=("unstarted", "started")).count()
    assert d["totals"]["open_count"] == manual_open


def test_members_role_filter_and_validation(env):
    c = _client(env["viewer"])
    res = c.get(_url(env, "members/") + "?role=PROJ_CONTRIBUTOR", format="json")
    rows = res.json()["data"]["rows"]
    assert len(rows) == 1 and rows[0]["role"] == ProjectRole.CONTRIBUTOR
    assert c.get(_url(env, "members/") + "?role=PROJ_BOSS", format="json").status_code == 400
    assert c.get(_url(env, "members/") + "?order_by=-hacked", format="json").status_code == 400


def test_members_ordering(env):
    res = _client(env["viewer"]).get(
        _url(env, "members/") + "?order_by=-logged_minutes_30d", format="json")
    rows = res.json()["data"]["rows"]
    logged = [r["logged_minutes_30d"] for r in rows]
    assert logged == sorted(logged, reverse=True)


def test_members_outsider_404(env):
    assert _client(env["outsider"]).get(
        _url(env, "members/"), format="json").status_code == 404
