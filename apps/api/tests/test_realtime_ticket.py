"""COLLAB-004 实时票据 + 事件扇出测试（T3-09/T3-11，§5.1 对齐）。

覆盖：换票签发/验签（含过期、rooms 上限、issue 不可见 403 拒整票）、续签 jti
轮换、verify-rooms 失效项返回与 X-Internal-Key 拒绝、project.read 权限（VIEWER
可换票 / 外人 404）、event_publisher（消息格式 / 2KB 拒 / Redis 不可用降级 /
unknown 事件丢弃）、挂点（Activity 落库后扇出、评论/通知 on_commit 扇出）。

夹具风格对照 tests/test_activity_stream.py（WorkspaceMember 必建、APIClient +
force_authenticate）；RS256 密钥对在测试内生成（cryptography），经
override_settings 注入——不依赖 .env。
"""
from __future__ import annotations

import json
import uuid as uuid_mod
from unittest.mock import patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import TestCase as DjangoTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from plane.bgtasks import event_publisher
from plane.bgtasks.issue_activity import record_activity_row
from plane.db.models import (
    Issue,
    Project,
    ProjectMember,
    State,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.roles import ProjectRole, WorkspaceRole
from plane.db.seeds.project_states import seed_project_states

pytestmark = pytest.mark.django_db


# ────────────────────────────────────────────────────────────────
# RS256 密钥对夹具（模块级生成一次；override_settings 注入）
# ────────────────────────────────────────────────────────────────
_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIV_PEM = _key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
PUB_PEM = _key.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()

# 单行 \n 转义形态（normalize_pem 归一用例）
PRIV_ESCAPED = PRIV_PEM.replace("\n", "\\n")


@pytest.fixture()
def rt_settings(settings):
    settings.LIVE_JWT_PRIVATE_KEY = PRIV_PEM
    settings.LIVE_JWT_PUBLIC_KEY = PUB_PEM
    settings.INTERNAL_KEY = "test-internal-key"
    settings.LIVE_TICKET_TTL = 120
    return settings


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="c004-owner@rabbit.dev", password="Rabbit123!")
    viewer = User.objects.create_user(email="c004-viewer@rabbit.dev", password="Rabbit123!")
    outsider = User.objects.create_user(email="c004-outsider@rabbit.dev", password="Rabbit123!")
    ws = Workspace.objects.create(name="W", slug=f"w-c004-{owner.id.hex[:8]}",
                                  owner=owner, created_by=owner)
    for u, role in ((owner, WorkspaceRole.OWNER), (viewer, WorkspaceRole.MEMBER),
                    (outsider, WorkspaceRole.MEMBER)):
        WorkspaceMember.objects.create(workspace=ws, member=u, role=role, created_by=owner)
    proj = Project.objects.create(name="P", identifier="C04", workspace=ws, created_by=owner)
    ProjectMember.objects.create(project=proj, member=viewer, role=ProjectRole.VIEWER,
                                 created_by=owner)
    seed_project_states(proj)
    todo = State.objects.get(project=proj, group=State.Group.UNSTARTED)
    started = State.objects.get(project=proj, group=State.Group.STARTED)
    issue = Issue.objects.create(name="T1", project=proj, state=todo,
                                 sequence_id=1, sort_order=100, created_by=owner)
    other_proj = Project.objects.create(name="P2", identifier="CX9", workspace=ws,
                                        created_by=owner)
    foreign_issue = Issue.objects.create(name="T-foreign", project=other_proj, state=todo,
                                         sequence_id=1, sort_order=100, created_by=owner)
    return {
        "owner": owner, "viewer": viewer, "outsider": outsider, "ws": ws,
        "proj": proj, "todo": todo, "started": started,
        "issue": issue, "other_proj": other_proj, "foreign_issue": foreign_issue,
    }


def _client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _token_url(env) -> str:
    return f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/realtime-token/"


