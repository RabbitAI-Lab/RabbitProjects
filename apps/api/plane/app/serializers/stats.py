"""个人统计 / 我的待办序列化器（RPT-001 §4.2）。

- StatsQuerySerializer：stats 端点的查询参数（workspace 必填 + tz 可选）
- MyIssuesQuerySerializer：我的待办列表的查询参数（workspace + 可选分页 + 状态组筛选）
"""
from __future__ import annotations

from rest_framework import serializers

from plane.db.services.stats import DEFAULT_TZ, _is_valid_tz

ALLOWED_STATE_GROUPS = ("backlog", "unstarted", "started", "completed", "cancelled")
MAX_PER_PAGE = 100


class StatsQuerySerializer(serializers.Serializer):
    """GET /users/me/issues/stats/ —— workspace 必填 + tz 可选（默认 Asia/Shanghai）。"""

    workspace = serializers.SlugField(max_length=48)
    tz = serializers.CharField(required=False, default=DEFAULT_TZ, max_length=64)

    def validate_tz(self, value: str) -> str:
        if not _is_valid_tz(value):
            raise serializers.ValidationError("tz 须为合法 IANA 时区名，如 Asia/Shanghai")
        return value


class MyIssuesQuerySerializer(serializers.Serializer):
    """GET /users/me/issues/ —— workspace + state_group + 分页。"""

    workspace = serializers.SlugField(max_length=48)
    state_group = serializers.CharField(required=False, default="unstarted,started",
                                         max_length=120)
    ordering = serializers.CharField(required=False, default="target_date", max_length=64)
    per_page = serializers.IntegerField(required=False, default=50, min_value=1,
                                          max_value=MAX_PER_PAGE)
    cursor = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_state_group(self, value: str) -> str:
        parts = [v.strip() for v in value.split(",") if v.strip()]
        bad = [p for p in parts if p not in ALLOWED_STATE_GROUPS]
        if bad:
            raise serializers.ValidationError(
                f"state_group 非法值 {bad}；合法值 {list(ALLOWED_STATE_GROUPS)}"
            )
        return ",".join(parts) if parts else "unstarted,started"
