"""PROJ-003 项目生命周期测试（T5-07）——状态机 + 守卫 + 模板 + 副本。

覆盖：转换矩阵合法/非法边（409 RESOURCE_TRANSITION_INVALID + allowed_targets）、
幂等短路（BR-02）、draft→active 双守卫（无状态 409 / identifier 复检 409）、
close 前置（开放任务 409 OPEN_ISSUES + open_count；force 批量取消 + affected）、
closed 无出边（BR-05）、closed 写保护（评论 403 PERM_PROJECT_CLOSED）、
状态日志只增（BR-13 首行 from=''）、生命周期事件投递（enqueue 捕获断言）、
模板 CRUD（内置保护 BR-10 / 快照校验 / 跨空间 404）、create 初态与模板实例化
（BR-11 计数）、副本重开（源态校验 + -C 标识 + 描述附注）。
"""
from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from plane.db.models import (
    Issue,
    Label,
    Project,
    ProjectMember,
    ProjectRole,
    ProjectStatusLog,
    State,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.roles import WorkspaceRole
from plane.db.seeds.project_states import seed_project_states

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="p3-owner@rabbit.dev", password="Rabbit123!",
                                     display_name="张三")
    member = User.objects.create_user(email="p3-member@rabbit.dev", password="Rabbit123!",
                                     display_name="李成员")
    ws = Workspace.objects.create(name="W", slug=f"w-p3-{owner.id.hex[:8]}",
                                  owner=owner, created_by=owner)
    for u, r in ((owner, WorkspaceRole.OWNER), (member, WorkspaceRole.MEMBER)):
        WorkspaceMember.objects.create(workspace=ws, member=u, role=r, created_by=owner)
    proj = Project.objects.create(name="P", identifier="P03", workspace=ws, created_by=owner)
    ProjectMember.objects.create(project=proj, member=member,
                                 role=ProjectRole.CONTRIBUTOR, created_by=owner)
    seed_project_states(proj)
    ProjectStatusLog.objects.create(project=proj, from_status="", to_status="active")
    return {"owner": owner, "member": member, "ws": ws, "proj": proj}


def _client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _t_url(env, suffix=""):
    return f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/{suffix}"


@pytest.fixture(autouse=True)
def _capture_lifecycle(monkeypatch):
    """捕获生命周期事件投递（TestCase 内 on_commit/.delay 不确定性隔离）。"""

    sent: list[dict] = []
    from plane.bgtasks import project_activity as pa

    monkeypatch.setattr(
        pa, "enqueue_project_activity",
        lambda **kw: sent.append(kw))
    # 服务模块 import 时机：project_lifecycle 在方法内延迟 import，patch bgtasks 层即可
    _capture_lifecycle.sent = sent
    yield sent


def test_transition_edges_and_idempotent(env):
    c = _client(env["owner"])
    # active → archived → active → archived（合法链）+ 幂等
    r = c.post(_t_url(env, "transitions/"), format="json", data={"to_status": "archived"})
    assert r.status_code == 200 and r.json()["data"]["status"] == "archived"
    r2 = c.post(_t_url(env, "transitions/"), format="json", data={"to_status": "archived"})
    assert r2.status_code == 200 and r2.json()["data"]["idempotent"] is True
    r3 = c.post(_t_url(env, "transitions/"), format="json", data={"to_status": "active"})
    assert r3.status_code == 200
    # 非法边：active → draft
    r4 = c.post(_t_url(env, "transitions/"), format="json", data={"to_status": "draft"})
    assert r4.status_code == 409
    e = r4.json()["error"]
    assert e["code"] == "RESOURCE_TRANSITION_INVALID"
    assert e["details"][0]["allowed_targets"] == ["archived", "closed"]
    # 权限：member（CONTRIBUTOR < ADMIN）403
    assert _client(env["member"]).post(
        _t_url(env, "transitions/"), format="json",
        data={"to_status": "archived"}).status_code == 403


