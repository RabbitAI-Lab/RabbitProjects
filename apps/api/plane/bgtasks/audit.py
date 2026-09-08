"""全站审计事件入口（AUTH-010 占位，Sprint-8 R4 兑现真管道）。

AUTH-007 的部门/授权事件按规格 §4.3 以 ``record_audit.delay(event,
actor_id=…, object_id=…)`` 投递；R1 阶段 AUTH-010 审计管道（月分区表 +
hash 链 + 幂等三层去重 + DLX）尚未交付，本 task 仅做结构化日志落
``plane.audit`` logger——事件不静默丢失（log 可查），R4 替换函数体为
真管道写入，调用点与 include 注册不动。
"""
import logging

from celery import shared_task

logger = logging.getLogger("plane.audit")


@shared_task(ignore_result=True)
def record_audit(event: str, *, actor_id: str, object_id: str | None = None,
                 **extra) -> None:
    logger.info(
        "audit_event=%s actor=%s object=%s extra=%s",
        event, actor_id, object_id, extra,
    )
