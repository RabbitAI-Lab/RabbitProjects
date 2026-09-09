"""成员治理服务（AUTH-006 §2.3/§2.4/§4.5）——批量角色 + 账号启停。

批量角色为**部分成功语义**（§2.3 豁免声明：治理操作回滚成本高，逐条分态返回；
结构性校验——缺字段 / >100 人 / 角色非法 / 含 OWNER 目标——整请求 400）。

账号启停（§4.5）：is_active 开关 + 会话吊销。API 面 0 秒生效——DRF
SessionAuthentication 拒 inactive（禁用后一切既有请求 401）；DB 会话行为纵深
（decode 扫描 django_session 删行）；Valkey 索引清理复用 AUTH-004 §4.3.5。

偏差登记（随 Sprint-5 ADR 汇总）：
  - APIKey 吊销计数恒 0——全站无 APIKey 基建（AUTH-004 §2.5 未落地）；
  - WS 连接踢出依赖 live 票据 120s 轮换（BR-05 的 ≤5s 对 session/API 面成立，
    WS 面为票据 TTL 上界；主动踢出通道归 COLLAB-004 后续增强）；
  - 启停/角色变更的 Activity 留痕：workspace 域无 Activity 表（issue_activities
    双轨仅覆盖 issue/project 域）——暂走 COLLAB-001 通知 + 日志，扩域归后续。
"""
from __future__ import annotations

import logging

from django.contrib.sessions.models import Session
from django.db import transaction
from django.utils import timezone

