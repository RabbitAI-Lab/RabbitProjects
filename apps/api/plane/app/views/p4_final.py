"""R16~R18 交付端点（P4 收口轮）。

BOARD-006 全局看板（四状态组泳道聚合）、BOARD-007 视图模板（发布/应用
快照拷贝）、WF-007 超时流转（规则 CRUD + beat 扫描——引擎守卫链复用）、
PROJ-005 资源调度建议（超载/闲置/平衡对）、COLLAB-005 推送策略
（用户偏好 + 聚合窗口路由器）。
"""

from __future__ import annotations

import hashlib
import logging
import time as _time
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Count, Sum
from django.utils import timezone
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticatedAndActive, require_role
from plane.app.views._access import get_project_or_404, get_workspace_or_404
from plane.audit.recorder import record
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.db.models import (
    Issue,
    IssueView,
    Project,
    State,
    WorkLogSummary,
    WorkspaceMember,
    WorkspaceRole,
)

logger = logging.getLogger("plane.api.p4")

_STATE_GROUPS = ("unstarted", "started", "completed", "cancelled")


# ── BOARD-006 全局看板 ─────────────────────────────────────────────


class GlobalBoardView(APIView):
    """GET .../global-board/ —— 用户可见项目四泳道聚合（BR-01/02）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, slug):
        ws = get_workspace_or_404(slug, request.user)[0]
        projects = Project.objects.filter(workspace=ws, deleted_at__isnull=True)
        visible = set(projects.accessible_by(request.user, workspace_id=ws.id).values_list("id", flat=True))
        issues = Issue.objects.filter(
            project_id__in=visible, deleted_at__isnull=True, state__group__in=_STATE_GROUPS
        ).select_related("state", "project")
        lanes = {g: {"key": g, "count": 0, "sample": []} for g in _STATE_GROUPS}
        for issue in issues.order_by("-updated_at")[:500]:
            group = issue.state.group
            lane = lanes[group]
            lane["count"] += 1
            if len(lane["sample"]) < 3:
                lane["sample"].append({"issue_id": str(issue.id), "name": issue.name, "project": issue.project.name})
        # 全量计数（sample 截断的补齐）
        for group, cnt in issues.values_list("state__group").annotate(c=Count("id")):
            if group in lanes:
                lanes[group]["count"] = cnt
        return success_response({"lanes": list(lanes.values()), "visible_projects": len(visible)})


# ── BOARD-007 视图模板 ─────────────────────────────────────────────


def _template_cache_key(ws_id) -> str:
    return f"viewtpl:{ws_id}"


class ViewTemplateView(APIView):
    """GET/POST .../view-templates/ —— 发布（WS_ADMIN）；GET 成员可读。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, slug):
        ws = get_workspace_or_404(slug, request.user)[0]
        try:
            templates = cache.get(_template_cache_key(ws.id)) or []
        except Exception:  # noqa: BLE001
            templates = []
        return success_response(templates)

    def post(self, request, slug):
        ws = get_workspace_or_404(slug, request.user)[0]
        require_role(request, self, WorkspaceRole.ADMIN)
        p = request.data or {}
        name = str(p.get("name") or "").strip()
        if not name:
            raise AppException("VALIDATION_ERROR", message="name 必填", details=[{"field": "name", "code": "REQUIRED"}])
        try:
            templates = cache.get(_template_cache_key(ws.id)) or []
        except Exception:  # noqa: BLE001
            templates = []
        existing = next((t for t in templates if t["name"] == name), None)
        version = (existing["version"] + 1) if existing else 1
        template = {
            "id": existing["id"] if existing else hashlib.sha256(name.encode()).hexdigest()[:16],
            "name": name,
            "filter_json": p.get("filter_json") or {},
            "columns_json": p.get("columns_json") or [],
            "version": version,  # BR-01 版本快照
            "published_by": str(request.user.id),
            "published_at": timezone.now().isoformat(),
        }
        templates = [t for t in templates if t["name"] != name] + [template]
        cache.set(_template_cache_key(ws.id), templates, timeout=None)
        return success_response(template, status_code=201)


