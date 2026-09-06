"""GANTT-001 甘特核心单元测试（UT-01~21 + IT-07 索引断言；直连 PG）。

覆盖：视窗相交判定（BR-02）、未排期/聚合条（BR-03/BR-11）、进度唯一口径
（BR-04）、请求级 tz（BR-05）、连线批量去重与 violation 派生（§4.3.3）、
视窗参数与 tz 校验、批量上限、权限（BR-12）、筛选复用（BR-01）、工时对照
（TASK-006 契约字段）、游标分页（api-conventions §6.2/§6.3）。
性能门禁（IT-01/IT-02）在 tests/jmeter/sprint-4-bench-gantt.py（HTTP 采样口径）。

GANTT-002 后端子集（§5 中后端相关用例，前缀 g002）：
- UT-12/13/18：概览口径（completed 豁免）/ 多人分布（每人各计 1）/ 读权限；
- IT-04：概览 vs 行级 is_overdue 同源性证明（同一数据集数字一致，含开放端条、
  NULL state、今日边界、归档）；
- UT-17/IT-07：端点级限流 10/min·user（真实 Valkey 计数，FILE-004 redis_clean
  同款隔离纪律；键 gantt-agg:{user_id}，夹具 teardown 显式清键）；
- 截断前 20 / 响应形状 / 筛选同源管道（view_id 与 ?filters=）/ tz 同基准。
夹具纪律（坑 18）：断言一律 filter 到本测试作用域，禁全表 count()（_issue 的
sequence 取数是项目作用域 filter）。
"""
from __future__ import annotations

import time
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from plane.app.views import gantt as gantt_views
from plane.db.models import (
    Issue,
    IssueAssignee,
    IssueLink,
    IssueView,
    Project,
    ProjectMember,
    ProjectRole,
    State,
    User,
    WorkLog,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)

pytestmark = pytest.mark.django_db

VP = {"start": "2026-09-01", "end": "2026-09-30"}


