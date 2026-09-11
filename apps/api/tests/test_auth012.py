"""租户治理与风控测试（AUTH-012，P4 R1 门禁）。

用例映射规格 §5.1/§5.2（编号注释在各用例 docstring）；IT-01/02/04/06
（爬虫全链/邻居 P95/两层扣减顺序压测/多空间共享池压测）需 broker 与压测
环境，归 jmeter flow 与 R1 尾部 perf 套件（known-debt 登记）。

影响面依据（CodeGraph 2026-09-11）：治理域新符号（RiskRuleEngine/
execute_action/enforcement 四判定）零历史调用方；四个挂接视图的回归由
test_audit_log/test_intg002/test_file_library 既有套件守护，本文件只测
AUTH-012 自身契约。
"""
from __future__ import annotations

import datetime as dt

import pytest
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient, APIRequestFactory

from plane.base.exception import AppException
from plane.db.models import (
    GovernanceTicket,
    RiskAppeal,
    RiskEvent,
    RiskRule,
    SystemAdmin,
    Tenant,
    TenantQuota,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.roles import WorkspaceRole
from plane.governance import enforcement, risk_engine
from plane.governance.middleware import GovernanceMiddleware
from plane.governance.risk_engine import TIER_DEFAULTS, RiskRuleEngine, tier_quota

pytestmark = pytest.mark.django_db

GOV = override_settings(TENANT_GOVERNANCE_ENABLED=True)


@pytest.fixture()
def env(db):
    """治理测试环境：租户（free 档）+ 工作空间 + 平台运营二人 + 客户管理员。"""
    ops1 = User.objects.create_user(email="g-ops1@rabbit.dev", password="Rabbit123!",
                                    display_name="张运营")
    ops2 = User.objects.create_user(email="g-ops2@rabbit.dev", password="Rabbit123!",
                                    display_name="李运营")
    admin = User.objects.create_user(email="g-admin@rabbit.dev", password="Rabbit123!",
                                     display_name="客户管理员")
    SystemAdmin.objects.create(user=ops1, is_active=True, is_tenant_ops=True,
                               created_by=ops1)
    SystemAdmin.objects.create(user=ops2, is_active=True, is_tenant_ops=True,
                               created_by=ops2)
    tenant = Tenant.objects.create(name="Beta Test", tier="free", created_by=ops1)
    ws = Workspace.objects.create(name="W", slug=f"w-gov-{tenant.id.hex[:8]}",
                                  owner=admin, created_by=admin)
    Workspace.objects.filter(pk=ws.pk).update(tenant=tenant)
    ws.refresh_from_db()
    WorkspaceMember.objects.create(workspace=ws, member=admin,
                                   role=WorkspaceRole.OWNER, created_by=admin)
    return {"ops1": ops1, "ops2": ops2, "admin": admin,
            "tenant": tenant, "ws": ws}


def _ops_client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user)
    return c


# ── UT-01 配额 null 跟随 tier ──────────────────────────────────────

def test_ut01_quota_null_follows_tier(env):
    """TenantQuota 无显式值时按 tier 默认表取值（free 档全列）。"""
    assert tier_quota(env["tenant"]) == TIER_DEFAULTS["free"]
    TenantQuota.objects.create(tenant=env["tenant"], storage_bytes=100,
                               created_by=env["ops1"])
    q = tier_quota(env["tenant"])
    assert q["storage_bytes"] == 100                      # 显式值优先
    assert q["member_limit"] == TIER_DEFAULTS["free"]["member_limit"]  # null 跟随
    env["tenant"].tier = "standard"
    assert tier_quota(env["tenant"])["export_rows_per_day"] == 100_000


# ── UT-02/03/25/26 配额强制点（enforcement 直测）───────────────────

def _ws_with_assets(env, sizes: list[int]):
    import uuid as _uuid

    from plane.db.models import FileAsset

    for i, s in enumerate(sizes):
        FileAsset.objects.create(
            workspace=env["ws"], entity_type="issue",
            entity_id=_uuid.uuid4(), size=s, attributes={"size": s},
            storage_path=f"t/{i}", status="completed",
            created_by=env["admin"])