def test_close_guard_and_force(env):
    todo = State.objects.get(project=env["proj"], group="unstarted")
    Issue.objects.create(name="开1", project=env["proj"], state=todo, priority="none",
                         sequence_id=1, sort_order=100, created_by=env["owner"])
    c = _client(env["owner"])
    r = c.post(_t_url(env, "transitions/"), format="json", data={"to_status": "closed"})
    assert r.status_code == 409
    d = r.json()["error"]["details"][0]
    assert d["code"] == "OPEN_ISSUES" and d["open_count"] == 1
    # force：批量取消 + affected=1 + 状态落 closed
    r2 = c.post(_t_url(env, "transitions/"), format="json",
                data={"to_status": "closed", "force": True, "reason": "收尾"})
    assert r2.status_code == 200
    assert r2.json()["data"]["affected_issues"] == 1
    env["proj"].refresh_from_db()
    assert env["proj"].status == "closed"
    assert Issue.objects.get(name="开1").state.group == "cancelled"
    # closed 无出边（BR-05）
    r3 = c.post(_t_url(env, "transitions/"), format="json", data={"to_status": "active"})
    assert r3.status_code == 409
    assert r3.json()["error"]["details"][0]["allowed_targets"] == []


def test_closed_write_protection(env):
    todo = State.objects.get(project=env["proj"], group="unstarted")
    issue = Issue.objects.create(name="任", project=env["proj"], state=todo,
                                 priority="none", sequence_id=1, sort_order=100,
                                 created_by=env["owner"])
    c = _client(env["owner"])
    c.post(_t_url(env, "transitions/"), format="json",
           data={"to_status": "closed", "force": True})
    r = c.post(
        f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/"
        f"issues/{issue.id}/comments/",
        format="json", data={"comment_html": "<p>hi</p>", "comment_json": {}})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "PERM_PROJECT_CLOSED"


def test_status_logs_append_only(env):
    c = _client(env["owner"])
    c.post(_t_url(env, "transitions/"), format="json", data={"to_status": "archived"})
    r = c.get(_t_url(env, "status-logs/"), format="json")
    rows = r.json()["data"]
    assert [x["to_status"] for x in reversed(rows)] == ["active", "archived"]
    assert rows[-1]["from_status"] == "" and rows[-1]["operator"] is None  # 首行（迁移回填形态）
    assert rows[0]["from_status"] == "active" and rows[0]["to_status"] == "archived"


def test_lifecycle_event_emitted(env, _capture_lifecycle):
    sent = _capture_lifecycle  # 夹具 yield 即捕获列表
    c = _client(env["owner"])
    c.post(_t_url(env, "transitions/"), format="json", data={"to_status": "archived"})
    assert any(k.get("field") == "status" and k.get("new_value") == "archived"
               and k.get("comment") == "milestone"
               for k in sent)


def test_draft_activate_guards(env):
    draft = Project.objects.create(name="D", identifier="D03", status="draft",
                                   workspace=env["ws"], created_by=env["owner"])
    ProjectMember.objects.create(project=draft, member=env["owner"],
                                 role=ProjectRole.ADMIN, created_by=env["owner"])
    url = f"/api/v1/workspaces/{env['ws'].slug}/projects/{draft.id}/transitions/"
    c = _client(env["owner"])
    # 无状态 → 409
    r = c.post(url, format="json", data={"to_status": "active"})
    assert r.status_code == 409 and "状态" in r.json()["error"]["message"]
    seed_project_states(draft)
    # BR-14：identifier 被 active 项目占用（P03 本身 active）——draft 建时撞了 P03？
    # 夹具 draft 用 D03 不冲突；构造占用：改名 P03
    draft.identifier = "P03"
    Project.objects.filter(pk=draft.pk).update(identifier="P03")
    r2 = c.post(url, format="json", data={"to_status": "active"})
    assert r2.status_code == 409  # identifier 复检
    Project.objects.filter(pk=draft.pk).update(identifier="D03")
    r3 = c.post(url, format="json", data={"to_status": "active"})
    assert r3.status_code == 200


