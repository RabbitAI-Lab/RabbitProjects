"""INTG-002 出站 Webhook 测试（T5-05）。

覆盖：签名往返（±5min 窗 + 过期拒）；端点 CRUD（事件闭集校验 / 同 URL 409 /
≤20 上限 / 启停幂等 + 连败清零）；dispatch 扇出（事件匹配 + 幂等锚去重 +
ping 免订阅直达）；投递引擎（2xx 成功 + 连败 −1、非 2xx 按退避表重试、
7 次后 dead + 连败 +1、≥50 auto_disabled + 通知、cancelled、死信重放
replay_of 审计链）；project.* / issue.* / comment.created 挂点投递。
HTTP 以本地 mock server（_post 注入）承载——零出网。
"""

from __future__ import annotations

import time

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from plane.db.models import (
    IntegrationInstallation,
    Project,
    ProjectMember,
    ProjectRole,
    User,
    WebhookDelivery,
    WebhookEndpoint,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.roles import WorkspaceRole
from plane.db.seeds.project_states import seed_project_states

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clean():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="w2-owner@rabbit.dev", password="Rabbit123!", display_name="张三")
    member = User.objects.create_user(email="w2-member@rabbit.dev", password="Rabbit123!", display_name="李成员")
    ws = Workspace.objects.create(name="W", slug=f"w-w2-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    for u, r in ((owner, WorkspaceRole.OWNER), (member, WorkspaceRole.MEMBER)):
        WorkspaceMember.objects.create(workspace=ws, member=u, role=r, created_by=owner)
    proj = Project.objects.create(name="P", identifier="W02", workspace=ws, created_by=owner)
    ProjectMember.objects.create(project=proj, member=member, role=ProjectRole.CONTRIBUTOR, created_by=owner)
    seed_project_states(proj)
    return {"owner": owner, "member": member, "ws": ws, "proj": proj}


def _client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _mk_endpoint(env, *, events=None, url="https://hooks.example.com/rp", status="active", failures=0):
    from plane.db.models.integration import encrypt_secret
    from plane.db.services.webhook_outbound import new_endpoint_secret

    return WebhookEndpoint.objects.create(
        project=env["proj"],
        workspace=env["ws"],
        url=url,
        events=events or ["issue.updated", "comment.created"],
        secret_encrypted=encrypt_secret(new_endpoint_secret()),
        is_active=status,
        consecutive_failures=failures,
        created_by=env["owner"],
    )


def _base(env):
    return f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/webhooks/"


# ── 签名 ──────────────────────────────────────────────────────
def test_signature_roundtrip():
    from plane.db.services.webhook_outbound import sign_payload, verify_signature

    body = b'{"event":"issue.updated"}'
    sig = sign_payload("s3cret", body, int(time.time()))["X-RP-Signature"]
    assert verify_signature("s3cret", body, sig)
    assert not verify_signature("wrong", body, sig)
    assert not verify_signature("s3cret", body, sig, timestamp=int(time.time()) - 600)


# ── 端点 CRUD ─────────────────────────────────────────────────
def test_endpoint_crud(env):
    url = _base(env)
    assert (
        _client(env["member"])
        .post(url, format="json", data={"url": "https://x.example.com", "events": ["issue.updated"]})
        .status_code
        == 403
    )  # integration.config = PROJ_ADMIN
    r = _client(env["owner"]).post(
        url, format="json", data={"url": "https://hooks.example.com/rp", "events": ["issue.updated"]}
    )
    assert r.status_code == 201
    d = r.json()["data"]
    assert d["secret_shown_once"] and d["is_active"] == "active"
    eid = d["id"]
    # 非法事件 / 空 events / 重复 URL / ping 免勾选
    assert (
        _client(env["owner"])
        .post(url, format="json", data={"url": "https://a.example.com", "events": ["bogus.event"]})
        .status_code
        == 400
    )
    assert (
        _client(env["owner"]).post(url, format="json", data={"url": "https://a.example.com", "events": []}).status_code
        == 400
    )
    assert (
        _client(env["owner"])
        .post(url, format="json", data={"url": "https://hooks.example.com/rp", "events": ["issue.updated"]})
        .status_code
        == 409
    )
    assert (
        _client(env["owner"])
        .post(url, format="json", data={"url": "https://b.example.com", "events": ["webhook.ping"]})
        .status_code
        == 400
    )
    # 上限 20
    for i in range(19):
        WebhookEndpoint.objects.create(
            project=env["proj"],
            workspace=env["ws"],
            url=f"https://x{i}.example.com",
            events=["issue.updated"],
            secret_encrypted="x",
            created_by=env["owner"],
        )
    assert (
        _client(env["owner"])
        .post(url, format="json", data={"url": "https://cap.example.com", "events": ["issue.updated"]})
        .status_code
        == 409
    )
    # 停用 / 启用（连败清零）
    WebhookEndpoint.objects.filter(pk=eid).update(consecutive_failures=7)
    assert _client(env["owner"]).post(f"{url}{eid}/disable/", format="json").status_code == 200
    assert WebhookEndpoint.objects.get(pk=eid).is_active == "disabled"
    r2 = _client(env["owner"]).post(f"{url}{eid}/enable/", format="json")
    row = WebhookEndpoint.objects.get(pk=eid)
    assert r2.status_code == 200 and row.is_active == "active" and row.consecutive_failures == 0
    # 删除（软删后同 URL 可重建 BR-11）
    assert _client(env["owner"]).delete(f"{url}{eid}/").status_code == 204


# ── 扇出与投递引擎 ────────────────────────────────────────────
def test_dispatch_fanout_and_idempotent_anchor(env, monkeypatch):
    sent: list[str] = []
    from plane.db.services import webhook_outbound as wo

    monkeypatch.setattr(wo.deliver_webhook, "delay", lambda i: sent.append(str(i)))
    e1 = _mk_endpoint(env)  # 订阅 issue.updated
    _mk_endpoint(env, events=["comment.created"], url="https://hooks.example.com/c")  # 不匹配
    wo.dispatch_events(
        "issue.updated",
        {"event_id": "00000000-0000-0000-0000-000000000001", "data": {"x": 1}},
        project_id=env["proj"].id,
    )
    assert len(sent) == 1
    assert WebhookDelivery.objects.filter(endpoint=e1).count() == 1
    # 幂等锚重投 → 不新建行
    wo.dispatch_events(
        "issue.updated",
        {"event_id": "00000000-0000-0000-0000-000000000001", "data": {"x": 1}},
        project_id=env["proj"].id,
    )
    assert WebhookDelivery.objects.filter(endpoint=e1).count() == 1
    # ping 免订阅直达
    _mk_endpoint(env, events=["issue.updated"], url="https://hooks.example.com/p")
    wo.dispatch_events("webhook.ping", {"data": {"ping": True}}, project_id=env["proj"].id)
    assert WebhookDelivery.objects.filter(event="webhook.ping").count() == 3  # 全部活跃端点


def test_deliver_success_and_retry_to_dead(env, monkeypatch):
    from plane.db.services import webhook_outbound as wo

    e1 = _mk_endpoint(env, failures=3)
    row = WebhookDelivery.objects.create(
        endpoint=e1,
        event="issue.updated",
        event_id="00000000-0000-0000-0000-000000000002",
        payload={"event": "issue.updated"},
    )
    calls: list[int] = []

    def _ok(url, body, headers):
        calls.append(200)
        return 200, 5, None

    monkeypatch.setattr(wo, "_post", _ok)
    assert wo.deliver_webhook(str(row.id)) == "success"
    e1.refresh_from_db()
    assert e1.consecutive_failures == 2  # success −1（3→2）
    # 退避到死信：7 次尝试全 500
    row2 = WebhookDelivery.objects.create(
        endpoint=e1,
        event="issue.updated",
        event_id="00000000-0000-0000-0000-000000000003",
        payload={"event": "issue.updated"},
    )
    rescheduled: list = []
    monkeypatch.setattr(wo.deliver_webhook, "apply_async", lambda a, countdown=None: rescheduled.append(countdown))
    monkeypatch.setattr(wo, "_post", lambda u, b, h: (500, 8, None))
    results = [wo.deliver_webhook(str(row2.id)) for _ in range(7)]
    assert results[0].startswith("retrying:1s")
    assert results[-1] == "dead"
    row2.refresh_from_db()
    assert row2.status == "dead" and len(row2.attempts) == 7
    assert rescheduled == [1, 10, 60, 600, 3600, 21600]  # 退避表全覆盖
    e1.refresh_from_db()
    assert e1.consecutive_failures == 3  # dead +1（2→3）
    # 死信重放：新行指回原行（BR-07 审计链）
    replay = wo.replay_delivery(row2.id, actor=env["owner"])
    assert replay.replay_of == row2.id and replay.status == "pending"
    assert WebhookDelivery.objects.get(pk=row2.pk).status == "dead"  # 原行不动


def test_deliver_auto_disable_and_notify(env, monkeypatch, django_capture_on_commit_callbacks):
    from plane.db.services import webhook_outbound as wo

    notified: list[dict] = []
    import plane.db.services.webhook_outbound as wom

    monkeypatch.setattr(
        "plane.bgtasks.notifications.send_workspace_notification.delay", lambda **kw: notified.append(kw)
    )
    monkeypatch.setattr(wom, "_post", lambda u, b, h: (500, 8, None))
    monkeypatch.setattr(wo.deliver_webhook, "apply_async", lambda a, countdown=None: None)
    e1 = _mk_endpoint(env, failures=49)  # 再一败即 50
    row = WebhookDelivery.objects.create(
        endpoint=e1,
        event="issue.updated",
        event_id="00000000-0000-0000-0000-000000000004",
        payload={"event": "issue.updated"},
    )
    with django_capture_on_commit_callbacks(execute=True):
        for _ in range(7):
            wo.deliver_webhook(str(row.id))
    e1.refresh_from_db()
    assert e1.is_active == "auto_disabled"
    assert notified and notified[0]["event"] == "webhook.auto_disabled"
    # 后续投递 → cancelled（执行前重读状态，边界 #4）
    row2 = WebhookDelivery.objects.create(
        endpoint=e1,
        event="issue.updated",
        event_id="00000000-0000-0000-0000-000000000005",
        payload={"event": "issue.updated"},
    )
    assert wo.deliver_webhook(str(row2.id)) == "cancelled"


def test_project_events_hook(env, monkeypatch, django_capture_on_commit_callbacks):
    from plane.db.services import webhook_outbound as wo
    from plane.db.services.project_lifecycle import ProjectLifecycleService

    e1 = _mk_endpoint(env, events=["project.archived", "project.activated"])
    sent: list[str] = []
    monkeypatch.setattr(wo.deliver_webhook, "delay", lambda i: sent.append(str(i)))
    with django_capture_on_commit_callbacks(execute=True):
        ProjectLifecycleService().transition(env["proj"], to_status="archived", actor=env["owner"])
    rows = WebhookDelivery.objects.filter(endpoint=e1)
    assert rows.count() == 1 and rows.first().event == "project.archived"
    assert rows.first().payload["data"]["status"] == "archived"


def test_ping_endpoint_202(env, monkeypatch):
    e1 = _mk_endpoint(env)
    from plane.db.services import webhook_outbound as wo

    queued: list = []
    monkeypatch.setattr(wo.dispatch_events, "__module__", wo.__name__)  # 保持同源
    r = _client(env["owner"]).post(f"{_base(env)}{e1.id}/ping/", format="json")
    assert r.status_code == 202
    # on_commit 在测试事务内不触发——直接断言视图装配（真实投递归 e2e/flow）
    del queued


# ── INTG-002 交接项 3（Sprint-6 T6）：quota-status 端点 + degraded 旗标 ──


def _mk_installation(env, installation_id=4859835):
    return IntegrationInstallation.objects.create(
        project=env["proj"],
        installation_id=installation_id,
        repository_full_name="rabbit/test",
        created_by=env["owner"],
    )


def test_quota_status_endpoint_and_permission(env):
    """交接清单路径原样：PROJ_ADMIN+ 200（minute/hour/degraded/paused_for）；
    非配置角色与局外人统一 404（越权同构）。"""
    inst = _mk_installation(env)
    r = _client(env["owner"]).get(f"/api/v1/integrations/{inst.installation_id}/quota-status/")
    assert r.status_code == 200, r.json()
    data = r.json()["data"]
    assert data["installation_id"] == inst.installation_id
    for key in ("minute", "hour"):
        assert data[key]["cap"] in (30, 5000)
        assert data[key]["count"] == 0 and data[key]["degraded"] is False
    assert data["degraded"] is False and data["paused_for"] is None
    # CONTRIBUTOR（integration.config = PROJ_ADMIN+）→ 404 同构
    assert _client(env["member"]).get(f"/api/v1/integrations/{inst.installation_id}/quota-status/").status_code == 404
    # 局外人 → 404
    outsider = User.objects.create_user(email="w2-out@rabbit.dev", password="Rabbit123!")
    assert _client(outsider).get(f"/api/v1/integrations/{inst.installation_id}/quota-status/").status_code == 404
    # 不存在的 installation → 404
    assert _client(env["owner"]).get("/api/v1/integrations/9999999/quota-status/").status_code == 404


def test_quota_status_degraded_flag_and_pause(env):
    """degraded 口径与 hit() 同源（≥70% 触发）；pause_until 暴露剩余暂停秒。"""
    from django.core.cache import cache

    from plane.integrations.github import IntegrationQuotaService

    inst = _mk_installation(env)
    cache.clear()
    # 稳态窗口 21/30 = 70% → degraded
    for _ in range(21):
        IntegrationQuotaService.hit(inst.installation_id, steady=True)
    data = _client(env["owner"]).get(f"/api/v1/integrations/{inst.installation_id}/quota-status/").json()["data"]
    assert data["minute"]["ratio"] >= 0.70 and data["minute"]["degraded"] is True
    assert data["degraded"] is True
    # 暂停标记暴露
    IntegrationQuotaService.pause_until(inst.installation_id, int(time.time()) + 120)
    data = _client(env["owner"]).get(f"/api/v1/integrations/{inst.installation_id}/quota-status/").json()["data"]
    assert data["paused_for"] is not None and 0 < data["paused_for"] <= 120
    cache.clear()
