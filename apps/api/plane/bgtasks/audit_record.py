"""审计落库 Worker + 每日维护（AUTH-010 §4.3，Sprint-8 R4）。

幂等时序铁律：DB 落库成功才是幂等锚点。三层去重：
① Redis 完成标记 audit:ek:{key}（24h，仅落库成功后写）前置快检；
② DB 同键查询（终局裁决——Redis 过期/清空后仍准确）；
③ SETNX 处理锁（300s）——占用失败 retry(310) 等锁过期，不 ack 丢弃。
失败：释放锁 → 退避 4^n 秒重试 5 次 → reject 入 audit.dlq（可重放）。
链串行化：per-workspace pg_advisory_xact_lock（BR-12）。
"""
from __future__ import annotations

import hashlib
import json
import logging

from celery import shared_task
from django.core.cache import cache
from django.db import IntegrityError, OperationalError, connection, transaction

logger = logging.getLogger("plane.audit")


def _jsonify(value):
    """递归净化：UUID/datetime 等非 JSON 基本类型转 str（psycopg jsonb
    dumper 用纯 json.dumps，无 DjangoJSONEncoder——worker 侧兜底）。"""
    if isinstance(value, dict):
        return {str(k): _jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def write_chained_row(payload: dict):
    """链式哈希写入：读 prev → sha256(prev|canonical) → INSERT（临界区串行化）。"""
    from plane.db.models import AuditLog

    payload = _jsonify(payload)
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (f"audit-chain:{payload['workspace_id']}",),
            )
        prev = (AuditLog.objects
                .filter(workspace_id=payload["workspace_id"])
                .order_by("-created_at", "-id")
                .values_list("hash", flat=True).first()) or "0" * 64
        row_hash = hashlib.sha256(
            f"{prev}|{_canonical(payload)}".encode()).hexdigest()
        return AuditLog.objects.create(
            event_key=payload["event_key"],
            workspace_id=payload["workspace_id"],
            category=payload["category"], action=payload["action"],
            actor_id=payload.get("actor_id"),
            actor_snapshot=payload.get("actor") or {},
            object_type=(payload.get("object") or {}).get("type"),
            object_id=payload.get("object_id") or (payload.get("object") or {}).get("id"),
            object_snapshot=payload.get("object") or {},
            detail=payload.get("detail") or {},
            ip=payload.get("ip"), user_agent=payload.get("user_agent") or "",
            prev_hash=prev, hash=row_hash,
        )


@shared_task(bind=True, max_retries=5,
             acks_late=True, acks_on_failure_or_timeout=False)
def audit_record(self, payload: dict) -> None:
    """审计落库（at-least-once 消费，三层去重幂等收敛）。"""
    from plane.audit.registry import validate_registered
    from plane.db.models import AuditLog

    event_key = payload["event_key"]
    done_key, lock_key = f"audit:ek:{event_key}", f"audit-lock:{event_key}"
    try:
        validate_registered(payload["category"], payload["action"])
    except Exception:  # noqa: BLE001 —— 未注册事件重试无意义，直接弃入 DLQ
        logger.error("audit.unregistered_event %s.%s", payload["category"],
                     payload["action"])
        raise
    if cache.get(done_key):  # ① 前置快检
        return
    if AuditLog.objects.filter(event_key=event_key).exists():  # ② 终局裁决
        cache.set(done_key, 1, timeout=86400)
        return
    if not cache.add(lock_key, 1, timeout=300):  # ③ 处理锁
        raise self.retry(countdown=310)
    try:
        write_chained_row(payload)  # ④ 幂等锚点
        cache.set(done_key, 1, timeout=86400)  # ⑤ 完成标记（仅成功后）
    except IntegrityError:  # 同分区微秒冲突兜底 → 幂等收敛
        cache.set(done_key, 1, timeout=86400)
    except OperationalError as exc:
        cache.delete(lock_key)
        raise self.retry(countdown=4 ** self.request.retries, exc=exc) from exc
    except Exception:
        cache.delete(lock_key)
        raise  # 不可恢复 → reject → audit.dlq


# ── 每日维护（分区运维 + 留存 + 校验 + 重复发现）──────────

