"""个人统计与「我的待办」视图（RPT-001 §4.2）。

端点：
  GET  /users/me/issues/stats/?workspace=<slug>[&tz=<IANA>]   四卡 + 7 日趋势
  GET  /users/me/issues/?workspace=<slug>[&state_group=&ordering=&per_page=]
                                                              跨项目任务列表（个人视角）

两个端点都不嵌套 workspace（api-conventions §2.4「可独立存在不嵌套」）；
workspace 经 query 参数限定。
"""
from __future__ import annotations

from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated

from plane.app.serializers.issue import IssueSerializer
from plane.app.serializers.stats import MyIssuesQuerySerializer, StatsQuerySerializer
from plane.app.views._access import get_workspace_or_404
from plane.base.response import success_response
from plane.db.models import Issue
from plane.db.services.stats import PersonalStatsService


def _serialize_issue(issue: Issue) -> dict:
    """复用 sprint-0 序列化器；缺省字段由 IssueSerializer 兜底。"""
    return IssueSerializer(issue).data


# ── 统计卡 ──
class PersonalStatsView(GenericAPIView):
    """GET /users/me/issues/stats/ —— 四计数 + 7 日趋势。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        s = StatsQuerySerializer(data=request.query_params)
        s.is_valid(raise_exception=True)
        slug = s.validated_data["workspace"]
        ws, _ = get_workspace_or_404(slug, request.user)
        data = PersonalStatsService().stats(
            user=request.user, workspace_id=ws.id,
            tz_name=s.validated_data["tz"],
        )
        return success_response(
            data,
            headers={"Cache-Control": "no-store"},
        )


# ── 我的待办列表 ──
class MyIssuesListView(GenericAPIView):
    """GET /users/me/issues/ —— 跨项目聚合（个人视角）；与统计卡共用 Service 基座（BR-01）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        s = MyIssuesQuerySerializer(data=request.query_params)
        s.is_valid(raise_exception=True)
        slug = s.validated_data["workspace"]
        ws, _ = get_workspace_or_404(slug, request.user)
        svc = PersonalStatsService()
        qs = svc.my_issues_queryset(user=request.user, workspace_id=ws.id)
        # state_group 多值过滤（AND 一类）
        state_groups = [g.strip() for g in s.validated_data["state_group"].split(",") if g.strip()]
        if state_groups:
            qs = qs.filter(state__group__in=state_groups)
        # ordering 白名单（防止 SQL 注入式排序注入）
        ordering = s.validated_data["ordering"]
        allowed_orderings = {"target_date", "-target_date", "created_at", "-created_at",
                              "completed_at", "-completed_at", "updated_at", "-updated_at"}
        if ordering not in allowed_orderings:
            ordering = "target_date"
        qs = qs.order_by(ordering, "id")
        per_page = s.validated_data["per_page"]
        items = [_serialize_issue(i) for i in qs[:per_page]]
        total = qs.count()
        return success_response(
            items,
            meta={
                "count": len(items),
                "total_count": total,
                "per_page": per_page,
                "applied": {"state_group": state_groups, "ordering": ordering},
            },
        )


# ─────────────────────────────────────────────────────────────────────
# 项目统计（RPT-002 §4.2——Sprint-5 T5-06）
# ─────────────────────────────────────────────────────────────────────
from django.core.cache import cache  # noqa: E402

from plane.app.views._access import get_project_or_404  # noqa: E402
from plane.base.exception import AppException  # noqa: E402
from plane.db.models.roles import ProjectRole  # noqa: E402
from plane.db.services.stats import (  # noqa: E402
    DEFAULT_TZ,
    MEMBER_ORDER_FIELDS,
    MemberStatsService,
    ProjectStatsService,
    _is_valid_tz,
)

#: 报表聚合限流（BR-13：10 req/min·user；手工实现保 429 信封 + X-RateLimit 头
#: ——DRF throttle_classes 的 Throttled 异常不经信封处理器，见 base/handlers）
_REPORT_RATE_PER_MIN = 10
_TREND_DAYS = (7, 14, 30, 90)