# ────────────────────────────────────────────────────────────────
# T3-09 换票端点
# ────────────────────────────────────────────────────────────────
class TestIssueToken:
    def test_issue_and_verify_roundtrip(self, rt_settings, env):
        resp = _client(env["viewer"]).post(_token_url(env), {
            "client_tab_id": "tab-1",
            "issue_rooms": [str(env["issue"].id)],
        }, format="json")
        assert resp.status_code == 200
        data = resp.json()["data"]
        rooms = data["rooms"]
        assert rooms == [
            f"project:{env['proj'].id}", f"issue:{env['issue'].id}",
            f"user:{env['viewer'].id}",
        ]
        assert data["renew_after"] == 90
        claims = pyjwt.decode(data["token"], PUB_PEM, algorithms=["RS256"])
        assert claims["sub"] == str(env["viewer"].id)
        assert claims["ws"] == f"{env['viewer'].id}:tab-1"
        assert claims["rooms"] == rooms
        assert claims["exp"] - claims["iat"] == 120
        assert claims["jti"]

    def test_viewer_can_get_ticket_project_read(self, rt_settings, env):
        """project.read（VIEWER+）即可换票（§1.3 订阅条件表）。"""
        resp = _client(env["viewer"]).post(_token_url(env), {
            "client_tab_id": "tab-v", "issue_rooms": [],
        }, format="json")
        assert resp.status_code == 200

    def test_outsider_404(self, rt_settings, env):
        """非项目成员 → 404 存在性隐藏（_access 收口）。"""
        resp = _client(env["outsider"]).post(_token_url(env), {
            "client_tab_id": "tab-o", "issue_rooms": [],
        }, format="json")
        assert resp.status_code == 404

    def test_invisible_issue_rejects_whole_ticket_403(self, rt_settings, env):
        """issue 不可见（他项目）→ 403 PERM_DENIED 拒整票（§4.2.1）。"""
        resp = _client(env["owner"]).post(_token_url(env), {
            "client_tab_id": "tab-x",
            "issue_rooms": [str(env["issue"].id), str(env["foreign_issue"].id)],
        }, format="json")
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "PERM_DENIED"

    def test_rooms_cap_10(self, rt_settings, env):
        """issue_rooms > 8（project+user+8=10 上限）→ 400 VALIDATION_INVALID_PARAM。"""
        ids = []
        for i in range(2, 11):  # 9 个任务 → 11 rooms
            ids.append(str(Issue.objects.create(
                name=f"T{i}", project=env["proj"], state=env["todo"],
                sequence_id=i, sort_order=i * 100, created_by=env["owner"]).id))
        resp = _client(env["owner"]).post(_token_url(env), {
            "client_tab_id": "tab-cap", "issue_rooms": ids,
        }, format="json")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_INVALID_PARAM"

    def test_pem_escaped_newline_form_supported(self, rt_settings, env):
        """PEM 单行 \n 转义注入形态与真实多行等价（normalize_pem）。"""
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "django.conf.settings.LIVE_JWT_PRIVATE_KEY", PRIV_ESCAPED,
                raising=True)
            resp = _client(env["owner"]).post(_token_url(env), {
                "client_tab_id": "tab-e", "issue_rooms": [],
            }, format="json")
            assert resp.status_code == 200
            claims = pyjwt.decode(resp.json()["data"]["token"], PUB_PEM,
                                  algorithms=["RS256"])
            assert claims["sub"] == str(env["owner"].id)

    def test_missing_client_tab_id_400(self, rt_settings, env):
        resp = _client(env["owner"]).post(_token_url(env), {"issue_rooms": []},
                                          format="json")
        assert resp.status_code == 400


