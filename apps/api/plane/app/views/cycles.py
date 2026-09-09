"""敏捷报表迭代视图（RPT-003 §4.5，Sprint-9）——10 端点。

权限（BR-13）：读 report.read（VIEWER）/ 写 cycle.manage（ADMIN，rbac §8.2
⚠️ 可配置列按默认）/ config 写 project.setting.manage / 导出 report.export。
聚合三端点挂 ReportAggRateThrottle 10/min/用户（api-conventions §7.2）。
"""
from __future__ import annotations

from django.http import HttpResponse
from rest_framework.exceptions import NotFound
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated, require_permission
from plane.app.views._access import get_project_or_404
from plane.base.exception import AppException
from plane.base.response import created_response, success_response
from plane.base.throttling import BASE_THROTTLES, ReportAggRateThrottle
from plane.db.models import Cycle, Issue, IssueActivity, ProjectReportConfig
from plane.db.services.agile_reports import (
    AgileReportService,
    MeasureInvalid,
    backfill_cycle_snapshots,
    on_cycle_completed,
    validate_measure,
)

ORDERING_WHITELIST = {"name", "start_date", "end_date"}


def _get_cycle_or_404(kwargs, project) -> Cycle:
    cycle = Cycle.objects.filter(
        id=kwargs["cycle_id"], project=project).first()
    if cycle is None:
        raise NotFound("RESOURCE_NOT_FOUND") from None
    return cycle


def _cycle_progress(cycle: Cycle) -> float:
    """列表进度：completed 组度量 / 范围总量（快照可算则用终值，否则直查）。"""
    snap = (cycle.snapshots.order_by("-snapshot_date").first())
    if snap is not None:
        total = snap.scope_total
        done = (snap.remaining_by_group or {}).get("completed", 0.0)
        return (done / total) if total else 0.0
    issues = list(cycle.issues.filter(deleted_at__isnull=True).select_related("state"))
    total = len(issues)
    done = sum(1 for i in issues
               if (i.state.group if i.state else "unstarted") == "completed")
    return (done / total) if total else 0.0


