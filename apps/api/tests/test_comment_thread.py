"""COLLAB-002 楼中楼 / 表情 / 图片评论测试（T3-05，§5 用例表）。

UT-01~19 + IT-02（一次取齐查询数）+ expand 名单 + 权限矩阵。
夹具风格对照 tests/test_issue_grouping.py（WorkspaceMember 必建——owner 隐式
ADMIN，成员显式 ProjectMember；APIClient + force_authenticate）。
通知三互斥走 fanout_comment 服务层直测（API 路径的 Celery 投递在无 worker
环境下不确定，服务层是分派规则的唯一实现点）。
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from plane.app.comments.reaction_service import EMOJI_WHITELIST
from plane.db.models import (
    CommentReaction,
    FileAsset,
    Issue,
    IssueComment,
    IssueType,
    Notification,
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
from plane.db.services.notify import fanout_comment

pytestmark = pytest.mark.django_db


# ────────────────────────────────────────────────────────────────
# 夹具
# ────────────────────────────────────────────────────────────────
@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="t3-owner@rabbit.dev", password="Rabbit123!",
                                     display_name="王五")
    member = User.objects.create_user(email="t3-member@rabbit.dev", password="Rabbit123!",
                                      display_name="李四")
    third = User.objects.create_user(email="t3-third@rabbit.dev", password="Rabbit123!",
                                     display_name="张三")
    viewer = User.objects.create_user(email="t3-viewer@rabbit.dev", password="Rabbit123!",
                                      display_name="只读")
    ws = Workspace.objects.create(name="W", slug=f"w-t3-{owner.id.hex[:8]}",
                                  owner=owner, created_by=owner)
    for u, ws_role in ((owner, WorkspaceRole.OWNER), (member, WorkspaceRole.MEMBER),
                       (third, WorkspaceRole.MEMBER), (viewer, WorkspaceRole.MEMBER)):
        WorkspaceMember.objects.create(workspace=ws, member=u, role=ws_role, created_by=owner)
    proj = Project.objects.create(name="P", identifier="T3", workspace=ws, created_by=owner)
    ProjectMember.objects.create(project=proj, member=member, role=ProjectRole.COMMENTER,
                                 created_by=owner)
    ProjectMember.objects.create(project=proj, member=third, role=ProjectRole.CONTRIBUTOR,
                                 created_by=owner)
    ProjectMember.objects.create(project=proj, member=viewer, role=ProjectRole.VIEWER,
                                 created_by=owner)
    seed_project_states(proj)
    IssueType.objects.create(workspace=ws, name="需求", is_default=True, created_by=owner)
    todo = State.objects.get(project=proj, group=State.Group.UNSTARTED)

    seq = iter(range(1, 100))

    def mk_issue(name):
        return Issue.objects.create(
            name=name, project=proj, state=todo, priority="none",
            sequence_id=next(seq), sort_order=next(seq) * 100, created_by=owner,
        )

    issue = mk_issue("主任务")
    other_issue = mk_issue("他任务")

    return {
        "owner": owner, "member": member, "third": third, "viewer": viewer,
        "ws": ws, "proj": proj, "issue": issue, "other_issue": other_issue,
    }


class _Client:
    def __init__(self, user):
        self.c = APIClient()
        self.c.force_authenticate(user=user)

    def get(self, path):
        return self.c.get(path, format="json")

    def post(self, path, body=None):
        return self.c.post(path, body or {}, format="json")

    def patch(self, path, body=None):
        return self.c.patch(path, body or {}, format="json")

    def delete(self, path, body=None):
        return self.c.delete(path, body or {}, format="json")


def _comments_url(env, issue=None):
    issue = issue or env["issue"]
    return (f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}"
            f"/issues/{issue.id}/comments/")


def _asset_src(env, asset_id, *, variant=True, issue=None):
    issue = issue or env["issue"]
    base = (f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}"
            f"/issues/{issue.id}/attachments/{asset_id}/download/")
    return f"{base}?variant=thumb" if variant else base


def _mk_comment(env, actor, html, *, parent=None, t=None, issue=None):
    """直建评论（绕过 API）；t 控制时间序（auto_now_add 覆盖入参，创建后 update）。"""
    issue = issue or env["issue"]
    c = IssueComment.objects.create(
        issue=issue, actor=actor, parent=parent,
        comment_html=html, comment_json={}, accessory={},
        created_by=actor, updated_by=actor,
    )
    if t is not None:
        IssueComment.objects.filter(pk=c.pk).update(
            created_at=timezone.now() - timedelta(minutes=t))
        c.refresh_from_db()
    return c


def _mk_image_asset(env, *, actor=None, issue=None, status=FileAsset.Status.UPLOADED,
                    entity_type=FileAsset.EntityType.COMMENT_IMAGE):
    """直建评论图域 asset（域校验是纯 DB 判定，不依赖对象存储）。"""
    issue = issue or env["issue"]
    return FileAsset.objects.create(
        workspace=env["ws"], project=env["proj"],
        entity_type=entity_type, entity_id=issue.id,
        attributes={"name": "shot.png", "size": 1024, "mime": "image/png", "ext": ".png"},
        size=1024, storage_path=f"test/{issue.id}/{issue.id.hex}.png",
        status=status, is_uploaded=(status == FileAsset.Status.UPLOADED),
        uploaded_by=actor or env["owner"], created_by=actor or env["owner"],
    )


def _mention(user) -> str:
    return (f'<span data-mention-id="{user.id}" class="mention">'
            f"@{user.display_name}</span>")


def _post_comment(env, user, html, parent_id=None, extra=None):
    body = {"comment_html": html}
    if parent_id is not None:
        body["parent_id"] = str(parent_id)
    if extra:
        body.update(extra)
    return _Client(user).post(_comments_url(env), body)


# ────────────────────────────────────────────────────────────────
# 楼中楼：归并 / 校验 / 上限
# ────────────────────────────────────────────────────────────────
class TestThreadReply:
    def test_reply_merges_to_root(self, env):
        """UT-01：回复目标自身是回复 → parent = 顶层根（BR-03）。"""
        top = _mk_comment(env, env["owner"], "<p>顶层</p>", t=10)
        r1 = _post_comment(env, env["member"], "<p>第一条回复</p>", parent_id=top.id)
        assert r1.status_code == 201
        reply1 = IssueComment.objects.get(id=r1.json()["data"]["id"])
        assert str(reply1.parent_id) == str(top.id)
        # 回复的回复 → 归并挂顶层，且响应回传 root_id / reply_to_actor
        r2 = _post_comment(env, env["third"], "<p>再回复</p>", parent_id=reply1.id)
        assert r2.status_code == 201
        body = r2.json()["data"]
        assert body["parent_id"] == str(top.id)
        assert body["root_id"] == str(top.id)
        assert body["reply_to_actor"]["id"] == str(env["member"].id)
        assert IssueComment.objects.get(id=body["id"]).parent_id == top.id

    def test_parent_contract_flip(self, env):
        """UT-19 / BR-14：带非空 parent_id 的提交从 P1 的 400 翻转为 201。"""
        top = _mk_comment(env, env["owner"], "<p>顶层</p>")
        resp = _post_comment(env, env["member"], "<p>回复</p>", parent_id=top.id)
        assert resp.status_code == 201
        assert resp.json()["data"]["parent_id"] == str(top.id)

    def test_cross_issue_parent_rejected(self, env):
        """UT-02 / BR-02：跨任务父 → 400 DOES_NOT_EXIST。"""
        foreign = _mk_comment(env, env["owner"], "<p>他任务评论</p>", issue=env["other_issue"])
        resp = _post_comment(env, env["member"], "<p>回复</p>", parent_id=foreign.id)
        assert resp.status_code == 400
        err = resp.json()["error"]
        assert err["code"] == "VALIDATION_ERROR"
        assert err["details"][0] == {"field": "parent_id", "code": "DOES_NOT_EXIST",
                                     "message": "回复目标不存在或已删除"}

    def test_deleted_parent_rejected(self, env):
        """UT-03：已软删父 → 400（占位行无回复按钮）。"""
        top = _mk_comment(env, env["owner"], "<p>顶层</p>")
        top.deleted_at = timezone.now()
        top.save(update_fields=["deleted_at"])
        resp = _post_comment(env, env["member"], "<p>回复</p>", parent_id=top.id)
        assert resp.status_code == 400
        assert resp.json()["error"]["details"][0]["code"] == "DOES_NOT_EXIST"

    def test_reply_limit_100(self, env):
        """UT-04/UT-05：第 100 条合法，第 101 条 409 RESOURCE_LIMIT_EXCEEDED + LIMIT。"""
        top = _mk_comment(env, env["owner"], "<p>顶层</p>", t=120)
        for i in range(100):
            _mk_comment(env, env["member"], f"<p>回复 {i}</p>", parent=top, t=100 - i)
        assert _post_comment(env, env["third"], "<p>第 101 条</p>",
                             parent_id=top.id).status_code == 409
        # 造出空位（软删一条）后可再回复 —— 上限只数存活回复
        IssueComment.objects.filter(parent=top).first().delete()
        resp = _post_comment(env, env["third"], "<p>补位回复</p>", parent_id=top.id)
        assert resp.status_code == 201
        # 再次超限
        assert _post_comment(env, env["third"], "<p>又超了</p>",
                             parent_id=top.id).status_code == 409

    def test_reply_limit_error_shape(self, env):
        top = _mk_comment(env, env["owner"], "<p>顶层</p>", t=120)
        for i in range(100):
            _mk_comment(env, env["member"], f"<p>r{i}</p>", parent=top, t=100 - i)
        resp = _post_comment(env, env["third"], "<p>第 101 条</p>", parent_id=top.id)
        err = resp.json()["error"]
        assert err["code"] == "RESOURCE_LIMIT_EXCEEDED"
        assert err["details"][0]["field"] == "parent_id"
        assert err["details"][0]["code"] == "LIMIT"


# ────────────────────────────────────────────────────────────────
# 两层结构列表
# ────────────────────────────────────────────────────────────────
class TestTwoLevelList:
    def _build_thread(self, env):
        top1 = _mk_comment(env, env["owner"], "<p>顶层一</p>", t=30)
        _mk_comment(env, env["member"], f"<p>{_mention(env['owner'])} 回复一</p>",
                    parent=top1, t=20)
        _mk_comment(env, env["third"], "<p>回复二</p>", parent=top1, t=10)
        top2 = _mk_comment(env, env["member"], "<p>顶层二</p>", t=5)
        _mk_comment(env, env["owner"], "<p>顶层二回复</p>", parent=top2, t=1)
        # 孤立回复挂他任务线程，验证隔离
        _mk_comment(env, env["member"], "<p>他任务顶层</p>", t=8, issue=env["other_issue"])
        return top1, top2

    def test_two_level_assembly(self, env):
        """UT-06：顶层正序 + replies 正序 + reply_count = replies.length。"""
        top1, top2 = self._build_thread(env)
        resp = _Client(env["owner"]).get(_comments_url(env))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2
        assert data[0]["id"] == str(top1.id)
        assert data[0]["reply_count"] == 2 == len(data[0]["replies"])
        assert [r["actor"]["display_name"] for r in data[0]["replies"]] == ["李四", "张三"]
        assert all(r["parent_id"] == str(top1.id) for r in data[0]["replies"])
        assert data[1]["id"] == str(top2.id)
        assert data[1]["reply_count"] == 1
        meta = resp.json()["meta"]
        assert meta["total_count"] == 2 and meta["count"] == 2
        assert meta["per_page"] == 30 and meta["next_cursor"] is None

    def test_constant_query_count(self, env, django_assert_num_queries):
        """IT-02：查询数不随评论规模增长（一次取齐，常数级）。"""
        for i in range(8):
            top = _mk_comment(env, env["owner"], f"<p>顶层 {i}</p>", t=100 - i)
            for j in range(3):
                r = _mk_comment(env, env["member"], f"<p>r{i}-{j}</p>", parent=top, t=90 - j)
                CommentReaction.objects.create(
                    comment=r, actor=env["third"], emoji="👍",
                    created_by=env["third"], updated_by=env["third"])
        client = _Client(env["owner"])
        url = _comments_url(env)
        client.get(url)  # 预热
        # 9 查询：工作空间×1 + WS 成员×1 + 项目×1 + 项目成员×1 + 任务×1 + 顶层计数×1
        # + 顶层页×1 + 回复批量×1 + 表情聚合×1（被回复人闭包在本用例无 reply_to
        # 数据，`__in=[]` 被 Django 短路）——与评论规模无关（常数级）
        with django_assert_num_queries(9):
            resp = client.get(url)
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 8
        for top in resp.json()["data"]:
            assert len(top["replies"]) == 3
            assert all(r["reactions"][0]["emoji"] == "👍" for r in top["replies"])

    def test_top_level_cursor_pagination(self, env):
        """游标按顶层分页：35 顶层 → 第 1 页 30 条 + next_cursor，第 2 页 5 条。"""
        for i in range(35):
            _mk_comment(env, env["owner"], f"<p>t{i}</p>", t=200 - i)
        client = _Client(env["owner"])
        page1 = client.get(_comments_url(env)).json()
        assert page1["meta"]["count"] == 30 and page1["meta"]["total_count"] == 35
        assert page1["meta"]["next_cursor"] is not None
        assert page1["meta"]["next_page_results"] is True
        page2 = client.get(f"{_comments_url(env)}?cursor={page1['meta']['next_cursor']}").json()
        assert page2["meta"]["count"] == 5
        assert page2["data"][0]["comment_html"] == "<p>t30</p>"
        assert page1["data"][29]["comment_html"] == "<p>t29</p>"
        # 游标非法 → 400 VALIDATION_INVALID_CURSOR
        bad = client.get(f"{_comments_url(env)}?cursor=%%%")
        assert bad.status_code == 400
        assert bad.json()["error"]["code"] == "VALIDATION_INVALID_CURSOR"

    def test_deleted_parent_placeholder_keeps_replies(self, env):
        """UT-07 / BR-06：父删子留——父占位行 + replies 保留渲染。"""
        top = _mk_comment(env, env["owner"], "<p>顶层</p>", t=30)
        r1 = _mk_comment(env, env["member"], "<p>回复一</p>", parent=top, t=20)
        _mk_comment(env, env["third"], "<p>回复二</p>", parent=top, t=10)
        top.deleted_at = timezone.now()
        top.save(update_fields=["deleted_at"])
        data = _Client(env["owner"]).get(_comments_url(env)).json()["data"]
        assert len(data) == 1
        row = data[0]
        assert row["is_deleted"] is True
        assert row["comment_html"] == ""               # 软删后正文不再可见
        assert len(row["replies"]) == 2
        assert row["replies"][0]["id"] == str(r1.id)
        assert row["reply_count"] == 2
        # 无回复的软删顶层维持 COLLAB-001 消失语义
        lone = _mk_comment(env, env["member"], "<p>孤评论</p>", t=5)
        lone.deleted_at = timezone.now()
        lone.save(update_fields=["deleted_at"])
        data = _Client(env["owner"]).get(_comments_url(env)).json()["data"]
        assert len(data) == 1


# ────────────────────────────────────────────────────────────────
# 表情 Reaction
# ────────────────────────────────────────────────────────────────
class TestReactions:
    def _url(self, env, comment):
        return f"{_comments_url(env)}{comment.id}/reactions/"

    def test_whitelist_rejects_custom_emoji(self, env):
        """UT-08 / BR-09：白名单外 → 400 NOT_A_CHOICE。"""
        c = _mk_comment(env, env["owner"], "<p>c</p>")
        resp = _Client(env["member"]).post(self._url(env, c), {"emoji": "🦄"})
        assert resp.status_code == 400
        err = resp.json()["error"]
        assert err["code"] == "VALIDATION_ERROR"
        assert err["details"][0] == {"field": "emoji", "code": "NOT_A_CHOICE",
                                     "message": "不支持的表情，请从选择器中选择"}
        assert len(EMOJI_WHITELIST) == 24

    def test_toggle_on_idempotent(self, env):
        """UT-09 / BR-10：重复 POST 同 emoji → changed=false，count 不变。"""
        c = _mk_comment(env, env["owner"], "<p>c</p>")
        client = _Client(env["member"])
        r1 = client.post(self._url(env, c), {"emoji": "🎉"}).json()["data"]
        assert r1 == {"emoji": "🎉", "count": 1, "reacted_by_me": True, "changed": True}
        r2 = client.post(self._url(env, c), {"emoji": "🎉"}).json()["data"]
        assert r2["changed"] is False and r2["count"] == 1 and r2["reacted_by_me"] is True

    def test_two_users_same_emoji_and_unique_guard(self, env):
        """UT-10：两人同点 → count=2；唯一约束兜底重复活跃行。"""
        c = _mk_comment(env, env["owner"], "<p>c</p>")
        _Client(env["member"]).post(self._url(env, c), {"emoji": "👍"})
        r = _Client(env["third"]).post(self._url(env, c), {"emoji": "👍"}).json()["data"]
        assert r["count"] == 2
        with pytest.raises(IntegrityError), transaction.atomic():
            CommentReaction.objects.create(
                comment=c, actor=env["member"], emoji="👍",
                created_by=env["member"], updated_by=env["member"])

    def test_soft_deleted_row_revives_or_recreates(self, env):
        """BR-10 / §4.3.2：撤销后软删行留存，重按 get_or_create 复活。"""
        c = _mk_comment(env, env["owner"], "<p>c</p>")
        client = _Client(env["member"])
        client.post(self._url(env, c), {"emoji": "👍"})
        client.delete(self._url(env, c), {"emoji": "👍"})
        assert CommentReaction.objects.filter(comment=c).count() == 0
        # 软删行留存（作用域化：共享 dev PG 会被主栈 e2e 直写污染）
        assert CommentReaction.all_objects.filter(comment=c).count() == 1
        again = client.post(self._url(env, c), {"emoji": "👍"}).json()["data"]
        assert again["changed"] is True and again["count"] == 1
        assert CommentReaction.objects.filter(comment=c).count() == 1

    def test_toggle_off_missing_is_idempotent_200(self, env):
        """UT-11：DELETE 未点过的 → 200 changed=false。"""
        c = _mk_comment(env, env["owner"], "<p>c</p>")
        resp = _Client(env["member"]).delete(self._url(env, c), {"emoji": "🎉"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["changed"] is False and data["count"] == 0
        assert data["reacted_by_me"] is False

    def test_toggle_off_then_list_aggregate(self, env):
        c = _mk_comment(env, env["owner"], "<p>c</p>")
        client = _Client(env["member"])
        client.post(self._url(env, c), {"emoji": "🎉"})
        off = client.delete(self._url(env, c), {"emoji": "🎉"}).json()["data"]
        assert off["changed"] is True and off["count"] == 0 and off["reacted_by_me"] is False
        reactions = _Client(env["owner"]).get(_comments_url(env)).json()["data"][0]["reactions"]
        assert reactions == []

    def test_multiple_emojis_per_user(self, env):
        """UT-12：一人多 emoji（👍 + 🎉）两行并存，聚合两组。"""
        c = _mk_comment(env, env["owner"], "<p>c</p>")
        client = _Client(env["member"])
        client.post(self._url(env, c), {"emoji": "👍"})
        client.post(self._url(env, c), {"emoji": "🎉"})
        reactions = _Client(env["member"]).get(_comments_url(env)).json()["data"][0]["reactions"]
        assert {r["emoji"] for r in reactions} == {"👍", "🎉"}
        assert all(r["count"] == 1 and r["reacted_by_me"] for r in reactions)

    def test_reaction_no_notification_no_activity(self, env):
        """BR-09：点表情不产通知、不写 IssueActivity（断言按任务域收窄——
        dev PG 为共享库，全局计数含既有数据）。"""
        from plane.db.models import IssueActivity
        c = _mk_comment(env, env["owner"], "<p>c</p>")
        _Client(env["member"]).post(self._url(env, c), {"emoji": "👍"})
        assert Notification.objects.filter(
            data__issue_id=str(env["issue"].id)).count() == 0
        assert IssueActivity.objects.filter(issue=env["issue"]).count() == 0

    def test_viewer_cannot_react(self, env):
        """权限：comment.create = COMMENTER+，VIEWER 403。"""
        c = _mk_comment(env, env["owner"], "<p>c</p>")
        resp = _Client(env["viewer"]).post(self._url(env, c), {"emoji": "👍"})
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "PERM_ROLE_INSUFFICIENT"

    def test_archived_project_reactions_readonly(self, env):
        """IT-08：归档项目点表情 403 PERM_PROJECT_ARCHIVED。"""
        c = _mk_comment(env, env["owner"], "<p>c</p>")
        env["proj"].status = "archived"
        env["proj"].save(update_fields=["status"])
        resp = _Client(env["member"]).post(self._url(env, c), {"emoji": "👍"})
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "PERM_PROJECT_ARCHIVED"

    def test_reactions_on_deleted_comment_404(self, env):
        c = _mk_comment(env, env["owner"], "<p>c</p>")
        c.deleted_at = timezone.now()
        c.save(update_fields=["deleted_at"])
        resp = _Client(env["member"]).post(self._url(env, c), {"emoji": "👍"})
        assert resp.status_code == 404

    def test_expand_reactions_user_ids(self, env):
        """IT-04：?expand=reactions 追加 user_ids；默认不含。"""
        c = _mk_comment(env, env["owner"], "<p>c</p>")
        _Client(env["member"]).post(self._url(env, c), {"emoji": "👍"})
        _Client(env["third"]).post(self._url(env, c), {"emoji": "👍"})
        _Client(env["third"]).post(self._url(env, c), {"emoji": "😂"})
        base = _Client(env["owner"]).get(_comments_url(env)).json()["data"][0]["reactions"]
        assert all("user_ids" not in r for r in base)
        expanded = _Client(env["owner"]).get(
            f"{_comments_url(env)}?expand=reactions").json()["data"][0]["reactions"]
        by_emoji = {r["emoji"]: r for r in expanded}
        assert set(by_emoji["👍"]["user_ids"]) == {str(env["member"].id), str(env["third"].id)}
        assert by_emoji["👍"]["count"] == 2
        assert by_emoji["😂"]["user_ids"] == [str(env["third"].id)]

    def test_reply_reactions_aggregated_inline(self, env):
        """IT-03：replies[].reactions 内联聚合 + reacted_by_me 按请求者。"""
        top = _mk_comment(env, env["owner"], "<p>顶层</p>", t=30)
        r1 = _mk_comment(env, env["member"], "<p>回复</p>", parent=top, t=20)
        _Client(env["third"]).post(f"{_comments_url(env)}{r1.id}/reactions/", {"emoji": "😂"})
        data = _Client(env["third"]).get(_comments_url(env)).json()["data"][0]
        assert data["replies"][0]["reactions"] == [
            {"emoji": "😂", "count": 1, "reacted_by_me": True}]
        data_owner = _Client(env["owner"]).get(_comments_url(env)).json()["data"][0]
        assert data_owner["replies"][0]["reactions"][0]["reacted_by_me"] is False


# ────────────────────────────────────────────────────────────────
# 图片评论（净化 / 域校验 / accessory 聚合）
# ────────────────────────────────────────────────────────────────
class TestImageComments:
    def test_valid_images_sanitized_and_aggregated(self, env):
        """UT-18：images 服务端聚合进 accessory，客户端直传 accessory 被忽略。"""
        a1 = _mk_image_asset(env, actor=env["member"])
        a2 = _mk_image_asset(env, actor=env["member"])
        html = (f"<p>两张图：</p>"
                f'<img src="{_asset_src(env, a1.id)}" alt="a.png">'
                f'<img src="{_asset_src(env, a2.id, variant=False)}" alt="b.png">')
        resp = _post_comment(env, env["member"], html,
                             extra={"accessory": {"images": ["spoof"], "hack": True}})
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["images"] == [str(a1.id), str(a2.id)]
        cid = data["id"]
        stored = IssueComment.objects.get(id=cid)
        assert stored.accessory["images"] == [str(a1.id), str(a2.id)]
        assert stored.accessory.get("hack") is None       # 客户端 accessory 全忽略

    def test_cross_issue_asset_replaced_with_placeholder(self, env):
        """UT-13 / BR-07：引用他任务 asset（src 路径指向他任务）→ 「图片不可用」占位，不 500。"""
        foreign = _mk_image_asset(env, actor=env["member"], issue=env["other_issue"])
        html = (f'<p>x</p>'
                f'<img src="{_asset_src(env, foreign.id, issue=env["other_issue"])}" alt="x.png">')
        resp = _post_comment(env, env["member"], html)
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["images"] == []
        assert "图片不可用" in data["comment_html"]
        assert "<img" not in data["comment_html"]

    def test_foreign_uploader_asset_replaced(self, env):
        """BR-07：uploaded_by 非当前用户（他人上传）→ 占位。"""
        asset = _mk_image_asset(env, actor=env["owner"])     # owner 上传
        html = f'<img src="{_asset_src(env, asset.id)}" alt="x.png">'
        resp = _post_comment(env, env["member"], html)        # member 发评论
        assert resp.status_code == 201
        assert "图片不可用" in resp.json()["data"]["comment_html"]

    def test_not_uploaded_asset_replaced(self, env):
        asset = _mk_image_asset(env, actor=env["member"], status=FileAsset.Status.UPLOADING)
        html = f'<img src="{_asset_src(env, asset.id)}" alt="x.png">'
        resp = _post_comment(env, env["member"], html)
        assert "图片不可用" in resp.json()["data"]["comment_html"]

    def test_issue_attachment_asset_replaced(self, env):
        """BR-07：entity_type=issue 的附件不在评论图域 → 占位。"""
        asset = _mk_image_asset(env, actor=env["member"],
                                entity_type=FileAsset.EntityType.ISSUE)
        html = f'<img src="{_asset_src(env, asset.id)}" alt="x.png">'
        resp = _post_comment(env, env["member"], html)
        assert "图片不可用" in resp.json()["data"]["comment_html"]

    def test_external_image_stripped_to_link(self, env):
        """UT-14 / BR-15：外链 http(s) src 剥离为链接文本。"""
        html = '<p>看这个</p><img src="http://evil.example/x.png" alt="e">'
        resp = _post_comment(env, env["member"], html)
        assert resp.status_code == 201
        out = resp.json()["data"]["comment_html"]
        assert "<img" not in out
        assert '<a href="http://evil.example/x.png">http://evil.example/x.png</a>' in out

    def test_data_uri_and_garbage_src_replaced(self, env):
        html = ('<p>x</p><img src="data:image/png;base64,AAAA" alt="d">'
                '<img src="/not/allowed/x.png" alt="n">')
        resp = _post_comment(env, env["member"], html)
        out = resp.json()["data"]["comment_html"]
        assert "<img" not in out
        assert out.count("图片不可用") == 2

    def test_tenth_image_rejected(self, env):
        """UT-15 / BR-08：第 10 张 → 409 RESOURCE_LIMIT_EXCEEDED + LIMIT。"""
        assets = [_mk_image_asset(env, actor=env["member"]) for _ in range(10)]
        html = "<p>十张</p>" + "".join(
            f'<img src="{_asset_src(env, a.id)}" alt="{i}.png">'
            for i, a in enumerate(assets))
        resp = _post_comment(env, env["member"], html)
        assert resp.status_code == 409
        err = resp.json()["error"]
        assert err["code"] == "RESOURCE_LIMIT_EXCEEDED"
        assert err["details"][0]["field"] == "comment_html"
        assert err["details"][0]["code"] == "LIMIT"
        # 9 张合法
        html9 = "<p>九张</p>" + "".join(
            f'<img src="{_asset_src(env, a.id)}" alt="{i}.png">'
            for i, a in enumerate(assets[:9]))
        assert _post_comment(env, env["member"], html9).status_code == 201

    def test_img_alt_attr_injection_escaped(self, env):
        """安全：alt 属性值转义，防属性注入（onerror 载体不成为真属性）。"""
        asset = _mk_image_asset(env, actor=env["member"])
        html = (f"<p>x</p>"
                f'<img src="{_asset_src(env, asset.id)}" alt=\'x" onerror="alert(1)\'>')
        resp = _post_comment(env, env["member"], html)
        assert resp.status_code == 201
        out = resp.json()["data"]["comment_html"]
        assert "<img" in out
        assert out.count("alt=") == 1
        assert 'onerror="' not in out       # 注入值整体落在被转义的 alt 值内

    def test_update_rebuilds_images(self, env):
        """编辑窗口内换图：accessory.images 重建。"""
        a1 = _mk_image_asset(env, actor=env["member"])
        a2 = _mk_image_asset(env, actor=env["member"])
        c = _mk_comment(env, env["member"],
                        f'<img src="{_asset_src(env, a1.id)}" alt="1.png">')
        url = f"{_comments_url(env)}{c.id}/"
        resp = _Client(env["member"]).patch(
            url, {"comment_html": f'<p>换图</p><img src="{_asset_src(env, a2.id)}" alt="2.png">'})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["images"] == [str(a2.id)]
        assert data["is_edited"] is True


# ────────────────────────────────────────────────────────────────
# 通知三互斥（fanout 服务层直测——分派规则唯一实现点）
# ────────────────────────────────────────────────────────────────
class TestNotificationMutex:
    def _fanout(self, env, comment):
        from plane.app.comments.sanitize import extract_mention_ids
        return fanout_comment(
            comment_id=str(comment.id), issue_id=str(env["issue"].id),
            actor=comment.actor, mention_ids=extract_mention_ids(comment.comment_html),
        )

    def test_replied_targets_top_level_author(self, env):
        """UT-17 / BR-12：回复（未 @ 顶层作者）→ 顶层作者收 comment.replied。"""
        from plane.db.models import IssueAssignee
        IssueAssignee.objects.create(issue=env["issue"], assignee=env["third"],
                                     created_by=env["owner"])
        top = _mk_comment(env, env["owner"], "<p>顶层</p>", t=30)
        reply = _mk_comment(env, env["member"], "<p>收到</p>", parent=top, t=20)
        count = self._fanout(env, reply)
        assert count == 2
        owner_notifs = Notification.objects.filter(receiver=env["owner"])
        assert owner_notifs.count() == 1
        n = owner_notifs.get()
        assert n.event == "comment.replied"
        assert n.title == "李四 回复了你在 T3-1 的评论"
        assert n.data["root_id"] == str(top.id)
        assert n.data["comment_id"] == str(reply.id)
        # 指派人（第三人，非作者/操作者）走 issue.commented
        third_notif = Notification.objects.get(receiver=env["third"])
        assert third_notif.event == "issue.commented"

    def test_mention_beats_replied_and_commented(self, env):
        """UT-16 / BR-11：回复且 @ 了顶层作者 → 该作者仅收 mentioned 一条。"""
        top = _mk_comment(env, env["owner"], "<p>顶层</p>", t=30)
        reply = _mk_comment(env, env["member"],
                            f"<p>{_mention(env['owner'])} 看下</p>", parent=top, t=20)
        self._fanout(env, reply)
        events = [n.event for n in Notification.objects.filter(receiver=env["owner"])]
        assert events == ["issue.mentioned"]
        n = Notification.objects.get(receiver=env["owner"])
        assert n.data["root_id"] == str(top.id)

    def test_top_level_comment_no_replied_event(self, env):
        """顶层评论（无 parent）：沿用 COLLAB-001 两事件，顶层作者=操作者不收。"""
        c = _mk_comment(env, env["owner"], f"<p>hi {_mention(env['member'])}</p>", t=5)
        self._fanout(env, c)
        events = list(Notification.objects.filter(receiver=env["member"])
                      .values_list("event", flat=True))
        assert events == ["issue.mentioned"]
        # 作用域化：共享 dev PG 会被真栈 e2e/录屏的 comment.replied 残留污染（CLAUDE.md 坑 #18）
        env_uids = [u.id for u in (env["owner"], env["member"], env["third"], env["viewer"])]
        assert not Notification.objects.filter(receiver_id__in=env_uids, event="comment.replied").exists()

    def test_operator_and_outside_domain_excluded(self, env):
        """BR-12：操作者本人 / 域外成员剔除；重复投递零重复（dedup_key）。
        （计数按任务域收窄——dev PG 为共享库。）"""
        outsider = User.objects.create_user(email="t3-out@rabbit.dev",
                                            password="Rabbit123!", display_name="域外")
        top = _mk_comment(env, env["owner"], "<p>顶层</p>", t=30)
        reply = _mk_comment(env, env["owner"], f"<p>自答 {_mention(outsider)}</p>",
                            parent=top, t=20)
        scoped = Notification.objects.filter(data__issue_id=str(env["issue"].id))
        count = self._fanout(env, reply)
        assert count == 0                          # 作者=操作者；@ 域外被剔
        assert scoped.count() == 0
        # 幂等重投（IT-07 语义）
        assert self._fanout(env, reply) == 0
        assert scoped.count() == 0

    def test_reply_to_reply_notifies_root_author_not_target(self, env):
        """回复的回复：被回复人走 mentioned（BR-03/§1.3 归并语义），
        顶层作者走 comment.replied——各一条、不重复。

        （归并是 Service 写入点行为——这里直建已归并形态 parent=top，
        验证 fanout 对「挂顶层 + @ 被回复人」数据的分派。）"""
        top = _mk_comment(env, env["owner"], "<p>顶层</p>", t=30)
        _mk_comment(env, env["member"], "<p>回复一</p>", parent=top, t=20)
        # 张三 回复 李四的回复（自动 @李四），落库归并挂 top
        r2 = _mk_comment(env, env["third"],
                         f"<p>{_mention(env['member'])} 同意</p>", parent=top, t=10)
        assert r2.parent_id == top.id
        count = self._fanout(env, r2)
        assert count == 2
        member_events = list(Notification.objects.filter(receiver=env["member"])
                             .values_list("event", flat=True))
        assert member_events == ["issue.mentioned"]
        owner_notif = Notification.objects.get(receiver=env["owner"])
        assert owner_notif.event == "comment.replied"
        assert owner_notif.data["root_id"] == str(top.id)


# ────────────────────────────────────────────────────────────────
# FILE-001 侧：comment_image presign 收紧与分派
# ────────────────────────────────────────────────────────────────
class TestCommentImagePresign:
    def _presign(self, env, user, body):
        url = (f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}"
               f"/issues/{env['issue'].id}/attachments/presign/")
        return _Client(user).post(url, body)

    def test_comment_image_size_and_type_tightened(self, env):
        """BR-08：comment_image ≤5MB + png/jpg/jpeg/gif/webp。"""
        assert self._presign(env, env["third"], {
            "file_name": "big.png", "file_size": 6 * 1024 * 1024,
            "content_type": "image/png", "entity_type": "comment_image",
        }).json()["error"]["code"] == "VALIDATION_FILE_SIZE_EXCEEDED"
        assert self._presign(env, env["third"], {
            "file_name": "doc.pdf", "file_size": 1024,
            "content_type": "application/pdf", "entity_type": "comment_image",
        }).json()["error"]["code"] == "VALIDATION_FILE_TYPE_NOT_ALLOWED"

    def test_comment_image_does_not_consume_attachment_quota(self, env):
        """comment_image 不占 20 附件配额：满额后仍可 presign（存储 mock 掉）。"""
        from unittest.mock import patch
        for i in range(20):
            FileAsset.objects.create(
                workspace=env["ws"], project=env["proj"],
                entity_type=FileAsset.EntityType.ISSUE, entity_id=env["issue"].id,
                attributes={"name": f"a{i}.txt"}, size=1,
                storage_path=f"p/{i}", status=FileAsset.Status.UPLOADED,
                is_uploaded=True, uploaded_by=env["third"], created_by=env["third"],
            )
        with patch("plane.storage.minio.presigned_put_url") as mock:
            mock.return_value = "http://minio/uploads/x"
            resp = self._presign(env, env["third"], {
                "file_name": "shot.webp", "file_size": 2048,
                "content_type": "image/webp", "entity_type": "comment_image",
            })
        assert resp.status_code == 201
        asset = FileAsset.objects.get(id=resp.json()["data"]["asset_id"])
        assert asset.entity_type == "comment_image"
        assert asset.entity_id == env["issue"].id
        assert "comment_image" in asset.storage_path
        # 缺省 entity_type 仍占配额：满额 → 409
        resp2 = self._presign(env, env["third"], {
            "file_name": "a.txt", "file_size": 1, "content_type": "text/plain",
        })
        assert resp2.status_code == 409

    def test_download_variant_validation_and_entity_dispatch(self, env):
        """download：?variant 非法 400；comment_image 可达（存储 mock）。"""
        from unittest.mock import patch
        asset = _mk_image_asset(env, actor=env["member"])
        url = (f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}"
               f"/issues/{env['issue'].id}/attachments/{asset.id}/download/")
        bad = _Client(env["member"]).get(f"{url}?variant=huge")
        assert bad.status_code == 400
        assert bad.json()["error"]["code"] == "VALIDATION_INVALID_PARAM"
        # comment_image 原图直取：mock presigned_get_url
        with patch("plane.storage.minio.presigned_get_url") as mock:
            mock.return_value = "http://minio/origkey"
            resp = _Client(env["member"]).get(url)
        assert resp.status_code == 302
        assert resp["Location"].startswith("/uploads/origkey")
        # 缩略：首次生成（GET 原图 + PUT webp）后签发
        import io

        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (960, 480), "red").save(buf, format="PNG")
        with patch("plane.storage.minio.presigned_get_url") as mock_get, \
                patch("plane.storage.minio.get_object_bytes") as mock_bytes, \
                patch("plane.storage.minio.put_object") as mock_put, \
                patch("plane.storage.minio.head_object_size",
                      side_effect=storage_not_found):
            mock_get.return_value = "http://minio/uploads/thumb"
            mock_bytes.return_value = buf.getvalue()
            resp = _Client(env["member"]).get(f"{url}?variant=thumb")
        assert resp.status_code == 302
        assert mock_put.called
        put_key = mock_put.call_args.kwargs["key"]
        assert put_key.endswith(".__thumb.webp")
        assert mock_put.call_args.kwargs["content_type"] == "image/webp"


def storage_not_found(*, bucket, key):
    """head_object_size 的「缩略不存在」桩（触发首取生成路径）。"""
    from plane.storage import minio as storage
    raise storage.StorageObjectNotFound(key)