@pytest.fixture()
def env(db):
    """owner（WS OWNER）+ viewer（项目 VIEWER）+ 五语义组状态齐全的项目。"""
    owner = User.objects.create_user(email="gantt-owner@rabbit.dev", password="Rabbit123!")
    viewer = User.objects.create_user(email="gantt-viewer@rabbit.dev", password="Rabbit123!")
    ws = Workspace.objects.create(name="W", slug=f"w-gantt-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    # HTTP 面用例需过 get_project_or_404 的成员校验（坑：夹具必须建 WorkspaceMember）
    WorkspaceMember.objects.create(workspace=ws, member=owner, role=WorkspaceRole.OWNER, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=viewer, role=WorkspaceRole.MEMBER, created_by=owner)
    proj = Project.objects.create(name="P", identifier="GNT", workspace=ws, created_by=owner)
    ProjectMember.objects.create(project=proj, member=viewer, role=ProjectRole.VIEWER, created_by=owner)
    states = {}
    for group, name in (
        ("backlog", "待办"), ("unstarted", "未开始"), ("started", "进行中"),
        ("completed", "已完成"), ("cancelled", "已取消"),
    ):
        states[group] = State.objects.create(project=proj, name=name, group=group, color="#123456")
    return {"owner": owner, "viewer": viewer, "ws": ws, "proj": proj, "states": states}


def _issue(env, name: str, *, start=None, target=None, state="started", parent=None,
           priority="none", estimate=None, sort=None) -> Issue:
    seq = Issue.objects.filter(project=env["proj"]).count() + 1
    return Issue.objects.create(
        name=name, project=env["proj"], sequence_id=seq,
        sort_order=sort if sort is not None else seq * 100,
        start_date=start, target_date=target,
        state=env["states"][state], priority=priority,
        parent=parent, estimate_minutes=estimate, created_by=env["owner"],
    )


def _client(user):
    from rest_framework.test import APIClient

    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _rows(client, env, *, start=VP["start"], end=VP["end"], **params):
    qs = f"?viewport_start={start}&viewport_end={end}"
    for k, v in params.items():
        qs += f"&{k}={v}"
    resp = client.get(f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/gantt/{qs}")
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    return body["data"], body["meta"]


def _bulk(client, env, ids):
    resp = client.post(
        f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/gantt/relations/bulk/",
        {"issue_ids": [str(i) for i in ids]}, format="json",
    )
    assert resp.status_code == 200, resp.json()
    return resp.json()["data"], resp.json()["meta"]


def _by_id(data):
    """rows[] → {UUID: row}（id 键转 UUID，与 ORM 对象 id 同型可比）。"""
    return {uuid.UUID(r["id"]): r for r in data["rows"]}


# ─────────────────────────────────────────────────────────────────────
# UT-01~05：视窗相交判定（BR-02 / BR-03）
# ─────────────────────────────────────────────────────────────────────
def test_ut01_intersect_partial_overlap(env):
    """条 9-01~9-08，视窗 9-05~9-30 → 命中（尾端进入视窗）。"""
    a = _issue(env, "A", start="2026-09-01", target="2026-09-08")
    data, _ = _rows(_client(env["owner"]), env)
    assert a.id in _by_id(data)


def test_ut02_intersect_full_year_crossing(env):
    """条 1-01~12-31，视窗 8 月 → 命中（跨整年任务在任意月视窗都渲染）。"""
    a = _issue(env, "A", start="2026-01-01", target="2026-12-31")
    data, _ = _rows(_client(env["owner"]), env, start="2026-08-01", end="2026-08-31")
    assert a.id in _by_id(data)


def test_ut03_disjoint_excluded(env):
    """条 8-01~8-15，视窗 9 月 → 不返回；且不是未排期。"""
    _issue(env, "A", start="2026-08-01", target="2026-08-15")
    data, meta = _rows(_client(env["owner"]), env)
    assert data["rows"] == []
    assert data["unscheduled_count"] == 0
    assert meta["total_count"] == 0


def test_ut04_single_null_open_end(env):
    """start NULL / target 9-10，视窗 9 月 → 命中且 start_date 下发 null（开放端）。"""
    a = _issue(env, "A", start=None, target="2026-09-10")
    data, _ = _rows(_client(env["owner"]), env)
    row = _by_id(data)[a.id]
    assert row["start_date"] is None and row["target_date"] == "2026-09-10"
    # target NULL 对称：end NULL / start 9-10 同样命中
    b = _issue(env, "B", start="2026-09-10", target=None)
    data2, _ = _rows(_client(env["owner"]), env)
    assert _by_id(data2)[b.id]["target_date"] is None


def test_ut05_double_null_goes_unscheduled(env):
    """双 NULL 且子树无日期 → 不入 rows；unscheduled_count +1；unscheduled/ 列表一致。"""
    a = _issue(env, "A", start=None, target=None)
    client = _client(env["owner"])
    data, meta = _rows(client, env)
    assert a.id not in _by_id(data)
    assert data["unscheduled_count"] == 1
    resp = client.get(
        f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/gantt/unscheduled/")
    assert resp.status_code == 200
    body = resp.json()
    assert [r["id"] for r in body["data"]] == [str(a.id)]
    assert body["meta"]["total_count"] == 1 == data["unscheduled_count"]  # IT-06：列表一致
    # 未排期行 fields 裁剪：编号/标题/状态/执行人
    row = body["data"][0]
    assert set(row) == {"id", "issue_key", "name", "state_group", "state_color", "assignee_ids"}
    assert row["issue_key"] == f"GNT-{a.sequence_id}"


# ─────────────────────────────────────────────────────────────────────
# UT-06~08：进度唯一口径（BR-04 / §1.2）
# ─────────────────────────────────────────────────────────────────────
def test_ut06_progress_subtasks_ratio(env):
    """父 + 5 子（2 完成）→ progress 40 / source=subtasks。"""
    parent = _issue(env, "P", start="2026-09-01", target="2026-09-08")
    child_states = ["started", "completed", "started", "started", "completed"]
    for i, st in enumerate(child_states):
        _issue(env, f"C{i}", start="2026-09-01", target="2026-09-02", state=st, parent=parent)
    data, _ = _rows(_client(env["owner"]), env)
    row = _by_id(data)[parent.id]
    assert row["progress"] == 40 and row["progress_source"] == "subtasks"
    assert row["has_children"] is True and row["depth"] == 0


def test_ut07_progress_state_group_started_50(env):
    a = _issue(env, "A", start="2026-09-01", target="2026-09-08", state="started")
    data, _ = _rows(_client(env["owner"]), env)
    row = _by_id(data)[a.id]
    assert row["progress"] == 50 and row["progress_source"] == "state"
    assert row["has_children"] is False


def test_ut08_progress_cancelled_no_fill(env):
    """cancelled → progress 0 / source=state（条体虚线由前端按 state_group 渲染）。"""
    a = _issue(env, "A", start="2026-09-01", target="2026-09-08", state="cancelled")
    data, _ = _rows(_client(env["owner"]), env)
    row = _by_id(data)[a.id]
    assert row["progress"] == 0 and row["progress_source"] == "state"
    assert row["state_group"] == "cancelled"


# ─────────────────────────────────────────────────────────────────────
# UT-09~10：逾期判定（§4.3.2 is_overdue）
# ─────────────────────────────────────────────────────────────────────
def test_ut09_overdue_true_for_started(env):
    a = _issue(env, "A", start="2026-08-01", target=date.today() - timedelta(days=1), state="started")
    data, _ = _rows(_client(env["owner"]), env)
    assert _by_id(data)[a.id]["is_overdue"] is True


def test_ut10_overdue_exempt_completed_and_cancelled(env):
    yesterday = date.today() - timedelta(days=1)
    done = _issue(env, "DONE", start="2026-08-01", target=yesterday, state="completed")
    cancel = _issue(env, "CANCEL", start="2026-08-01", target=yesterday, state="cancelled")
    data, _ = _rows(_client(env["owner"]), env)
    rows = _by_id(data)
    assert rows[done.id]["is_overdue"] is False
    assert rows[cancel.id]["is_overdue"] is False


# ─────────────────────────────────────────────────────────────────────
# UT-11~12 / UT-14 / UT-21：连线批量（§4.3.3）
# ─────────────────────────────────────────────────────────────────────
def test_ut11_violation_only_for_blocks(env):
    """B.start < A.target（blocks 边）→ violation=true；relates_to 边恒 false。"""
    from plane.db.services.issue_link import create_relation

    a = _issue(env, "A", start="2026-09-01", target="2026-09-10")
    b = _issue(env, "B", start="2026-09-05", target="2026-09-20")
    c = _issue(env, "C", start="2026-09-05", target="2026-09-20")
    create_relation(issue_id=a.id, related_issue_id=b.id, relation_type="blocks", actor_id=env["owner"].id)
    create_relation(issue_id=a.id, related_issue_id=c.id, relation_type="relates_to", actor_id=env["owner"].id)
    data, meta = _bulk(_client(env["owner"]), env, [a.id, b.id, c.id])
    by_type = {e["relation_type"]: e for e in data["edges"]}
    assert by_type["blocks"]["to"]["violation"] is True  # B.start(9-05) < A.target(9-10)
    assert by_type["relates_to"]["to"]["violation"] is False
    assert meta == {"requested": 3, "edges": 2}
    # 端点字段形状（§4.2.2）
    e = by_type["blocks"]
    assert set(e) == {"from_issue_id", "to_issue_id", "relation_type", "from", "to"}
    assert e["from"]["issue_key"] == "GNT-1" and e["from"]["target_date"] == "2026-09-10"
    assert e["to"]["start_date"] == "2026-09-05"


def test_ut12_mirror_dedup(env):
    """blocks+is_blocked_by 两行 + relates_to 正反两行 → 各成 1 条边（§4.3.3 去重）。"""
    from plane.db.services.issue_link import create_relation

    a = _issue(env, "A", start="2026-09-01", target="2026-09-05")
    b = _issue(env, "B", start="2026-09-01", target="2026-09-05")
    create_relation(issue_id=a.id, related_issue_id=b.id, relation_type="blocks", actor_id=env["owner"].id)
    create_relation(issue_id=a.id, related_issue_id=b.id, relation_type="relates_to", actor_id=env["owner"].id)
    data, _ = _bulk(_client(env["owner"]), env, [a.id, b.id])
    types = sorted(e["relation_type"] for e in data["edges"])
    assert types == ["blocks", "relates_to"]  # 每业务关系恰一条，无镜像重复
    # 方向：blocks 为 A→B 正向
    blocks = next(e for e in data["edges"] if e["relation_type"] == "blocks")
    assert blocks["from_issue_id"] == str(a.id) and blocks["to_issue_id"] == str(b.id)


def test_ut14_bulk_cap_60(env):
    """61 ids → 截断 60（meta.requested=61）；被截断端点的边不返回。"""
    from plane.db.services.issue_link import create_relation

    a = _issue(env, "A", start="2026-09-01", target="2026-09-05")
    b = _issue(env, "B", start="2026-09-01", target="2026-09-05")
    create_relation(issue_id=a.id, related_issue_id=b.id, relation_type="blocks", actor_id=env["owner"].id)
    fillers = [uuid.uuid4() for _ in range(60)]
    # a 排在第 61 位（0-based 60）→ 被截断，正向行（issue_id=a）取不到
    data, meta = _bulk(_client(env["owner"]), env, fillers + [a.id])
    assert meta["requested"] == 61 and data["edges"] == []
    # a 排在首位 → 命中
    data2, meta2 = _bulk(_client(env["owner"]), env, [a.id] + fillers)
    assert meta2["requested"] == 61 and len(data2["edges"]) == 1


def test_ut21_relation_count_signal(env):
    """一行 2 条关联、一行 0 条 → relation_count 随行下发 2 / 0（§2.1 判定信号）。"""
    from plane.db.services.issue_link import create_relation

    a = _issue(env, "A", start="2026-09-01", target="2026-09-05")
    b = _issue(env, "B", start="2026-09-01", target="2026-09-05")
    c = _issue(env, "C", start="2026-09-01", target="2026-09-05")
    d = _issue(env, "D", start="2026-09-01", target="2026-09-05")
    create_relation(issue_id=a.id, related_issue_id=b.id, relation_type="blocks", actor_id=env["owner"].id)
    create_relation(issue_id=a.id, related_issue_id=c.id, relation_type="relates_to", actor_id=env["owner"].id)
    data, _ = _rows(_client(env["owner"]), env)
    rows = _by_id(data)
    assert rows[a.id]["relation_count"] == 2
    assert rows[d.id]["relation_count"] == 0


# ─────────────────────────────────────────────────────────────────────
# UT-13 / UT-20：参数与 tz 校验（§2.4 异常表）
# ─────────────────────────────────────────────────────────────────────
def _get_raw(client, env, qs: str):
    return client.get(f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/gantt/{qs}")


def test_ut13_viewport_and_granularity_validation(env):
    client = _client(env["owner"])
    # 视窗 start > end → 400 VALIDATION_INVALID_PARAM / 子码 INVALID_DATE_RANGE
    resp = _get_raw(client, env, "?viewport_start=2026-09-30&viewport_end=2026-09-01")
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "VALIDATION_INVALID_PARAM"
    assert err["details"][0]["field"] == "viewport_start"
    assert err["details"][0]["code"] == "INVALID_DATE_RANGE"
    # granularity=hour → 400
    resp = _get_raw(client, env, f"?viewport_start={VP['start']}&viewport_end={VP['end']}&granularity=hour")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_INVALID_PARAM"
    assert resp.json()["error"]["details"][0]["field"] == "granularity"
    # 缺参 / 非法日期 → 400
    assert _get_raw(client, env, "?viewport_end=2026-09-30").status_code == 400
    resp = _get_raw(client, env, "?viewport_start=2026/09/01&viewport_end=2026-09-30")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_INVALID_PARAM"


def test_ut20_invalid_tz_rejected_not_silent_fallback(env):
    resp = _get_raw(_client(env["owner"]), env,
                    f"?viewport_start={VP['start']}&viewport_end={VP['end']}&tz=ABC")
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert err["details"][0]["field"] == "tz"
    assert err["details"][0]["code"] == "INVALID"


def test_granularity_echo_and_no_effect_on_rows(env):
    """granularity 选填：默认 day；三粒度下行集/分页口径相同（§4.2.1 要点 8）。"""
    a = _issue(env, "A", start="2026-09-01", target="2026-09-08")
    client = _client(env["owner"])
    ids_by_gran: dict[str, set] = {}
    for gran in (None, "day", "week", "month"):
        if gran is None:
            data, meta = _rows(client, env)
            assert meta["granularity"] == "day"  # 默认
        else:
            data, meta = _rows(client, env, granularity=gran)
            assert meta["granularity"] == gran
        ids_by_gran[gran or "day"] = {r["id"] for r in data["rows"]}
    assert all(v == {str(a.id)} for v in ids_by_gran.values())


# ─────────────────────────────────────────────────────────────────────
# UT-15：权限（BR-12：无越权行，非成员 404）
# ─────────────────────────────────────────────────────────────────────
def test_ut15_permission_404_for_non_member(env):
    stranger = User.objects.create_user(email="gantt-stranger@rabbit.dev", password="Rabbit123!")
    client = _client(stranger)
    base = f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/gantt/"
    assert client.get(base + f"?viewport_start={VP['start']}&viewport_end={VP['end']}").status_code == 404
    assert client.post(base + "relations/bulk/", {"issue_ids": []}, format="json").status_code == 404
    assert client.get(base + "unscheduled/").status_code == 404
    # 越权 id 探测：他项目 issue 的 id 也不产生边（行集经项目过滤）；
    # 另造一条跨项目脏边（TASK-005 BR-02 服务层拦、此处防御纵深）——
    # bulk 以本项目 issue id 请求也不得返回该越权边
    other_ws = Workspace.objects.create(
        name="W2", slug=f"w-gantt2-{env['owner'].id.hex[:6]}", owner=env["owner"], created_by=env["owner"])
    other_proj = Project.objects.create(name="P2", identifier="GNT2", workspace=other_ws, created_by=env["owner"])
    foreign = Issue.objects.create(
        name="F", project=other_proj, sequence_id=1, sort_order=100,
        start_date="2026-09-01", target_date="2026-09-02", created_by=env["owner"])
    domestic = _issue(env, "D", start="2026-09-01", target="2026-09-05")
    IssueLink.objects.create(
        issue=domestic, related_issue=foreign,
        relation_type=IssueLink.RelationType.BLOCKS, created_by=env["owner"])
    data, _ = _bulk(_client(env["owner"]), env, [foreign.id, domestic.id])
    assert data["edges"] == []  # 边的 related 端在项目外 → 整条不出现


def test_viewer_role_can_read(env):
    """gantt.read（VIEWER+）：项目 VIEWER 可读三端点。"""
    _issue(env, "A", start="2026-09-01", target="2026-09-08")
    client = _client(env["viewer"])
    data, _ = _rows(client, env)
    assert len(data["rows"]) == 1
    assert client.get(
        f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/gantt/unscheduled/"
    ).status_code == 200


# ─────────────────────────────────────────────────────────────────────
# UT-16：筛选复用（BR-01：view_id DSL 与列表视图一致）
# ─────────────────────────────────────────────────────────────────────
def test_ut16_view_id_filter_reuse(env):
    urgent = _issue(env, "U", start="2026-09-01", target="2026-09-05", priority="urgent")
    _issue(env, "L", start="2026-09-01", target="2026-09-05", priority="low")
    view = IssueView.objects.create(
        workspace=env["ws"], project=env["proj"], owner=env["owner"], name="仅紧急",
        layout=IssueView.Layout.GANTT,
        filters={"op": "AND", "conditions": [
            {"field": "priority", "operator": "in", "value": ["urgent"]}]},
    )
    client = _client(env["owner"])
    data, meta = _rows(client, env, view_id=view.id)
    assert list(_by_id(data)) == [urgent.id]  # 行集与视图筛选一致
    # 与 issues 列表（同 view_id）行集一致（BR-01 复用语义）
    resp = client.get(
        f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/issues/?view_id={view.id}")
    list_ids = {r["id"] for r in resp.json()["data"]}
    assert list_ids == {str(urgent.id)}
    # 临时筛选（?filters=）同管道
    import urllib.parse

    dsl = urllib.parse.quote('{"op":"AND","conditions":[{"field":"priority","operator":"in","value":["low"]}]}')
    data2, _ = _rows(client, env, filters=dsl)
    assert set(_by_id(data2)) != {urgent.id} and len(data2["rows"]) == 1


# ─────────────────────────────────────────────────────────────────────
# UT-17：工时对照字段下发（TASK-006 契约）
# ─────────────────────────────────────────────────────────────────────
def test_ut17_estimate_spent_fields(env):
    a = _issue(env, "A", start="2026-09-01", target="2026-09-05", estimate=480)
    b = _issue(env, "B", start="2026-09-01", target="2026-09-05", estimate=None)
    WorkLog.objects.create(issue=a, actor=env["owner"], worked_on="2026-09-02",
                           minutes=240, created_by=env["owner"])
    data, _ = _rows(_client(env["owner"]), env)
    rows = _by_id(data)
    assert rows[a.id]["estimate_minutes"] == 480 and rows[a.id]["spent_minutes"] == 240
    assert rows[b.id]["estimate_minutes"] is None  # null 时前端不渲染工时位


# ─────────────────────────────────────────────────────────────────────
# UT-18：聚合条口径（BR-11）
# ─────────────────────────────────────────────────────────────────────
def test_ut18_aggregated_parent_bar(env):
    """父双 NULL、子树最早 9-02 / 最晚 9-20，视窗 9 月 → 命中；聚合区间下发；
    不计 unscheduled；子行 depth=1。"""
    parent = _issue(env, "P", start=None, target=None)
    c1 = _issue(env, "C1", start="2026-09-02", target="2026-09-06", parent=parent)
    c2 = _issue(env, "C2", start="2026-09-10", target="2026-09-20", parent=parent)
    data, meta = _rows(_client(env["owner"]), env)
    rows = _by_id(data)
    prow = rows[parent.id]
    assert prow["is_aggregated"] is True
    assert prow["start_date"] == "2026-09-02" and prow["target_date"] == "2026-09-20"
    assert data["unscheduled_count"] == 0  # BR-11：聚合条不计入未排期
    assert rows[c1.id]["depth"] == 1 and rows[c2.id]["depth"] == 1
    assert prow["depth"] == 0 and rows[c1.id]["is_aggregated"] is False
    # 子树区间与视窗相交才入轴：平移到 10 月 → 父行不出现
    data2, _ = _rows(_client(env["owner"]), env, start="2026-10-01", end="2026-10-31")
    assert parent.id not in _by_id(data2)
    # 深层（3 层）深度递增
    gc = _issue(env, "GC", start="2026-09-03", target="2026-09-04", parent=c1)
    data3, _ = _rows(_client(env["owner"]), env)
    assert _by_id(data3)[gc.id]["depth"] == 2


# ─────────────────────────────────────────────────────────────────────
# UT-19：请求级时区折算（BR-05，RPT-001 范式）
# ─────────────────────────────────────────────────────────────────────
def test_ut19_request_tz_today_and_overdue(env):
    """meta.today / is_overdue 按请求时区日历判定。

    Asia/Shanghai(UTC+8) 与 America/New_York(UTC-4) 每天仅 4 小时日期不同——
    边界判定改用恒差 25h 的 Pacific/Kiritimati(+14) vs Pacific/Pago_Pago(-11)
    （两时区「今天」恒差 ≥1 日，断言与运行时刻无关）。
    """
    ny_today = datetime.now(ZoneInfo("America/New_York")).date()
    kiri_today = datetime.now(ZoneInfo("Pacific/Kiritimati")).date()
    pago_today = datetime.now(ZoneInfo("Pacific/Pago_Pago")).date()
    assert kiri_today > pago_today  # 前置自检：恒差成立
    a = _issue(env, "A", start=None, target=pago_today, state="started")
    client = _client(env["owner"])
    # tz=America/New_York：meta.today 折算为 NY 本地日
    data, meta = _rows(client, env, tz="America/New_York")
    assert meta["today"] == ny_today.isoformat()
    rows = _by_id(data)
    # target = Pago_Pago 的今天 → Pago 判定不过期；Kiritimati 判定已过期
    assert rows[a.id]["is_overdue"] is False
    data2, meta2 = _rows(client, env, tz="Pacific/Kiritimati")
    assert meta2["today"] == kiri_today.isoformat()
    assert _by_id(data2)[a.id]["is_overdue"] is True
    # X-Client-TZ 头 > 默认（?tz= 优先于头）
    resp = client.get(
        f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/gantt/"
        f"?viewport_start={VP['start']}&viewport_end={VP['end']}",
        headers={"X-Client-TZ": "Pacific/Pago_Pago"})
    assert resp.json()["meta"]["today"] == pago_today.isoformat()
    resp2 = client.get(
        f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/gantt/"
        f"?viewport_start={VP['start']}&viewport_end={VP['end']}&tz=Pacific/Kiritimati",
        headers={"X-Client-TZ": "Pacific/Pago_Pago"})
    assert resp2.json()["meta"]["today"] == kiri_today.isoformat()


# ─────────────────────────────────────────────────────────────────────
# 行契约形状 / 折叠回显 / 游标分页 / per_page 截断
# ─────────────────────────────────────────────────────────────────────
def test_row_contract_shape(env):
    a = _issue(env, "A", start="2026-09-01", target="2026-09-08")
    data, meta = _rows(_client(env["owner"]), env)
    row = data["rows"][0]
    assert set(row) == {
        "id", "issue_key", "name", "depth", "has_children", "collapsed",
        "start_date", "target_date", "progress", "progress_source",
        "state_group", "state_color", "is_overdue", "assignee_ids",
        "is_aggregated", "relation_count", "estimate_minutes", "spent_minutes",
    }
    assert row["issue_key"] == f"GNT-{a.sequence_id}" and row["state_color"] == "#123456"
    # meta 全字段（§6.3）+ 端点自有字段
    assert set(meta) >= {
        "next_cursor", "prev_cursor", "next_page_results", "prev_page_results",
        "count", "total_count", "total_pages", "page", "per_page",
        "granularity", "viewport", "today",
    }
    assert meta["viewport"] == {"start": VP["start"], "end": VP["end"]}


def test_collapsed_echo_from_view(env):
    """BR-10：collapsed 回显视图 display_props.collapsed。"""
    a = _issue(env, "A", start="2026-09-01", target="2026-09-08")
    view = IssueView.objects.create(
        workspace=env["ws"], project=env["proj"], owner=env["owner"], name="甘特",
        layout=IssueView.Layout.GANTT, display_props={"collapsed": [str(a.id)]},
    )
    data, _ = _rows(_client(env["owner"]), env, view_id=view.id)
    assert _by_id(data)[a.id]["collapsed"] is True
    # 无视图 → 恒 false
    data2, _ = _rows(_client(env["owner"]), env)
    assert _by_id(data2)[a.id]["collapsed"] is False


def test_cursor_pagination_walk(env):
    """行分页（默认 60）：next_cursor 翻页 → page 2 接续；非法游标 400。"""
    client = _client(env["owner"])
    for i in range(7):
        _issue(env, f"I{i}", start="2026-09-01", target="2026-09-05", sort=(i + 1) * 100.0)
    data, meta = _rows(client, env, per_page=3)
    assert meta["count"] == 3 and meta["page"] == 1 and meta["total_pages"] == 3
    assert meta["next_page_results"] is True and meta["prev_page_results"] is False
    page1_ids = [r["id"] for r in data["rows"]]
    data2, meta2 = _rows(client, env, per_page=3, cursor=meta["next_cursor"])
    assert meta2["page"] == 2 and meta2["prev_page_results"] is True
    assert not ({r["id"] for r in data2["rows"]} & set(page1_ids))
    data3, meta3 = _rows(client, env, per_page=3, cursor=meta2["next_cursor"])
    assert meta3["count"] == 1 and meta3["next_cursor"] is None
    # 非法游标 → 400 VALIDATION_INVALID_CURSOR
    resp = _get_raw(client, env,
                    f"?viewport_start={VP['start']}&viewport_end={VP['end']}&per_page=3&cursor=!!!bogus")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_INVALID_CURSOR"


def test_per_page_cap_100_silent_truncate(env):
    _issue(env, "A", start="2026-09-01", target="2026-09-08")
    data, meta = _rows(_client(env["owner"]), env, per_page=500)
    assert meta["per_page"] == 100
    assert meta["degraded"]["per_page"]


def test_archived_excluded_from_rows(env):
    a = _issue(env, "A", start="2026-09-01", target="2026-09-08")
    a.archived_at = datetime.now(ZoneInfo("UTC"))
    a.save(update_fields=["archived_at"])
    data, _ = _rows(_client(env["owner"]), env)
    assert data["rows"] == [] and data["unscheduled_count"] == 0


def test_soft_deleted_link_excluded_from_relation_count(env):
    """软删或对方软删不计（§4.2.1 要点 7）。"""
    from plane.db.services.issue_link import create_relation, delete_relation

    a = _issue(env, "A", start="2026-09-01", target="2026-09-05")
    b = _issue(env, "B", start="2026-09-01", target="2026-09-05")
    forward, _mirror = create_relation(
        issue_id=a.id, related_issue_id=b.id, relation_type="relates_to", actor_id=env["owner"].id)
    data, _ = _rows(_client(env["owner"]), env)
    assert _by_id(data)[a.id]["relation_count"] == 1
    delete_relation(link_id=forward.id, actor_id=env["owner"].id)
    data2, _ = _rows(_client(env["owner"]), env)
    assert _by_id(data2)[a.id]["relation_count"] == 0


def test_bulk_body_validation(env):
    client = _client(env["owner"])
    url = f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/gantt/relations/bulk/"
    resp = client.post(url, {"issue_ids": "not-a-list"}, format="json")
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "VALIDATION_ERROR"
    resp2 = client.post(url, {"issue_ids": ["not-uuid"]}, format="json")
    assert resp2.status_code == 400 and resp2.json()["error"]["code"] == "VALIDATION_ERROR"
    # 空列表 → 200 空边（幂等跳过 §2.1）
    resp3 = client.post(url, {"issue_ids": []}, format="json")
    assert resp3.status_code == 200 and resp3.json()["meta"] == {"requested": 0, "edges": 0}


# ─────────────────────────────────────────────────────────────────────
# IT-07：视窗查询索引命中（EXPLAIN 按索引名断言，不锁扫描形态）
# ─────────────────────────────────────────────────────────────────────
def test_it07_viewport_query_uses_gantt_index(env):
    """EXPLAIN 视窗行查询 → idx_issue_gantt_viewport 命中。

    小数据集下规划器天然走 Seq Scan——``SET LOCAL enable_seqscan=off``
    （事务内生效，pytest-django 回滚不泄漏）验证查询形状可走该索引；
    真实数据量的计划断言（BitmapOr 合并等形态）在 sprint-4-bench-gantt.py
    以 1 万行数据集 EXPLAIN (ANALYZE) 复核。
    """
    from plane.app.views.gantt import GanttRowsView
    from django.test import RequestFactory

    for i in range(5):
        _issue(env, f"I{i}", start="2026-09-0%d" % (i + 1), target="2026-09-10")
    rf = RequestFactory()
    req = rf.get(
        f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/gantt/"
        f"?viewport_start={VP['start']}&viewport_end={VP['end']}")
    req.user = env["owner"]
    with CaptureQueriesContext(connection) as ctx:
        resp = GanttRowsView.as_view()(req, slug=env["ws"].slug, project_id=env["proj"].id)
        assert resp.status_code == 200
    rows_sql = next(
        q["sql"] for q in ctx.captured_queries
        if q["sql"].startswith("SELECT")
        and 'ORDER BY "issues"."sort_order"' in q["sql"]  # 行窗口主查询（BR-11 行序）
    )
    with connection.cursor() as cur:
        cur.execute("SET LOCAL enable_seqscan = off")
        cur.execute("EXPLAIN " + rows_sql)
        plan = "\n".join(str(r[0]) for r in cur.fetchall())
    assert "idx_issue_gantt_viewport" in plan, plan
    # 索引在实库存在（迁移 0012 落地证据）
    with connection.cursor() as cur:
        cur.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'issues' AND indexname = 'idx_issue_gantt_viewport'")
        assert cur.fetchone() is not None


# ═════════════════════════════════════════════════════════════════════
# GANTT-002 后端子集：延期概览聚合（§4.2.1/§4.3.1）+ 端点级限流（§4.2.1 要点 4）
# ═════════════════════════════════════════════════════════════════════
TZ_SH = ZoneInfo("Asia/Shanghai")  # DEFAULT_TZ——概览/行集两端的默认「今天」基准


def _today_sh() -> date:
    """默认 tz 口径的「今天」——与端点内 ``datetime.now(tz).date()`` 同基准。"""
    return datetime.now(TZ_SH).date()


def _summary(client, env, **params):
    qs = "".join(f"&{k}={v}" for k, v in params.items())
    return client.get(
        f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/gantt/overdue-summary/?{qs}")


def _summary_ok(client, env, **params):
    resp = _summary(client, env, **params)
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    return body["data"], body["meta"]


@pytest.fixture(autouse=True)
def agg_redis_clean():
    """限流计数键测试隔离（FILE-004 redis_clean 同款纪律）：本文件 g002 用例
    全部命中限流端点（每次请求 INCR），故 autouse——前置重置进程态 + 后置清
    ``gantt-agg:*`` 键，不留跨用例状态（坑 18；键自带 60s TTL 兜底自过期）。
    GANTT-001 用例不触达 Redis，仅承担无害的空清理。"""
    gantt_views.reset_gantt_redis_state()
    yield
    client = gantt_views._agg_redis()
    if client is not None:
        keys = client.keys("gantt-agg:*")
        if keys:
            client.delete(*keys)
    gantt_views.reset_gantt_redis_state()


def _mk_user(email: str, display_name: str) -> User:
    return User.objects.create_user(email=email, password="Rabbit123!", display_name=display_name)


def test_g002_ut12_overdue_count_excludes_completed(env):
    """UT-12 概览口径：6 条 target 已过含 1 completed → overdue_count 5；
    completed 不入明细（§2.3 豁免组）。"""
    past = _today_sh() - timedelta(days=10)
    for i in range(4):
        _issue(env, f"S{i}", start=None, target=past, state="started")
    _issue(env, "BACK", start=None, target=past, state="backlog")
    done = _issue(env, "DONE", start=past - timedelta(days=5), target=past, state="completed")
    data, _ = _summary_ok(_client(env["owner"]), env)
    assert data["overdue_count"] == 5
    assert data["items_truncated"] is False
    assert str(done.id) not in {i["id"] for i in data["items"]}


def test_g002_ut13_multi_assignee_distribution(env):
    """UT-13 多人分布：1 条逾期任务 2 执行人 → 两人各 +1（RPT-001 口径，
    IssueAssignee 逐行计数）；未指派逾期行计入总数不计分布；by_assignee 降序。"""
    zhang = _mk_user("g002-zhang@rabbit.dev", "张三")
    li = _mk_user("g002-li@rabbit.dev", "李四")
    t = _today_sh()
    a = _issue(env, "A", start=None, target=t - timedelta(days=9), state="started")
    b = _issue(env, "B", start=None, target=t - timedelta(days=3), state="started")
    _issue(env, "N", start=None, target=t - timedelta(days=2), state="started")  # 未指派
    for u in (zhang, li):
        IssueAssignee.objects.create(issue=a, assignee=u, created_by=env["owner"])
    IssueAssignee.objects.create(issue=b, assignee=zhang, created_by=env["owner"])
    data, _ = _summary_ok(_client(env["owner"]), env)
    assert data["overdue_count"] == 3
    by = {r["assignee_id"]: r["count"] for r in data["by_assignee"]}
    assert by == {str(zhang.id): 2, str(li.id): 1}  # 多人任务每人各计 1
    counts = [r["count"] for r in data["by_assignee"]]
    assert counts == sorted(counts, reverse=True)
    assert {r["display_name"] for r in data["by_assignee"]} == {"张三", "李四"}
    item_a = next(i for i in data["items"] if i["id"] == str(a.id))
    assert set(item_a["assignee_ids"]) == {str(zhang.id), str(li.id)}


def test_g002_ut18_viewer_reads_non_member_404(env):
    """UT-18 概览读权限：VIEWER 200（gantt.read 只读聚合正常）；非成员 404。"""
    _issue(env, "A", start=None, target=_today_sh() - timedelta(days=5), state="started")
    resp = _summary(_client(env["viewer"]), env)
    assert resp.status_code == 200
    assert resp.json()["data"]["overdue_count"] == 1
    stranger = _mk_user("g002-stranger@rabbit.dev", "路人")
    assert _summary(_client(stranger), env).status_code == 404


def test_g002_it04_summary_consistent_with_row_is_overdue(env):
    """IT-04 概览一致性＝同源性证明：overdue-summary 与 ``gantt/`` 行级
    is_overdue 在同一数据集上数字一致——含开放端条（start NULL 仅 target
    逾期）、NULL state（折算 unstarted）、完成/取消豁免、今日边界（严格 <）、
    双 NULL、归档（§2.3 与 GANTT-001 is_overdue 真同源）。"""
    t = _today_sh()
    open_end = _issue(env, "OPEN", start=None, target=t - timedelta(days=9), state="started")  # 开放端条计入
    deep = _issue(env, "DEEP", start=t - timedelta(days=40), target=t - timedelta(days=30), state="unstarted")
    _issue(env, "DONE", start=t - timedelta(days=8), target=t - timedelta(days=6), state="completed")
    _issue(env, "CANCEL", start=t - timedelta(days=8), target=t - timedelta(days=6), state="cancelled")
    _issue(env, "FUTURE", start=t, target=t + timedelta(days=5), state="started")
    _issue(env, "EDGE", start=t - timedelta(days=2), target=t, state="started")  # target=今天 → 不过期（严格 <）
    _issue(env, "DN", start=None, target=None, state="started")  # 双 NULL 不可能逾期
    seq = Issue.objects.filter(project=env["proj"]).count() + 1
    no_state = Issue.objects.create(  # state FK 为 NULL → unstarted 折算 → 逾期（两端口径须一致）
        name="NS", project=env["proj"], sequence_id=seq, sort_order=seq * 100.0,
        start_date=t - timedelta(days=5), target_date=t - timedelta(days=2), created_by=env["owner"])
    archived = _issue(env, "ARCH", start=t - timedelta(days=8), target=t - timedelta(days=6), state="started")
    archived.archived_at = datetime.now(ZoneInfo("UTC"))
    archived.save(update_fields=["archived_at"])

    client = _client(env["owner"])
    vs, ve = (t - timedelta(days=60)).isoformat(), (t + timedelta(days=60)).isoformat()
    data, meta = _summary_ok(client, env)
    assert meta["today"] == t.isoformat()
    rows_data, rows_meta = _rows(client, env, start=vs, end=ve, per_page=100)
    flagged = {r["id"] for r in rows_data["rows"] if r["is_overdue"]}

    expected = {str(open_end.id), str(deep.id), str(no_state.id)}
    assert data["overdue_count"] == 3 == len(expected)
    assert {i["id"] for i in data["items"]} == expected
    assert flagged == expected  # 同源性：概览明细集 == 行级 is_overdue 标记集
    assert rows_meta["today"] == meta["today"]  # 同一「今天」基准（BR-05）
    assert data["max_overdue_days"] == 30  # DEEP 30 > OPEN 9 > NS 2
    by_id = {i["id"]: i for i in data["items"]}
    assert by_id[str(deep.id)]["overdue_days"] == 30
    assert by_id[str(open_end.id)]["overdue_days"] == 9
    assert by_id[str(open_end.id)]["issue_key"] == f"GNT-{open_end.sequence_id}"

    # 换请求 tz 后两端仍同基准一致（数字本身随 tz 变化，一致性不变）
    data2, meta2 = _summary_ok(client, env, tz="Pacific/Kiritimati")
    rows_data2, rows_meta2 = _rows(client, env, start=vs, end=ve, per_page=100, tz="Pacific/Kiritimati")
    flagged2 = {r["id"] for r in rows_data2["rows"] if r["is_overdue"]}
    assert {i["id"] for i in data2["items"]} == flagged2
    assert data2["overdue_count"] == len(flagged2)
    assert meta2["today"] == rows_meta2["today"]


def test_g002_items_truncated_at_20_desc_order(env):
    """§4.2.1 契约要点 1：items 按逾期天数降序前 20 截断（items_truncated=true）；
    统计三数字仍为完整集口径（overdue_count=23 / max=23）。"""
    t = _today_sh()
    for i in range(23):
        _issue(env, f"I{i}", start=None, target=t - timedelta(days=i + 1), state="started")
    data, _ = _summary_ok(_client(env["owner"]), env)
    assert data["overdue_count"] == 23
    assert data["max_overdue_days"] == 23
    assert len(data["items"]) == 20
    assert data["items_truncated"] is True
    days = [i["overdue_days"] for i in data["items"]]
    assert days == sorted(days, reverse=True)  # 23..4
    assert days[0] == data["max_overdue_days"]


def test_g002_contract_shape_and_empty(env):
    """§4.2.1 JSON 形状：data 五键 / item 六键 / by_assignee 三键 / meta.today；
    空项目零逾期形态；granularity/viewport_* 不参与聚合（忽略不报错）。"""
    client = _client(env["owner"])
    data, meta = _summary_ok(client, env)
    assert data == {"overdue_count": 0, "max_overdue_days": 0,
                    "by_assignee": [], "items": [], "items_truncated": False}
    assert set(meta) == {"today"}
    _issue(env, "A", start=None, target=_today_sh() - timedelta(days=5), state="started")
    data2, _ = _summary_ok(client, env)
    assert set(data2) == {"overdue_count", "max_overdue_days", "by_assignee", "items", "items_truncated"}
    item = data2["items"][0]
    assert set(item) == {"id", "issue_key", "name", "target_date", "overdue_days", "assignee_ids"}
    assert item["assignee_ids"] == []
    # 视窗/粒度参数与聚合无关——携带不改变结果（§4.2.1 注）
    data3, _ = _summary_ok(client, env, granularity="day",
                           viewport_start="2026-01-01", viewport_end="2026-01-31")
    assert data3 == data2


def test_g002_filter_pipeline_same_source(env):
    """§4.2.1 参数：view_id 视图层与 ?filters= 临时层——与甘特行集同一筛选
    管道（_filtered_base，GANTT-001 BR-01 语义），聚合只统计命中筛选的逾期行。"""
    t = _today_sh() - timedelta(days=5)
    urgent = _issue(env, "U", start=None, target=t, state="started", priority="urgent")
    low = _issue(env, "L", start=None, target=t, state="started", priority="low")
    view = IssueView.objects.create(
        workspace=env["ws"], project=env["proj"], owner=env["owner"], name="仅紧急",
        layout=IssueView.Layout.GANTT,
        filters={"op": "AND", "conditions": [
            {"field": "priority", "operator": "in", "value": ["urgent"]}]})
    client = _client(env["owner"])
    data, _ = _summary_ok(client, env, view_id=view.id)
    assert data["overdue_count"] == 1 and data["items"][0]["id"] == str(urgent.id)
    # 临时筛选（?filters=）同管道
    import urllib.parse

    dsl = urllib.parse.quote(
        '{"op":"AND","conditions":[{"field":"priority","operator":"in","value":["low"]}]}')
    data2, _ = _summary_ok(client, env, filters=dsl)
    assert data2["overdue_count"] == 1 and data2["items"][0]["id"] == str(low.id)
    # 不带筛选 → 全量
    data3, _ = _summary_ok(client, env)
    assert data3["overdue_count"] == 2


def test_g002_ut17_it07_throttle_10_per_min_per_user(env):
    """UT-17/IT-07：同一 user 60s 固定窗口内第 11 次 → 429 RATE_LIMIT_EXCEEDED +
    Retry-After + X-RateLimit 三件套（§4.2.1 契约要点 4、api-conventions §7.3）；
    键维度 user_id（他人不受牵连）；窗口流逝（清键 == 60s 固定窗口过）恢复。"""
    _issue(env, "A", start=None, target=_today_sh() - timedelta(days=5), state="started")
    client = _client(env["owner"])
    for i in range(10):  # 配额内逐次 200
        assert _summary(client, env).status_code == 200, f"attempt {i}"
    resp = _summary(client, env)  # 第 11 次 → 429
    assert resp.status_code == 429
    body = resp.json()
    assert body["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert int(resp.headers["Retry-After"]) >= 1
    assert resp.headers["X-RateLimit-Limit"] == "10"      # §7.3 模板三件套
    assert resp.headers["X-RateLimit-Remaining"] == "0"
    assert int(resp.headers["X-RateLimit-Reset"]) > time.time()
    # 键维度 user_id（§4.2.1 原文键 gantt-agg:{user_id}）：另一用户不受牵连
    assert _summary(_client(env["viewer"]), env).status_code == 200
    # IT-07 窗口恢复：清计数键等价 60s 固定窗口流逝 → 配额重置
    redis_client = gantt_views._agg_redis()
    assert redis_client is not None, "限流用例要求真实 Valkey（rp-redis，FILE-004 同款前提）"
    redis_client.delete(gantt_views.gantt_agg_attempt_key(str(env["owner"].id)))
    assert _summary(client, env).status_code == 200


def test_g002_assignee_batch_no_n_plus_one(env):
    """§4.2.1 契约要点 2：items[].assignee_ids 与 by_assignee 各为单条批量查询
    ——issue_assignees 表查询数不随明细行数增长（≤2，防逐行 N+1）。"""
    zhang = _mk_user("g002-bz@rabbit.dev", "批量张")
    li = _mk_user("g002-bl@rabbit.dev", "批量李")
    t = _today_sh() - timedelta(days=5)
    for i in range(6):
        a = _issue(env, f"I{i}", start=None, target=t - timedelta(days=i), state="started")
        IssueAssignee.objects.create(issue=a, assignee=zhang, created_by=env["owner"])
        IssueAssignee.objects.create(issue=a, assignee=li, created_by=env["owner"])
    client = _client(env["owner"])
    with CaptureQueriesContext(connection) as ctx:
        resp = _summary(client, env)
        assert resp.status_code == 200, resp.json()
    ia_queries = [q["sql"] for q in ctx.captured_queries if '"issue_assignees"' in q["sql"]]
    assert len(ia_queries) <= 2, ia_queries