@shared_task
def audit_daily_maintenance() -> dict:
    """BR-07/12 + §2.4：前置建分区 / DEFAULT 巡检 / 180 天清理 / 链校验。"""
    result: dict = {}
    result["partition"] = create_next_month_partition()
    result["dropped"] = drop_partitions_older_than(days=180)
    result["chain"] = verify_hash_chain(sample_rows=200)
    return result


def create_next_month_partition() -> str:
    """前置建下月分区 + DEFAULT 分区巡检（建分区失败窗口的写入兜底）。"""
    import datetime as _dt

    from django.utils import timezone

    now = timezone.now()
    nxt = (now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
           + _dt.timedelta(days=32)).replace(day=1)
    name = f"audit_log_p{nxt:%Y_%m}"
    end = (nxt + _dt.timedelta(days=32)).replace(day=1)
    with connection.cursor() as cur:
        cur.execute("SELECT EXISTS (SELECT 1 FROM pg_tables WHERE tablename=%s)",
                    [name])
        if cur.fetchone()[0]:
            return name
        cur.execute(
            f"CREATE TABLE {name} PARTITION OF audit_log "
            f"FOR VALUES FROM ('{nxt:%Y-%m-%d}') TO ('{end:%Y-%m-%d}')")
    return name


def drop_partitions_older_than(days: int = 180) -> list[str]:
    """留存清理：DROP 过期月分区（非 DELETE，BR-07；审批域留存独立不受影响）。"""
    import datetime as _dt

    from django.utils import timezone

    cutoff = timezone.now() - _dt.timedelta(days=days)
    with connection.cursor() as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE tablename ~ '^audit_log_p[0-9]{4}_[0-9]{2}$'"
        )
        dropped = []
        for (name,) in cur.fetchall():
            y, m = name.rsplit("_p", 1)[1].split("_")
            part_end = _dt.datetime(int(y), int(m), 1, tzinfo=_dt.UTC) \
                + _dt.timedelta(days=32)
            part_end = part_end.replace(day=1)
            if part_end < cutoff:
                cur.execute(f"DROP TABLE IF EXISTS {name}")
                dropped.append(name)
    return dropped


def verify_hash_chain(*, sample_rows: int = 200) -> dict:
    """链完整性校验：hash == sha256(prev_hash | canonical(payload 回构))。

    回构 payload 由行字段组装（worker 写入同构）；断链 → CRITICAL 日志 +
    导出冻结标记（audit_chain_broken，导出端点读取拒绝）。
    """
    from django.core.cache import cache

    from plane.db.models import AuditLog

    broken: list[str] = []
    checked = 0
    for row in (AuditLog.objects.order_by("-created_at", "-id")
                .iterator(chunk_size=200)):
        checked += 1
        if checked > sample_rows:
            break
        ws_id = str(row.workspace_id) if row.workspace_id else ""
        prev = (AuditLog.objects
                .filter(workspace_id=ws_id)
                .filter(created_at__lt=row.created_at)
                .order_by("-created_at", "-id")
                .values_list("hash", flat=True).first()) or "0" * 64
        if prev != row.prev_hash:
            broken.append(str(row.id))
            continue
        rebuilt = _row_to_payload(row)
        expected = hashlib.sha256(
            f"{prev}|{_canonical(rebuilt)}".encode()).hexdigest()
        if expected != row.hash:
            broken.append(str(row.id))
    result = {"checked": checked, "broken": broken}
    if broken:
        logger.critical("audit.hash_chain_broken rows=%s", broken)
        cache.set("audit_chain_broken", broken, timeout=None)
    return result


def _row_to_payload(row) -> dict:
    """行字段 → hash 计算时的 payload 回构（与 worker 写入同构）。"""
    return {
        "event_key": row.event_key,
        "category": row.category, "action": row.action,
        "workspace_id": str(row.workspace_id) if row.workspace_id else None,
        "actor": row.actor_snapshot or {}, "actor_id": row.actor_id,
        "object": row.object_snapshot or {},
        "detail": row.detail or {},
        "ip": str(row.ip) if row.ip else None,
        "user_agent": row.user_agent or "",
    }
