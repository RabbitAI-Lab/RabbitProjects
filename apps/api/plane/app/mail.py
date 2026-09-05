"""邮件投递层（AUTH-004 §4.4 注释 + INFRA-004 BR-14）。

SMTP 未配置（``settings.SMTP_HOST`` 为空）时降级为 ``plane.app.mail`` 日志输出；
IT-05 / EC-12 的「从 worker 日志提取重置链接」即由该层兑现。

**降级优先于真实投递**——不抛异常，保证事务内 ``on_commit`` 投递失败不阻塞业务流。
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from django.conf import settings

logger = logging.getLogger("plane.app.mail")


def deliver_reset_email(*, user_id: str, token: str) -> bool:
    """投递密码重置邮件。

    入口：`plane.account.tasks.send_reset_email`（Celery）。
    """
    from plane.db.models import User

    user = User.objects.filter(pk=user_id).first()
    if user is None:
        logger.warning("reset_email.user_missing user_id=%s", user_id)
        return False

    subject = "重置你的 RabbitProjects 密码"
    base = getattr(settings, "WEB_BASE_URL", "")
    link = f"{base}/reset-password?token={quote(token)}" if base else f"/reset-password?token={quote(token)}"
    body = (
        f"你在 {timezone_str()} 申请了密码重置。\n"
        f"30 分钟内有效，点击重置：{link}\n"
        f"若非本人操作请忽略本邮件，你的密码不会发生变化。"
    )

    smtp_host = getattr(settings, "SMTP_HOST", "") or ""
    if not smtp_host:
        # 降级：日志输出（运维 / IT-05 端到端验证从此处取链接）
        logger.info(
            "mail.reset_email.degraded user=%s to=%s link=%s",
            user_id, user.email, link,
        )
        return True

    # SMTP 真实投递：复用 Django send_mail；连不上抛 SMTPException 由调用方重试
    from smtplib import SMTPException

    from django.core.mail import send_mail

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=getattr(settings, "EMAIL_FROM", "noreply@example.com"),
            recipient_list=[user.email],
            fail_silently=False,
        )
        return True
    except SMTPException:
        raise


def timezone_str() -> str:
    from django.utils import timezone
    return timezone.localtime().strftime("%Y-%m-%d %H:%M")