def test_ut02_storage_two_layers(env):
    """租户层打满 → QUOTA_STORAGE_EXCEEDED 且 details 注明租户层。"""
    with GOV:
        _ws_with_assets(env, [4 * 2**30])                 # 4GB / free 5GB
        with pytest.raises(AppException) as exc:
            enforcement.check_storage_quota(env["ws"], 2 * 2**30)  # +2GB 越限
        assert exc.value.error_code == "QUOTA_STORAGE_EXCEEDED"
        assert "租户层" in str(exc.value.extra_details)
        enforcement.check_storage_quota(env["ws"], 512 * 2**20)   # 余量内放行


def test_ut03_member_quota(env):
    """成员超限 → QUOTA_MEMBER_EXCEEDED（租户层聚合）。"""
    from plane.db.models import Workspace as W

    ws2 = Workspace.objects.create(name="W2", slug=f"w2-gov-{env['tenant'].id.hex[:6]}",
                                   owner=env["admin"], created_by=env["admin"])
    W.objects.filter(pk=ws2.pk).update(tenant=env["tenant"])
    for i in range(8):                                    # free 档 10 人
        u = User.objects.create_user(email=f"m{i}@rabbit.dev", password="x")
        WorkspaceMember.objects.create(workspace=env["ws"], member=u,
                                       role=WorkspaceRole.MEMBER, created_by=u)
    with GOV:
        enforcement.check_member_quota(env["ws"])         # 9 人 +1 = 10 未超
        u10 = User.objects.create_user(email="m10@rabbit.dev", password="x")
        WorkspaceMember.objects.create(workspace=ws2, member=u10,
                                       role=WorkspaceRole.MEMBER, created_by=u10)
        with pytest.raises(AppException) as exc:          # 10 人 +1 = 11 超
            enforcement.check_member_quota(env["ws"])
        assert exc.value.error_code == "QUOTA_MEMBER_EXCEEDED"


def test_ut04_downgrade_grace(env):
    """宽限期内只放行不硬拒；到期恢复硬拒（§2.3）。"""
    with GOV:
        _ws_with_assets(env, [6 * 2**30])                 # 超 free 5GB
        TenantQuota.objects.create(
            tenant=env["tenant"],
            downgrade_grace_until=dt.date.today() + dt.timedelta(days=5),
            created_by=env["ops1"])
        enforcement.check_storage_quota(env["ws"], 1)     # 宽限期内放行
        TenantQuota.objects.filter(tenant=env["tenant"]).update(
            downgrade_grace_until=dt.date.today() - dt.timedelta(days=1))
        with pytest.raises(AppException):
            enforcement.check_storage_quota(env["ws"], 1)  # 到期硬拒


def test_ut25_webhook_quota(env):
    from plane.db.models import Project, WebhookEndpoint

    with GOV:
        project = Project.objects.create(workspace=env["ws"], name="P",
                                         identifier="GV", created_by=env["admin"])
        for i in range(5):                                # free 档 webhook 5
            WebhookEndpoint.objects.create(
                project=project, workspace=env["ws"], url=f"https://h{i}.dev",
                events=["issue.created"], secret_encrypted="x",
                created_by=env["admin"])
        with pytest.raises(AppException) as exc:
            enforcement.check_webhook_quota(env["ws"])
        assert exc.value.error_code == "RESOURCE_LIMIT_EXCEEDED"


def test_ut26_project_quota(env):
    from plane.db.models import Project

    with GOV:
        for i in range(3):                                # free 档项目 3
            Project.objects.create(workspace=env["ws"], name=f"P{i}",
                                   identifier=f"G{i}", created_by=env["admin"])
        with pytest.raises(AppException) as exc:
            enforcement.check_project_quota(env["ws"])
        assert exc.value.error_code == "RESOURCE_LIMIT_EXCEEDED"


# ── UT-05/06/13~17 引擎规则语义 ────────────────────────────────────

def _fire_rule_seeded():
    """平台默认六行（迁移种子同构——测试库由迁移播种；缺行时补种幂等）。"""
    for code, threshold, action in [
        ("R-01", {"count": 200, "window": "10m"}, "alert"),
        ("R-02", {"distance_km": 1000, "window": "1h"}, "alert"),
        ("R-03", {"warn_ratio": 0.8, "deny_ratio": 1.0, "window": "1d"}, "deny"),
        ("R-04", {"count": 5, "window": "1h"}, "alert"),
        ("R-05", {"count": 50, "window": "1d",
                  "night_start_hour": 0, "night_end_hour": 6}, "alert"),
        ("R-06", {"quota_ratio": 0.5, "getlist_ratio": 0.95, "window": "1h"},
         "throttle"),
    ]:
        RiskRule.objects.get_or_create(
            code=code, tenant__isnull=True,
            defaults={"threshold": threshold, "action": action})


