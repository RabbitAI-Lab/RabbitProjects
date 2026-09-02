"""新建项目时种子四态（§4.13 + 概览 §2.1：建而不作为 P0 看板列）。"""

from plane.db.models.state import State


def seed_project_states(project) -> None:
    defaults = [
        {"name": "待办", "group": State.Group.UNSTARTED, "color": "#9CA3AF", "sort_order": 65535.0, "is_default": True},
        {
            "name": "进行中",
            "group": State.Group.STARTED,
            "color": "#3B82F6",
            "sort_order": 131070.0,
            "is_default": False,
        },
        {
            "name": "已完成",
            "group": State.Group.COMPLETED,
            "color": "#10B981",
            "sort_order": 196605.0,
            "is_default": False,
        },
        {
            "name": "已取消",
            "group": State.Group.CANCELLED,
            "color": "#6B7280",
            "sort_order": 262140.0,
            "is_default": False,
        },
    ]
    for d in defaults:
        State.objects.get_or_create(
            project=project,
            name=d["name"],
            issue_type=None,
            defaults={**d, "created_by_id": project.created_by_id},
        )
