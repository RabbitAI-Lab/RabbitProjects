"""发布门禁服务（QA-001 §4.4——append-only 事件流 + 裁决守卫）。

checklist 8 键单一来源 = INFRA-005 §4.5.4（本模块常量仅引用口径，防双源漂移）。
裁决规则：放行须四门禁 passed 且 8 项 checklist 全签且无反签未重签。
"""
from __future__ import annotations

from django.utils import timezone

from plane.base.exception import AppException
from plane.db.models import ReleaseGate, ReleaseGateEvent

#: INFRA-005 §4.5.4 发布 checklist 恰 8 项（QA-001 §3.1 引用同源）
CHECKLIST_KEYS = (
    "preflight", "image_scan", "migration_drill", "restore_drill",
    "rate_limit_config", "backup_streak", "perf_baseline", "e2e_matrix",
)
GATES = ("gate_defects", "gate_perf", "gate_security", "gate_compat")


def append_event(gate: ReleaseGate, event_type: str, payload: dict,
                 actor=None) -> ReleaseGateEvent:
    return ReleaseGateEvent.objects.create(
        gate=gate, event_type=event_type, payload=payload, actor=actor)


def create_release_gate(*, version: str, commit_sha: str, actor=None) -> ReleaseGate:
    """创建发布尝试（同 version+sha 幂等返回既有行——CI 重试语义）。"""
    existing = ReleaseGate.objects.filter(version=version, commit_sha=commit_sha).first()
    if existing is not None:
        return existing
    gate = ReleaseGate.objects.create(
        version=version, commit_sha=commit_sha,
        created_by=actor, updated_by=actor)
    append_event(gate, "created", {"version": version, "commit_sha": commit_sha},
                 actor=actor)
    return gate


def update_gate(gate: ReleaseGate, *, gate_name: str, status: str,
                artifacts: dict | None = None, actor=None) -> tuple[ReleaseGate, int]:
    """CI 回调：更新一门禁状态（合法名/态校验）+ 快照同事务更新 + 追加事件。"""
    if gate_name not in GATES:
        raise AppException("VALIDATION_ERROR", message="请求参数校验失败",
                            details=[{"field": "gate", "code": "NOT_A_CHOICE",
                                      "message": f"must be one of: {', '.join(GATES)}"}])
    if status not in ReleaseGate.Status.values:
        raise AppException("VALIDATION_ERROR", message="请求参数校验失败",
                            details=[{"field": "status", "code": "NOT_A_CHOICE",
                                      "message": "must be one of: pending/running/passed/blocked"}])
    setattr(gate, gate_name, status)
    if artifacts:
        gate.artifacts = {**(gate.artifacts or {}), gate_name: artifacts}
    gate.save(update_fields=[gate_name, "artifacts", "updated_at"])
    event = append_event(gate, "gate_update",
                         {"gate": gate_name, "status": status, "artifacts": artifacts or {}},
                         actor=actor)
    return gate, event.id


def sign_checklist(gate: ReleaseGate, *, key: str, signed: bool,
                   note: str = "", actor=None) -> ReleaseGate:
    """签署/反签 checklist 项（signed=false 即反签——append 追加 revoked 事件，
    不删原签署记录；幂等键 = (gate, key, actor, signed)，重复即幂等返回）。"""
    if key not in CHECKLIST_KEYS:
        raise AppException("VALIDATION_ERROR", message="请求参数校验失败",
                            details=[{"field": "key", "code": "NOT_A_CHOICE",
                                      "message": f"must be one of: {', '.join(CHECKLIST_KEYS)}"}])
    now = timezone.now().isoformat()
    actor_name = getattr(actor, "display_name", None) or (str(actor.id) if actor else "ci")
    entry = next((c for c in gate.checklist if c["key"] == key), None)
    if entry is None:
        entry = {"key": key, "records": []}
        gate.checklist = [*gate.checklist, entry]
    records: list[dict] = entry.get("records", [])
    # 幂等仅对「与最后一条相同」的重放生效——sign→revoke→sign 是合法时序，
    # 严格 (gate,key,actor,signed) 去重会挡死重签（QA-001 §4.4 反签语义）
    if records and records[-1]["actor"] == actor_name \
            and records[-1]["signed"] is signed:
        return gate
    entry["records"] = [*records, {"actor": actor_name, "at": now,
                                   "signed": signed, "note": note}]
    # 反签即 revoked 标记（最后一条生效；原签署记录保留——append-only 审计）
    entry["revoked"] = not signed
    gate.save(update_fields=["checklist", "updated_at"])
    append_event(gate, "checklist_sign" if signed else "checklist_revoke",
                 {"key": key, "note": note, "actor": actor_name}, actor=actor)
    return gate


def _unpassed_gates(gate: ReleaseGate) -> list[str]:
    return [g for g in GATES if getattr(gate, g) != ReleaseGate.Status.PASSED]


def _unsigned_keys(gate: ReleaseGate) -> list[str]:
    """有效签署 = 最后一条 record 为 signed=True（反签在后即失效）。"""
    def _signed(c: dict) -> bool:
        recs = c.get("records") or []
        return bool(recs) and recs[-1]["signed"] is True
    signed = {c["key"] for c in gate.checklist if _signed(c)}
    return [k for k in CHECKLIST_KEYS if k not in signed]


def set_verdict(gate: ReleaseGate, *, verdict: str, note: str = "",
                actor=None) -> ReleaseGate:
    """发布评审裁决（放行/打回）。放行守卫：四门禁全过 + 8 项全签（BR 门禁
    二/§2.6）；打回无条件允许（打回是安全方向）。"""
    if verdict not in ("approved", "rejected"):
        raise AppException("VALIDATION_ERROR", message="请求参数校验失败",
                            details=[{"field": "verdict", "code": "NOT_A_CHOICE",
                                      "message": "must be one of: approved, rejected"}])
    unpassed = _unpassed_gates(gate)
    unsigned = _unsigned_keys(gate)
    if verdict == "approved" and (unpassed or unsigned):
        blocked = unpassed or unsigned
        raise AppException(
            "VALIDATION_ERROR", message="存在未通过的门禁，不可裁决放行",
            details=[{"field": "verdict", "code": "BLOCKED_BY_GATE",
                      "message": f"未达标：{','.join(blocked) if unpassed else ''}"
                                 f"{'未签署:' + ','.join(unsigned) if unsigned else ''}".strip()}])
    gate.verdict = verdict
    gate.save(update_fields=["verdict", "updated_at"])
    append_event(gate, "verdict", {"verdict": verdict, "note": note}, actor=actor)
    return gate
