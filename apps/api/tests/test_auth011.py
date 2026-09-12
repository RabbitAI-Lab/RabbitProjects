"""目录同步测试（AUTH-011，P4 R2 门禁）。

用例映射规格 §5.1（编号见各 docstring）；LDAP 线路经 client_fetch 注入伪
目录（拉取面外置，归并裁决全真实）；SCIM 走 APIClient + Bearer。
影响面依据（CodeGraph 2026-09-12）：directory 域全新零历史调用方；
workspace_member._do_accept 补挂租户配额由 test_auth012 既有面回归守护。
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from plane.db.models import (
    DirectoryPendingAction,
    DirectorySyncRun,
    DirectoryUserMapping,
    LdapDirectoryConfig,
    ScimConnector,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.roles import WorkspaceRole
from plane.directory.services import DirectorySyncService

pytestmark = pytest.mark.django_db

GOV = override_settings(TENANT_GOVERNANCE_ENABLED=True)


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="d-owner@rabbit.dev", password="Rabbit123!", display_name="主")
    member = User.objects.create_user(email="d-member@rabbit.dev", password="Rabbit123!", display_name="员")
    ws = Workspace.objects.create(name="D", slug=f"w-dir-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=owner, role=WorkspaceRole.OWNER, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=member, role=WorkspaceRole.MEMBER, created_by=owner)
    config = LdapDirectoryConfig.objects.create(
        workspace=ws,
        name="AD",
        server_uri="ldaps://ad.corp:636",
        bind_dn="CN=sync",
        bind_secret_ref="env:LDAP_BIND_SECRET",
        base_dn="OU=Staff,DC=corp",
        is_enabled=True,
        created_by=owner,
    )
    return {"owner": owner, "member": member, "ws": ws, "config": config}


def _entry(env, eid, email, name="目录用户", **kw):
    return {
        "external_id": eid,
        "email": email,
        "display_name": name,
        "department": kw.get("department", ""),
        "title": "",
        "usn_changed": kw.get("usn", "100"),
    }


def _mapping(env, user, eid, email, **kw):
    return DirectoryUserMapping.objects.create(
        workspace=env["ws"], user=user, channel="ldap", external_id=eid, email_snapshot=email, **kw
    )


# ── 归并核心 ───────────────────────────────────────────────────────


def test_ut01_missing_email_skipped(env):
    svc = DirectorySyncService(env["ws"], "ldap")
    b = svc.reconcile([_entry(env, "g1", "")], full_sync=True)
    assert b.counts() == {"created": 0, "updated": 0, "disabled": 0, "skipped": 1, "failed": 0}
    assert b.skipped[0]["reason"] == "missing_email"
    assert DirectoryUserMapping.objects.filter(workspace=env["ws"]).count() == 0


def test_ut02_email_normalization(env):
    svc = DirectorySyncService(env["ws"], "ldap")
    svc.reconcile([_entry(env, "g1", "  Wang.Fang@Corp.com ")], full_sync=True)
    assert User.objects.filter(email="wang.fang@corp.com").exists()


def test_ut03_first_sync_creates(env):
    svc = DirectorySyncService(env["ws"], "ldap")
    b = svc.reconcile([_entry(env, "g1", "new.hire@corp.com")], full_sync=True)
    assert b.counts()["created"] == 1
    m = DirectoryUserMapping.objects.get(external_id="g1")
    assert m.identity_source == "directory"
    assert m.user.display_name == "目录用户"


def test_ut04_local_account_merge(env):
    svc = DirectorySyncService(env["ws"], "ldap")
    b = svc.reconcile([_entry(env, "g1", "d-member@rabbit.dev")], full_sync=True)
    assert b.created[0].get("reason") == "merged_local"
    m = DirectoryUserMapping.objects.get(external_id="g1")
    assert m.user == env["member"]
    assert m.identity_source == "merged"


def test_ut05_ut06_absence_double_confirm(env):
    svc1 = DirectorySyncService(env["ws"], "ldap")
    m = _mapping(env, env["member"], "g1", "d-member@rabbit.dev")
    svc1.reconcile([], full_sync=True)  # 第一次缺席
    m.refresh_from_db()
    assert m.absence_count == 1 and m.user.is_active
    svc2 = DirectorySyncService(env["ws"], "ldap")
    b = svc2.reconcile([], full_sync=True)  # 第二次缺席 → 禁用
    m.refresh_from_db()
    assert not m.user.is_active
    assert m.disabled_at_source == "ldap_absent"
    assert m.restore_snapshot["memberships"]  # 快照已存
    assert b.counts()["disabled"] == 1


def test_ut16_delta_batch_no_absence(env):
    m = _mapping(env, env["member"], "g1", "d-member@rabbit.dev")
    svc = DirectorySyncService(env["ws"], "ldap")
    svc.reconcile([], full_sync=False)  # 增量批：不判定缺席
    m.refresh_from_db()
    assert m.absence_count == 0 and m.user.is_active


def test_ut07_reactivate_with_snapshot(env):
    m = _mapping(
        env,
        env["member"],
        "g1",
        "d-member@rabbit.dev",
        absence_count=2,
        disabled_at_source="ldap_absent",
        restore_snapshot={"memberships": [{"department_id": None, "role": WorkspaceRole.MEMBER}]},
    )
    # queryset 更新绕过 post_save（避免 manual 盖章——本用例测 ldap_absent 复活）
    User.objects.filter(pk=env["member"].pk).update(is_active=False)
    svc = DirectorySyncService(env["ws"], "ldap")
    b = svc.reconcile([_entry(env, "g1", "d-member@rabbit.dev")], full_sync=True)
    m.refresh_from_db()
    env["member"].refresh_from_db()
    assert env["member"].is_active  # 复活
    assert m.absence_count == 0 and m.disabled_at_source == ""
    assert b.updated[0].get("reason") == "restored"


def test_ut08_manual_never_reactivate(env):
    _mapping(env, env["member"], "g1", "d-member@rabbit.dev", disabled_at_source="manual")
    User.objects.filter(pk=env["member"].pk).update(is_active=False)
    svc = DirectorySyncService(env["ws"], "ldap")
    b = svc.reconcile([_entry(env, "g1", "d-member@rabbit.dev")], full_sync=True)
    env["member"].refresh_from_db()
    assert not env["member"].is_active  # 守卫：不复活
    assert b.counts()["updated"] == 0 and b.counts()["created"] == 0


def test_ut09_protected_members(env):
    # WS_OWNER（角色实时判定）+ is_sync_protected 双保护
    _mapping(env, env["owner"], "g0", "d-owner@rabbit.dev")
    prot = User.objects.create_user(email="svc@rabbit.dev", password="x")
    WorkspaceMember.objects.create(workspace=env["ws"], member=prot, role=WorkspaceRole.MEMBER, created_by=prot)
    _mapping(env, prot, "g9", "svc@rabbit.dev", is_sync_protected=True)
    svc = DirectorySyncService(env["ws"], "ldap")
    svc.reconcile([], full_sync=True)
    svc2 = DirectorySyncService(env["ws"], "ldap")
    b2 = svc2.reconcile([], full_sync=True)  # 两轮缺席
    env["owner"].refresh_from_db()
    prot.refresh_from_db()
    assert env["owner"].is_active and prot.is_active
    assert {s["reason"] for s in b2.skipped} == {"sync_protected"}


def test_ut10_dry_run_rollback(env):
    from django.db import transaction

    before_users = User.objects.count()
    before_maps = DirectoryUserMapping.objects.count()
    with transaction.atomic():
        svc = DirectorySyncService(env["ws"], "ldap", dry_run=True, run_id="r1")
        b = svc.reconcile([_entry(env, "g1", "dry.run@corp.com")], full_sync=True)
        transaction.set_rollback(True)
    assert b.counts()["created"] == 1  # 桶计数真实
    assert User.objects.count() == before_users  # 数据库零变化
    assert DirectoryUserMapping.objects.count() == before_maps


def test_ut13_seats_full_pending(env):
    from unittest.mock import patch

    with patch.object(DirectorySyncService, "_seats_available", return_value=False):
        svc = DirectorySyncService(env["ws"], "ldap")
        b = svc.reconcile([_entry(env, "g1", "over.seat@corp.com")], full_sync=True)
    assert b.counts()["created"] == 0
    pending = DirectoryPendingAction.objects.filter(kind="pending_provision", workspace=env["ws"]).first()
    assert pending.payload["email"] == "over.seat@corp.com"
    # 幂等去重：同 external_id 再入队复用
    with patch.object(DirectorySyncService, "_seats_available", return_value=False):
        DirectorySyncService(env["ws"], "ldap").reconcile([_entry(env, "g1", "over.seat@corp.com")], full_sync=True)
    assert DirectoryPendingAction.objects.filter(kind="pending_provision", workspace=env["ws"]).count() == 1


def test_ut15_email_change_manual_review(env):
    _mapping(env, env["member"], "g1", "old.mail@corp.com")
    svc = DirectorySyncService(env["ws"], "ldap")
    b = svc.reconcile([_entry(env, "g1", "new.mail@corp.com")], full_sync=True)
    review = DirectoryPendingAction.objects.filter(kind="manual_review", workspace=env["ws"]).first()
    assert review.payload["old_email"] == "old.mail@corp.com"
    assert review.payload["new_email"] == "new.mail@corp.com"
    assert b.skipped[0]["reason"] == "email_changed_manual_review"
    assert not User.objects.filter(email="new.mail@corp.com").exists()  # 不自动归并


def test_ut17_cursor_monotonic(env):
    from plane.directory.tasks import ldap_sync

    def fake_fetch(*, full_sync):
        return [
            _entry(env, "g1", "cursor@corp.com", usn="500"),
            _entry(env, "g2", "cursor2@corp.com", usn="300"),
        ]  # 乱序

    ldap_sync.run(str(env["config"].id), full_sync=True, client_fetch=fake_fetch)
    env["config"].refresh_from_db()
    assert env["config"].sync_cursor == "500"  # 批内取 max
    ldap_sync.run(str(env["config"].id), full_sync=True, client_fetch=fake_fetch)
    env["config"].refresh_from_db()
    assert env["config"].sync_cursor == "500"  # 重放不退（跨批只进不退）


def test_ut23_manual_stamp_signal(env):
    m = _mapping(env, env["member"], "g1", "d-member@rabbit.dev")
    env["member"].is_active = False  # 管理员禁用 → post_save 盖章
    env["member"].save(update_fields=["is_active"])
    m.refresh_from_db()
    assert m.disabled_at_source == "manual"


# ── 管理端点 ───────────────────────────────────────────────────────


def _c(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_ut14_channel_exclusive(env):
    r = _c(env["owner"]).post(f"/api/v1/workspaces/{env['ws'].slug}/directory/scim/", {}, format="json")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "RESOURCE_STATE_INVALID"


def test_ut19_member_forbidden(env):
    r = _c(env["member"]).post(f"/api/v1/workspaces/{env['ws'].slug}/directory/sync/", {}, format="json")
    assert r.status_code == 403


def test_br07_map_change_requires_confirmed_dry_run(env):
    c = _c(env["owner"])
    # 造一个未确认的干跑
    DirectorySyncRun.objects.create(
        workspace=env["ws"],
        channel="ldap",
        is_dry_run=True,
        full_sync=True,
        status="dry_run",
        triggered_by="dry_run",
        expires_at=timezone.now() + timedelta(hours=24),
    )
    r = c.patch(
        f"/api/v1/workspaces/{env['ws'].slug}/directory/ldap/",
        {"group_map": [{"dn": "CN=QA", "department": "质量部"}]},
        format="json",
    )
    assert r.status_code == 409
    assert "干跑" in r.json()["error"]["message"]


def test_ut20_pending_resolve_actions(env):
    action = DirectoryPendingAction.objects.create(
        workspace=env["ws"],
        kind="pending_provision",
        dedup_key="g1",
        payload={"external_id": "g1", "email": "wait.seat@corp.com", "display_name": "等待者"},
    )
    c = _c(env["owner"])
    # 席位仍满 → 409 不静默失败
    from unittest.mock import patch

    with patch.object(DirectorySyncService, "_seats_available", return_value=False):
        r0 = c.post(
            f"/api/v1/workspaces/{env['ws'].slug}/directory/pending-actions/{action.id}/resolve/",
            {"action": "provision"},
            format="json",
        )
    assert r0.status_code == 409
    # expand_seats（有余量）→ 开通 + resolved
    r = c.post(
        f"/api/v1/workspaces/{env['ws'].slug}/directory/pending-actions/{action.id}/resolve/",
        {"action": "expand_seats"},
        format="json",
    )
    assert r.status_code == 200
    action.refresh_from_db()
    assert action.status == "resolved" and action.resolved_action == "expand_seats"
    assert User.objects.filter(email="wait.seat@corp.com").exists()
    assert DirectoryUserMapping.objects.filter(external_id="g1").exists()


def test_ut21_resolve_idempotent_replay(env):
    action = DirectoryPendingAction.objects.create(
        workspace=env["ws"],
        kind="manual_review",
        dedup_key="a@x->b@x",
        payload={"old_email": "a@x", "new_email": "b@x"},
        status="dismissed",
    )
    r = _c(env["owner"]).post(
        f"/api/v1/workspaces/{env['ws'].slug}/directory/pending-actions/{action.id}/resolve/",
        {"action": "dismiss"},
        format="json",
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "RESOURCE_STATE_INVALID"


def test_ut22_pending_list_filters(env):
    DirectoryPendingAction.objects.create(workspace=env["ws"], kind="pending_provision", dedup_key="k1", payload={})
    DirectoryPendingAction.objects.create(workspace=env["ws"], kind="manual_review", dedup_key="k2", payload={})
    c = _c(env["owner"])
    base = f"/api/v1/workspaces/{env['ws'].slug}/directory/pending-actions/"
    r = c.get(base + "?kind=manual_review&status=pending")
    assert r.status_code == 200 and len(r.json()["data"]) == 1
    r2 = c.get(base + "?per_page=500")
    assert r2.json()["meta"]["degraded"] is True  # >100 截断
    r3 = c.get(base + "?ordering=bad_field")
    assert r3.status_code == 400


# ── SCIM 协议面 ────────────────────────────────────────────────────


def _scim_env(env):
    env["config"].is_enabled = False
    env["config"].save(update_fields=["is_enabled"])
    token = "scim_testtoken123"
    return ScimConnector.objects.create(
        workspace=env["ws"],
        name="SCIM",
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        token_prefix=token[:8],
        is_enabled=True,
        created_by=env["owner"],
        group_map=[{"dn": "CN=QA-Team", "department": "质量部"}],
    ), token


def _bearer(client, token):
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def test_ut11_scim_idempotent_create(env):
    _, token = _scim_env(env)
    c = _bearer(APIClient(), token)
    body = {
        "schemas": ["x"],
        "userName": "idp.user@corp.com",
        "externalId": "idp-1",
        "name": {"displayName": "IdP 用户"},
    }
    r1 = c.post("/scim/v2/Users/", body, format="json")
    assert r1.status_code == 201
    r2 = c.post("/scim/v2/Users/", body, format="json")  # 重复开通 → 200 既有
    assert r2.status_code == 200 and r2.json()["id"] == r1.json()["id"]
    assert DirectoryUserMapping.objects.filter(channel="scim", external_id="idp-1").count() == 1
    # 信封豁免：响应是 SCIM schema 而非统一信封
    assert "schemas" in r1.json() and "status" not in r1.json()


def test_ut12_scim_delete_disables(env):
    _, token = _scim_env(env)
    c = _bearer(APIClient(), token)
    r1 = c.post("/scim/v2/Users/", {"userName": "gone@corp.com", "externalId": "idp-2"}, format="json")
    scim_id = r1.json()["id"]
    r2 = c.delete(f"/scim/v2/Users/{scim_id}/")
    assert r2.status_code == 204  # 拒绝物理删除
    m = DirectoryUserMapping.objects.get(external_id="idp-2")
    assert not m.user.is_active  # 按禁用处理
    assert m.disabled_at_source == "scim_inactive"
    assert m.user_id  # 历史账号保留（BR-05）


def test_ut18_scim_invalid_token(env):
    _scim_env(env)
    c = _bearer(APIClient(), "scim_wrong")
    r = c.get("/scim/v2/Users/")
    assert r.status_code == 401
    assert r.json()["detail"]  # SCIM Errors 结构


def test_scim_patch_active_and_groups(env):
    from plane.db.models import Department

    _, token = _scim_env(env)
    c = _bearer(APIClient(), token)
    r1 = c.post(
        "/scim/v2/Users/",
        {"userName": "grp@corp.com", "externalId": "idp-3", "name": {"displayName": "组员"}},
        format="json",
    )
    uid = r1.json()["id"]
    # PATCH replace active=false
    r2 = c.patch(
        f"/scim/v2/Users/{uid}/", {"Operations": [{"op": "replace", "path": "active", "value": False}]}, format="json"
    )
    assert r2.status_code == 200 and r2.json()["active"] is False
    # Group push → 部门差集应用
    dept = Department.objects.create(workspace=env["ws"], name="质量部", path="001", created_by=env["owner"])
    r3 = c.post(
        "/scim/v2/Groups/", {"displayName": "CN=QA-Team", "members": [{"value": "grp@corp.com"}]}, format="json"
    )
    assert r3.status_code == 201
    wm = WorkspaceMember.objects.get(workspace=env["ws"], member__email="grp@corp.com")
    assert wm.department == dept
