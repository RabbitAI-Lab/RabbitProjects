"""DRF Permission 类族 —— AUTH-005 三重防护的第二层（接口二次鉴权）。

硬性约定（AUTH-005 §4.3）：
1. Permission 类判定语义必须逐字对齐 §2.5 有效角色推导规则（R1~R4）。
   前端 PermissionStore.can() 与本模块同源；任何偏离由 §4.6 C1/C3 CI 检查守护。
2. WS_ADMIN+ 隐式 PROJ_ADMIN 提升（rbac-permission-model.md §7.4）由
   ``ProjectPermission.get_effective_project_role`` 一处实现，ProjectPermission
   子类一律走该路径，禁止各子类重复推导。
3. R1 对象级「仅本人」判定在 ``IssuePermission.has_object_permission`` 实现；
   R2 层级保护（不可管同级/上级）与 R3 末位 Owner 保护属业务层
   （TEAM-002 / PROJ-002 / AUTH-009），本模块只承担门槛判定。矩阵阈值与附加
   约束是「门槛 + 附加规则」关系，不是替代关系。
4. 403 信封收敛：Permission 类判定不通过统一抛 ``PermissionDeniedWithCode``，
   全局异常处理器（plane.base.handlers.envelope_exception_handler）按
   ``exc.error_code`` 收敛为 PERM_ROLE_INSUFFICIENT 错误信封。
5. 矩阵单一数据源在 ``plane.constants.permissions.PERMISSION_MATRIX``；本模块
   任何代码禁止再次声明权限点清单（rbac §1.2 「唯一手写处」原则）。
"""
from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import SAFE_METHODS, BasePermission

from plane.constants.permissions import (
    PERMISSION_MATRIX,
    threshold_of,
)
from plane.db.models import ProjectMember, SystemAdmin, WorkspaceMember
from plane.db.models.roles import ProjectRole, WorkspaceRole

if TYPE_CHECKING:
    pass


# ── 异常类型 ────────────────────────────────────────────────────────
class PermissionDeniedWithCode(PermissionDenied):
    """携带注册表错误码的 403。

    handlers.py 第 5 步按 ``exc.error_code`` 收敛到 PERM_ROLE_INSUFFICIENT；
    这里允许子类覆写以区分「角色不足」与更具体的拒绝码。
    """

    default_code = "PERM_ROLE_INSUFFICIENT"


# ── 角色解析共用辅助 ──────────────────────────────────────────────
def is_system_admin(user) -> bool:
    """SystemAdmin 表存在性查询 —— 第二层短路的唯一判定源（BR-09）。"""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return SystemAdmin.objects.filter(user=user, is_active=True).exists()


def _resolve_workspace_role(request, view) -> int | None:
    """单请求级缓存：同一 request 多次查同一 slug 只走一次 DB。

    缓存键挂到 request 对象（请求结束随 GC 消失），不同请求隔离。
    """
    slug = view.kwargs.get("slug") or view.kwargs.get("workspace_slug")
    if not slug:
        return None
    cache_key = f"_perm_ws_role_{slug}"
    if not hasattr(request, cache_key):
        role = (
            WorkspaceMember.objects
            .filter(workspace__slug=slug, member=request.user, is_active=True)
            .values_list("role", flat=True)
            .first()
        )
        setattr(request, cache_key, role)
    return getattr(request, cache_key)


def _resolve_project_role(request, view) -> int | None:
    """单请求级缓存：同一 request 多次查同一 project_id 只走一次 DB。"""
    project_id = view.kwargs.get("project_id") or view.kwargs.get("pk")
    if not project_id:
        return None
    cache_key = f"_perm_proj_role_{project_id}"
    if not hasattr(request, cache_key):
        role = (
            ProjectMember.objects
            .filter(project_id=project_id, member=request.user, is_active=True)
            .values_list("role", flat=True)
            .first()
        )
        setattr(request, cache_key, role)
    return getattr(request, cache_key)


