"""企微/钉钉 IM 通道测试（INTG-005，P4 R5b 门禁）。

覆盖 §5：通道 CRUD 与 target 去重（UT-01）、订阅全项目行去重（UT-02）、
钉钉加签公式（UT-03）、入站动作（UT-04）、degraded 连败（UT-05）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from plane.db.models import (
    ImSubscription,
    ImWebhookChannel,
    Issue,
    Project,
    SlackUserMap,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.roles import WorkspaceRole
from plane.app.views.im_integrations import _dingtalk_sign, im_deliver

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="im-owner@rabbit.dev", password="Rabbit123!", display_name="主")
    ws = Workspace.objects.create(name="IM", slug=f"w-im-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=owner, role=WorkspaceRole.OWNER, created_by=owner)
    proj = Project.objects.create(workspace=ws, name="P", identifier="IM1", created_by=owner)
    issue = Issue.objects.create(project=proj, name="任务", sequence_id=1, created_by=owner)
    channel = ImWebhookChannel.objects.create(
        workspace=ws,
        provider="wecom",
        name="企微群",
        target="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=K1",
        created_by=owner,
    )
    ding = ImWebhookChannel.objects.create(
        workspace=ws,
        provider="dingtalk",
        name="钉钉群",
        target="https://oapi.dingtalk.com/robot/send?access_token=T1",
        secret_ref="env:DINGTALK_SECRET",
        created_by=owner,
    )
    return {"owner": owner, "ws": ws, "proj": proj, "issue": issue, "channel": channel, "ding": ding}


def _c(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user)
    return c


def _ch(env):
    return f"/api/v1/workspaces/{env['ws'].slug}/integrations/im/channels/"


def _sub(env):
    return f"/api/v1/workspaces/{env['ws'].slug}/integrations/im/subscriptions/"


def test_ut01_channel_crud_and_dedup(env):
    c = _c(env["owner"])
    r = c.post(_ch(env), {"provider": "wecom", "name": "重复群", "target": env["channel"].target}, format="json")
    assert r.status_code == 409 and r.json()["error"]["details"][0]["code"] == "UNIQUE"
    r2 = c.post(_ch(env), {"provider": "wecom", "target": "http://insecure"}, format="json")
    assert r2.status_code == 400  # 非 https 拒
    lst = c.get(_ch(env)).json()["data"]
    assert len(lst) == 2 and lst[0]["is_degraded"] is False


def test_ut02_subscription_dedup_and_softdelete(env):
    c = _c(env["owner"])
    r = c.post(_sub(env), {"channel_id": str(env["channel"].id), "event_types": ["created"]}, format="json")
    assert r.status_code == 201  # 全项目行（null）
    r2 = c.post(_sub(env), {"channel_id": str(env["channel"].id), "event_types": ["created"]}, format="json")
    assert r2.status_code == 409  # 同范围重复（BR-01）
    r3 = c.post(_sub(env), {"channel_id": str(env["channel"].id), "project_id": str(env["proj"].id)}, format="json")
    assert r3.status_code == 201  # 另一范围合法
    sid = r.json()["data"]["id"]
    c.delete(f"{_sub(env)}{sid}/")
    r4 = c.post(_sub(env), {"channel_id": str(env["channel"].id)}, format="json")
    assert r4.status_code == 201  # 软删让位重建


def test_ut03_dingtalk_sign_formula():
    ts = 1726099200000
    sign = _dingtalk_sign("SEC123", ts)
    expect = base64.b64encode(hmac.new(b"SEC123", f"{ts}\nSEC123".encode(), hashlib.sha256).digest()).decode()
    assert sign == expect


def test_ut04_im_actions(env):
    member = User.objects.create_user(email="im-m@rabbit.dev", password="Rabbit123!")
    SlackUserMap.objects.create(
        installation=None, slack_user_id="IMU1", slack_email="im-m@rabbit.dev", slack_display_name="IM 员", user=member
    ) if False else None
    # SlackUserMap.installation 非空约束——走既有 slack 安装行
    from plane.db.models import IntegrationInstallation

    inst = IntegrationInstallation.objects.create(
        provider="slack", workspace=env["ws"], team_id="TIM", created_by=env["owner"]
    )
    SlackUserMap.objects.create(
        installation=inst, slack_user_id="IMU1", slack_email="im-m@rabbit.dev", slack_display_name="IM 员", user=member
    )
    c = APIClient()
    r_anon = c.post(
        "/api/v1/integrations/im/actions/",
        {"action": "assign_me", "im_user_id": "NOPE", "issue_id": str(env["issue"].id)},
        format="json",
    )
    assert r_anon.json()["ok"] is False  # 未映射拒
    r = c.post(
        "/api/v1/integrations/im/actions/",
        {"action": "assign_me", "im_user_id": "IMU1", "issue_id": str(env["issue"].id)},
        format="json",
    )
    assert r.json()["ok"] is True
    from plane.db.models import IssueAssignee

    assert IssueAssignee.objects.filter(issue=env["issue"], assignee=member).exists()
    r2 = c.post(
        "/api/v1/integrations/im/actions/",
        {"action": "complete", "im_user_id": "IMU1", "issue_id": str(env["issue"].id)},
        format="json",
    )
    assert r2.json()["ok"] is True
    env["issue"].refresh_from_db()
    assert env["issue"].completed_at is not None


def test_ut05_degraded_after_retries(env):
    from plane.app.views.im_integrations import deliver_once

    import requests as _rq

    with patch.object(_rq, "post", side_effect=RuntimeError("webhook down")):
        try:
            deliver_once(
                str(env["channel"].id),
                {"title": "x", "issue_key": "K", "issue_name": "n", "detail": "d"},
                is_final_retry=True,
            )
        except RuntimeError:
            pass
        env["channel"].refresh_from_db()
        assert env["channel"].is_degraded is True  # BR-03 连败置位

    class _Ok:
        status_code = 200

    with patch.object(_rq, "post", return_value=_Ok()):
        assert (
            deliver_once(str(env["channel"].id), {"title": "x", "issue_key": "K", "issue_name": "n", "detail": "d"})[
                "ok"
            ]
            is True
        )
        env["channel"].refresh_from_db()
        assert env["channel"].is_degraded is False  # 成功复位
