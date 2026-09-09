"""全站审计测试（AUTH-010，Sprint-8 R4 门禁）。

覆盖：三层去重幂等（同 event_key 重放仅一行）、hash 链衔接与篡改检出
（BR-12）、黑名单过滤（BR-11）、注册表拒未注册（BR-05）、兼容桥
（record_audit → 真管道）、检索权限/筛选/分页（BR-03/04）、导出双授权 +
自审计（BR-08）+ 链断冻结、实例级自审计（BR-16）、留存 drop 分区
（BR-07）、检索 P95。
"""
from __future__ import annotations

import time

import pytest
from django.db import connection
from rest_framework.test import APIClient

from plane.audit.recorder import blacklist_filter, record
from plane.audit.registry import EVENT_REGISTRY
from plane.bgtasks.audit_record import (
    audit_daily_maintenance,
    audit_record,
    verify_hash_chain,
    write_chained_row,
)
from plane.db.models import AuditLog, SystemAdmin, User, Workspace, WorkspaceMember
from plane.db.models.roles import WorkspaceRole

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _sync_audit(monkeypatch):
    """delay → 同步执行 worker 体（测试事务内直落库）。"""
    from plane.bgtasks import audit_record as mod
    monkeypatch.setattr(mod.audit_record, "delay",
                        lambda payload: mod.audit_record.run(payload))


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="au-owner@rabbit.dev",
                                     password="Rabbit123!", display_name="主")
    member = User.objects.create_user(email="au-member@rabbit.dev",
                                      password="Rabbit123!", display_name="员")
    ws = Workspace.objects.create(name="A", slug=f"w-au-{owner.id.hex[:8]}",
                                  owner=owner, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=owner,
                                   role=WorkspaceRole.OWNER, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=member,
                                   role=WorkspaceRole.MEMBER, created_by=owner)
    return {"owner": owner, "member": member, "ws": ws}


def _payload(env, key="k1", **over):
    p = {
        "event_key": key, "category": "member", "action": "role_changed",
        "workspace_id": str(env["ws"].id),
        "actor": {"id": str(env["owner"].id), "name": "主",
                  "email": "au-owner@rabbit.dev"},
        "actor_id": str(env["owner"].id),
        "object": {"type": "project_member", "id": "o1", "name": "李四"},
        "detail": {"role": {"from": "VIEWER", "to": "CONTRIBUTOR"}},
        "ip": "10.0.1.23", "user_agent": "pytest",
    }
    p.update(over)
    return p


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


# ── 管道与链 ──────────────────────────────────────────────
class TestPipeline:
    def test_write_and_chain(self, env):
        r1 = write_chained_row(_payload(env, "k1"))
        r2 = write_chained_row(_payload(env, "k2"))
        assert r1.prev_hash == "0" * 64
        assert r2.prev_hash == r1.hash  # 链衔接

    def test_idempotent_replay_single_row(self, env, django_capture_on_commit_callbacks):
        p = _payload(env, "dup")
        with django_capture_on_commit_callbacks(execute=True):
            record(p["event_key"], category=p["category"], action=p["action"],
                   workspace_id=env["ws"].id, actor=env["owner"],
                   obj=p["object"], detail=p["detail"])
        # 重放两次：三层去重收敛为一行
        audit_record(p)
        audit_record(p)
        assert AuditLog.objects.filter(event_key="dup").count() == 1

    def test_br11_blacklist(self, env):
        cleaned = blacklist_filter({"password": "x", "api_token": "y",
                                    "client_secret": "z", "role": "ok"})
        assert cleaned == {"role": "ok"}

    def test_br05_unregistered_rejected(self, env):
        p = _payload(env, "bad", category="nope", action="nada")
        from plane.audit.recorder import UnregisteredEvent
        with pytest.raises(UnregisteredEvent):
            audit_record(p)
        assert AuditLog.objects.filter(event_key="bad").count() == 0

    def test_compat_bridge_routes_events(self, env, django_capture_on_commit_callbacks):
        from plane.audit.recorder import record_audit

        with django_capture_on_commit_callbacks(execute=True):
            record_audit("department.created", actor_id=str(env["owner"].id),
                         object_id="d1")
        row = AuditLog.objects.filter(category="department").first()
        assert row is not None and row.action == "created"
        assert row.actor_id == str(env["owner"].id)

    def test_chain_verify_detects_tamper(self, env):
        write_chained_row(_payload(env, "k1"))
        write_chained_row(_payload(env, "k2"))
        assert verify_hash_chain(sample_rows=10)["broken"] == []
        # 裸 SQL 篡改 detail（绕 ORM 只增约束模拟攻击）
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE audit_log SET detail = '{\"evil\": true}'::jsonb "
                "WHERE event_key = 'k2'")
        result = verify_hash_chain(sample_rows=10)
        assert result["broken"], "篡改行必须被链校验检出"
        from django.core.cache import cache
        cache.delete("audit_chain_broken")  # 清冻结标记（LocMem 全局，防污染后续测试）


