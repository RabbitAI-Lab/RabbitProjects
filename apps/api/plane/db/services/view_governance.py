"""视图治理服务（BOARD-005 §4.3，Sprint-8 R5）。

共享/锁定/项目默认/副本/订阅的唯一业务收口。锁定态拦截优先于权限码
判定（BR-02）；解锁不级联取消项目默认（BR-15 两步显式流）；订阅计数
口径 = 存活 UserViewPreference(pinned=True) 行数（BR-16 唯一口径）。
"""
from __future__ import annotations

import logging
from copy import deepcopy

from django.db import transaction
from django.utils import timezone

from plane.base.exception import AppException
from plane.db.models import IssueView, ProjectMember, UserViewPreference

logger = logging.getLogger("plane.api.views")


def _audit(event: str, *, actor_id, object_id, workspace_id=None, **extra) -> None:
    from plane.audit.recorder import record_audit

    transaction.on_commit(
        lambda: record_audit.delay(event, actor_id=str(actor_id),
                                   object_id=str(object_id) if object_id else None,
                                   workspace_id=str(workspace_id) if workspace_id else None,
                                   **extra))


def _dedup_name(project, base: str) -> str:
    """BR-09：副本重名再加序号。"""
    name = base
    n = 2
    existing = set(IssueView.objects
                   .filter(project=project, deleted_at__isnull=True)
                   .values_list("name", flat=True))
    while name in existing:
        name = f"{base}（{n}）"
        n += 1
    return name


@transaction.atomic
def lock_view(*, actor, view: IssueView, is_locked: bool,
              is_project_default: bool | None) -> IssueView:
    # 权限由视图层守门（actor 的有效项目角色 = board.lock 门槛）；此处状态机
    if view.is_system:  # BR-14
        raise AppException("VALIDATION_ERROR", message="内置视图不可锁定",
                           details=[{"field": "view", "code": "INVALID",
                                     "message": "内置系统视图不可共享/锁定"}])
    if (is_locked is False and view.is_project_default
            and is_project_default is not False):  # BR-15
        raise AppException(
            "RESOURCE_STATE_INVALID", message="请先显式取消项目默认，再解锁",
            details=[{"field": "is_locked", "code": "INVALID",
                      "message": "请先显式取消项目默认，再解锁"}])
    if is_project_default and not is_locked:  # BR-05
        raise AppException(
            "VALIDATION_ERROR", message="项目默认视图须先锁定",
            details=[{"field": "is_project_default", "code": "INVALID",
                      "message": "项目默认视图须先锁定"}])
    view.is_locked = is_locked
    if is_locked:
        view.locked_by, view.locked_at = actor, timezone.now()
    else:
        view.locked_by, view.locked_at = None, None
    if is_project_default is True:
        (IssueView.objects.select_for_update()
         .filter(project=view.project, is_project_default=True,
                 deleted_at__isnull=True)
         .exclude(pk=view.pk).update(is_project_default=False))  # BR-04 原子替换
        view.is_project_default = True
    elif is_project_default is False:
        view.is_project_default = False
    view.updated_by = actor
    view.save()
    if view.is_project_default:
        _subscribe_all_members(view)  # 存量成员批量订阅（BR-16 +1）
    _audit("view.locked" if is_locked else "view.unlocked",
           actor_id=actor.id, object_id=view.id,
           workspace_id=view.workspace_id, view_name=view.name)
    return view


def _subscribe_all_members(view: IssueView) -> int:
    member_ids = ProjectMember.objects.filter(
        project=view.project, is_active=True, deleted_at__isnull=True,
    ).values_list("member_id", flat=True)
    rows = [UserViewPreference(user_id=uid, project=view.project,
                               view=view, pinned=True) for uid in member_ids]
    UserViewPreference.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