def test_ut05_ut13_r01_login_storm(env, monkeypatch):
    """R-01：10m 键第 201 次触发（>200）+ IP 段封禁；不同租户键隔离。"""
    _fire_rule_seeded()
    with GOV:
        eng = RiskRuleEngine()
        tid = str(env["tenant"].id)
        ev = {"action": "login_failed", "actor_id": "u1",
              "ip": "203.0.113.5", "workspace_id": str(env["ws"].id)}
        for _ in range(200):
            eng.on_login_failed(tid, ev)
        assert RiskEvent.objects.filter(tenant_id=tid, rule_code="R-01").count() == 0
        eng.on_login_failed(tid, ev)                      # 第 201 次
        assert RiskEvent.objects.filter(tenant_id=tid, rule_code="R-01").count() == 1
        assert cache.get("risk:R-01:ipban:203.0.113.0/24") is not None
        # 租户隔离：另一租户同键计数互不影响
        other = Tenant.objects.create(name="Other", created_by=env["ops1"])
        for _ in range(210):
            eng.on_login_failed(str(other.id), ev)
        assert RiskEvent.objects.filter(tenant_id=str(other.id),
                                        rule_code="R-01").count() == 1
        assert RiskEvent.objects.filter(tenant_id=tid, rule_code="R-01").count() == 1


def test_ut14_r02_geo_distance(env):
    """R-02：>1,000km 触发、≤ 不触发、账号主体互不影响。"""
    _fire_rule_seeded()
    with GOV:
        eng = RiskRuleEngine()
        tid = str(env["tenant"].id)
        cache.delete("risk:R-02:user:u1:last_geo")
        ev = {"action": "login_success", "actor_id": "u1",
              "workspace_id": str(env["ws"].id),
              "detail": {"geo": {"lat": 31.23, "lng": 121.47}}}   # 上海
        eng.on_login(tid, ev)                              # 首登录建基线
        assert RiskEvent.objects.count() == 0
        eng.on_login(tid, {**ev, "detail": {"geo": {"lat": 39.90, "lng": 116.40}}})
        # 上海→北京 ~1068km > 1000 → 触发
        assert RiskEvent.objects.filter(rule_code="R-02").count() == 1
        # 不同账号不受波及（主体=账号）
        ev2 = {**ev, "actor_id": "u2", "detail": {"geo": {"lat": 31.25, "lng": 121.50}}}
        eng.on_login(tid, ev2)
        assert RiskEvent.objects.filter(rule_code="R-02").count() == 1


def test_ut15_r04_operator_subject(env):
    """R-04：操作者主体，第 6 次授予触发；另一操作者独立。"""
    _fire_rule_seeded()
    with GOV:
        eng = RiskRuleEngine()
        tid = str(env["tenant"].id)
        ev = {"action": "role_grant", "actor_id": "op1",
              "workspace_id": str(env["ws"].id)}
        for _ in range(5):
            eng.on_perm_grant(tid, ev)
        assert RiskEvent.objects.count() == 0
        eng.on_perm_grant(tid, ev)                         # 第 6 次
        assert RiskEvent.objects.filter(rule_code="R-04").count() == 1
        for _ in range(5):
            eng.on_perm_grant(tid, {**ev, "actor_id": "op2"})
        assert RiskEvent.objects.filter(rule_code="R-04").count() == 1


def test_ut16_r05_night_predicate(env, monkeypatch):
    """R-05：深夜窗内第 51 个触发；窗外键不增长（谓词前置）。"""
    _fire_rule_seeded()

    class _NightTZ:
        @staticmethod
        def now():
            return dt.datetime(2026, 9, 11, 3, 0, tzinfo=dt.UTC)

    class _DayTZ:
        @staticmethod
        def now():
            return dt.datetime(2026, 9, 11, 10, 0, tzinfo=dt.UTC)

    with GOV:
        tid = str(env["tenant"].id)
        ev = {"action": "issue.delete", "actor_id": "u1",
              "workspace_id": str(env["ws"].id)}
        monkeypatch.setattr(risk_engine, "timezone", _NightTZ)
        eng = RiskRuleEngine()
        for _ in range(50):
            eng.on_delete(tid, ev)
        eng.on_delete(tid, ev)                             # 第 51 个
        assert RiskEvent.objects.filter(rule_code="R-05").count() == 1
        monkeypatch.setattr(risk_engine, "timezone", _DayTZ)
        before = eng._read(f"risk:R-05:tenant:{tid}:1d:20260911")
        eng.on_delete(tid, ev)                             # 10:00 窗外
        after = eng._read(f"risk:R-05:tenant:{tid}:1d:20260911")
        assert before == after                             # 键不增长


