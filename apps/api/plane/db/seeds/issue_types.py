"""Workspace 级默认任务类型（P0 仅种子「任务」1 条）。"""

from plane.db.models.issue_type import IssueType


def seed_issue_types(workspace, actor_id) -> IssueType:
    obj, _ = IssueType.objects.get_or_create(
        workspace=workspace,
        name="任务",
        defaults={
            "icon": "circle-dot",
            "color": "#3F76FF",
            "is_default": True,
            "is_system": True,
            "sort_order": 1000,
            "created_by_id": actor_id,
        },
    )
    return obj
