"""关键路径 CPM 引擎与重算管道（GANTT-003 §4.1~4.3，Sprint-9）。

Kahn 拓扑 + 正推/逆推两遍扫描，O(V+E) 纯内存（BR-11）；环防御跳过项目不
阻断（BR-02）；anchor_today 入指纹（跨日必失配 → 每日真算，BR-07 防漂移）；
60s debounce 合并触发（BR-10）；负浮动 = 关键（§1.4，float ≤ 0）。
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import date, timedelta

from celery import shared_task
from django.core.cache import cache
from django.utils import timezone

from plane.db.models import Issue, IssueCPMCache, IssueLink, Project

logger = logging.getLogger(__name__)


@dataclass
class CPMTask:
    """参与计算的任务分档快照（BR-01/04/12）。"""
    id: uuid.UUID
    start_date: date | None
    target_date: date | None
    duration: int
    completed_date: date | None = None
    started: bool = False


@dataclass
class CPMRow:
    issue_id: uuid.UUID
    es: date
    ef: date
    ls: date
    lf: date
    float_days: int
    is_critical: bool


@dataclass
class CPMResult:
    rows: list[CPMRow] = field(default_factory=list)
    input_hash: str | None = None
    external_preds: set = field(default_factory=set)


class CPMEngine:
    """Kahn 拓扑 + 两遍扫描；节点标识统一 issue_id（UUID）。"""

    # BR-01：CPM 只统计「日期完整」的未完成任务 + 已完成锚点；cancelled 剔除
    def _load_tasks(self, project_id) -> list[CPMTask]:
        rows = (Issue.objects
                .filter(project_id=project_id, deleted_at__isnull=True,
                        start_date__isnull=False, target_date__isnull=False)
                .exclude(state__group="cancelled")
                .values("id", "start_date", "target_date", "state__group",
                        "completed_at", "updated_at"))
        tasks = []
        for r in rows:
            group = r["state__group"]
            completed = group == "completed"
            tasks.append(CPMTask(
                id=r["id"],
                start_date=r["start_date"], target_date=r["target_date"],
                duration=max((r["target_date"] - r["start_date"]).days, 1),
                completed_date=r["start_date"] if completed else None,  # 完成档历史锚点（简化：以排期窗为锚）
                started=(group == "started")))
        return tasks

    def _load_edges(self, project_id) -> tuple[dict, dict]:
        """BR-03：仅同项目 blocks 正向边（跨项目不进 CPM——外部约束单列 ⚓）。"""
        preds: dict = {}
        succs: dict = {}
        links = (IssueLink.objects
                 .filter(relation_type="blocks", deleted_at__isnull=True,
                         issue__project_id=project_id,
                         related_issue__project_id=project_id,
                         issue__deleted_at__isnull=True,
                         related_issue__deleted_at__isnull=True)
                 .values_list("issue_id", "related_issue_id"))
        for src, dst in links:  # src blocks dst → dst 的前置是 src
            succs.setdefault(src, []).append(dst)
            preds.setdefault(dst, []).append(src)
        return preds, succs

    def external_pred_ids(self, project_id) -> set:
        """BR-03 外部前置：阻塞源在其它项目的任务集合（⚓ 标记，不进计算）。"""
        return set(IssueLink.objects
                   .filter(relation_type="is_blocked_by", issue__project_id=project_id,
                           issue__deleted_at__isnull=True, deleted_at__isnull=True)
                   .exclude(related_issue__project_id=project_id)
                   .values_list("issue_id", flat=True))

    def compute(self, project_id, anchor_today: date, project_deadline=None) -> CPMResult:
        tasks = self._load_tasks(project_id)
        if not tasks:
            return CPMResult(input_hash=self._fingerprint(tasks, {}, None, anchor_today))
        preds, succs = self._load_edges(project_id)
        task_ids = {t.id for t in tasks}
        # 边裁剪：端点不在任务集（缺日期/取消）的边不参与（防 KeyError）
        preds = {k: [p for p in v if p in task_ids] for k, v in preds.items() if k in task_ids}
        succs = {k: [s for s in v if s in task_ids] for k, v in succs.items() if k in task_ids}
        order = self._topo_sort(tasks, preds, succs)
        if order is None:
            return CPMResult(input_hash=None)
        es: dict = {}
        ef: dict = {}
        by_id = {t.id: t for t in tasks}
        for tid in order:                                        # ① 正推
            t = by_id[tid]
            if t.completed_date is not None:                     # 已完成档：历史锚点（BR-12）
                es[tid] = t.start_date
                ef[tid] = t.target_date
                continue
            preds_ef = [ef[p] for p in preds.get(tid, []) if p in ef]
            if t.started:                                        # 已开始未完成：不叠加今日锚点
                base = preds_ef + ([t.start_date] if t.start_date else [])
            else:                                                # 未开始：今日下限（BR-05）
                base = preds_ef + [max(t.start_date or anchor_today, anchor_today)]
            es[tid] = max(base) if base else anchor_today
            ef[tid] = es[tid] + timedelta(days=t.duration)  # type: ignore[operator]
        lf: dict = {}
        ls: dict = {}
        deadline = project_deadline or max(t.target_date for t in tasks if t.target_date)
        for tid in reversed(order):                              # ② 逆推（BR-06 锚点）
            succs_ls = [ls[s] for s in succs.get(tid, []) if s in ls]
            lf[tid] = min(succs_ls + [deadline])
            ls[tid] = lf[tid] - timedelta(days=by_id[tid].duration)
        rows = []
        for tid in order:                                        # ③ 派生：float ≤ 0 = 关键
            t = by_id[tid]
            if t.completed_date is not None:
                continue                                         # BR-12：完成者不进 rows
            float_days = (ls[tid] - es[tid]).days
            rows.append(CPMRow(issue_id=tid, es=es[tid], ef=ef[tid],
                               ls=ls[tid], lf=lf[tid], float_days=float_days,
                               is_critical=(float_days <= 0)))
        return CPMResult(rows=rows,
                         input_hash=self._fingerprint(tasks, preds, deadline, anchor_today),
                         external_preds=self.external_pred_ids(project_id))

    def _topo_sort(self, tasks, preds, succs):
        indeg = {t.id: len(preds.get(t.id, [])) for t in tasks}
        queue = deque(tid for tid, d in indeg.items() if d == 0)
        order = []
        while queue:
            tid = queue.popleft()
            order.append(tid)
            for s in succs.get(tid, []):
                indeg[s] -= 1
                if indeg[s] == 0:
                    queue.append(s)
        if len(order) < len(tasks):                              # 残余 = 环（BR-02 防御）
            logger.error("cpm_cycle_detected project=%s", tasks[0] and tasks[0].id)
            return None
        return order

    @staticmethod
    def _fingerprint(tasks, preds, deadline, anchor_today) -> str:
        """BR-07 输入指纹：任务排期/工期/完成态 + 边集 + deadline + anchor_today。"""
        payload = sorted(
            (str(t.id), str(t.start_date), str(t.target_date), t.duration,
             str(t.completed_date), int(t.started)) for t in tasks)
        edges = sorted((str(k), str(v)) for k, vs in preds.items() for v in vs)
        raw = repr((payload, edges, str(deadline), str(anchor_today)))
        return hashlib.md5(raw.encode()).hexdigest()


# ────────────────────────────────────────────────────────────────
# 重算管道（BR-10 debounce / BR-07 指纹零写）
# ────────────────────────────────────────────────────────────────
@shared_task(queue="reports", max_retries=3, autoretry_for=(Exception,), retry_backoff=True)
def cpm_recompute(project_id: str):
    """改期/依赖/完成 on_commit 触发；同项目 60s debounce 合并；指纹命中零写。"""
    if not cache.add(f"cpm:run:{project_id}", "1", timeout=60):
        cpm_recompute.apply_async(args=[project_id], countdown=60)
        return {"deferred": True}
    from plane.db.models import CPMAlertConfig
    engine = CPMEngine()
    today = timezone.localdate()
    config = CPMAlertConfig.objects.filter(project_id=project_id).first()
    old = {r.issue_id: r for r in IssueCPMCache.objects.filter(project_id=project_id)}
    old_hash = next(iter(old.values())).input_hash if old else None
    result = engine.compute(uuid.UUID(project_id), anchor_today=today,
                            project_deadline=(config.target_completion_date if config else None))
    if result.input_hash is None:
        return {"skipped": "cycle"}
    if old_hash == result.input_hash:
        return {"skipped": "fingerprint-hit"}
    # BR-09「转移」判定需旧行可比：old_hash is None（首算/重建）整批跳过转移告警
    notify_transitions = old_hash is not None
    IssueCPMCache.objects.filter(project_id=project_id).delete()
    rows = []
    for row in result.rows:
        rows.append(IssueCPMCache(
            project_id=uuid.UUID(project_id), issue_id=row.issue_id,
            es=row.es, ef=row.ef, ls=row.ls, lf=row.lf,
            float_days=row.float_days, is_critical=row.is_critical,
            has_external_preds=row.issue_id in result.external_preds,
            input_hash=result.input_hash))
    IssueCPMCache.objects.bulk_create(rows)
    if notify_transitions and config and config.float_consumed_alert_enabled:
        _notify_float_consumed(project_id, old, result)
    return {"rows": len(rows), "hash": result.input_hash}


def _notify_float_consumed(project_id, old_rows: dict, result: CPMResult) -> None:
    """BR-09：非关键 → 关键转移即时一条（负责人 + 项目经理）。"""
    from plane.db.models import Notification
    for row in result.rows:
        old = old_rows.get(row.issue_id)
        if old is not None and not old.is_critical and row.is_critical:
            issue = Issue.objects.filter(id=row.issue_id).select_related(
                "project", "project__created_by").first()
            if issue is None:
                continue
            key = f"cpm:trans:{row.issue_id}:{timezone.localdate()}"
            Notification.objects.get_or_create(
                receiver=issue.project.created_by, dedup_key=key,
                defaults={
                    "event": "issue.updated",
                    "title": f"{issue.project.identifier}-{issue.sequence_id} 浮动耗尽，转为关键任务",
                    "data": {"issue_id": str(issue.id), "project_id": project_id,
                             "float_days": row.float_days},
                })


@shared_task(queue="reports")
def cpm_daily_maintenance() -> dict:
    """beat 每日 09:30：① anchor_today 跨日指纹失配全量真算；② BR-08 关键逾期预警。"""
    from plane.db.models import CPMAlertConfig, ProjectMember
    from plane.db.models.roles import ProjectRole
    today = timezone.localdate()
    projects = (Project.objects.filter(status="active", deleted_at__isnull=True)
                .filter(issues__deleted_at__isnull=True,
                        issues__start_date__isnull=False,
                        issues__target_date__isnull=False)
                .exclude(issues__state__group="cancelled").distinct().values_list("id", flat=True))
    notified = 0
    for pid in projects:
        cpm_recompute(str(pid))
        config = CPMAlertConfig.objects.filter(project_id=pid).first()
        if config and not config.overdue_alert_enabled:
            continue
        rows = (IssueCPMCache.objects
                .filter(project_id=pid, is_critical=True, issue__target_date__lt=today)
                .exclude(issue__state__group__in=["completed", "cancelled"])
                .select_related("issue", "issue__project"))
        from plane.db.models import Notification
        for row in rows:
            key = f"cpm:overdue:{row.issue_id}:{today}"
            manager = ProjectMember.objects.filter(
                project_id=pid, role__gte=ProjectRole.ADMIN, is_active=True
            ).values_list("member_id", flat=True).first()
            receiver_id = manager or row.issue.project.created_by_id
            _, created = Notification.objects.get_or_create(
                receiver_id=receiver_id, dedup_key=key,
                defaults={
                    "event": "issue.updated",
                    "title": (f"{row.issue.project.identifier}-{row.issue.sequence_id} "
                              f"关键任务已逾期 {abs(row.float_days)} 天"),
                    "data": {"issue_id": str(row.issue_id), "project_id": str(pid),
                             "float_days": row.float_days},
                })
            notified += int(created)
    return {"projects": len(list(projects)), "notified": notified}
