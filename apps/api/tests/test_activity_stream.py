"""COLLAB-003 项目动态流测试（T3-06，§5 用例表）。

覆盖：合流排序（UT-01）/ 复合游标同秒（UT-02）/ 批量折叠（UT-03/04/05）/
软删任务与归档保留（UT-06/14）/ 软删评论（UT-07）/ event 白名单（UT-08）/
cf 族过滤（UT-09）/ actor 域外空集（UT-10）/ 可见域 404（UT-11）/
明细上限（UT-12）/ stream_cursor 仅首页（UT-13）/ 非法游标三态（UT-15）/
组感知分页不割裂（UT-16 / IT-10）/ 非法 epoch（UT-17）/ 过滤组合矩阵（IT-06）/
移出成员 404（IT-04）/ 大项目分片（IT-03 缩放变体：450 任务 × 200 chunk，
逻辑同 6000×2000——仅阈值与数据集缩放）。

夹具风格对照 tests/test_issue_grouping.py（WorkspaceMember 必建、
APIClient + force_authenticate、直插 IssueActivity/IssueComment 造数据）。
"""
from __future__ import annotations

import base64
import itertools
import json
import time
import uuid as uuid_mod
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from plane.db.models import (
    Issue,
    IssueActivity,
    IssueComment,
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

_EPOCH_BASE = int(time.time() * 1000)
_EPOCH_SEQ = itertools.count(1)


def _next_epoch() -> float:
    """测试内唯一 epoch（毫秒时间戳形态，对照 build_activities 的 time()*1000）。"""
    return float(_EPOCH_BASE + next(_EPOCH_SEQ))


# ────────────────────────────────────────────────────────────────
# 夹具
# ────────────────────────────────────────────────────────────────
@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="c003-owner@rabbit.dev", password="Rabbit123!",
                                     display_name="张三")
    lisi = User.objects.create_user(email="c003-lisi@rabbit.dev", password="Rabbit123!",
                                    display_name="李四")
    wangwu = User.objects.create_user(email="c003-wangwu@rabbit.dev", password="Rabbit123!",
                                      display_name="王五")
    viewer = User.objects.create_user(email="c003-viewer@rabbit.dev", password="Rabbit123!",
                                      display_name="只读")
    outsider = User.objects.create_user(email="c003-outsider@rabbit.dev", password="Rabbit123!",
                                        display_name="外人")
    ws = Workspace.objects.create(name="W", slug=f"w-c003-{owner.id.hex[:8]}",
                                  owner=owner, created_by=owner)
    for u, ws_role in ((owner, WorkspaceRole.OWNER), (lisi, WorkspaceRole.MEMBER),
                       (wangwu, WorkspaceRole.MEMBER), (viewer, WorkspaceRole.MEMBER)):
        WorkspaceMember.objects.create(workspace=ws, member=u, role=ws_role, created_by=owner)
    proj = Project.objects.create(name="P", identifier="C03", workspace=ws, created_by=owner)
    ProjectMember.objects.create(project=proj, member=lisi, role=ProjectRole.CONTRIBUTOR,
                                 created_by=owner)
    ProjectMember.objects.create(project=proj, member=wangwu, role=ProjectRole.COMMENTER,
                                 created_by=owner)
    ProjectMember.objects.create(project=proj, member=viewer, role=ProjectRole.VIEWER,
                                 created_by=owner)
    seed_project_states(proj)
    todo = State.objects.get(project=proj, group=State.Group.UNSTARTED)

    seq = iter(range(1, 10000))

    def mk_issue(name):
        return Issue.objects.create(
            name=name, project=proj, state=todo, priority="none",
            sequence_id=next(seq), sort_order=next(seq) * 100, created_by=owner,
        )

    return {
        "owner": owner, "lisi": lisi, "wangwu": wangwu, "viewer": viewer,
        "outsider": outsider, "ws": ws, "proj": proj, "todo": todo, "mk_issue": mk_issue,
    }


class _Client:
    def __init__(self, user):
        self.c = APIClient()
        self.c.force_authenticate(user=user)

    def get(self, path):
        return self.c.get(path, format="json")


def _url(env, query=""):
    return f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/activities/{query}"


def _act(env, issue, actor, *, verb="updated", field=None, old=None, new=None,
         comment=None, epoch=None, minutes_ago=None):
    """直插 IssueActivity；minutes_ago 控制 created_at（auto_now_add 创建后覆写）。"""
    a = IssueActivity.objects.create(
        issue=issue, actor=actor, verb=verb, field=field,
        old_value=old, new_value=new,
        comment=comment or (f"更新了 {field}" if field else "操作了任务"),
        epoch=epoch if epoch is not None else _next_epoch(),
    )
    if minutes_ago is not None:
        IssueActivity.objects.filter(pk=a.pk).update(
            created_at=timezone.now() - timedelta(minutes=minutes_ago))
    return a


