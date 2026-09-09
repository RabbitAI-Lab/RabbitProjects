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
        # ── Sprint-5 INTG-001（rbac §8.1 已登记：WS 级安装/回调）──
        "integration.manage": WorkspaceRole.ADMIN,
        # ── Sprint-8 AUTH-007（rbac §8.1 Department（P3）行落地，非新增码）──
        "department.manage": WorkspaceRole.ADMIN,
        # ── Sprint-8 AUTH-008（rbac §8.1 已注册 WS 级，BR-08）──
        "role.manage": WorkspaceRole.ADMIN,
        # ── Sprint-8 AUTH-009（附录 B 唯一新增码：WS_OWNER 级）──
        "workspace.sso.manage": WorkspaceRole.OWNER,
        # ── Sprint-8 AUTH-010（rbac §8.1 Audit 行落地，BR-04）──
        "audit.read": WorkspaceRole.ADMIN,
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
        # ── Sprint-5 INTG-001/002（rbac §8.2：绑定/配置/同步日志 = PROJ_ADMIN）──
        "integration.config": ProjectRole.ADMIN,
        "integration.link": ProjectRole.CONTRIBUTOR,
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
        # ── Sprint-7 M11-WF（rbac §8.2 注册表；概览 §5 权限行）──
        # workflow.manage：WF-001/002/004/005 项目侧配置（PROJ_ADMIN+）；
        # automation.manage：WF-003 规则引擎（rbac §8.2 注册表专行，R3 轮消费）；
        # approval.act / approval.withdraw：WF-002 审批动作（真正判定在业务层——
        # 当前级指定审批人 / 仅发起人，矩阵只承担成员门槛，§8.4 R8 口径）。
        "workflow.manage": ProjectRole.ADMIN,
        "automation.manage": ProjectRole.ADMIN,
        "approval.act": ProjectRole.COMMENTER,
        "approval.withdraw": ProjectRole.CONTRIBUTOR,
        # ── Sprint-8 BOARD-005（rbac §8.2 既有注册码落地）──
        "board.read": ProjectRole.VIEWER,
        "view.create.own": ProjectRole.VIEWER,
        "view.create.shared": ProjectRole.CONTRIBUTOR,
        "view.manage": ProjectRole.ADMIN,
        "board.lock": ProjectRole.ADMIN,
        # ── Sprint-9 RPT-003（rbac §8.2 已登记行落地，零新增码）──
        "cycle.manage": ProjectRole.ADMIN,        # ⚠️ 列=可配置开关，按默认列（同 file.share 模式）
        "report.read": ProjectRole.VIEWER,
        "report.export": ProjectRole.ADMIN,       # ⚠️ 列=可配置开关，按默认列
        "project.setting.manage": ProjectRole.ADMIN,
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
    "integration.manage": "管理集成安装",
    "department.manage": "管理部门与组织架构",
    "role.manage": "管理自定义角色组",
    "workspace.sso.manage": "管理 SSO 单点登录",
    "audit.read": "查看审计日志",
    "integration.config": "配置项目集成",
    "integration.link": "关联外部对象",
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
    # Sprint-7 M11-WF
    "workflow.manage": "管理工作流",
    "automation.manage": "管理自动化规则",
    "approval.act": "审批操作",
    "approval.withdraw": "撤回审批",
    "board.read": "查看看板",
    "view.create.own": "创建个人视图",
    "view.create.shared": "创建共享视图",
    "view.manage": "管理他人共享视图",
    "board.lock": "锁定组织标准视图",
    # Sprint-9 RPT-003
    "cycle.manage": "管理迭代",
    "report.read": "查看报表",
    "report.export": "导出报表",
    "project.setting.manage": "管理项目设置",
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
