"""发布门禁端点/服务用例（QA-001 §5.2 门禁面；§7.2.5 演示口径同源）。

覆盖：权限 403 / 创建幂等 / 门禁回调校验与快照 / 签署-反签-重签时序 /
裁决守卫（BLOCKED_BY_GATE）/ 事件时间线 append-only。
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from plane.db.models import ReleaseGateEvent, SystemAdmin, User
from plane.db.services import release_gate as svc

pytestmark = pytest.mark.django_db

BASE = "/api/v1/instances/release-gates"


@pytest.fixture()
def env(db):
    admin = User.objects.create_user(email="rg-admin@rabbit.dev", password="Rabbit123!", display_name="发布管")
    SystemAdmin.objects.create(user=admin)
    outsider = User.objects.create_user(email="rg-out@rabbit.dev", password="Rabbit123!")
    return {"admin": admin, "outsider": outsider}


def _c(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user)
    return c


def _mk_gate(env, version="1.0.0", sha="a" * 40):
    return svc.create_release_gate(version=version, commit_sha=sha, actor=env["admin"])


def test_permission_403_and_list(env):
    assert _c(env["outsider"]).get(BASE + "/").status_code == 403
    _mk_gate(env)
    r = _c(env["admin"]).get(BASE + "/")
    assert r.status_code == 200 and r.json()["data"][0]["version"] == "1.0.0"


def test_create_idempotent(env):
    r1 = _c(env["admin"]).post(BASE + "/create/", {"version": "1.0.0", "commit_sha": "b" * 40}, format="json")
    assert r1.status_code == 201 and r1.headers["Location"].endswith(r1.json()["data"]["id"] + "/")
    r2 = _c(env["admin"]).post(BASE + "/create/", {"version": "1.0.0", "commit_sha": "b" * 40}, format="json")
    assert r2.json()["data"]["id"] == r1.json()["data"]["id"]  # 幂等
    assert _c(env["admin"]).post(BASE + "/create/", {"version": "", "commit_sha": ""}, format="json").status_code == 400


def test_gate_update_validation_and_snapshot(env):
    gate = _mk_gate(env)
    url = f"{BASE}/{gate.id}/gate-events/"
    assert _c(env["admin"]).post(url, {"gate": "nope", "status": "passed"}, format="json").status_code == 400
    assert _c(env["admin"]).post(url, {"gate": "gate_perf", "status": "green"}, format="json").status_code == 400
    r = _c(env["admin"]).post(url, {"gate": "gate_perf", "status": "passed", "artifacts": {"rounds": 2}}, format="json")
    assert r.status_code == 200
    gate.refresh_from_db()
    assert gate.gate_perf == "passed" and gate.artifacts["gate_perf"]["rounds"] == 2
    assert ReleaseGateEvent.objects.filter(gate=gate, event_type="gate_update").count() == 1


def _full_sign(gate, env):
    for k in svc.CHECKLIST_KEYS:
        svc.sign_checklist(gate, key=k, signed=True, actor=env["admin"])
    for g in svc.GATES:
        svc.update_gate(gate, gate_name=g, status="passed", actor=env["admin"])


def test_sign_revoke_resign_flow(env):
    gate = _mk_gate(env)
    url = f"{BASE}/{gate.id}/checklist/perf_baseline/sign/"
    r = _c(env["admin"]).post(url, {"signed": True, "note": "两轮已归档"}, format="json")
    assert r.status_code == 200
    # 反签（signed=false → revoked 事件；原记录保留）
    r = _c(env["admin"]).post(url, {"signed": False, "note": "报告数据存疑"}, format="json")
    assert r.status_code == 200
    types = list(ReleaseGateEvent.objects.filter(gate=gate).values_list("event_type", flat=True))
    assert "checklist_sign" in types and "checklist_revoke" in types
    # 重签恢复
    _c(env["admin"]).post(url, {"signed": True}, format="json")
    gate.refresh_from_db()
    recs = next(c for c in gate.checklist if c["key"] == "perf_baseline")["records"]
    assert len(recs) == 3 and recs[-1]["signed"] is True
    # 非法 key 400
    assert (
        _c(env["admin"]).post(f"{BASE}/{gate.id}/checklist/no_such/sign/", {"signed": True}, format="json").status_code
        == 400
    )


def test_verdict_guard_blocked_by_gate(env):
    gate = _mk_gate(env)
    _full_sign(gate, env)  # 全签但门禁未全过
    gate.gate_compat = "blocked"
    gate.save()
    r = _c(env["admin"]).post(f"{BASE}/{gate.id}/verdict/", {"verdict": "approved"}, format="json")
    assert r.status_code == 400
    detail = r.json()["error"]["details"][0]
    assert detail["code"] == "BLOCKED_BY_GATE" and "gate_compat" in detail["message"]
    # 打回无条件允许
    assert (
        _c(env["admin"]).post(f"{BASE}/{gate.id}/verdict/", {"verdict": "rejected"}, format="json").status_code == 200
    )


def test_verdict_approved_when_all_green(env):
    gate = _mk_gate(env)
    _full_sign(gate, env)
    r = _c(env["admin"]).post(
        f"{BASE}/{gate.id}/verdict/", {"verdict": "approved", "note": "评审会放行"}, format="json"
    )
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["verdict"] == "approved"
    # 事件时间线完整可查（详情端点）
    detail = _c(env["admin"]).get(f"{BASE}/{gate.id}/").json()["data"]
    assert {e["event_type"] for e in detail["events"]} >= {"created", "gate_update", "checklist_sign", "verdict"}
    assert _c(env["outsider"]).get(f"{BASE}/{gate.id}/").status_code == 403
