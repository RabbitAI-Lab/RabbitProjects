"""Sprint-5 T5-02 管道扩域测试：project 域动态流 + file 域事件上流。

覆盖（PROJ-003 §4.3.1 扩域设计 + ADR-0022 D-2 收口）：

  - XOR 双轨约束（全空 / 双填均拒绝）
  - project 域行入流（kind='project'，与 issue/comment 原位合流排序）
  - event=lifecycle 语义组（project 域保留、issue/comment 排除）
  - issue 语义组 / comment 组排除 project 域行；actor 过滤三源同 AND
  - 空任务集项目同样上流 project 域行（旧口径为整页空）
  - project_activity 任务幂等（同载荷重投一行；field 参键区分同毫秒异操作）
  - emit_file_activity 十动作 → verb/field 映射；非文件库归属（project 空）不投
  - record_project_activity 薄壳（milestone → comment='milestone'）
  - 落库尾部 activity.created 水位锚投递（dispatch_event 捕获）

夹具风格对照 tests/test_activity_stream.py。
"""
from __future__ import annotations

import uuid

import pytest
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from plane.db.models import (
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

pytestmark = pytest.mark.django_db


# ────────────────────────────────────────────────────────────────
# 夹具
# ────────────────────────────────────────────────────────────────
@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="p005-owner@rabbit.dev", password="Rabbit123!",
                                     display_name="张三")
    lisi = User.objects.create_user(email="p005-lisi@rabbit.dev", password="Rabbit123!",
                                    display_name="李四")
    ws = Workspace.objects.create(name="W", slug=f"w-p005-{owner.id.hex[:8]}",
                                  owner=owner, created_by=owner)
    for u, ws_role in ((owner, WorkspaceRole.OWNER), (lisi, WorkspaceRole.MEMBER)):
        WorkspaceMember.objects.create(workspace=ws, member=u, role=ws_role, created_by=owner)
    proj = Project.objects.create(name="P", identifier="P05", workspace=ws, created_by=owner)
    ProjectMember.objects.create(project=proj, member=lisi, role=ProjectRole.CONTRIBUTOR,
                                 created_by=owner)
    seed_project_states(proj)
    todo = State.objects.get(project=proj, group=State.Group.UNSTARTED)
    issue = Issue.objects.create(
        name="任务甲", project=proj, state=todo, priority="none",
        sequence_id=1, sort_order=100, created_by=owner,
    )
    empty_proj = Project.objects.create(name="E", identifier="E05", workspace=ws,
                                        created_by=owner)
    return {"owner": owner, "lisi": lisi, "ws": ws, "proj": proj,
            "issue": issue, "empty_proj": empty_proj}


def _client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _url(env, query=""):
    return (f"/api/v1/workspaces/{env['ws'].slug}/"
            f"projects/{env['proj'].id}/activities/{query}")


def _issue_act(env, *, field="state", verb="updated"):
    return IssueActivity.objects.create(
        issue=env["issue"], actor=env["owner"], verb=verb, field=field,
        old_value="待办", new_value="进行中", comment=f"更新了 {field}", epoch=1000.0,
    )


def _project_act(env, *, verb="updated", field="status", actor=None, epoch=1001.0,
                 comment=""):
    from plane.bgtasks.project_activity import _write_row

    row, _ = _write_row({
        "project_id": str(env["proj"].id),
        "actor_id": str((actor or env["owner"]).id),
        "verb": verb, "field": field, "epoch": epoch,
        "old_value": "active", "new_value": "archived",
        "comment": comment or "归档了项目",
    })
    return row


def _run_task(payload: dict) -> None:
    from plane.bgtasks.project_activity import project_activity

    project_activity(payload)


# ────────────────────────────────────────────────────────────────
# XOR 双轨约束（迁移要点 ⑤）
# ────────────────────────────────────────────────────────────────
def test_xor_rejects_neither(env):
    with pytest.raises(IntegrityError), transaction.atomic():
        IssueActivity.objects.create(
            actor=env["owner"], verb="created", comment="孤儿行", epoch=1.0)


def test_xor_rejects_both(env):
    with pytest.raises(IntegrityError), transaction.atomic():
        IssueActivity.objects.create(
            issue=env["issue"], project=env["proj"],
            actor=env["owner"], verb="created", comment="双轨行", epoch=1.0)


