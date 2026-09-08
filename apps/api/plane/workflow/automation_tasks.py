"""自动化 Celery 任务（WF-003 §4.3）——automation_match 主队列 + due 扫描 + 90 天清理。

`workflow` 队列新增：与审批超时扫描共用（同优先级、相同 worker profile）。
"""
from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from plane.db.models import AutomationRule, AutomationRun, Issue
from plane.workflow.automation_service import (
    run_event,
)

logger = logging.getLogger(__name__)


@shared_task(queue="workflow", ignore_result=True)
def automation_match(event: dict) -> dict:
    """主入口：事件总线投递——on_commit 钩子或人工触发。"""
    return run_event(event)


@shared_task(queue="workflow", ignore_result=True)
def automation_due_scan() -> None:
    """due_approaching beat 扫描（§2.2/§4.3）：每 15min 一次（INFRA-002 §4.1），
    按 hours_before 提前命中；每任务每规则 SETNX 独立键 autodedup:due 防双占。"""
    from django.core.cache import cache

    now = timezone.now()
    rules = AutomationRule.objects.filter(
        trigger__type="due_approaching", is_active=True, deleted_at__isnull=True
    )
    for rule in rules:
        hours = (rule.trigger.get("config") or {}).get("hours_before")
        if not isinstance(hours, int):
            continue
        threshold = now + timedelta(hours=hours)
        issues = Issue.objects.filter(
            project_id=rule.project_id, target_date__lte=threshold.date(),
            deleted_at__isnull=True, archived_at__isnull=True,
        )
        for issue in issues:
            # §2.3 BR-10：每任务每规则每 hours_before 唯一触发
            key = f"autodedup:due:{rule.id}:{issue.id}:{hours}"
            if not cache.add(key, 1, timeout=max(hours * 3600, 86400)):
                continue
            cache.set(key, 1, timeout=max(hours * 3600, 86400))
            issue_type_id = str(issue.issue_type_id) if issue.issue_type_id else None
            cfg = rule.trigger.get("config") or {}
            if cfg.get("issue_types") and issue_type_id not in cfg["issue_types"]:
                continue
            run_event({
                "type": "due_approaching",
                "project_id": str(rule.project_id),
                "issue_id": str(issue.id),
                "chain_depth": 0, "origin": "",
                "payload": {"hours_before": hours,
                            "issue_type_id": issue_type_id,
                            "target_date": str(issue.target_date)},
            })


@shared_task(queue="workflow", ignore_result=True)
def automation_purge_old_runs(retention_days: int = 90) -> None:
    """90 天日志清理（BR-12）：分批 500 行（§4.2 索引支撑范围删除）。"""
    cutoff = timezone.now() - timedelta(days=retention_days)
    AutomationRun.objects.filter(created_at__lt=cutoff).delete()
