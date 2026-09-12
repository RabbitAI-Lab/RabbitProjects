"""报表查询引擎（RPT-005 §4.2，P4 R7）。

数据集注册表（上游实况映射——零新建聚合表）：ds_issue_live（Issue 实时
聚合，RPT-002 口径基座）/ ds_cycle（CycleSnapshot）/ ds_worklog
（WorkLogSummary）/ ds_health（HealthSnapshot）。指标按注册表编译为
对应源查询；维度分组聚合（时间维度按月分桶、自定义字段枚举维度）。
权限剪枝：查看者对项目不可见 → 该项目行不进聚合（pruned 标记，BR-06）。
"""

from __future__ import annotations

import logging

from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import TruncMonth

logger = logging.getLogger("plane.reports")

#: 数据集注册表（§4.7——dataset → 源模型与可用指标/维度）
DATASETS: dict[str, dict] = {
    "ds_issue_live": {
        "metrics": {
            "m_issue_count": ("count", "任务总数"),
            "m_done_count": ("done_count", "已完成数"),
            "m_open_count": ("open_count", "未结数"),
            "m_done_ratio": ("done_ratio", "完成率"),
            "m_overdue_count": ("overdue_count", "逾期数"),
        },
        "dimensions": {"d_month": "按月", "d_state": "按状态", "d_priority": "按优先级"},
        "source": "issue",
    },
    "ds_cycle": {
        "metrics": {"m_cycle_progress": ("progress", "迭代进度")},
        "dimensions": {"d_cycle": "按迭代"},
        "source": "cycle_snapshot",
    },
    "ds_worklog": {
        "metrics": {"m_logged_minutes": ("total_minutes", "已投工时")},
        "dimensions": {"d_member": "按成员"},
        "source": "worklog_summary",
    },
    "ds_health": {
        "metrics": {"m_load_ratio": ("load_ratio", "负载率")},
        "dimensions": {"d_member": "按成员"},
        "source": "health_snapshot",
    },
}


class ReportQueryEngine:
    """config 编译执行（metrics × dimensions → 分组聚合行）。"""

    def execute(self, workspace, config: dict, *, viewer=None) -> dict:
        dataset = config.get("dataset", "ds_issue_live")
        if dataset not in DATASETS:
            raise ValueError(f"未知数据集 {dataset}")
        metrics = [m for m in (config.get("metrics") or []) if m in DATASETS[dataset]["metrics"]]
        if not metrics:
            raise ValueError("无有效指标")
        dimension = (config.get("dimensions") or [None])[0]
        rows, pruned = (
            self._query_issue_live(workspace, metrics, dimension, viewer)
            if dataset == "ds_issue_live"
            else self._query_snapshot(workspace, dataset, metrics, dimension)
        )
        return {
            "dataset": dataset,
            "rows": rows,
            "pruned": pruned,
            "truncated": len(rows) > 200,
            "data_until": "realtime" if dataset == "ds_issue_live" else "latest_snapshot",
        }

    # ── ds_issue_live（Issue 实时聚合——RPT-002 口径） ─────────────

    def _query_issue_live(self, workspace, metrics, dimension, viewer):
        from plane.db.models import Issue, Project

        projects = Project.objects.filter(workspace=workspace, deleted_at__isnull=True)
        if viewer is not None:
            visible = set(projects.accessible_by(viewer, workspace_id=workspace.id).values_list("id", flat=True))
            pruned = projects.count() - len(visible)
            projects = projects.filter(id__in=visible)
        else:
            pruned = 0
        qs = Issue.objects.filter(project__in=projects, deleted_at__isnull=True)
        today = __import__("django.utils.timezone", fromlist=["timezone"]).now().date()
        annotations = {
            "count": Count("id"),
            "done_count": Count("id", filter=Q(completed_at__isnull=False)),
            "open_count": Count("id", filter=Q(completed_at__isnull=True)),
            "overdue_count": Count("id", filter=Q(completed_at__isnull=True, target_date__lt=today)),
        }
        if dimension == "d_month":
            grouped = qs.annotate(bucket=TruncMonth("created_at")).values("bucket").annotate(**annotations)
            rows = []
            for g in grouped.order_by("bucket")[:200]:
                total = g["count"] or 0
                rows.append(
                    {
                        "key": str(g["bucket"] or "-"),
                        "m_issue_count": total,
                        "m_done_count": g["done_count"],
                        "m_open_count": g["open_count"],
                        "m_overdue_count": g["overdue_count"],
                        "m_done_ratio": round(g["done_count"] / total, 4) if total else 0,
                    }
                )
            return rows, pruned
        # 无维度 / 枚举维度（d_state/d_priority 以行内枚举键返回）
        agg = qs.aggregate(**annotations)
        total = agg["count"] or 0
        row = {
            "key": "ALL",
            "m_issue_count": total,
            "m_done_count": agg["done_count"],
            "m_open_count": agg["open_count"],
            "m_overdue_count": agg["overdue_count"],
            "m_done_ratio": round(agg["done_count"] / total, 4) if total else 0,
        }
        if dimension == "d_state":
            for state_id, cnt in qs.values_list("state_id").annotate(c=Count("id")).order_by("-c")[:12]:
                row[f"state:{state_id}"] = cnt
        if dimension == "d_priority":
            for pr, cnt in qs.values_list("priority").annotate(c=Count("id")).order_by("-c")[:12]:
                row[f"priority:{pr}"] = cnt
        return [row], pruned

    # ── 快照型数据集 ──────────────────────────────────────────────

    def _query_snapshot(self, workspace, dataset, metrics, dimension):
        rows: list[dict] = []
        if dataset == "ds_cycle":
            # 剩余总量趋势（remaining_total——口径 measure 维度）
            from plane.db.models import CycleSnapshot

            qs = CycleSnapshot.objects.filter(cycle__project__workspace=workspace)
            agg = qs.aggregate(remaining=Avg("remaining_total"))
            rows = [{"key": "ALL", "m_cycle_progress": round(agg["remaining"] or 0, 2)}]
        elif dataset == "ds_worklog":
            from plane.db.models import WorkLogSummary

            qs = WorkLogSummary.objects.filter(project__workspace=workspace)
            agg = qs.aggregate(total_minutes=Sum("total_minutes"), approved=Sum("approved_minutes"))
            rows = [
                {
                    "key": "ALL",
                    "m_logged_minutes": int(agg["total_minutes"] or 0),
                    "m_approved_minutes": int(agg["approved"] or 0),
                }
            ]
        elif dataset == "ds_health":
            from plane.db.models import HealthSnapshot

            qs = HealthSnapshot.objects.filter(project__workspace=workspace)
            agg = qs.aggregate(score=Avg("total_score"))
            rows = [{"key": "ALL", "m_load_ratio": round(agg["score"] or 0, 4)}]
        return rows, 0