def _cmt(env, issue, actor, html, *, parent=None, accessory=None, deleted=False,
         minutes_ago=None):
    c = IssueComment.objects.create(
        issue=issue, actor=actor, parent=parent,
        comment_html=f"<p>{html}</p>", comment_json={}, accessory=accessory or {},
        created_by=actor, updated_by=actor,
    )
    # 覆写走 all_objects：软删后默认管理器滤掉该行，created_at 覆写会丢
    if minutes_ago is not None:
        IssueComment.all_objects.filter(pk=c.pk).update(
            created_at=timezone.now() - timedelta(minutes=minutes_ago))
    if deleted:
        IssueComment.all_objects.filter(pk=c.pk).update(deleted_at=timezone.now())
    return c


def _page(client, env, query=""):
    resp = client.get(_url(env, query))
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    return body["data"], body["meta"]


def _page_detail(client, env, epoch_query):
    resp = client.get(_url(env, f"?epoch={epoch_query}"))
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    return body["data"], body["meta"]


# ────────────────────────────────────────────────────────────────
# 合流排序（UT-01）与三态行契约
# ────────────────────────────────────────────────────────────────
class TestStreamMerge:
    def test_merged_global_order_activity_and_comment(self, env):
        """activity × comment 混排全局时间倒序（BR-03：SQL 层归并）。"""
        i1 = env["mk_issue"]("任务一")
        _cmt(env, i1, env["lisi"], "最早的评论", minutes_ago=40)
        _act(env, i1, env["owner"], field="state", old="待办", new="进行中",
             comment="更新了 状态", minutes_ago=30)
        _cmt(env, i1, env["wangwu"], "中间的评论", minutes_ago=20)
        _act(env, i1, env["lisi"], field="priority", old="none", new="high",
             comment="更新了 优先级", minutes_ago=10)

        data, meta = _page(_Client(env["owner"]), env)
        assert [r["kind"] for r in data] == ["activity", "comment", "activity", "comment"]
        stamps = [r["created_at"] for r in data]
        assert stamps == sorted(stamps, reverse=True)   # 全局倒序无乱序
        assert meta["count"] == 4 and meta["prev_cursor"] is None
        assert meta["prev_page_results"] is False       # is_prev 恒 0 的显式豁免

    def test_activity_row_shape(self, env):
        i1 = env["mk_issue"]("任务甲")
        _act(env, i1, env["lisi"], field="worklog", comment="⏱ 填报 2h", minutes_ago=5)
        data, _ = _page(_Client(env["viewer"]), env)    # VIEWER 也可读（project.read 全员）
        row = data[0]
        assert row["kind"] == "activity"
        assert row["verb"] == "updated" and row["field"] == "worklog"
        assert row["text"] == "⏱ 填报 2h" and row["is_system"] is False
        assert row["actor"]["display_name"] == "李四"
        assert row["issue"]["issue_key"] == f"C03-{i1.sequence_id}"
        assert row["issue"]["is_deleted"] is False and row["issue"]["is_archived"] is False

    def test_comment_row_shape_root_and_reply(self, env):
        """root_id / reply_to_actor 沿 COLLAB-002 §4.2 词汇；顶层两字段 null。"""
        i1 = env["mk_issue"]("任务乙")
        top = _cmt(env, i1, env["lisi"], "顶层评论", minutes_ago=10)
        _cmt(env, i1, env["wangwu"], "回复内容", parent=top,
             accessory={"reply_to": {"comment_id": str(top.id),
                                     "actor_id": str(env["lisi"].id)}},
             minutes_ago=5)
        data, _ = _page(_Client(env["owner"]), env)
        reply, top_row = data[0], data[1]
        assert top_row["kind"] == "comment"
        assert top_row["root_id"] is None and top_row["reply_to_actor"] is None
        assert top_row["text"] == "顶层评论"
        assert reply["root_id"] == str(top.id)
        assert reply["reply_to_actor"] == {"id": str(env["lisi"].id), "display_name": "李四"}

    def test_empty_project_stream(self, env):
        data, meta = _page(_Client(env["owner"]), env)
        assert data == []
        assert meta["count"] == 0 and meta["total_count"] == 0
        assert meta["next_cursor"] is None and meta["next_page_results"] is False


