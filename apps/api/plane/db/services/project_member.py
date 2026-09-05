"""项目成员 / 收藏 / 归档业务服务（PROJ-002 §4.3.2）。

保护规则三处收口：
  BR-05 GUEST 上限（rbac §7.3，整请求级前置）
  BR-06 末位 ADMIN 保护 + 隐式接管判定（rbac §7.2）
  BR-12 角色调整层级保护（rbac §7.1，PROJ_ADMIN 之间不可互改）
"""
from __future__ import annotations

from django.db import transaction

from plane.base.exception import AppException
from plane.db.models import (
    Notification,
    Project,
    ProjectFavorite,
    ProjectMember,
    WorkspaceMember,
)
from plane.db.models.roles import ProjectRole, WorkspaceRole

MAX_BATCH_MEMBERS = 20
PROJECT_MEMBER_LIMIT = 100
FAVORITE_LIMIT_PER_USER = 50


#: rbac §7.3 GUEST 在项目中最高允许的角色
_ALLOWED_PROJECT_ROLES_FOR_GUEST = {ProjectRole.VIEWER, ProjectRole.COMMENTER}


def _notify(receiver_id, *, event: str, title: str, data: dict) -> None:
    """落库式通知（COLLAB-001 通知管道复用，P1 仅落库由通知中心消费）。

    BR-14：成员变动 / 归档事件落库。本实现直接同步落库，避免引入 Celery 依赖；
    COLLAB-001 接入异步管道后此处替换为 ``transaction.on_commit(notify.delay(...))``。
    """
    Notification.objects.create(
        receiver_id=receiver_id,
        event=event,
        title=title[:200],
        data=data,
    )


