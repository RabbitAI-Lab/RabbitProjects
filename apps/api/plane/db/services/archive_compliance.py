"""归档强制合规服务（FILE-007，P4 R6）。

归档前置闸门：阻断性检查（未结任务/活跃 LegalHold——BR-01）与警告性
检查（无负责人/未来日期——BR-02 不阻断 force 可过）；归档成功生成组织
快照报告（BR-03 不可编辑）。挂接 TEAM-003 archive 端点前置（workspace_
governance.py 归档路径回改登记）。
"""

from __future__ import annotations

import logging

from django.utils import timezone

logger = logging.getLogger("plane.archive_compliance")

_BLOCKING_LIST_LIMIT = 20


def check_archive_compliance(workspace) -> dict:
    """归档前置检查（BR-01/02）：blocking / warnings / stats。"""
    from plane.db.models import Issue, LegalHold

    now = timezone.now()
    blocking: list[dict] = []
    warnings: list[dict] = []

    open_issues = Issue.objects.filter(
        project__workspace=workspace, deleted_at__isnull=True, completed_at__isnull=True
    )[:_BLOCKING_LIST_LIMIT]
    for issue in open_issues:
        blocking.append({"kind": "open_issue", "issue": str(issue.id), "name": issue.name})
    from plane.db.models import Issue as _I

    blocking_count = _I.objects.filter(
        project__workspace=workspace, deleted_at__isnull=True, completed_at__isnull=True
    ).count()
    for hold in LegalHold.objects.filter(asset__workspace=workspace, released_at__isnull=True)[:10]:
        blocking.append({"kind": "legal_hold", "hold": str(hold.id), "reason": hold.reason})

    unassigned = _I.objects.filter(
        project__workspace=workspace, deleted_at__isnull=True, issue_assignees__isnull=True, completed_at__isnull=True
    ).count()
    if unassigned:
        warnings.append({"kind": "unassigned", "count": unassigned})
    future = _I.objects.filter(
        project__workspace=workspace, deleted_at__isnull=True, target_date__gt=now.date()
    ).count()
    if future:
        warnings.append({"kind": "future_dated", "count": future})

    return {"blocking": blocking, "warnings": warnings, "stats": _workspace_stats(workspace)}


def _workspace_stats(workspace) -> dict:
    from plane.db.models import Issue, Project, WorkspaceMember

    total = Issue.objects.filter(project__workspace=workspace, deleted_at__isnull=True).count()
    done = Issue.objects.filter(
        project__workspace=workspace, deleted_at__isnull=True, completed_at__isnull=False
    ).count()
    return {
        "projects": Project.objects.filter(workspace=workspace, deleted_at__isnull=True).count(),
        "issues_total": total,
        "issues_done": done,
        "issues_open": total - done,
        "members": WorkspaceMember.objects.filter(workspace=workspace, is_active=True, deleted_at__isnull=True).count(),
    }


def build_archive_report(workspace) -> dict:
    """归档时刻快照（BR-03：报告不可编辑——落库后无更新路径）。"""
    return {
        "generated_at": timezone.now().isoformat(),
        "workspace": {"id": str(workspace.id), "name": workspace.name, "slug": workspace.slug},
        **_workspace_stats(workspace),
    }
