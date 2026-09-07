"""出站 Webhook 投递引擎（INTG-002 §4.3——Sprint-5 T5-05）。

- ``dispatch_events``（挂点收口）：on_commit 后按事件匹配项目活跃端点，
  建 Delivery（冻结载荷）→ ``deliver_webhook.delay``；
- ``deliver_webhook``：HMAC-SHA256 签名（timestamp ±5min 防重放）→ POST
  （connect 3s / read 10s）→ 2xx 终态 success（连败 −1 钳位）；非 2xx 按
  退避表 1s/10s/1m/10m/1h/6h 重试（初始 + 6 次 = 7 次尝试后 dead，
  连败 +1）；≥50 → auto_disabled + 通知创建者（webhook.auto_disabled）；
- 死信重放：新建 pending 行 ``replay_of`` 指回原 dead（BR-07 审计链）；
- beat：``purge_webhook_deliveries`` 每日清 30 天前终态行（分批 5000）。

事件面闭集（§2.3 十二种）：issue.created/updated/state.changed、
comment.created、project.created/activated/restored/archived/closed、
webhook.ping（免勾选）、report.snapshot。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from plane.base.exception import AppException
from plane.db.models import WebhookDelivery, WebhookEndpoint
from plane.db.models.integration import decrypt_secret

logger = logging.getLogger("plane.db.services.webhook_outbound")

#: 退避表（BR-06：1s/10s/1m/10m/1h/6h；初始 + 6 重试 = 7 次尝试）
RETRY_SCHEDULE = (1, 10, 60, 600, 3600, 21600)
#: 连败停用阈值（BR-08：终态计数器，无时间窗）
AUTO_DISABLE_THRESHOLD = 50
#: 签名时间窗（§2.2：±5 分钟防重放）
SIGNATURE_SKEW_SECONDS = 300
#: 端点/项目上限（§2.6）
MAX_ENDPOINTS_PER_PROJECT = 20

#: 事件面闭集（§2.3——前端复选与扇出校验同源）
EVENT_CHOICES: tuple[str, ...] = (
    "issue.created", "issue.updated", "issue.state.changed",
    "comment.created",
    "project.created", "project.activated", "project.restored",
    "project.archived", "project.closed",
    "webhook.ping", "report.snapshot",
)


def new_endpoint_secret() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex


def sign_payload(secret: str, body: bytes, timestamp: int) -> dict[str, str]:
    """§2.2 签名头：X-RP-Signature（HMAC-SHA256 over "{t}.{body}"）+ X-RP-Timestamp。"""
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body,
                   hashlib.sha256).hexdigest()
    return {"X-RP-Signature": f"v1={mac}", "X-RP-Timestamp": str(timestamp)}


def verify_signature(secret: str, body: bytes, signature: str,
                     timestamp: int | None = None) -> bool:
    """接收方校验指引的参考实现（官方示例语义，测试自证用）。"""
    if timestamp is None:
        timestamp = int(time.time())
    if abs(time.time() - timestamp) > SIGNATURE_SKEW_SECONDS:
        return False
    expected = sign_payload(secret, body, timestamp)["X-RP-Signature"]
    return hmac.compare_digest(signature, expected)


def dispatch_events(event: str, payload: dict, *, project_id) -> None:
    """事件扇出挂点（on_commit 回调中调用；匹配活跃端点建 Delivery 并投递）。

    幂等锚 event_id：调用方传事件真相表主键（issue.*=IssueActivity.id /
    comment.created=IssueComment.id / project.*=ProjectStatusLog.id /
    webhook.ping=uuid4）——payload["event_id"] 缺省自动生成。
    """
    try:
        event_id = uuid.UUID(str(payload.get("event_id") or uuid.uuid4()))
    except (ValueError, TypeError):
        event_id = uuid.uuid4()
    body = {"event": event, "event_id": str(event_id),
            "project_id": str(project_id), "data": payload.get("data", payload)}
    endpoints = WebhookEndpoint.objects.filter(
        project_id=project_id, is_active="active", deleted_at__isnull=True)
    if event != "webhook.ping":
        endpoints = endpoints.filter(events__contains=[event])
    for endpoint in endpoints:
        row, created = WebhookDelivery.objects.get_or_create(
            endpoint=endpoint, event_id=event_id,
            defaults={"event": event, "payload": body, "status": "pending"},
        )
        if not created and row.status in ("dead", "cancelled"):
            continue
        deliver_webhook.delay(row.id)
    if endpoints:
        logger.info("webhook.dispatch event=%s endpoints=%s", event,
                    len(list(endpoints)))


@shared_task(bind=True, max_retries=None, acks_late=True)
def deliver_webhook(self, delivery_id: str) -> str:
    """单次投递尝试（含退避调度）。返回终态或 retrying。"""
    row = WebhookDelivery.objects.select_related("endpoint").filter(
        pk=delivery_id).first()
    if row is None:
        return "missing"
    endpoint = row.endpoint
    if endpoint.is_active != "active":      # 边界 #4：执行前重读状态
        row.status = "cancelled"
        row.save(update_fields=["status", "updated_at"])
        return "cancelled"
    body = json.dumps(row.payload, ensure_ascii=False,
                      separators=(",", ":"), default=str).encode()
    secret = decrypt_secret(endpoint.secret_encrypted)
    ts = int(time.time())
    headers = {
        "Content-Type": "application/json", "User-Agent": "rabbit-projects-webhook",
        **(sign_payload(secret, body, ts) if secret else {}),
    }
    attempt_no = len(row.attempts) + 1
    code, latency_ms, error = _post(endpoint.url, body, headers)
    row.attempts.append({"n": attempt_no, "at": timezone.now().isoformat(),
                         "code": code, "latency_ms": latency_ms, "error": error})
    if 200 <= code < 300:
        row.status = "success"
        row.next_retry_at = None
        row.save(update_fields=["attempts", "status", "next_retry_at", "updated_at"])
        _bump_failures(endpoint, -1)
        return "success"
    if attempt_no > len(RETRY_SCHEDULE):    # 7 次尝试走完 → 死信（BR-06）
        row.status = "dead"
        row.next_retry_at = None
        row.save(update_fields=["attempts", "status", "next_retry_at", "updated_at"])
        _bump_failures(endpoint, +1)
        return "dead"
    backoff = RETRY_SCHEDULE[attempt_no - 1]
    row.status = "retrying"
    row.next_retry_at = timezone.now() + timedelta(seconds=backoff)
    row.save(update_fields=["attempts", "status", "next_retry_at", "updated_at"])
    deliver_webhook.apply_async((str(row.id),),
                                countdown=backoff)   # 退避自调度
    return f"retrying:{backoff}s"


def _post(url: str, body: bytes, headers: dict) -> tuple[int, int, str | None]:
    import requests

    started = time.monotonic()
    try:
        resp = requests.post(url, data=body, headers=headers, timeout=(3, 10))
        return resp.status_code, int((time.monotonic() - started) * 1000), None
    except Exception as exc:  # noqa: BLE001 —— 网络/DNS/超时统一入退避（§2.5）
        return 0, int((time.monotonic() - started) * 1000), str(exc)[:200]


def _bump_failures(endpoint: WebhookEndpoint, delta: int) -> None:
    """终态连败计数（BR-08：dead +1 / success −1 钳位 ≥0；≥50 停用 + 通知）。"""
    from django.db.models import F

    WebhookEndpoint.objects.filter(pk=endpoint.pk).update(
        consecutive_failures=F("consecutive_failures") + delta)
    endpoint.refresh_from_db(fields=["consecutive_failures"])
    if endpoint.consecutive_failures < 0:
        WebhookEndpoint.objects.filter(pk=endpoint.pk).update(consecutive_failures=0)
        endpoint.refresh_from_db(fields=["consecutive_failures"])
    if (delta > 0 and endpoint.consecutive_failures >= AUTO_DISABLE_THRESHOLD
            and endpoint.is_active == "active"):

        # 单写者幂等迁移（管理面竞争防御，§4.1 并发约束）
        updated = WebhookEndpoint.objects.filter(
            pk=endpoint.pk, is_active="active").update(is_active="auto_disabled")
        if updated:
            transaction.on_commit(lambda: _notify_auto_disabled(endpoint))


def _notify_auto_disabled(endpoint: WebhookEndpoint) -> None:
    from plane.bgtasks.notifications import send_workspace_notification

    if endpoint.created_by_id:
        send_workspace_notification.delay(
            receiver_id=str(endpoint.created_by_id),
            event="webhook.auto_disabled",
            context={"endpoint_id": str(endpoint.id), "url": endpoint.url,
                     "failures": endpoint.consecutive_failures},
            title=f"Webhook 端点已自动停用（连续 {endpoint.consecutive_failures} 次失败）")


def replay_delivery(delivery_id, *, actor) -> WebhookDelivery | None:
    """死信重放（BR-07）：新建 pending 行 replay_of 指回原 dead——原行不动。"""
    source = WebhookDelivery.objects.filter(pk=delivery_id).first()
    if source is None or source.status != "dead":
        return None
    row = WebhookDelivery.objects.create(
        endpoint=source.endpoint, event=source.event,
        event_id=uuid.uuid4(), payload=source.payload,
        status="pending", replay_of=source.id,
        created_by=actor, updated_by=actor)
    deliver_webhook.delay(row.id)
    return row


@shared_task
def purge_webhook_deliveries() -> int:
    """30 天滚动清理（分批 5000，仅终态行）。"""
    cutoff = timezone.now() - timedelta(days=30)
    total = 0
    while True:
        ids = list(WebhookDelivery.objects.filter(
            created_at__lt=cutoff,
            status__in=("success", "dead", "cancelled"),
        ).values_list("id", flat=True)[:5000])
        if not ids:
            break
        WebhookDelivery.objects.filter(id__in=ids).delete()
        total += len(ids)
    return total


@shared_task
def retry_due_deliveries() -> int:
    """退避到期扫描（beat 兜底——自调度丢失时的恢复通道）。"""
    due = WebhookDelivery.objects.filter(
        status="retrying", next_retry_at__lte=timezone.now())
    count = 0
    for row in due.only("id")[:1000]:
        deliver_webhook.delay(row.id)
        count += 1
    return count


# ── 管理面校验（视图消费）──────────────────────────────────────
def validate_endpoint_payload(payload: dict, *, project) -> dict:
    url = str(payload.get("url") or "").strip()
    events = payload.get("events") or []
    if not url.startswith(("http://", "https://")):
        raise AppException("VALIDATION_ERROR", message="url 必须为 http(s) 地址",
                           details=[{"field": "url", "code": "INVALID"}])
    unknown = [e for e in events if e not in EVENT_CHOICES or e == "webhook.ping"]
    if unknown:
        raise AppException("VALIDATION_ERROR",
                           message="events 含非法值（webhook.ping 免勾选）",
                           details=[{"field": "events", "code": "NOT_A_CHOICE",
                                     "message": f"unknown: {unknown}"}])
    if not events:
        raise AppException("VALIDATION_ERROR", message="至少订阅 1 个事件",
                           details=[{"field": "events", "code": "INVALID"}])
    if WebhookEndpoint.objects.filter(
            project=project, url=url, deleted_at__isnull=True).exists():
        raise AppException("RESOURCE_ALREADY_EXISTS", message="同项目同 URL 已存在",
                           details=[{"field": "url", "code": "UNIQUE"}])
    if WebhookEndpoint.objects.filter(
            project=project, deleted_at__isnull=True).count() >= MAX_ENDPOINTS_PER_PROJECT:
        raise AppException("RESOURCE_LIMIT_EXCEEDED",
                           message=f"单项目最多 {MAX_ENDPOINTS_PER_PROJECT} 个端点")
    return {"url": url, "events": sorted(set(events))}