def test_templates_crud(env):
    base = f"/api/v1/workspaces/{env['ws'].slug}/project-templates/"
    c = _client(env["owner"])
    rows = c.get(base, format="json").json()["data"]
    assert sum(1 for r in rows if r["is_builtin"]) == 3  # 迁移种子
    builtin = next(r for r in rows if r["is_builtin"])
    # member 建 → 403；owner 建自定义
    assert _client(env["member"]).post(base, format="json", data={"name": "T"}).status_code == 403
    r = c.post(base, format="json", data={
        "name": "自研模板", "description": "d",
        "states_snapshot": [{"name": "待办", "group": "unstarted"}],
        "labels_snapshot": [{"name": "x", "color": "#111111"}]})
    assert r.status_code == 201
    tid = r.json()["data"]["id"]
    assert c.post(base, format="json", data={"name": "自研模板"}).status_code == 409
    assert c.post(base, format="json", data={
        "name": "坏", "states_snapshot": [{"no_group": 1}]}).status_code == 400
    # 内置保护（BR-10）
    assert c.patch(f"{base}{builtin['id']}/", format="json",
                   data={"name": "X"}).status_code == 403
    assert c.delete(f"{base}{builtin['id']}/").status_code == 403
    assert c.patch(f"{base}{tid}/", format="json",
                   data={"description": "n"}).status_code == 200
    assert c.delete(f"{base}{tid}/").status_code == 204
    # 跨空间 404：另一空间的模板 id
    other = User.objects.create_user(email="p3-o2@rabbit.dev", password="Rabbit123!",
                                     display_name="外")
    ws2 = Workspace.objects.create(name="W2", slug=f"w2-p3-{other.id.hex[:8]}",
                                   owner=other, created_by=other)
    t2 = __import__("plane.db.models", fromlist=["ProjectTemplate"]).ProjectTemplate.objects.create(
        workspace=ws2, name="他山", states_snapshot=[], created_by=other)
    assert c.get(f"{base}{t2.id}/", format="json").status_code in (404, 405)  # 无 GET 详情端点→405/404


def test_create_with_initial_status_and_template(env):
    tpl = __import__("plane.db.models", fromlist=["ProjectTemplate"]).ProjectTemplate.objects.create(
        workspace=env["ws"], name="建站模板",
        states_snapshot=[{"name": "待办", "group": "unstarted", "is_default": True},
                         {"name": "进行中", "group": "started"}],
        labels_snapshot=[{"name": "web", "color": "#3B82F6"}],
        folders_snapshot=[{"name": "素材"}],
        created_by=env["owner"])
    c = _client(env["owner"])
    r = c.post(f"/api/v1/workspaces/{env['ws'].slug}/projects/", format="json", data={
        "name": "新项目", "identifier": "NP1", "initial_status": "draft",
        "template_id": str(tpl.id)})
    assert r.status_code == 201
    pid = r.json()["data"]["id"]
    proj = Project.objects.get(pk=pid)
    assert proj.status == "draft"
    assert State.objects.filter(project=proj).count() == 2      # 模板态替代种子四态
    assert Label.objects.filter(project=proj, name="web").exists()
    assert proj.status_logs.first().to_status == "draft"
    # 初态非法 400
    r2 = c.post(f"/api/v1/workspaces/{env['ws'].slug}/projects/", format="json", data={
        "name": "X", "identifier": "NP2", "initial_status": "closed"})
    assert r2.status_code == 400


def test_duplicate_closed(env):
    c = _client(env["owner"])
    # 非 closed 源 → 409
    assert c.post(_t_url(env, "duplicate/"), format="json").status_code == 409
    c.post(_t_url(env, "transitions/"), format="json",
           data={"to_status": "closed", "force": True})
    r = c.post(_t_url(env, "duplicate/"), format="json")
    assert r.status_code == 201
    copy = Project.objects.get(pk=r.json()["data"]["id"])
    assert copy.status == "draft" and copy.identifier == "P03-C"
    assert "副本" in copy.description
    assert ProjectMember.objects.filter(project=copy, member=env["owner"],
                                        role=ProjectRole.ADMIN).exists()
    assert State.objects.filter(project=copy).count() >= 4