def _resolve_effective_project_role(request, view) -> int | None:
    """★ 唯一隐式提升实现（rbac §7.4）—— SYSTEM_ADMIN / WS_ADMIN+ → PROJ_ADMIN。"""
    if is_system_admin(request.user):
        return ProjectRole.ADMIN
    ws_role = _resolve_workspace_role(request, view)
    if ws_role is not None and ws_role >= WorkspaceRole.ADMIN:
        return ProjectRole.ADMIN
    return _resolve_project_role(request, view)


# ── 工作空间级基类（AUTH-005 §4.3.1）──────────────────────────────
class WorkspacePermission(BasePermission):
    """工作空间级基类：read/write 分别声明最低角色（rbac §5.2）。"""

    message = "当前角色无权执行此操作"
    code = "PERM_ROLE_INSUFFICIENT"

    read_role: int = WorkspaceRole.GUEST
    write_role: int = WorkspaceRole.ADMIN

    def has_permission(self, request, view) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False                                  # → 401（认证层先行）
        if is_system_admin(request.user):
            return True                                   # BR-09 第二层短路
        role = _resolve_workspace_role(request, view)
        if role is None:
            return False                                  # → 404（_access 配合防 ID 枚举）
        required = self.read_role if request.method in SAFE_METHODS else self.write_role
        if role < required:
            raise PermissionDeniedWithCode(self.message)
        return True


class WorkspaceAdminPermission(WorkspacePermission):
    """工作空间管理员基类 —— read/write 均要求 ADMIN。"""

    read_role = write_role = WorkspaceRole.ADMIN


class WorkspaceOwnerPermission(WorkspacePermission):
    """工作空间所有者基类 —— workspace.transfer 的守护（AUTH-005 §2.4.1）。"""

    read_role = write_role = WorkspaceRole.OWNER


# ── 项目级基类（AUTH-005 §4.3.2）──────────────────────────────────
class ProjectPermission(WorkspacePermission):
    """项目级基类。判定顺序（短路，rbac §5.2）：

    1. SYSTEM_ADMIN → ADMIN；2. WS_OWNER/ADMIN → 隐式 PROJ_ADMIN（§7.4 绕过）；
    3. 显式 ProjectMember 且 role ≥ 所需；4. 拒绝。
    """

    read_role = ProjectRole.VIEWER
    write_role = ProjectRole.CONTRIBUTOR

    def has_permission(self, request, view) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False
        role = _resolve_effective_project_role(request, view)
        if role is None:
            return False
        required = self.read_role if request.method in SAFE_METHODS else self.write_role
        if role < required:
            raise PermissionDeniedWithCode(self.message)
        return True


class ProjectAdminPermission(ProjectPermission):
    """项目管理员基类 —— project.delete / project.update / project.member.manage。"""

    read_role = write_role = ProjectRole.ADMIN


# ── 任务级（AUTH-005 §4.3.3）──────────────────────────────────────
class IssuePermission(ProjectPermission):
    """任务级：COMMENTER 可读不可写；CONTRIBUTOR 的 destroy 仅限本人创建（rbac §5.3）。"""

    read_role = ProjectRole.VIEWER
    write_role = ProjectRole.CONTRIBUTOR

    def has_object_permission(self, request, view, obj) -> bool:
        role = _resolve_effective_project_role(request, view)
        if role is None:
            return False
        if request.method in SAFE_METHODS:
            return role >= ProjectRole.VIEWER
        if role >= ProjectRole.ADMIN:
            return True
        if view.action == "destroy":                       # BR-01 对象级「仅本人」
            return role >= ProjectRole.CONTRIBUTOR and obj.created_by_id == request.user.id
        return role >= ProjectRole.CONTRIBUTOR


class IssueCommentPermission(IssuePermission):
    """评论级：COMMENTER 可创建；编辑/删除仅本人。"""

    write_role = ProjectRole.COMMENTER

    def has_object_permission(self, request, view, obj) -> bool:
        role = _resolve_effective_project_role(request, view)
        if role is None:
            return False
        if request.method in SAFE_METHODS:
            return True
        if role >= ProjectRole.ADMIN:
            return True
        return obj.actor_id == request.user.id             # 编辑/删除仅本人


