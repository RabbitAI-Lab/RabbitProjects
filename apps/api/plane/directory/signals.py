"""目录域信号（AUTH-011 §4.3 manual 盖章钩子，P4 R2）。

User.is_active True→False 时盖章：该用户全部 DirectoryUserMapping 置
disabled_at_source="manual"——同步永不自动复活手工禁用账号（BR-06 延伸，
UT-23）。注意：不挂接成员移除路径（WorkspaceMember 软删≠账号禁用，
§4.3 设计表「manual 盖章钩子」注）。
"""

from __future__ import annotations

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger("plane.directory")


@receiver(post_save, sender="db.User", dispatch_uid="directory_manual_stamp")
def stamp_manual_disable(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    if instance.is_active:
        return
    try:
        from plane.db.models import DirectoryUserMapping

        updated = (
            DirectoryUserMapping.objects.filter(user=instance)
            .exclude(disabled_at_source="manual")
            .update(disabled_at_source="manual")
        )
        if updated:
            logger.info("directory.manual_stamped user=%s mappings=%s", instance.id, updated)
    except Exception:  # noqa: BLE001 —— 信号体不阻断保存
        logger.exception("directory.manual_stamp_failed user=%s", instance.id)
