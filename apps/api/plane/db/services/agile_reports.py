"""敏捷报表快照管道与图表服务（RPT-003 §4.3/§4.4，Sprint-9）。

数据可信设计（§2.3）：迭代结束即冻结、已归档报表只读快照直查——历史任务
修改零影响（BR-06 不可篡改红线）。口径单源 compute_cycle_snapshot：
进行中日直查当前表 / 历史日经 IssueActivity（field='state'/'cycles'）回放，
同一函数两态复用（实时/回算一致）。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from plane.db.models import (
    Cycle,
    CycleSnapshot,
    DailyGroupSnapshot,
    Issue,
    IssueActivity,
    ProjectReportConfig,
    State,
)

logger = logging.getLogger(__name__)

GROUPS = ["backlog", "unstarted", "started", "completed", "cancelled"]
REMAINING_GROUPS = ("backlog", "unstarted", "started")


class MeasureInvalid(Exception):
    """度量口径非法（非 count/estimate_minutes/cf_<uuid> 或 cf 非数字类型）→ 400。"""


def measure_of(issue: Issue, measure: str) -> float:
    """count=1 / estimate_minutes=分钟数 / cf_<uuid>=数字自定义字段值。"""
    if measure == "count":
        return 1.0
    if measure == "estimate_minutes":
        return float(issue.estimate_minutes or 0)
    if measure.startswith("cf_"):
        val = (issue.custom_fields or {}).get(measure[3:])
        if val is None:
            return 0.0
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0
    raise MeasureInvalid(measure)


def validate_measure(measure: str) -> None:
    if measure not in ("count", "estimate_minutes") and not (
            measure.startswith("cf_") and len(measure) > 3):
        raise MeasureInvalid(measure)


# ────────────────────────────────────────────────────────────────
# 回放（历史日口径）——state / cycles 两类事件
# ────────────────────────────────────────────────────────────────
def _events(issue_ids: list, field_name: str, upto_day):
    """截至 upto_day 24:00 的事件（含当日），按时间升序。"""
    day_end = _end_of_day(upto_day)
    return list(
        IssueActivity.objects
        .filter(issue_id__in=issue_ids, field=field_name, created_at__lte=day_end)
        .order_by("created_at", "id")
        .values("issue_id", "old_identifier", "new_identifier", "old_value", "new_value")
    )


def _end_of_day(day):
    import datetime as _dt
    return timezone.make_aware(_dt.datetime.combine(day, _dt.time(23, 59, 59)))


def state_group_at(issue: Issue, day, state_events: dict) -> str:
    """任务在 day 当日所处的状态组：day >= 今日 → 当前值；历史日 → 事件回放。"""
    if day >= timezone.localdate():
        return (issue.state.group if issue.state else None) or "unstarted"
    events = state_events.get(issue.id)
    if not events:
        return (issue.state.group if issue.state else None) or "unstarted"
    group = None
    state_ids: set[uuid.UUID] = set()
    for ev in events:
        state_ids.update(i for i in (ev["new_identifier"],) if i)
    group_of = dict(State.objects.filter(id__in=state_ids).values_list("id", "group"))
    for ev in events:
        group = group_of.get(ev["new_identifier"]) or group
    return group or "unstarted"


def issues_in_cycle_at(cycle: Cycle, day) -> list[Issue]:
    """day 当日的迭代成员集：当前成员 ∪ 回放（cycles 事件当日有效归属）。"""
    current = list(Issue.objects.filter(cycle=cycle, deleted_at__isnull=True))
    if day >= timezone.localdate():
        return current
    member_ids = {i.id for i in current}
    events = list(
        IssueActivity.objects
        .filter(field="cycles", issue__project_id=cycle.project_id,
                created_at__lte=_end_of_day(day))
        .filter(Q(new_identifier=cycle.id) | Q(old_identifier=cycle.id))
        .order_by("created_at", "id")
        .values("issue_id", "old_identifier", "new_identifier"))
    replay = {e["issue_id"] for e in events if e["new_identifier"] == cycle.id}
    replay -= {e["issue_id"] for e in events if e["old_identifier"] == cycle.id}
    extra_ids = (replay | member_ids) - member_ids
    if extra_ids:
        current += list(Issue.objects.filter(id__in=extra_ids, deleted_at__isnull=True))
    return current


def compute_group_counts(project_id, day, measure: str) -> dict:
    """项目级五组快照（CFD 源，BR-12）——进行中日直查；历史日回放。"""
    issues = list(Issue.objects.filter(
        project_id=project_id, deleted_at__isnull=True).select_related("state"))
    state_events: dict = {}
    if day < timezone.localdate():
        for ev in _events([i.id for i in issues], "state", day):
            state_events.setdefault(ev["issue_id"], []).append(ev)
    counts = {g: 0.0 for g in GROUPS}
    for issue in issues:
        counts[state_group_at(issue, day, state_events)] += measure_of(issue, measure)
    return counts


def compute_cycle_snapshot(cycle: Cycle, day, measure: str) -> dict:
    """口径单源：当日迭代内任务按 state.group 分解 + 各 delta + 恒等式对账。"""
    issues = issues_in_cycle_at(cycle, day)
    state_events: dict = {}
    if day < timezone.localdate():
        for ev in _events([i.id for i in issues], "state", day):
            state_events.setdefault(ev["issue_id"], []).append(ev)
    by_group = {g: 0.0 for g in GROUPS}
    for issue in issues:
        by_group[state_group_at(issue, day, state_events)] += measure_of(issue, measure)
    remaining = sum(by_group[g] for g in REMAINING_GROUPS)
    prev = (CycleSnapshot.objects
            .filter(cycle=cycle, snapshot_date=day - timedelta(days=1))
            .values("remaining_total").first())
    prev_remaining = prev["remaining_total"] if prev else None
    snap = {
        "measure": measure,
        "remaining_by_group": by_group,
        "remaining_total": remaining,
        "scope_total": sum(v for g, v in by_group.items() if g != "cancelled"),
    }
    # 历史/对账路径才有 delta 意义；首日（无 prev）delta 全零
    if prev is None:
        return snap
    day_events = list(
        IssueActivity.objects
        .filter(field="cycles", issue__project_id=cycle.project_id,
                created_at__range=(_start_of_day(day), _end_of_day(day)))
        .filter(Q(new_identifier=cycle.id) | Q(old_identifier=cycle.id))
        .order_by("created_at", "id")
        .values("issue_id", "old_identifier", "new_identifier"))
    completed_delta = scope_delta = cancelled_net = scope_remaining = adjustment = 0.0
    issue_map = {i.id: i for i in issues}
    for ev in day_events:
        issue = issue_map.get(ev["issue_id"]) or Issue.objects.filter(
            id=ev["issue_id"], deleted_at__isnull=True).select_related("state").first()
        if issue is None:
            continue
        amount = measure_of(issue, measure)
        entered = ev["new_identifier"] == cycle.id
        left = ev["old_identifier"] == cycle.id
        if entered:
            scope_delta += amount
            if (issue.state.group if issue.state else "unstarted") in REMAINING_GROUPS:
                scope_remaining += amount
        elif left:
            scope_delta -= amount
            if (issue.state.group if issue.state else "unstarted") in REMAINING_GROUPS:
                scope_remaining -= amount
    # 完成净口径：相邻快照 completed 组差 − scope 折算（避开全量事件回放的复杂度；
    # completed 组差 = 真完成 − 重开流出 + scope 带入/带出 completed 的量）
    prev_snap = CycleSnapshot.objects.filter(
        cycle=cycle, snapshot_date=day - timedelta(days=1)).first()
    prev_group = (prev_snap.remaining_by_group or {}) if prev_snap else {}
    completed_diff = by_group["completed"] - (prev_group.get("completed") or 0)
    cancelled_diff = by_group["cancelled"] - (prev_group.get("cancelled") or 0)
    remaining_diff = remaining - (prev_remaining or 0)
    completed_delta = completed_diff  # 展示口径（BR-09 与 BR-10 分列由 scope_delta 承担）
    cancelled_net = cancelled_diff
    # adjustment 兜底恒等式：remaining = prev − completed − cancelled + scope_remaining + adj
    adjustment = remaining_diff + completed_diff + cancelled_diff - scope_remaining
    snap.update({
        "completed_delta": completed_delta,
        "scope_delta": scope_delta,
        "cancelled_net_delta": cancelled_net,
        "scope_remaining_delta": scope_remaining,
        "adjustment_delta": adjustment,
    })
    return snap


def _start_of_day(day):
    import datetime as _dt
    return timezone.make_aware(_dt.datetime.combine(day, _dt.time(0, 0, 0)))


def backfill_cycle_snapshots(cycle: Cycle, up_to, measure: str) -> int:
    """缺日补跑（§2.3 幂等，UT-10/IT-03）：自 start_date 起逐日 update_or_create。"""
    day, today, n = cycle.start_date, timezone.localdate(), 0
    while day <= min(up_to, today):
        CycleSnapshot.objects.update_or_create(
            cycle=cycle, snapshot_date=day,
            defaults=compute_cycle_snapshot(cycle, day, measure))
        day += timedelta(days=1)
        n += 1
    return n


def on_cycle_completed(cycle: Cycle, carry_over_issues: list[Issue], target_cycle=None) -> None:
    """BR-04/BR-06：终版快照（is_final + scope_events 序列化 + frozen 全量）+ 结转移交。

    事务内顺序（§4.5 complete 端点）：先落 status=completed 触发本函数（快照含
    未完成任务）→ 后执行结转（next）/ 移回（backlog=置空 cycle）。
    """
    config, _ = ProjectReportConfig.objects.get_or_create(
        project_id=cycle.project_id, defaults={"report_measure": "count"})
    measure = config.report_measure
    # 终版快照锚 end_date（与日快照同键则 update_or_create 升级为终版）
    data = compute_cycle_snapshot(cycle, min(cycle.end_date, timezone.localdate()), measure)
    scope_events = list(
        IssueActivity.objects
        .filter(field="cycles", issue__project_id=cycle.project_id,
                created_at__gte=_start_of_day(cycle.start_date))
        .filter(Q(new_identifier=cycle.id) | Q(old_identifier=cycle.id))
        .order_by("created_at", "id")
        .values("issue_id", "created_at", "old_identifier", "new_identifier"))
    issue_names = dict(Issue.objects.filter(
        id__in={e["issue_id"] for e in scope_events}).values_list("id", "name"))
    data["scope_events"] = [
        {"date": e["created_at"].date().isoformat(),
         "direction": "added" if e["new_identifier"] == cycle.id else "removed",
         "issue": issue_names.get(e["issue_id"], str(e["issue_id"])),
         "delta": None}
        for e in scope_events]
    data["frozen"] = True
    data["is_final"] = True
    CycleSnapshot.objects.update_or_create(
        cycle=cycle, snapshot_date=min(cycle.end_date, timezone.localdate()), defaults=data)
    CycleSnapshot.objects.filter(cycle=cycle).update(frozen=True)
    # 冻结后其余快照补齐（缺口日统一 frozen）
    backfill_cycle_snapshots(cycle, cycle.end_date, measure)
    # 结转/移回（快照已含未完成任务——移出事件属于下一迭代 scope）
    Issue.objects.filter(id__in=[i.id for i in carry_over_issues]).update(
        cycle=target_cycle)


# ────────────────────────────────────────────────────────────────
# beat 管道（BR-05/BR-12）
# ────────────────────────────────────────────────────────────────
def cycle_daily_snapshot() -> dict:
    from plane.db.models import Project
    yesterday = timezone.localdate() - timedelta(days=1)
    cycles_done = projects_done = 0
    for project in Project.objects.filter(status="active", deleted_at__isnull=True):
        config, _ = ProjectReportConfig.objects.get_or_create(
            project=project, defaults={"report_measure": "count"})
        measure = config.report_measure
        DailyGroupSnapshot.objects.update_or_create(
            project=project, snapshot_date=yesterday, measure=measure,
            defaults={"counts": compute_group_counts(project.id, yesterday, measure)})
        projects_done += 1
        cycle = project.cycles.filter(status=Cycle.Status.ACTIVE).first()
        if cycle:
            cycles_done += backfill_cycle_snapshots(cycle, yesterday, measure)
    return {"projects": projects_done, "cycle_days": cycles_done}


# ────────────────────────────────────────────────────────────────
# 图表查询（§4.4）——三图全部直查快照表，零 Activity 回放
# ────────────────────────────────────────────────────────────────
@dataclass
class BurndownPayload:
    cycle: dict
    measure: str
    frozen: bool
    ideal: list = field(default_factory=list)
    points: list = field(default_factory=list)
    today: dict | None = None
    scope_events: list = field(default_factory=list)


def _realtime_point(cycle: Cycle, measure: str) -> dict:
    by_group = {g: 0.0 for g in GROUPS}
    for issue in Issue.objects.filter(cycle=cycle, deleted_at__isnull=True).select_related("state"):
        by_group[(issue.state.group if issue.state else "unstarted") or "unstarted"] += measure_of(issue, measure)
    return {
        "date": timezone.localdate().isoformat(),
        "remaining": sum(by_group[g] for g in REMAINING_GROUPS),
        "by_group": by_group,
        "provisional": True,
    }


class AgileReportService:
    def burndown(self, cycle_id) -> BurndownPayload:
        cycle = Cycle.objects.select_related("project").get(pk=cycle_id)
        config, _ = ProjectReportConfig.objects.get_or_create(
            project_id=cycle.project_id, defaults={"report_measure": "count"})
        measure = config.report_measure
        snaps = list(cycle.snapshots.order_by("snapshot_date"))
        points = [{
            "date": s.snapshot_date.isoformat(),
            "remaining": s.remaining_total,
            "completed_delta": s.completed_delta,
            "by_group": s.remaining_by_group,
        } for s in snaps]
        scope_events: list[dict] = []
        if cycle.status == Cycle.Status.ACTIVE and timezone.localdate() >= cycle.start_date:
            points.append(_realtime_point(cycle, measure))
        if cycle.status == Cycle.Status.COMPLETED:
            final = next((s for s in snaps if s.is_final), None)
            scope_events = (final.scope_events if final else []) or []
        else:
            events = list(
                IssueActivity.objects
                .filter(field="cycles", issue__project_id=cycle.project_id,
                        created_at__gte=_start_of_day(cycle.start_date))
                .filter(Q(new_identifier=cycle.id) | Q(old_identifier=cycle.id))
                .order_by("created_at", "id")
                .values("issue_id", "created_at", "new_identifier"))
            names = dict(Issue.objects.filter(
                id__in={e["issue_id"] for e in events}).values_list("id", "name"))
            scope_events = [
                {"date": e["created_at"].date().isoformat(),
                 "direction": "added" if e["new_identifier"] == cycle.id else "removed",
                 "issue": names.get(e["issue_id"], str(e["issue_id"]))}
                for e in events]
            names = dict(Issue.objects.filter(
                id__in={e["issue_id"] for e in scope_events}).values_list("id", "name"))
            for e in scope_events:
                e["issue"] = names.get(e["issue_id"], str(e["issue_id"]))
        # 理想线：首点剩余 → end_date 0（线性）
        first_remaining = points[0]["remaining"] if points else 0.0
        last_date = max(cycle.end_date, timezone.localdate())
        ideal = [{"date": cycle.start_date.isoformat(), "remaining": first_remaining},
                 {"date": last_date.isoformat(), "remaining": 0.0}]
        return BurndownPayload(
            cycle={"id": str(cycle.id), "name": cycle.name,
                   "start_date": cycle.start_date, "end_date": cycle.end_date,
                   "status": cycle.status},
            measure=measure, frozen=cycle.status == Cycle.Status.COMPLETED,
            ideal=ideal, points=points,
            today=points[-1] if points and points[-1].get("provisional") else None,
            scope_events=scope_events)

    def velocity(self, project_id, limit: int = 6) -> dict:
        cycles = (Cycle.objects.filter(project_id=project_id, status="completed")
                  .prefetch_related("snapshots").order_by("-end_date")[:limit])
        bars, warnings = [], []
        completed_values = []
        for c in reversed(cycles):
            first_day = self._first_day_snapshot(c)
            final = next((s for s in c.snapshots.all() if s.is_final), None)
            if getattr(first_day, "_degraded", False):
                warnings.append(f"cycle:{c.pk} first-day snapshot missing, planned degraded to 0")
            completed = ((final.remaining_by_group or {}).get("completed", 0.0)
                         if final else 0.0)
            completed_values.append(completed)
            bars.append({"cycle": c.name, "cycle_id": str(c.pk),
                         "completed": completed,
                         "planned": getattr(first_day, "scope_total", 0.0) or 0.0})
        moving = self._moving_average(completed_values, 5)
        return {"bars": bars, "moving_avg": moving, "warnings": warnings}

    @staticmethod
    def _moving_average(values: list, window: int) -> list:
        out = []
        for i in range(len(values)):
            lo = max(0, i - window + 1)
            seg = values[lo:i + 1]
            out.append(round(sum(seg) / len(seg), 4) if seg else None)
        return out

    def _first_day_snapshot(self, cycle: Cycle) -> CycleSnapshot:
        """首日快照缺行（beat 宕机漏跑）→ 同步补跑后直取；仍缺返回零值降级对象。"""
        snap = next((s for s in cycle.snapshots.all()
                     if s.snapshot_date == cycle.start_date), None)
        if snap is None:
            config, _ = ProjectReportConfig.objects.get_or_create(
                project_id=cycle.project_id, defaults={"report_measure": "count"})
            backfill_cycle_snapshots(cycle, cycle.start_date, config.report_measure)
            snap = cycle.snapshots.filter(snapshot_date=cycle.start_date).first()
        if snap is not None:
            return snap
        degraded = CycleSnapshot(
            cycle=cycle, snapshot_date=cycle.start_date, measure="count",
            remaining_by_group={g: 0 for g in GROUPS},
            remaining_total=0, scope_total=0)
        degraded._degraded = True  # type: ignore[attr-defined]  # velocity 并入 warnings
        return degraded

    def cumulative_flow(self, project_id, frm, to, measure: str) -> dict:
        rows = (DailyGroupSnapshot.objects
                .filter(project_id=project_id, snapshot_date__range=(frm, to),
                        measure=measure)
                .order_by("snapshot_date")
                .values("snapshot_date", "counts"))
        return {"series": [
            {"date": r["snapshot_date"].isoformat(), "counts": r["counts"]} for r in rows]}
