"""INFRA-005 §5.1 限流单元/集成用例（UT-01~07 + UT-13 + 守卫断言）。

L2 全局四类与 AuthBurst 受 ``RATE_LIMIT_ENABLED`` 门控（throttling.py 模块
docstring 偏差 1），故 L2 相关用例以 ``override_settings`` 显式开启——pytest
全局保持关闭（既有 574 测与 flow 脚本不被全局 60/min 打爆）；收编类
（Report/Bulk/ShareUnlock）不受门控，语义用例直接跑。

计数隔离：LocMem cache 按用例前后 ``cache.clear()``（坑 18 同纪律）。
"""
from __future__ import annotations

import logging
import time
import uuid
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.test import override_settings
from rest_framework.exceptions import Throttled
from rest_framework.test import APIClient, APIRequestFactory

from plane.base import throttling
from plane.base.throttling import (
    AnonRateThrottle,
    ApiKeyRateThrottle,
    RedisRateThrottle,
    ReportRateThrottle,
    ShareUnlockRateThrottle,
    UserRateThrottle,
)
from plane.db.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _cache_clean():
    cache.clear()
    yield
    cache.clear()


def _auth_client(user: User) -> APIClient:
    c = APIClient()
    c.force_authenticate(user)
    return c


def _fake_view(slug: str = "") -> SimpleNamespace:
    return SimpleNamespace(kwargs={"slug": slug})


# ── UT-01/UT-06：用户桶 60/min + 头装配（L2 端到端）───────────────────

