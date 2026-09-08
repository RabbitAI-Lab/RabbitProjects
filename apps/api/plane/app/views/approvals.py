"""审批端点（WF-002 §4.6）——审批流 CRUD + 审批中心三 Tab + 实例详情/动作。

行级口径（§4.6 公共约定）：pending = 本人当前级 pending 票；acted = 本人存在
approve/reject 动作票（skipped 不计）；mine = 本人发起。排序固定不开放 ordering。
"""
from __future__ import annotations

import base64

from django.db.models import F
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated, ProjectAdminPermission
from plane.app.views._access import get_project_or_404, get_workspace_or_404
from plane.base.exception import AppException
from plane.base.response import created_response, success_response
from plane.db.models import (
    ApprovalFlow,
    ApprovalInstance,
    ApprovalNode,
    ApprovalRecord,
    Issue,
    WorkflowTransition,
)
from plane.workflow.approval import ApprovalError, ApprovalService, validate_flow_definition

MAX_PER_PAGE = 100


def _approval_err(exc: ApprovalError) -> AppException:
    details = ([{"field": exc.field, "code": exc.sub}] if exc.field and exc.sub else None)
    return AppException(exc.code, message=exc.message, details=details)


def _encode_cursor(offset: int, *, is_prev: bool, per_page: int) -> str:
    raw = f"{per_page}:{offset}:{1 if is_prev else 0}"
    return base64.b64encode(raw.encode()).decode()


def _decode_cursor(raw: str | None) -> tuple[int, int]:
    """返回 (per_page, offset)；缺省 per_page=100 offset=0（§6.2 默认）。"""
    if not raw:
        return MAX_PER_PAGE, 0
    try:
        parts = base64.b64decode(raw.encode()).decode().split(":")
        per_page = min(max(int(parts[0]), 1), MAX_PER_PAGE)
        offset = max(int(parts[1]), 0)
        return per_page, offset
    except (ValueError, IndexError):
        return MAX_PER_PAGE, 0


def _page_meta(offset: int, per_page: int, total: int) -> dict:
    next_cursor = _encode_cursor(offset + per_page, is_prev=False, per_page=per_page) \
        if offset + per_page < total else None
    prev_cursor = _encode_cursor(max(offset - per_page, 0), is_prev=True, per_page=per_page) \
        if offset > 0 else None
    import math

    return {
        "next_cursor": next_cursor,
        "prev_cursor": prev_cursor,
        "next_page_results": next_cursor is not None,
        "prev_page_results": prev_cursor is not None,
        "count": min(per_page, max(total - offset, 0)),
        "total_count": total,
        "total_pages": math.ceil(total / per_page) if per_page else 1,
        "page": offset // per_page + 1 if per_page else 1,
        "per_page": per_page,
    }


def _instance_row(inst: ApprovalInstance, *, my_action: str | None = None) -> dict:
    issue = inst.issue
    project = issue.project
    row = {
        "instance_id": str(inst.id),
        "status": inst.status,
        "current_level": inst.current_level,
        "flow_name": inst.flow_snapshot["name"],
        "issue": {
            "id": str(issue.id),
            "issue_key": f"{project.identifier}-{issue.sequence_id}",
            "name": issue.name,
            "project_id": str(project.id),
        },
        "initiator_id": str(inst.initiator_id),
        "created_at": inst.created_at,
        "completed_at": inst.completed_at,
    }
    if my_action is not None:
        row["my_action"] = my_action
    return row


def _instance_detail(inst: ApprovalInstance) -> dict:
    nodes = [
        {"level": n["level"], "pass_mode": n["pass_mode"],
         "approver_type": n["approver_type"], "approver_config": n["approver_config"]}
        for n in inst.flow_snapshot["nodes"]
    ]
    records = [
        {"level": r.level, "approver_id": str(r.approver_id),
         "approver_name": r.approver.display_name, "action": r.action,
         "comment": r.comment, "acted_at": r.acted_at}
        for r in inst.records.select_related("approver").order_by("level", "id")
    ]
    return {
        **_instance_row(inst),
        "from_state": {"id": str(inst.from_state_id)},
        "nodes": nodes,
        "records": records,
    }