class ViewTemplateApplyView(APIView):
    """POST .../view-templates/{id}/apply/?project_id= —— 快照拷贝（BR-02）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, slug, template_id):
        ws = get_workspace_or_404(slug, request.user)[0]
        project = get_project_or_404(slug, request.query_params.get("project_id") or "", request.user)[0]
        try:
            templates = cache.get(_template_cache_key(ws.id)) or []
        except Exception:  # noqa: BLE001
            templates = []
        template = next((t for t in templates if t["id"] == template_id), None)
        if template is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        view = IssueView.objects.create(
            workspace=project.workspace,
            project=project,
            owner=request.user,
            name=f"模板·{template['name']}",
            filters=template["filter_json"],
            created_by=request.user,
        )
        record(
            event_key=hashlib.sha256(f"viewtpl.apply:{view.id}:{request.user.id}:{_time.time()}".encode()).hexdigest()[
                :80
            ],
            category="workflow",
            action="state_changed",
            workspace_id=ws.id,
            actor=request.user,
            obj={"type": "issue_view", "id": str(view.id)},
            detail={"template": template["name"], "version": template["version"]},
        )
        return success_response({"view_id": str(view.id), "template_version": template["version"]}, status_code=201)


# ── WF-007 超时流转 ────────────────────────────────────────────────


def _rules_key(project_id) -> str:
    return f"wftimeout:{project_id}"


class TimeoutRuleView(APIView):
    """GET/POST/DELETE .../workflow/timeout-rules/（WF 管理员配，BR-01）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def _project(self, request, slug, project_id):
        require_role(request, self, WorkspaceRole.ADMIN)
        return get_project_or_404(slug, project_id, request.user)[0]

    def get(self, request, slug, project_id):
        project = self._project(request, slug, project_id)
        try:
            rules = cache.get(_rules_key(project.id)) or []
        except Exception:  # noqa: BLE001
            rules = []
        return success_response(rules)

    def post(self, request, slug, project_id):
        project = self._project(request, slug, project_id)
        p = request.data or {}
        from_state = str(p.get("from_state_id") or "")
        to_state = str(p.get("to_state_id") or "")
        hours = int(p.get("hours") or 0)
        if not from_state or not to_state or hours <= 0:
            raise AppException(
                "VALIDATION_ERROR",
                message="from_state_id/to_state_id/hours 必填",
                details=[{"field": "hours", "code": "REQUIRED"}],
            )
        rule = {
            "id": hashlib.sha256(f"{project.id}:{from_state}:{to_state}".encode()).hexdigest()[:12],
            "from_state_id": from_state,
            "to_state_id": to_state,
            "hours": hours,
            "enabled": True,
            "created_by": str(request.user.id),
        }
        try:
            rules = cache.get(_rules_key(project.id)) or []
        except Exception:  # noqa: BLE001
            rules = []
        rules = [r for r in rules if r["id"] != rule["id"]] + [rule]
        cache.set(_rules_key(project.id), rules, timeout=None)
        return success_response(rule, status_code=201)


def wf_timeout_sweep() -> dict:
    """beat：每小时扫描停留超时任务→引擎守卫流转（BR-02 失败留原态记因）。

    本轮实现守卫链的最小执行面：目标态存在性校验 + 状态直迁（守卫阻断
    场景经 updated_at 幂等重试；完整 TASK-005 断言链接线随 WF 引擎演进）。
    """
    moved = skipped = 0
    projects = Project.objects.filter(deleted_at__isnull=True)
    for project in projects:
        try:
            rules = cache.get(_rules_key(project.id)) or []
        except Exception:  # noqa: BLE001
            continue
        if not rules:
            continue
        state_ids = {str(s.id) for s in State.objects.filter(project=project)}
        for rule in rules:
            if not rule.get("enabled"):
                continue
            cutoff = timezone.now() - timedelta(hours=rule["hours"])
            stale = list(
                Issue.objects.filter(
                    project=project,
                    state_id=rule["from_state_id"],
                    deleted_at__isnull=True,
                    completed_at__isnull=True,
                    updated_at__lt=cutoff,
                )[:200]
            )
            for issue in stale:
                if rule["to_state_id"] not in state_ids:
                    skipped += 1  # 目标态失效——守卫跳过
                    continue
                Issue.objects.filter(pk=issue.pk).update(state_id=rule["to_state_id"], updated_at=timezone.now())
                moved += 1
    return {"moved": moved, "skipped": skipped}


# ── PROJ-005 资源调度建议 ──────────────────────────────────────────


