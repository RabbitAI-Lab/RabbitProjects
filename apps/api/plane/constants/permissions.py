"""权限矩阵 —— 全仓库唯一手写权限点清单（AUTH-005 §4.4）。

硬性约定：
1. 本文件是权限点的**单一数据源**。前端 `PermissionGate` 消费的权限码常量、
   `/users/me/permissions/` 下发的键集合、DRF Permission 类的门槛判定，
   三者全部由此派生；新增权限点必须先在 `rbac-permission-model.md` §4/§8
   注册表登记，再在此实现（AUTH-005 §4.6 四道 CI 检查守护）。
2. 矩阵阈值是**最低门槛**（角色等级 >= 阈值即通过）。R1 对象级归属、
   R2 同级层级保护、R3 末位保护等附加约束在 Permission 类与业务层叠加，
   与门槛是「门槛 + 附加规则」关系，不是替代关系（AUTH-005 §4.4 注）。
3. 角色等级取自 `plane.db.models.roles`：数值越大权限越高。
"""
from plane.db.models.roles import ProjectRole, WorkspaceRole

#: 域 → {权限点: 最低角色等级}
PERMISSION_MATRIX: dict[str, dict[str, int]] = {
    "workspace": {  # AUTH-005 §2.4.1（P1 子集）
        "workspace.read": WorkspaceRole.GUEST,
        "workspace.update": WorkspaceRole.ADMIN,
        "workspace.setting.manage": WorkspaceRole.ADMIN,
        "workspace.member.read": WorkspaceRole.MEMBER,
        "workspace.member.invite": WorkspaceRole.ADMIN,
        "workspace.member.manage": WorkspaceRole.ADMIN,   # + R2 层级保护（业务层）
        "workspace.member.remove": WorkspaceRole.ADMIN,   # + R2
        "workspace.member.leave": WorkspaceRole.MEMBER,   # + R3 末位保护（业务层）
        "workspace.transfer": WorkspaceRole.OWNER,
        "project.create": WorkspaceRole.MEMBER,           # R5 可配置（默认开）
    },
    "project": {  # AUTH-005 §2.4.2（P1 子集）
        "project.read": ProjectRole.VIEWER,
        "project.update": ProjectRole.ADMIN,
        "project.delete": ProjectRole.ADMIN,
        "project.member.read": ProjectRole.VIEWER,        # PROJ-002 §4.2.3 成员列表
        "project.member.manage": ProjectRole.ADMIN,       # + R2
        "project.favorite": ProjectRole.VIEWER,           # PROJ-002 §4.2.6 个人态收藏
        "project.archive": ProjectRole.ADMIN,             # PROJ-002 §4.2.7 active↔archived
        "project.label.manage": ProjectRole.ADMIN,
        "issue.create": ProjectRole.CONTRIBUTOR,
        "issue.update": ProjectRole.CONTRIBUTOR,
        "issue.state.transition": ProjectRole.CONTRIBUTOR,
        "issue.delete": ProjectRole.ADMIN,
        "issue.delete.own": ProjectRole.CONTRIBUTOR,      # + R1 对象级
        "comment.create": ProjectRole.COMMENTER,
        "file.upload": ProjectRole.CONTRIBUTOR,
    },
}

#: 权限点中文名 —— 403 页按 URL 参数渲染中文名，禁止裸露英文 key（AUTH-005 §3.3）。
#: 必须覆盖 PERMISSION_MATRIX 全部键（UT 断言集合相等）。
PERMISSION_LABELS: dict[str, str] = {
    # workspace 域
    "workspace.read": "查看团队",
    "workspace.update": "编辑团队信息",
    "workspace.setting.manage": "管理团队设置",
    "workspace.member.read": "查看成员列表",
    "workspace.member.invite": "邀请成员",
    "workspace.member.manage": "管理成员角色",
    "workspace.member.remove": "移除成员",
    "workspace.member.leave": "退出团队",
    "workspace.transfer": "转让所有权",
    "project.create": "创建项目",
    # project 域
    "project.read": "查看项目",
    "project.update": "编辑项目设置",
    "project.delete": "删除项目",
    "project.member.read": "查看项目成员",
    "project.member.manage": "管理项目成员",
    "project.favorite": "收藏项目",
    "project.archive": "归档或恢复项目",
    "project.label.manage": "管理项目标签",
    "issue.create": "创建任务",
    "issue.update": "编辑任务",
    "issue.state.transition": "流转任务状态",
    "issue.delete": "删除任务",
    "issue.delete.own": "删除自己创建的任务",
    "comment.create": "发表评论",
    "file.upload": "上传文件",
}


def all_permission_keys() -> set[str]:
    """全部权限点键集合 —— CI 四道一致性检查与 UT 的数据源。"""
    return {key for scope in PERMISSION_MATRIX.values() for key in scope}


def threshold_of(key: str) -> int:
    """取权限点的最低角色等级；未注册键直接 KeyError（测试期即暴露）。"""
    for scope in PERMISSION_MATRIX.values():
        if key in scope:
            return scope[key]
    raise KeyError(
        f"未注册的权限点 {key!r}：请先在 rbac-permission-model.md §4/§8 登记，"
        f"再在 plane/constants/permissions.py 实现（AUTH-005 §4.6）"
    )