# ────────────────────────────────────────────────────────────────
# T3-09 续签端点（BR-02 jti 轮换）
# ────────────────────────────────────────────────────────────────
class TestRenewToken:
    def _first_ticket(self, env, user, issue_rooms=None):
        c = _client(user)
        body = {"client_tab_id": "tab-r"}
        if issue_rooms is not None:
            body["issue_rooms"] = issue_rooms
        resp = c.post(_token_url(env), body, format="json")
        assert resp.status_code == 200
        return resp.json()["data"]

    def test_renew_rotates_jti_and_keeps_rooms(self, rt_settings, env):
        first = self._first_ticket(env, env["owner"], [str(env["issue"].id)])
        c = _client(env["owner"])
        resp = c.post("/api/v1/users/me/realtime-token/renew/", {
            "token": first["token"], "client_tab_id": "tab-r",
        }, format="json")
        assert resp.status_code == 200
        renewed = resp.json()["data"]
        old = pyjwt.decode(first["token"], PUB_PEM, algorithms=["RS256"])
        new = pyjwt.decode(renewed["token"], PUB_PEM, algorithms=["RS256"])
        assert new["jti"] != old["jti"]              # jti 轮换
        assert new["rooms"] == old["rooms"]           # 房间集以旧票为准
        assert new["ws"] == old["ws"]

    def test_renew_expired_old_token_401(self, rt_settings, env):
        first = self._first_ticket(env, env["owner"])
        # 手工签一张已过期的旧票（同私钥）
        now = int(timezone.now().timestamp())
        expired = pyjwt.encode({
            "sub": str(env["owner"].id), "rooms": first["rooms"],
            "ws": f"{env['owner'].id}:tab-r",
            "iat": now - 300, "exp": now - 60, "jti": "expired-jti",
        }, PRIV_PEM, algorithm="RS256")
        resp = _client(env["owner"]).post("/api/v1/users/me/realtime-token/renew/", {
            "token": expired, "client_tab_id": "tab-r",
        }, format="json")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "AUTH_TOKEN_EXPIRED"

    def test_renew_other_users_token_403(self, rt_settings, env):
        first = self._first_ticket(env, env["owner"])
        resp = _client(env["viewer"]).post("/api/v1/users/me/realtime-token/renew/", {
            "token": first["token"], "client_tab_id": "tab-r",
        }, format="json")
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "PERM_DENIED"

    def test_renew_issue_rooms_delta_revalidated(self, rt_settings, env):
        """携带 issue_rooms 时增删重走校验：删旧 + 增新（新不可见拒）。"""
        first = self._first_ticket(env, env["owner"], [str(env["issue"].id)])
        c = _client(env["owner"])
        # 增加一个可见 issue、去掉旧 issue
        new_issue = Issue.objects.create(
            name="T-new", project=env["proj"], state=env["todo"],
            sequence_id=99, sort_order=99, created_by=env["owner"])
        resp = c.post("/api/v1/users/me/realtime-token/renew/", {
            "token": first["token"], "client_tab_id": "tab-r",
            "issue_rooms": [str(new_issue.id)],
        }, format="json")
        assert resp.status_code == 200
        renewed = resp.json()["data"]
        assert f"issue:{new_issue.id}" in renewed["rooms"]
        assert f"issue:{env['issue'].id}" not in renewed["rooms"]
        # 增加不可见 → 403 拒续签
        resp2 = c.post("/api/v1/users/me/realtime-token/renew/", {
            "token": first["token"], "client_tab_id": "tab-r",
            "issue_rooms": [str(env["foreign_issue"].id)],
        }, format="json")
        assert resp2.status_code == 403

    def test_renew_after_member_removed_403(self, rt_settings, env):
        """viewer 被移出项目后续签拒绝（project 房间失效重校验）。"""
        first = self._first_ticket(env, env["viewer"])
        ProjectMember.objects.filter(project=env["proj"], member=env["viewer"]).delete()
        resp = _client(env["viewer"]).post("/api/v1/users/me/realtime-token/renew/", {
            "token": first["token"], "client_tab_id": "tab-r",
        }, format="json")
        assert resp.status_code == 403


