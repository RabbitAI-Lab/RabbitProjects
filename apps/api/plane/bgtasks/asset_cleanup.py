"""文件资产清理任务（FILE-001 §4.6；FILE-002 §4.3.3 引用计数增强）。

- ``mark_abandoned_uploads`` —— 每 30 分钟把超时未 complete 的 presign 标记 abandoned
- ``purge_deleted_assets`` —— 每日 02:30 物理回收三类目标：
  ① abandoned 超 1 天
  ② 软删超 30 天（FILE-002 BR-06：按 storage_path 键级引用计数决定是否删对象）
  ③ 宿主 Issue 已软删超 30 天的 uploaded 附件（多态无 FK 的级联兜底；FILE-002
    起子查询扩展覆盖双挂行——issue 外键非空的文件库行同样随宿主回收）

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


def _purge_one_asset(asset, *, is_soft_deleted_branch: bool) -> tuple[str, int]:
    """单资产物理回收：返回 (结果, 对象删除数)。

    结果 = purged（行已硬删）/ kept（软删分支对象被引用，仅硬删行）/ failed
    （存储不可达跳过，行保留下轮重试）。FILE-003 IT-07 扩展：键集 = 行级
    storage_path ∪ 名下全部版本对象键（回滚零拷贝复用的旧键可能仍被其他存活
    行引用——级联硬删前逐键引用计数）。
    """
    from plane.db.models import FileVersion
    from plane.db.services.file_library import collect_purge_keys
    from plane.storage import minio as storage

    def _referenced_outside(key: str) -> bool:
        from plane.db.models import FileAsset as FA

        if (FA.objects.filter(storage_path=key, deleted_at__isnull=True)
                .exclude(pk=asset.pk).exists()):
            return True
        return (FileVersion.objects
                .filter(object_key=key, deleted_at__isnull=True)
                .exclude(id__in=going_version_ids)
                .exists())

    going_version_ids = set(
        FileVersion.all_objects.filter(asset_id=asset.pk).values_list("id", flat=True)
    )
    removed = kept_keys = 0
    for key in collect_purge_keys(asset):
        if _referenced_outside(key):
            kept_keys += 1
            continue
        try:
            storage.remove_object(bucket="rp-uploads", key=key)
        except storage.StorageUnavailable:
            return "failed", removed
        removed += 1
    asset.delete()  # 实例删除=硬删（级联 file_versions 行）
    if is_soft_deleted_branch and kept_keys:
        return "kept", removed
    return "purged", removed


@shared_task(name="plane.bgtasks.asset_cleanup.mark_abandoned_uploads")
def mark_abandoned_uploads(*, restrict_workspace_id=None) -> int:
    """把超时未 complete 的上传标记 abandoned —— 条件 UPDATE 天然幂等。

    FILE-003 §1.6 第 1 条（IT-09 第一段）：带活跃分片会话（status=uploading 的
    ``UploadSession``）的资产行**豁免**——2GB 断点续传/24h TTL 场景必然越过
    30 分钟，无条件扫描会把在途行误标 abandoned 并次日硬删。30min 语义收窄为
    无会话直传行的孤儿回收；会话 expired/aborted 后资产行自动回归本扫描管辖。

    ``restrict_workspace_id``：测试安全参数（pytest 直连共享 dev PG——不限域的
    全表 UPDATE 会误伤主栈数据）；beat 生产调用不传，行为与 FILE-001 原版一致。
    """
    from plane.db.models import FileAsset, UploadSession

    qs = FileAsset.objects.filter(
        status=FileAsset.Status.UPLOADING,
        created_at__lt=timezone.now() - ABANDON_AFTER,
    ).exclude(  # WHERE NOT EXISTS 活跃会话豁免（FILE-003 §1.6）
        upload_sessions__status=UploadSession.Status.UPLOADING
    )
    if restrict_workspace_id is not None:
        qs = qs.filter(workspace_id=restrict_workspace_id)
    return qs.update(status=FileAsset.Status.ABANDONED)


@shared_task(
    bind=True,
    max_retries=3,
    name="plane.bgtasks.asset_cleanup.purge_deleted_assets",
)
def purge_deleted_assets(self, *, restrict_workspace_id=None) -> dict:
    """每日物理回收。

    对象不可达（StorageUnavailable）→ 跳过本条，下轮重试；
    NoSuchKey → 视为成功。

    三分支（FILE-001 原口径 + FILE-002/FILE-003 扩展）：
    ① abandoned 超 1 天（残片对象必删）；
    ② 软删超 30 天——BR-06 键级引用计数（同键其他存活资产行 / 存活版本行引用
      则保留对象只硬删行）；FILE-003 IT-07 起键集扩至名下全部版本对象键；
    ③ 宿主 Issue 已软删超 30 天的 uploaded 附件（多态无 FK 的级联兜底；FILE-002
      起子查询扩展覆盖双挂行——issue 外键非空的文件库行同样随宿主回收）。

    ``restrict_workspace_id``：测试安全参数（pytest 直连共享 dev PG——不限域的
    全表硬删会误伤主栈数据）；beat 生产调用不传，行为与 FILE-001 原版一致。
    """
    from django.db.models import Q

    from plane.db.models import FileAsset, Issue

    def _scoped(qs):
        return qs.filter(workspace_id=restrict_workspace_id) if restrict_workspace_id else qs

    now = timezone.now()
    qs_abandoned = _scoped(FileAsset.all_objects.filter(
        status=FileAsset.Status.ABANDONED,
        created_at__lt=now - PURGE_ABANDONED_AFTER,
    ))
    qs_deleted = _scoped(FileAsset.all_objects.filter(
        deleted_at__lt=now - PURGE_DELETED_AFTER,
        deleted_at__isnull=False,
    ))
    # ③ 宿主级联：Issue 已软删超 30 天的 uploaded 附件——子查询扩展覆盖双挂行
    #    （issue 外键，§1.7 第 1 行演进列：文件库行的任务视图随宿主回收）
    cascade_issue_ids = Issue.all_objects.filter(
        deleted_at__lt=now - PURGE_DELETED_AFTER,
    ).values("id")
    qs_cascade = _scoped(FileAsset.all_objects.filter(
        Q(entity_type=FileAsset.EntityType.ISSUE, entity_id__in=cascade_issue_ids)
        | Q(issue_id__in=cascade_issue_ids),
        status=FileAsset.Status.UPLOADED,
        deleted_at__isnull=True,
    ))
    targets = qs_abandoned.union(qs_deleted).union(qs_cascade)

    purged = failed = objects_kept = 0
    for asset in targets.iterator(chunk_size=BATCH):
        is_soft_deleted_branch = (
            asset.deleted_at is not None
            and asset.status == FileAsset.Status.UPLOADED
        )
        result, _removed = _purge_one_asset(
            asset, is_soft_deleted_branch=is_soft_deleted_branch
        )
        if result == "failed":
            failed += 1
            logger.warning(
                "asset_cleanup.remove_failed asset=%s key=%s will_retry",
                asset.id, asset.storage_path,
            )
            continue
        if result == "kept":
            # BR-06：同键仍有存活引用 → 对象保留，仅硬删本行（引用计数语义）
            objects_kept += 1
        purged += 1
    return {"purged": purged, "failed": failed, "objects_kept_by_reference": objects_kept}