class FilePermission(ProjectPermission):
    """附件上传级 —— file.upload 需 CONTRIBUTOR（AUTH-005 §2.4.2）。"""

    write_role = ProjectRole.CONTRIBUTOR


# ── 装饰器（AUTH-005 §4.3.4）──────────────────────────────────────
def _resolve_role_for_scope(request, view, scope: str) -> int | None:
    """按 scope 解析当前用户的有效角色等级（给 require_permission 复用）。"""
    if scope == "workspace":
        if is_system_admin(request.user):
            return WorkspaceRole.OWNER
        return _resolve_workspace_role(request, view)
    if scope == "project":
        return _resolve_effective_project_role(request, view)
    raise KeyError(f"未知权限 scope {scope!r}（仅支持 workspace / project）")


def require_permission(permission_key: str, scope: str = "project"):
    """细粒度动作端点专用（invite / favorite / transfer 等）。

    矩阵取所需角色 → 重新推导实际角色 → 比较。KeyError = 未登记权限点，
    在测试期即暴露（BR-06）——这是「矩阵是唯一数据源」的运行时防线。
    """
    def decorator(func):
        @wraps(func)
        def wrapper(self, request, *args, **kwargs):
            # 未登记权限点 → KeyError 暴露（BR-06：测试期失败）
            required = PERMISSION_MATRIX[scope][permission_key]
            actual = _resolve_role_for_scope(request, self, scope)
            if actual is None or actual < required:
                raise PermissionDeniedWithCode(
                    f"缺少权限：{permission_key}（需要角色等级 ≥ {required}）"
                )
            return func(self, request, *args, **kwargs)
        return wrapper
    return decorator


# ── L0：账号已认证且未禁用 ──────────────────────────────────────
class IsAuthenticatedAndActive(BasePermission):
    """L0 基类 —— 已认证 + 账号未禁用。所有需要鉴权端点的最小门槛（AUTH-005 §4.3）。

    注：账号禁用场景（is_active=False）由 AUTH-001 路径在登录时阻断；本类只兜
    底已认证但停用的越权 token（极少）——通过 ``AUTH_ACCOUNT_DISABLED`` 信封返回。
    """

    message = "AUTH_ACCOUNT_DISABLED"

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not user or not getattr(user, "is_authenticated", False):
            return False  # → 由 _access 抛 404 或由认证层抛 401
        if not getattr(user, "is_active", True):
            raise PermissionDeniedWithCode("账号已被禁用，请联系管理员")
        return True


# ── 业务层 helper：层级保护 R2 + 末位保护 R3（rbac-permission-model.md §7）──
def assert_can_manage_member(
    operator_role: int, target_role: int, new_role: int | None = None
) -> None:
    """层级保护（rbac §7.1）。

    operator_role：操作者当前角色；target_role：被操作成员的角色；new_role（可选）：
    调整后的目标角色（PATCH role 时传入）。
    违反 → AppException("PERM_ROLE_INSUFFICIENT")，由 §5.5 错误码分工对齐为 403。
    """
    from plane.base.exception import AppException  # 避免循环引用

    if target_role >= operator_role:
        raise AppException(
            "PERM_ROLE_INSUFFICIENT",
            message="不能修改权限等级不低于自己的成员",
        )
    if new_role is not None and new_role >= operator_role:
        raise AppException(
            "PERM_ROLE_INSUFFICIENT",
            message="不能将成员提升到高于或等于自己的等级",
        )


def assert_not_last_owner(workspace_id, exclude_member_id=None) -> None:
    """末位 OWNER 保护（rbac §7.2）。

    离开 / 移除 / 角色降级 / 账号禁用四条路径必须调用。覆盖场景：被排除的成员
    仍为 OWNER 时也判定「还有其他 OWNER」，否则视为即将成为末位 → 拒。
    """
    from plane.base.exception import AppException

    qs = WorkspaceMember.objects.filter(
        workspace_id=workspace_id, role=WorkspaceRole.OWNER, is_active=True
    )
    if exclude_member_id is not None:
        qs = qs.exclude(member_id=exclude_member_id)
    if not qs.exists():
        raise AppException(
            "RESOURCE_STATE_INVALID",
            message="工作空间必须保留至少一名所有者，请先转让所有权",
        )


