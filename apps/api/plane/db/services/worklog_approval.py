"""工时审批服务（TASK-013 §4.3）——轻量单级状态机 + 锁定 + 快照。

- submit/approve/reject/revoke：四态机（ALLOWED_TRANSITIONS 驱动，§4.2）
- refresh_summary(freeze=…)：单事务内重算 WorkLogSummary（BR-13：is_frozen
  拒绝日常增量重算，freeze=True 穿透守卫）
- 锁定 BR-06：approve 时 work_logs.locked=true；reject/revoke 解锁
- BR-08 硬上限：单日单人累计 ≤1440，跨行用 SELECT FOR UPDATE 锁当日行后聚合
  校验；软上限超 480 仅 meta.warnings（不拦截）；粒度不符 NOT_A_CHOICE
- BR-04 不可自审 + 审批人 = PROJ_ADMIN+（worklog.approve 注册表专行）
- BR-12 5 字段按周计算 week_start（服务器时区，BR-01 降级：全系统单一时区）
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from plane.db.models import (
    ProjectWorklogConfig,
    User,
    WorkLog,
    WorkLogApproval,
    WorkLogSummary,
)

logger = logging.getLogger(__name__)


def _week_start(d: date) -> date:
    """BR-01：周一 00:00（服务器时区）——全系统单一时区（§4.4 降级口径）。"""
    return d - timedelta(days=d.weekday())


def _week_range(week_start: date) -> tuple[date, date]:
    return week_start, week_start + timedelta(days=6)


class WorkLogApprovalError(Exception):
    def __init__(self, code: str, status: int, sub: str | None = None, message: str = ""):
        self.code, self.status, self.sub, self.message = code, status, sub, message
        super().__init__(message)


def get_or_create_config(project) -> ProjectWorklogConfig:
    s, _ = ProjectWorklogConfig.objects.get_or_create(project=project)
    return s


def _check_daily_hard_limit(actor_id, project_id, worked_on, minutes_to_add):
    """BR-08 硬上限：单日单人累计 ≤ 1440。跨行用 SELECT FOR UPDATE 锁当日未锁行后聚合。"""
    with transaction.atomic():
        locked_ids = list(
            WorkLog.objects.select_for_update().filter(
                actor_id=actor_id, issue__project_id=project_id, worked_on=worked_on,
                locked=False, deleted_at__isnull=True
            ).values_list("id", flat=True)
        )
        s = WorkLog.objects.filter(
            actor_id=actor_id, issue__project_id=project_id, worked_on=worked_on,
            deleted_at__isnull=True
        ).aggregate(total=Sum("minutes"))["total"] or 0
        if s + minutes_to_add > 1440:
            raise WorkLogApprovalError(
                "VALIDATION_ERROR", 400, sub="TOO_LARGE",
                message=f"单日累计 {s + minutes_to_add} 分钟超过硬上限 1440") from None


def _check_granularity(minutes: int, config: ProjectWorklogConfig) -> None:
    if minutes % config.granularity_minutes != 0:
        raise WorkLogApprovalError(
            "VALIDATION_ERROR", 400, sub="NOT_A_CHOICE",
            message=f"单笔粒度须为 {config.granularity_minutes} 分钟倍数")  # noqa: B904 -- 顶层校验非 except 链


def soft_limit_warning(actor_id, project_id, week_start, daily_soft_limit):
    """BR-08 软上限：超 daily_soft_limit_minutes 触发 warning（不拦截）。"""
    ws, _ = _week_range(week_start)
    qs = WorkLog.objects.filter(
        actor_id=actor_id, issue__project_id=project_id, worked_on__gte=ws,
        worked_on__lte=ws + timedelta(days=6), deleted_at__isnull=True
    ).values("worked_on").annotate(total=Sum("minutes")).order_by("worked_on")
    warnings: list[dict] = []
    for row in qs:
        if row["total"] > daily_soft_limit:
            warnings.append({
                "worked_on": str(row["worked_on"]),
                "total_minutes": row["total"],
                "soft_limit_minutes": daily_soft_limit,
            })
    return warnings


# ── 提交/审批/驳回/撤销（§4.3）─────────────────────────────────

@transaction.atomic
def submit(*, actor, project, week_start) -> WorkLogApproval:
    config = get_or_create_config(project)
    if not config.approval_enabled:
        raise WorkLogApprovalError("VALIDATION_ERROR", 400, message="项目未启用工时审批")
    ws, we = _week_range(week_start)
    if not WorkLog.objects.filter(
            issue__project=project, actor=actor, deleted_at__isnull=True,
            worked_on__gte=ws, worked_on__lte=we
    ).exists():
        raise WorkLogApprovalError("VALIDATION_ERROR", 400, sub="REQUIRED",
                                    message="本周无可提交工时")
    batch, created = WorkLogApproval.objects.get_or_create(
        actor=actor, project=project, week_start=ws,
        defaults={"status": WorkLogApproval.Status.DRAFT},
    )
    if batch.status == WorkLogApproval.Status.SUBMITTED:
        return batch  # 幂等
    if batch.status not in (WorkLogApproval.Status.DRAFT,
                            WorkLogApproval.Status.REJECTED):
        raise WorkLogApprovalError("RESOURCE_STATE_INVALID", 409,
                                    message=f"当前状态 {batch.status} 不可提交")
    _move(batch, WorkLogApproval.Status.SUBMITTED, by=actor, note="", review_action=False)
    return batch


@transaction.atomic
def review(*, batch_id, reviewer, action: str, note: str = "") -> WorkLogApproval:
    batch = WorkLogApproval.objects.select_for_update().get(pk=batch_id)
    if batch.actor_id == reviewer.id:  # BR-04 不可自审
        raise WorkLogApprovalError("VALIDATION_ERROR", 400,
                                    message="不可审批自己的批次")
    if action in ("reject", "revoke") and not note.strip():
        raise WorkLogApprovalError("VALIDATION_ERROR", 400, sub="REQUIRED",
                                    message="驳回/撤销必填意见")
    target = {"approve": WorkLogApproval.Status.APPROVED,
              "reject": WorkLogApproval.Status.REJECTED,
              "revoke": WorkLogApproval.Status.REJECTED}[action]
    _move(batch, target, by=reviewer, note=note, review_action=True)
    if action == "approve":
        # 锁定覆盖周工时（BR-06）
        WorkLog.objects.filter(
            issue__project=batch.project, actor=batch.actor,
            worked_on__gte=_week_range(batch.week_start)[0],
            worked_on__lte=_week_range(batch.week_start)[1],
            deleted_at__isnull=True
        ).update(locked=True)
        # 冻结快照（is_frozen=True）
        refresh_summary(batch, freeze=True)
    elif action == "revoke":  # BR-07：撤销审批 → 解锁 + 解冻
        WorkLog.objects.filter(
            issue__project=batch.project, actor=batch.actor,
            worked_on__gte=_week_range(batch.week_start)[0],
            worked_on__lte=_week_range(batch.week_start)[1],
            deleted_at__isnull=True
        ).update(locked=False)
        # status 已变 REJECTED → refresh_summary(freeze=False) 走覆写分支
        batch.refresh_from_db()
        refresh_summary(batch, freeze=False)
    return batch


def _move(batch: WorkLogApproval, target: str, *, by: User, note: str,
          review_action: bool) -> None:
    allowed = WorkLogApproval.ALLOWED_TRANSITIONS.get(batch.status, set())
    if target not in allowed:
        raise WorkLogApprovalError("RESOURCE_STATE_INVALID", 409,
                                    message=f"{batch.status} → {target} 不在合法流转集")
    batch.status = target
    if review_action:
        batch.reviewer = by
        batch.reviewed_at = timezone.now()
    if note:
        batch.review_note = note[:500]
    if target == WorkLogApproval.Status.SUBMITTED:
        batch.submitted_at = timezone.now()
    batch.updated_by = by
    batch.save()


# ── 快照聚合（BR-13：只增改不删，is_frozen 守卫，freeze=True/False 穿透）───

def refresh_summary(batch: WorkLogApproval, *, freeze: bool) -> WorkLogSummary:
    """从工时明细重算 summary（台账与 RPT-004 负载唯一源）。
    freeze=True 穿透守卫：审批通过时落 is_frozen=True（拒绝日常增量）。"""
    ws, we = _week_range(batch.week_start)
    qs = WorkLog.objects.filter(
        issue__project=batch.project, actor=batch.actor,
        worked_on__gte=ws, worked_on__lte=we, deleted_at__isnull=True,
    )
    agg = qs.aggregate(total=Sum("minutes"))
    over_8h = qs.values("worked_on").annotate(daily=Sum("minutes")).filter(
        daily__gt=480).count()
    if not freeze:
        # 日常增量：is_frozen=True 行守卫（BR-13）——解锁路径（撤销审批）在
        # review(revoke) 显式 set(locked=False) 落库后调用本函数，**强制**覆写
        # 守卫：撤销的本质是「把冻结行回退到非冻结可改」的状态，必须覆写。
        if batch.status != WorkLogApproval.Status.REJECTED:
            if WorkLogSummary.objects.filter(
                project=batch.project, actor=batch.actor, week_start=ws, is_frozen=True
            ).exists():
                logger.info("worklog.summary.skip_frozen project=%s actor=%s",
                            batch.project_id, batch.actor_id)
                return WorkLogSummary.objects.get(
                    project=batch.project, actor=batch.actor, week_start=ws)
    # BR-07 撤销审批：batch 状态变 REJECTED + is_frozen=False（强制覆写守卫）
    summary, _ = WorkLogSummary.objects.update_or_create(
        project=batch.project, actor=batch.actor, week_start=ws,
        defaults={
            "total_minutes": agg["total"] or 0,
            "task_count": qs.values("issue_id").distinct().count(),
            "approved_minutes": agg["total"] or 0 if freeze else 0,
            "over_8h_days": over_8h,
            "is_frozen": freeze,
        })
    return summary