# ────────────────────────────────────────────────────────────────
# 批量折叠（UT-03/04/05）
# ────────────────────────────────────────────────────────────────
class TestBatchFolding:
    def test_cross_issue_same_epoch_folds_to_batch(self, env):
        """UT-03：同 epoch 跨任务折叠，batch_count = 整组行数。"""
        issues = [env["mk_issue"](f"批量任务 {n}") for n in range(3)]
        ep = _next_epoch()
        for i in issues:
            _act(env, i, env["owner"], field="state", old="待办", new="已完成",
                 comment="更新了 状态", epoch=ep, minutes_ago=10)
        data, meta = _page(_Client(env["owner"]), env)
        assert len(data) == 1
        row = data[0]
        assert row["kind"] == "batch" and row["batch_count"] == 3
        assert row["summary"] == "批量更新了 3 个任务"
        assert row["change_brief"] == "状态 待办 → 已完成"
        assert isinstance(row["epoch"], int)            # 整数毫秒直出（契约示例形态）
        assert row["issue"] is None                     # batch 行无 issue（跨任务）
        assert meta["count"] == 1 and meta["total_count"] == 1

    def test_same_issue_same_epoch_not_folded(self, env):
        """UT-04：同任务同 epoch 多字段 = 任务级一组，直出 N 条带 issue 单行。"""
        i1 = env["mk_issue"]("多字段任务")
        ep = _next_epoch()
        for field, old, new in (("state", "待办", "进行中"), ("priority", "none", "high"),
                                ("target_date", None, "2026-09-30")):
            _act(env, i1, env["owner"], field=field, old=old, new=new, epoch=ep,
                 comment=f"更新了 {field}", minutes_ago=10)
        data, meta = _page(_Client(env["owner"]), env)
        assert len(data) == 3 and all(r["kind"] == "activity" for r in data)
        assert all(r["issue"]["id"] == str(i1.id) for r in data)
        assert meta["total_count"] == 3                # 同任务多字段组计 N 行（要点 4）

    def test_change_brief_mixed(self, env):
        """UT-05：批量内变更不一致 → 「多种变更」。"""
        issues = [env["mk_issue"](f"混合批量 {n}") for n in range(2)]
        ep = _next_epoch()
        _act(env, issues[0], env["owner"], field="state", old="待办", new="进行中",
             comment="更新了 状态", epoch=ep, minutes_ago=10)
        _act(env, issues[1], env["owner"], field="priority", old="none", new="urgent",
             comment="更新了 优先级", epoch=ep, minutes_ago=10)
        data, _ = _page(_Client(env["owner"]), env)
        assert data[0]["change_brief"] == "多种变更"

    def test_comments_never_fold(self, env):
        """comment 行恒单行组（组键 c<id>）——同分钟两条评论不折叠。"""
        i1 = env["mk_issue"]("评论任务")
        _cmt(env, i1, env["lisi"], "评论一", minutes_ago=6)
        _cmt(env, i1, env["wangwu"], "评论二", minutes_ago=5)
        data, _ = _page(_Client(env["owner"]), env)
        assert [r["kind"] for r in data] == ["comment", "comment"]

    def test_actor_null_is_system_row(self, env):
        """BR-13：actor 为空才归系统行（is_system=true、actor=null）。"""
        i1 = env["mk_issue"]("系统任务")
        a = _act(env, i1, None, verb="created", field=None, comment="系统创建了任务",
                 minutes_ago=5)
        assert a.actor_id is None
        data, _ = _page(_Client(env["owner"]), env)
        assert data[0]["is_system"] is True and data[0]["actor"] is None


