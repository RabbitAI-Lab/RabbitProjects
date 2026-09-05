"""工作空间成员生命周期服务（TEAM-002 §4.3）。

收口：
  BR-01 ~ BR-14 业务规则（批量邀请分拣 / 接受原子翻转 / 移除级联 / 层级 + 末位保护 / 所有权转让）。
  业务规则全部集中在本 Service；view 层只做参数解析与权限接线（INFRA-003 收口原则）。
"""
from __future__ import annotations

import hashlib
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from plane.app.permissions import (
    assert_can_manage_member,
)
from plane.base.exception import AppException
from plane.db.models import (
    ProjectMember,
    User,
    WorkspaceMember,
    WorkspaceMemberInvite,
)
from plane.db.models.roles import WorkspaceRole

MAX_INVITE_EMAILS = 20
MAX_WORKSPACE_MEMBERS = 100  # §2.8 P1 标准版软限

logger = logging.getLogger("plane.db.services.workspace_member")


class MemberService:
    """工作空间成员生命周期服务 —— 邀请 / 接受 / 角色 / 移除 / 转让。"""

    # ────────── helper ──────────

    @staticmethod
    def _get_user_id(email: str):
        """按归一邮箱取 user.id；调用前已通过 exists 校验。"""
        return User.objects.get(email=email).id

    @staticmethod
    def get_membership(user, workspace) -> WorkspaceMember | None:
        """取用户在指定工作空间的 active 成员行。"""
        return (
            WorkspaceMember.objects
            .filter(workspace=workspace, member=user, is_active=True, deleted_at__isnull=True)
            .first()
        )

    # ────────── 邀请（§4.3.1） ──────────

    def invite_members(
        self, *, workspace, actor, emails: list[str], role: int = WorkspaceRole.MEMBER
    ) -> list[dict]:
        """批量邀请：一次取数、内存分拣、逐条独立落库。

        返回逐条结果（added / invited / skipped / failed）。
        整体走 200；结构性错误（emails 缺失 / 超 20 / 角色非法）仍 400 整请求拒绝。
        """
        if role not in (WorkspaceRole.MEMBER, WorkspaceRole.ADMIN):
            raise AppException(
                "VALIDATION_ERROR",
                message="邀请角色仅支持成员或管理员",
                details=[{"field": "role", "code": "NOT_A_CHOICE",
                          "message": "邀请角色仅支持成员或管理员"}],
            )

        # 归一化 + 请求内去重（保留首现顺序）
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in emails:
            email = (raw or "").strip().lower()
            if not email:
                continue
            if email in seen:
                continue
            seen.add(email)
            normalized.append(email)

        if not normalized:
            return []

        # 一次取数：active 成员邮箱集
        member_emails = set(
            User.objects.filter(
                member_workspace__workspace=workspace,
                member_workspace__is_active=True,
                member_workspace__deleted_at__isnull=True,
            ).values_list("email", flat=True)
        )
        # pending 邀请映射（邮箱 → 邀请行）
        pending_invites: dict[str, WorkspaceMemberInvite] = {
            inv.email: inv
            for inv in WorkspaceMemberInvite.objects.filter(
                workspace=workspace, status=WorkspaceMemberInvite.Status.PENDING,
            )
        }

        # 当前 active 成员数 + 名额剩余
        active_count = WorkspaceMember.objects.filter(
            workspace=workspace, is_active=True, deleted_at__isnull=True,
        ).count()
        remaining_seats = MAX_WORKSPACE_MEMBERS - active_count

        results: list[dict] = []
        for email in normalized:
            if email in member_emails:
                results.append({"email": email, "status": "skipped", "reason": "already_member"})
                continue

            registered = User.objects.filter(email=email).exists()
            if registered:
                # 已注册 → 直加或复活软删行
                if remaining_seats <= 0:
                    results.append({
                        "email": email, "status": "failed", "reason": "member_limit",
                        "message": f"已达标准版成员上限（{MAX_WORKSPACE_MEMBERS}）",
                    })
                    continue
                user_id = self._get_user_id(email)
                member, revived = self._add_or_reactivate(
                    workspace=workspace, user_id=user_id, role=role, actor=actor,
                )
                remaining_seats -= 1
                results.append({
                    "email": email, "status": "added",
                    "member_id": str(member.id), "role": role, "revived": revived,
                })
            else:
                # 未注册 → token 邀请（或顺延既有 pending）
                invite, token, refreshed = self._upsert_invite(
                    workspace=workspace, actor=actor, email=email, role=role,
                    existing=pending_invites.get(email),
                )
                results.append({
                    "email": email, "status": "invited",
                    "invite_id": str(invite.id),
                    "expires_at": invite.expires_at.isoformat(),
                    "refreshed": refreshed,
                    "_token_plain": token,  # 内部传递：用于降级回显；序列化时清除
                })
        return results

    @staticmethod
    @transaction.atomic
    def _add_or_reactivate(*, workspace, user_id, role, actor):
        """已注册非成员 → 直加或复活软删行（UPDATE 而非 INSERT，§2.3 复活语义）。"""
        soft_deleted = (
            WorkspaceMember.all_objects
            .select_for_update()
            .filter(workspace=workspace, member_id=user_id, deleted_at__isnull=False)
            .first()
        )
        if soft_deleted is not None:
            soft_deleted.deleted_at = None
            soft_deleted.is_active = True
            soft_deleted.role = role
            soft_deleted.updated_at = timezone.now()
            soft_deleted.updated_by = actor
            soft_deleted.save(update_fields=[
                "deleted_at", "is_active", "role", "updated_at", "updated_by",
            ])
            member, revived = soft_deleted, True
        else:
            member = WorkspaceMember.objects.create(
                workspace=workspace, member_id=user_id, role=role,
                is_active=True, created_by=actor, updated_by=actor,
            )
            revived = False

        transaction.on_commit(
            lambda m=member: _notify_member_added(m, actor)
        )
        return member, revived

    @staticmethod
    def _upsert_invite(*, workspace, actor, email, role, existing):
        """创建或顺延 pending 邀请，返回 (invite, token 明文, refreshed)。"""
        if existing is not None:
            existing.expires_at = timezone.now() + timedelta(
                days=WorkspaceMemberInvite.INVITE_TTL_DAYS,
            )
            # 既有行的 token 明文不可再现，生成新 token 覆盖（旧链接失效）
            token, token_hash = WorkspaceMemberInvite.issue_token()
            existing.token_hash = token_hash
            existing.save(update_fields=["expires_at", "token_hash", "updated_at", "updated_by"])
            return existing, token, True

        token, token_hash = WorkspaceMemberInvite.issue_token()
        invite = WorkspaceMemberInvite.objects.create(
            workspace=workspace, email=email, role=role,
            token_hash=token_hash,
            expires_at=timezone.now() + timedelta(days=WorkspaceMemberInvite.INVITE_TTL_DAYS),
            invited_by=actor, created_by=actor, updated_by=actor,
        )
        return invite, token, False

    # ────────── 接受（§4.3.2） ──────────

    @transaction.atomic
    def accept_invite(self, *, token: str, actor: User) -> WorkspaceMember:
        """接受邀请：哈希检索 + 邮箱绑定校验 + 原子状态翻转 + 成员落库。"""
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        invite = (
            WorkspaceMemberInvite.objects
            .select_for_update()
            .filter(token_hash=token_hash)
            .first()
        )
        if invite is None or invite.status != WorkspaceMemberInvite.Status.PENDING:
            raise AppException(
                "VALIDATION_ERROR",
                message="邀请无效或已被使用",
                details=[{"field": "token", "code": "INVALID", "message": "邀请无效或已被使用"}],
            )
        if invite.expires_at <= timezone.now():
            raise AppException(
                "VALIDATION_ERROR",
                message="邀请已过期，请联系管理员重新发送",
                details=[{"field": "token", "code": "INVALID", "message": "邀请已过期"}],
            )
        if invite.email != actor.email.lower():
            # BR-04 token 抢用防护
            raise AppException("PERM_DENIED", message="该邀请面向其他邮箱，请切换账号后重试")

        return self._do_accept(invite=invite, actor=actor)

    @staticmethod
    @transaction.atomic
    def _do_accept(*, invite: WorkspaceMemberInvite, actor: User) -> WorkspaceMember:
        """接受邀请的原子单元（行已 select_for_update 锁住）。

        跳过 token 校验（注册钩子路径不需要 token）。
        §2.8 软限二次校验：仅对「消耗名额」路径生效；早期 active 成员兜底分支不消耗名额。
        """
        existing = (
            WorkspaceMember.objects
            .select_for_update()
            .filter(workspace=invite.workspace, member=actor)
            .first()
        )
        if existing is not None and existing.deleted_at is None and existing.is_active:
            # 已是 active 成员（兜底）→ 标记邀请 accepted 后直接返回
            invite.status = WorkspaceMemberInvite.Status.ACCEPTED
            invite.accepted_at = invite.accepted_at or timezone.now()
            invite.accepted_by = actor
            invite.save(update_fields=["status", "accepted_at", "accepted_by", "updated_at"])
            return existing

        # §2.8 软限二次校验（仅对复活 / 新建两条「消耗名额」路径生效）
        active_count = WorkspaceMember.objects.filter(
            workspace=invite.workspace, is_active=True, deleted_at__isnull=True,
        ).count()
        if active_count + 1 > MAX_WORKSPACE_MEMBERS:
            raise AppException(
                "RESOURCE_LIMIT_EXCEEDED",
                message=f"工作空间已达标准版成员上限（{MAX_WORKSPACE_MEMBERS}），无法接受该邀请",
            )

        if existing is not None and existing.deleted_at is not None:
            # 复活软删行（裸 unique_together 不允许 INSERT 新行，§2.3）
            existing.deleted_at = None
            existing.is_active = True
            existing.role = invite.role
            existing.updated_at = timezone.now()
            existing.updated_by = invite.invited_by or actor
            existing.save(update_fields=[
                "deleted_at", "is_active", "role", "updated_at", "updated_by",
            ])
            member = existing
        else:
            member = WorkspaceMember.objects.create(
                workspace=invite.workspace, member=actor,
                role=invite.role, is_active=True,
                created_by=invite.invited_by or actor,
                updated_by=invite.invited_by or actor,
            )

        invite.status = WorkspaceMemberInvite.Status.ACCEPTED
        invite.accepted_at = timezone.now()
        invite.accepted_by = actor
        invite.save(update_fields=["status", "accepted_at", "accepted_by", "updated_at"])

        transaction.on_commit(lambda: _notify_member_added(member, invite.invited_by or actor))
        return member

    @staticmethod
    def accept_pending_invites(user: User) -> list[WorkspaceMember]:
        """注册钩子（AUTH-001 注册事务内调用）：自动接受所有面向该邮箱的 pending 邀请。

        调用顺序硬性约定：必须在 create_default_workspace(user) 之后。
        邮箱在注册时已验证（AUTH-001），故跳过 BR-04 的登录邮箱比对。
        """
        members: list[WorkspaceMember] = []
        for invite in (
            WorkspaceMemberInvite.objects
            .filter(email=user.email.lower(), status=WorkspaceMemberInvite.Status.PENDING)
            .select_for_update()
        ):
            member = MemberService._do_accept(invite=invite, actor=user)
            members.append(member)
        return members

    # ────────── 移除 / 退出（§4.3.3） ──────────

    @staticmethod
    @transaction.atomic
    def _soft_delete_with_cascade(*, workspace, membership: WorkspaceMember, actor) -> None:
        """软删除成员 + 级联回收 ProjectMember；私有 helper。"""
        membership.deleted_at = timezone.now()
        membership.is_active = False
        membership.updated_by = actor
        membership.save(update_fields=["deleted_at", "is_active", "updated_by", "updated_at"])
        # 级联：ProjectMember.workspace 冗余列使之为单表 UPDATE
        ProjectMember.objects.filter(
            workspace=workspace, member=membership.member, deleted_at__isnull=True,
        ).update(deleted_at=timezone.now(), updated_at=timezone.now())

    @transaction.atomic
    def remove_member(self, *, workspace, member: WorkspaceMember, actor) -> None:
        if member.role == WorkspaceRole.OWNER:
            raise AppException("RESOURCE_STATE_INVALID", message="所有者不可移除，请先转让所有权")
        if member.member_id == actor.id:
            raise AppException(
                "VALIDATION_ERROR",
                message="不能移除自己，请使用退出团队",
                details=[{"field": "member_id", "code": "INVALID",
                          "message": "不能移除自己，请使用退出团队"}],
            )
        operator_membership = self.get_membership(actor, workspace)
        if operator_membership is None:
            raise AppException("PERM_NOT_WORKSPACE_MEMBER", message="你不是该工作空间成员")
        # rbac §7.1：层级保护
        assert_can_manage_member(
            operator_role=operator_membership.role, target_role=member.role,
        )

        self._soft_delete_with_cascade(workspace=workspace, membership=member, actor=actor)
        transaction.on_commit(
            lambda: _notify_member_event(member.member_id, "workspace.member.removed",
                                         workspace=workspace, actor=actor,
                                         actor_display=actor.display_name)
        )

    @transaction.atomic
    def leave_workspace(self, *, workspace, actor) -> None:
        membership = self.get_membership(actor, workspace)
        if membership is None:
            raise AppException("PERM_NOT_WORKSPACE_MEMBER", message="你不是该工作空间成员")
        if membership.role == WorkspaceRole.OWNER:
            raise AppException("RESOURCE_STATE_INVALID", message="所有者不能退出团队，请先转让所有权")
        active_count = WorkspaceMember.objects.filter(
            workspace=workspace, is_active=True, deleted_at__isnull=True,
        ).count()
        if active_count <= 1:
            raise AppException("RESOURCE_STATE_INVALID", message="团队仅剩你一名成员，无法退出")

        self._soft_delete_with_cascade(workspace=workspace, membership=membership, actor=actor)
        transaction.on_commit(
            lambda: _notify_member_event(
                actor.id, "workspace.member.removed",
                workspace=workspace, actor=actor,
                actor_display=actor.display_name, self_initiated=True,
            )
        )

    # ────────── 角色 / 转让（§4.3.4） ──────────

    @transaction.atomic
    def change_role(self, *, workspace, member: WorkspaceMember, new_role: int, actor) -> WorkspaceMember:
        if member.role == WorkspaceRole.OWNER:
            raise AppException(
                "VALIDATION_ERROR",
                message="所有者角色仅能通过转让变更",
                details=[{"field": "role", "code": "NOT_A_CHOICE",
                          "message": "所有者角色仅能通过转让变更"}],
            )
        if member.member_id == actor.id:
            raise AppException(
                "VALIDATION_ERROR",
                message="不能修改自己的角色",
                details=[{"field": "role", "code": "INVALID", "message": "不能修改自己的角色"}],
            )
        if new_role == WorkspaceRole.OWNER:
            raise AppException(
                "VALIDATION_ERROR",
                message="所有者仅能通过转让所有权产生",
                details=[{"field": "role", "code": "NOT_A_CHOICE",
                          "message": "所有者仅能通过转让所有权产生"}],
            )
        if new_role not in (WorkspaceRole.MEMBER, WorkspaceRole.ADMIN):
            raise AppException(
                "VALIDATION_ERROR",
                message="角色非法",
                details=[{"field": "role", "code": "NOT_A_CHOICE", "message": "角色非法"}],
            )

        operator_membership = self.get_membership(actor, workspace)
        if operator_membership is None:
            raise AppException("PERM_NOT_WORKSPACE_MEMBER", message="你不是该工作空间成员")
        operator_role = operator_membership.role
        # rbac §7.1：层级保护（双向：被改角色 < 操作者 ∧ 新角色 < 操作者）
        assert_can_manage_member(
            operator_role=operator_role, target_role=member.role, new_role=new_role,
        )

        old_role = member.role
        member.role = new_role
        member.updated_by = actor
        member.save(update_fields=["role", "updated_by", "updated_at"])

        transaction.on_commit(
            lambda: _notify_member_event(
                member.member_id, "workspace.member.role_changed",
                workspace=workspace, actor=actor, actor_display=actor.display_name,
                old_role=old_role, new_role=new_role,
            )
        )
        return member

    @transaction.atomic
    def transfer_ownership(self, *, workspace, target: WorkspaceMember, actor,
                            confirm_name: str) -> dict:
        if confirm_name != workspace.name:
            raise AppException(
                "VALIDATION_ERROR",
                message="输入的团队名称不匹配",
                details=[{"field": "confirm_name", "code": "INVALID",
                          "message": "输入的团队名称不匹配"}],
            )
        if target.role != WorkspaceRole.ADMIN or not target.is_active:
            raise AppException(
                "VALIDATION_ERROR",
                message="转让目标必须是在职管理员",
                details=[{"field": "new_owner_member_id", "code": "INVALID",
                          "message": "转让目标必须是在职管理员"}],
            )
        if target.member_id == actor.id:
            raise AppException(
                "VALIDATION_ERROR",
                message="不能转让给自己",
                details=[{"field": "new_owner_member_id", "code": "INVALID",
                          "message": "不能转让给自己"}],
            )

        current = self.get_membership(actor, workspace)
        if current is None or current.role != WorkspaceRole.OWNER:
            raise AppException("PERM_ROLE_INSUFFICIENT", message="仅所有者可以转让所有权")

        # 固定 id 序加锁防死锁；两行同锁保证「恰一 OWNER」不变量无真空窗口
        rows = list(
            WorkspaceMember.objects.select_for_update()
            .filter(pk__in=sorted([target.pk, current.pk]))
            .order_by("pk")
        )
        if len(rows) != 2 or {r.role for r in rows} != {WorkspaceRole.OWNER, WorkspaceRole.ADMIN}:
            raise AppException("RESOURCE_CONFLICT", message="成员状态已变化，请刷新后重试")
        # rows 是新取的 ORM 对象；bulk_update 只持久化 rows，target/current 仅用于上下文读取
        # 这里把目标角色写到 rows 对应位置上，避免「改 target.current 但 bulk_update 写 rows」的错位
        rows_by_pk = {r.pk: r for r in rows}
        rows_by_pk[target.pk].role = WorkspaceRole.OWNER
        rows_by_pk[current.pk].role = WorkspaceRole.ADMIN
        for r in rows:
            r.updated_by = actor
        WorkspaceMember.objects.bulk_update(rows, ["role", "updated_by", "updated_at"])
        workspace.owner = target.member
        workspace.save(update_fields=["owner", "updated_at"])

        transaction.on_commit(
            lambda: _notify_member_event(
                target.member_id, "workspace.member.role_changed",
                workspace=workspace, actor=actor, actor_display=actor.display_name,
                old_role=WorkspaceRole.ADMIN, new_role=WorkspaceRole.OWNER,
                ownership_transferred=True,
            )
        )
        transaction.on_commit(
            lambda: _notify_member_event(
                actor.id, "workspace.member.role_changed",
                workspace=workspace, actor=actor, actor_display=actor.display_name,
                old_role=WorkspaceRole.OWNER, new_role=WorkspaceRole.ADMIN,
                ownership_transferred=True,
            )
        )
        return {
            "new_owner": {
                "member_id": str(target.id),
                "user_id": str(target.member_id),
                "display_name": target.member.display_name,
            },
            "previous_owner_role": WorkspaceRole.ADMIN,
        }

    # ────────── 邀请治理 ──────────

    @staticmethod
    @transaction.atomic
    def revoke_invite(*, workspace, invite_id, actor) -> bool:
        """撤销 pending 邀请；幂等：已 revoked 视为成功。"""
        invite = (
            WorkspaceMemberInvite.objects
            .select_for_update()
            .filter(id=invite_id, workspace=workspace)
            .first()
        )
        if invite is None:
            return False
        if invite.status == WorkspaceMemberInvite.Status.PENDING:
            invite.status = WorkspaceMemberInvite.Status.REVOKED
            invite.updated_by = actor
            invite.save(update_fields=["status", "updated_by", "updated_at"])
        return True


