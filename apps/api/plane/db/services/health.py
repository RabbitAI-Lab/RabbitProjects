"""项目健康度服务（RPT-004 §4.2/§4.3，Sprint-9）。

BR-01 四维全部复用 RPT-002 基座取数（issue_stats_base/overdue_q/open_q，
零独立统计 SQL）；BR-04① dims 与 drilldown_count 同事务口径；BR-05 样本
不足维度剔除后权重重归一；负载矩阵消费 WorkLogSummary 快照（TASK-013
同源，容量分母 = ProjectWorklogConfig.weekly_capacity_minutes 唯一源）。
"""
from __future__ import annotations

from datetime import date, timedelta

from celery import shared_task
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from plane.db.models import (
    Cycle,
    HealthConfig,
    HealthSnapshot,
    IssueLink,
    Project,
    ProjectWorklogConfig,
    WorkLog,
    WorkLogSummary,
)
from plane.db.services.stats import issue_stats_base, open_q, overdue_q

#: 数据不足样本下限（BR-05：effort 维 <3 → None）
MIN_EFFORT_SAMPLES = 3


def _renormalize(weights: dict, dims: dict) -> dict:
    """BR-05：样本不足（None）维度剔除后权重重归一（和恒 1；全空保持原值）。"""
    alive = [k for k, v in dims.items() if v is not None]
    total = sum(weights.get(k, 0) for k in alive)
    if not alive or total == 0:
        return {k: 0.0 for k in weights}
    return {k: (weights.get(k, 0) / total if k in alive else 0.0) for k in weights}