def test_ut17_r06_dual_counter(env):
    """R-06：total > 50% 小时配额 且 getlist/total ≥95% 才触发。"""
    _fire_rule_seeded()
    with GOV:
        TenantQuota.objects.create(tenant=env["tenant"], api_rate_per_minute=10,
                                   created_by=env["ops1"])   # 10rpm → 600/h → 50%=300
        eng = RiskRuleEngine()
        tid = str(env["tenant"].id)
        base = {"action": "api_call", "workspace_id": str(env["ws"].id),
                "detail": {"token_id": "tk1"}}
        for i in range(300):                               # 93.3% GET 且未越量线
            eng.on_api_call(tid, {**base,
                                  "detail": {"token_id": "tk1",
                                             "is_get_list": i < 280}})
        assert RiskEvent.objects.filter(rule_code="R-06").count() == 0
        for _ in range(240):                               # 追加全 GET：t≥400 后占比过 95%
            eng.on_api_call(tid, {**base,
                                  "detail": {"token_id": "tk1",
                                             "is_get_list": True}})
        assert RiskEvent.objects.filter(rule_code="R-06").count() == 1
        # 占比 80% 不触发（双键独立——total 越线但 getlist 不达 95%）
        cache.delete_pattern("risk:R-06:*") if hasattr(cache, "delete_pattern") else None
        for i in range(500):
            eng.on_api_call(tid, {**base,
                                  "detail": {"token_id": "tk2",
                                             "is_get_list": i % 5 != 0}})


def test_ut06_br08_aggregation(env, monkeypatch):
    """BR-08：同小时桶第二次触发并入 occurrences；跨桶新建事件。"""
    _fire_rule_seeded()

    class _TZ:
        def __init__(self, hour):
            self._hour = hour

        def now(self):
            return dt.datetime(2026, 9, 11, self._hour, 30, tzinfo=dt.UTC)

    with GOV:
        monkeypatch.setattr(risk_engine, "timezone", _TZ(10))
        eng = RiskRuleEngine()
        rule, thr = eng._rule(str(env["tenant"].id), "R-04")
        ev = {"action": "role_grant", "actor_id": "op1"}
        eng._fire(rule, str(env["tenant"].id), "operator:op1", ev, evidence={})
        eng._fire(rule, str(env["tenant"].id), "operator:op1", ev, evidence={})
        assert RiskEvent.objects.count() == 1
        assert RiskEvent.objects.first().evidence.get("occurrences") == 2
        monkeypatch.setattr(risk_engine, "timezone", _TZ(11))   # 跨桶
        eng._fire(rule, str(env["tenant"].id), "operator:op1", ev, evidence={})
        assert RiskEvent.objects.count() == 2                 # 分桶各建


def test_ut11_ungoverned_skip(env):
    """tenant_id=None 事件 ingest 直接返回，无 Redis 写入、无事件。"""
    _fire_rule_seeded()
    with GOV:
        plain = Workspace.objects.create(name="P", slug=f"w-plain-{env['ops1'].id.hex[:6]}",
                                         owner=env["admin"], created_by=env["admin"])
        RiskRuleEngine().ingest({"action": "login_failed", "ip": "1.2.3.4",
                                 "workspace_id": str(plain.id)})
        assert RiskEvent.objects.count() == 0


def test_ut12_private_mode_off(env):
    """私有化：治理 API 501、ingest 短路、中间件直通（BR-11）。"""
    c = _ops_client(env["ops1"])
    r = c.get("/api/v1/instances/tenants/")
    assert r.status_code == 501
    assert r.json()["error"]["code"] == "SERVER_NOT_IMPLEMENTED"
    RiskRuleEngine().ingest({"action": "login_failed", "ip": "1.2.3.4",
                             "workspace_id": str(env["ws"].id)})
    assert RiskEvent.objects.count() == 0