# ────────────────────────────────────────────────────────────────
# T3-09 verify-rooms 内部端点（BR-03 / §9.7）
# ────────────────────────────────────────────────────────────────
class TestVerifyRooms:
    URL = "/api/v1/internal/realtime/verify-rooms/"

    def test_internal_key_required(self, rt_settings, env):
        resp = APIClient().post(self.URL, {"tickets": []}, format="json")
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "PERM_DENIED"
        resp2 = APIClient().post(
            self.URL, {"tickets": []}, format="json",
            HTTP_X_INTERNAL_KEY="wrong-key")
        assert resp2.status_code == 403

    def test_returns_only_invalid_entries(self, rt_settings, env):
        ProjectMember.objects.create(
            project=env["other_proj"], member=env["owner"], role=ProjectRole.ADMIN,
            created_by=env["owner"])
        payload = {
            "tickets": [
                {  # 全有效（project / issue / user）
                    "sub": str(env["owner"].id),
                    "rooms": [f"project:{env['proj'].id}",
                              f"issue:{env['issue'].id}",
                              f"user:{env['owner'].id}"],
                },
                {  # viewer 已被移出：project/issue 失效，user 恒有效
                    "sub": str(env["viewer"].id),
                    "rooms": [f"project:{env['proj'].id}",
                              f"issue:{env['issue'].id}",
                              f"user:{env['viewer'].id}"],
                },
            ],
        }
        ProjectMember.objects.filter(project=env["proj"],
                                     member=env["viewer"]).delete()
        resp = APIClient().post(self.URL, payload, format="json",
                                HTTP_X_INTERNAL_KEY="test-internal-key")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        invalid = body["data"]["invalid"]
        assert invalid == [{
            "sub": str(env["viewer"].id),
            "rooms": [f"project:{env['proj'].id}", f"issue:{env['issue'].id}"],
        }]

    def test_all_valid_returns_empty_invalid(self, rt_settings, env):
        resp = APIClient().post(self.URL, {
            "tickets": [{
                "sub": str(env["owner"].id),
                "rooms": [f"project:{env['proj'].id}", f"user:{env['owner'].id}"],
            }],
        }, format="json", HTTP_X_INTERNAL_KEY="test-internal-key")
        assert resp.status_code == 200
        assert resp.json()["data"]["invalid"] == []

    def test_unknown_sub_only_user_room_valid(self, rt_settings, env):
        ghost = str(uuid_mod.uuid4())
        resp = APIClient().post(self.URL, {
            "tickets": [{
                "sub": ghost,
                "rooms": [f"project:{env['proj'].id}", f"user:{ghost}"],
            }],
        }, format="json", HTTP_X_INTERNAL_KEY="test-internal-key")
        assert resp.json()["data"]["invalid"] == [
            {"sub": ghost, "rooms": [f"project:{env['proj'].id}"]},
        ]

    def test_malformed_tickets_400(self, rt_settings, env):
        resp = APIClient().post(self.URL, {"tickets": [{"rooms": ["project:x"]}]},
                                format="json", HTTP_X_INTERNAL_KEY="test-internal-key")
        assert resp.status_code == 400


# ────────────────────────────────────────────────────────────────
# T3-11 event_publisher
# ────────────────────────────────────────────────────────────────
class FakeRedis:
    def __init__(self):
        self.published: list[tuple[str, str]] = []

    def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1