# ────────────────────────────────────────────────────────────────
# 组感知分页（UT-16 / IT-10 / UT-02 / UT-13 / BR-11）
# ────────────────────────────────────────────────────────────────
class TestGroupAwarePagination:
    @pytest.fixture()
    def boundary_env(self, env):
        """UT-16 布局（自新向旧数）：29 个单行组占第 1~29 组，
        50 行跨任务批量组 B30 恰为第 30 组、B31 为第 31 组——行分页下第 30 组
        必被割裂（50 行 > 30 限额），组感知两步取数下首页恰 30 组、批量组整组
        在页、次页从第 31 组起步无残余（IT-10 同范式）。
        """
        issues = [env["mk_issue"](f"边界任务 {n}") for n in range(50)]
        # 29 个单行组（最新，minute 10~38）
        for n in range(29):
            _act(env, issues[n % 50], env["owner"], field="priority", old="none",
                 new="low", comment="更新了 优先级", epoch=_next_epoch(),
                 minutes_ago=10 + n)
        # 批量组 B30（第 30 组，minute 50，比全部单行组旧）
        ep30 = _next_epoch()
        for i in issues:
            _act(env, i, env["owner"], field="state", old="待办", new="已完成",
                 comment="更新了 状态", epoch=ep30, minutes_ago=50)
        # 批量组 B31（第 31 组，minute 60，最旧）
        ep31 = _next_epoch()
        for i in issues:
            _act(env, i, env["lisi"], field="state", old="已完成", new="进行中",
                 comment="更新了 状态", epoch=ep31, minutes_ago=60)
        return {"issues": issues, "ep30": ep30, "ep31": ep31}

    def test_batch_group_not_split_across_pages(self, env, boundary_env):
        client = _Client(env["owner"])
        data, meta = _page(client, env)
        # 首页恰 30 组：29 单行 + B30 整组（29 + 1 = 30 视觉行）
        assert meta["count"] == 30 and meta["per_page"] == 30
        assert meta["next_page_results"] is True and meta["next_cursor"]
        batch_rows = [r for r in data if r["kind"] == "batch"]
        assert len(batch_rows) == 1
        assert batch_rows[0]["batch_count"] == 50      # 整组行数（非页内可见数，BR-04）
        assert batch_rows[0]["epoch"] == int(boundary_env["ep30"])
        # 次页：从第 31 组（B31）起步，无第 30 组残余
        data2, meta2 = _page(client, env, f"?cursor={meta['next_cursor']}")
        assert meta2["page"] == 2
        assert len(data2) == 1 and data2[0]["kind"] == "batch"
        assert data2[0]["batch_count"] == 50
        assert data2[0]["epoch"] == int(boundary_env["ep31"])
        assert meta2["next_page_results"] is False
        epochs = {r.get("epoch") for r in data2 if r["kind"] == "activity"}
        assert int(boundary_env["ep30"]) not in epochs  # 次页无 B30 残余行

    def test_same_timestamp_keyset_no_dup_no_loss(self, env):
        """UT-02：同秒 3 条不同 id（组键不同）——复合游标翻页无重复无丢失。"""
        i1, i2, i3 = (env["mk_issue"](f"同秒任务 {n}") for n in range(3))
        same = timezone.now() - timedelta(minutes=5)
        for issue, ep in ((i1, 9000.0), (i2, 7000.0), (i3, 5000.0)):
            a = IssueActivity.objects.create(
                issue=issue, actor=env["owner"], verb="updated", field="priority",
                old_value="none", new_value="high", comment="更新了 优先级", epoch=ep)
            IssueActivity.objects.filter(pk=a.pk).update(created_at=same)
        client = _Client(env["owner"])
        data1, meta1 = _page(client, env, "?per_page=2")
        data2, _ = _page(client, env, f"?cursor={meta1['next_cursor']}")
        ids1 = [r["id"] for r in data1]
        ids2 = [r["id"] for r in data2]
        assert len(ids1) == 2 and len(ids2) == 1
        assert not (set(ids1) & set(ids2)) and len(set(ids1 + ids2)) == 3

    def test_stream_cursor_first_page_only(self, env):
        """UT-13/BR-12：stream_cursor 仅首页携带；格式 <ISO8601>:<UUID>。"""
        i1 = env["mk_issue"]("水位任务")
        _act(env, i1, env["owner"], field="state", old="待办", new="进行中",
             comment="更新了 状态", minutes_ago=5)
        for n in range(35):  # 合计 36 组 > 30/页，保证有第二页
            _act(env, i1, env["lisi"], field="priority", old="none", new="low",
                 comment="更新了 优先级", epoch=_next_epoch(), minutes_ago=10 + n)
        client = _Client(env["owner"])
        _, meta1 = _page(client, env)
        assert meta1["stream_cursor"]
        ts, _, uid = meta1["stream_cursor"].rpartition(":")
        uuid_mod.UUID(uid)                              # 自右向左最后一段为 UUID
        assert ts.endswith("+00:00") or ts.endswith("Z")
        _, meta2 = _page(client, env, f"?cursor={meta1['next_cursor']}")
        assert "stream_cursor" not in meta2

    def test_stream_cursor_batch_first_row_uses_latest_activity(self, env):
        """要点 2：首页首行为 batch 行时，水位取其底层最新一条 Activity 的锚。"""
        issues = [env["mk_issue"](f"水位批量 {n}") for n in range(3)]
        ep = _next_epoch()
        rows = [
            _act(env, i, env["owner"], field="state", old="待办", new="已完成",
                 comment="更新了 状态", epoch=ep, minutes_ago=m)
            for i, m in zip(issues, (30, 20, 10), strict=True)
        ]
        latest = IssueActivity.objects.get(pk=rows[-1].pk)   # minutes_ago=10 的一行
        _, meta = _page(_Client(env["owner"]), env)
        assert meta["stream_cursor"] == f"{latest.created_at.isoformat()}:{latest.id}"

    def test_per_page_clamped_with_degraded(self, env):
        """BR-11：per_page 按组数上限 50——超限静默截断 + meta.degraded。"""
        i1 = env["mk_issue"]("截断任务")
        _act(env, i1, env["owner"], field="state", old="待办", new="进行中",
             comment="更新了 状态", minutes_ago=5)
        _, meta = _page(_Client(env["owner"]), env, "?per_page=100")
        assert meta["per_page"] == 50
        assert "已截断" in meta["degraded"]["per_page"]

    def test_meta_nine_fields_present(self, env):
        _, meta = _page(_Client(env["owner"]), env)
        assert set(meta) >= {
            "next_cursor", "prev_cursor", "next_page_results", "prev_page_results",
            "count", "total_count", "total_pages", "page", "per_page",
        }

    def test_folded_counting_units(self, env):
        """要点 4：total_count 为折叠后视觉行数（batch 组 1 行、同任务组 N 行）。"""
        batch_issues = [env["mk_issue"](f"计数批量 {n}") for n in range(3)]
        ep = _next_epoch()
        for i in batch_issues:
            _act(env, i, env["owner"], field="state", old="待办", new="进行中",
                 comment="更新了 状态", epoch=ep, minutes_ago=40)
        multi = env["mk_issue"]("计数字段组")
        ep2 = _next_epoch()
        for f_ in ("state", "priority"):
            _act(env, multi, env["owner"], field=f_, old="a", new="b", epoch=ep2,
                 comment=f"更新了 {f_}", minutes_ago=30)
        _act(env, multi, env["lisi"], field="priority", old="a", new="b",
             comment="更新了 优先级", minutes_ago=20)     # 单行组
        _cmt(env, multi, env["wangwu"], "计数评论", minutes_ago=10)
        _, meta = _page(_Client(env["owner"]), env)
        # 1（batch）+ 2（同任务组 N 行）+ 1 + 1（comment）= 5 视觉行；总组数 4
        assert meta["total_count"] == 5
        assert meta["count"] == 5
        assert meta["total_pages"] == 1


