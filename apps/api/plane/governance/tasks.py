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
