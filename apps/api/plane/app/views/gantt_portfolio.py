"""跨项目甘特三件套（GANTT-004/005/006，P4 R15）。

项目集甘特（项目行区间聚合 + 里程碑）、资源负载（成员×周负载率分档）、
关键路径锁定（CPM 快照不漂移）。全部纯读聚合 + 一张锁定表（无 DDL——
锁定快照落 Redis 键 + AuditLog 审计；如需持久表随演进登记）。
"""

from __future__ import annotations

import hashlib
import logging
import time as _time

from django.core.cache import cache
from django.db.models import Count, Max, Min, Q
from django.utils import timezone
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticatedAndActive, require_role
from plane.app.views._access import get_project_or_404, get_workspace_or_404
from plane.audit.recorder import record
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.db.models import (
    Issue,
    IssueCPMCache,
    Portfolio,
    PortfolioMilestone,
    ProjectWorklogConfig,
    WorkLogSummary,
    WorkspaceMember,
    WorkspaceRole,
)

logger = logging.getLogger("plane.api.gantt")

#: BR-01 负载容量缺省（5d × 8h）
DEFAULT_WEEKLY_CAPACITY = 2400
#: BR-02 三档分界
_BANDS = ((0.70, "green"), (1.00, "amber"), (float("inf"), "red"))

#: GANTT-006 锁定快照缓存键
_CP_LOCK_KEY = "cplock:{project_id}"


def _band(ratio: float) -> str:
    for cap, name in _BANDS:
        if ratio <= cap:
            return name
    return "red"


class PortfolioGanttView(APIView):
    """GET .../portfolios/{id}/gantt/ —— 项目集甘特（BR-01/02）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, slug, portfolio_id):
        ws = get_workspace_or_404(slug, request.user)[0]
        portfolio = Portfolio.objects.filter(pk=portfolio_id, workspace=ws).first()
        if portfolio is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        projects = []
        for link in portfolio.mounted_projects.select_related("project").filter(deleted_at__isnull=True):
            proj = link.project
            issues = Issue.objects.filter(project=proj, deleted_at__isnull=True)
            row = issues.aggregate(
                start=Min("start_date"),
                target=Max("target_date"),
                open_count=Count("id", filter=Q(completed_at__isnull=True)),
            )
            projects.append(
                {
                    "project_id": str(proj.id),
                    "name": proj.name,
                    "range": {"start": row["start"], "target": row["target"]},
                    "open_count": row["open_count"],
                }
            )
        milestones = [
            {
                "id": str(m.id),
                "name": m.name,
                "target_date": m.target_date,
                "completed": m.completed_at is not None,
                "late": any(p["range"]["target"] and m.target_date > p["range"]["target"] for p in projects),
            }
            for m in PortfolioMilestone.objects.filter(portfolio=portfolio, deleted_at__isnull=True)
        ]
        return success_response({"projects": projects, "milestones": milestones})


class ResourceLoadView(APIView):
    """GET .../resource-load/?weeks=8 —— 成员×周负载（BR-01/02）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, slug):
        ws = get_workspace_or_404(slug, request.user)[0]
        try:
            weeks = min(int(request.query_params.get("weeks", 8)), 26)
        except ValueError:
            weeks = 8
        import datetime as _dt

        today = timezone.now().date()
        week_start = today - _dt.timedelta(days=today.weekday())
        starts = [week_start - _dt.timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
        # 周容量：项目级配置缺省 2400（BR-01）
        capacities = dict(
            ProjectWorklogConfig.objects.filter(project__workspace=ws).values_list(
                "project_id", "weekly_capacity_minutes"
            )
        )
        rows_map: dict[str, dict] = {}
        for s in WorkLogSummary.objects.filter(project__workspace=ws, week_start__in=starts):
            member = str(s.actor_id)
            row = rows_map.setdefault(member, {"member_id": member, "name": "", "weeks": {str(d): 0 for d in starts}})
            row["weeks"][str(s.week_start)] += s.total_minutes or 0
        names = dict(
            WorkspaceMember.objects.filter(workspace=ws, is_active=True, deleted_at__isnull=True).values_list(
                "member_id", "member__display_name"
            )
        )
        rows = []
        for member, row in rows_map.items():
            cap = capacities.get("default") or DEFAULT_WEEKLY_CAPACITY
            weeks_out = []
            for d in starts:
                minutes = row["weeks"][str(d)]
                ratio = round(minutes / cap, 3) if cap else 0
                weeks_out.append({"week_start": str(d), "minutes": minutes, "ratio": ratio, "band": _band(ratio)})
            rows.append({"member_id": member, "name": names.get(member, "？"), "weeks": weeks_out})
        rows.sort(key=lambda r: -sum(w["minutes"] for w in r["weeks"]))
        return success_response(rows, meta={"weeks": weeks, "default_capacity": DEFAULT_WEEKLY_CAPACITY})


class CpLockView(APIView):
    """POST/GET/DELETE .../projects/{pid}/gantt/cp-lock/ —— 锁定/查询/解锁。"""

    permission_classes = [IsAuthenticatedAndActive]

    def _project(self, request, *args, **kwargs):
        return get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)[0]

    def get(self, request, *args, **kwargs):
        project = self._project(request, **kwargs)
        key = _CP_LOCK_KEY.format(project_id=project.id)
        snap = cache.get(key)
        return success_response({"locked": snap is not None, "snapshot": snap})

    def post(self, request, *args, **kwargs):
        project = self._project(request, **kwargs)
        require_role(request, self, WorkspaceRole.ADMIN)  # BR-03
        key = _CP_LOCK_KEY.format(project_id=project.id)
        if cache.get(key) is not None:  # BR-01 重复锁定
            raise AppException("RESOURCE_STATE_INVALID", message="关键路径已锁定（先解锁再重锁）")
        chain = list(
            IssueCPMCache.objects.filter(project=project, is_critical=True)
            .order_by("es")
            .values_list("issue_id", "es", "ef", "ls", "lf", "float_days")
        )
        snapshot = {
            "locked_at": timezone.now().isoformat(),
            "locked_by": str(request.user.id),
            "chain": [
                {
                    "issue_id": str(c[0]),
                    "es": str(c[1]),
                    "ef": str(c[2]),
                    "ls": str(c[3]),
                    "lf": str(c[4]),
                    "float_days": c[5],
                }
                for c in chain
            ],
        }
        cache.set(key, snapshot, timeout=None)  # 永久直至解锁
        record(
            event_key=hashlib.sha256(f"cplock:{project.id}:{request.user.id}:{_time.time()}".encode()).hexdigest()[:80],
            category="workflow",
            action="state_changed",
            workspace_id=project.workspace_id,
            actor=request.user,
            obj={"type": "cp_lock", "id": str(project.id)},
            detail={"chain_len": len(chain)},
        )
        return success_response({"locked": True, "chain_len": len(chain)}, status_code=201)

    def delete(self, request, *args, **kwargs):
        project = self._project(request, **kwargs)
        require_role(request, self, WorkspaceRole.ADMIN)
        key = _CP_LOCK_KEY.format(project_id=project.id)
        deleted = cache.delete(key)
        return success_response({"unlocked": bool(deleted or True)})
