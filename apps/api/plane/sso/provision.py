"""SSO 用户解析 / JIT 开通 / 属性同步（AUTH-009 §2.3/§4.3）。"""
from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Q

from plane.db.models import (
    CustomRole,
    Department,
    ProjectRoleAssignment,
    SSOAccount,
    User,
    WorkspaceMember,
)
from plane.db.models.roles import WorkspaceRole

logger = logging.getLogger("plane.api.sso")


class ClaimRequired(Exception):
    """邮箱已存在且设过密码 → 视图捕获后把 sso_txn 重写为 pending_claim 态。"""

    def __init__(self, user: User):
        self.user = user
        super().__init__("claim required")


def _ws_role_for(value: str) -> int:
    return {"WS_MEMBER": WorkspaceRole.MEMBER,
            "WS_GUEST": WorkspaceRole.GUEST}.get(value, WorkspaceRole.MEMBER)


@transaction.atomic
def jit_provision(idp, claims: dict) -> User:
    """JIT 开通：建 User + SSOAccount + WorkspaceMember（默认角色 + 部门映射）。

    角色映射（claim_role）仅首次 JIT 生效（BR-10）：按 CustomRole 名称精确
    匹配（不区分大小写）逐个挂接；无匹配记警告继续。
    """
    email = str(claims["email"]).lower()
    sub = str(claims["sub"])
    user = User.objects.create_user(
        email=email, password=None,  # type: ignore[arg-type]  # unusable password
        display_name=claims.get("name") or email.split("@")[0],
    )
    SSOAccount.objects.create(idp=idp, user=user, subject=sub,
                              email_at_binding=email)
    wm, _ = WorkspaceMember.objects.get_or_create(
        workspace=idp.workspace, member=user,
        defaults={"role": _ws_role_for(idp.jit_default_role),
                  "created_by": idp.created_by},
    )
    # 部门映射（BR-09：仅名称精确匹配，不自动建部门）
    dept_name = claims.get(idp.claim_department or "department")
    if dept_name:
        dept = Department.objects.filter(
            workspace=idp.workspace, name__iexact=str(dept_name),
            deleted_at__isnull=True).first()
        if dept:
            wm.department = dept
            wm.save(update_fields=["department_id", "updated_at"])
        else:
            logger.warning("sso.jit.dept_unmapped idp=%s dept=%s",
                           idp.id, dept_name)
    # 角色映射（BR-10：仅首次 JIT；按名称挂自定义角色）
    role_claims = claims.get(idp.claim_role or "groups") or []
    if isinstance(role_claims, str):
        role_claims = [role_claims]
    for role_name in role_claims:
        cr = (CustomRole.objects
              .filter(Q(project__workspace=idp.workspace), name__iexact=str(role_name),
                      deleted_at__isnull=True).first())
        if cr:
            ProjectRoleAssignment.objects.get_or_create(
                project=cr.project, user=user, role=cr,
                defaults={"created_by": idp.created_by})
        else:
            logger.warning("sso.jit.role_unmapped idp=%s role=%s",
                           idp.id, role_name)
    return user


def sync_profile(user: User, idp, claims: dict) -> None:
    """每次登录的属性同步（§2.3）：姓名 IdP 为准；部门按名称匹配不自动建。"""
    if claims.get("name"):
        user.display_name = claims["name"]
    if claims.get("email"):
        email = str(claims["email"]).lower()
        # 唯一性冲突 → 拒绝登录由调用方（_resolve_user）前置处理，此处只同步
        if user.email.lower() != email:
            if User.objects.filter(email__iexact=email) \
                    .exclude(pk=user.pk).exists():
                logger.warning("sso.email_conflict user=%s new=%s",
                               user.id, email)
            else:
                user.email = email
    dept_name = claims.get(idp.claim_department or "department")
    if dept_name:
        dept = Department.objects.filter(
            workspace=idp.workspace, name__iexact=str(dept_name),
            deleted_at__isnull=True).first()
        if dept:
            WorkspaceMember.objects.filter(
                workspace=idp.workspace, member=user, is_active=True,
                deleted_at__isnull=True,
            ).update(department=dept)
        else:
            logger.warning("sso.sync.dept_unmapped idp=%s dept=%s",
                           idp.id, dept_name)
    user.save(update_fields=["display_name", "email"])


def resolve_user(idp, claims: dict) -> User:
    """绑定解析：已绑定→属性同步；未绑定·邮箱已有密码→ClaimRequired；否则 JIT。"""
    sub = str(claims["sub"])
    email = str(claims["email"]).lower()
    binding = SSOAccount.objects.filter(idp=idp, subject=sub).first()
    if binding:
        user = binding.user
    else:
        local = User.objects.filter(email__iexact=email).first()
        if local and local.has_usable_password():
            raise ClaimRequired(local)
        # 邮箱唯一性冲突防御（IdP 改邮箱撞他人）：同邮箱不可用账号（无密码）
        # 也可直接绑定复用（本地无密码账号视为未激活的预建壳）
        if local:
            SSOAccount.objects.create(idp=idp, user=local, subject=sub,
                                      email_at_binding=email)
            user = local
        else:
            user = jit_provision(idp, claims)
    if idp.sync_profile_on_login:
        sync_profile(user, idp, claims)
    return user