# ────────────────────────────────────────────────────────────────
# 入流与过滤矩阵
# ────────────────────────────────────────────────────────────────
def test_project_rows_merge_into_stream(env):
    _issue_act(env)
    _project_act(env)
    res = _client(env["owner"]).get(_url(env), format="json")
    assert res.status_code == 200
    rows = res.json()["data"]
    kinds = [r["kind"] for r in rows]
    assert kinds.count("project") == 1 and kinds.count("activity") == 1
    proj_row = next(r for r in rows if r["kind"] == "project")
    assert proj_row["issue"] is None  # 视图水合：issue_id → issue 投影（project 域无归属）
    assert proj_row["actor"]["id"] == str(env["owner"].id)
    assert proj_row["field"] == "status" and proj_row["verb"] == "updated"


def test_lifecycle_event_group(env):
    _issue_act(env)
    _project_act(env)                       # field=status（生命周期）
    _project_act(env, verb="updated", field="file.renamed", epoch=1002.0)
    res = _client(env["owner"]).get(_url(env) + "?event=lifecycle", format="json")
    assert res.status_code == 200
    rows = res.json()["data"]
    assert rows and all(r["kind"] == "project" for r in rows)
    assert all(r["field"] == "status" or r["verb"] == "created" for r in rows)
    assert not any(r["kind"] in ("activity", "comment") for r in rows)


def test_issue_group_excludes_project_rows(env):
    _issue_act(env)
    _project_act(env)
    _project_act(env, field="file.renamed", epoch=1002.0)
    res = _client(env["owner"]).get(_url(env) + "?event=state", format="json")
    rows = res.json()["data"]
    assert rows and all(r["kind"] == "activity" for r in rows)


def test_actor_filter_applies_to_project_rows(env):
    _project_act(env, actor=env["owner"], epoch=1001.0)
    _project_act(env, actor=env["lisi"], epoch=1002.0)
    res = _client(env["owner"]).get(
        _url(env) + f"?actor_id={env['lisi'].id}", format="json")
    rows = res.json()["data"]
    assert len(rows) == 1 and rows[0]["actor"]["id"] == str(env["lisi"].id)


def test_empty_issue_project_still_streams(env):
    from plane.bgtasks.project_activity import _write_row

    _write_row({"project_id": str(env["empty_proj"].id), "actor_id": str(env["owner"].id),
                "verb": "created", "field": None, "epoch": 2000.0,
                "comment": "创建了项目"})
    res = _client(env["owner"]).get(
        f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['empty_proj'].id}/activities/",
        format="json")
    assert res.status_code == 200
    rows = res.json()["data"]
    assert len(rows) == 1 and rows[0]["kind"] == "project" and rows[0]["verb"] == "created"


# ────────────────────────────────────────────────────────────────
# 任务幂等与水位锚
# ────────────────────────────────────────────────────────────────
def test_task_idempotent_same_payload(env):
    payload = {"project_id": str(env["proj"].id), "actor_id": str(env["owner"].id),
               "verb": "updated", "field": "status", "epoch": 3000.0,
               "old_value": "active", "new_value": "closed", "comment": "关闭了项目"}
    _run_task(payload)
    _run_task(payload)
    assert IssueActivity.objects.filter(
        project=env["proj"], verb="updated", field="status", epoch=3000.0).count() == 1


def test_task_field_disambiguates_same_epoch(env):
    base = {"project_id": str(env["proj"].id), "actor_id": str(env["owner"].id),
            "epoch": 3001.0}
    _run_task({**base, "verb": "updated", "field": "file.renamed", "comment": "重命名"})
    _run_task({**base, "verb": "updated", "field": "file.moved", "comment": "移动"})
    assert IssueActivity.objects.filter(project=env["proj"], epoch=3001.0).count() == 2


def test_actor_deleted_race_degrades_to_system_row(env):
    """on_commit 异步窗内 actor 被硬删（清理竞态）：留痕优先降级系统行，不入死信。"""
    ghost = uuid.uuid4()  # 不存在的用户 ID——模拟任务执行时 actor 已被硬删
    _run_task({"project_id": str(env["proj"].id), "actor_id": str(ghost),
               "verb": "updated", "field": "file.renamed", "epoch": 3003.0,
               "comment": "重命名（操作者已删）"})
    row = IssueActivity.objects.get(project=env["proj"], epoch=3003.0)
    assert row.actor_id is None  # is_system 口径（BR-13）


