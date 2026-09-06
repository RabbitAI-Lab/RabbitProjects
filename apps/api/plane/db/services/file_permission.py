"""可见性单入口（FILE-002 §4.3.1 / BR-08）。

``can_view_file`` 是三态可见性判定的**唯一实现点**——目录树（§4.2 #1）、
文件列表（§4.2 #5）、回收站（§4.2 #11）的查询过滤与预签名签发（download-url
#7 的对象级判定）全部复用本函数；权限 bug 最常见来源就是各处自行实现
（§6.3 设计决策 2，评审红线）。

预签名 URL 有效期仅 5 分钟，且**签发时**必须完成与列表同源的权限判定——
「先拿链接再被移出权限」的窗口被 5 分钟时效 + 签发时校验双重收窄（BR-09）。
"""
from __future__ import annotations

import uuid
from typing import Any

from plane.db.models import FileAsset, FileFolder, Project, ProjectMember, WorkspaceMember
from plane.db.models.roles import ProjectRole, WorkspaceRole

#: 可见性对象（FileFolder 或 FileAsset——两模型字段同构：visibility/allowed_members/project_id）
type VisibilityHost = FileFolder | FileAsset

_UNSET = object()


def effective_project_role(user, project_id: uuid.UUID | None) -> int | None:
    """user 在项目上的有效角色等级；非成员 / 无项目返回 None。

    与 ``plane.app.permissions._resolve_effective_project_role`` 同语义
    （rbac §7.4：SYSTEM_ADMIN / WS_ADMIN+ 隐式 PROJ_ADMIN），但本函数面向
    service 层（无 request/view），供目录树批量剪枝与 ``can_view_file`` 复用。
    """
    from plane.app.permissions import is_system_admin

    if user is None or not getattr(user, "is_authenticated", False):
        return None
    if project_id is None:
        return None
    if is_system_admin(user):
        return ProjectRole.ADMIN
    pm_role = (
        ProjectMember.objects
        .filter(project_id=project_id, member=user, is_active=True)
        .values_list("role", flat=True)
        .first()
    )
    if pm_role is not None:
        return pm_role
    ws_id = (
        Project.objects.filter(id=project_id)
        .values_list("workspace_id", flat=True)
        .first()
    )
    if ws_id is None:
        return None
    ws_role = (
        WorkspaceMember.objects
        .filter(workspace_id=ws_id, member=user, is_active=True)
        .values_list("role", flat=True)
        .first()
    )
    if ws_role is not None and ws_role >= WorkspaceRole.ADMIN:
        return ProjectRole.ADMIN
    return None


def can_view_file(user, asset_or_folder: VisibilityHost, *, role: Any = _UNSET) -> bool:
    """BR-08 单入口：三态可见性判定（§4.3.1 原文语义）。

    - ``all``     → 项目成员（VIEWER+）可见；
    - ``admins``  → PROJ_ADMIN / WS_ADMIN+（隐式）可见；
    - ``members`` → admins ∪ ``allowed_members`` 列表内用户。

    ``role`` 可传入预先求值的有效角色（目录树/列表批量剪枝时一次求值、
    逐对象复用，避免 N+1 查询）；缺省时内部求值——两条路径同源，判定逻辑
    仍只有本函数一处。
    """
    if role is _UNSET:
        role = effective_project_role(user, asset_or_folder.project_id)
    v = asset_or_folder.visibility
    if v == FileFolder.Visibility.ALL:
        return role is not None and role >= ProjectRole.VIEWER
    if v == FileFolder.Visibility.ADMINS:
        return role is not None and role >= ProjectRole.ADMIN
    # members 态：admin 直通；否则按 allowed_members 列表（§4.3.1 伪代码原文口径）
    if role is not None and role >= ProjectRole.ADMIN:
        return True
    allowed = asset_or_folder.allowed_members or []
    return str(user.id) in {str(m) for m in allowed}


def assert_can_view(user, asset_or_folder: VisibilityHost, *, role: Any = _UNSET) -> None:
    """对象级判定失败 → 404 存在性隐藏（§2.5「可见性不足」行）。"""
    from rest_framework.exceptions import NotFound

    if not can_view_file(user, asset_or_folder, role=role):
        raise NotFound("RESOURCE_NOT_FOUND")