# ────────────────────────────────────────────────────────────────
# 过滤矩阵（§4.3.1 actor × event / IT-06 / UT-09 / UT-10）
# ────────────────────────────────────────────────────────────────
class TestFilterMatrix:
    @pytest.fixture()
    def matrix_env(self, env):
        i1, i2 = env["mk_issue"]("过滤一"), env["mk_issue"]("过滤二")
        self.lisi_state = _act(env, i1, env["lisi"], field="state", old="待办",
                               new="进行中", comment="更新了 状态", minutes_ago=50)
        self.lisi_priority = _act(env, i1, env["lisi"], field="priority", old="none",
                                  new="high", comment="更新了 优先级", minutes_ago=40)
        self.wangwu_state = _act(env, i2, env["wangwu"], field="state", old="进行中",
                                 new="已完成", comment="更新了 状态", minutes_ago=30)
        self.lisi_comment = _cmt(env, i1, env["lisi"], "李四的评论", minutes_ago=20)
        self.wangwu_comment = _cmt(env, i2, env["wangwu"], "王五的评论", minutes_ago=10)
        return env

    def test_event_state_excludes_comments(self, matrix_env):
        data, _ = _page(_Client(matrix_env["owner"]), matrix_env, "?event=state")
        assert {r["kind"] for r in data} == {"activity"}
        assert {r["field"] for r in data} == {"state"}
        assert {r["id"] for r in data} == {str(self.lisi_state.id), str(self.wangwu_state.id)}

    def test_event_comment_excludes_activities(self, matrix_env):
        data, _ = _page(_Client(matrix_env["owner"]), matrix_env, "?event=comment")
        assert {r["kind"] for r in data} == {"comment"}
        assert {r["id"] for r in data} == {str(self.lisi_comment.id), str(self.wangwu_comment.id)}

    def test_actor_and_event_and_semantics(self, matrix_env):
        """IT-06：actor_id × event 恒 AND；仅李四的 state 流转 / 仅李四的评论。"""
        data, _ = _page(_Client(matrix_env["owner"]), matrix_env,
                        f"?actor_id={matrix_env['lisi'].id}&event=state")
        assert [r["id"] for r in data] == [str(self.lisi_state.id)]
        data, _ = _page(_Client(matrix_env["owner"]), matrix_env,
                        f"?actor_id={matrix_env['lisi'].id}&event=comment")
        assert [r["id"] for r in data] == [str(self.lisi_comment.id)]
        # 无 event 时 actor 同样滤 comment 行（过滤所有行类型的本人动作）
        data, _ = _page(_Client(matrix_env["owner"]), matrix_env,
                        f"?actor_id={matrix_env['lisi'].id}")
        assert {r["id"] for r in data} == {
            str(self.lisi_state.id), str(self.lisi_priority.id), str(self.lisi_comment.id)}

    def test_actor_outside_domain_empty_not_404(self, matrix_env):
        """UT-10/BR-09：域外 actor_id → 空集（过滤是缩小不是寻址）。"""
        data, meta = _page(_Client(matrix_env["owner"]), matrix_env,
                           f"?actor_id={matrix_env['outsider'].id}")
        assert data == [] and meta["total_count"] == 0

    def test_actor_malformed_uuid_empty_set(self, matrix_env):
        data, _ = _page(_Client(matrix_env["owner"]), matrix_env, "?actor_id=not-a-uuid")
        assert data == []

    def test_custom_fields_family(self, env):
        """UT-09：event=custom_fields 命中 cf_* 全部 field。"""
        i1 = env["mk_issue"]("字段任务")
        cf_a = _act(env, i1, env["owner"], field="cf_severity", old="major",
                    new="critical", comment="更新了 severity", minutes_ago=30)
        cf_b = _act(env, i1, env["owner"], field="cf_risk", old="low", new="high",
                    comment="更新了 risk", minutes_ago=20)
        _act(env, i1, env["owner"], field="priority", old="none", new="high",
             comment="更新了 优先级", minutes_ago=10)
        data, _ = _page(_Client(env["owner"]), env, "?event=custom_fields")
        assert {r["id"] for r in data} == {str(cf_a.id), str(cf_b.id)}

    def test_other_semantic_groups(self, env):
        i1 = env["mk_issue"]("语义组任务")
        cases = {
            "created": dict(verb="created", field=None),
            "dates": dict(field="target_date"),
            "estimate": dict(field="estimate_minutes"),
            "relations": dict(field="relation"),
            "parent": dict(field="parent"),
            "worklog": dict(field="worklog"),
            "archived": dict(field="archived_at"),
            "deleted": dict(verb="deleted", field="parent"),
        }
        minutes = 100
        expected = {}
        for group, kw in cases.items():
            a = _act(env, i1, env["owner"], minutes_ago=minutes, comment="动作", **kw)
            expected[group] = str(a.id)
            minutes -= 10
        _act(env, i1, env["owner"], field="priority", old="a", new="b",
             comment="更新了 优先级", minutes_ago=5)
        for group, aid in expected.items():
            data, _ = _page(_Client(env["owner"]), env, f"?event={group}")
            assert [r["id"] for r in data] == [aid], group