class HealthReportService:
    def _dim(self, *, value, score, n, drill_qs) -> dict:
        return {"value": value, "score": round(score), "n": n,
                "drilldown_count": drill_qs.count()}

    def _blocked_dim(self, project, base) -> dict | None:
        """BR-11 阻塞率：分子含跨项目边（镜像行判定不加项目过滤——统计面扩大
        至含跨项目边集，与 PROJ-004 §4.4 放开同口径）；分母 = 非取消任务数。"""
        blocked_ids = set(
            IssueLink.objects
            .filter(relation_type="is_blocked_by", deleted_at__isnull=True,
                    issue__deleted_at__isnull=True)
            .exclude(related_issue__state__group__in=["completed", "cancelled"])
            .exclude(issue__state__group__in=["completed", "cancelled"])
            .values_list("issue_id", flat=True))
        n = base.filter(state__group__in=["backlog", "unstarted", "started"]).count()
        if n == 0:
            return None
        blocked_n = base.filter(id__in=blocked_ids).count()
        value = round(blocked_n / n, 2)
        return self._dim(value=value, score=100 - value * 300, n=n,
                         drill_qs=base.filter(id__in=blocked_ids)
                                      .order_by("target_date", "id"))

    def compute(self, project, day: date, cfg: HealthConfig) -> HealthSnapshot:
        """四维评分 + 快照落库（update_or_create 幂等）。"""
        with transaction.atomic():  # BR-04①：dims 与 drilldown_count 同事务口径
            base = issue_stats_base(project_id=project.id)
            agg = base.aggregate(
                non_cancelled=Count("id", filter=~Q(state__group="cancelled")),
                done=Count("id", filter=Q(state__group="completed")),
                due_open=Count("id", filter=~Q(state__group__in=["completed", "cancelled"])
                                          & Q(target_date__isnull=False)),
                overdue=Count("id", filter=overdue_q(day)),
                effort_n=Count("id", filter=open_q() & Q(estimate_minutes__gt=0)),
                est=Coalesce(Sum("estimate_minutes", filter=open_q() & Q(estimate_minutes__gt=0)), 0),
            )
            spent = (WorkLog.objects
                     .filter(issue__project=project, deleted_at__isnull=True,
                             issue__archived_at__isnull=True,
                             issue__state__group__in=["unstarted", "started"],
                             issue__estimate_minutes__gt=0)
                     .aggregate(s=Coalesce(Sum("minutes"), 0))["s"])

            # 活跃时间盒（RPT-003 Cycle 同源）：无活跃 → progress 样本不足（BR-05）
            cycle = next((c for c in Cycle.objects.filter(
                project=project, start_date__isnull=False, end_date__isnull=False
            ).order_by("-start_date") if c.start_date <= day <= c.end_date), None)
            progress = None
            if cycle is not None:
                done_ratio = agg["done"] / max(agg["non_cancelled"], 1)
                span = (cycle.end_date - cycle.start_date).days
                elapsed = 1 if span == 0 else min(max((day - cycle.start_date).days / span, 0), 1)
                value = round(done_ratio - elapsed, 2)
                progress = self._dim(
                    value=value, score=100 - abs(value) * 200, n=agg["non_cancelled"],
                    drill_qs=base.exclude(state__group__in=["completed", "cancelled"])
                                 .filter(cycle_id=cycle.id).order_by("target_date", "id"))

            overdue_v = round(agg["overdue"] / max(agg["due_open"], 1), 2)
            overdue = (self._dim(
                value=overdue_v, score=100 - overdue_v * 200, n=agg["due_open"],
                drill_qs=base.filter(overdue_q(day)).order_by("target_date", "id"))
                if agg["due_open"] else None)   # 分母 0 = 无到期任务 → 样本不足（BR-10）

            effort = None                       # BR-05 样本 <3 → None
            if agg["effort_n"] >= MIN_EFFORT_SAMPLES and agg["est"]:
                effort_v = round(spent / agg["est"], 2)
                effort = self._dim(
                    value=effort_v, score=100 - abs(1 - effort_v) * 100, n=agg["effort_n"],
                    drill_qs=base.filter(open_q() & Q(estimate_minutes__gt=0))
                                 .order_by("target_date", "id"))

            dims = {"progress": progress, "overdue": overdue,
                    "effort": effort, "blocked": self._blocked_dim(project, base)}
            weights = _renormalize(cfg.weights, dims)
            weighted = [d["score"] * w for k, w in weights.items()
                        if (d := dims.get(k)) is not None and w]
            total = round(sum(weighted), 1) if weighted else None
            if total is None:
                band = "insufficient"
            elif total >= 80:
                band = "green"
            elif total >= 60:
                band = "yellow"
            else:
                band = "red"
            snap, _ = HealthSnapshot.objects.update_or_create(
                project=project, snapshot_date=day,
                defaults={"dimensions": dims, "total_score": total, "band": band,
                          "config_snapshot": cfg.as_dict()})
        return snap

    # ── 下钻（BR-04② 实时口径）──────────────────────────────────
    DRILL_WHITELIST = {"progress", "overdue", "effort", "blocked"}

    def drilldown(self, project, dimension: str):
        """实时构成清单（游标分页前 100）——与快照 drilldown_count 允许时点差。"""
        base = issue_stats_base(project_id=project.id)
        today = timezone.localdate()
        if dimension == "overdue":
            qs = base.filter(overdue_q(today)).order_by("target_date", "id")
        elif dimension == "progress":
            cycle = next((c for c in Cycle.objects.filter(
                project=project, start_date__isnull=False, end_date__isnull=False
            ).order_by("-start_date") if c.start_date <= today <= c.end_date), None)
            qs = (base.exclude(state__group__in=["completed", "cancelled"])
                      .filter(cycle_id=cycle.id).order_by("target_date", "id")) if cycle else base.none()
        elif dimension == "effort":
            qs = base.filter(open_q() & Q(estimate_minutes__gt=0)).order_by("target_date", "id")
        elif dimension == "blocked":
            blocked_ids = set(
                IssueLink.objects
                .filter(relation_type="is_blocked_by", deleted_at__isnull=True,
                        issue__deleted_at__isnull=True)
                .exclude(related_issue__state__group__in=["completed", "cancelled"])
                .exclude(issue__state__group__in=["completed", "cancelled"])
                .values_list("issue_id", flat=True))
            qs = base.filter(id__in=blocked_ids).order_by("target_date", "id")
        else:
            raise KeyError(dimension)
        return qs

    # ── 负载矩阵（BR-06/07，§2.3）────────────────────────────────
    def workload(self, project, frm: date, to: date) -> tuple[list, int]:
        """人×周矩阵（WorkLogSummary 快照直查）；容量分母唯一源 weekly_capacity_minutes。"""
        rows = (WorkLogSummary.objects
                .filter(project=project, week_start__range=(frm, to))
                .values("actor_id", "actor__display_name", "week_start")
                .annotate(minutes=Sum("total_minutes"))
                .order_by("actor__display_name", "week_start"))
        matrix: dict = {}
        for r in rows:
            cell = matrix.setdefault(r["actor_id"], {
                "actor_id": str(r["actor_id"]), "actor": r["actor__display_name"], "cells": {}})
            cell["cells"][r["week_start"].isoformat()] = r["minutes"]
        return list(matrix.values()), len(matrix)

    @staticmethod
    def capacity_minutes(project) -> int:
        cfg = ProjectWorklogConfig.objects.filter(project=project).first()
        return cfg.weekly_capacity_minutes if cfg else 2400   # 默认 40h/周


@shared_task(queue="reports")
def health_daily_snapshot() -> dict:
    """beat 每日 00:40：活跃项目健康快照（趋势数据源，BR-03）。"""
    yesterday = timezone.localdate() - timedelta(days=1)
    done = 0
    for project in Project.objects.filter(status="active", deleted_at__isnull=True):
        HealthReportService().compute(project, yesterday, HealthConfig.of(project))
        done += 1
    return {"projects": done}


