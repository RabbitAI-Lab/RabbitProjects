"""个人维度统计服务（RPT-001 §4.3.1）。

统计卡与「我的待办」列表共用同一 QuerySet 构造器（BR-01），口径单源。
时区按用户本地（BR-04），跨日界任务归属正确（与 TruncDate 同语义）。

P2 演进位：RPT-002 注入同一框架换 project 维度 + 成员分组。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone as dj_timezone

from plane.db.models import Issue, Project, State

#: 统计四卡与列表共用的语义组集合
OPEN_GROUPS = ("unstarted", "started")
COMPLETED_GROUP = "completed"

# 默认时区（BR-04 兜底）
DEFAULT_TZ = "Asia/Shanghai"


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