class ApprovalFlowListCreateView(APIView):
    """GET 列表（workflow.manage）/ POST 创建（nodes 全量创建，§4.6）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        flows = ApprovalFlow.objects.filter(project=project).order_by("-updated_at")
        data = [
            {
                "id": str(f.id), "name": f.name, "description": f.description,
                "is_active": f.is_active, "forbid_self_approve": f.forbid_self_approve,
                "nodes": [
                    {"level": n.level, "pass_mode": n.pass_mode,
                     "approver_type": n.approver_type, "approver_config": n.approver_config,
                     "timeout_hours": n.timeout_hours}
                    for n in f.nodes.order_by("level")
                ],
            }
            for f in flows
        ]
        return success_response(data)

    def post(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        if not ProjectAdminPermission().has_permission(request, self):
            raise AppException("PERM_ROLE_INSUFFICIENT", message="需要项目管理员权限")
        name = (request.data.get("name") or "").strip()
        if not name:
            raise AppException("VALIDATION_ERROR",
                               details=[{"field": "name", "code": "REQUIRED"}],
                               message="审批流名称必填")
        nodes_payload = request.data.get("nodes") or []
        try:
            validate_flow_definition(nodes_payload)
        except ApprovalError as exc:
            raise _approval_err(exc) from None
        if ApprovalFlow.objects.filter(project=project, name=name).exists():
            raise AppException("RESOURCE_ALREADY_EXISTS", message="同名审批流已存在")
        flow = ApprovalFlow.objects.create(
            project=project, name=name,
            description=request.data.get("description") or "",
            forbid_self_approve=bool(request.data.get("forbid_self_approve", True)),
            created_by=request.user)
        for n in nodes_payload:
            ApprovalNode.objects.create(
                flow=flow, level=n["level"], pass_mode=n["pass_mode"],
                approver_type=n["approver_type"], approver_config=n.get("approver_config") or {},
                timeout_hours=n.get("timeout_hours"))
        return created_response(
            {"id": str(flow.id), "name": flow.name, "nodes": nodes_payload},
            location=f"/api/v1/workspaces/{slug}/projects/{project_id}/approval-flows/{flow.id}/")


class ApprovalFlowDetailView(APIView):
    """GET / PATCH（nodes 整体替换）/ DELETE（SET_NULL 摘挂，返回受影响边清单）。"""

    permission_classes = [IsAuthenticated]

    def _get_flow(self, slug, project_id, fid, user) -> ApprovalFlow:
        project, _, _ = get_project_or_404(slug, project_id, user)
        try:
            return ApprovalFlow.objects.get(id=fid, project=project)
        except ApprovalFlow.DoesNotExist:
            raise AppException("RESOURCE_NOT_FOUND", message="审批流不存在") from None

    def get(self, request, slug, project_id, fid):
        flow = self._get_flow(slug, project_id, fid, request.user)
        return success_response({
            "id": str(flow.id), "name": flow.name, "description": flow.description,
            "is_active": flow.is_active, "forbid_self_approve": flow.forbid_self_approve,
            "nodes": [
                {"level": n.level, "pass_mode": n.pass_mode,
                 "approver_type": n.approver_type, "approver_config": n.approver_config,
                 "timeout_hours": n.timeout_hours}
                for n in flow.nodes.order_by("level")
            ],
        })

    def patch(self, request, slug, project_id, fid):
        flow = self._get_flow(slug, project_id, fid, request.user)
        if not ProjectAdminPermission().has_permission(request, self):
            raise AppException("PERM_ROLE_INSUFFICIENT", message="需要项目管理员权限")
        for f in ("name", "description"):
            if f in request.data:
                setattr(flow, f, request.data[f])
        if "is_active" in request.data:
            flow.is_active = bool(request.data["is_active"])
        if "forbid_self_approve" in request.data:
            flow.forbid_self_approve = bool(request.data["forbid_self_approve"])
        if "nodes" in request.data:
            nodes_payload = request.data["nodes"]
            try:
                validate_flow_definition(nodes_payload)
            except ApprovalError as exc:
                raise _approval_err(exc) from None
            flow.nodes.all().delete()  # 整体替换（与 WF-001 PUT graph/ 集合型同形）
            for n in nodes_payload:
                ApprovalNode.objects.create(
                    flow=flow, level=n["level"], pass_mode=n["pass_mode"],
                    approver_type=n["approver_type"], approver_config=n.get("approver_config") or {},
                    timeout_hours=n.get("timeout_hours"))
        flow.updated_by = request.user
        flow.save()
        return success_response({"id": str(flow.id), "name": flow.name,
                                 "is_active": flow.is_active})

    def delete(self, request, slug, project_id, fid):
        flow = self._get_flow(slug, project_id, fid, request.user)
        if not ProjectAdminPermission().has_permission(request, self):
            raise AppException("PERM_ROLE_INSUFFICIENT", message="需要项目管理员权限")
        affected = list(
            WorkflowTransition.objects.filter(approval_flow=flow)
            .values_list("id", flat=True))
        flow.delete()  # 引用边 SET_NULL 自动摘挂（BR-13）
        return success_response({"id": fid, "affected_transition_ids": affected})


class ApprovalsPendingView(APIView):
    """GET 我的待办（跨项目）：当前级 pending 票；?count_only=1 精简信封。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        ws, _ = get_workspace_or_404(slug, request.user)
        base_qs = (
            ApprovalInstance.objects.filter(
                status=ApprovalInstance.Status.PENDING,
                issue__project__workspace=ws,
                records__approver=request.user,
                records__action=ApprovalRecord.Action.PENDING,
                records__level=F("current_level"),
            )
            .select_related("issue", "issue__project")
            .distinct()
            .order_by("-created_at", "-id")
        )
        if request.query_params.get("count_only") in ("1", "true"):
            return success_response({"count": base_qs.count()})
        per_page, offset = _decode_cursor(request.query_params.get("cursor"))
        total = base_qs.count()
        rows = [_instance_row(i) for i in base_qs[offset:offset + per_page]]
        return success_response(rows, meta=_page_meta(offset, per_page, total))


