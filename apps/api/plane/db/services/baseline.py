"""基线服务（TASK-015 §4.2/§4.3，P4 R3）。

快照填充（BR-03 REPEATABLE READ 同一切面）与对比行构建（§2.3 差异类型
+ 两标志列）。ORM 批量路径替代规格 SNAPSHOT_SQL（语义同构：软删排除、
state_group COALESCE backlog 兜底、CPM 缓存无行视为不在关键链、purged
三态）；对比以 Python 侧全集合并实现（快照行 ≤ 项目规模，单套对比内存
可控；万级项目走分页读取——按 issue_id 批窗口，本轮登记 §7.1 规模注）。
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.db import connection, transaction

logger = logging.getLogger("plane.baseline")

#: 对比行装配的当前侧批窗口（万级项目内存护栏）
_WINDOW = 2000


def _snapshot_rows(project):
    """当前计划切面（快照源字段；软删排除——含归档任务，仅排软删）。"""

    from plane.db.models import Issue, IssueAssignee, IssueCPMCache

    issues = (
        Issue.objects.filter(project=project, deleted_at__isnull=True).select_related("state").order_by("sequence_id")
    )
    assignee_map: dict[str, list[str]] = {}
    for ia in IssueAssignee.objects.filter(issue__project=project).values_list("issue_id", "assignee_id"):
        assignee_map.setdefault(str(ia[0]), []).append(str(ia[1]))
    cpm_map: dict[str, tuple[bool, int | None]] = {}
    for row in IssueCPMCache.objects.filter(issue__project=project).values_list(
        "issue_id", "is_critical", "float_days"
    ):
        cpm_map[str(row[0])] = (bool(row[1]), row[2])
    for issue in issues.iterator(chunk_size=_WINDOW):
        state_group = (issue.state.group if issue.state else None) or "backlog"
        critical, float_days = cpm_map.get(str(issue.id), (False, None))
        yield {
            "issue": issue,
            "sequence_id": issue.sequence_id,
            "name_snapshot": issue.name[:512],
            "start_date": issue.start_date,
            "target_date": issue.target_date,
            "estimate_minutes": issue.estimate_minutes,
            "assignee_ids": sorted(assignee_map.get(str(issue.id), [])),
            "state_group": state_group[:16],
            "is_critical": critical,
            "total_float_days": float_days,
        }


def fill_baseline_sync(baseline_id: str) -> int:
    """同步填充（BR-03：REPEATABLE READ 同一切面；首语句设隔离级）。"""
    from plane.db.models import Baseline, BaselineItem

    baseline = Baseline.objects.select_related("project").get(pk=baseline_id)
    # BR-03 REPEATABLE READ：仅顶层请求事务可设隔离级（嵌套保存点/测试事务
    # 内 SET 会毒化事务——aborted）；嵌套场景降级默认隔离，快照同一切面
    # 仍由单一 atomic 事务承载
    can_set_isolation = not connection.in_atomic_block
    with transaction.atomic():
        if can_set_isolation:
            with connection.cursor() as cur:
                cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        created = 0
        batch = []
        for row in _snapshot_rows(baseline.project):
            issue = row.pop("issue")
            batch.append(BaselineItem(baseline=baseline, issue=issue, **row))
            created += 1
            if len(batch) >= _WINDOW:
                BaselineItem.objects.bulk_create(batch)
                batch = []
        if batch:
            BaselineItem.objects.bulk_create(batch)
        Baseline.objects.filter(pk=baseline.pk).update(
            status=Baseline.Status.READY, issue_count=created, stats_cache=_quick_stats(baseline)
        )
    return created


@shared_task(bind=True, max_retries=1)
def fill_baseline_async(self, baseline_id: str):
    """大项目异步填充（BR-11）；失败置 failed（不占 BR-02 配额）。"""
    from plane.db.models import Baseline

    try:
        fill_baseline_sync(baseline_id)
    except Exception:  # noqa: BLE001 —— 台账化失败
        Baseline.objects.filter(pk=baseline_id).update(status=Baseline.Status.FAILED)
        logger.exception("baseline.fill_failed id=%s", baseline_id)
        raise


def _quick_stats(baseline) -> dict:
    return {"generated_at": str(baseline.created_at)}


# ── 对比（§2.3/§4.3）────────────────────────────────────────────────


def build_compare_rows(baseline) -> list[dict]:
    """快照集 ∪ 当前集 全集对比（BR-05 行不灭失）。

    差异类型：on_track / delayed / added / deleted / unscheduled；
    标志列：assignee_changed / drifted（新增删除行恒 False）。
    purged 三态：issue 行缺失（物理删除兜底，BR-04）→ deleted + purged。
    """
    from plane.db.models import Issue, IssueAssignee, IssueCPMCache

    project = baseline.project
    snap = {}
    for it in baseline.items.all():
        snap[str(it.issue_id) if it.issue_id else f"purged:{it.sequence_id}"] = it
    current = {}
    for issue in (
        Issue.objects.filter(project=project, deleted_at__isnull=True)
        .select_related("state")
        .iterator(chunk_size=_WINDOW)
    ):
        current[str(issue.id)] = issue
    cur_ids = list(current)
    cur_assignees: dict[str, list[str]] = {}
    if cur_ids:
        for ia in IssueAssignee.objects.filter(issue_id__in=cur_ids).values_list("issue_id", "assignee_id"):
            cur_assignees.setdefault(str(ia[0]), []).append(str(ia[1]))
    cur_critical = (
        set(
            str(r[0])
            for r in (IssueCPMCache.objects.filter(issue_id__in=cur_ids, is_critical=True).values_list("issue_id"))
        )
        if cur_ids
        else set()
    )

    rows: list[dict] = []
    for key, it in snap.items():
        issue = current.get(key)
        if issue is None:
            rows.append(
                {
                    "issue_id": key if not key.startswith("purged:") else None,
                    "sequence_id": it.sequence_id,
                    "name": it.name_snapshot,
                    "diff_type": "deleted",
                    "purged": key.startswith("purged:"),
                    "base_start_date": it.start_date,
                    "base_target_date": it.target_date,
                    "variance_days": None,
                    "assignee_changed": False,
                    "drifted": False,
                    "is_critical": it.is_critical,
                    "assignees": it.assignee_ids,
                    "state_group": it.state_group,
                }
            )
            continue
        cur_target = issue.target_date
        base_target = it.target_date
        if cur_target is None or base_target is None:
            diff = "unscheduled"
            variance = None
        else:
            variance = (cur_target - base_target).days
            diff = "delayed" if variance > 0 else "on_track"
        cur_assignee_set = sorted(cur_assignees.get(key, []))
        rows.append(
            {
                "issue_id": key,
                "sequence_id": it.sequence_id,
                "name": issue.name,
                "diff_type": diff,
                "purged": False,
                "start_date": issue.start_date,
                "target_date": cur_target,
                "base_start_date": it.start_date,
                "base_target_date": base_target,
                "variance_days": variance,
                "assignee_changed": cur_assignee_set != sorted(it.assignee_ids),
                "drifted": it.is_critical != (key in cur_critical),
                "is_critical": key in cur_critical,
                "assignees": cur_assignee_set,
                "state_group": (issue.state.group if issue.state else "backlog"),
            }
        )
    snap_ids = {k for k in snap if not k.startswith("purged:")}
    for key, issue in current.items():
        if key in snap_ids:
            continue
        rows.append(
            {
                "issue_id": key,
                "sequence_id": issue.sequence_id,
                "name": issue.name,
                "diff_type": "added",
                "purged": False,
                "start_date": issue.start_date,
                "target_date": issue.target_date,
                "base_target_date": None,
                "variance_days": None,
                "assignee_changed": False,
                "drifted": False,
                "is_critical": key in cur_critical,
                "assignees": cur_assignees.get(key, []),
                "state_group": (issue.state.group if issue.state else "backlog"),
            }
        )
    rows.sort(key=lambda r: r["sequence_id"] or 0)
    return rows


def compute_stats(baseline) -> dict:
    """偏差统计（§2.4：延期率/平均延期/蔓延率/按人前 10/趋势位）。"""
    rows = build_compare_rows(baseline)
    dated = [r for r in rows if r["diff_type"] in ("delayed", "on_track")]
    delayed = [r for r in dated if r["diff_type"] == "delayed"]
    added = [r for r in rows if r["diff_type"] == "added"]
    deleted = [r for r in rows if r["diff_type"] == "deleted"]
    per_assignee: dict[str, int] = {}
    for r in delayed:
        for a in r.get("assignees") or ["(未分配)"]:
            per_assignee[a] = per_assignee.get(a, 0) + 1
    variances = sorted(r["variance_days"] for r in delayed) or [0]

    def pct(p):
        idx = min(int(len(variances) * p) - 1, len(variances) - 1)
        return variances[max(idx, 0)]

    return {
        "baseline_id": str(baseline.id),
        "issue_count": baseline.issue_count,
        "dated_count": len(dated),
        "delayed_count": len(delayed),
        "delay_rate": round(len(delayed) / len(dated), 4) if dated else None,
        "avg_delay_days": round(sum(r["variance_days"] for r in delayed) / len(delayed), 2) if delayed else 0,
        "p50_delay_days": pct(0.5),
        "p90_delay_days": pct(0.9),
        "scope_creep_rate": round(len(added) / baseline.issue_count, 4) if baseline.issue_count else None,
        "added_count": len(added),
        "deleted_count": len(deleted),
        "drifted_count": sum(1 for r in rows if r["drifted"]),
        "by_assignee_top10": sorted(per_assignee.items(), key=lambda kv: -kv[1])[:10],
        "computed_at": str(timezone_now_iso()),
    }


def timezone_now_iso() -> str:
    from django.utils import timezone

    return timezone.now().isoformat()