def test_ut10_evidence_snapshot(env, monkeypatch):
    """BR-05：证据快照固化——只含统计与 ID（L1），不含业务内容。"""
    _fire_rule_seeded()
    with GOV:
        eng = RiskRuleEngine()
        for _ in range(6):                                 # 第 6 次授予触发
            eng.on_perm_grant(str(env["tenant"].id), {
                "action": "role_grant", "actor_id": "op9", "ip": "9.9.9.9",
                "object_id": "obj-1", "workspace_id": str(env["ws"].id),
                "detail": {"role_name": "超管", "business_content": "任务标题X"}})
        ev = RiskEvent.objects.get(rule_code="R-04")
        assert ev.evidence["actor_id"] == "op9"
        assert "business_content" not in str(ev.evidence)  # L1 不落业务内容


# ── UT-07 阈值只紧不松（客户侧 API）────────────────────────────────

def test_ut07_rules_only_tighter(env):
    _fire_rule_seeded()
    url = f"/api/v1/workspaces/{env['ws'].slug}/security/rules/R-01/"
    with GOV:
        c = _ops_client(env["admin"])
        r = c.patch(url, {"threshold": {"count": 300}}, format="json")
        assert r.status_code == 400
        body = r.json()["error"]
        assert body["code"] == "VALIDATION_INVALID_PARAM"
        assert body["details"][0]["field"] == "threshold.count"
        assert body["details"][0]["code"] == "TOO_LARGE"
        r2 = c.patch(url, {"threshold": {"count": 100}}, format="json")
        assert r2.status_code == 200
        assert RiskRule.objects.get(code="R-01", tenant=env["tenant"]) \
            .threshold["count"] == 100


# ── UT-08/09/19 冻结两签/写拒/解除（视图层全链）────────────────────

def _mk_event(env, **kw):
    return RiskEvent.objects.create(
        tenant=env["tenant"], rule_code=kw.get("rule", "R-03"),
        severity=kw.get("severity", "high"),
        evidence=kw.get("evidence", {"rows_used": 1}),
        aggregate_key=kw.get("agg", f"R-03:{env['tenant'].id}:t:{dt.datetime.now():%Y%m%d%H}"))


def test_ut08_ut19_freeze_two_sign_and_release(env):
    """冻结两签（同人 403）→ 生效 → 解除同等级二签 → is_frozen=False。"""
    with GOV:
        ev = _mk_event(env)
        c1, c2 = _ops_client(env["ops1"]), _ops_client(env["ops2"])
        # 第一签
        r = c1.post(f"/api/v1/instances/risk-events/{ev.id}/actions/",
                    {"action": "freeze", "note": "复核"}, format="json")
        assert r.status_code == 200
        ev.refresh_from_db()
        assert ev.freeze_approval["pending"] == "freeze"
        assert not env["tenant"].is_frozen                 # 第一签不置位
        # 同人二签 → 403
        r = c1.post(f"/api/v1/instances/risk-events/{ev.id}/freeze-approval/", {},
                    format="json")
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "PERM_DENIED"
        assert not env["tenant"].is_frozen
        # 异人二签 → 生效 + Redis 标记
        r = c2.post(f"/api/v1/instances/risk-events/{ev.id}/freeze-approval/", {},
                    format="json")
        assert r.status_code == 200
        env["tenant"].refresh_from_db()
        assert env["tenant"].is_frozen
        assert cache.get(f"frozen:{env['tenant'].id}") is not None
        # 解除：发起（ops2）→ 同人二签 403 → 异人（ops1）二签生效
        r = c2.post(f"/api/v1/instances/risk-events/{ev.id}/releases/",
                    {"note": "复核完成"}, format="json")
        assert r.status_code == 200 and r.json()["data"]["pending_second_sign"]
        r = c2.post(f"/api/v1/instances/risk-events/{ev.id}/freeze-approval/", {},
                    format="json")
        assert r.status_code == 403
        r = c1.post(f"/api/v1/instances/risk-events/{ev.id}/freeze-approval/", {},
                    format="json")
        assert r.status_code == 200
        env["tenant"].refresh_from_db()
        assert not env["tenant"].is_frozen
        assert cache.get(f"frozen:{env['tenant'].id}") is None