@transaction.atomic
def share_view(*, actor, view: IssueView, access: str) -> IssueView:
    """共享（personal→shared）/收回（shared→personal，级联软删全部订阅行）。"""
    if view.is_system:  # BR-14
        raise AppException("VALIDATION_ERROR", message="内置视图不可共享",
                           details=[{"field": "access", "code": "INVALID",
                                     "message": "内置系统视图不可共享"}])
    if access not in (IssueView.Access.PERSONAL, IssueView.Access.SHARED):
        raise AppException("VALIDATION_ERROR", message="非法的访问范围",
                           details=[{"field": "access", "code": "NOT_A_CHOICE",
                                     "message": "access ∈ personal|shared"}])
    if view.is_locked and access != IssueView.Access.SHARED:
        raise AppException(
            "RESOURCE_STATE_INVALID", message="锁定视图不可收回共享（先解锁）",
            details=[{"field": "access", "code": "INVALID",
                      "message": "请先解锁再收回共享"}])
    view.access = access
    view.updated_by = actor
    view.save(update_fields=["access", "updated_by", "updated_at"])
    if access == IssueView.Access.PERSONAL:
        # BR-16 −1②：收回共享级联软删全部订阅行（共享动作本身不改计数）
        UserViewPreference.objects.filter(
            view=view, deleted_at__isnull=True).delete()
    _audit("view.shared" if access == "shared" else "view.unshared",
           actor_id=actor.id, object_id=view.id,
           workspace_id=view.workspace_id, view_name=view.name)
    return view


@transaction.atomic
def duplicate_view(*, actor, view: IssueView) -> IssueView:
    """副本另存（BR-06）：恒 personal、owner=操作者；归档项目也允许（BR-12）。"""
    name = _dedup_name(view.project, f"{view.name}（副本）")
    fork = IssueView.objects.create(
        workspace=view.workspace, project=view.project,
        owner=actor, name=name,
        access=IssueView.Access.PERSONAL,
        layout=view.layout, filters=deepcopy(view.filters or {}),
        display_props=deepcopy(view.display_props or {}),
        created_by=actor, updated_by=actor,
    )
    _audit("view.duplicated", actor_id=actor.id, object_id=fork.id,
           workspace_id=view.workspace_id, source=str(view.id), view_name=name)
    return fork


def pin_view(*, actor, view: IssueView) -> None:
    """订阅（BR-16 +1①）：仅共享视图可订阅。"""
    if view.access != IssueView.Access.SHARED or view.is_system:
        raise AppException("VALIDATION_ERROR", message="仅共享视图支持订阅",
                           details=[{"field": "view", "code": "INVALID",
                                     "message": "仅共享视图支持订阅"}])
    # 复活或重建（存活行唯一约束 + 软删行复活）
    pref = UserViewPreference.objects.filter(
        user=actor, view=view).first()
    if pref is None:
        UserViewPreference.objects.create(user=actor, project=view.project,
                                          view=view, pinned=True,
                                          created_by=actor, updated_by=actor)
    else:
        pref.pinned = True
        pref.deleted_at = None
        pref.save(update_fields=["pinned", "deleted_at", "updated_at"])


def unpin_view(*, actor, view: IssueView) -> None:
    UserViewPreference.objects.filter(
        user=actor, view=view, deleted_at__isnull=True).delete()  # 软删


def subscribe_new_member(*, project, user_id) -> None:
    """新成员默认订阅钩子（§2.2，PROJ-002 add_members on_commit 调用）。"""
    default = IssueView.objects.filter(
        project=project, is_project_default=True, deleted_at__isnull=True).first()
    if default is not None:
        pin = UserViewPreference.objects.filter(user_id=user_id,
                                                view=default).first()
        if pin is None:
            UserViewPreference.objects.create(
                user_id=user_id, project=project, view=default, pinned=True)
        else:
            pin.pinned = True
            pin.deleted_at = None
            pin.save(update_fields=["pinned", "deleted_at", "updated_at"])


def subscriber_count(view: IssueView) -> int:
    """BR-16 唯一口径：存活 pinned 行数（owner 不隐含计入）。"""
    if hasattr(view, "_subscriber_count"):
        return view._subscriber_count
    return view.preferences.filter(pinned=True, deleted_at__isnull=True).count()
