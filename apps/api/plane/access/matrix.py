"""行级可见性矩阵（AUTH-006 §2.1/§4.2）——资源族 → Q 表达式的唯一实现地。

矩阵变更纪律（BR-09）：文档表（AUTH-006 §2.1）/ 本文件 / 越权测试三处必须同步；
CI 守护见 scripts/lint_access.py（AC-01~05）。

**当前态声明**（AUTH-006 §4.2 目标态说明）：
  - 公开项目对 WS_ONLY 可见通道**未启用**——`Project.visibility` 列已上线
    （0014 迁移，默认 private），但 rbac §6.2 `_scoped_for` 公开分支的架构回改
    未完成前，public 项目行为 = private（不可见）。见 §2.1 注 ① / §4.1 注 B。
  - 覆盖分支（当前态）：WS_ADMIN+ 隐式全权（rbac §7.4）∪ 显式 ProjectMember；
    draft 项目仅创建者 ∪ WS_ADMIN+ 可见（BR-11，PROJ-003 矩阵行收编）。
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from django.db.models import Q

logger = logging.getLogger("plane.access.matrix")

#: unsafe_all 例外登记表（BR-08）：文件路径 → 允许的 reason 集合。
#: 系统任务 / 管理后台等确需绕过可见性的调用点在此登记，CI 守护核对。
UNSAFE_EXCEPTIONS: dict[str, set[str]] = {}

#: 运行时计数（进程内）：unsafe_all 调用告警面（BR-08 计数告警）
_unsafe_counters: dict[str, int] = {}


def workspace_role(user, workspace_id) -> int | None:
    """当前用户的工作空间角色（无成员行 → None）。"""
    from plane.db.models import WorkspaceMember

    row = WorkspaceMember.objects.filter(
        workspace_id=workspace_id, member=user, is_active=True, deleted_at__isnull=True,
    ).only("role").first()
    return row.role if row else None


def is_ws_admin_plus(user, workspace_id) -> bool:
    from plane.db.models.roles import WorkspaceRole

    role = workspace_role(user, workspace_id)
    return role is not None and role >= WorkspaceRole.ADMIN


def workspace_q(user, workspace_id: uuid.UUID | None = None) -> Q:
    """工作空间族（§2.1「工作空间」行）：仅我所在——成员行活跃。

    本族可见性与 workspace_id 无关（用户维度），accessible_by 对该族免传。
    """
    return Q(
        workspace_member__member=user,
        workspace_member__is_active=True,
        workspace_member__deleted_at__isnull=True,
        deleted_at__isnull=True,
    )


def project_q(user, workspace_id: uuid.UUID) -> Q:
    """项目族可见性（rbac §6.2 当前态 + BR-11 draft 行）。

    WS_ADMIN+ → 全空间（含 draft）；其余 → 显式成员 ∩ 非 draft（创建者除外）。
    """
    from plane.db.models import Project

    member_cond = Q(
        project_projectmember__member=user,
        project_projectmember__is_active=True,
        project_projectmember__deleted_at__isnull=True,
    )
    base = Q(workspace_id=workspace_id, deleted_at__isnull=True)
    if is_ws_admin_plus(user, workspace_id):
        return base
    # draft 仅创建者（BR-11）；公开通道待架构回改解锁（当前态 = 视同私有）
    return base & member_cond & (~Q(status=Project.Status.DRAFT) | Q(created_by=user))


def visible_project_ids(user, workspace_id: uuid.UUID):
    from plane.db.models import Project

    return Project.objects.filter(project_q(user, workspace_id)).values("id")


def issue_q(user, workspace_id: uuid.UUID) -> Q:
    """任务族：随项目可见性（§2.1「任务 / 子任务」行）。"""
    return Q(project_id__in=visible_project_ids(user, workspace_id))


def issue_comment_q(user, workspace_id: uuid.UUID) -> Q:
    """评论族：随项目。"""
    return Q(issue__project_id__in=visible_project_ids(user, workspace_id))


def file_asset_q(user, workspace_id: uuid.UUID) -> Q:
    """文件族：随项目（分享链接匿名通道独立鉴权，BR-12 不得经此）。"""
    return Q(project_id__in=visible_project_ids(user, workspace_id))


#: 资源族注册表（AUTH-006 §2.1 矩阵行的代码单源；AC-05 与文档锚点核对）。
#: key = 资源族名（model 的 db_table 或族名）；value = Q 构造器 (user, workspace_id)。
MATRIX: dict[str, Callable[[Any, uuid.UUID], Q]] = {
    # §2.1「工作空间」行（成员所见；免 workspace_id）
    "workspaces": workspace_q,
    # §2.1「项目（私有/draft）」行（公开行待架构回改解锁）
    "projects": project_q,
    # §2.1「任务 / 子任务」行
    "issues": issue_q,
    # §2.1「评论 / 动态」行
    "issue_comments": issue_comment_q,
    # §2.1「文件 / 目录」行
    "file_assets": file_asset_q,
}


def matrix_family_for(model) -> str:
    """model → 资源族名（MATRIX 键）。未注册的模型调用 accessible_by 即报错——
    强制新资源族显式进矩阵（BR-09 三处同步）。"""
    family = model._meta.db_table  # noqa: SLF001 —— 注册表以表名为族键
    if family not in MATRIX:
        raise LookupError(
            f"模型 {model.__name__}（表 {family}）未注册进 plane.access.matrix——"
            "新资源族必须先在 MATRIX 登记并同步 AUTH-006 §2.1 与越权测试（BR-09）"
        )
    return family