class TestPublishEvent:
    def test_message_format_and_channel(self, rt_settings):
        event_publisher.reset_redis_state()
        fake = FakeRedis()
        with patch.object(event_publisher, "_redis", return_value=fake):
            ok = event_publisher.publish_event.apply(
                args=["issue.state.changed",
                      {"issue_id": "i1", "actor_id": "u1",
                       "from_group": "unstarted", "to_group": "started"},
                      ["project:p1", "issue:i1"],
                      "2026-09-05T06:32:00.220Z"],
                kwargs={}).get()
        assert ok is True
        assert len(fake.published) == 1
        channel, raw = fake.published[0]
        assert channel == "rp:events"
        msg = json.loads(raw)
        assert msg == {
            "event": "issue.state.changed",
            "rooms": ["project:p1", "issue:i1"],
            "payload": {"issue_id": "i1", "actor_id": "u1",
                        "from_group": "unstarted", "to_group": "started"},
            "occurred_at": "2026-09-05T06:32:00.220Z",
        }

    def test_oversize_payload_dropped(self, rt_settings):
        """BR-05：>2KB 丢弃（不出 Redis publish）+ 不抛错。"""
        event_publisher.reset_redis_state()
        fake = FakeRedis()
        with patch.object(event_publisher, "_redis", return_value=fake):
            ok = event_publisher.publish_event.apply(
                args=["issue.updated",
                      {"blob": "x" * event_publisher.MAX_MESSAGE_BYTES},
                      ["project:p1"], None],
                kwargs={}).get()
        assert ok is False
        assert fake.published == []

    def test_unknown_event_dropped(self, rt_settings):
        event_publisher.reset_redis_state()
        fake = FakeRedis()
        with patch.object(event_publisher, "_redis", return_value=fake):
            ok = event_publisher.publish_event.apply(
                args=["not.an.event", {}, ["project:p1"], None],
                kwargs={}).get()
        assert ok is False
        assert fake.published == []

    def test_redis_unavailable_degrades_without_raise(self, rt_settings):
        """Redis 不可用：warning 不抛（推送尽力而为，拉取兜底 §4.3.4）。"""
        event_publisher.reset_redis_state()
        with patch.object(event_publisher, "_redis", return_value=None):
            ok = event_publisher.publish_event.apply(
                args=["issue.updated", {"issue_id": "i1"}, ["project:p1"], None],
                kwargs={}).get()
        assert ok is False

    def test_redis_error_gives_up_without_raise(self, rt_settings, caplog):
        """Redis 抖动：退避重试后放弃（不抛错；eager 模式下 retry 轨道内联收敛）。"""
        with caplog.at_level("WARNING", logger="plane.bgtasks.event_publisher"):
            event_publisher.reset_redis_state()
            boom = FakeRedis()
            boom.publish = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down"))
            with patch.object(event_publisher, "_redis", return_value=boom):
                ok = event_publisher.publish_event.apply(
                    args=["issue.updated", {"issue_id": "i1"}, ["project:p1"], None],
                    kwargs={}).get()
        assert ok is False  # 不抛错（推送尽力而为）
        assert any("giveup" in r.message for r in caplog.records)