class ResourceSchedulingView(APIView):
    """GET .../resource-scheduling/ —— 超载/闲置/平衡对（只读建议 BR-01）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, slug):
        from plane.app.views.gantt_portfolio import DEFAULT_WEEKLY_CAPACITY

        ws = get_workspace_or_404(slug, request.user)[0]
        week_start = timezone.now().date() - timedelta(days=timezone.now().weekday())
        rows = (
            WorkLogSummary.objects.filter(project__workspace=ws, week_start=week_start)
            .values("actor_id")
            .annotate(minutes=Sum("total_minutes"))
        )
        names = dict(
            WorkspaceMember.objects.filter(workspace=ws, is_active=True, deleted_at__isnull=True).values_list(
                "member_id", "member__display_name"
            )
        )
        overloaded, underutilized = [], []
        for row in rows:
            ratio = (row["minutes"] or 0) / DEFAULT_WEEKLY_CAPACITY
            entry = {
                "member_id": str(row["actor_id"]),
                "name": names.get(row["actor_id"], "？"),
                "minutes": row["minutes"] or 0,
                "ratio": round(ratio, 3),
            }
            (overloaded if ratio > 1.0 else underutilized if ratio < 0.5 else []).append(entry)
        suggestions = []
        for over in sorted(overloaded, key=lambda x: -x["minutes"]):
            for under in sorted(underutilized, key=lambda x: x["minutes"]):
                if under["ratio"] < 0.9:
                    suggestions.append(
                        {
                            "from": over["member_id"],
                            "to": under["member_id"],
                            "minutes": min(
                                over["minutes"] - DEFAULT_WEEKLY_CAPACITY,
                                int(DEFAULT_WEEKLY_CAPACITY * 0.9 - under["minutes"]),
                            ),
                        }
                    )
                    break
        return success_response(
            {"overloaded": overloaded, "underutilized": underutilized, "suggestions": suggestions[:20]}
        )


# ── COLLAB-005 推送策略 ────────────────────────────────────────────

_DIGEST_KEY = "pushdigest:{user_id}:{kind}"
_P1_KINDS = {"mention", "approval"}


def _pref_cache_key(user_id) -> str:
    return f"pushpref:{user_id}"


class PushPreferencesView(APIView):
    """GET/PATCH /users/me/push-preferences/（BR-01 偏好）。"""

    permission_classes = [IsAuthenticatedAndActive]

    _DEFAULT = {
        "channel_weights": {"im": 3, "email": 2, "im_bot": 1},
        "dnd": {"start": None, "end": None},
        "digest_minutes": 10,
    }

    def get(self, request):
        try:
            pref = cache.get(_pref_cache_key(request.user.id)) or self._DEFAULT
        except Exception:  # noqa: BLE001
            pref = self._DEFAULT
        return success_response(pref)

    def patch(self, request):
        try:
            pref = cache.get(_pref_cache_key(request.user.id)) or dict(self._DEFAULT)
        except Exception:  # noqa: BLE001
            pref = dict(self._DEFAULT)
        p = request.data or {}
        if "digest_minutes" in p:
            pref["digest_minutes"] = max(0, min(int(p["digest_minutes"] or 10), 1440))
        if "dnd" in p and isinstance(p["dnd"], dict):
            pref["dnd"] = {"start": p["dnd"].get("start"), "end": p["dnd"].get("end")}
        if "channel_weights" in p and isinstance(p["channel_weights"], dict):
            pref["channel_weights"] = {
                k: int(v) for k, v in p["channel_weights"].items() if k in ("im", "email", "im_bot")
            }
        cache.set(_pref_cache_key(request.user.id), pref, timeout=None)
        return success_response(pref)


def route_push(user_id: str, kind: str, payload: dict) -> dict:
    """路由器（内部）：DND 静默 / P1 直发 / 聚合窗口合并（BR-02/03）。

    返回 {action: send|digest|silent, channels: [...]}；通道投递失败降级
    由调用方（通知域）按 channels 顺序执行。
    """
    try:
        pref = cache.get(_pref_cache_key(user_id)) or {}
    except Exception:  # noqa: BLE001
        pref = {}
    dnd = pref.get("dnd") or {}
    now = timezone.now()
    if dnd.get("start") is not None and dnd.get("end") is not None:
        start = int(dnd["start"]), int(dnd["end"])
        hour = now.hour
        if start[0] <= start[1]:
            in_dnd = start[0] <= hour < start[1]
        else:  # 跨午夜
            in_dnd = hour >= start[0] or hour < start[1]
        if in_dnd and kind not in _P1_KINDS:
            return {"action": "silent", "channels": []}  # BR-02 UT-01
    weights = pref.get("channel_weights") or {"im": 3, "email": 2, "im_bot": 1}
    channels = [c for c, _ in sorted(weights.items(), key=lambda kv: -kv[1]) if weights.get(c, 0) > 0]
    if kind in _P1_KINDS:
        return {"action": "send", "channels": channels}  # BR-02 P1 直发
    window = int(pref.get("digest_minutes") or 10)
    if window <= 0:
        return {"action": "send", "channels": channels}
    key = _DIGEST_KEY.format(user_id=user_id, kind=kind)
    try:
        queued = cache.get(key) or []
        queued.append(payload)
        cache.set(key, queued, timeout=window * 60)
        if len(queued) > 1:
            return {"action": "digest", "channels": channels}  # 合并同类
        return {"action": "send", "channels": channels}
    except Exception:  # noqa: BLE001 —— 降级直发
        return {"action": "send", "channels": channels}
