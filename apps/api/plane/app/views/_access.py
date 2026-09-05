"""工作空间 / 项目访问控制共享辅助（AUTH-003 三防护的落点）。

sprint-0 把这两个 helper 分别放在 views/workspaces.py 与 views/projects.py，
其它 view 模块跨文件 import 私有函数。sprint-1 新增大量按域拆分的 view 模块
（成员 / 附件 / 评论 / 通知 / 统计…）全部依赖它们，故抽到独立模块作为唯一定义点，
原位置保留再导出以兼容既有 import 路径。

三防护口径（rbac-permission-model.md §5）：
  UI 隐藏 → API 层不可见即 404（防 ID 枚举，不用 403）→ DB 层 workspace 作用域过滤。
"""
from rest_framework.exceptions import NotFound

from plane.db.models import Project, ProjectMember, Workspace, WorkspaceMember
from plane.db.models.roles import ProjectRole, WorkspaceRole


def get_workspace_or_404(slug, user):
    """按 slug 取工作空间并校验成员身份；非成员一律 404（不泄露存在性）。

    返回 (workspace, workspace_member)。
    """
    try:
        ws = Workspace.objects.get(slug=slug, deleted_at__isnull=True)
    except Workspace.DoesNotExist:
        raise NotFound("RESOURCE_NOT_FOUND") from None
    member = WorkspaceMember.objects.filter(workspace=ws, member=user, is_active=True).first()
    if member is None:
        raise NotFound("RESOURCE_NOT_FOUND") from None
    return ws, member


def get_project_or_404(slug, project_id, user):
    """按 id 取项目并解析当前用户的项目角色；不可见一律 404。

    WS_OWNER/ADMIN 无 ProjectMember 行时隐式视为项目 ADMIN（INFRA-003 §4.5 决策）。
    结果角色挂在 `project.current_user_role`（内存属性），供 Permission 类与视图判定。

    返回 (project, project_member_or_None, workspace_member)。
    """
    ws, member = get_workspace_or_404(slug, user)
    try:
        project = Project.objects.get(id=project_id, workspace_id=ws.id, deleted_at__isnull=True)
    except Project.DoesNotExist:
        raise NotFound("RESOURCE_NOT_FOUND") from None
    pm = ProjectMember.objects.filter(project=project, member=user, is_active=True).first()
    if pm is None:
        if member.role < WorkspaceRole.ADMIN:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        proj_role = ProjectRole.ADMIN
    else:
        proj_role = pm.role
    project.current_user_role = proj_role
    return project, pm, member