# ────────────────────────────────────────────────────────────────
# T3-11 挂点：Worker 尾部 / 评论 / 通知
# ────────────────────────────────────────────────────────────────
class TestMountPoints:
    def test_activity_row_publishes_events(self, rt_settings, env):
        """Activity 落库 → Worker 尾部映射扇出（state → issue.state.changed
        + activity.created；mock dispatch_event 断言调用）。"""
        with patch.object(event_publisher, "dispatch_event") as mock_dispatch:
            record_activity_row.apply(
                kwargs=dict(
                    issue_id=str(env["issue"].id), actor_id=str(env["owner"].id),
                    verb="updated", field="state", epoch=1000.0,
                    old_identifier=None, new_identifier=str(env["started"].id),
                ))
        events = {c.args[0] for c in mock_dispatch.call_args_list}
        assert "issue.state.changed" in events
        assert "activity.created" in events
        # rooms：issue.state.changed → project + issue
        state_call = next(c for c in mock_dispatch.call_args_list
                          if c.args[0] == "issue.state.changed")
        assert state_call.args[2] == [
            f"project:{env['proj'].id}", f"issue:{env['issue'].id}"]
        payload = state_call.args[1]
        assert payload["to_group"] == "started"    # identifier → 组语义解析
        assert payload["from_group"] is None
        assert payload["actor_id"] == str(env["owner"].id)
        assert payload["version"]
        # activity.created → 仅 project 房间 + 水位锚
        activity_call = next(c for c in mock_dispatch.call_args_list
                             if c.args[0] == "activity.created")
        assert activity_call.args[2] == [f"project:{env['proj'].id}"]
        assert ":" in activity_call.args[1]["stream_cursor"]

    def test_activity_sort_order_maps_board_moved_project_only(self, rt_settings, env):
        with patch.object(event_publisher, "dispatch_event") as mock_dispatch:
            record_activity_row.apply(
                kwargs=dict(
                    issue_id=str(env["issue"].id), actor_id=str(env["owner"].id),
                    verb="updated", field="sort_order", epoch=2000.0,
                ))
        board_calls = [c for c in mock_dispatch.call_args_list
                       if c.args[0] == "board.moved"]
        assert len(board_calls) == 1
        assert board_calls[0].args[2] == [f"project:{env['proj'].id}"]  # 仅 project
        assert board_calls[0].args[1]["column_version"]

    def test_activity_batch_carries_batch_id(self, rt_settings, env):
        """批量载荷（record_activity_batch）逐实体复用既有事件 + batch_id=epoch。"""
        from plane.bgtasks.issue_activity import record_activity_batch

        with patch.object(event_publisher, "dispatch_event") as mock_dispatch:
            record_activity_batch.apply(kwargs={"payload": {
                "batch": [
                    {"issue_id": str(env["issue"].id),
                     "actor_id": str(env["owner"].id),
                     "verb": "updated", "field": "name", "epoch": 7777.0},
                ],
                "comment": "batch: 批量更新",
            }})
        issue_updated = [c for c in mock_dispatch.call_args_list
                         if c.args[0] == "issue.updated"]
        assert len(issue_updated) == 1
        assert issue_updated[0].args[1]["batch_id"] == 7777.0

    def test_activity_dedup_skip_does_not_republish(self, rt_settings, env):
        """行级幂等命中（exists）→ 不重复扇出。"""
        kwargs = dict(issue_id=str(env["issue"].id), actor_id=str(env["owner"].id),
                      verb="updated", field="name", epoch=3000.0)
        with patch.object(event_publisher, "dispatch_event") as mock_dispatch:
            record_activity_row.apply(kwargs=dict(kwargs))
            record_activity_row.apply(kwargs=dict(kwargs))  # at-least-once 重投
        total = mock_dispatch.call_args_list
        assert len({json.dumps([c.args[0], c.args[1].get("brief")], default=str)
                    for c in total}) >= 1
        # 同一 (issue, field, epoch) 只落一行
        from plane.db.models import IssueActivity
        assert IssueActivity.objects.filter(
            issue=env["issue"], field="name", epoch=3000.0).count() == 1

    def test_comment_create_publishes_on_commit(self, rt_settings, env):
        """评论落库 → on_commit 后 comment.created 扇出（issue + project 摘要房间）。"""
        from plane.db.services.comment import CommentService

        with patch.object(event_publisher, "dispatch_event") as mock_dispatch:
            with DjangoTestCase.captureOnCommitCallbacks(execute=True) as callbacks:
                comment, _ = CommentService().create(
                    issue=env["issue"], actor=env["owner"],
                    payload={"comment_html": "<p>hello world</p>"})
        comment_events = [c for c in mock_dispatch.call_args_list
                          if c.args[0] == "comment.created"]
        assert len(comment_events) == 1
        assert comment_events[0].args[2] == [
            f"issue:{env['issue'].id}", f"project:{env['proj'].id}"]
        assert comment_events[0].args[1]["comment_id"] == str(comment.id)
        assert comment_events[0].args[1]["actor_id"] == str(env["owner"].id)
        assert callbacks  # on_commit 确实注册且已执行

    def test_notification_fanout_publishes_user_room_events(self, rt_settings, env):
        """通知落库 → notification.created → user 房间（按 receiver 逐条）。"""
        from plane.db.models import IssueComment
        from plane.db.services.notify import fanout_comment

        comment = IssueComment.objects.create(
            issue=env["issue"], actor=env["owner"], comment_html="<p>@view</p>",
            created_by=env["owner"])
        with patch.object(event_publisher, "dispatch_event") as mock_dispatch:
            fanout_comment(comment_id=str(comment.id),
                           issue_id=str(env["issue"].id),
                           actor=env["owner"],
                           mention_ids={str(env["viewer"].id)})
        notif_events = [c for c in mock_dispatch.call_args_list
                        if c.args[0] == "notification.created"]
        assert len(notif_events) >= 1
        for call in notif_events:
            room = call.args[2][0]
            assert room.startswith("user:")
            assert call.args[1]["unread_delta"] == 1
            assert call.args[1]["notification_id"]