class ApprovalsActedView(APIView):
    """GET 我的已办：本人存在 approve/reject 动作票的实例（按动作时间倒序）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        ws, _ = get_workspace_or_404(slug, request.user)
        action = (
            ApprovalRecord.objects.filter(approver=request.user)
            .exclude(action=ApprovalRecord.Action.PENDING)
            .exclude(action=ApprovalRecord.Action.SKIPPED)
            .order_by("-acted_at", "-id")
            .values_list("instance_id", "acted_at", "action")[:10000]
        )
        per_page, offset = _decode_cursor(request.query_params.get("cursor"))
        # 排序按动作票 acted_at 倒序（§4.6 固定排序）；实例去重取最新动作
        seen: dict[str, tuple] = {}
        for inst_id, acted_at, act in action:
            if str(inst_id) not in seen:
                seen[str(inst_id)] = (acted_at, act)
        inst_ids = list(seen.keys())
        qs = (
            ApprovalInstance.objects.filter(id__in=inst_ids,
                                            issue__project__workspace=ws)
            .select_related("issue", "issue__project")
        )
        by_id = {str(i.id): i for i in qs}
        ordered = []
        for iid in inst_ids:
            if iid in by_id:
                acted_at, act = seen[iid]
                ordered.append((acted_at, _instance_row(by_id[iid], my_action=act)))
        total = len(ordered)
        rows = [r for _, r in ordered[offset:offset + per_page]]
        return success_response(rows, meta=_page_meta(offset, per_page, total))


class ApprovalsMineView(APIView):
    """GET 我发起的（仅本人发起）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        ws, _ = get_workspace_or_404(slug, request.user)
        base_qs = (
            ApprovalInstance.objects.filter(
                initiator=request.user, issue__project__workspace=ws)
            .select_related("issue", "issue__project")
            .order_by("-created_at", "-id")
        )
        per_page, offset = _decode_cursor(request.query_params.get("cursor"))
        total = base_qs.count()
        rows = [_instance_row(i) for i in base_qs[offset:offset + per_page]]
        return success_response(rows, meta=_page_meta(offset, per_page, total))


class ApprovalInstanceDetailView(APIView):
    """GET 实例详情（时间线）——项目成员可读（BR-15 透明）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_id, aid):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        try:
            inst = ApprovalInstance.objects.select_related(
                "issue", "issue__project").get(id=aid, issue__project=project)
        except ApprovalInstance.DoesNotExist:
            raise AppException("RESOURCE_NOT_FOUND", message="审批实例不存在") from None
        return success_response(_instance_detail(inst))


class ApprovalInstanceActionView(APIView):
    """POST 动作（approve/reject/withdraw/terminate，L1-L9 边界在服务层）。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, slug, project_id, aid):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        action = request.data.get("action") or ""
        comment = request.data.get("comment") or ""
        try:
            inst = ApprovalService().act(
                instance_id=aid, actor=request.user, action=action, comment=comment)
        except ApprovalError as exc:
            raise _approval_err(exc) from None
        out: dict = {
            "instance_id": str(inst.id), "status": inst.status,
            "current_level": inst.current_level,
            "completed_at": inst.completed_at,
        }
        if inst.status == ApprovalInstance.Status.APPROVED:
            inst.issue.refresh_from_db(fields=["state"])
            state = inst.issue.state
            out["issue_state"] = ({"id": str(state.id), "name": state.name}
                                  if state else None)
        return success_response(out)


class IssueApprovalsView(APIView):
    """GET 任务的实例列表（项目成员）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_id, issue_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        try:
            Issue.objects.get(id=issue_id, project=project, deleted_at__isnull=True)
        except Issue.DoesNotExist:
            raise AppException("RESOURCE_NOT_FOUND", message="任务不存在") from None
        qs = (
            ApprovalInstance.objects.filter(issue_id=issue_id)
            .select_related("issue", "issue__project")
            .order_by("-created_at")
        )
        return success_response([_instance_row(i) for i in qs])