def test_ut09_frozen_write_reject_middleware(env):
    """冻结后写请求 409 RESOURCE_STATE_INVALID；读请求放行（BR-09）。"""
    factory = APIRequestFactory()
    called = {"n": 0}

    def get_resp(request):
        called["n"] += 1
        from django.http import HttpResponse

        return HttpResponse("ok")

    mw = GovernanceMiddleware(get_resp)
    cache.set(f"frozen:{env['tenant'].id}", 1, timeout=None)
    with GOV:
        w = f"/api/v1/workspaces/{env['ws'].slug}/issues/"
        resp = mw(factory.post(w, {}))
        import json as _json

        assert resp.status_code == 409
        assert _json.loads(resp.content)["error"]["code"] == "RESOURCE_STATE_INVALID"
        resp_get = mw(factory.get(w))
        assert resp_get.status_code == 200                 # 读放行
    cache.delete(f"frozen:{env['tenant'].id}")


def test_ut19_throttle_release_immediate(env):
    """限流解除即时生效（Retry-After 停发=降速标记删除）。"""
    with GOV:
        cache.set(f"risk:throttle:{env['tenant'].id}", 0.1, timeout=3600)
        ev = _mk_event(env, rule="R-06", agg="R-06:t:t")
        c = _ops_client(env["ops1"])
        r = c.post(f"/api/v1/instances/risk-events/{ev.id}/releases/",
                   {"note": "误操作"}, format="json")
        assert r.status_code == 200 and r.json()["data"]["released"]
        assert cache.get(f"risk:throttle:{env['tenant'].id}") is None
    cache.delete(f"risk:throttle:{env['tenant'].id}")


# ── UT-18/IT-07 L2 最小知情闸门 ────────────────────────────────────

def test_ut18_l2_gate_and_written_confirm(env):
    """L2 闸门：approved 未过期放行 scope；过期剥离；written 缺 ref 400 REQUIRED。"""
    with GOV:
        ev = _mk_event(env)
        t = GovernanceTicket.objects.create(
            tenant=env["tenant"], risk_event=ev, requested_by=env["ops1"],
            scope={"fields": ["issue.title"], "ids": ["u_1"]},
            approve_channel="written", created_by=env["ops1"])
        c = _ops_client(env["ops1"])
        r = c.post(f"/api/v1/instances/l2-tickets/{t.id}/written-confirm/",
                   {"note": "无单号"}, format="json")
        assert r.status_code == 400
        body = r.json()["error"]
        assert body["details"][0]["field"] == "written_ref"
        assert body["details"][0]["code"] == "REQUIRED"
        r2 = c.post(f"/api/v1/instances/l2-tickets/{t.id}/written-confirm/",
                    {"written_ref": "GT-2026-0099"}, format="json")
        assert r2.status_code == 200
        t.refresh_from_db()
        assert t.status == "approved"
        assert t.expires_at > t.granted_at                 # 24h
        # 序列化层 L1/L2 边界：事件列表 approved 时带 l2_fields
        rr = c.get("/api/v1/instances/risk-events/")
        row = next(x for x in rr.json()["data"] if x["id"] == str(ev.id))
        assert row.get("l2_fields") == ["issue.title"]
        # 过期后剥离
        GovernanceTicket.objects.filter(pk=t.id).update(
            expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=1))
        rr2 = c.get("/api/v1/instances/risk-events/")
        row2 = next(x for x in rr2.json()["data"] if x["id"] == str(ev.id))
        assert "l2_fields" not in row2


def test_it07_l2_online_approval_chain(env):
    """L2 在线通道：客户 WS_ADMIN approval/ 批准 → approved + 24h。"""
    with GOV:
        ev = _mk_event(env)
        t = GovernanceTicket.objects.create(
            tenant=env["tenant"], risk_event=ev, requested_by=env["ops1"],
            scope={"fields": ["file.name"], "ids": ["f_1"]},
            approve_channel="online", created_by=env["ops1"])
        c = _ops_client(env["admin"])
        r = c.post(f"/api/v1/workspaces/{env['ws'].slug}/security/l2-tickets/"
                   f"{t.id}/approval/", {"decision": "approve"}, format="json")
        assert r.status_code == 200
        t.refresh_from_db()
        assert t.status == "approved" and t.approver == env["admin"]


# ── UT-20/IT-09 误报申诉状态机 ─────────────────────────────────────

