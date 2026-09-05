"""Workspace 级默认任务类型（TASK-002 §1.3 P1 起 5 内置全开）。

P0 仅种子「任务」1 条；本迭代补齐其余 4 类（需求/缺陷/测试/文档），与
0004_p1_issue_types 迁移对新老 Workspace 同口径。
"""

BUILTIN_ISSUE_TYPES = [
    {"name": "需求", "icon": "sparkles",      "color": "#8B5CF6", "sort_order": 1000, "is_default": False},
    {"name": "缺陷", "icon": "bug",           "color": "#EF4444", "sort_order": 2000, "is_default": False},
    {"name": "任务", "icon": "circle-check",  "color": "#3B82F6", "sort_order": 3000, "is_default": True},
    {"name": "测试", "icon": "flask-conical", "color": "#10B981", "sort_order": 4000, "is_default": False},
    {"name": "文档", "icon": "file-text",     "color": "#F59E0B", "sort_order": 5000, "is_default": False},
]


def seed_issue_types(workspace, actor_id):
    """为新 Workspace 一次性种入 5 内置类型（idempotent）。"""
    from plane.db.models.issue_type import IssueType

    created = []
    for spec in BUILTIN_ISSUE_TYPES:
        obj, _ = IssueType.objects.get_or_create(
            workspace=workspace,
            name=spec["name"],
            defaults={
                "icon": spec["icon"],
                "color": spec["color"],
                "is_default": spec["is_default"],
                "is_system": True,
                "is_active": True,
                "sort_order": spec["sort_order"],
                "created_by_id": actor_id,
            },
        )
        created.append(obj)
    return created
