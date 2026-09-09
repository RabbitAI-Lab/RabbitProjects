"""强制 SSO 门（AUTH-009 §2.4/§4.3）。

执行顺序唯一定义：AUTH-001 密码校验通过后、建会话前调用；密码错误者在
更早的校验步即收 401，不经本门。route/（登录路由发现）与本门共用同一
查询口径，防两套判定漂移。
"""
from __future__ import annotations

from plane.base.exception import AppException
from plane.db.models import IdentityProvider


def find_enforced_idp_for_email(email: str) -> IdentityProvider | None:
    """命中「强制 SSO 的 Workspace 成员」判定（route/ 与 sign-in 拦截共用）。"""
    return (IdentityProvider.objects
            .filter(enforce_sso=True, is_enabled=True,
                    workspace__workspace_member__is_active=True,
                    workspace__workspace_member__deleted_at__isnull=True,
                    workspace__workspace_member__member__email__iexact=email)
            .first())


def enforce_sso_gate(email: str) -> None:
    """密码校验通过后、建会话前的策略门（§2.4 状态码裁定：403 PERM_SSO_REQUIRED）。"""
    from django.conf import settings as dj_settings

    idp = find_enforced_idp_for_email(email)
    if idp and email.lower() not in dj_settings.SSO_BREAK_GLASS_EMAILS:
        raise AppException(
            "PERM_SSO_REQUIRED",
            message="该组织已启用强制 SSO 登录",
            details=[{"field": "sso_login_url", "code": "SSO_LOGIN_URL",
                      "message": f"/api/v1/auth/sso/{idp.workspace.slug}/sign-in/"}],
        )
