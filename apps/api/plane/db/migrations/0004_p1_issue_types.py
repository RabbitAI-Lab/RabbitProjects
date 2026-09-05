"""P1 启用 IssueType 五内置全开 + 存量回填（TASK-002 §1.3 决策 3、TASK-002 §4.1.4）。

P0 只种了「任务」1 类；本迭代把另外 4 类（需求/缺陷/测试/文档）补齐并把存量
issue_type__isnull=True 的任务回填为「任务」。零 DDL（沿用 P0 已建的 IssueType 列）。

幂等：get_or_create 保证重复执行幂等；存量回填仅处理 NULL 字段（MIG-01）。
"""
from __future__ import annotations

from django.db import migrations


BUILTIN_ISSUE_TYPES = [
    {"name": "需求",   "icon": "sparkles",        "color": "#8B5CF6", "sort_order": 1000, "is_default": False},
    {"name": "缺陷",   "icon": "bug",             "color": "#EF4444", "sort_order": 2000, "is_default": False},
    {"name": "任务",   "icon": "circle-check",    "color": "#3B82F6", "sort_order": 3000, "is_default": True},
    {"name": "测试",   "icon": "flask-conical",   "color": "#10B981", "sort_order": 4000, "is_default": False},
    {"name": "文档",   "icon": "file-text",       "color": "#F59E0B", "sort_order": 5000, "is_default": False},
]


def enable(apps, schema_editor):
    IssueType = apps.get_model("db", "IssueType")
    Issue = apps.get_model("db", "Issue")
    Project = apps.get_model("db", "Project")
    Workspace = apps.get_model("db", "Workspace")

    # ① 种子：每个已存在 Workspace 补齐全部 5 类（get_or_create 幂等）
    for workspace in Workspace.objects.filter(deleted_at__isnull=True):
        for spec in BUILTIN_ISSUE_TYPES:
            IssueType.objects.get_or_create(
                workspace=workspace,
                name=spec["name"],
                defaults={
                    "icon": spec["icon"],
                    "color": spec["color"],
                    "sort_order": spec["sort_order"],
                    "is_default": spec["is_default"],
                    "is_system": True,
                    "is_active": True,
                },
            )

    # ② 存量回填：type 为空的任务 → 所属 Workspace「任务」类型
    task_types = dict(
        IssueType.objects.filter(
            is_system=True, name="任务", deleted_at__isnull=True
        ).values_list("workspace_id", "id")
    )
    qs = (
        Issue.objects.filter(issue_type__isnull=True, deleted_at__isnull=True)
        .only("id", "project_id")
        .iterator(chunk_size=500)
    )
    for issue in qs:
        type_id = task_types.get(issue.project_id)  # project_id == project.workspace_id 经 project 取 ws
        if type_id is None:
            # 兜底：直接经 Project 表拿 workspace
            project = Project.objects.filter(pk=issue.project_id).first()
            if project is None:
                continue
            type_id = task_types.get(project.workspace_id)
        if type_id:
            Issue.objects.filter(pk=issue.pk).update(issue_type_id=type_id)


def rollback(apps, schema_editor):
    """回滚不删数据（列本就可空），仅恢复 P0 门控：把 任务 以外的内置类型置 is_active=False。"""
    IssueType = apps.get_model("db", "IssueType")
    IssueType.objects.filter(is_system=True).exclude(name="任务").update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0003_sprint1"),
    ]
    operations = [
        migrations.RunPython(enable, rollback),
    ]
