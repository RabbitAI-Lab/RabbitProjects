"""TEAM-003 工作空间治理测试（T5-08）。

覆盖：归档/恢复（OWNER 专属 403 / 幂等 / affected_projects / 中间件全站写保护
+ 豁免表 + GET 放行）；全局标签 CRUD（颜色校验 / 同名 409 / 软删
affected_issues + name_snapshot）；项目标签列表的全局并集与覆盖排除；
状态模板（内置兜底 version=0 / PUT 校验五组+连续 sequence / version 递增）；
活跃度（BR-09 Schema 红线——递归断言键路径无 user_id；分桶算术自洽；
命中表幂等登录）；team.stats.read 门槛（member 403）。
"""
from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from plane.db.models import (
    Issue,
    IssueLabel,
    Label,
    Project,
    State,
    User,
    Workspace,
    WorkspaceLoginDailyAggregate,
    WorkspaceMember,
    WorkspaceRole,
)
from plane.db.seeds.project_states import seed_project_states

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="t3-owner@rabbit.dev", password="Rabbit123!",
                                     display_name="张三")
    admin = User.objects.create_user(email="t3-admin@rabbit.dev", password="Rabbit123!",
                                     display_name="李管理")
    member = User.objects.create_user(email="t3-member@rabbit.dev", password="Rabbit123!",
                                      display_name="王成员")
    ws = Workspace.objects.create(name="W", slug=f"w-t3-{owner.id.hex[:8]}",
                                  owner=owner, created_by=owner)
    for u, r in ((owner, WorkspaceRole.OWNER), (admin, WorkspaceRole.ADMIN),
                 (member, WorkspaceRole.MEMBER)):
        WorkspaceMember.objects.create(workspace=ws, member=u, role=r, created_by=owner)
    proj = Project.objects.create(name="P", identifier="T03", workspace=ws, created_by=owner)
    seed_project_states(proj)
    todo = State.objects.get(project=proj, group="unstarted")
    issue = Issue.objects.create(name="任", project=proj, state=todo, priority="none",
                                 sequence_id=1, sort_order=100, created_by=owner)
    return {"owner": owner, "admin": admin, "member": member, "ws": ws,
            "proj": proj, "issue": issue}


def _client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _base(env):
    return f"/api/v1/workspaces/{env['ws'].slug}"


# ── 归档 / 恢复 ────────────────────────────────────────────────
def test_archive_owner_only_and_idempotent(env):
    # FILE-007（R6）归档合规闸门：先完成任务再归档（阻断语义另有专测）
    from plane.db.models import Issue
    from django.utils import timezone as _tz

    Issue.objects.filter(project__workspace=env["ws"]).update(
        completed_at=_tz.now())
    assert _client(env["admin"]).post(
        f"{_base(env)}/archive/", format="json").status_code == 403
    r = _client(env["owner"]).post(f"{_base(env)}/archive/", format="json")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["affected_projects"] == 1 and d["idempotent"] is False
    r2 = _client(env["owner"]).post(f"{_base(env)}/archive/", format="json")
    assert r2.json()["data"]["idempotent"] is True
    # 恢复
    r3 = _client(env["owner"]).post(f"{_base(env)}/restore/", format="json")
    assert r3.status_code == 200 and r3.json()["data"]["archived_at"] is None


def test_archive_middleware_write_guard(env):
    # FILE-007（R6）合规清场后归档（写保护语义与本闸门正交）
    from django.utils import timezone as _tz

    from plane.db.models import Issue

    Issue.objects.filter(project__workspace=env["ws"]).update(
        completed_at=_tz.now())
    _client(env["owner"]).post(f"{_base(env)}/archive/", format="json")
    # GET 放行（admin——member 非项目成员按矩阵本就 404）
    assert _client(env["admin"]).get(
        f"{_base(env)}/projects/{env['proj'].id}/issues/",
        format="json").status_code == 200
    # 写短路 403 PERM_WORKSPACE_ARCHIVED（信封）
    r = _client(env["owner"]).post(
        f"{_base(env)}/projects/", format="json",
        data={"name": "X", "identifier": "XA1"})
    assert r.status_code == 403
    e = r.json()["error"]
    assert e["code"] == "PERM_WORKSPACE_ARCHIVED"
    assert any(x["field"] == "restore_hint" for x in e["details"])
    # restore 自身豁免（能进视图恢复）
    r2 = _client(env["owner"]).post(f"{_base(env)}/restore/", format="json")
    assert r2.status_code == 200
    # 恢复后写恢复
    r3 = _client(env["owner"]).post(
        f"{_base(env)}/projects/", format="json",
        data={"name": "X", "identifier": "XA1"})
    assert r3.status_code == 201