class CycleListCreateView(APIView):
    """GET/POST cycles/ —— 列表（含进度，游标分页白名单排序）/ 新建。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        ordering = request.query_params.get("ordering", "-start_date")
        if ordering.lstrip("-") not in ORDERING_WHITELIST:
            raise AppException("VALIDATION_INVALID_PARAM",
                               details=[{"field": "ordering", "code": "INVALID",
                                         "message": "排序字段不在白名单"}])
        status_f = request.query_params.get("status")
        qs = Cycle.objects.filter(project=project).order_by(ordering, "-id")
        if status_f:
            qs = qs.filter(status=status_f)
        data = [{
            "id": str(c.id), "name": c.name, "status": c.status,
            "start_date": c.start_date, "end_date": c.end_date,
            "issue_count": c.issues.filter(deleted_at__isnull=True).count(),
            "progress": round(_cycle_progress(c), 4),
        } for c in qs[:100]]
        return success_response(data, meta={"count": len(data)})

    @require_permission("cycle.manage")
    def post(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        name = (request.data.get("name") or "").strip()
        start, end = request.data.get("start_date"), request.data.get("end_date")
        errors = []
        if not name:
            errors.append({"field": "name", "code": "REQUIRED", "message": "名称必填"})
        if not start or not end:
            errors.append({"field": "start_date", "code": "REQUIRED",
                           "message": "时间盒起止必填"})
        elif str(end) <= str(start):
            raise AppException("VALIDATION_INVALID_DATE_RANGE",
                               message="结束日期必须晚于开始日期")
        if errors:
            raise AppException("VALIDATION_ERROR", details=errors)
        if Cycle.objects.filter(project=project, name=name).exists():
            raise AppException("RESOURCE_ALREADY_EXISTS", message="同名迭代已存在")
        cycle = Cycle.objects.create(
            project=project, name=name, start_date=start, end_date=end,
            created_by=request.user)
        return created_response(
            {"id": str(cycle.id), "name": cycle.name, "status": cycle.status},
            location=f"/api/v1/workspaces/{slug}/projects/{project_id}/cycles/{cycle.id}/")


class CycleDetailView(APIView):
    """GET/PATCH/DELETE cycles/{cycle_id}/ —— planned 可改时间盒；删仅 planned。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_id, cycle_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        cycle = _get_cycle_or_404({"cycle_id": cycle_id}, project)
        return success_response({
            "id": str(cycle.id), "name": cycle.name, "status": cycle.status,
            "start_date": cycle.start_date, "end_date": cycle.end_date,
            "issue_count": cycle.issues.filter(deleted_at__isnull=True).count(),
            "progress": round(_cycle_progress(cycle), 4)})

    @require_permission("cycle.manage")
    def patch(self, request, slug, project_id, cycle_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        cycle = _get_cycle_or_404({"cycle_id": cycle_id}, project)
        if cycle.status == Cycle.Status.COMPLETED:
            raise AppException("RESOURCE_STATE_INVALID",
                               details=[{"field": "status", "code": "COMPLETED",
                                         "message": "已归档迭代不可修改"}])
        for f in ("name", "start_date", "end_date"):
            if f in request.data:
                setattr(cycle, f, request.data[f])
        if cycle.end_date <= cycle.start_date:
            raise AppException("VALIDATION_INVALID_DATE_RANGE",
                               message="结束日期必须晚于开始日期")
        cycle.updated_by = request.user
        cycle.save()
        return success_response({"id": str(cycle.id)})

    @require_permission("cycle.manage")
    def delete(self, request, slug, project_id, cycle_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        cycle = _get_cycle_or_404({"cycle_id": cycle_id}, project)
        if cycle.status != Cycle.Status.PLANNED:
            raise AppException("RESOURCE_STATE_INVALID",
                               details=[{"field": "status", "code": "INVALID_STATE",
                                         "message": "仅规划中迭代可删除"}])
        cycle.soft_delete(actor_id=request.user.id)
        return success_response({"id": str(cycle.id)})


class CycleStartView(APIView):
    """POST cycles/{cycle_id}/start/ —— BR-03 唯一 active（DB 偏唯一约束兜底）。"""

    permission_classes = [IsAuthenticated]

    @require_permission("cycle.manage")
    def post(self, request, slug, project_id, cycle_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        cycle = _get_cycle_or_404({"cycle_id": cycle_id}, project)
        active = Cycle.objects.filter(project=project, status=Cycle.Status.ACTIVE).first()
        if active is not None and active.id != cycle.id:
            raise AppException("RESOURCE_STATE_INVALID",
                               details=[{"field": "cycle", "code": "ACTIVE_EXISTS",
                                         "message": f"当前进行中迭代 {active.name}"}])
        cycle.status = Cycle.Status.ACTIVE
        cycle.updated_by = request.user
        cycle.save(update_fields=["status", "updated_by", "updated_at"])
        # 首日快照即时落（BR-10 planned 口径锚；beat 漏跑由 velocity 兜底补跑）
        config, _ = ProjectReportConfig.objects.get_or_create(
            project=project, defaults={"report_measure": "count"})
        backfill_cycle_snapshots(cycle, cycle.start_date, config.report_measure)
        return success_response({"id": str(cycle.id), "status": cycle.status})


class CycleCompleteView(APIView):
    """POST cycles/{cycle_id}/complete/ —— {carry_over: next|backlog}（BR-04）。

    事务内顺序：先落 status=completed 触发终版快照（含未完成任务）→ 后结转。
    """

    permission_classes = [IsAuthenticated]

    @require_permission("cycle.manage")
    def post(self, request, slug, project_id, cycle_id):
        from django.db import transaction
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        cycle = _get_cycle_or_404({"cycle_id": cycle_id}, project)
        if cycle.status != Cycle.Status.ACTIVE:
            raise AppException("RESOURCE_STATE_INVALID",
                               details=[{"field": "status", "code": "INVALID_STATE",
                                         "message": "仅进行中迭代可结束"}])
        carry_over = request.data.get("carry_over", "backlog")
        if carry_over not in ("next", "backlog"):
            raise AppException("VALIDATION_ERROR",
                               details=[{"field": "carry_over", "code": "NOT_A_CHOICE",
                                         "message": "carry_over 取值 next|backlog"}])
        with transaction.atomic():
            cycle.status = Cycle.Status.COMPLETED
            cycle.updated_by = request.user
            cycle.save(update_fields=["status", "updated_by", "updated_at"])
            unfinished = list(cycle.issues.filter(
                deleted_at__isnull=True
            ).exclude(state__group="completed").select_related("state"))
            target_cycle = None
            if carry_over == "next":
                target_cycle = Cycle.objects.filter(
                    project=project, status=Cycle.Status.PLANNED
                ).order_by("start_date").first()
            # 终版快照先于移交（含未完成任务；结转属于下一迭代 scope）
            on_cycle_completed(cycle, unfinished, target_cycle=target_cycle)
        return success_response({
            "id": str(cycle.id), "status": "completed",
            "carried_over": len(unfinished) if carry_over == "next" and target_cycle else 0,
            "moved_back": len(unfinished) if carry_over == "backlog" else 0})


class CycleIssuesBatchView(APIView):
    """PUT cycles/{cycle_id}/issues/ —— 整批设置迭代任务（BR-01 换绑同事务
    + field='cycles' 事件埋点——scope change 追踪与回放的唯一事件源）。"""

    permission_classes = [IsAuthenticated]

    @require_permission("cycle.manage")
    def put(self, request, slug, project_id, cycle_id):
        from django.db import transaction
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        cycle = _get_cycle_or_404({"cycle_id": cycle_id}, project)
        if cycle.status == Cycle.Status.COMPLETED:
            raise AppException("RESOURCE_STATE_INVALID",
                               details=[{"field": "status", "code": "COMPLETED",
                                         "message": "已归档迭代不可调整任务"}])
        issue_ids = request.data.get("issue_ids", [])
        if not isinstance(issue_ids, list):
            raise AppException("VALIDATION_ERROR",
                               details=[{"field": "issue_ids", "code": "INVALID",
                                         "message": "issue_ids 必须为数组"}])
        issues = list(Issue.objects.filter(
            id__in=issue_ids, project=project, deleted_at__isnull=True))
        if len(issues) != len(set(issue_ids)):
            raise AppException("VALIDATION_ERROR",
                               details=[{"field": "issue_ids", "code": "DOES_NOT_EXIST",
                                         "message": "含项目外或不存在任务"}])
        with transaction.atomic():
            before = set(cycle.issues.filter(deleted_at__isnull=True)
                         .values_list("id", flat=True))
            after = {i.id for i in issues}
            # 移出（换绑同事务，BR-01）
            for issue in cycle.issues.filter(deleted_at__isnull=True):
                if issue.id not in after:
                    issue.cycle = None
                    issue.save(update_fields=["cycle", "updated_at"])
                    IssueActivity.objects.create(
                        issue=issue, actor=request.user,   # issue 域行（XOR：不传 project）
                        verb=IssueActivity.Verb.UPDATED, field="cycles",
                        old_identifier=cycle.id, new_identifier=None,
                        comment=f"移出迭代 {cycle.name}")
            for issue in issues:
                if issue.id not in before:
                    old_cycle_id = issue.cycle_id
                    issue.cycle = cycle
                    issue.save(update_fields=["cycle", "updated_at"])
                    IssueActivity.objects.create(
                        issue=issue, actor=request.user,   # issue 域行（XOR：不传 project）
                        verb=IssueActivity.Verb.UPDATED, field="cycles",
                        old_identifier=old_cycle_id, new_identifier=cycle.id,
                        comment=f"加入迭代 {cycle.name}")
        return success_response({"cycle_id": str(cycle.id),
                                 "issue_count": len(after)})


class CycleBurndownView(APIView):
    """GET cycles/{cycle_id}/burndown/ —— 燃尽载荷（快照直查，归档只读终版）。"""

    permission_classes = [IsAuthenticated]
    throttle_classes = [*BASE_THROTTLES, ReportAggRateThrottle]

    @require_permission("report.read")
    def get(self, request, slug, project_id, cycle_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        cycle = _get_cycle_or_404({"cycle_id": cycle_id}, project)
        payload = AgileReportService().burndown(cycle.id)
        return success_response({
            "cycle": payload.cycle, "measure": payload.measure,
            "frozen": payload.frozen, "ideal": payload.ideal,
            "points": payload.points, "today": payload.today,
            "scope_events": payload.scope_events})


class VelocityView(APIView):
    """GET projects/{id}/reports/velocity/ —— 速率载荷（终版快照唯一锚）。"""

    permission_classes = [IsAuthenticated]
    throttle_classes = [*BASE_THROTTLES, ReportAggRateThrottle]

    @require_permission("report.read")
    def get(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        limit = min(int(request.query_params.get("limit", 6)), 24)
        payload = AgileReportService().velocity(project.id, limit=limit)
        return success_response(payload)


class CumulativeFlowView(APIView):
    """GET projects/{id}/reports/cumulative-flow/?from=&to= —— CFD（快照直查）。"""

    permission_classes = [IsAuthenticated]
    throttle_classes = [*BASE_THROTTLES, ReportAggRateThrottle]

    @require_permission("report.read")
    def get(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        from datetime import timedelta

        from django.utils import timezone as dj_tz
        frm = request.query_params.get("from") or (
            dj_tz.localdate() - timedelta(days=30)).isoformat()
        to = request.query_params.get("to") or dj_tz.localdate().isoformat()
        config, _ = ProjectReportConfig.objects.get_or_create(
            project=project, defaults={"report_measure": "count"})
        payload = AgileReportService().cumulative_flow(
            project.id, frm, to, config.report_measure)
        return success_response(payload)


class ReportConfigView(APIView):
    """GET/PATCH projects/{id}/reports/config/ —— 度量口径单例（BR-07）。

    PATCH 单例（api-conventions §3.2 禁单例 PUT）；改配置即重算当日之后快照口径。
    """

    permission_classes = [IsAuthenticated]

    @require_permission("report.read")
    def get(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        config, _ = ProjectReportConfig.objects.get_or_create(
            project=project, defaults={"report_measure": "count"})
        return success_response({"report_measure": config.report_measure})

    @require_permission("project.setting.manage")
    def patch(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        measure = request.data.get("report_measure", "")
        try:
            validate_measure(measure)
        except MeasureInvalid:
            raise AppException("VALIDATION_ERROR",
                               details=[{"field": "report_measure", "code": "INVALID",
                                         "message": "度量口径非法"}]) from None
        config, _ = ProjectReportConfig.objects.get_or_create(
            project=project, defaults={"report_measure": "count"})
        config.report_measure = measure
        config.updated_by = request.user
        config.save()
        return success_response({"report_measure": config.report_measure})


class CycleBurndownExportView(APIView):
    """GET cycles/{cycle_id}/burndown/export/?format=csv —— CSV 流式（数百行级）。"""

    permission_classes = [IsAuthenticated]

    @require_permission("report.export")
    def get(self, request, slug, project_id, cycle_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        cycle = _get_cycle_or_404({"cycle_id": cycle_id}, project)
        payload = AgileReportService().burndown(cycle.id)
        rows = [["date", "remaining", "completed_delta", "scope_delta"]]
        for p in payload.points:
            rows.append([p["date"], p["remaining"],
                         p.get("completed_delta", ""), p.get("scope_delta", "")])
        for e in payload.scope_events:
            rows.append([e["date"], "", "", f"{e['direction']}:{e['issue']}"])
        buf = []
        for row in rows:
            buf.append(",".join(f'"{c}"' if isinstance(c, str) and "," in c else str(c)
                                for c in row))
        resp = HttpResponse("\r\n".join(buf), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = (
            f'attachment; filename="burndown-{cycle.id}.csv"')
        return resp