# ────────────────────────────────────────────────────────────────
# 异步导出执行器（S7 A#1 + S8 A#1 债统一接入，GANTT-002 §4.5 两段式）
# ────────────────────────────────────────────────────────────────
@shared_task(queue="reports", max_retries=2, autoretry_for=(Exception,), retry_backoff=True)
def run_export_task(task_id: str) -> dict:
    """worker 侧：生成 CSV → MinIO → 预签名 URL（任务行状态机 pending→succeeded/failed）。"""
    from plane.db.models import ExportTask
    task = ExportTask.objects.filter(pk=task_id).first()
    if task is None:
        return {"skipped": "missing"}
    ExportTask.objects.filter(pk=task_id).update(status=ExportTask.Status.RUNNING)
    try:
        csv_text = _render_export_csv(task)
        key = f"exports/{task.export_type}/{task.id}.csv"
        url, expires_at = _put_minio_and_presign(key, csv_text, task)
        ExportTask.objects.filter(pk=task_id).update(
            status=ExportTask.Status.SUCCEEDED, file_key=key,
            download_url=url, expires_at=expires_at, error="")
        return {"status": "succeeded", "rows": csv_text.count("\n")}
    except Exception as exc:  # noqa: BLE001 —— 状态机兜底：失败留因可查
        ExportTask.objects.filter(pk=task_id).update(
            status=ExportTask.Status.FAILED, error=str(exc)[:2000])
        raise


def _render_export_csv(task) -> str:
    """按导出类型渲染 CSV 文本（同步流式同款行源，复用聚合函数）。"""
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    if task.export_type == "workload_csv":
        project = Project.objects.get(pk=task.params["project_id"])
        frm, to = date.fromisoformat(task.params["from"]), date.fromisoformat(task.params["to"])
        rows, _ = HealthReportService().workload(project, frm, to)
        capacity = HealthReportService.capacity_minutes(project)
        w.writerow(["actor", "week_start", "minutes", "load_ratio"])
        for row in rows:
            for wk, minutes in sorted(row["cells"].items()):
                w.writerow([row["actor"], wk, minutes, round(minutes / capacity, 4)])
    elif task.export_type == "burndown_csv":
        from plane.db.models import Cycle
        from plane.db.services.agile_reports import AgileReportService
        cycle = Cycle.objects.select_related("project").get(pk=task.params["cycle_id"])
        payload = AgileReportService().burndown(cycle.id)
        w.writerow(["date", "remaining", "completed_delta"])
        for p in payload.points:
            w.writerow([p["date"], p["remaining"], p.get("completed_delta", "")])
    elif task.export_type == "audit_csv":
        from plane.db.models import AuditLog
        filters = task.params.get("filters") or {}
        qs = AuditLog.objects.filter(workspace_id=task.params["workspace_id"])
        for key in ("actor", "category", "action", "object_type", "ip"):
            v = filters.get(key)
            if v:
                field = "actor_id" if key == "actor" else key
                qs = qs.filter(**{f"{field}__iexact": v})
        if filters.get("search"):
            qs = qs.filter(object_snapshot__name__icontains=filters["search"])
        w.writerow(["id", "event_key", "category", "action", "actor_id",
                    "actor_name", "object_type", "object_id", "object_name",
                    "detail", "ip", "created_at", "hash"])
        for r in qs.iterator(chunk_size=2000):
            w.writerow([
                r.id, r.event_key, r.category, r.action, r.actor_id,
                (r.actor_snapshot or {}).get("name", ""),
                r.object_type, r.object_id,
                (r.object_snapshot or {}).get("name", ""),
                r.detail, r.ip, r.created_at.isoformat(), r.hash,
            ])
    else:
        raise ValueError(f"未知导出类型 {task.export_type}")
    return buf.getvalue()


def _put_minio_and_presign(key: str, text: str, task):
    """MinIO put_object + 预签名 URL（15 分钟）——复用 FILE 域存储基建。"""
    import boto3
    from django.conf import settings as dj_settings
    s3 = boto3.client(
        "s3", endpoint_url=dj_settings.AWS_S3_ENDPOINT_URL,
        aws_access_key_id=dj_settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=dj_settings.AWS_SECRET_ACCESS_KEY)
    s3.put_object(Bucket=dj_settings.AWS_S3_BUCKET_NAME, Key=key,
                  Body=text.encode("utf-8"), ContentType="text/csv; charset=utf-8")
    expires = 900
    url = s3.generate_presigned_url(
        "get_object", Params={"Bucket": dj_settings.AWS_S3_BUCKET_NAME, "Key": key},
        ExpiresIn=expires)
    return url, timezone.now() + timedelta(seconds=expires)