# ────────── 通知投递（on_commit 钩子内调用） ──────────

def _safe_delay(task, *args, **kwargs):
    """业务层投递通知的兜底 —— broker 不可用时静默失败。

    通知失败不应阻断已提交的业务事务；on_commit 钩子异常会被全局处理器收为 500，
    违背 §4.4「通知失败入日志不抛错」约定。
    """
    try:
        task.delay(*args, **kwargs)
    except Exception as exc:                              # noqa: BLE001
        logger.warning("notify.delivery_failed task=%s exc=%s",
                       getattr(task, "name", task), exc)


def _notify_member_added(member: WorkspaceMember, actor) -> None:
    """workspace.member.added 事件投递 —— send_workspace_notification 重试 3 次 + 去重。"""
    from plane.bgtasks.notifications import send_workspace_notification

    _safe_delay(
        send_workspace_notification,
        receiver_id=str(member.member_id),
        event="workspace.member.added",
        context={
            "workspace_slug": str(member.workspace_id),
            "actor_id": str(actor.id) if actor else None,
            "actor": getattr(actor, "display_name", "系统"),
            "role": member.role,
        },
    )


def _notify_member_event(receiver_id, event: str, *, workspace, actor,
                         actor_display: str, **extra) -> None:
    from plane.bgtasks.notifications import send_workspace_notification

    context = {
        "workspace_slug": workspace.slug,
        "actor_id": str(actor.id) if actor else None,
        "actor": actor_display,
    }
    context.update(extra)
    _safe_delay(
        send_workspace_notification,
        receiver_id=str(receiver_id), event=event, context=context,
    )
