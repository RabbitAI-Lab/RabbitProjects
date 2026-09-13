"""INTG-001 GitHub 集成测试（T5-04——mock 门禁口径，零出网）。

覆盖：Fernet 密钥往返；绑定 CRUD（≤5/项目、重复 409、解绑 external 保留）；
入站三道闸（定位→验签常量时间比对→Delivery 查重；坏签名 403、未绑定 202 丢弃、
重复投递 202）；Worker 路由（opened 建任务+幂等锚+标题前缀回写出站 mock、
edited 后写胜出+冲突日志、closed 守卫流转/依赖阻塞降级系统评论、
PR 合并 RBT-n 关联流转、push commit 挂载去重）；速率预算（70% 降级/暂停窗口）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from plane.db.models import (
    IntegrationInstallation,
    Issue,
    IssueLink,
    Project,
    ProjectMember,
    ProjectRole,
    State,
    SyncConflictLog,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.integration import decrypt_secret, encrypt_secret, new_integration_secret
from plane.db.models.roles import WorkspaceRole
from plane.db.seeds.project_states import seed_project_states

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="i1-owner@rabbit.dev", password="Rabbit123!", display_name="张三")
    admin = User.objects.create_user(email="i1-admin@rabbit.dev", password="Rabbit123!", display_name="李管理")
    ws = Workspace.objects.create(name="W", slug=f"w-i1-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    for u, r in ((owner, WorkspaceRole.OWNER), (admin, WorkspaceRole.ADMIN)):
        WorkspaceMember.objects.create(workspace=ws, member=u, role=r, created_by=owner)
    proj = Project.objects.create(name="P", identifier="RBT", workspace=ws, created_by=owner)
    ProjectMember.objects.create(project=proj, member=admin, role=ProjectRole.ADMIN, created_by=owner)
    seed_project_states(proj)
    secret = new_integration_secret()
    binding = IntegrationInstallation.objects.create(
        installation_id=9001,
        project=proj,
        repository_full_name="acme/rabbit-web",
        repository_node_id="R_1",
        webhook_secret=encrypt_secret(secret),
        created_by=owner,
    )
    return {"owner": owner, "admin": admin, "ws": ws, "proj": proj, "binding": binding, "secret": secret}


def _client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _signed_post(env, payload: dict, *, event="issues", delivery="d-1", secret=None):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new((secret or env["secret"]).encode(), body, hashlib.sha256).hexdigest()
    return APIClient().post(
        "/api/v1/integrations/github/webhook/",
        data=body,
        content_type="application/json",
        headers={"X-GitHub-Event": event, "X-GitHub-Delivery": delivery, "X-Hub-Signature-256": sig},
    )


def _issue_payload(env, *, action="opened", node="N_1", number=7, title="从 GitHub 来的", updated=None):
    from django.utils import timezone as tz

    now = updated or tz.now().isoformat()
    return {
        "action": action,
        "installation": {"id": 9001},
        "repository": {"full_name": "acme/rabbit-web", "node_id": "R_1"},
        "issue": {
            "node_id": node,
            "number": number,
            "title": title,
            "body": "正文",
            "state": "open",
            "updated_at": now,
            "created_at": now,
            "html_url": "https://github.com/acme/rabbit-web/issues/7",
        },
    }


# ── 密钥 ──────────────────────────────────────────────────────
def test_secret_roundtrip():
    plain = new_integration_secret()
    cipher = encrypt_secret(plain)
    assert cipher != plain and decrypt_secret(cipher) == plain
    assert decrypt_secret("garbage!!") == ""  # 密钥轮换残留 → 空串 → 验签失败收口


# ── 绑定 CRUD ─────────────────────────────────────────────────
def test_binding_crud(env):
    url = f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/integrations/github/bindings/"
    r = _client(env["admin"]).post(
        url, format="json", data={"repository_full_name": "acme/rabbit-api", "installation_id": 9002}
    )
    assert r.status_code == 201
    d = r.json()["data"]
    assert d["webhook_registered"] is False  # 无回调基址（dev）→ 不注册
    assert len(d["webhook_secret_shown_once"]) >= 32  # 明文仅此一次
    row = IntegrationInstallation.objects.get(pk=d["id"])
    assert decrypt_secret(row.webhook_secret) == d["webhook_secret_shown_once"]
    # 重复绑定 409
    assert (
        _client(env["admin"]).post(url, format="json", data={"repository_full_name": "acme/rabbit-web"}).status_code
        == 409
    )
    # 上限 5（已 2，再造 4 → 第 4 个 409）
    for i in range(3):
        assert (
            _client(env["admin"]).post(url, format="json", data={"repository_full_name": f"acme/r{i}"}).status_code
            == 201
        )
    assert _client(env["admin"]).post(url, format="json", data={"repository_full_name": "acme/r9"}).status_code == 409
    # 暂停 / 恢复
    bid = row.id
    r2 = _client(env["admin"]).patch(f"{url}{bid}/", format="json", data={"sync_status": "paused"})
    assert r2.status_code == 200 and r2.json()["data"]["sync_status"] == "paused"
    # 权限：非项目成员 403
    outsider = User.objects.create_user(email="i1-out@rabbit.dev", password="Rabbit123!", display_name="外")
    WorkspaceMember.objects.create(
        workspace=env["ws"], member=outsider, role=WorkspaceRole.MEMBER, created_by=env["owner"]
    )
    assert _client(outsider).post(url, format="json", data={"repository_full_name": "acme/x"}).status_code == 403
    # 解绑：external 保留（BR-13）
    issue = Issue.objects.create(
        name="t",
        project=env["proj"],
        state=State.objects.get(project=env["proj"], group="unstarted"),
        priority="none",
        sequence_id=1,
        sort_order=100,
        created_by=env["owner"],
    )
    issue.external_source, issue.external_id = "github", "N_keep"
    issue.save(update_fields=["external_source", "external_id"])
    r3 = _client(env["admin"]).delete(f"{url}{bid}/")
    assert r3.status_code == 204
    row.refresh_from_db()
    assert row.sync_status == "unbound"
    assert Issue.objects.get(pk=issue.pk).external_id == "N_keep"


# ── 入站三道闸 ────────────────────────────────────────────────
def test_inbound_gates(env, monkeypatch):
    sent: list[dict] = []
    from plane.bgtasks import github_sync as gs

    monkeypatch.setattr(gs.dispatch_github_event, "delay", lambda ev: sent.append(ev))
    # 未绑定仓库 → 202 丢弃
    payload = _issue_payload(env)
    payload["repository"]["full_name"] = "other/repo"
    r0 = _signed_post(env, payload, delivery="d-0")
    assert r0.status_code == 202 and not sent
    # 坏签名 → 403 PERM_DENIED，无副作用
    bad = _signed_post(env, _issue_payload(env), delivery="d-bad", secret="wrong-secret")
    assert bad.status_code == 403
    assert bad.json()["error"]["code"] == "PERM_DENIED"
    assert not sent
    # 合法 → 202 + 入队规范化事件
    r1 = _signed_post(env, _issue_payload(env), delivery="d-1")
    assert r1.status_code == 202
    assert sent[-1]["kind"] == "issue.opened"
    assert sent[-1]["installation_id"] == 9001
    # 重复 Delivery → 202 丢弃
    r2 = _signed_post(env, _issue_payload(env, action="edited"), delivery="d-1")
    assert r2.status_code == 202 and len(sent) == 1


# ── Worker：opened 建任务 + 回写 ───────────────────────────────
def test_worker_issue_opened(env, monkeypatch):
    writeback: list[tuple] = []
    from plane.bgtasks import github_sync as gs
    from plane.integrations import github as gh

    monkeypatch.setattr(
        gh.GitHubClient,
        "edit_issue_title",
        lambda self, repo, num, title, token_cache=None: writeback.append((repo, num, title)),
    )
    result = gs._on_issue_opened(
        {
            "kind": "issue.opened",
            "installation_id": 9001,
            "repository_full_name": "acme/rabbit-web",
            "delivery_id": "d-1",
            "issue": _issue_payload(env)["issue"],
        }
    )
    assert result.startswith("created:")
    issue = Issue.objects.get(external_source="github", external_id="N_1")
    assert issue.name == "从 GitHub 来的"
    assert issue.created_by.email == gs.SYSTEM_ACCOUNT_EMAIL  # 系统账号（BR-15）
    assert issue.github_context["number"] == 7
    assert writeback and writeback[0][2].startswith("[RBT-1] ")  # 标题前缀回写
    env["binding"].refresh_from_db()
    assert env["binding"].last_synced_at is not None


def test_worker_edited_lww_and_conflict(env):
    from django.utils import timezone

    from plane.bgtasks import github_sync as gs

    gs._on_issue_opened(
        {
            "kind": "issue.opened",
            "installation_id": 9001,
            "repository_full_name": "acme/rabbit-web",
            "issue": _issue_payload(env)["issue"],
        }
    )
    issue = Issue.objects.get(external_id="N_1")
    # 入站较新（updated 2026-09-08）→ 覆盖
    gs._on_issue_edited(
        {
            "kind": "issue.edited",
            "installation_id": 9001,
            "repository_full_name": "acme/rabbit-web",
            "issue": _issue_payload(env, action="edited", title="改后标题", updated="2030-01-01T00:00:00Z")["issue"],
        }
    )
    issue.refresh_from_db()
    assert issue.name == "改后标题"
    # 系统侧较新（把 updated_at 拨到 2027）→ 入站为败方 → 冲突日志 + 保持
    Issue.objects.filter(pk=issue.pk).update(updated_at=timezone.now().replace(year=2027))
    gs._on_issue_edited(
        {
            "kind": "issue.edited",
            "installation_id": 9001,
            "repository_full_name": "acme/rabbit-web",
            "issue": _issue_payload(env, action="edited", title="更晚的 GitHub 改动", updated="2026-09-20T00:00:00Z")[
                "issue"
            ],
        }
    )
    issue.refresh_from_db()
    assert issue.name == "改后标题"  # 后写胜出：系统侧新
    assert SyncConflictLog.objects.filter(issue=issue, scope="title").exists()


def test_worker_close_via_guard(env, monkeypatch):
    from plane.bgtasks import github_sync as gs

    gs._on_issue_opened(
        {
            "kind": "issue.opened",
            "installation_id": 9001,
            "repository_full_name": "acme/rabbit-web",
            "issue": _issue_payload(env)["issue"],
        }
    )
    issue = Issue.objects.get(external_id="N_1")
    # 无阻塞 → closed 迁 completed
    r = gs._on_issue_closed(
        {
            "kind": "issue.closed",
            "installation_id": 9001,
            "repository_full_name": "acme/rabbit-web",
            "issue": _issue_payload(env)["issue"],
        }
    )
    assert r == "transitioned"
    issue.refresh_from_db()
    assert issue.state.group == "completed" and issue.completed_at is not None
    # 阻塞场景：新任务被未完成前置挡住 → blocked + 系统评论（BR-10）
    gs._on_issue_opened(
        {
            "kind": "issue.opened",
            "installation_id": 9001,
            "repository_full_name": "acme/rabbit-web",
            "issue": _issue_payload(env, node="N_2", number=8, title="第二个")["issue"],
        }
    )
    issue2 = Issue.objects.get(external_id="N_2")
    IssueLink.objects.create(
        issue=issue2, related_issue=issue, relation_type="is_blocked_by", created_by=env["owner"]
    )  # issue2 被未完成的 issue 阻塞
    issue.state = State.objects.get(project=env["proj"], group="unstarted")
    issue.save(update_fields=["state"])  # 前置回未完成 → 构成阻塞
    r2 = gs._on_issue_closed(
        {
            "kind": "issue.closed",
            "installation_id": 9001,
            "repository_full_name": "acme/rabbit-web",
            "issue": _issue_payload(env, node="N_2", number=8)["issue"],
        }
    )
    assert r2.startswith("blocked:")
    from plane.db.models import IssueComment

    assert IssueComment.objects.filter(issue=issue2).exists()  # 降级系统评论


def test_worker_pr_merge_and_push(env):
    from plane.bgtasks import github_sync as gs

    gs._on_issue_opened(
        {
            "kind": "issue.opened",
            "installation_id": 9001,
            "repository_full_name": "acme/rabbit-web",
            "issue": _issue_payload(env)["issue"],
        }
    )
    issue = Issue.objects.get(external_id="N_1")
    pr_event = {
        "kind": "pull_request.merged",
        "installation_id": 9001,
        "repository_full_name": "acme/rabbit-web",
        "pull_request": {
            "number": 42,
            "merged": True,
            "title": "fix: RBT-1 修复",
            "body": "Fixes [RBT-1]",
            "html_url": "https://github.com/p/1",
            "merged_at": "2026-09-07T06:30:00Z",
        },
    }
    r = gs._on_pr_merged(pr_event)
    assert "done" in r
    issue.refresh_from_db()
    assert issue.state.group == "completed"
    assert issue.github_context["prs"][0]["number"] == 42
    # push 挂载 + 去重
    push = {
        "kind": "push",
        "installation_id": 9001,
        "repository_full_name": "acme/rabbit-web",
        "commits": [{"id": "a" * 40, "message": "chore: RBT-1 联调", "url": "u", "author": {"name": "z"}}],
    }
    assert gs._on_push(push) == "mounted:1"
    assert gs._on_push(push) == "mounted:0"  # sha×任务 去重（BR-11）
    issue.refresh_from_db()
    assert len(issue.github_context["commits"]) == 1


# ── 速率预算（BR-14）──────────────────────────────────────────
def test_quota_service():
    from plane.integrations.github import IntegrationQuotaService as Q

    r = Q.hit(9001, steady=True)
    assert r["remaining"] >= 0 and r["degraded"] is False
    Q.pause_until(9001, int(time.time()) + 30)
    assert Q.paused(9001) is not None and Q.paused(9001) > 20