# ────────────────────────────────────────────────────────────────
# 参数校验（UT-08 / UT-17 / UT-15）
# ────────────────────────────────────────────────────────────────
class TestValidation:
    def test_invalid_event_400_with_choices(self, env):
        resp = _Client(env["owner"]).get(_url(env, "?event=foo"))
        assert resp.status_code == 400
        err = resp.json()["error"]
        assert err["code"] == "VALIDATION_INVALID_PARAM"
        assert err["details"][0]["field"] == "event"
        assert "可用值" in err["details"][0]["message"]
        assert "custom_fields" in err["details"][0]["message"]

    def test_invalid_epoch_400(self, env):
        """UT-17：寻址型参数非数值 → 400（区别于 BR-09 过滤空集语义）。"""
        resp = _Client(env["owner"]).get(_url(env, "?epoch=abc"))
        assert resp.status_code == 400
        err = resp.json()["error"]
        assert err["code"] == "VALIDATION_INVALID_PARAM"
        assert err["details"][0]["field"] == "epoch"

    def test_invalid_cursor_garbage_400(self, env):
        resp = _Client(env["owner"]).get(_url(env, "?cursor=%%%garbage"))
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_INVALID_CURSOR"

    def test_invalid_cursor_bad_group_key_400(self, env):
        """篡改锚字段：组键 g 格式非法 → 400（要点 5 失败路径）。"""
        payload = base64.urlsafe_b64encode(json.dumps(
            {"a": "2026-09-05T06:32:00+00:00", "g": "zz-bad-key", "f": "da39a3ee"}
        ).encode()).decode().rstrip("=")
        resp = _Client(env["owner"]).get(_url(env, f"?cursor={payload}:1:0"))
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_INVALID_CURSOR"

    def test_invalid_cursor_fingerprint_mismatch_400(self, env):
        """过滤变更后复用旧游标（指纹不符）→ 400（UT-15/IT-09）。"""
        i1 = env["mk_issue"]("游标任务")
        for n in range(35):
            _act(env, i1, env["owner"], field="priority", old="a", new="b",
                 comment="更新了 优先级", epoch=_next_epoch(), minutes_ago=5 + n)
        client = _Client(env["owner"])
        _, meta = _page(client, env)
        resp = client.get(_url(env, f"?cursor={meta['next_cursor']}&event=state"))
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_INVALID_CURSOR"