def _report_throttle(user) -> tuple[bool, dict[str, str]]:
    """(放行?, 头)。窗口固定 60s 滑动计数（cache 计数器，dev LocMem/生产 Redis）。"""
    import time

    key = f"rpt-throttle:{user.id}"
    now = time.time()
    window = 60
    hits = [t for t in (cache.get(key) or []) if now - t < window]
    allowed = len(hits) < _REPORT_RATE_PER_MIN
    if allowed:
        hits.append(now)
    cache.set(key, hits, timeout=window)
    remaining = max(0, _REPORT_RATE_PER_MIN - len(hits))
    reset = int(now + window - (hits[0] - now if hits else 0)) if hits else int(now + window)
    return allowed, {
        "X-RateLimit-Limit": str(_REPORT_RATE_PER_MIN),
        "X-RateLimit-Remaining": str(remaining if allowed else 0),
        "X-RateLimit-Reset": str(reset),
    }


class ProjectStatsView(GenericAPIView):
    """GET …/projects/{pid}/stats/ —— 项目进度（五组/完成率/逾期/工时/趋势）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        allowed, rate_headers = _report_throttle(request.user)
        if not allowed:
            raise AppException("RATE_LIMIT_EXCEEDED", message="请求过于频繁，请稍后再试")
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user)
        days = self._days(request)
        tz_name = request.query_params.get("tz") or DEFAULT_TZ
        if not _is_valid_tz(tz_name):
            raise AppException("VALIDATION_INVALID_PARAM", message="tz 必须为合法 IANA 时区")
        archived = request.query_params.get("archived", "false").lower() == "true"
        data = ProjectStatsService().progress(project, days=days, tz_name=tz_name)
        data["project_id"] = str(project.id)
        data["as_of"] = dj_now().isoformat()
        if not archived:  # 基座已默认排除归档——显式参数留档（与 TASK-009 同语义）
            pass
        return success_response(
            data, headers={"Cache-Control": "no-store", **rate_headers})

    @staticmethod
    def _days(request) -> int:
        raw = request.query_params.get("days", "30")
        try:
            days = int(raw)
        except (TypeError, ValueError):
            days = -1
        if days not in _TREND_DAYS:
            raise AppException(
                "VALIDATION_INVALID_PARAM", message="days 必须为 7/14/30/90 之一",
                details=[{"field": "days", "code": "NOT_A_CHOICE",
                          "message": "must be one of: 7, 14, 30, 90"}])
        return days


class ProjectMemberStatsView(GenericAPIView):
    """GET …/projects/{pid}/stats/members/ —— 成员任务量（分组聚合 + 未指派）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        from zoneinfo import ZoneInfo

        allowed, rate_headers = _report_throttle(request.user)
        if not allowed:
            raise AppException("RATE_LIMIT_EXCEEDED", message="请求过于频繁，请稍后再试")
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user)
        roles = self._roles(request)
        order_by = request.query_params.get("order_by", "-open_count")
        if order_by not in MEMBER_ORDER_FIELDS:
            raise AppException(
                "VALIDATION_INVALID_PARAM", message="order_by 非白名单值",
                details=[{"field": "order_by", "code": "NOT_A_CHOICE",
                          "message": f"must be one of: {', '.join(MEMBER_ORDER_FIELDS)}"}])
        tz_name = request.query_params.get("tz") or DEFAULT_TZ
        today = dj_now().astimezone(ZoneInfo(tz_name)).date()
        data = MemberStatsService().members(
            project, roles=roles, order_by=order_by, today=today)
        return success_response(
            data, headers={"Cache-Control": "no-store", **rate_headers},
            meta={"per_page": 50})

    @staticmethod
    def _roles(request) -> list[int] | None:
        raw = request.query_params.get("role")
        if not raw:
            return None
        out: list[int] = []
        for part in raw.split(","):
            member = getattr(ProjectRole, part.strip().upper().removeprefix("PROJ_"), None)
            if member is None:
                raise AppException(
                    "VALIDATION_INVALID_PARAM", message="role 含非法值",
                    details=[{"field": "role", "code": "INVALID",
                              "message": "must be one of: PROJ_ADMIN, PROJ_CONTRIBUTOR, "
                                         "PROJ_COMMENTER, PROJ_VIEWER"}])
            out.append(member.value)
        return out


def dj_now():
    from django.utils import timezone

    return timezone.now()