def require_role(request, view, role: int) -> int:
    """按最低角色等级校验（TEAM-002 §4.5 矩阵直接消费，无须走 PERMISSION_MATRIX）。

    返回当前用户的有效角色等级（供视图后续判定复用）。不通过抛 404（防 ID
    枚举：非成员视为不可见资源）或 403。
    """
    from plane.base.exception import AppException

    actual = _resolve_workspace_role(request, view)
    if actual is None:
        # 与 _access.get_workspace_or_404 同口径：未取到工作空间 / 非成员 → 404
        from rest_framework.exceptions import NotFound

        raise NotFound("RESOURCE_NOT_FOUND")
    if actual < role:
        raise AppException(
            "PERM_ROLE_INSUFFICIENT",
            message="当前角色权限不足",
        )
    return actual


# ── 兼容既有导入 ──────────────────────────────────────────────────
class IsAuthenticated(BasePermission):
    """会话已登录判定 —— 既有视图使用的最小权限类（auth/projects/issues/workspaces）。"""

    def has_permission(self, request, view) -> bool:
        return bool(request.user and request.user.is_authenticated)


class IsWorkspaceOwnerOrAdmin(BasePermission):
    """WORKSPACE OWNER/ADMIN 才能修改。WS_OWNER/ADMIN 隐式 = 全项目 ADMIN。

    保留以兼容既有 import 路径；新增视图应优先用 ``WorkspaceAdminPermission``
    / ``WorkspaceOwnerPermission``，避免在视图层手工取 current_user_role。
    """

    message = "PERM_WORKSPACE_ADMIN_REQUIRED"

    def has_object_permission(self, request, view, obj) -> bool:
        role = getattr(obj, "current_user_role", None)
        return bool(role and role >= WorkspaceRole.ADMIN)


# ── 工作空间成员管理域 Permission 类（TEAM-002 §4.5 矩阵）─────────
class WorkspaceMemberPermission(IsAuthenticatedAndActive):
    """工作空间成员管理域 Permission 类 —— 不同 action 派分到不同权限点。

    - list：``workspace.member.read``（WS_MEMBER+）
    - partial_update：``workspace.member.manage``（WS_ADMIN+，业务层叠加 R2）
    - destroy：``workspace.member.remove``（WS_ADMIN+，业务层叠加 R2）
    - 默认 action（leave / revoke / transfer）：由视图显式调用 ``require_role``
      或 ``require_permission`` 装饰器，不走 ACTION_PERMISSION_MAP。
    """

    # action → permission_key；空 = 交由视图 / 装饰器判定
    ACTION_PERMISSION_MAP: dict[str, str] = {}

    def has_permission(self, request, view) -> bool:
        if not super().has_permission(request, view):
            return False
        # leave / revoke / transfer 等不进入 ACTION_PERMISSION_MAP 的 action
        # 由视图在自身方法内显式 require_permission / require_role；
        # 默认放行（业务层兜底）。
        if view.action not in self.ACTION_PERMISSION_MAP:
            return True
        permission_key = self.ACTION_PERMISSION_MAP[view.action]
        # workspace.member.read 由 L0 + require_role 兜底；其余走矩阵
        if permission_key == "workspace.member.read":
            return require_role(request, view, WorkspaceRole.MEMBER) >= WorkspaceRole.MEMBER

        threshold = threshold_of(permission_key)
        actual = _resolve_workspace_role(request, view)
        if actual is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        if actual < threshold:
            from plane.base.exception import AppException

            raise AppException(
                "PERM_ROLE_INSUFFICIENT",
                message="当前角色权限不足",
            )
        return True