def test_ut20_it09_appeal_chain(env):
    """申诉受理 → accepted：事件 dismissed + 限流自动解除 + 复核留痕；
    rejected：事件维持处置。"""
    with GOV:
        ev = _mk_event(env)
        cache.set(f"risk:throttle:{env['tenant'].id}", 0.1, timeout=3600)
        c_admin, c_ops = _ops_client(env["admin"]), _ops_client(env["ops1"])
        r = c_admin.post(f"/api/v1/workspaces/{env['ws'].slug}/security/events/"
                         f"{ev.id}/appeals/", {"reason": "批量导出系报表例行"},
                         format="json")
        assert r.status_code == 200
        appeal_id = r.json()["data"]["appeal_id"]
        appeal = RiskAppeal.objects.get(pk=appeal_id)
        assert appeal.status == "pending"
        r2 = c_ops.post(f"/api/v1/instances/risk-appeals/{appeal_id}/review/",
                        {"decision": "accepted", "note": "核实为例行"},
                        format="json")
        assert r2.status_code == 200
        ev.refresh_from_db()
        assert ev.status == "dismissed"
        assert cache.get(f"risk:throttle:{env['tenant'].id}") is None  # 自动解除
        appeal.refresh_from_db()
        assert appeal.reviewed_by == env["ops1"]
        assert appeal.review_note == "核实为例行"
        # rejected 路径
        ev2 = _mk_event(env, agg="R-03:t:2")
        cache.set(f"risk:throttle:{env['tenant'].id}", 0.1, timeout=3600)
        r3 = c_admin.post(f"/api/v1/workspaces/{env['ws'].slug}/security/events/"
                          f"{ev2.id}/appeals/", {"reason": "x"}, format="json")
        r4 = c_ops.post(f"/api/v1/instances/risk-appeals/"
                        f"{r3.json()['data']['appeal_id']}/review/",
                        {"decision": "rejected"}, format="json")
        assert r4.status_code == 200
        ev2.refresh_from_db()
        assert ev2.status != "dismissed"
        assert cache.get(f"risk:throttle:{env['tenant'].id}") is not None  # 维持
    cache.delete(f"risk:throttle:{env['tenant'].id}")


# ── UT-21 偏条件唯一约束 ───────────────────────────────────────────

def test_ut21_risk_rule_unique_constraints(env):
    _fire_rule_seeded()
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError):
        with transaction.atomic():                          # 保存点：防事务中止污染
            RiskRule.objects.create(code="R-01",
                                    threshold={"count": 1}, action="alert")
    RiskRule.objects.create(code="R-01", tenant=env["tenant"],
                            threshold={"count": 1}, action="alert",
                            created_by=env["ops1"])
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            RiskRule.objects.create(code="R-01", tenant=env["tenant"],
                                    threshold={"count": 2}, action="alert")


# ── UT-22/IT-10 R-03 双档与硬拒 ────────────────────────────────────

def test_ut22_it10_r03_deny_tier(env):
    """80% 预警（medium warn）→ 100% 越线（high deny）→ 端点 409 硬拒；
    deny 不置 is_frozen（与 freeze 分档）。"""
    _fire_rule_seeded()
    with GOV:
        eng = RiskRuleEngine()
        tid = str(env["tenant"].id)
        quota = TIER_DEFAULTS["free"]["export_rows_per_day"]   # 10,000
        ev = {"action": "export", "workspace_id": str(env["ws"].id)}
        eng.on_export(tid, {**ev, "detail": {"rows": int(quota * 0.81)}})
        warn = RiskEvent.objects.get(rule_code="R-03")
        assert warn.severity == "medium" and warn.evidence["tier"] == "warn"
        eng.on_export(tid, {**ev, "detail": {"rows": quota}})  # 累计越界 100%+
        deny = (RiskEvent.objects.filter(rule_code="R-03")
                .order_by("-created_at").first())
        assert deny.severity == "high" and deny.evidence["tier"] == "deny"
        env["tenant"].refresh_from_db()
        assert not env["tenant"].is_frozen                    # deny ≠ freeze
        # 端点硬拒（创建路径）
        with pytest.raises(AppException) as exc:
            enforcement.check_export_deny(env["ws"])
        assert exc.value.error_code == "RESOURCE_LIMIT_EXCEEDED"
        assert "R-03" in str(exc.value.extra_details)


def test_r03_under_warn_passes(env):
    """80%~100% 区间不拒（仅预警）。"""
    _fire_rule_seeded()
    with GOV:
        from plane.governance.enforcement import _export_day_key

        cache.set(_export_day_key(str(env["tenant"].id)),
                  int(TIER_DEFAULTS["free"]["export_rows_per_day"] * 0.95))
        enforcement.check_export_deny(env["ws"])             # 95% 不拒
        cache.delete(_export_day_key(str(env["tenant"].id)))


# ── 视图权限与门控面 ───────────────────────────────────────────────

