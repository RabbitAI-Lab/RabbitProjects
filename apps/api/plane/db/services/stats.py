"""统计服务（RPT-001 个人维度 + RPT-002 项目维度，§4.3.1 公共口径基座）。

统计卡与「我的待办」列表共用同一 QuerySet 构造器（BR-01），口径单源；
RPT-002 的项目进度/成员任务量从同一基座（issue_stats_base/overdue_q/
open_q）派生——禁第二份口径表达式（BR-01 红线延续）。
时区按用户本地（BR-04），跨日界任务归属正确（与 TruncDate 同语义）。

实现偏差：规格 §4.3.1 落位 plane/analytics/services/project.py——实现并
入本模块（RPT-001 已在此，双端点共用基座免跨包 import；随 Sprint-5 ADR 登记）。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Count, Q, QuerySet, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone as dj_timezone

from plane.db.models import Issue, Project, ProjectMember, State, WorkLog

#: 统计四卡与列表共用的语义组集合
OPEN_GROUPS = ("unstarted", "started")
COMPLETED_GROUP = "completed"
#: 五组全集（state_distribution 键序，RPT-002 §4.2.1）
STATE_GROUPS = ("backlog", "unstarted", "started", "completed", "cancelled")

# 默认时区（BR-04 兜底）
DEFAULT_TZ = "Asia/Shanghai"


# ─────────────────────────────────────────────────────────────────────
# 公共口径基座（RPT-002 §4.3.1——RPT-001/002 共用，口径单源红线落点）
# ─────────────────────────────────────────────────────────────────────
def issue_stats_base(*, project_id=None, workspace_id=None, archived: bool = False) -> QuerySet:
    """公共基座：``archived`` 与 TASK-009 列表归档参数同语义。"""
    qs = Issue.objects.all()
    if project_id:
        qs = qs.filter(project_id=project_id)
    if workspace_id:
        qs = qs.filter(project__workspace_id=workspace_id)
    if not archived:
        qs = qs.filter(archived_at__isnull=True)
    return qs.select_related("state")


def overdue_q(today: date) -> Q:
    return Q(target_date__lt=today) & ~Q(state__group__in=["completed", "cancelled"])


def open_q() -> Q:
    return Q(state__group__in=list(OPEN_GROUPS))


def _is_valid_tz(tz_name: str) -> bool:
    try:
        ZoneInfo(tz_name)
        return True
    except Exception:                                     # noqa: BLE001
        return False


class PersonalStatsService:
    """个人维度统计 —— 统计卡与「我的待办」列表的唯一口径来源（BR-01）。"""

    # ── 基座：统计与列表共用的 QuerySet 构造器 ──
    def my_issues_queryset(self, *, user, workspace_id: uuid.UUID):
        """我的可见任务基座 —— 统计与列表都从这里出发（口径单源）。"""
        return (
            Issue.objects
            .filter(
                issue_assignees__assignee=user,           # idx_assignee_issue 反查
                project__workspace_id=workspace_id,
                archived_at__isnull=True,
                deleted_at__isnull=True,
                # Project 无 archived_at 列；以 status 枚举记归档态（active/archived/closed）
                project__status=Project.Status.ACTIVE,
            )
            .distinct()                                    # M2M join 去重
        )

    # ── 统计 ──
    def stats(self, *, user, workspace_id: uuid.UUID, tz_name: str) -> dict:
        tz = ZoneInfo(tz_name)
        base = self.my_issues_queryset(user=user, workspace_id=workspace_id)
        now_local = datetime.now(tz)
        today = now_local.date()
        monday_local = datetime.combine(
            today - timedelta(days=today.weekday()),
            datetime.min.time(),
            tzinfo=tz,
        )
        monday_utc = monday_local.astimezone(ZoneInfo("UTC"))

        # open + completed 一次取（BR-06 SQL 预算）
        state_pairs = list(
            State.objects
            .filter(group__in=(*OPEN_GROUPS, COMPLETED_GROUP))
            .values_list("group", "id")
        )
        open_state_ids = [sid for g, sid in state_pairs if g in OPEN_GROUPS]
        completed_state_ids = [sid for g, sid in state_pairs if g == COMPLETED_GROUP]

        # 单条 aggregate 取四计数
        counts = base.aggregate(
            todo_count=Count("id", filter=Q(state_id__in=open_state_ids)),
            due_today_count=Count(
                "id",
                filter=Q(state_id__in=open_state_ids, target_date=today),
            ),
            overdue_count=Count(
                "id",
                filter=Q(state_id__in=open_state_ids, target_date__lt=today),
            ),
            completed_this_week_count=Count(
                "id",
                filter=Q(
                    state_id__in=completed_state_ids,
                    completed_at__gte=monday_utc,
                ),
            ),
        )

        # 7 日趋势：按用户本地日历切日（TruncDate tzinfo=tz）
        seven_days_ago_utc = (now_local.astimezone(ZoneInfo("UTC")) - timedelta(days=7))
        trend_rows = (
            base.filter(
                completed_at__gte=seven_days_ago_utc,
                state__group=COMPLETED_GROUP,             # 防御过滤：trend 仅含 completed
            )
            .annotate(day=TruncDate("completed_at", tzinfo=tz))
            .values("day")
            .annotate(count=Count("id"))
            .order_by("day")
        )
        return {
            **{k: counts.get(k) or 0 for k in (
                "todo_count",
                "due_today_count",
                "overdue_count",
                "completed_this_week_count",
            )},
            "trend": self._pad_trend(trend_rows, today),
            "generated_at": dj_timezone.now().isoformat(),
        }

    @staticmethod
    def _pad_trend(rows, today: date) -> list[dict]:
        """补零 —— 恒 7 点，无完成日填 0（BR-10）。"""
        by_day = {r["day"]: r["count"] for r in rows}
        return [
            {
                "date": (today - timedelta(days=offset)).isoformat(),
                "count": by_day.get(today - timedelta(days=offset), 0),
            }
            for offset in range(6, -1, -1)
        ]


# ─────────────────────────────────────────────────────────────────────
# 项目进度（RPT-002 §4.3.2）
# ─────────────────────────────────────────────────────────────────────
class ProjectStatsService:
    """项目维度统计——progress（五组分布/完成率/逾期/工时/趋势）。"""

    def progress(self, project: Project, *, days: int, tz_name: str) -> dict:
        tz = ZoneInfo(tz_name)
        today = dj_timezone.now().astimezone(tz).date()
        base = issue_stats_base(project_id=project.id)
        row = base.aggregate(                                   # SQL #1：五组 + 逾期 + 工时面
            **{g: Count("id", filter=Q(state__group=g)) for g in STATE_GROUPS},
            overdue=Count("id", filter=overdue_q(today)),
            est=Sum("estimate_minutes", filter=open_q()),
            unestimated=Count("id", filter=open_q() & Q(estimate_minutes__isnull=True)),
        )
        logged = WorkLog.objects.filter(                        # SQL #2：工时登记
            issue__project=project, issue__archived_at__isnull=True,
        ).aggregate(s=Sum("minutes"))["s"] or 0
        since = today - timedelta(days=days - 1)
        since_utc = datetime.combine(since, datetime.min.time(), tzinfo=tz).astimezone(
            ZoneInfo("UTC"))
        trend_created = self._daily(base, "created_at", since_utc, tz)      # SQL #3
        trend_completed = self._daily(
            base.filter(completed_at__isnull=False), "completed_at", since_utc, tz)  # SQL #4
        row["total"] = sum(row[g] for g in STATE_GROUPS)
        denom = row["total"] - row["cancelled"]
        est = row["est"] or 0
        return {
            "state_distribution": {g: row[g] for g in STATE_GROUPS},
            "total": row["total"],
            "completion_rate": round(row["completed"] / denom, 4) if denom else None,
            "overdue_count": row["overdue"],
            "worklog_summary": {
                "estimate_minutes": est, "logged_minutes": logged,
                "remaining_minutes": max(0, est - logged),
                "overrun_minutes": max(0, logged - est),
                "unestimated_count": row["unestimated"],
            },
            "trend": {"days": days, "created": trend_created,
                      "completed": trend_completed},
        }

    @staticmethod
    def _daily(base: QuerySet, field: str, since_utc, tz) -> list[dict]:
        rows = (base.filter(**{f"{field}__gte": since_utc})
                .annotate(day=TruncDate(field, tzinfo=tz))
                .values("day").annotate(count=Count("id")).order_by("day"))
        by_day = {r["day"]: r["count"] for r in rows}
        today = dj_timezone.now().astimezone(tz).date()
        since = since_utc.astimezone(tz).date()
        out, d = [], since
        while d <= today:
            out.append({"date": d.isoformat(), "count": by_day.get(d, 0)})
            d += timedelta(days=1)
        return out


# ─────────────────────────────────────────────────────────────────────
# 成员任务量（RPT-002 §4.3.4）
# ─────────────────────────────────────────────────────────────────────
#: order_by 白名单（§4.2.2——非白名单 400）
MEMBER_ORDER_FIELDS = (
    "-open_count", "open_count", "-done_count_30d", "done_count_30d",
    "-overdue_count", "overdue_count", "-logged_minutes_30d", "logged_minutes_30d",
)
MEMBER_PAGE_SIZE = 50
_DONE_WINDOW_DAYS = 30


class MemberStatsService:
    """成员维度统计——单条 GROUP BY + 未指派/合计行。"""

    def members(self, project: Project, *, roles: list[int] | None,
                order_by: str, today: date) -> dict:
        members = ProjectMember.objects.filter(
            project=project, is_active=True, deleted_at__isnull=True,
        ).select_related("member")
        if roles:
            members = members.filter(role__in=roles)
        member_rows = list(members)
        member_ids = [m.member_id for m in member_rows]
        since_30d = today - timedelta(days=_DONE_WINDOW_DAYS - 1)

        base = issue_stats_base(project_id=project.id)
        agg = (base.filter(assignees__in=member_ids)
               .values("assignees")
               .annotate(
                   open_count=Count("id", filter=open_q(), distinct=True),
                   done_count=Count(
                       "id", filter=Q(state__group=COMPLETED_GROUP,
                                      completed_at__gte=since_30d), distinct=True),
                   overdue_count=Count("id", filter=overdue_q(today), distinct=True),
                   est_open=Sum("estimate_minutes", filter=open_q()),
               ))
        by_member = {r["assignees"]: r for r in agg}
        logged = {
            r["actor_id"]: r["s"]
            for r in WorkLog.objects.filter(
                issue__project=project, issue__archived_at__isnull=True,
                worked_on__gte=since_30d, actor_id__in=member_ids,
            ).values("actor_id").annotate(s=Sum("minutes"))
        }

        def _row(m: ProjectMember) -> dict:
            r = by_member.get(m.member_id, {})
            return {
                "member_id": str(m.member_id),
                "display_name": m.member.display_name,
                "avatar_url": m.member.avatar_url or None,
                "role": m.role,
                "is_active": m.member.is_active,   # 灰标口径（AUTH-006 §3.3）
                "open_count": r.get("open_count", 0),
                "done_count_30d": r.get("done_count", 0),
                "overdue_count": r.get("overdue_count", 0),
                "estimate_minutes_open": r.get("est_open") or 0,
                "logged_minutes_30d": logged.get(m.member_id) or 0,
            }

        rows = [_row(m) for m in member_rows]
        key = order_by.lstrip("-")
        rows.sort(key=lambda r: r[key], reverse=order_by.startswith("-"))

        unassigned = base.filter(assignees__isnull=True).aggregate(
            open_count=Count("id", filter=open_q()),
            overdue_count=Count("id", filter=overdue_q(today)),
            est_open=Sum("estimate_minutes", filter=open_q()))
        assigned_all = base.filter(assignees__isnull=False).aggregate(
            open_count=Count("id", filter=open_q(), distinct=True),
            done=Count("id", filter=Q(state__group=COMPLETED_GROUP,
                                       completed_at__gte=since_30d), distinct=True),
            overdue_count=Count("id", filter=overdue_q(today), distinct=True),
            est_open=Sum("estimate_minutes", filter=open_q()))
        logged_total = WorkLog.objects.filter(
            issue__project=project, issue__archived_at__isnull=True,
            worked_on__gte=since_30d).aggregate(s=Sum("minutes"))["s"] or 0
        totals = {
            "open_count": (unassigned["open_count"] or 0) + (assigned_all["open_count"] or 0),
            "done_count_30d": assigned_all["done"] or 0,
            "overdue_count": (unassigned["overdue_count"] or 0) + (assigned_all["overdue_count"] or 0),
            "estimate_minutes_open": (unassigned["est_open"] or 0) + (assigned_all["est_open"] or 0),
            "logged_minutes_30d": logged_total,
        }
        return {"rows": rows, "unassigned": {
            "open_count": unassigned["open_count"] or 0,
            "overdue_count": unassigned["overdue_count"] or 0,
            "estimate_minutes_open": unassigned["est_open"] or 0,
        }, "totals": totals}