# ────────────────────────────────────────────────────────────────
# 软删 / 归档可见性（UT-06 / UT-07 / UT-14 / 父删子留）
# ────────────────────────────────────────────────────────────────
class TestSoftDeleteVisibility:
    def test_soft_deleted_issue_activity_kept(self, env):
        """UT-06/BR-06：软删任务动态保留，issue.is_deleted=true。"""
        i1 = env["mk_issue"]("将被删除")
        a = _act(env, i1, env["owner"], field="state", old="待办", new="进行中",
                 comment="更新了 状态", minutes_ago=10)
        Issue.objects.filter(pk=i1.pk).update(deleted_at=timezone.now())
        data, _ = _page(_Client(env["owner"]), env)
        assert len(data) == 1 and data[0]["id"] == str(a.id)
        assert data[0]["issue"]["is_deleted"] is True

    def test_archived_issue_activity_kept(self, env):
        """UT-14/BR-07：归档任务动态保留，is_archived=true。"""
        i1 = env["mk_issue"]("将被归档")
        _act(env, i1, env["owner"], field="archived_at", old=None, new="2026-09-05",
             comment="归档了任务", minutes_ago=10)
        Issue.objects.filter(pk=i1.pk).update(archived_at=timezone.now())
        data, _ = _page(_Client(env["owner"]), env)
        assert data[0]["issue"]["is_archived"] is True
        assert data[0]["issue"]["is_deleted"] is False

    def test_soft_deleted_comment_placeholder_text(self, env):
        """UT-07：软删评论 → kind=comment 行「删除了一条评论」（不显内容）。"""
        i1 = env["mk_issue"]("评论软删任务")
        _cmt(env, i1, env["lisi"], "这条会被删除", minutes_ago=10, deleted=True)
        _cmt(env, i1, env["wangwu"], "这条保留", minutes_ago=5)
        data, _ = _page(_Client(env["owner"]), env)
        assert [r["text"] for r in data] == ["这条保留", "删除了一条评论"]

    def test_soft_deleted_parent_reply_stays_visible(self, env):
        """父删子留：软删父以「删除了一条评论」行出现，子回复原样保留（§4.1）。"""
        i1 = env["mk_issue"]("父删子留")
        parent = _cmt(env, i1, env["lisi"], "父评论", minutes_ago=20, deleted=True)
        _cmt(env, i1, env["wangwu"], "子回复", parent=parent,
             accessory={"reply_to": {"comment_id": str(parent.id),
                                     "actor_id": str(env["lisi"].id)}}, minutes_ago=10)
        data, _ = _page(_Client(env["owner"]), env)
        reply = data[0]
        assert reply["text"] == "子回复" and reply["root_id"] == str(parent.id)
        assert data[1]["text"] == "删除了一条评论"

    def test_batch_detail_includes_soft_deleted_issue(self, env):
        """明细抽屉同样保留软删任务的行（BR-06 审计整圈口径）。"""
        i1, i2 = env["mk_issue"]("明细存活"), env["mk_issue"]("明细已删")
        ep = _next_epoch()
        for i in (i1, i2):
            _act(env, i, env["owner"], field="state", old="待办", new="已完成",
                 comment="更新了 状态", epoch=ep, minutes_ago=10)
        Issue.objects.filter(pk=i2.pk).update(deleted_at=timezone.now())
        data, _ = _page_detail(_Client(env["owner"]), env, ep)
        assert {r["issue_id"] for r in data} == {str(i1.id), str(i2.id)}


