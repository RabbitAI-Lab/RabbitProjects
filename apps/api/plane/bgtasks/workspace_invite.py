"""工作空间邀请邮件投递（TEAM-002 §4.4）。

SMTP 未配置（SMTP_HOST 为空）时按 INFRA-004 降级口径走日志投递，邀请本身不受影响。
"""
from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from plane.db.models.workspace import WorkspaceMemberInvite

logger = logging.getLogger("plane.bgtasks.workspace_invite")


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_invite_email(self, invite_id: str, token: str) -> bool:
    """投递邀请邮件 —— 事务提交后触发（on_commit）。

    任务内仅传 ID + token（token 明文不入库，故此处必须透传；重试时
    token 仍可用——status 仍为 pending 时）。
    """
    try:
        invite = WorkspaceMemberInvite.all_objects.get(id=invite_id)
    except WorkspaceMemberInvite.DoesNotExist:
        logger.warning("invite_email.invite_missing invite_id=%s", invite_id)
        return False

    if invite.status != WorkspaceMemberInvite.Status.PENDING:
        return False  # 已撤销 / 过期 / 已使用 → 不再投递（幂等）

    link = f"{getattr(settings, 'APP_BASE_URL', '')}/invite/{token}"
    subject = f"【{invite.workspace.name}】邀请你加入团队"
    body = (
        f"{invite.invited_by.display_name if invite.invited_by else '系统'} "
        f"邀请你加入「{invite.workspace.name}」团队。\n\n"
        f"点击链接接受邀请（7 天内有效）：\n{link}\n\n"
        f"链接过期时间：{invite.expires_at.date().isoformat()}\n"
    )

    smtp_host = getattr(settings, "SMTP_HOST", "")
    if not smtp_host:
        # 降级模式：未配置 SMTP → 日志投递，邀请本身不受影响
        logger.warning(
            "invite_email.degraded invite_id=%s email=%s link=%s",
            invite_id, invite.email, link,
        )
        return True

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@rabbit.dev"),
            recipient_list=[invite.email],
            fail_silently=False,
        )
        return True
    except Exception as exc:
        logger.warning("invite_email.retry invite_id=%s exc=%s", invite_id, exc)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.exception("invite_email.giveup invite_id=%s", invite_id)
            return False


@shared_task
def expire_invites() -> int:
    """beat 每日 03:30 —— 将过期 pending 邀请置 expired（行保留供审计）。

    实时有效性由 ``WorkspaceMemberInvite.is_consumable`` 兜底；本任务只把状态落库，
    使待接受面板不再显示过期项。
    """
    return WorkspaceMemberInvite.objects.filter(
        status=WorkspaceMemberInvite.Status.PENDING,
        expires_at__lt=timezone.now(),
    ).update(status=WorkspaceMemberInvite.Status.EXPIRED, updated_at=timezone.now())
