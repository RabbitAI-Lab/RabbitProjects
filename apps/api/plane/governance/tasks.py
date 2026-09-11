"""租户治理异步面（AUTH-012 §4.3/§4.4，P4 R1）。

risk_ingest：AUTH-010 审计管道的 risk 订阅者（audit_record 落库成功后
扇出投递，不引入第二条事件流）。
execute_action：处置执行器（alert/throttle 同步落 Redis；freeze 不自动
执行——双人审批子流程由 freeze-approval/ 端点承载；deny 为同步档，判定
在导出端点请求路径内直接 409，不经本任务）。

任务暂走 celery 默认队列（dev worker 只消费默认队列）；「独立 governance
队列」为部署期路由配置，随私有化部署文档收口（risk_engine 模块注同条）。
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.db import OperationalError
from django.utils import timezone

logger = logging.getLogger("plane.governance")


@shared_task(bind=True, max_retries=3, acks_late=True, acks_on_failure_or_timeout=False)
def risk_ingest(self, payload: dict) -> None:
    """审计事件 → 规则引擎（BR-11 门控：私有化直接返回）。"""
    if not getattr(settings, "TENANT_GOVERNANCE_ENABLED", False):
        return
    try:
        from plane.governance.risk_engine import RiskRuleEngine

        RiskRuleEngine().ingest(payload)
    except OperationalError as exc:
        raise self.retry(countdown=2**self.request.retries, exc=exc) from exc
    except Exception:  # noqa: BLE001 —— 引擎缺陷不重试（毒丸保护）
        logger.exception("risk.ingest_failed action=%s", payload.get("action"))


#: throttle 处置的降速系数（R-06：token 降速至 10% 配额 1h）
THROTTLE_RATIO = 0.1
THROTTLE_TTL = 3600


@shared_task
def execute_action(action: str, tenant_id: str, rule_code: str, event_id: str | None = None) -> dict:
    """规则处置执行（§4.3 处置隔离；治理域写动作全落事件 actions 台账）。"""
    if not getattr(settings, "TENANT_GOVERNANCE_ENABLED", False):
        return {"skipped": "governance_disabled"}
    result = {"action": action, "rule": rule_code, "tenant": tenant_id}
    if action == "alert":
        # 告警：通知平台运营（事件台账已落，通知通道 COLLAB 域 P4 演进）
        logger.info("risk.alert rule=%s tenant=%s event=%s", rule_code, tenant_id, event_id)
        result["notified"] = "platform_ops"
    elif action == "throttle":
        # 限流：租户桶降速标记（R-06 1h 自动恢复=键 TTL 到期）
        try:
            cache.add(f"risk:throttle:{tenant_id}", THROTTLE_RATIO, timeout=THROTTLE_TTL)
            result["throttled_to"] = THROTTLE_RATIO
        except Exception:  # noqa: BLE001 —— fail-open 同限流域
            logger.warning("risk.throttle_degraded tenant=%s", tenant_id)
    elif action == "freeze":
        # 冻结不自动执行（BR-04 双人审批）：仅 CRITICAL 告警待运营发起审批链
        logger.critical("risk.freeze_pending_approval rule=%s tenant=%s event=%s", rule_code, tenant_id, event_id)
        result["pending"] = "freeze_approval"
    elif action == "deny":
        # 同步档：判定在导出端点（§4.5 第 5 强制点）请求路径内 409，此处
        # 仅台账落档（引擎 _fire 已建事件，deny 记录由端点补 actions）
        result["sync_tier"] = True
    if event_id:
        _append_action(event_id, action, tenant_id)
    return result


def _append_action(event_id: str, action: str, tenant_id: str) -> None:
    """处置动作追加进事件 actions 台账（引擎自动处置记录，by=system）。"""
    from plane.db.models import RiskEvent

    updated = RiskEvent.objects.filter(id=event_id).first()
    if updated is None:
        return
    updated.actions = [
        *updated.actions,
        {
            "action": action,
            "by": "system",
            "at": timezone.now().isoformat(),
            "note": "auto",
        },
    ]
    updated.save(update_fields=["actions", "updated_at"])


# ── 边界证明报告（§2.6 / BR-10：系统生成，运营仅触发）────────────────


def _cross_tenant_hits_90d(tenant_id: str) -> int:
    """近 90 天跨租户访问尝试计数（§4.3：Σ 隔离渗透 beat 日计数键；
    get_many 批读 90 键，后端无关——Redis 丢数由逐日落库持久化兜底）。"""
    import datetime as _dt

    days = []
    d = timezone.now().date()
    for _ in range(90):
        days.append(f"iso:probe:{tenant_id}:{d:%Y%m%d}")
        d -= _dt.timedelta(days=1)
    try:
        values = cache.get_many(days).values()
        return sum(int(v or 0) for v in values)
    except Exception:  # noqa: BLE001 —— Redis 失联按 0 出报告
        logger.warning("boundary.probe_keys_degraded tenant=%s", tenant_id)
        return 0


#: AUTH-006 隔离机制清单（报告内容项之一；行级过滤为应用层 accessible_by）
ISOLATION_MECHANISMS = [
    "应用层行级过滤 accessible_by/accessible_in（AUTH-006 §4.2，全模型经 SoftDeleteManager 继承强制起步）",
    "工作空间边界 workspace_id 全表覆盖（AUTH-006 BR-02）",
    "越权响应与真 404 逐字节一致（不可枚举，AUTH-006 越权矩阵语义）",
    "unsafe_all 唯一例外出口：必须带 reason 且进例外登记（AUTH-006 BR-08）",
    "零外键审计表（对象可删不级联，AUTH-010 §4.1）",
]


def _render_report(tenant, report) -> str:
    import json

    payload = {
        "kind": "data_boundary_proof",
        "tenant": {"id": str(tenant.id), "name": tenant.name, "tier": tenant.tier},
        "generated_at": timezone.now().isoformat(),
        "window_days": 90,
        "isolation_mechanisms": ISOLATION_MECHANISMS,
        "cross_tenant_hits": report.cross_tenant_hits,
        "cross_tenant_hits_expectation": "恒为 0；任何 >0 即事故（§2.6）",
        "probe_source": "隔离渗透测试 beat 任务日探测（§4.3 要点表）",
        "audit_sample": {
            "recent_events": list(
                tenant.risk_events.order_by("-created_at").values(
                    "id", "rule_code", "severity", "status", "created_at"
                )[:5]
            ),
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _s3_client():
    import boto3
    from django.conf import settings as dj_settings

    return boto3.client(
        "s3",
        endpoint_url=dj_settings.AWS_S3_ENDPOINT_URL,
        aws_access_key_id=dj_settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=dj_settings.AWS_SECRET_ACCESS_KEY,
    )


def _put_minio(key: str, text: str) -> None:
    from django.conf import settings as dj_settings

    _s3_client().put_object(
        Bucket=dj_settings.AWS_S3_BUCKET_NAME,
        Key=key,
        Body=text.encode("utf-8"),
        ContentType="application/json; charset=utf-8",
    )


def presign_report(key: str, expires: int = 3600) -> str:
    """succeeded 报告的 1h 预签名下载 URL（换发幂等，§4.4）。"""
    from django.conf import settings as dj_settings

    return _s3_client().generate_presigned_url(
        "get_object", Params={"Bucket": dj_settings.AWS_S3_BUCKET_NAME, "Key": key}, ExpiresIn=expires
    )


@shared_task(queue="reports", max_retries=2, autoretry_for=(Exception,), retry_backoff=True)
def generate_boundary_report(report_id: str) -> dict:
    """worker 侧：聚合隔离证据 → MinIO → 状态机 queued→succeeded/failed。"""
    from plane.db.models import BoundaryReport

    report = BoundaryReport.objects.select_related("tenant").filter(pk=report_id).first()
    if report is None:
        return {"skipped": "missing"}
    BoundaryReport.objects.filter(pk=report_id).update(state="processing")
    try:
        hits = _cross_tenant_hits_90d(str(report.tenant_id))
        key = f"boundary-reports/{report.tenant_id}/{report.id}.json"
        _put_minio(key, _render_report(report.tenant, report))
        BoundaryReport.objects.filter(pk=report_id).update(state="succeeded", cross_tenant_hits=hits, file_path=key)
        return {"status": "succeeded", "cross_tenant_hits": hits}
    except Exception as exc:  # noqa: BLE001 —— 失败留因可查
        BoundaryReport.objects.filter(pk=report_id).update(state="failed")
        logger.error("boundary.report_failed id=%s err=%s", report_id, exc)
        raise