# ── 检索端点 ──────────────────────────────────────────────
class TestSearchEndpoints:
    def _seed(self, env, n=5):
        for i in range(n):
            write_chained_row(_payload(env, f"k{i}",
                                       detail={"i": i}))

    def test_admin_can_search_member_forbidden(self, env):
        self._seed(env)
        url = f"/api/v1/workspaces/{env['ws'].slug}/audit-logs/"
        assert _client(env["owner"]).get(url).status_code == 200
        r = _client(env["member"]).get(url)
        assert r.status_code == 403  # audit.read：WS_ADMIN+

    def test_filters_and_meta(self, env):
        self._seed(env, 6)
        url = f"/api/v1/workspaces/{env['ws'].slug}/audit-logs/"
        r = _client(env["owner"]).get(url, {"category": "member",
                                            "per_page": 3})
        body = r.json()
        assert body["meta"]["count"] == 3
        assert body["meta"]["total_count"] == 6
        assert body["meta"]["next_cursor"] == "3:3:0"
        assert all(row["category"] == "member" for row in body["data"])

    def test_unknown_filter_400(self, env):
        r = _client(env["owner"]).get(
            f"/api/v1/workspaces/{env['ws'].slug}/audit-logs/",
            {"hacker_field": "1"})
        assert r.status_code == 400

    def test_workspace_isolation(self, env, db):
        other_owner = User.objects.create_user(email="au2@rabbit.dev",
                                               password="x", display_name="乙")
        ws2 = Workspace.objects.create(name="B", slug=f"w-au2-{other_owner.id.hex[:6]}",
                                       owner=other_owner, created_by=other_owner)
        write_chained_row(_payload(env, "mine"))
        p2 = _payload(env, "theirs")
        p2["workspace_id"] = str(ws2.id)
        write_chained_row(p2)
        r = _client(env["owner"]).get(
            f"/api/v1/workspaces/{env['ws'].slug}/audit-logs/")
        keys = {row["event_key"] for row in r.json()["data"]}
        assert keys == {"mine"}  # BR-03 强制空间过滤

    def test_catalog(self, env):
        r = _client(env["owner"]).get(
            f"/api/v1/workspaces/{env['ws'].slug}/audit-logs/catalog/")
        assert r.status_code == 200
        assert any(g["category"] == "member" for g in r.json()["data"])

    def test_search_p95_under_300ms(self, env):
        for i in range(200):
            write_chained_row(_payload(env, f"perf{i}"))
        url = f"/api/v1/workspaces/{env['ws'].slug}/audit-logs/"
        c = _client(env["owner"])
        c.get(url)  # 预热
        samples = []
        for _ in range(20):
            t0 = time.perf_counter()
            c.get(url, {"category": "member", "search": "李"})
            samples.append((time.perf_counter() - t0) * 1000)
        samples.sort()
        p95 = samples[int(len(samples) * 0.95) - 1]
        assert p95 < 300, f"P95={p95:.0f}ms"


# ── 导出 ────────────────────────────────────────────────
class TestExport:
    def test_export_csv_and_self_audit(self, env, django_capture_on_commit_callbacks):
        write_chained_row(_payload(env, "k1"))
        with django_capture_on_commit_callbacks(execute=True):
            r = _client(env["owner"]).post(
                f"/api/v1/workspaces/{env['ws'].slug}/audit-logs/exports/",
                {"password": "Rabbit123!"}, format="json")
        assert r.status_code == 200
        assert r["Content-Type"].startswith("text/csv")
        assert b"role_changed" in r.content
        assert AuditLog.objects.filter(category="audit",
                                       action="exported").exists()  # BR-08

    def test_export_wrong_password_401(self, env):
        r = _client(env["owner"]).post(
            f"/api/v1/workspaces/{env['ws'].slug}/audit-logs/exports/",
            {"password": "bad"}, format="json")
        assert r.status_code == 401

    def test_export_frozen_on_broken_chain(self, env):
        from django.core.cache import cache
        cache.set("audit_chain_broken", ["x"], timeout=None)
        r = _client(env["owner"]).post(
            f"/api/v1/workspaces/{env['ws'].slug}/audit-logs/exports/",
            {"password": "Rabbit123!"}, format="json")
        assert r.status_code == 409
        cache.delete("audit_chain_broken")


# ── 实例级（BR-16）───────────────────────────────────────
class TestInstanceEndpoint:
    def test_system_admin_self_audited(self, env, django_capture_on_commit_callbacks):
        SystemAdmin.objects.create(user=env["owner"], is_active=True,
                                   created_by=env["owner"])
        with django_capture_on_commit_callbacks(execute=True):
            r = _client(env["owner"]).get("/api/v1/instances/audit-logs/")
        assert r.status_code == 200
        assert AuditLog.objects.filter(
            action="instance_query", workspace_id__isnull=True).exists()

    def test_non_system_admin_403(self, env):
        assert _client(env["owner"]).get(
            "/api/v1/instances/audit-logs/").status_code == 403


# ── 留存（BR-07）─────────────────────────────────────────
class TestRetention:
    def test_drop_old_partitions(self, env):
        with connection.cursor() as cur:
            cur.execute(
                "CREATE TABLE audit_log_p2020_01 PARTITION OF audit_log "
                "FOR VALUES FROM ('2020-01-01') TO ('2020-02-01')")
        result = audit_daily_maintenance()
        assert "audit_log_p2020_01" in result["dropped"]
        with connection.cursor() as cur:
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_tables "
                "WHERE tablename='audit_log_p2020_01')")
            assert not cur.fetchone()[0]

    def test_registry_nonempty(self):
        assert EVENT_REGISTRY and all(
            actions for actions in EVENT_REGISTRY.values())