# ────────────────────────────────────────────────────────────────
# 批量明细（§4.2.2 / UT-12 / meta 豁免）
# ────────────────────────────────────────────────────────────────
class TestBatchDetail:
    def test_detail_light_rows_and_meta_waiver(self, env):
        issues = [env["mk_issue"](f"明细任务 {n}") for n in range(3)]
        ep = _next_epoch()
        for i in issues:
            _act(env, i, env["owner"], field="state", old="待办", new="已完成",
                 comment="更新了 状态", epoch=ep, minutes_ago=10)
        data, meta = _page_detail(_Client(env["owner"]), env, ep)
        assert set(meta) == {"count", "total_count", "truncated", "limit"}  # 翻页九字段显式豁免
        assert meta == {"count": 3, "total_count": 3, "truncated": False, "limit": 100}
        assert set(data[0]) == {"issue_id", "issue_key", "name", "field", "old_value", "new_value"}
        assert data[0]["issue_key"] == f"C03-{issues[0].sequence_id}"
        assert data[0]["old_value"] == "待办" and data[0]["new_value"] == "已完成"
        # 按 sequence_id 排序
        keys = [int(r["issue_key"].split("-")[1]) for r in data]
        assert keys == sorted(keys)

    def test_detail_truncated_at_100(self, env):
        """UT-12：epoch 内 150 条 → 100 + truncated。"""
        issues = [env["mk_issue"](f"截断明细 {n}") for n in range(150)]
        ep = _next_epoch()
        for i in issues:
            _act(env, i, env["owner"], field="state", old="待办", new="已完成",
                 comment="更新了 状态", epoch=ep, minutes_ago=10)
        data, meta = _page_detail(_Client(env["owner"]), env, ep)
        assert meta["count"] == 100 and meta["total_count"] == 150
        assert meta["truncated"] is True and meta["limit"] == 100

    def test_detail_per_page_lower_limit(self, env):
        issues = [env["mk_issue"](f"限额明细 {n}") for n in range(5)]
        ep = _next_epoch()
        for i in issues:
            _act(env, i, env["owner"], field="state", old="待办", new="已完成",
                 comment="更新了 状态", epoch=ep, minutes_ago=10)
        data, meta = _page_detail(_Client(env["owner"]), env, f"{ep}&per_page=2")
        assert len(data) == 2 and meta["limit"] == 2 and meta["truncated"] is True

    def test_detail_epoch_no_match_empty(self, env):
        """epoch 数值合法但无匹配 → 200 空集（§2.5 明细抽屉空）。"""
        data, meta = _page_detail(_Client(env["owner"]), env, "12345.5")
        assert data == [] and meta["count"] == 0 and meta["truncated"] is False


# ────────────────────────────────────────────────────────────────
# 可见域（UT-11 / IT-04）
# ────────────────────────────────────────────────────────────────
class TestAccessControl:
    def test_non_member_404(self, env):
        resp = _Client(env["outsider"]).get(_url(env))
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    def test_removed_project_member_404(self, env):
        """IT-04：移出成员后访问旧链接 → 404（WS MEMBER 无隐式 ADMIN）。"""
        ProjectMember.objects.filter(project=env["proj"], member=env["wangwu"]).delete()
        resp = _Client(env["wangwu"]).get(_url(env))
        assert resp.status_code == 404

    def test_viewer_can_read(self, env):
        resp = _Client(env["viewer"]).get(_url(env))
        assert resp.status_code == 200


# ────────────────────────────────────────────────────────────────
# 大项目分片（IT-03 缩放变体：450 任务 × 200 chunk）
# ────────────────────────────────────────────────────────────────
class TestIssueIdsChunking:
    def test_chunked_stream_pagination_consistent(self, env, monkeypatch):
        """IT-03 缩放变体：阈值 5000/2000 → 400/200（逻辑同款，数据集缩放）。

        450 任务触发 [200, 200, 50] 三分片——OR(ANY(…)) 同语句归并下
        翻页遍历无重复无丢失、折叠计数精确。
        """
        import plane.db.services.activity_stream as svc
        monkeypatch.setattr(svc, "ISSUE_IDS_CHUNK_TRIGGER", 400)
        monkeypatch.setattr(svc, "ISSUE_IDS_CHUNK_SIZE", 200)

        Issue.objects.bulk_create([
            Issue(name=f"分片任务 {n}", project=env["proj"], state=env["todo"],
                  priority="none", sequence_id=1000 + n, sort_order=1000 + n,
                  created_by=env["owner"])
            for n in range(450)
        ])
        chunk_ids = list(
            Issue.objects.filter(project=env["proj"], name__startswith="分片任务")
            .values_list("id", flat=True))
        IssueActivity.objects.bulk_create([
            IssueActivity(issue_id=iid, actor_id=env["owner"].id, verb="updated",
                          field="priority", old_value="none", new_value="low",
                          comment="更新了 优先级", epoch=float(_EPOCH_BASE + n))
            for n, iid in enumerate(chunk_ids)
        ])
        assert len(chunk_ids) == 450
        assert [len(c) for c in svc._chunk_issue_ids(
            svc.project_issue_ids(env["proj"].id))] == [200, 200, 50]

        client = _Client(env["owner"])
        seen: set[str] = set()
        query = "?per_page=50"
        pages = 0
        while True:
            data, meta = _page(client, env, query)
            for r in data:
                assert r["id"] not in seen
                seen.add(r["id"])
            pages += 1
            assert meta["total_count"] == 450 and meta["per_page"] == 50
            if not meta["next_cursor"]:
                break
            query = f"?cursor={meta['next_cursor']}&per_page=50"
            assert pages <= 10
        assert pages == 9 and len(seen) == 450   # 无重复无丢失
