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
