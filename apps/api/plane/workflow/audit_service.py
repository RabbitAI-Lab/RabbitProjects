"""审批留痕服务（WF-006 §4.2/§4.3）——哈希链 append + 检索 + 异步导出。

哈希链（§2.2）：event_hash = sha256(prev_hash | project_id | type | actor_id |
created_at | payload_json)；项目内单链（pg_advisory_xact_lock 串行化）。
审计事件经触发器强制只增（BR-01）。
"""
from __future__ import annotations

import hashlib
import json
import logging

from django.db import connection, transaction
from django.utils import timezone

from plane.db.models import ApprovalAuditEvent, ApprovalInstance, Project, User

logger = logging.getLogger(__name__)

#: §2.1 审计事件清单（十类）
AUDIT_TYPES = (
    "approval.started", "approval.level_opened", "approval.approved",
    "approval.rejected", "approval.withdrawn", "approval.terminated",
    "approval.timeout_reminded", "approval.record_skipped",
    "approval.instance_state_changed", "approval.exported",
)


def _event_hash(prev_hash: str, project_id, type_: str, actor_id, created_at, payload: dict) -> str:
    raw = "|".join([
        prev_hash, str(project_id), type_, str(actor_id or "system"),
        created_at.isoformat(), json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)])
    return hashlib.sha256(raw.encode()).hexdigest()


@transaction.atomic
def append_audit_event(*, project: Project, type_: str, instance: ApprovalInstance | None,
                       actor: User | None, payload: dict | None = None,
                       request_id: str = "") -> ApprovalAuditEvent:
    """追加审计事件（项目内单链，advisory lock 串行化——§2.2 篡改检测基线）。"""
    payload = payload or {}
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [f"aae:{project.id}"])
    prev = (ApprovalAuditEvent.objects.filter(project=project)
            .only("event_hash").order_by("-id").first())
    prev_hash = prev.event_hash if prev else "0" * 64
    created_at = timezone.now()
    event_hash = _event_hash(prev_hash, project.id, type_,
                             actor.id if actor else None, created_at, payload)
    event = ApprovalAuditEvent(
        project=project, type=type_, instance=instance, actor=actor,
        payload=payload, request_id=request_id[:32],
        prev_hash=prev_hash, event_hash=event_hash, created_at=created_at)
    event.save()
    return event


def verify_chain(project: Project) -> list[int]:
    """全链校验（§2.2 篡改检测）：返回断链事件 id 列表（空 = 完整）。"""
    broken: list[int] = []
    prev_hash = "0" * 64
    for e in ApprovalAuditEvent.objects.filter(project=project).order_by("id"):
        expect = _event_hash(prev_hash, project.id, e.type,
                             e.actor_id, e.created_at, e.payload)
        if expect != e.event_hash or e.prev_hash != prev_hash:
            broken.append(e.id)
        prev_hash = e.event_hash
    return broken


def query_events(project: Project, *, instance_id=None, actor_id=None,
                 type_: str | None = None):
    """检索（§2.3）：project 必带；instance/actor/type 可选过滤。"""
    qs = ApprovalAuditEvent.objects.filter(project=project).select_related(
        "instance", "actor").order_by("-id")
    if instance_id:
        qs = qs.filter(instance_id=instance_id)
    if actor_id:
        qs = qs.filter(actor_id=actor_id)
    if type_:
        qs = qs.filter(type=type_)
    return qs


def export_rows_to_csv(events) -> str:
    """导出 CSV（§2.3：审计列全集；流式由端点 FileWrapper 承载）。"""
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "created_at", "type", "instance_id", "actor",
                "payload", "request_id", "event_hash"])
    for e in events:
        w.writerow([e.id, e.created_at.isoformat(), e.type,
                    str(e.instance_id) if e.instance_id else "",
                    str(e.actor_id) if e.actor_id else "system",
                    json.dumps(e.payload, ensure_ascii=False, default=str),
                    e.request_id, e.event_hash])
    return buf.getvalue()
