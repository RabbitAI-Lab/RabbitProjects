"""文件资产清理任务（FILE-001 §4.6）。

- ``mark_abandoned_uploads`` —— 每 30 分钟把超时未 complete 的 presign 标记 abandoned
- ``purge_deleted_assets`` —— 每日 02:30 物理回收三类目标：
  ① abandoned 超 1 天
  ② 软删超 30 天
  ③ 宿主 Issue 已软删超 30 天的 uploaded 附件（多态无 FK 的级联兜底）

清理策略：先删对象（404 视为成功幂等）→ 后硬删记录（``all_objects.delete``）。
任一记录失败计入 failed 跳过，不阻塞批次；下轮 beat 重试。
"""
from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger("plane.bgtasks.asset_cleanup")

ABANDON_AFTER = timedelta(minutes=30)            # 与 presign URL TTL 对齐
PURGE_DELETED_AFTER = timedelta(days=30)
PURGE_ABANDONED_AFTER = timedelta(days=1)
BATCH = 500


@shared_task(name="plane.bgtasks.asset_cleanup.mark_abandoned_uploads")
def mark_abandoned_uploads() -> int:
    """把超时未 complete 的上传标记 abandoned —— 条件 UPDATE 天然幂等。"""
    from plane.db.models import FileAsset

    cutoff = timezone.now() - ABANDON_AFTER
    return (
        FileAsset.objects.filter(
            status=FileAsset.Status.UPLOADING,
            created_at__lt=cutoff,
        ).update(status=FileAsset.Status.ABANDONED)
    )


@shared_task(
    bind=True,
    max_retries=3,
    name="plane.bgtasks.asset_cleanup.purge_deleted_assets",
)
def purge_deleted_assets(self) -> dict:
    """每日物理回收。

    对象不可达（StorageUnavailable）→ 跳过本条，下轮重试；
    NoSuchKey → 视为成功。
    """
    from plane.db.models import FileAsset, Issue
    from plane.storage import minio as storage

    now = timezone.now()
    qs_abandoned = FileAsset.all_objects.filter(
        status=FileAsset.Status.ABANDONED,
        created_at__lt=now - PURGE_ABANDONED_AFTER,
    )
    qs_deleted = FileAsset.all_objects.filter(
        deleted_at__lt=now - PURGE_DELETED_AFTER,
        deleted_at__isnull=False,
    )
    # ③ 宿主级联：Issue 已软删超 30 天的 uploaded 附件
    qs_cascade = FileAsset.all_objects.filter(
        entity_type=FileAsset.EntityType.ISSUE,
        status=FileAsset.Status.UPLOADED,
        deleted_at__isnull=True,
        entity_id__in=Issue.all_objects.filter(
            deleted_at__lt=now - PURGE_DELETED_AFTER,
        ).values("id"),
    )
    targets = qs_abandoned.union(qs_deleted).union(qs_cascade)

    purged = failed = 0
    for asset in targets.iterator(chunk_size=BATCH):
        try:
            storage.remove_object(bucket="rp-uploads", key=asset.storage_path)
        except storage.StorageUnavailable:
            failed += 1
            logger.warning(
                "asset_cleanup.remove_failed asset=%s key=%s will_retry",
                asset.id,
                asset.storage_path,
            )
            continue
        asset.delete()  # 实例删除=硬删；软删走 soft_delete()
        purged += 1
    return {"purged": purged, "failed": failed}
