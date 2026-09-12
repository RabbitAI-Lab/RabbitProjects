"""Slack/Zoom 集成测试（INTG-003，P4 R5 门禁）。

覆盖 §5 核心：订阅 CRUD 与全项目行去重（表达式索引兜底 409 预检）、
软删让位重建（BR-14）、用户映射、入站动作三枚（未映射拒/指派/完成）、
Zoom 会议关联与纪要回挂（HMAC 验签负向）、安装演进列零影响（GitHub
既有行回归由 test_intg001 承载）。
"""

from __future__ import annotations

import hashlib
import hmac

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from plane.db.models import (
    IntegrationInstallation,
    Issue,
    Project,
    SlackChannelSubscription,
    SlackUserMap,
    User,
    Workspace,
    WorkspaceMember,
    ZoomConnector,
)
from plane.db.models.roles import WorkspaceRole

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="sk-owner@rabbit.dev", password="Rabbit123!", display_name="主")
    ws = Workspace.objects.create(name="SK", slug=f"w-sk-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=owner, role=WorkspaceRole.OWNER, created_by=owner)
    proj = Project.objects.create(workspace=ws, name="P", identifier="SK1", created_by=owner)
    issue = Issue.objects.create(project=proj, name="任务", sequence_id=1, created_by=owner)
    inst = IntegrationInstallation.objects.create(
        provider="slack",
        workspace=ws,
        team_id="T123TEAM",
        team_name="Acme",
        bot_token_ref="env:SLACK_BOT_TOKEN",
        bot_user_id="U123BOT",
        created_by=owner,
    )
    return {"owner": owner, "ws": ws, "proj": proj, "issue": issue, "inst": inst}


def _c(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user)
    return c


def _subs(env):
    return f"/api/v1/workspaces/{env['ws'].slug}/integrations/slack/subscriptions/"


def test_subscription_crud_and_dedup(env):
    c = _c(env["owner"])
    r = c.post(
        _subs(env),
        {
            "channel_id": "CCHAN1",
            "channel_name": "发版",
            "event_types": ["created", "state_changed"],
            "thread_sync": True,
        },
        format="json",
    )
    assert r.status_code == 201  # null=全项目行
    r2 = c.post(_subs(env), {"channel_id": "CCHAN1", "channel_name": "发版", "event_types": ["created"]}, format="json")
    assert r2.status_code == 409 and r2.json()["error"]["details"][0]["code"] == "UNIQUE"  # 全项目行去重
    # 项目范围行同频道合法（另一范围）
    r3 = c.post(_subs(env), {"channel_id": "CCHAN1", "project_id": str(env["proj"].id)}, format="json")
    assert r3.status_code == 201
    # 非法事件类型拒绝
    r4 = c.post(_subs(env), {"channel_id": "CCH2", "event_types": ["boom"]}, format="json")
    assert r4.status_code == 400
    # 软删让位重建（BR-14）
    sid = r.json()["data"]["id"]
    c.delete(f"{_subs(env)}{sid}/")
    r5 = c.post(_subs(env), {"channel_id": "CCHAN1"}, format="json")
    assert r5.status_code == 201
    # DB 表达式索引兜底（绕预检直插）
    with pytest.raises(Exception):
        SlackChannelSubscription.objects.create(
            installation=env["inst"], channel_id="CCHAN1", channel_name="x", event_types=[]
        )  # 同范围存活行唯一索引拦截


def test_user_mapping_and_actions(env):
    c = _c(env["owner"])
    member = User.objects.create_user(email="sk-m@rabbit.dev", password="Rabbit123!", display_name="映射员")
    r = c.put(
        f"/api/v1/workspaces/{env['ws'].slug}/integrations/slack/user-map/",
        {
            "slack_user_id": "U999",
            "slack_email": "sk-m@rabbit.dev",
            "slack_display_name": "映射员",
            "user_id": str(member.id),
        },
        format="json",
    )
    assert r.status_code == 200
    # 未映射用户动作 → 拒
    r_anon = APIClient().post(
        "/api/v1/integrations/slack/actions/",
        {"payload": '{"actions":[{"action_id":"assign_me"}],"callback_id":"issue:x","user":{"id":"U000"}}'},
        format="json",
    )
    assert r_anon.status_code == 200 and r_anon.json()["ok"] is False
    # 指派给我
    payload = {
        "actions": [{"action_id": "assign_me"}],
        "callback_id": f"issue:{env['issue'].id}",
        "user": {"id": "U999"},
    }
    r_assign = APIClient().post("/api/v1/integrations/slack/actions/", {"payload": payload}, format="json")
    assert r_assign.status_code == 200 and r_assign.json()["ok"] is True
    from plane.db.models import IssueAssignee

    assert IssueAssignee.objects.filter(issue=env["issue"], assignee=member).exists()
    # 完成
    payload["actions"] = [{"action_id": "complete"}]
    r_done = APIClient().post("/api/v1/integrations/slack/actions/", {"payload": payload}, format="json")
    assert r_done.json()["ok"] is True
    env["issue"].refresh_from_db()
    assert env["issue"].completed_at is not None


def test_zoom_meeting_and_minutes_hmac(env):
    ZoomConnector.objects.create(
        account_id="acc1",
        client_id="cid",
        client_secret_ref="env:ZC",
        webhook_secret_ref="env:ZOOM_WEBHOOK_SECRET",
        created_by=env["owner"],
    )
    c = _c(env["owner"])
    r = c.post(
        f"/api/v1/workspaces/{env['ws'].slug}/integrations/zoom/meetings/",
        {
            "issue_id": str(env["issue"].id),
            "meeting_id": "M123",
            "join_url": "https://zoom.us/j/M123",
            "topic": "评审会",
        },
        format="json",
    )
    assert r.status_code == 201
    # 纪要回挂：错误签名 401
    body = {"meeting_id": "M123", "summary": "三个行动项"}
    bad = APIClient().post("/api/v1/integrations/zoom/minutes/", body, format="json", HTTP_X_ZOOM_SIGNATURE="deadbeef")
    assert bad.status_code == 401
    # 正确 HMAC → 评论落任务
    import json as _json

    raw = _json.dumps(body)
    sig = hmac.new(b"zoom-secret", raw.encode(), hashlib.sha256).hexdigest()
    client = APIClient()
    # DRF format=json 的 body 与 raw 一致性不可假定——改 raw 形态提交保签名一致
    ok = client.post(
        "/api/v1/integrations/zoom/minutes/", raw, content_type="application/json", HTTP_X_ZOOM_SIGNATURE=sig
    )
    assert ok.status_code == 200, ok.content
    from plane.db.models import IssueComment

    assert IssueComment.objects.filter(issue=env["issue"], comment_stripped__contains="三个行动项").exists()
