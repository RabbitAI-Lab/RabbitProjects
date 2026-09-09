"""项目集里程碑预警任务（PROJ-004 §4.5，Sprint-9）。

Celery beat 每日 09:00：BR-06 前 7 天每日一条（幂等），逾期转红标（读时派生）。
幂等走 Notification.dedup_key DB 偏条件唯一（worker 重试/MQ 重投零重复，
与既有通知域同一机制）；扫描窗 30 天约束预警发送，「已延期」红标为读时派生
口径（BR-06），不受扫描窗影响。
"""
from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.contrib.auth import get_user_model
from django.utils import timezone

from plane.db.models import Notification, PortfolioMilestone, WorkspaceMember
from plane.db.models.roles import WorkspaceRole
from plane.db.services.portfolio import PortfolioService

logger = logging.getLogger(__name__)


@shared_task(queue="reports")
def milestone_due_alerts() -> dict:
    """每日 09:00 扫描：未完成 + 剩 ≤7 天（含逾期 30 天内）→ 通知 manager（空则 WS_ADMIN+）。"""
    today = timezone.localdate()
    svc = PortfolioService()
    sent, skipped_done, skipped_window = 0, 0, 0
    rows = (PortfolioMilestone.objects
            .filter(completed_at__isnull=True,
                    target_date__gte=today - timedelta(days=30),
                    deleted_at__isnull=True)
            .select_related("portfolio"))
    for ms in rows:
        # for_system=True：BR-06 系统任务走全集口径（通知 manager 的完成度不随读者波动）
        progress = svc.milestone_progress(ms.id, for_system=True)
        if progress >= 1.0:
            skipped_done += 1
            continue
        days_left = (ms.target_date - today).days
        if days_left > 7:
            skipped_window += 1
            continue
        receivers = _receivers(ms)
        key = f"ms:alert:{ms.id}:{today.isoformat()}"
        for user in receivers:
            title = (f"里程碑「{ms.name}」{'已逾期' if days_left < 0 else f'剩 {days_left} 天'}"
                     f"· 完成度 {progress:.0%}（{ms.portfolio.name}）")
            _, created = Notification.objects.get_or_create(
                receiver=user, dedup_key=key,
                defaults={
                    "event": Notification.Event.MILESTONE_AT_RISK,
                    "title": title,
                    "data": {
                        "milestone_id": str(ms.id),
                        "portfolio_id": str(ms.portfolio_id),
                        "workspace_id": str(ms.portfolio.workspace_id),
                        "days_left": days_left, "progress": round(progress, 4),
                    },
                })
            sent += int(created)
    summary = {"scanned": len(rows), "sent": sent,
               "skipped_done": skipped_done, "skipped_window": skipped_window}
    logger.info("milestone_due_alerts %s", summary)
    return summary


def _receivers(ms) -> list:
    """收件人：项目集 manager（含祖先链，§4.6 ③ 同口径）→ 空则 WS_ADMIN+ 兜底。"""
    User = get_user_model()
    managers: list = []
    node = ms.portfolio
    hops = 0
    while node is not None and hops <= 3:
        if node.manager_id:
            user = User.objects.filter(id=node.manager_id, is_active=True).first()
            if user:
                managers.append(user)
                break  # 最近祖先命中即止（与 BR-03 判定同向）
        node = node.parent
        hops += 1
    if managers:
        return managers
    admins = (WorkspaceMember.objects
              .filter(workspace_id=ms.portfolio.workspace_id, is_active=True,
                      role__gte=WorkspaceRole.ADMIN)
              .values_list("member_id", flat=True))
    return list(User.objects.filter(id__in=list(admins), is_active=True))
