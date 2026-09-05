"""Account 域 Celery 任务（AUTH-004 §4.4）。

- ``send_reset_email`` —— 重置邮件投递（worker 仅传 ID，§10.5）
- ``cleanup_expired_reset_tokens`` —— 过期令牌物理回收
- ``cleanup_avatar_orphan_assets`` —— 头像孤儿资产兜底（FILE-001 资产生命周期前
  本任务先行承担；FILE-001 §4.6 落地后由其统一 beat 取代，本任务退役）

降级：
- SMTP 未配置 → ``plane.app.mail`` 日志输出（AUTH-004 §4.4 注 / IT-05）
- broker 不可达 → ``send_reset_email.delay`` 在主流程事务内被外层 ``_dispatch_reset_email``
  捕获降级为本地日志，不阻塞 forgot-password 返回 202
"""
from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger("plane.account.tasks")


# ──────────────────────────────────────────────────────────────────────
# 重置邮件投递
# ──────────────────────────────────────────────────────────────────────
@shared_task(
    bind=True,
    max_retries=3,
    name="plane.account.tasks.send_reset_email",
)
def send_reset_email(self, user_id: str, token: str) -> bool:
    """投递密码重置邮件（AUTH-004 §4.4）。

    仅传 ID + 明文令牌（令牌明文唯一离开事务的副本）；任务内重查用户避免过期快照。
    """
    from plane.app.mail import deliver_reset_email  # SMTP 未配置时降级记日志
    from plane.db.models import User

    User.objects.filter(pk=user_id).first()
    try:
        deliver_reset_email(user_id=user_id, token=token)
        return True
    except Exception as exc:
        logger.warning(
            "reset_email.delivery_failed user=%s retry=%s err=%s",
            user_id, self.request.retries, exc,
        )
        if self.request.retries >= self.max_retries:
            logger.error(
                "reset_email.giveup user=%s retries=%s", user_id, self.max_retries,
            )
            return False
        try:
            raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))
        except self.MaxRetriesExceededError:
            return False


# ──────────────────────────────────────────────────────────────────────
# 过期令牌清理
# ──────────────────────────────────────────────────────────────────────
@shared_task(name="plane.account.tasks.cleanup_expired_reset_tokens")
def cleanup_expired_reset_tokens() -> int:
    """每小时：物理删除过期超 7 天的令牌行（保留 7 天供安全审计）。"""
    from plane.db.models import PasswordResetToken

    threshold = timezone.now() - timedelta(days=7)
    deleted, _ = PasswordResetToken.all_objects.filter(
        expires_at__lt=threshold,
    ).delete()
    return deleted


# ──────────────────────────────────────────────────────────────────────
# 头像孤儿资产兜底（FILE-001 §1.4 矩阵未到位前的过渡）
# ──────────────────────────────────────────────────────────────────────
@shared_task(name="plane.account.tasks.cleanup_avatar_orphan_assets")
def cleanup_avatar_orphan_assets() -> int:
    """每 30 分钟：清理头像类孤儿资产。

    两条判定：
    ① 超时未 complete（is_uploaded=False 且 created_at 超 30 分钟）→ 删对象 + 删记录；
    ② 已上传但不再被任何 User.avatar_url 引用（恢复默认 / 更换头像后）→ 同上。
    对象不存在视为成功（幂等）。FILE-001 §4.6 上线后本任务由其统一 beat 取代。
    """
    from plane.db.models import FileAsset, User
    from plane.storage import minio as storage

    cutoff = timezone.now() - timedelta(minutes=30)
    removed = 0
    qs = (
        FileAsset.objects
        .filter(entity_type=FileAsset.EntityType.AVATAR, created_at__lt=cutoff)
        .iterator(chunk_size=500)
    )
    for asset in qs:
        if asset.is_uploaded:
            # 在役判定：任意 User.avatar_url 以 storage_path 结尾即视为仍在引用
            referenced = User.objects.filter(
                avatar_url__endswith=asset.storage_path,
            ).exists()
            if referenced:
                continue
        try:
            storage.remove_object(bucket="rp-uploads", key=asset.storage_path)
        except storage.StorageUnavailable:
            logger.warning(
                "avatar_cleanup.remove_failed key=%s will_retry", asset.storage_path,
            )
            continue
        asset.delete()  # 实例删除=硬删；软删走 soft_delete()
        removed += 1
    return removed
