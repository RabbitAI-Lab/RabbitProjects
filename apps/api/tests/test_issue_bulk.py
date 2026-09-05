"""BOARD-004 任务批量操作测试（UT-01~15 / BE-1~15 对应）。

夹具风格同 test_issue_grouping.py；nowait 并发用例（BE-13）采用
psycopg 直连第二连接锁行——pytest 事务内的未提交行对第二连接不可见
（已实测锚定），故该用例的最小夹具经 raw psycopg **真实提交**建库、
finally 级清理，属 dev 库内自建自清的可控等价手法。
"""

from __future__ import annotations

import datetime
import os
import time
import uuid

import psycopg
import pytest
from django.test import TestCase as _DjTestCase
from rest_framework.test import APIClient

from plane.db.models import (
    Issue,
    IssueActivity,
    IssueAssignee,
    IssueComment,
    IssueLabel,
    IssueLink,
    Label,
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
from plane.db.seeds.project_states import seed_project_states
from plane.db.services.issue_archive import archive_subtree
from plane.db.services.issue_hierarchy import delete_subtree

pytestmark = pytest.mark.django_db


# ─────────────────────────────────────────────────────────────────────
# 夹具
# ─────────────────────────────────────────────────────────────────────
@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="bulk-owner@rabbit.dev", password="Rabbit123!")
    member = User.objects.create_user(email="bulk-member@rabbit.dev", password="Rabbit123!")
    viewer = User.objects.create_user(email="bulk-viewer@rabbit.dev", password="Rabbit123!")
    ws = Workspace.objects.create(name="W", slug=f"w-bulk-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    proj = Project.objects.create(name="P", identifier="BK", workspace=ws, created_by=owner)
    other = Project.objects.create(name="O", identifier="BK2", workspace=ws, created_by=owner)
    for u, ws_role in ((owner, WorkspaceRole.OWNER), (member, WorkspaceRole.MEMBER), (viewer, WorkspaceRole.MEMBER)):
        WorkspaceMember.objects.create(workspace=ws, member=u, role=ws_role, created_by=owner)
    ProjectMember.objects.create(project=proj, member=member, role=ProjectRole.CONTRIBUTOR, created_by=owner)
    ProjectMember.objects.create(project=proj, member=viewer, role=ProjectRole.VIEWER, created_by=owner)
    seed_project_states(proj)
    todo = State.objects.get(project=proj, group=State.Group.UNSTARTED)
    doing = State.objects.get(project=proj, group=State.Group.STARTED)
    done = State.objects.get(project=proj, group=State.Group.COMPLETED)
    seq = iter(range(1, 500))

    def mk(name, *, project=None, state=None, pri="none", parent=None, created_by=None):
        return Issue.objects.create(
            name=name, project=project or proj, state=state or todo, priority=pri,
            parent=parent, sequence_id=next(seq), sort_order=next(seq) * 100,
            created_by=created_by or owner,
        )

    return {
        "owner": owner, "member": member, "viewer": viewer, "ws": ws,
        "proj": proj, "other": other, "todo": todo, "doing": doing, "done": done, "mk": mk,
    }


class _Client:
    def __init__(self, user):
        self.c = APIClient()
        self.c.force_authenticate(user=user)

    def patch(self, path, payload):
        return self.c.patch(path, payload, format="json")

    def post(self, path, payload):
        return self.c.post(path, payload, format="json")

    def delete(self, path, payload):
        return self.c.delete(path, payload, format="json")


def _bulk_url(env, suffix=""):
    return f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/issues/bulk/{suffix}"


def _ids(*issues):
    return [str(i.id) for i in issues]


# ─────────────────────────────────────────────────────────────────────
# UT-01/02 / BR-01 / BR-07 / BR-12：上限 / 去重 / 项级不可见 / comment
# ─────────────────────────────────────────────────────────────────────
class TestBulkUpdateBasics:
    def test_priority_success_envelope(self, env):
        a, b, c = env["mk"]("A"), env["mk"]("B"), env["mk"]("C")
        resp = _Client(env["owner"]).patch(_bulk_url(env), {
            "issue_ids": _ids(a, b, c), "patch": {"priority": "high"}, "comment": "提级",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["updated"] == 3 and body["data"]["action"] == "priority"
        assert body["data"]["comment"] == "提级" and body["meta"]["batch_size"] == 3
        assert isinstance(body["data"]["epoch"], float)
        assert {i.priority for i in Issue.objects.filter(pk__in=[a.id, b.id, c.id])} == {"high"}

    def test_bulk_limit_boundary_and_exceeded(self, env):
        issues = [env["mk"](f"L{i}") for i in range(100)]  # 恰 100 → 200
        resp = _Client(env["owner"]).patch(_bulk_url(env), {
            "issue_ids": _ids(*issues), "patch": {"priority": "low"},
        })
        assert resp.status_code == 200 and resp.json()["data"]["updated"] == 100
        extra = str(uuid.uuid4())
        resp2 = _Client(env["owner"]).patch(_bulk_url(env), {  # 101 → 400（UT-01）
            "issue_ids": [*_ids(*issues), extra], "patch": {"priority": "low"},
        })
        assert resp2.status_code == 400
        err = resp2.json()["error"]
        assert err["code"] == "VALIDATION_BULK_LIMIT_EXCEEDED"
        assert err["details"][0] == {"field": "issue_ids", "code": "LIMIT", "message": "提交 101 条"}

    def test_dedupe_preserve_order(self, env):
        a, b, c = env["mk"]("A"), env["mk"]("B"), env["mk"]("C")
        resp = _Client(env["owner"]).patch(_bulk_url(env), {
            "issue_ids": [str(a.id), str(b.id), str(a.id), str(c.id)],  # UT-02：去重保序
            "patch": {"priority": "medium"},
        })
        assert resp.status_code == 200
        assert resp.json()["data"]["updated"] == 3 and resp.json()["meta"]["batch_size"] == 3

    def test_cross_project_id_item_level(self, env):
        a = env["mk"]("A")
        foreign = env["mk"]("F", project=env["other"])
        resp = _Client(env["owner"]).patch(_bulk_url(env), {
            "issue_ids": [str(a.id), str(foreign.id)], "patch": {"priority": "high"},
        })
        assert resp.status_code == 400  # BR-07：项级 DOES_NOT_EXIST，不 404 整批
        err = resp.json()["error"]
        assert err["code"] == "VALIDATION_ERROR"
        assert err["details"][0]["field"] == "issue_ids[1]"
        assert err["details"][0]["code"] == "DOES_NOT_EXIST"
        a.refresh_from_db()
        assert a.priority == "none"  # 整批回滚（BE-15 + 零残留）

    def test_comment_too_long_rejected(self, env):
        a = env["mk"]("A")
        resp = _Client(env["owner"]).patch(_bulk_url(env), {
            "issue_ids": [str(a.id)], "patch": {"priority": "high"}, "comment": "x" * 501,
        })
        assert resp.status_code == 400  # UT-13 / BR-12
        assert resp.json()["error"]["details"][0]["code"] == "TOO_LONG"

    def test_action_field_cardinality(self, env):
        a = env["mk"]("A")
        base = _bulk_url(env)
        resp = _Client(env["owner"]).patch(base, {"issue_ids": [str(a.id)]})
        assert resp.status_code == 400
        resp2 = _Client(env["owner"]).patch(base, {
            "issue_ids": [str(a.id)], "patch": {"priority": "high"},
            "assignees": {"mode": "replace", "assignee_ids": []},
        })
        assert resp2.status_code == 400
        assert resp2.json()["error"]["details"][0]["field"] == "__all__"

    def test_payload_level_validation(self, env):
        a = env["mk"]("A")
        base = _bulk_url(env)
        resp = _Client(env["owner"]).patch(base, {
            "issue_ids": [str(a.id)], "patch": {"priority": "bogus"},
        })
        assert resp.status_code == 400
        assert resp.json()["error"]["details"][0] == {
            "field": "patch.priority", "code": "NOT_A_CHOICE", "message": "优先级取值非法"}
        resp2 = _Client(env["owner"]).patch(base, {
            "issue_ids": [str(a.id)], "patch": {"state_id": str(uuid.uuid4())},
        })
        assert resp2.status_code == 400
        assert resp2.json()["error"]["details"][0]["field"] == "patch.state_id"


# ─────────────────────────────────────────────────────────────────────
# UT-04 / BE-1~3：流转守卫逐条 + 单事务回滚零残留
# ─────────────────────────────────────────────────────────────────────
class TestStateGuardAndRollback:
    def test_guard_blocks_whole_batch_with_index(self, env):
        issues = [env["mk"](f"S{i}") for i in range(7)]
        # 第 7 项（0 基索引 6）被**批外**未完成前置阻塞（UT-04 场景：阻塞源不在本批——
        # 批内阻塞源会被本批先行完成的逐条写解除，属合法的「阻塞链一起完成」语义）
        blocker = env["mk"]("前置未完成")
        IssueLink.objects.create(
            issue=issues[6], related_issue=blocker,
            relation_type="is_blocked_by", created_by=env["owner"])
        before = {i.id: (i.state_id, i.updated_at, i.priority) for i in issues}
        resp = _Client(env["owner"]).patch(_bulk_url(env), {
            "issue_ids": _ids(*issues), "patch": {"state_id": str(env["done"].id)},
            "comment": "迭代收尾",
        })
        assert resp.status_code == 400  # UT-04 / BE-2
        err = resp.json()["error"]
        assert "7 项中 1 项未通过校验" in err["message"]
        assert err["details"][0]["field"] == "issue_ids[6]"  # 0 基索引（BE-2）
        assert err["details"][0]["code"] == "BLOCKED_BY"
        assert f"BK-{blocker.sequence_id}" in err["details"][0]["message"]  # 阻塞源 issue_key
        for i in Issue.objects.filter(pk__in=[x.id for x in issues]):  # BE-3：零残留
            state, updated_at, priority = before[i.id]
            assert i.state_id == state and i.priority == priority
            assert i.updated_at == updated_at  # 回滚验证：updated_at 未变

    def test_state_change_writes_completed_at(self, env):
        a, b, c = env["mk"]("A"), env["mk"]("B"), env["mk"]("C")
        resp = _Client(env["owner"]).patch(_bulk_url(env), {
            "issue_ids": _ids(a, b, c), "patch": {"state_id": str(env["done"].id)},
        })
        assert resp.status_code == 200  # BE-1
        for i in Issue.objects.filter(pk__in=[a.id, b.id, c.id]):
            assert i.state_id == env["done"].id and i.completed_at is not None

    def test_member_role_allowed_for_update(self, env):
        a = env["mk"]("A")
        resp = _Client(env["member"]).patch(_bulk_url(env), {
            "issue_ids": [str(a.id)], "patch": {"priority": "high"},
        })
        assert resp.status_code == 200  # CONTRIBUTOR：issue.update / issue.bulk.update


# ─────────────────────────────────────────────────────────────────────
# UT-05~08 / BE-4/5：指派与标签三模式（差 / 并 / 替换）
# ─────────────────────────────────────────────────────────────────────
class TestSetOperations:
    @pytest.fixture()
    def assignee_env(self, env):
        env["contributor_b"] = User.objects.create_user(email="bulk-b@rabbit.dev", password="Rabbit123!")
        ProjectMember.objects.create(
            project=env["proj"], member=env["contributor_b"], role=ProjectRole.CONTRIBUTOR,
            created_by=env["owner"])
        return env

    def _assign(self, issue, *users):
        IssueAssignee.objects.bulk_create([
            IssueAssignee(issue=issue, assignee=u, created_by=issue.created_by) for u in users])

    def test_assignees_add_union(self, assignee_env):
        env = assignee_env
        a = env["mk"]("A")
        self._assign(a, env["member"])  # current：member（显式 CONTRIBUTOR 成员）
        resp = _Client(env["owner"]).patch(_bulk_url(env), {
            "issue_ids": [str(a.id)],
            "assignees": {"mode": "add",
                          "assignee_ids": [str(env["contributor_b"].id), str(env["member"].id)]},
        })
        assert resp.status_code == 200  # UT-06：并集（current + 新者追加，去重）
        got = set(IssueAssignee.objects.filter(issue=a).values_list("assignee_id", flat=True))
        assert got == {env["member"].id, env["contributor_b"].id}

    def test_assignees_remove_difference(self, assignee_env):
        env = assignee_env
        a = env["mk"]("A")
        self._assign(a, env["member"], env["contributor_b"])
        resp = _Client(env["owner"]).patch(_bulk_url(env), {
            "issue_ids": [str(a.id)],
            "assignees": {"mode": "remove", "assignee_ids": [str(env["contributor_b"].id)]},
        })
        assert resp.status_code == 200  # UT-07：差集
        got = set(IssueAssignee.objects.filter(issue=a).values_list("assignee_id", flat=True))
        assert got == {env["member"].id}

    def test_assignees_replace_put_semantics(self, assignee_env):
        env = assignee_env
        a = env["mk"]("A")
        self._assign(a, env["member"])
        resp = _Client(env["owner"]).patch(_bulk_url(env), {
            "issue_ids": [str(a.id)],
            "assignees": {"mode": "replace", "assignee_ids": [str(env["contributor_b"].id)]},
        })
        assert resp.status_code == 200  # UT-08：与单条 PUT 等价
        got = set(IssueAssignee.objects.filter(issue=a).values_list("assignee_id", flat=True))
        assert got == {env["contributor_b"].id}

    def test_assignees_over_limit_item_failure(self, assignee_env):
        env = assignee_env  # UT-07：add 并集后 > 10 → 项级 LIMIT（sync 入口校验同源）
        more = [User.objects.create_user(email=f"bulk-x{i}@rabbit.dev", password="Rabbit123!")
                for i in range(8)]
        for u in more:
            ProjectMember.objects.create(
                project=env["proj"], member=u, role=ProjectRole.CONTRIBUTOR, created_by=env["owner"])
        b = env["mk"]("B")
        self._assign(b, env["member"], env["contributor_b"], *more[:8])  # current 10 人
        eleventh = User.objects.create_user(email="bulk-x11@rabbit.dev", password="Rabbit123!")
        ProjectMember.objects.create(
            project=env["proj"], member=eleventh, role=ProjectRole.CONTRIBUTOR, created_by=env["owner"])
        resp = _Client(env["owner"]).patch(_bulk_url(env), {
            "issue_ids": [str(b.id)],
            "assignees": {"mode": "add", "assignee_ids": [str(eleventh.id)]},
        })
        assert resp.status_code == 400  # 10 + 1 = 11 > 10
        det = resp.json()["error"]["details"][0]
        assert det["field"] == "issue_ids[0]" and det["code"] == "LIMIT"
        assert IssueAssignee.objects.filter(issue=b).count() == 10  # 整批回滚

    def test_assignees_invalid_member_item_failure(self, assignee_env):
        env = assignee_env  # viewer 是 VIEWER，不可被指派（BR-02 口径）
        a = env["mk"]("A")
        resp = _Client(env["owner"]).patch(_bulk_url(env), {
            "issue_ids": [str(a.id)],
            "assignees": {"mode": "add", "assignee_ids": [str(env["viewer"].id)]},
        })
        assert resp.status_code == 400
        det = resp.json()["error"]["details"][0]
        assert det["field"] == "issue_ids[0]" and det["code"] == "DOES_NOT_EXIST"

    @pytest.fixture()
    def label_env(self, env):
        env["la"] = Label.objects.create(project=env["proj"], name="la", created_by=env["owner"])
        env["lb"] = Label.objects.create(project=env["proj"], name="lb", created_by=env["owner"])
        env["lother"] = Label.objects.create(project=env["other"], name="lo", created_by=env["owner"])
        return env

    def _label(self, issue, *labels):
        IssueLabel.objects.bulk_create([
            IssueLabel(issue=issue, label=lab, created_by=issue.created_by) for lab in labels])

    def test_labels_three_modes(self, label_env):
        env = label_env
        a = env["mk"]("A")
        self._label(a, env["la"])
        base = _bulk_url(env)
        resp = _Client(env["owner"]).patch(base, {  # add 并集
            "issue_ids": [str(a.id)],
            "labels": {"mode": "add", "label_ids": [str(env["lb"].id)]}})
        assert resp.status_code == 200
        assert set(IssueLabel.objects.filter(issue=a).values_list("label_id", flat=True)) == {
            env["la"].id, env["lb"].id}
        resp2 = _Client(env["owner"]).patch(base, {  # remove 差集
            "issue_ids": [str(a.id)],
            "labels": {"mode": "remove", "label_ids": [str(env["la"].id)]}})
        assert resp2.status_code == 200
        assert set(IssueLabel.objects.filter(issue=a).values_list("label_id", flat=True)) == {
            env["lb"].id}
        resp3 = _Client(env["owner"]).patch(base, {  # replace 替换
            "issue_ids": [str(a.id)],
            "labels": {"mode": "replace", "label_ids": [str(env["la"].id)]}})
        assert resp3.status_code == 200
        assert set(IssueLabel.objects.filter(issue=a).values_list("label_id", flat=True)) == {
            env["la"].id}

    def test_labels_foreign_label_payload_level(self, label_env):
        env = label_env
        a = env["mk"]("A")
        resp = _Client(env["owner"]).patch(_bulk_url(env), {
            "issue_ids": [str(a.id)],
            "labels": {"mode": "add", "label_ids": [str(env["lother"].id)]}})
        assert resp.status_code == 400  # 标签属本项目（载荷级）
        assert resp.json()["error"]["details"][0]["field"] == "labels.label_ids"


# ─────────────────────────────────────────────────────────────────────
# UT-09 / BE-6：归档级联幂等 + archived_count 口径
# ─────────────────────────────────────────────────────────────────────
class TestBulkArchive:
    def test_archive_cascade_idempotent(self, env):
        # §4.2.2 场景同构：5 选 = 1 父 + 4 个独立任务；父的子树 4 条（2 条此前已归档）
        root = env["mk"]("R")
        children = [env["mk"](f"C{i}", parent=root) for i in range(4)]
        Issue.objects.filter(pk__in=[children[0].id, children[1].id]).update(
            archived_at=datetime.datetime(2026, 8, 1, tzinfo=datetime.UTC))
        standalone = [env["mk"](f"S{i}") for i in range(4)]
        payload = {"issue_ids": _ids(root, *standalone), "comment": "季度收尾归档"}
        resp = _Client(env["owner"]).post(_bulk_url(env, "archive/"), payload)
        assert resp.status_code == 200
        data, meta = resp.json()["data"], resp.json()["meta"]
        assert data["archived_count"] == 7  # 仅新置行：root + 2 未归档子 + 4 独立（幂等口径）
        assert data["affected_total"] == 9  # 选中 + 级联（含已归档行）
        assert meta == {"batch_size": 5, "cascade": 4}
        # 幂等重提交：不重复计数（BE-6）
        resp2 = _Client(env["owner"]).post(_bulk_url(env, "archive/"), payload)
        assert resp2.status_code == 200
        assert resp2.json()["data"]["archived_count"] == 0
        assert resp2.json()["data"]["affected_total"] == 9

    def test_archive_member_only_own(self, env):
        a = env["mk"]("A")  # owner 创建；member 非 ADMIN → 项级 PERM_DENIED（issue.archive 口径）
        b = env["mk"]("B", created_by=env["member"])
        resp = _Client(env["member"]).post(_bulk_url(env, "archive/"), {
            "issue_ids": _ids(a, b)})
        assert resp.status_code == 400
        det = resp.json()["error"]["details"]
        assert det[0]["field"] == "issue_ids[0]" and det[0]["code"] == "PERM_DENIED"
        assert Issue.objects.get(pk=a.id).archived_at is None  # 整批回滚
        assert Issue.objects.get(pk=b.id).archived_at is None
        resp2 = _Client(env["member"]).post(_bulk_url(env, "archive/"), {
            "issue_ids": [str(b.id)]})  # 本人创建可归档
        assert resp2.status_code == 200 and resp2.json()["data"]["archived_count"] == 1

    def test_archive_owner_any(self, env):
        a = env["mk"]("A", created_by=env["member"])
        resp = _Client(env["owner"]).post(_bulk_url(env, "archive/"), {"issue_ids": [str(a.id)]})
        assert resp.status_code == 200  # WS OWNER 隐式 PROJ_ADMIN 任意归档


# ─────────────────────────────────────────────────────────────────────
# UT-12 / BE-9 / BE-14：删除确认错配 + 权限矩阵
# ─────────────────────────────────────────────────────────────────────
class TestBulkDelete:
    def test_confirm_count_mismatch(self, env):
        a, b = env["mk"]("A"), env["mk"]("B")
        resp = _Client(env["owner"]).delete(_bulk_url(env), {
            "issue_ids": _ids(a, b), "confirm_count": 1})
        assert resp.status_code == 400  # UT-12 / BE-9
        assert resp.json()["error"]["details"][0]["field"] == "confirm_count"
        assert Issue.objects.filter(pk__in=[a.id, b.id], deleted_at__isnull=True).count() == 2

    def test_delete_with_cascade(self, env):
        root = env["mk"]("R")
        child = env["mk"]("C", parent=root)
        leaf = env["mk"]("L", parent=child)
        resp = _Client(env["owner"]).delete(_bulk_url(env), {
            "issue_ids": [str(root.id)], "confirm_count": 1})
        assert resp.status_code == 200
        data, meta = resp.json()["data"], resp.json()["meta"]
        assert data == {"deleted": 1, "affected_total": 3, "epoch": data["epoch"]}
        assert meta == {"batch_size": 1, "cascade": 2}
        assert not Issue.objects.filter(pk__in=[root.id, child.id, leaf.id], deleted_at__isnull=True)

    def test_contributor_mixed_foreign_items(self, env):
        mine = env["mk"]("mine", created_by=env["member"])
        theirs = env["mk"]("theirs", created_by=env["owner"])
        resp = _Client(env["member"]).delete(_bulk_url(env), {
            "issue_ids": _ids(mine, theirs), "confirm_count": 2})
        assert resp.status_code == 400  # UT-05 / BR-10：混删他人 → 项级 PERM_DENIED
        det = resp.json()["error"]["details"]
        assert det[0]["field"] == "issue_ids[1]" and det[0]["code"] == "PERM_DENIED"
        assert Issue.objects.get(pk=mine.id).deleted_at is None  # 零残留
        resp2 = _Client(env["member"]).delete(_bulk_url(env), {
            "issue_ids": [str(mine.id)], "confirm_count": 1})
        assert resp2.status_code == 200

    def test_viewer_rejected_on_all_write_endpoints(self, env):
        a = env["mk"]("A")
        base = _bulk_url(env)
        resp1 = _Client(env["viewer"]).patch(base, {"issue_ids": [str(a.id)], "patch": {"priority": "high"}})
        resp2 = _Client(env["viewer"]).post(base + "archive/", {"issue_ids": [str(a.id)]})
        resp3 = _Client(env["viewer"]).delete(base, {"issue_ids": [str(a.id)], "confirm_count": 1})
        for r in (resp1, resp2, resp3):  # BE-14：VIEWER 全拒
            assert r.status_code == 403
            assert r.json()["error"]["code"] == "PERM_ROLE_INSUFFICIENT"
        # preview 只读（issue.read）：VIEWER 可用
        resp4 = _Client(env["viewer"]).post(base + "preview/", {
            "issue_ids": [str(a.id)], "action": "delete"})
        assert resp4.status_code == 200


# ─────────────────────────────────────────────────────────────────────
# BE-7/8：preview 统计与 denied + 只读性
# ─────────────────────────────────────────────────────────────────────
class TestPreview:
    def test_stats_and_denied(self, env):
        roots = [env["mk"](f"R{i}") for i in range(3)]
        children = [[env["mk"](f"C{i}-{j}", parent=r) for j in range(2)] for i, r in enumerate(roots)]
        flat = [i for sub in children for i in sub]
        IssueLink.objects.create(
            issue=roots[0], related_issue=roots[1], relation_type="relates_to", created_by=env["owner"])
        WorkLog.objects.create(
            issue=flat[0], actor=env["owner"], worked_on=datetime.date(2026, 9, 1), minutes=30)
        IssueComment.objects.create(
            issue=flat[1], actor=env["owner"], comment_html="<p>c</p>")
        before = {i.id: i.updated_at for i in [*roots, *flat]}
        resp = _Client(env["member"]).post(_bulk_url(env, "preview/"), {
            "issue_ids": _ids(*roots), "action": "delete"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["selected"] == 3 and data["with_subtree"] == 3
        assert data["cascade_total"] == 6 and data["affected_total"] == 9
        assert data["links"] == 1 and data["worklogs"] == 1 and data["comments"] == 1
        assert data["denied"] == [  # member 非 ADMIN 非创建者 → 全部项级暴露
            {"index": i, "issue_key": f"BK-{r.sequence_id}", "reason": "仅创建者可删除"}
            for i, r in enumerate(roots)
        ]
        for i in Issue.objects.filter(pk__in=[*before]):  # BE-8：零写入
            assert i.updated_at == before[i.id]

    def test_preview_denied_missing_id(self, env):
        a = env["mk"]("A")
        resp = _Client(env["owner"]).post(_bulk_url(env, "preview/"), {
            "issue_ids": [str(a.id), str(uuid.uuid4())], "action": "archive"})
        assert resp.status_code == 200
        assert resp.json()["data"]["denied"] == [
            {"index": 1, "issue_key": None, "reason": "任务不存在或不可见"}]
        assert resp.json()["data"]["selected"] == 2 and resp.json()["data"]["affected_total"] == 1

    def test_preview_invalid_action(self, env):
        a = env["mk"]("A")
        resp = _Client(env["owner"]).post(_bulk_url(env, "preview/"), {
            "issue_ids": [str(a.id)], "action": "copy"})
        assert resp.status_code == 400
        assert resp.json()["error"]["details"][0]["field"] == "action"


# ─────────────────────────────────────────────────────────────────────
# UT-14 / BE-12：throttle 10/min → 429 + Retry-After
# ─────────────────────────────────────────────────────────────────────
class TestThrottle:
    def test_11th_request_429(self, env, monkeypatch):
        from django.core.cache import cache

        cache.clear()  # locmem throttle 桶按 user id 隔离
        user = User.objects.create_user(email="bulk-throttle@rabbit.dev", password="Rabbit123!")
        WorkspaceMember.objects.create(
            workspace=env["ws"], member=user, role=WorkspaceRole.MEMBER, created_by=env["owner"])
        ProjectMember.objects.create(
            project=env["proj"], member=user, role=ProjectRole.CONTRIBUTOR, created_by=env["owner"])
        a = env["mk"]("A")
        client = _Client(user)
        for _ in range(10):
            resp = client.post(_bulk_url(env, "preview/"), {
                "issue_ids": [str(a.id)], "action": "delete"})
            assert resp.status_code == 200
        resp11 = client.post(_bulk_url(env, "preview/"), {
            "issue_ids": [str(a.id)], "action": "delete"})
        assert resp11.status_code == 429  # UT-14 / BE-12
        body = resp11.json()
        assert body["error"]["code"] == "RATE_LIMIT_EXCEEDED"
        assert "Retry-After" in resp11.headers
        assert int(resp11.headers["Retry-After"]) >= 1
        # 其他用户不受影响（10/min/用户）
        resp_other = _Client(env["owner"]).post(_bulk_url(env, "preview/"), {
            "issue_ids": [str(a.id)], "action": "delete"})
        assert resp_other.status_code == 200
        cache.clear()


# ─────────────────────────────────────────────────────────────────────
# UT-11 / BE-11：幂等键重放（Redis 可用；不可用 skip）
# ─────────────────────────────────────────────────────────────────────
class TestIdempotencyKey:
    def test_replay_returns_first_response(self, env):
        from plane.app.views.issue_bulk import _IDEM_PREFIX, _idem_redis

        client = _idem_redis()
        if client is None:
            pytest.skip("Redis 不可用：幂等键降级直通（报告注明的降级口径）")
        a, b = env["mk"]("A"), env["mk"]("B")
        key = f"test-{uuid.uuid4()}"
        headers = {"Idempotency-Key": key}
        c = APIClient()
        c.force_authenticate(user=env["owner"])
        url = _bulk_url(env)
        first = c.delete(url, {"issue_ids": _ids(a, b), "confirm_count": 2},
                         format="json", headers=headers)
        assert first.status_code == 200
        deleted_at_snapshot = {
            i.id: i.deleted_at for i in Issue.objects.filter(pk__in=[a.id, b.id])}
        second = c.delete(url, {"issue_ids": _ids(a, b), "confirm_count": 2},
                          format="json", headers=headers)
        assert second.status_code == 200  # UT-11 / BE-11：首次响应体回放
        assert second.json() == first.json()
        assert second.headers.get("Idempotency-Replayed") == "true"
        assert first.headers.get("Idempotency-Replayed") is None
        # 库中零二次副作用
        for i in Issue.objects.filter(pk__in=[a.id, b.id]):
            assert i.deleted_at == deleted_at_snapshot[i.id]
        client.delete(f"{_IDEM_PREFIX}{env['owner'].id}:{key}")

    def test_no_key_pass_through(self, env):
        a = env["mk"]("A")
        resp = _Client(env["owner"]).patch(_bulk_url(env), {
            "issue_ids": [str(a.id)], "patch": {"priority": "high"}})
        assert resp.status_code == 200
        assert "Idempotency-Replayed" not in resp.headers


# ─────────────────────────────────────────────────────────────────────
# BE-13：nowait 并发 → 409 快速失败（raw psycopg committed 夹具）
# ─────────────────────────────────────────────────────────────────────
PG_URL = os.environ.get("DATABASE_URL", "postgresql://rp:rp@localhost:5432/rabbit_projects")


def _raw_insert(cur, table: str, **cols) -> uuid.UUID:
    """NOT NULL 无默认列按类型补最小值后 INSERT，返回主键 id。"""
    cur.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s AND is_nullable='NO' AND column_default IS NULL",
        [table])
    values = dict(cols)
    for name, dtype in cur.fetchall():
        if name in values:
            continue
        if dtype == "uuid":  # BaseModel 主键与 FK：Django 应用侧默认，DB 无默认
            values[name] = uuid.uuid4()
        elif dtype in ("character varying", "text"):
            values[name] = ""
        elif dtype == "boolean":
            values[name] = False
        elif dtype in ("timestamp with time zone", "timestamp without time zone"):
            values[name] = datetime.datetime.now()
        elif dtype in ("integer", "bigint"):
            values[name] = 0
        elif dtype == "double precision":
            values[name] = 0.0
        elif dtype == "jsonb":
            from psycopg.types.json import Jsonb

            values[name] = Jsonb({})
    cols_sql = ", ".join(f'"{k}"' for k in values)
    placeholders = ", ".join(["%s"] * len(values))
    cur.execute(
        f'INSERT INTO "{table}" ({cols_sql}) VALUES ({placeholders}) RETURNING id',
        list(values.values()))
    return cur.fetchone()[0]


#: committed 原始夹具登记表（nowait 用例）——清理必须推迟到模块 teardown：
#: 用例内 finally 清理会与 pytest 事务（后续成功写持有的行锁）互等成死锁。
_RAW_CLEANUP: dict[str, list] = {"issues": [], "projects": [], "workspaces": [], "users": []}


@pytest.fixture(scope="module", autouse=True)
def _raw_fixture_cleanup():
    """模块 teardown 统一清理 committed 夹具（此时持锁的测试事务早已回滚释放）。"""
    yield
    if not any(_RAW_CLEANUP.values()):
        return
    conn = psycopg.connect(PG_URL)
    try:
        with conn.cursor() as cur:
            # issues 具 DB 级 ON DELETE CASCADE（中间表 / activities 随行级联）
            cur.execute("DELETE FROM issues WHERE id = ANY(%s)", [_RAW_CLEANUP["issues"]])
            cur.execute("DELETE FROM project_members WHERE project_id = ANY(%s)", [_RAW_CLEANUP["projects"]])
            cur.execute("DELETE FROM projects WHERE id = ANY(%s)", [_RAW_CLEANUP["projects"]])
            cur.execute("DELETE FROM workspace_members WHERE workspace_id = ANY(%s)", [_RAW_CLEANUP["workspaces"]])
            cur.execute("DELETE FROM workspaces WHERE id = ANY(%s)", [_RAW_CLEANUP["workspaces"]])
            cur.execute("DELETE FROM users WHERE id = ANY(%s)", [_RAW_CLEANUP["users"]])
        conn.commit()
    finally:
        conn.close()
    for ids in _RAW_CLEANUP.values():
        ids.clear()


class TestNowaitConflict:
    def test_second_writer_gets_409(self, db):
        """psycopg 第二连接持锁 → 端点 nowait 即刻 409（BR-04）；ROLLBACK 释放后可写。

        夹具经 raw psycopg **真实提交**（pytest 事务内未提交行对第二连接不可见，
        已实测锚定）；清理推迟到模块 teardown（见 _raw_fixture_cleanup 注释）。
        """
        conn = psycopg.connect(PG_URL)
        try:
            with conn.cursor() as cur:
                suffix = uuid.uuid4().hex[:8]
                owner = _raw_insert(cur, "users",
                                    email=f"nw-{suffix}@rabbit.dev", display_name="并发")
                ws = _raw_insert(cur, "workspaces",
                                 name="NW", slug=f"w-nw-{suffix}", owner_id=owner)
                _raw_insert(cur, "workspace_members",
                            workspace_id=ws, member_id=owner, role=20, is_active=True)
                proj = _raw_insert(cur, "projects",
                                   name="NWP", identifier=suffix[:8].upper(),
                                   workspace_id=ws, status="active")
                _raw_insert(cur, "project_members", workspace_id=ws, project_id=proj,
                            member_id=owner, role=30, is_active=True)
                issue_ids = [
                    _raw_insert(cur, "issues", project_id=proj, name=f"NW-{i}",
                                sequence_id=i + 1, sort_order=(i + 1) * 100.0,
                                priority="none", created_by_id=owner)
                    for i in range(3)
                ]
                _RAW_CLEANUP["issues"].extend(issue_ids)
                _RAW_CLEANUP["projects"].append(proj)
                _RAW_CLEANUP["workspaces"].append(ws)
                _RAW_CLEANUP["users"].append(owner)
            conn.commit()  # 夹具真实提交（第二连接可见）

            slug = Workspace.objects.get(pk=ws).slug  # default 连接可读 committed 行
            user = User.objects.get(pk=owner)
            client = APIClient()
            client.force_authenticate(user=user)
            url = f"/api/v1/workspaces/{slug}/projects/{proj}/issues/bulk/"

            with conn.cursor() as cur:  # 第二连接 BEGIN + FOR UPDATE NOWAIT 锁住目标行
                cur.execute("BEGIN")
                cur.execute(
                    "SELECT id FROM issues WHERE id = ANY(%s) FOR UPDATE NOWAIT",
                    [[str(i) for i in issue_ids]])
                assert len(cur.fetchall()) == 3  # committed 行可锁（与 pytest 事务行相反）

                resp = client.patch(url, {
                    "issue_ids": [str(i) for i in issue_ids],
                    "patch": {"priority": "high"},
                }, format="json")
                assert resp.status_code == 409  # BE-13：nowait 即刻 409，不排队
                err = resp.json()["error"]
                assert err["code"] == "RESOURCE_CONFLICT"
                assert err["details"][0]["field"] == "issue_ids"
                # 锁持有期间零写入
                assert Issue.objects.filter(
                    pk__in=issue_ids, priority="none").count() == 3
                cur.execute("ROLLBACK")  # 释放

            resp2 = client.patch(url, {  # 释放后同一批可写
                "issue_ids": [str(i) for i in issue_ids],
                "patch": {"priority": "high"},
            }, format="json")
            assert resp2.status_code == 200
            assert Issue.objects.filter(pk__in=issue_ids, priority="high").count() == 3
        finally:
            try:
                with conn.cursor() as cur:
                    cur.execute("ROLLBACK")  # 兜底：释放本连接可能持有的锁
            except Exception:  # noqa: BLE001 —— 连接已坏时忽略
                pass
            conn.close()


# ─────────────────────────────────────────────────────────────────────
# ADR-0017：上游签名参数化锚定（缺省行为逐字节不变）
# ─────────────────────────────────────────────────────────────────────
class TestUpstreamEpochParams:
    def test_delete_subtree_default_unchanged(self, env, monkeypatch):
        import plane.bgtasks.issue_hierarchy as ih

        calls: list[tuple] = []
        monkeypatch.setattr(ih.record_delete, "delay",
                            lambda *a, **k: calls.append(a))
        root = env["mk"]("R")
        env["mk"]("C", parent=root)
        with _DjTestCase.captureOnCommitCallbacks(execute=True):
            out = delete_subtree(root.id, env["owner"].id)
        assert out["deleted_count"] == 2 and len(out["descendant_ids"]) == 1
        assert len(calls) == 1  # 缺省：内建单条投递保留（单条端点零改动）
        assert calls[0][0] == str(root.id) and calls[0][2] == 2
        assert isinstance(calls[0][3], float)  # 自生成 epoch（毫秒）

    def test_delete_subtree_shared_epoch_suppressed(self, env, monkeypatch):
        import plane.bgtasks.issue_hierarchy as ih

        calls: list[tuple] = []
        monkeypatch.setattr(ih.record_delete, "delay",
                            lambda *a, **k: calls.append(a))
        root = env["mk"]("R")
        shared = 1756727890123.0
        with _DjTestCase.captureOnCommitCallbacks(execute=True):
            out = delete_subtree(root.id, env["owner"].id,
                                 epoch=shared, suppress_activity=True)
        assert out["deleted_count"] == 1 and calls == []  # 投递职责上移调用方

    def test_epoch_without_suppress_is_programming_error(self, env):
        root = env["mk"]("R")
        with pytest.raises(AssertionError):  # ADR-0017：防「共享 epoch 但双份投递」
            delete_subtree(root.id, env["owner"].id, epoch=123.0)
        with pytest.raises(AssertionError):
            archive_subtree(issue_id=root.id, actor_id=env["owner"].id, epoch=123.0)

    def test_archive_subtree_default_unchanged(self, env, monkeypatch):
        import plane.bgtasks.issue_hierarchy as ih

        calls: list[tuple] = []
        monkeypatch.setattr(ih.record_archive, "delay",
                            lambda *a, **k: calls.append(a))
        root = env["mk"]("R")
        env["mk"]("C", parent=root)
        with _DjTestCase.captureOnCommitCallbacks(execute=True):
            out = archive_subtree(issue_id=root.id, actor_id=env["owner"].id)
        assert out["archived_count"] == 2
        assert len(calls) == 1 and calls[0][2] == 2  # 缺省内建投递保留

    def test_archive_subtree_shared_epoch_suppressed(self, env, monkeypatch):
        import plane.bgtasks.issue_hierarchy as ih

        calls: list[tuple] = []
        monkeypatch.setattr(ih.record_archive, "delay",
                            lambda *a, **k: calls.append(a))
        root = env["mk"]("R")
        with _DjTestCase.captureOnCommitCallbacks(execute=True):
            out = archive_subtree(issue_id=root.id, actor_id=env["owner"].id,
                                  epoch=42.0, suppress_activity=True)
        assert out["archived_count"] == 1 and calls == []


# ─────────────────────────────────────────────────────────────────────
# UT-10 / BE-10：batch 载荷单次投递 + Worker 逐行落库（同 epoch）
# ─────────────────────────────────────────────────────────────────────
class TestWorkerBatch:
    def test_single_delivery_shared_epoch(self, env, monkeypatch):
        import plane.bgtasks.issue_activity as act

        dispatches: list[dict] = []
        monkeypatch.setattr(act, "enqueue_activity_rows",
                            lambda *, rows, comment="": dispatches.append({"rows": rows, "comment": comment}))
        a, b = env["mk"]("A"), env["mk"]("B")
        with _DjTestCase.captureOnCommitCallbacks(execute=True):
            from plane.db.services.issue_bulk import bulk_update

            out = bulk_update(
                project=env["proj"], actor=env["owner"], issue_ids=[a.id, b.id],
                patch={"priority": "high"}, comment="提级",
                is_admin=True)
        assert len(dispatches) == 1  # BR-05：单次投递（1 次 delay，非 N 次）
        rows = dispatches[0]["rows"]
        assert len(rows) == 2 and {r["issue_id"] for r in rows} == {str(a.id), str(b.id)}
        assert all(r["epoch"] == out["data"]["epoch"] for r in rows)  # UT-10：同 epoch
        assert dispatches[0]["comment"].startswith("batch:")
        assert all(r["comment"].startswith("batch:") for r in rows)  # 首行批量摘要

    def test_record_activity_batch_rows_and_idempotent(self, env):
        from plane.bgtasks.issue_activity import record_activity_batch

        a, b = env["mk"]("A"), env["mk"]("B")
        epoch = 1756727520000.0
        rows = [
            {"issue_id": str(a.id), "actor_id": str(env["owner"].id), "verb": "updated",
             "epoch": epoch, "field": "priority", "old_value": "none", "new_value": "high",
             "comment": ""},
            {"issue_id": str(b.id), "actor_id": str(env["owner"].id), "verb": "updated",
             "epoch": epoch, "field": "priority", "old_value": "none", "new_value": "high",
             "comment": ""},
        ]
        record_activity_batch({"batch": rows, "comment": "batch: 批量更新 2 个任务"})
        stored = list(IssueActivity.objects.filter(issue_id__in=[a.id, b.id], epoch=epoch))
        assert len(stored) == 2
        assert all(r.comment == "batch: 批量更新 2 个任务" for r in stored)  # 顶层 comment 填充
        record_activity_batch({"batch": rows, "comment": "batch: 批量更新 2 个任务"})  # 重放
        assert IssueActivity.objects.filter(
            issue_id__in=[a.id, b.id], epoch=epoch).count() == 2  # 行级幂等

    def test_enqueue_activity_rows_fallback_sync(self, env, monkeypatch):
        import plane.bgtasks.issue_activity as act

        def boom(payload):
            raise RuntimeError("broker down")

        monkeypatch.setattr(act.record_activity_batch, "delay", boom)
        a = env["mk"]("A")
        epoch = time.time() * 1000
        act.enqueue_activity_rows(
            rows=[{"issue_id": str(a.id), "actor_id": str(env["owner"].id), "verb": "updated",
                   "epoch": epoch, "field": "priority", "old_value": "none",
                   "new_value": "high", "comment": ""}],
            comment="batch: 降级")
        assert IssueActivity.objects.filter(
            issue_id=a.id, epoch=epoch, comment="batch: 降级").exists()