from plane.base.exception import AppException
from plane.db.models import (
    Project,
    ProjectMember,
    ProjectRole,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.roles import WorkspaceRole

logger = logging.getLogger("plane.db.services.member_admin")

#: 批量上限（BR-14，对齐 api-conventions §10.5 / §7.2）
BULK_ROLE_MAX = 100


class SkipReason:
    ALREADY_HAS_ROLE = "already_has_role"
    OWNER_IMPLICIT_FULL = "owner_implicit_full"
    NOT_WORKSPACE_MEMBER = "not_workspace_member"
    SELF_TARGET = "self_target"
    NOT_PROJECT_MEMBER = "not_project_member"


class FailReason:
    LAST_OWNER_DEMOTION = "last_owner_demotion"
    MEMBER_LIMIT = "member_limit"
    HIERARCHY = "hierarchy"


# ─────────────────────────────────────────────────────────────────────
# 批量角色（§2.3）
# ─────────────────────────────────────────────────────────────────────
@transaction.atomic
def bulk_role_workspace(*, workspace: Workspace, actor, user_ids: list, role: int) -> dict:
    """WS 域批量改角色：updated/skipped/failed 逐条分态（单事务——治理原子带）。

    skipped 里的 owner_implicit_full 行**不计入** skipped 计数（§2.3 OWNER 统一
    语义「不参与」）——响应体仍逐条列出，仅 updated=len / skipped 计数口径排除。
    """
    rows = WorkspaceMember.objects.filter(
        workspace=workspace, is_active=True, deleted_at__isnull=True,
        member_id__in=user_ids).select_related("member")
    by_user = {str(r.member_id): r for r in rows}

    updated, skipped, failed = 0, [], []
    for uid in user_ids:
        row = by_user.get(str(uid))
        if str(uid) == str(actor.id):
            skipped.append({"user_id": str(uid), "reason": SkipReason.SELF_TARGET})
            continue
        if row is None:
            skipped.append({"user_id": str(uid), "reason": SkipReason.NOT_WORKSPACE_MEMBER})
            continue
        if row.role == WorkspaceRole.OWNER:
            skipped.append({"user_id": str(uid), "reason": SkipReason.OWNER_IMPLICIT_FULL})
            continue
        if row.role == role:
            skipped.append({"user_id": str(uid), "reason": SkipReason.ALREADY_HAS_ROLE})
            continue
        op_role = WorkspaceMember.objects.filter(
            workspace=workspace, member=actor, is_active=True,
        ).values_list("role", flat=True).first() or 0
        # rbac §7.1 层级保护（双向）——批量路径逐条承接
        if not (row.role < op_role and role < op_role):
            failed.append({"user_id": str(uid), "reason": FailReason.HIERARCHY,
                           "message": "层级保护：目标或新角色不低于操作者"})
            continue
        old_role = row.role
        row.role = role
        row.updated_by = actor
        row.save(update_fields=["role", "updated_by", "updated_at"])
        updated += 1
        # BR-15：WS_MEMBER → WS_GUEST 降级联动（同事务）
        if old_role == WorkspaceRole.MEMBER and role == WorkspaceRole.GUEST:
            _cascade_demote_project_roles(member=row.member, actor=actor)
            # AUTH-008 BR-16 后半：越界自定义角色挂接同事务级联卸除
            from plane.db.services.custom_role import RoleService
            RoleService().cascade_revoke_on_ws_downgrade(
                workspace=workspace, target_user=row.member)
        def _notify(r: WorkspaceMember = row, o: int = old_role) -> None:
            _notify_role_changed(r, workspace=workspace, actor=actor, old_role=o)

        transaction.on_commit(_notify)
    return {
        "updated": updated,
        "skipped": [s for s in skipped if s["reason"] != SkipReason.OWNER_IMPLICIT_FULL],
        "failed": failed,
    }


def _cascade_demote_project_roles(*, member, actor) -> int:
    """BR-15：WS 降级为 GUEST 时，项目侧 CONTRIBUTOR+ 一并降为 COMMENTER。

    末位 PROJ_ADMIN 保护（rbac §7.2）优先：该成员是项目唯一 ADMIN 时跳过该项目
    不降（不因联动违反末位保护），记日志供审计。
    """
    demoted = 0
    rows = ProjectMember.objects.filter(
        member=member, is_active=True, deleted_at__isnull=True,
        role__gte=ProjectRole.CONTRIBUTOR).select_related("project")
    for pm in rows:
        if pm.role == ProjectRole.ADMIN:
            other_admins = ProjectMember.objects.filter(
                project=pm.project, role=ProjectRole.ADMIN, is_active=True,
                deleted_at__isnull=True).exclude(pk=pm.pk).exists()
            if not other_admins:
                logger.warning("member_admin.cascade_skip_last_admin project=%s member=%s",
                               pm.project_id, member.id)
                continue
        pm.role = ProjectRole.COMMENTER
        pm.updated_by = actor
        pm.save(update_fields=["role", "updated_by", "updated_at"])
        demoted += 1
    return demoted


def _notify_role_changed(row: WorkspaceMember, *, workspace, actor, old_role: int) -> None:
    from plane.bgtasks.notifications import send_workspace_notification

    send_workspace_notification.delay(
        receiver_id=str(row.member_id), event="workspace.member.role_changed",
        context={"actor_id": str(actor.id), "actor_display": actor.display_name,
                 "workspace_slug": workspace.slug, "workspace_name": workspace.name,
                 "old_role": WorkspaceRole(old_role).label,
                 "new_role": WorkspaceRole(row.role).label},
        title=f"你的角色已变更为 {WorkspaceRole(row.role).label}",
    )


@transaction.atomic
def bulk_role_project(*, project: Project, actor, member_ids: list, role: int,
                      op_role: int | None = None) -> dict:
    """项目域批量改角色（§4.4.3，同 §2.3 语义；角色枚举换项目四角色）。

    op_role 传操作者有效项目角色（get_project_or_404 已做 WS_ADMIN 隐式
    PROJ_ADMIN 提升，rbac §7.4）；缺省回查显式 ProjectMember 行。
    """
    rows = ProjectMember.objects.filter(
        project=project, is_active=True, deleted_at__isnull=True,
        member_id__in=member_ids).select_related("member")
    by_user = {str(r.member_id): r for r in rows}
    if op_role is None:
        op_role = ProjectMember.objects.filter(
            project=project, member=actor, is_active=True,
            deleted_at__isnull=True).values_list("role", flat=True).first()

    updated, skipped, failed = 0, [], []
    for uid in member_ids:
        row = by_user.get(str(uid))
        if str(uid) == str(actor.id):
            skipped.append({"user_id": str(uid), "reason": SkipReason.SELF_TARGET})
            continue
        if row is None:
            skipped.append({"user_id": str(uid), "reason": SkipReason.NOT_PROJECT_MEMBER})
            continue
        if row.role == role:
            skipped.append({"user_id": str(uid), "reason": SkipReason.ALREADY_HAS_ROLE})
            continue
        if row.role == ProjectRole.ADMIN and not ProjectMember.objects.filter(
                project=project, role=ProjectRole.ADMIN, is_active=True,
                deleted_at__isnull=True).exclude(pk=row.pk).exists():
            failed.append({"user_id": str(uid), "reason": FailReason.LAST_OWNER_DEMOTION,
                           "message": "末位 PROJ_ADMIN 保护（rbac §7.2）"})
            continue
        if op_role is None or not (row.role < op_role and role < op_role):
            failed.append({"user_id": str(uid), "reason": FailReason.HIERARCHY,
                           "message": "层级保护：目标或新角色不低于操作者"})
            continue
        row.role = role
        row.updated_by = actor
        row.save(update_fields=["role", "updated_by", "updated_at"])
        updated += 1
    return {"updated": updated, "skipped": skipped, "failed": failed}


# ─────────────────────────────────────────────────────────────────────
# 账号启停（§2.4/§4.5）
# ─────────────────────────────────────────────────────────────────────
class AccountService:

    @staticmethod
    def _guard_disable(*, target: User, actor: User, workspace: Workspace) -> None:
        if target.id == actor.id:
            raise AppException(
                "VALIDATION_ERROR", message="不能禁用自己",
                details=[{"field": "user", "code": "INVALID",
                          "message": "cannot disable yourself"}])
        row = WorkspaceMember.objects.filter(
            workspace=workspace, member=target, is_active=True,
            deleted_at__isnull=True).first()
        if row is None:
            raise AppException("RESOURCE_NOT_FOUND", message="成员不存在")
        if row.role == WorkspaceRole.OWNER:
            owners = WorkspaceMember.objects.filter(
                workspace=workspace, role=WorkspaceRole.OWNER, is_active=True,
                deleted_at__isnull=True).count()
            if owners <= 1:  # BR-03 末位 OWNER 禁停（⚠️ 不可管 Owner——任何 OWNER 均拒）
                raise AppException(
                    "RESOURCE_STATE_INVALID", message="最后所有者不可禁用",
                    details=[{"field": "member_id", "code": "INVALID",
                              "message": "cannot disable the last workspace owner"}])
            raise AppException("PERM_DENIED", message="不可禁用工作空间所有者")

    @transaction.atomic
    def disable(self, *, target: User, actor: User, workspace: Workspace) -> dict:
        """禁用：is_active=False + 审计列 + 会话四面吊销（§4.5；幂等）。"""
        self._guard_disable(target=target, actor=actor, workspace=workspace)
        if not target.is_active:  # 幂等重放：200 + revoked 全 0
            return {"user_id": str(target.id), "is_active": False,
                    "disabled_at": target.disabled_at.isoformat() if target.disabled_at else None,
                    "revoked": {"sessions": 0, "api_keys": 0, "ws_connections": 0}}
        target.is_active = False
        target.disabled_at = timezone.now()
        target.disabled_by = actor
        target.save(update_fields=["is_active", "disabled_at", "disabled_by"])
        sessions = _purge_django_sessions(target.id)
        from plane.account.sessions import delete_all_sessions as clear_index

        clear_index(target.id)  # Valkey 索引清理（AUTH-004 §4.3.5，尽力而为）
        transaction.on_commit(lambda: _notify_disabled(target, actor, workspace))
        return {
            "user_id": str(target.id), "is_active": False,
            "disabled_at": target.disabled_at.isoformat(),
            # api_keys 恒 0（无 APIKey 基建）；ws_connections 由票据 120s 轮换收敛
            "revoked": {"sessions": sessions, "api_keys": 0, "ws_connections": 0},
        }

    @transaction.atomic
    def enable(self, *, target: User, actor: User, workspace: Workspace) -> dict:
        """启用：恢复登录能力；会话已被清空（需重新登录），API Key 不恢复（BR-07）。"""
        row = WorkspaceMember.objects.filter(
            workspace=workspace, member=target, is_active=True,
            deleted_at__isnull=True).first()
        if row is None:
            raise AppException("RESOURCE_NOT_FOUND", message="成员不存在")
        if target.is_active:  # 幂等
            return {"user_id": str(target.id), "is_active": True, "disabled_at": None}
        target.is_active = True
        target.disabled_at = None
        target.disabled_by = None
        target.save(update_fields=["is_active", "disabled_at", "disabled_by"])
        transaction.on_commit(lambda: _notify_enabled(target, actor, workspace))
        return {"user_id": str(target.id), "is_active": True, "disabled_at": None}


def _purge_django_sessions(user_id) -> int:
    """删除该用户的全部 Django DB 会话（decode 扫描——session 行不带 user 索引）。"""
    uid = str(user_id)
    removed = 0
    for sk in list(Session.objects.filter(expire_date__gte=timezone.now())
                   .values_list("session_key", flat=True)):
        try:
            data = Session.objects.get(session_key=sk).get_decoded()
        except Session.DoesNotExist:
            continue
        if str(data.get("_auth_user_id", "")) == uid:
            Session.objects.filter(session_key=sk).delete()
            removed += 1
    return removed


def _notify_disabled(target: User, actor: User, workspace: Workspace) -> None:
    from plane.bgtasks.notifications import send_workspace_notification

    send_workspace_notification.delay(
        receiver_id=str(target.id), event="workspace.member_disabled",
        context={"actor_id": str(actor.id), "actor_display": actor.display_name,
                 "workspace_slug": workspace.slug, "workspace_name": workspace.name},
        title="你的账号已被管理员禁用",
    )


def _notify_enabled(target: User, actor: User, workspace: Workspace) -> None:
    from plane.bgtasks.notifications import send_workspace_notification

    send_workspace_notification.delay(
        receiver_id=str(target.id), event="workspace.member_enabled",
        context={"actor_id": str(actor.id), "actor_display": actor.display_name,
                 "workspace_slug": workspace.slug, "workspace_name": workspace.name},
        title="你的账号已恢复启用",
    )