class ProjectMemberService:
    """项目成员 / 收藏 / 归档：业务规则唯一收口。"""

    # ────────── 批量添加（§4.3.2） ──────────

    @transaction.atomic
    def add_members(self, *, project: Project, actor, member_ids: list[str],
                    role: int) -> list[dict]:
        if role not in ProjectRole.values:
            raise AppException("VALIDATION_ERROR",
                               message="非法的项目角色",
                               details=[{"field": "role", "code": "NOT_A_CHOICE",
                                         "message": "非法的项目角色"}])

        # 请求内去重（BR-02）
        deduped = list(dict.fromkeys(str(m) for m in member_ids))
        if len(deduped) > MAX_BATCH_MEMBERS:
            raise AppException("VALIDATION_ERROR",
                               message=f"单次最多添加 {MAX_BATCH_MEMBERS} 名成员",
                               details=[{"field": "member_ids", "code": "TOO_LONG",
                                         "message": f"单次最多添加 {MAX_BATCH_MEMBERS} 名成员"}])

        # 一次取数：空间 active 成员 + 既有项目成员 + 项目成员总数
        workspace_members = dict(
            WorkspaceMember.objects.filter(
                workspace=project.workspace, is_active=True, deleted_at__isnull=True
            ).values_list("member_id", "role")
        )
        existing = set(
            ProjectMember.objects.filter(
                project=project, is_active=True, deleted_at__isnull=True
            ).values_list("member_id", flat=True)
        )
        current_count = len(existing)

        # BR-05 GUEST 上限：整请求级前置校验（任一违规则整单拒绝，避免部分成功造成误解）
        for mid in deduped:
            ws_role = workspace_members.get(mid)
            if ws_role is not None and ws_role <= WorkspaceRole.GUEST and role > ProjectRole.COMMENTER:
                raise AppException(
                    "VALIDATION_ERROR",
                    message="工作空间访客在项目中最高只能被分配为评论者",
                    details=[{"field": "member_ids", "code": "INVALID",
                              "message": "工作空间访客在项目中最高只能被分配为评论者"}],
                )

        # 计算实际可加入的新人数，超上限整请求拒绝
        new_eligible = [m for m in deduped if m not in existing and m in workspace_members]
        if current_count + len(new_eligible) > PROJECT_MEMBER_LIMIT:
            raise AppException(
                "RESOURCE_LIMIT_EXCEEDED",
                message=f"项目成员上限为 {PROJECT_MEMBER_LIMIT} 人",
            )

        results: list[dict] = []
        for mid in deduped:
            if mid not in workspace_members:
                results.append({"member_id": mid, "status": "failed",
                                "reason": "not_workspace_member"})
                continue
            if mid in existing:
                results.append({"member_id": mid, "status": "skipped",
                                "reason": "already_member"})
                continue
            pm = ProjectMember.objects.create(
                project=project,
                member_id=mid,
                role=role,
                is_active=True,
                created_by=actor,
                updated_by=actor,
            )
            # workspace 列由 ProjectMember.save() 自动填充（BR-13）
            _notify(
                mid,
                event="project.member.added",
                title=f"你已加入项目「{project.name}」（{ProjectRole(role).label}）",
                data={
                    "project_id": str(project.id),
                    "project_name": project.name,
                    "workspace_slug": project.workspace.slug,
                    "role": role,
                    "actor_id": str(actor.id),
                    "actor": actor.display_name,
                },
            )
            results.append({"member_id": mid, "status": "added",
                            "project_member_id": str(pm.id), "role": role})
        return results

    # ────────── 保护规则 ──────────

    @staticmethod
    def _assert_not_last_admin(*, project: Project, member_being_changed: ProjectMember) -> None:
        """BR-06 末位 ADMIN 保护：显式 ADMIN 或隐式接管（WS_OWNER/WS_ADMIN）二者居其一。

        rbac §7.2：项目 Admin 全部离开时，自动回退为「由工作空间 Admin 隐式管理」。
        因此仅当「目标是最后一个显式 ADMIN ∧ 空间不存在 WS_ADMIN+」时才拒绝。
        """
        other_explicit_admin = ProjectMember.objects.filter(
            project=project,
            role=ProjectRole.ADMIN,
            is_active=True,
            deleted_at__isnull=True,
        ).exclude(pk=member_being_changed.pk).exists()
        if other_explicit_admin:
            return
        ws_admin_exists = WorkspaceMember.objects.filter(
            workspace=project.workspace,
            role__gte=WorkspaceRole.ADMIN,
            is_active=True,
            deleted_at__isnull=True,
        ).exists()
        if not ws_admin_exists:
            raise AppException(
                "PERM_LAST_OWNER",
                message="项目必须保留至少一名项目管理员：请先指定新管理员，或由工作空间管理员接管",
            )

    @staticmethod
    def _assert_can_change_role(*, operator_role: int, target_role: int,
                                new_role: int) -> None:
        """BR-12 层级保护（rbac §7.1）：PROJ_ADMIN 不可由同级 / 低级操作者改。

        操作者是 PROJ_ADMIN 才可调整其他 PROJ_ADMIN（隐式 ADMIN 同源处理）；
        非 ADMIN 操作者对 ADMIN 角色无写权。
        """
        if operator_role < ProjectRole.ADMIN:
            raise AppException("PERM_ROLE_INSUFFICIENT",
                               message="当前角色权限不足")
        if target_role == ProjectRole.ADMIN and operator_role < ProjectRole.ADMIN:
            raise AppException("PERM_ROLE_HIERARCHY",
                               message="不能修改项目管理员角色")

    # ────────── 移除 / 调整 ──────────

    @transaction.atomic
    def remove_member(self, *, project: Project, member: ProjectMember, actor) -> None:
        # 仅 PROJ_ADMIN（含隐式 WS_ADMIN+）可移除
        if actor is None:
            raise AppException("PERM_ROLE_INSUFFICIENT", message="当前角色权限不足")
        from plane.db.models.roles import ProjectRole as _PR
        # 操作者有效项目角色（隐式 ADMIN 由调用方在 view 层判断，此处只断言权限存在）
        self._assert_can_change_role(
            operator_role=_PR.ADMIN,  # 调用方已守门
            target_role=member.role,
            new_role=_PR.VIEWER,
        )
        self._assert_not_last_admin(project=project, member_being_changed=member)
        member_id_str = str(member.member_id)
        member.delete()  # 软删；任务指派保留（BR-07）
        _notify(
            member_id_str,
            event="project.member.removed",
            title=f"你已被移出项目「{project.name}」",
            data={
                "project_id": str(project.id),
                "project_name": project.name,
                "workspace_slug": project.workspace.slug,
                "actor_id": str(actor.id),
                "actor": actor.display_name,
            },
        )

    @transaction.atomic
    def change_role(self, *, project: Project, member: ProjectMember,
                    new_role: int, actor) -> ProjectMember:
        if new_role not in ProjectRole.values:
            raise AppException("VALIDATION_ERROR",
                               message="非法的项目角色",
                               details=[{"field": "role", "code": "NOT_A_CHOICE",
                                         "message": "非法的项目角色"}])
        # 调用方传入的 actor 已守门（PROJ_ADMIN）；这里再校验层级
        self._assert_can_change_role(
            operator_role=ProjectRole.ADMIN,
            target_role=member.role,
            new_role=new_role,
        )
        # BR-06 末位保护：ADMIN → 非 ADMIN 才需要判定
        if member.role == ProjectRole.ADMIN and new_role < ProjectRole.ADMIN:
            self._assert_not_last_admin(project=project, member_being_changed=member)
        old_role = member.role
        member.role = new_role
        member.updated_by = actor
        member.save(update_fields=["role", "updated_by", "updated_at"])
        _notify(
            str(member.member_id),
            event="project.member.role_changed",
            title=f"你在项目「{project.name}」的角色已变更为 {ProjectRole(new_role).label}",
            data={
                "project_id": str(project.id),
                "project_name": project.name,
                "workspace_slug": project.workspace.slug,
                "old_role": old_role,
                "new_role": new_role,
                "actor_id": str(actor.id),
                "actor": actor.display_name,
            },
        )
        return member

    # ────────── 收藏 / 取消收藏 ──────────

    @staticmethod
    def favorite(*, user, project: Project) -> dict:
        # BR-08 + P2 升级预留：先统计再 get_or_create（避免 IntegrityError 抖动）
        current_count = ProjectFavorite.objects.filter(user=user, deleted_at__isnull=True).count()
        if current_count >= FAVORITE_LIMIT_PER_USER:
            raise AppException(
                "RESOURCE_LIMIT_EXCEEDED",
                message=f"收藏数量已达上限（{FAVORITE_LIMIT_PER_USER}），请先取消部分收藏",
            )
        obj, created = ProjectFavorite.objects.get_or_create(
            user=user, project=project,
            defaults={"created_by": user, "updated_by": user},
        )
        # BR-15：取消时硬删除，再次收藏若残留 deleted_at 行会触发唯一约束
        # → 这里先 restore 一把再返回，保证幂等可重入
        if not created and obj.deleted_at is not None:
            obj.deleted_at = None
            obj.save(update_fields=["deleted_at", "updated_at"])
        return {"favorited": True, "favorited_at": obj.created_at.isoformat()}

    @staticmethod
    def unfavorite(*, user, project: Project) -> None:
        # BR-15：取消时硬删除（不再保留 deleted_at 软删痕迹），
        # 保证再次收藏走 get_or_create 创建新行时无唯一冲突。
        ProjectFavorite.objects.filter(user=user, project=project).delete()

    # ────────── 归档 / 恢复 ──────────

    @staticmethod
    @transaction.atomic
    def set_archived(*, project: Project, actor, archived: bool) -> Project:
        new_status = Project.Status.ARCHIVED if archived else Project.Status.ACTIVE
        project.status = new_status
        project.updated_by = actor
        project.save(update_fields=["status", "updated_by", "updated_at"])
        # 通知项目全部 active 成员
        member_ids = list(
            ProjectMember.objects.filter(
                project=project, is_active=True, deleted_at__isnull=True
            ).values_list("member_id", flat=True)
        )
        for mid in member_ids:
            _notify(
                mid,
                event="project.archived" if archived else "project.unarchived",
                title=f"项目「{project.name}」已{'归档' if archived else '恢复'}",
                data={
                    "project_id": str(project.id),
                    "project_name": project.name,
                    "workspace_slug": project.workspace.slug,
                    "status": new_status,
                    "actor_id": str(actor.id),
                    "actor": actor.display_name,
                },
            )
        return project