@override_settings(RATE_LIMIT_ENABLED=True)
def test_ut01_user_bucket_60_per_min_61st_429(db):
    user = User.objects.create_user(email="rl-u1@rabbit.dev", password="Rabbit123!")
    c = _auth_client(user)
    codes = [c.get("/api/v1/users/me/").status_code for _ in range(61)]
    assert codes[:60] == [200] * 60, f"前 60 次应全 200：{codes}"
    r61 = c.get("/api/v1/users/me/")
    assert r61.status_code == 429
    body = r61.json()
    assert body["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert body["error"]["details"][0]["field"] == "retry_after"   # §7.3 模板
    wait = int(r61.headers["Retry-After"])
    assert wait >= 1
    assert r61.headers["X-RateLimit-Limit"] == "60"
    assert r61.headers["X-RateLimit-Remaining"] == "0"
    assert int(r61.headers["X-RateLimit-Reset"]) > time.time() - 1


@override_settings(RATE_LIMIT_ENABLED=True)
def test_ut06_headers_on_success_response(db):
    """BR-02：成功响应三头为真值（非 -1 占位）。"""
    user = User.objects.create_user(email="rl-u6@rabbit.dev", password="Rabbit123!")
    r = _auth_client(user).get("/api/v1/users/me/")
    assert r.status_code == 200
    assert r.headers["X-RateLimit-Limit"] == "60"
    assert int(r.headers["X-RateLimit-Remaining"]) < 60   # 本请求已计数
    assert int(r.headers["X-RateLimit-Reset"]) > time.time() - 1


# ── UT-02/UT-03/UT-04：桶隔离与窗口（类级）───────────────────────────

def _req(user=None, api_key_id=None):
    req = APIRequestFactory().get("/api/v1/users/me/", REMOTE_ADDR="203.0.113.7")
    req.user = user or SimpleNamespace(is_authenticated=False)
    if api_key_id is not None:
        req.api_key_id = api_key_id
    return req


def _auth_user():
    return SimpleNamespace(is_authenticated=True, id=uuid.uuid4())


@override_settings(RATE_LIMIT_ENABLED=True)
def test_ut02_key_and_user_buckets_independent():
    """Key 与 Session 互不挤占：两桶键维度不同，各自独立计数（BR-04 顺序位）。
    需开 RATE_LIMIT_ENABLED——gated 类判定在门控位直接短路，关态下计数断言
    会空转。"""
    user = _auth_user()
    ut, at = UserRateThrottle(), ApiKeyRateThrottle()
    # Session 桶灌满 60（第 61 次拒）：
    for _ in range(60):
        assert ut.allow_request(_req(user=user), None) is True
    assert ut.allow_request(_req(user=user), None) is False
    # 同人带 API Key：Key 桶独立计数，不受 Session 桶打满影响
    kreq = _req(user=user, api_key_id="key-1")
    assert at.get_cache_key(kreq, None) == "k:key-1"
    for _ in range(60):
        assert at.allow_request(kreq, None) is True
    assert at.allow_request(kreq, None) is False
    # 反向同理：Key 桶打满后 Session 桶已独立计数（各 60 各拒）
    assert ut.allow_request(_req(user=user), None) is False
    # api_key_id 恒缺（V1.0 无 APIKey 基建）→ Key 桶空转不计数
    assert at.get_cache_key(_req(user=user), None) is None


@override_settings(RATE_LIMIT_ENABLED=True)
def test_ut03_authenticated_not_in_anon_bucket():
    """BR-04：已认证请求 70 次 → 匿名桶零计数（不重复落桶）。
    需开 RATE_LIMIT_ENABLED——关态下匿名类在门控位短路，断言空转。"""
    user = _auth_user()
    anon = AnonRateThrottle()
    for _ in range(70):
        assert anon.allow_request(_req(user=user), None) is True
    anon_keys = [k for k in getattr(cache, "_cache", {}) if "rl:anon:" in str(k)]
    assert anon_keys == []


def test_ut04_fixed_window_rollover(monkeypatch):
    """固定窗口翻转：跨窗口第 11 次放行（新窗口）。用收编类 Report（不受
    门控），窗口语义与 L2 同基类同实现。"""
    t = {"now": 1_800_000_000}
    monkeypatch.setattr(throttling.time, "time", lambda: t["now"])
    user = _auth_user()
    th = ReportRateThrottle()
    codes = [th.allow_request(_req(user=user), None) for _ in range(11)]
    assert codes[:10] == [True] * 10
    assert codes[10] is False                       # 本窗口已满
    t["now"] += 61                                  # 跨入下一窗口
    assert th.allow_request(_req(user=user), None) is True


# ── UT-05：Redis 失联 fail-open ───────────────────────────────────────

def test_ut05_fail_open_on_cache_errors(monkeypatch, caplog):
    """cache.add / cache.incr 抛错（含窗口首请求路径）→ 放行 + 告警日志。
    用收编类 Report（不受门控），保证判定路径真实触达计数。"""
    user = _auth_user()
    with caplog.at_level(logging.WARNING, logger="plane.api.throttling"):
        # 路径 ①：窗口首请求（cache.add 抛错）
        monkeypatch.setattr(throttling.cache, "add", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
        th1 = ReportRateThrottle()
        assert th1.allow_request(_req(user=user), None) is True
        # 路径 ②：窗口内后续请求（cache.add 返回 False → cache.incr 抛错）
        monkeypatch.setattr(throttling.cache, "add", lambda *a, **k: False)
        monkeypatch.setattr(throttling.cache, "incr", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
        th2 = ReportRateThrottle()
        assert th2.allow_request(_req(user=user), None) is True
    assert any("rate_limit_degraded" in r.getMessage() for r in caplog.records)


# ── UT-07：白名单路径（BR-05）────────────────────────────────────────

@override_settings(RATE_LIMIT_ENABLED=True)
def test_ut07_health_endpoint_exempt(db):
    """健康端点高频不被限且不消耗配额；响应三头 -1 占位（BR-02 头永不缺席）。"""
    c = APIClient()
    codes = [c.get("/api/v1/health/").status_code for _ in range(80)]
    assert codes == [200] * 80
    r = c.get("/api/v1/health/")
    assert r.headers["X-RateLimit-Limit"] == "-1"
    assert r.headers["X-RateLimit-Remaining"] == "-1"
    assert r.headers["X-RateLimit-Reset"] == "-1"
    touched = [k for k in getattr(cache, "_cache", {}) if str(k).startswith("rl:")]
    assert touched == []                            # 未参与任何计数


# ── UT-13：分享解锁收编语义（FILE-004 §5.1 UT-13 同口径，类级）────────

def test_ut13_share_unlock_failure_only_and_clear():
    th = ShareUnlockRateThrottle()
    req = _req()
    view = _fake_view("share-slug-x")
    # 5 次失败计数内放行（查询不计数）
    for i in range(5):
        th.hit(req, view)
        if i < 4:
            assert th.allow_request(req, view) is True
    # 第 5 次失败后：第 6 次失败尝试 → Throttled（429 由 handlers 第 8 步装配）
    with pytest.raises(Throttled) as ei:
        th.allow_request(req, view)
    info = ei.value.rate_limit_info
    assert info == {"limit": 5, "remaining": 0, "reset": pytest.approx(
        int(time.time()) + 600, abs=601)}
    assert ei.value.wait >= 1
    # 成功清零：恢复满额
    th.clear(req, view)
    assert th.allow_request(req, view) is True
    assert th.remaining(req, view) == 5


def test_ut13_share_unlock_success_does_not_consume_quota():
    th = ShareUnlockRateThrottle()
    req = _req()
    view = _fake_view("share-slug-y")
    for _ in range(4):
        th.hit(req, view)                     # 4 次失败
    th.clear(req, view)                       # 成功解锁清零（不消耗配额）
    assert th.remaining(req, view) == 5
    assert th.allow_request(req, view) is True


# ── 守卫断言（规格 §4.3.2：「每个子类均有 get_cache_key 覆写」入 CI）──

def test_every_subclass_overrides_get_cache_key():
    subs = RedisRateThrottle.__subclasses__()
    expect = ("User", "ApiKey", "OAuth", "Anon", "AuthBurst",
              "Report", "Search", "Presign", "Bulk", "ShareUnlock")
    assert len(subs) >= len(expect), f"Throttle 家族应 ≥{len(expect)} 类（{expect}）"
    missing = [c.__name__ for c in subs if "get_cache_key" not in vars(c)]
    assert missing == [], f"未覆写 get_cache_key 的子类挂载即 500（非 429）：{missing}"


def test_report_throttle_ungated_semantics_unchanged():
    """收编类不受 RATE_LIMIT_ENABLED 门控（缺省 False 下仍生效）——与
    sprint-4/5 既有全环境生效面一致（jMeter S5-R4-03 依赖）。"""
    user = _auth_user()
    th = ReportRateThrottle()
    codes = [th.allow_request(_req(user=user), None) for _ in range(11)]
    assert codes[:10] == [True] * 10 and codes[10] is False