def test_project_deleted_race_drops_row(env):
    """项目在任务执行前被硬删：行无落点，放弃（None 返回、不抛、不留半行）。"""
    from plane.bgtasks.project_activity import _write_row

    row, created = _write_row({
        "project_id": str(uuid.uuid4()), "actor_id": str(env["owner"].id),
        "verb": "updated", "field": "file.renamed", "epoch": 3004.0,
        "comment": "移动（项目已删）"})
    assert row is None and created is False


def test_task_publishes_water_level(env, monkeypatch):
    captured: list[tuple] = []
    import plane.bgtasks.event_publisher as ep

    monkeypatch.setattr(ep, "dispatch_event",
                        lambda event, payload, rooms, occurred_at=None: captured.append(
                            (event, payload, rooms)))
    _run_task({"project_id": str(env["proj"].id), "actor_id": str(env["owner"].id),
               "verb": "updated", "field": "status", "epoch": 3002.0,
               "comment": "归档了项目"})
    assert len(captured) == 1
    event, payload, rooms = captured[0]
    assert event == "activity.created"
    assert payload["project_id"] == str(env["proj"].id)
    assert "stream_cursor" in payload and "issue_id" not in payload
    assert rooms == [f"project:{env['proj'].id}"]


# ────────────────────────────────────────────────────────────────
# file 域事件映射（emit_file_activity）
# ────────────────────────────────────────────────────────────────
def test_file_activity_all_actions(env):
    from plane.bgtasks.project_activity import _write_row
    from plane.db.services.file_stream import _FILE_ACTIONS

    for i, action in enumerate(_FILE_ACTIONS, start=4000):
        verb, field = _FILE_ACTIONS[action]
        _write_row({"project_id": str(env["proj"].id),
                    "actor_id": str(env["owner"].id),
                    "verb": verb, "field": field, "epoch": float(i),
                    "comment": f"事件 {action}"})
    fields = set(IssueActivity.objects.filter(
        project=env["proj"], field__startswith="file.").values_list("field", flat=True))
    assert fields == {pair[1] for pair in _FILE_ACTIONS.values()}


def test_file_activity_none_project_noop(env):
    from plane.db.services.file_stream import emit_file_activity

    emit_file_activity(project_id=None, asset_id=None, actor_id=env["owner"].id,
                       action="uploaded", comment="不投")
    # 作用域过滤（坑 18）：全表 count 会撞 dev 库 worker 异步落库的残留
    assert IssueActivity.objects.filter(
        field="file.uploaded", comment="不投").count() == 0


def test_emit_file_activity_via_enqueue(env, monkeypatch):
    """enqueue → 任务轨道（测试内同步化：delay 改 apply 防真投递异步竞态）。"""
    import plane.bgtasks.project_activity as pa
    from plane.db.services.file_stream import emit_file_activity

    monkeypatch.setattr(
        pa.project_activity, "delay",
        lambda payload: pa.project_activity.apply(args=[payload]))
    emit_file_activity(project_id=env["proj"].id, asset_id=env["issue"].id,
                       actor_id=env["owner"].id, action="uploaded",
                       comment="上传了文件「a.png」")
    row = IssueActivity.objects.get(project=env["proj"], field="file.uploaded")
    assert row.verb == "created" and "a.png" in row.comment
    assert str(row.new_identifier) == str(env["issue"].id)


def test_record_project_activity_milestone(env, monkeypatch):
    import plane.bgtasks.project_activity as pa

    sent: list[dict] = []
    monkeypatch.setattr(pa, "enqueue_project_activity",
                        lambda **kw: sent.append(kw))
    pa.record_project_activity(  # bind=True 直调无需传 self（celery 装饰期已绑定）
        str(env["proj"].id), "updated", "archived",
        actor_id=str(env["owner"].id), milestone=True, old_status="active",
        epoch=6000.0)
    assert sent == [{
        "project_id": env["proj"].id, "actor_id": env["owner"].id,
        "verb": "updated", "field": "status", "old_value": "active",
        "new_value": "archived", "comment": "milestone", "epoch": 6000.0,
    }]
