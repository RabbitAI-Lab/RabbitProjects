"""自定义角色可勾选权限码目录（AUTH-008，Sprint-8 R2）。

冻结基线 42 码 = rbac-permission-model.md §8.2 项目级注册表的「可自定义」
子集：排除 §8.1 WS 层、§8.3 系统层、各 ``*.manage`` 管理码与集成配置
（integration.config，配置管理动作）、P4 未注册码（worklog.approve /
gantt.baseline.manage）。CI 断言 len == 42（test_custom_roles）。

``CATALOG_THRESHOLDS``：目录码 → 最低 PROJ 角色（按 rbac §8.2 列位映射：
四列任一 ✅ 取最左）。仅用于 BR-16 访客天花板判定与目录端点展示——
**不是 PERMISSION_MATRIX 的新增注册**（判定热路径仍以
plane/constants/permissions.py 为唯一数据源，未接线码在
effective_codes 中只是并集成员，无独立挂点）。
"""
from __future__ import annotations

from plane.db.models.roles import ProjectRole

#: 可勾选目录（冻结基线 42 码，AUTH-008 §1.2/BR-02）
CUSTOMIZABLE_CATALOG: frozenset[str] = frozenset({
    # Project
    "project.read", "project.update", "project.delete", "project.archive",
    "project.favorite", "project.member.read",
    # Issue
    "issue.create", "issue.read", "issue.update", "issue.delete",
    "issue.delete.own", "issue.assign", "issue.state.transition",
    "issue.archive", "issue.bulk.update", "issue.field.write",
    # Worklog
    "worklog.create", "worklog.read",
    # Comment
    "comment.create", "comment.read", "comment.update.own",
    "comment.delete", "comment.delete.own",
    # File / Attachment
    "file.read", "file.upload", "file.update", "file.delete", "file.share",
    # Board / View
    "board.read", "board.update", "board.lock",
    "view.create.own", "view.create.shared",
    # Gantt
    "gantt.read", "gantt.update",
    # Approval
    "approval.act", "approval.withdraw", "approval.read",
    # Notification / Integration / Report
    "notification.read", "integration.link", "report.read", "report.export",
})

#: 目录码 → 最低 PROJ 角色等级（rbac §8.2 列位；BR-16 天花板判定数据源）
CATALOG_THRESHOLDS: dict[str, int] = {
    # VIEWER(5) 列即可
    "project.read": ProjectRole.VIEWER, "project.favorite": ProjectRole.VIEWER,
    "project.member.read": ProjectRole.VIEWER, "issue.read": ProjectRole.VIEWER,
    "comment.read": ProjectRole.VIEWER, "file.read": ProjectRole.VIEWER,
    "board.read": ProjectRole.VIEWER, "view.create.own": ProjectRole.VIEWER,
    "gantt.read": ProjectRole.VIEWER, "approval.read": ProjectRole.VIEWER,
    "notification.read": ProjectRole.VIEWER, "report.read": ProjectRole.VIEWER,
    # COMMENTER(10) 列起
    "comment.create": ProjectRole.COMMENTER,
    "comment.update.own": ProjectRole.COMMENTER,
    "comment.delete.own": ProjectRole.COMMENTER,
    # CONTRIBUTOR(15) 列起
    "project.update": ProjectRole.CONTRIBUTOR, "issue.create": ProjectRole.CONTRIBUTOR,
    "issue.update": ProjectRole.CONTRIBUTOR, "issue.delete.own": ProjectRole.CONTRIBUTOR,
    "issue.assign": ProjectRole.CONTRIBUTOR, "issue.state.transition": ProjectRole.CONTRIBUTOR,
    "issue.archive": ProjectRole.CONTRIBUTOR, "issue.bulk.update": ProjectRole.CONTRIBUTOR,
    "issue.field.write": ProjectRole.CONTRIBUTOR, "worklog.create": ProjectRole.CONTRIBUTOR,
    "file.upload": ProjectRole.CONTRIBUTOR, "file.update": ProjectRole.CONTRIBUTOR,
    "file.delete": ProjectRole.CONTRIBUTOR, "board.update": ProjectRole.CONTRIBUTOR,
    "view.create.shared": ProjectRole.CONTRIBUTOR, "gantt.update": ProjectRole.CONTRIBUTOR,
    "approval.withdraw": ProjectRole.CONTRIBUTOR, "integration.link": ProjectRole.CONTRIBUTOR,
    "report.export": ProjectRole.CONTRIBUTOR,
    # ADMIN(20) 列起（PROJ_ADMIN 专属业务码，仍可被自定义给低角色——并集只加）
    "project.delete": ProjectRole.ADMIN, "project.archive": ProjectRole.ADMIN,
    "issue.delete": ProjectRole.ADMIN, "worklog.read": ProjectRole.ADMIN,
    "comment.delete": ProjectRole.ADMIN, "file.share": ProjectRole.ADMIN,
    "board.lock": ProjectRole.ADMIN, "approval.act": ProjectRole.ADMIN,
}

#: 目录按域分组（permissions-catalog 端点展示序，AUTH-008 §4.2）
CATALOG_GROUPS: list[tuple[str, list[str]]] = [
    ("project", ["project.read", "project.update", "project.delete",
                 "project.archive", "project.favorite", "project.member.read"]),
    ("issue", ["issue.read", "issue.create", "issue.update", "issue.delete",
               "issue.delete.own", "issue.assign", "issue.state.transition",
               "issue.archive", "issue.bulk.update", "issue.field.write"]),
    ("worklog", ["worklog.create", "worklog.read"]),
    ("comment", ["comment.read", "comment.create", "comment.update.own",
                 "comment.delete", "comment.delete.own"]),
    ("file", ["file.read", "file.upload", "file.update", "file.delete",
              "file.share"]),
    ("board", ["board.read", "board.update", "board.lock"]),
    ("view", ["view.create.own", "view.create.shared"]),
    ("gantt", ["gantt.read", "gantt.update"]),
    ("approval", ["approval.read", "approval.act", "approval.withdraw"]),
    ("misc", ["notification.read", "integration.link", "report.read",
              "report.export"]),
]

#: BR-16 访客天花板（rbac §7.3 同阈值）：GUEST 可挂角色的码集上限
GUEST_CEILING = ProjectRole.COMMENTER


def validate_codes(codes: list[str]) -> list[str]:
    """校验并归一化权限码（BR-02）：目录外码逐码报 NOT_A_CHOICE。"""
    from plane.base.exception import AppException

    bad = sorted({c for c in codes if c not in CUSTOMIZABLE_CATALOG})
    if bad:
        raise AppException(
            "VALIDATION_ERROR",
            message="包含不开放自定义的权限码",
            details=[{"field": f"permissions.{i}", "code": "NOT_A_CHOICE",
                      "message": f"{c} 不在可勾选目录（冻结基线 42 码）"}
                     for i, c in enumerate(codes) if c in bad],
        )
    if len(codes) > 60:  # BR-04 扩容预留上限
        raise AppException(
            "RESOURCE_LIMIT_EXCEEDED",
            message="单角色权限码最多 60 个",
            details=[{"field": "permissions", "code": "TOO_LARGE",
                      "message": "单角色权限码最多 60 个"}],
        )
    return sorted(set(codes))


def guest_ceiling_violations(permissions: list[str]) -> list[str]:
    """BR-16：目录码超出访客天花板（未注册 / 阈值 > COMMENTER）的码清单。"""
    return sorted(c for c in permissions
                  if CATALOG_THRESHOLDS.get(c, ProjectRole.ADMIN) > GUEST_CEILING)
