"""内置五视图种子（BOARD-003 §4.1.2，unified-issue-model.md §5.4 兑现）。

两个触发点（缺一不可）：
  1. 迁移 RunPython —— 覆盖**存量项目**补种；
  2. Project 创建服务 —— 部署后新建项目在创建事务内调用同一核心（与
     seed_project_states 同事务钩子，§7.2-5 验收对新建项目同样成立）。

「全部」为前端工具条固定首项（filters={} 裸态），**不种子入库**（§3.6）。
icon 只存 display_props.icon（IssueView 无 icon 模型字段，架构 §5.6），取值限
§3.4 八枚 emoji 预设——种子 defaults 仅传模型字段，杜绝迁移 FieldError。
占位符（@me / this_week / __requirement__ 类型名）由编译期解析：本迭代扁平编译
子集先行展开，TASK-011 全量编译器同族接管（§4.1.2 注）。
"""
from __future__ import annotations

# unified-issue-model.md §5.4 五视图清单（三处清单口径以此为准，BOARD-003 §4.1.2）
BUILTIN_VIEWS: list[dict] = [
    {
        "name": "需求池",
        "layout": "kanban",
        "filters": {
            "op": "AND",
            "conditions": [
                {"field": "issue_type", "operator": "in", "value": ["__requirement__"]}
            ],
        },
        "display_props": {"icon": "✨", "group_by": "state_id", "order_by": "sort_order"},
        "sort_order": 1000.0,
    },
    {
        "name": "缺陷列表",
        "layout": "list",
        "filters": {
            "op": "AND",
            "conditions": [{"field": "issue_type", "operator": "in", "value": ["__bug__"]}],
        },
        "display_props": {"icon": "🐛", "group_by": "priority", "order_by": "-priority"},
        "sort_order": 2000.0,
    },
    {
        "name": "我的待办",
        "layout": "list",
        "filters": {
            "op": "AND",
            "conditions": [
                {"field": "assignees", "operator": "in", "value": ["@me"]},
                {"field": "state.group", "operator": "in", "value": ["unstarted", "started"]},
            ],
        },
        # state.group 正向口径（unified-issue-model §5.4 / BOARD-003 §4.1.2，不用反向 not_in）
        "display_props": {"icon": "👤", "group_by": None, "order_by": "target_date"},
        "sort_order": 3000.0,
    },
    {
        "name": "本周到期",
        "layout": "list",
        "filters": {
            "op": "AND",
            "conditions": [
                {"field": "target_date", "operator": "between", "value": ["this_week"]}
            ],
        },
        "display_props": {"icon": "📅", "group_by": None, "order_by": "target_date"},
        "sort_order": 4000.0,
    },
    {
        "name": "测试执行",
        "layout": "list",
        "filters": {
            "op": "AND",
            "conditions": [{"field": "issue_type", "operator": "in", "value": ["__test__"]}],
        },
        "display_props": {"icon": "🧪", "group_by": None, "order_by": "-created_at"},
        "sort_order": 5000.0,
    },
]


def seed_project_views(project, *, issue_view_model=None) -> int:
    """单项目种子核心：迁移 RunPython 与 Project 创建服务共用（幂等 get_or_create）。

    issue_view_model 供迁移上下文注入历史模型类（RunPython 内不得 import 真实
    模型——schema 漂移防护）；运行时缺省用真实 IssueView。
    返回本次新建行数（幂等重跑为 0）。
    """
    if issue_view_model is None:
        from plane.db.models import IssueView as issue_view_model  # noqa: N813

    owner_id = project.created_by_id
    created = 0
    for spec in BUILTIN_VIEWS:
        _, was_created = issue_view_model.objects.get_or_create(
            project=project,
            name=spec["name"],
            is_system=True,
            defaults={
                "layout": spec["layout"],
                "filters": spec["filters"],
                "display_props": spec["display_props"],
                "sort_order": spec["sort_order"],
                "workspace_id": project.workspace_id,
                "owner_id": owner_id,
                "access": "personal",
            },
        )
        created += 1 if was_created else 0
    return created


def seed_builtin_views(apps, schema_editor) -> None:
    """迁移入口：对存量项目补种内置视图（单项目核心在 seed_project_views）。"""
    project_model = apps.get_model("db", "Project")
    issue_view_model = apps.get_model("db", "IssueView")
    for project in project_model.objects.filter(deleted_at__isnull=True):
        seed_project_views(project, issue_view_model=issue_view_model)
