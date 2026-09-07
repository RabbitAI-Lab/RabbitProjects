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
    "team.stats.read": WorkspaceRole.ADMIN,           # Sprint-5 TEAM-003（ADR-0025：rbac §8.1 Report 分区新行）

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
        # BOARD-004 §4.2 #1/#3：批级键（AUTH-005 矩阵增量，rbac §8.2 已注册）。
        # P2 以动作码逐条判定等效（rbac §5.3 对象级 + 各单条动作门槛）。
        "issue.bulk.update": ProjectRole.CONTRIBUTOR,
        "comment.create": ProjectRole.COMMENTER,
        "file.upload": ProjectRole.CONTRIBUTOR,
        # ── FILE-002 §2.4 BR-13（rbac §8.2 矩阵原码，不造新码）──
        # R1 对象级受限项（CONTRIBUTOR 仅本人上传）在视图层叠加，与门槛是
        # 「门槛 + 附加规则」关系（AUTH-005 §4.4 注）。
        "file.read": ProjectRole.VIEWER,              # 受可见性过滤（R6 / BR-08）
        "file.update": ProjectRole.CONTRIBUTOR,       # + R1：CONTRIBUTOR 仅本人上传
        "file.delete": ProjectRole.CONTRIBUTOR,       # + R1：CONTRIBUTOR 仅本人上传（回收站同键过滤）
        "file.permission.manage": ProjectRole.ADMIN,  # 可见性配置（目录与文件同码）
        "folder.manage": ProjectRole.CONTRIBUTOR,     # 目录新建/改名/移动/删除
        # ── FILE-003 §4.2.4（rbac §8.2 已登记条目：版本回溯）──
        "file.version.manage": ProjectRole.CONTRIBUTOR,  # 回滚（#8）
        # ── FILE-004 §2.3 BR-01（rbac §8.2 已登记行：分享生成）──
        # 默认 PROJ_ADMIN（rbac 表 CONTRIBUTOR 列为「⚠️ 可配置开关」——P3 起可
        # 下放；P2 按默认列实现）。创建另叠加 can_view_file（不能分享自己看不见的文件）。
        "file.share": ProjectRole.ADMIN,             # 创建/列表/延期/吊销
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
    "team.stats.read": "查看团队成员活跃度",
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
    "issue.bulk.update": "批量操作任务",
    "comment.create": "发表评论",
    "file.upload": "上传文件",
    "file.read": "查看文件",
    "file.update": "编辑文件",
    "file.delete": "删除文件",
    "file.permission.manage": "管理文件可见性",
    "folder.manage": "管理文件目录",
    "file.version.manage": "管理文件版本",
    "file.share": "分享文件",
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