# ── 全局标签 ───────────────────────────────────────────────────
def test_label_crud_and_soft_delete_snapshot(env):
    base = f"{_base(env)}/labels/"
    assert _client(env["member"]).post(base, format="json", data={"name": "x"}).status_code == 403
    r = _client(env["admin"]).post(base, format="json", data={
        "name": "bug", "color": "#E5484D", "description": "缺陷"})
    assert r.status_code == 201
    lid = r.json()["data"]["id"]
    assert _client(env["admin"]).post(base, format="json", data={
        "name": "bug", "color": "#111111"}).status_code == 409
    assert _client(env["admin"]).post(base, format="json", data={
        "name": "bad", "color": "red"}).status_code == 400
    # 项目侧覆盖行（overrides_global_id）挂任务 → 删除全局时快照 + affected=1
    cover = Label.objects.create(project=env["proj"], name="bug-自定义",
                                 color="#222222", origin="local",
                                 overrides_global_id=lid, created_by=env["owner"])
    IssueLabel.objects.create(issue=env["issue"], label=cover, created_by=env["owner"])
    r2 = _client(env["admin"]).delete(f"{base}{lid}/", format="json")
    assert r2.status_code == 200
    assert r2.json()["data"]["affected_issues"] == 1
    assert IssueLabel.objects.get(issue=env["issue"], label=cover).name_snapshot == "bug"
    assert str(Label.objects.get(pk=cover.pk).overrides_global_id) == lid  # 软删不触发 FK 级联


def test_project_label_list_merges_globals(env):
    from plane.db.models import WorkspaceLabel

    g = WorkspaceLabel.objects.create(workspace=env["ws"], name="urgent",
                                      color="#F59E0B", created_by=env["owner"])
    g2 = WorkspaceLabel.objects.create(workspace=env["ws"], name="feature",
                                       color="#8E4EC6", created_by=env["owner"])
    Label.objects.create(project=env["proj"], name="本地", color="#333333",
                         created_by=env["owner"])
    covered = Label.objects.create(project=env["proj"], name="urgent-覆盖",
                                   color="#444444", overrides_global_id=g.id,
                                   created_by=env["owner"])
    r = _client(env["admin"]).get(
        f"{_base(env)}/projects/{env['proj'].id}/labels/", format="json")
    names = {x["name"]: x for x in r.json()["data"]}
    assert "本地" in names and "feature" in names       # 并集下发（未覆盖全局在列）
    assert names["feature"]["origin"] == "global"
    assert covered.name in names                          # 覆盖行以 local 形态展示
    assert "urgent" not in names                          # 被覆盖的全局行不再重复出现
    del g2


# ── 状态模板 ───────────────────────────────────────────────────
def test_default_states_get_put(env):
    r = _client(env["member"]).get(f"{_base(env)}/default-states/", format="json")
    d = r.json()["data"]
    assert d["version"] == 0 and {g["group"] for g in d["groups"]} >= {
        "unstarted", "started", "completed", "cancelled"}
    payload = {"groups": [
        {"group": "backlog", "states": [{"name": "B", "sequence": 1}]},
        {"group": "unstarted", "states": [{"name": "T", "sequence": 2}]},
        {"group": "started", "states": [{"name": "S", "sequence": 3}]},
        {"group": "completed", "states": [{"name": "D", "sequence": 4}]},
        {"group": "cancelled", "states": [{"name": "C", "sequence": 5}]},
    ]}
    assert _client(env["member"]).put(
        f"{_base(env)}/default-states/", format="json", data=payload).status_code == 403
    r2 = _client(env["admin"]).put(
        f"{_base(env)}/default-states/", format="json", data=payload)
    assert r2.status_code == 200 and r2.json()["data"]["version"] == 1
    # sequence 断裂 → 400
    bad = {"groups": [
        {"group": g, "states": [{"name": "x", "sequence": 10 if g == "started" else i}]}
        for i, g in enumerate(["backlog", "unstarted", "started", "completed", "cancelled"], 1)]}
    assert _client(env["admin"]).put(
        f"{_base(env)}/default-states/", format="json", data=bad).status_code == 400
    # 缺组 → 400
    bad2 = {"groups": payload["groups"][:4]}
    assert _client(env["admin"]).put(
        f"{_base(env)}/default-states/", format="json", data=bad2).status_code == 400


# ── 活跃度（BR-09 红线）────────────────────────────────────────
def _walk_no_user_id(obj) -> bool:
    if isinstance(obj, dict):
        return all(k != "user_id" and _walk_no_user_id(v) for k, v in obj.items())
    if isinstance(obj, list):
        return all(_walk_no_user_id(x) for x in obj)
    return True


def test_activity_stats_schema_and_buckets(env):
    from plane.db.services.workspace_governance import record_login_hits

    record_login_hits(env["member"])   # 1 名活跃（登录命中）
    r = _client(env["admin"]).get(f"{_base(env)}/activity-stats/", format="json")
    assert r.status_code == 200
    d = r.json()["data"]
    assert _walk_no_user_id(d)                      # BR-09 Schema 红线
    assert d["total_members"] == 3
    buckets = d["contribution_distribution"][0]["buckets"]
    assert sum(buckets.values()) == 3               # 算术自洽（0 桶含全部成员）
    assert d["login_days_histogram"]["1"] == 1      # member 登录 1 天
    assert set(d["login_days_histogram"]) == {"1", "2_3", "4_5", "ge_6"}
    assert set(d["top_actions"]) == {"issue", "comment"}
    # 幂等登录不叠天数
    record_login_hits(env["member"])
    assert WorkspaceLoginDailyAggregate.objects.filter(
        member=env["member"], workspace=env["ws"]).count() == 1


def test_activity_stats_permission_and_days(env):
    assert _client(env["member"]).get(
        f"{_base(env)}/activity-stats/", format="json").status_code == 403
    assert _client(env["admin"]).get(
        f"{_base(env)}/activity-stats/?days=15", format="json").status_code == 400