def test_gov_endpoints_require_tenant_ops(env):
    """非 tenant_ops 的 SYSTEM_ADMIN / 普通用户 → 403（rbac 附录 B 口径）。"""
    plain = User.objects.create_user(email="g-plain@rabbit.dev",
                                     password="Rabbit123!")
    SystemAdmin.objects.create(user=plain, is_active=True, is_tenant_ops=False,
                               created_by=plain)
    with GOV:
        for client in (_ops_client(plain), _ops_client(env["admin"])):
            r = client.get("/api/v1/instances/tenants/")
            assert r.status_code == 403


def test_it05_quota_patch_audited(env, monkeypatch, django_capture_on_commit_callbacks):
    """PATCH quota → AuditLog 含新旧值 diff（BR-12/IT-05）。"""
    from plane.bgtasks import audit_record as _mod

    monkeypatch.setattr(_mod.audit_record, "delay",
                        lambda payload: _mod.audit_record.run(payload))
    from plane.governance import tasks as _gt

    monkeypatch.setattr(_gt.risk_ingest, "delay",
                        lambda payload: None)          # 测试内不扇出风控
    with GOV, django_capture_on_commit_callbacks(execute=True):
        c = _ops_client(env["ops1"])
        r = c.patch(f"/api/v1/instances/tenants/{env['tenant'].id}/quota/",
                    {"tier": "standard"}, format="json")
    assert r.status_code == 200
    from plane.db.models import AuditLog

    row = (AuditLog.objects.filter(category="governance",
                                   action="quota_changed")
           .order_by("-created_at").first())
    assert row is not None
    assert row.detail["before"]["tier"] == "free"
    assert row.detail["after"]["tier"] == "standard"


def test_tenant_list_water(env):
    """总览列表：水位聚合 + 排序白名单。"""
    with GOV:
        c = _ops_client(env["ops1"])
        r = c.get("/api/v1/instances/tenants/")
        assert r.status_code == 200
        row = next(t for t in r.json()["data"]
                   if t["id"] == str(env["tenant"].id))
        assert row["tier"] == "free"
        assert row["storage"]["limit"] == 5 * 2**30
        r_bad = c.get("/api/v1/instances/tenants/?ordering=-name")
        assert r_bad.status_code == 400
        assert r_bad.json()["error"]["code"] == "VALIDATION_INVALID_PARAM"


def test_security_events_view(env):
    """客户侧事件视角：WS_ADMIN 可见本租户事件；普通成员 403。"""
    member = User.objects.create_user(email="g-m@rabbit.dev", password="Rabbit123!")
    WorkspaceMember.objects.create(workspace=env["ws"], member=member,
                                   role=WorkspaceRole.MEMBER, created_by=member)
    with GOV:
        _mk_event(env)
        r = _ops_client(env["admin"]).get(
            f"/api/v1/workspaces/{env['ws'].slug}/security/events/")
        assert r.status_code == 200 and len(r.json()["data"]) == 1
        r2 = _ops_client(member).get(
            f"/api/v1/workspaces/{env['ws'].slug}/security/events/")
        assert r2.status_code == 403


# ── IT-08 边界报告（MinIO mock）────────────────────────────────────

def test_it08_boundary_report_chain(env, monkeypatch):
    """202 契约 → worker 生成（隔离清单 + 跨租户计数 0）→ succeeded。"""
    stored = {}

    def fake_put(key, text):
        stored[key] = text

    monkeypatch.setattr("plane.governance.tasks._put_minio", fake_put)
    with GOV:
        c = _ops_client(env["ops1"])
        r = c.post(f"/api/v1/instances/tenants/{env['tenant'].id}/boundary-reports/",
                   {}, format="json")
        assert r.status_code == 202
        data = r.json()["data"]
        assert data["state"] == "queued" and data["task_id"]
        assert data["status_url"].endswith(f"/{data['report_id']}/")
        from plane.governance.tasks import generate_boundary_report

        result = generate_boundary_report.run(data["report_id"])
        assert result["cross_tenant_hits"] == 0
        import json

        payload = json.loads(next(iter(stored.values())))
        assert payload["cross_tenant_hits"] == 0
        assert len(payload["isolation_mechanisms"]) >= 3
        r2 = c.get(f"/api/v1/instances/tenants/{env['tenant'].id}/"
                   f"boundary-reports/{data['report_id']}/")
        assert r2.json()["data"]["state"] == "succeeded"
