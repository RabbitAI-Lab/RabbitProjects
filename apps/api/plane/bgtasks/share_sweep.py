"""分享过期清扫（FILE-004 §4.3.4 / BR-04 兜底）。

读时硬校验（``resolve_active_share``）已把过期链接挡在门外并惰性标记 expired；
本 beat 任务每小时兜底批量迁移 ``active 且 expires_at < now`` 的存量行，
保证管理弹层（§3.2「已过期」态）与 BR-11 额度释放不依赖匿名访问触发。

测试纪律（坑 18）：pytest 直连共享 dev PG——全表 UPDATE 必须带
``restrict_share_ids`` / ``restrict_workspace_id`` 收窄作用域；beat 生产调用
不传，行为与 FILE-001 ``mark_abandoned_uploads`` 同款。
"""
from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger("plane.bgtasks.share_sweep")


@shared_task(name="plane.bgtasks.share_sweep.sweep_expired_shares")
def sweep_expired_shares(
    *, restrict_share_ids: list | None = None, restrict_workspace_id=None
) -> int:
    """每小时：active 且 expires_at < now → expired（BR-04 兜底）。"""
    from plane.db.models import FileShareLink

    qs = FileShareLink.objects.filter(
        status=FileShareLink.Status.ACTIVE, expires_at__lt=timezone.now()
    )
    if restrict_share_ids is not None:
        qs = qs.filter(id__in=restrict_share_ids)
    if restrict_workspace_id is not None:
        qs = qs.filter(asset__workspace_id=restrict_workspace_id)
    swept = qs.update(status=FileShareLink.Status.EXPIRED, updated_at=timezone.now())
    if swept:
        logger.info("share_sweep.swept count=%s", swept)
    return swept
