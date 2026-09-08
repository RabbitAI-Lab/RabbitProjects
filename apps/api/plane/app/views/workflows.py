"""工作流配置与流转端点（WF-001 §4.8）。

7 配置端点（列表/建草稿/图详情+ETag/PATCH draft-only/PUT graph 整图替换/
发布/归档）+ 2 运行端点（transitions/available/ 与 transitions/）。
权限：配置面 workflow.manage（PROJ_ADMIN+）；运行面 issue.state.transition
（PROJ_CONTRIBUTOR+，rbac §8.2）；读面 project.read（项目成员）。
"""
from __future__ import annotations

import hashlib

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated, ProjectAdminPermission, ProjectPermission
from plane.app.views._access import get_project_or_404
from plane.base.exception import AppException
from plane.base.response import created_response, success_response
from plane.db.models import Issue, State, Workflow, WorkflowState, WorkflowTransition
from plane.db.services.issue_transition_guard import TransitionBlockedError
from plane.workflow.approval import ApprovalError
from plane.workflow.services import (
    EFFECT_TYPES,
    GUARD_TYPES,
    TransitionError,
    WorkflowService,
)


def _wf_etag(wf: Workflow) -> str:
    digest = hashlib.md5(f"{wf.id}|{wf.updated_at.isoformat()}".encode()).hexdigest()
    return f'W/"wf-v1-{digest}"'


def _state_ref(ws: WorkflowState) -> dict:
    s = ws.state
    return {
        "id": str(ws.id), "state_id": str(s.id), "name": s.name, "group": s.group,
        "color": s.color, "is_initial": ws.is_initial,
        "layout_x": ws.layout_x, "layout_y": ws.layout_y, "field_locks": ws.field_locks,
    }


def _edge_ref(e: WorkflowTransition) -> dict:
    return {
        "id": str(e.id), "name": e.name,
        "from_state_id": str(e.from_state_id), "to_state_id": str(e.to_state_id),
        "guards": e.guards, "side_effects": e.side_effects,
        "approval_flow_id": str(e.approval_flow_id) if e.approval_flow_id else None,
        "sort_order": e.sort_order,
    }


def _graph_payload(wf: Workflow) -> dict:
    return {
        "id": str(wf.id), "name": wf.name, "description": wf.description,
        "issue_type_id": str(wf.issue_type_id) if wf.issue_type_id else None,
        "status": wf.status, "version": wf.version, "based_on_version": wf.based_on_version,
        "published_at": wf.published_at,
        "states": [_state_ref(s) for s in wf.wf_states.select_related("state").order_by("created_at")],
        "transitions": [
            _edge_ref(e) for e in wf.transitions.select_related("from_state", "to_state")
            .order_by("sort_order", "created_at")
        ],
        "created_at": wf.created_at, "updated_at": wf.updated_at,
    }


class WorkflowListCreateView(APIView):
    """GET（项目成员可读，含计数）/ POST 创建草稿（workflow.manage，BR-02 至多一个）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        wfs = (
            Workflow.objects.filter(project=project)
            .order_by("-updated_at")
        )
        data = [
            {
                "id": str(w.id), "name": w.name,
                "issue_type_id": str(w.issue_type_id) if w.issue_type_id else None,
                "status": w.status, "version": w.version,
                "state_count": w.wf_states.count(),
                "transition_count": w.transitions.count(),
                "updated_at": w.updated_at,
            }
            for w in wfs
        ]
        return success_response(data, meta={
            "next_cursor": None, "prev_cursor": None,
            "next_page_results": False, "prev_page_results": False,
            "count": len(data), "total_count": len(data), "total_pages": 1,
            "page": 1, "per_page": 100,
        })

    def post(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        if not ProjectAdminPermission().has_permission(request, self):
            raise AppException("PERM_ROLE_INSUFFICIENT", message="需要项目管理员权限")
        name = (request.data.get("name") or "").strip()
        if not name:
            raise AppException("VALIDATION_ERROR",
                               details=[{"field": "name", "code": "REQUIRED"}],
                               message="工作流名称必填")
        issue_type_id = request.data.get("issue_type_id") or None
        # BR-02：同维度已有草稿 → 409（类型专属由 DB 部分唯一兜底；项目默认走行锁）
        with transaction.atomic():
            existing = Workflow.objects.filter(
                project=project, issue_type_id=issue_type_id, status=Workflow.Status.DRAFT
            ).first()
            if existing is None and issue_type_id is None:
                # 项目默认工作流：Service 行锁串行化（§4.2 注，NULL 不受索引约束）
                existing = Workflow.objects.select_for_update().filter(
                    project=project, issue_type_id__isnull=True,
                    status=Workflow.Status.DRAFT).first()
            if existing is not None:
                raise AppException("RESOURCE_ALREADY_EXISTS",
                                   message="该维度已存在草稿工作流")
            wf = Workflow.objects.create(
                project=project, name=name,
                description=request.data.get("description") or "",
                issue_type_id=issue_type_id, created_by=request.user)
        return created_response(
            _graph_payload(wf),
            location=f"/api/v1/workspaces/{slug}/projects/{project_id}/workflows/{wf.id}/")


class WorkflowDetailView(APIView):
    """GET 图详情（画布载荷，ETag/304）/ PATCH draft-only（BR-11，含改绑双向缓存失效）。"""

    permission_classes = [IsAuthenticated]

    def _get_wf(self, slug, project_id, wf_id, user) -> Workflow:
        project, _, _ = get_project_or_404(slug, project_id, user)
        try:
            return Workflow.objects.get(id=wf_id, project=project)
        except Workflow.DoesNotExist:
            raise AppException("RESOURCE_NOT_FOUND", message="工作流不存在") from None

    def get(self, request, slug, project_id, wf_id):
        wf = self._get_wf(slug, project_id, wf_id, request.user)
        etag = _wf_etag(wf)
        if request.headers.get("If-None-Match") == etag:
            return Response(status=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
        return success_response(_graph_payload(wf), headers={"ETag": etag})

    def patch(self, request, slug, project_id, wf_id):
        wf = self._get_wf(slug, project_id, wf_id, request.user)
        if not ProjectAdminPermission().has_permission(request, self):
            raise AppException("PERM_ROLE_INSUFFICIENT", message="需要项目管理员权限")
        if wf.status != Workflow.Status.DRAFT:
            raise AppException("RESOURCE_STATE_INVALID",
                               message="仅草稿可编辑（BR-11：已发布请克隆新草稿）")
        old_type = wf.issue_type_id  # 改绑旧 (project, 旧类型) 缓存键失效由 view 承载（§4.5 注）
        for f in ("name", "description"):
            if f in request.data:
                setattr(wf, f, request.data[f])
        if "issue_type_id" in request.data:
            wf.issue_type_id = request.data["issue_type_id"] or None
        wf.updated_by = request.user
        wf.save()
        if old_type != wf.issue_type_id:
            from django.core.cache import cache

            cache.delete(f"wf:resolved:{wf.project_id}:{old_type}")
        return success_response(_graph_payload(wf))


class WorkflowGraphView(APIView):
    """PUT 整图替换（画布保存，§4.8④）：单事务删旧图+插新图+协议校验+updated_at bump。"""

    permission_classes = [IsAuthenticated]

    def put(self, request, slug, project_id, wf_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        if not ProjectAdminPermission().has_permission(request, self):
            raise AppException("PERM_ROLE_INSUFFICIENT", message="需要项目管理员权限")
        try:
            wf = Workflow.objects.get(id=wf_id, project=project)
        except Workflow.DoesNotExist:
            raise AppException("RESOURCE_NOT_FOUND", message="工作流不存在") from None
        if wf.status != Workflow.Status.DRAFT:
            raise AppException("RESOURCE_STATE_INVALID", message="仅草稿可保存画布（BR-11）")
        # If-Match 乐观锁（§3.3）：不匹配 → 409 RESOURCE_CONFLICT
        if_match = request.headers.get("If-Match")
        # RFC 7232：`*` 匹配任意现值（首存/无缓存场景）
        if if_match and if_match != "*" and if_match != _wf_etag(wf):
            raise AppException("RESOURCE_CONFLICT", message="画布已被他人修改，请刷新后重试")

        states_in = request.data.get("states") or []
        transitions_in = request.data.get("transitions") or []
        self._validate(project, wf, states_in, transitions_in)

        with transaction.atomic():
            WorkflowTransition.objects.filter(workflow=wf).delete()
            WorkflowState.objects.filter(workflow=wf).delete()
            id_map: dict[str, WorkflowState] = {}
            for s in states_in:
                id_map[s["id"]] = WorkflowState.objects.create(
                    workflow=wf, state_id=s["state_id"], is_initial=bool(s.get("is_initial")),
                    layout_x=float(s.get("layout_x") or 0.0),
                    layout_y=float(s.get("layout_y") or 0.0),
                    field_locks=s.get("field_locks") or [],
                )
            for e in transitions_in:
                WorkflowTransition.objects.create(
                    workflow=wf,
                    from_state=id_map[e["from_state_id"]], to_state=id_map[e["to_state_id"]],
                    name=e["name"], guards=e.get("guards") or [],
                    side_effects=e.get("side_effects") or [],
                    approval_flow_id=e.get("approval_flow_id"),
                    sort_order=int(e.get("sort_order") or 1000),
                )
            # ETag 自洽：graph/ 只写子表，事务末同步 bump Workflow.updated_at（§4.8④）
            Workflow.objects.filter(pk=wf.pk).update(updated_at=timezone.now())
            wf.refresh_from_db()
        return success_response(_graph_payload(wf), headers={"ETag": _wf_etag(wf)})

    @staticmethod
    def _validate(project, wf: Workflow, states_in: list[dict], transitions_in: list[dict]) -> None:
        def err(detail: dict, message: str):
            raise AppException("VALIDATION_ERROR", details=[detail], message=message)

        known_state_ids = set(
            State.objects.filter(project=project).values_list("id", flat=True))
        node_ids = set()
        for s in states_in:
            if not s.get("id"):
                err({"field": "states", "code": "REQUIRED"}, "节点 id 必填")
            if s["id"] in node_ids:
                err({"field": "states", "code": "INVALID"}, f"节点 id 重复：{s['id']}")
            node_ids.add(s["id"])
            # BR-04：State 归属同项目（类型专属/通用校验随 R2 守卫轮细化）
            if str(s.get("state_id")) not in {str(x) for x in known_state_ids}:
                err({"field": "states", "code": "DOES_NOT_EXIST"},
                    f"状态 {s.get('state_id')} 不属于本项目")
        for e in transitions_in:
            if not (e.get("name") or "").strip():
                err({"field": "transitions", "code": "REQUIRED"}, "流转边必须命名（§3.2）")
            if e.get("from_state_id") not in node_ids or e.get("to_state_id") not in node_ids:
                err({"field": "transitions", "code": "DOES_NOT_EXIST"}, "边引用了不在图内的节点")
            if e.get("from_state_id") == e.get("to_state_id"):
                err({"field": "transitions", "code": "INVALID"}, "禁止自环边（BR-07）")
            for g in e.get("guards") or []:
                if g.get("type") not in GUARD_TYPES:
                    err({"field": "guards", "code": "NOT_A_CHOICE"},
                        f"未知守卫类型，合法枚举 {list(GUARD_TYPES)}")
            for se in e.get("side_effects") or []:
                if se.get("type") not in EFFECT_TYPES:
                    err({"field": "side_effects", "code": "NOT_A_CHOICE"},
                        f"未知副作用类型，合法枚举 {list(EFFECT_TYPES)}")


class WorkflowPublishView(APIView):
    """POST 发布（§4.6 校验 + 两行模型版本轮转）。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, slug, project_id, wf_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        if not ProjectAdminPermission().has_permission(request, self):
            raise AppException("PERM_ROLE_INSUFFICIENT", message="需要项目管理员权限")
        try:
            wf = Workflow.objects.get(id=wf_id, project=project)
        except Workflow.DoesNotExist:
            raise AppException("RESOURCE_NOT_FOUND", message="工作流不存在") from None
        try:
            wf, prev_id, prev_version = WorkflowService().publish(wf, request.user)
        except TransitionError as exc:
            raise AppException(
                exc.code,
                details=exc.details or None,
                message=exc.message,
            ) from None
        return success_response({
            "id": str(wf.id), "status": wf.status, "version": wf.version,
            "published_at": wf.published_at,
            "archived_previous_id": prev_id,
            "archived_previous_version": prev_version,
        })


class WorkflowArchiveView(APIView):
    """POST 归档（BR-10：只读保留，任务回落兜底链下一级）。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, slug, project_id, wf_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        if not ProjectAdminPermission().has_permission(request, self):
            raise AppException("PERM_ROLE_INSUFFICIENT", message="需要项目管理员权限")
        try:
            wf = Workflow.objects.get(id=wf_id, project=project)
        except Workflow.DoesNotExist:
            raise AppException("RESOURCE_NOT_FOUND", message="工作流不存在") from None
        try:
            wf = WorkflowService().archive(wf, request.user)
        except TransitionError as exc:
            raise AppException(exc.code, details=exc.details or None,
                               message=exc.message) from None
        return success_response({"id": str(wf.id), "status": wf.status, "version": wf.version})


class IssueTransitionsAvailableView(APIView):
    """GET 当前可用流转列表（§4.8① + WF-004 §4.6）：blocked_by 执行态预览计数。"""

    permission_classes = [IsAuthenticated, ProjectPermission]

    def get(self, request, slug, project_id, issue_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        try:
            issue = Issue.objects.select_related("state").get(
                id=issue_id, project=project, deleted_at__isnull=True)
        except Issue.DoesNotExist:
            raise AppException("RESOURCE_NOT_FOUND", message="任务不存在") from None
        svc = WorkflowService()
        wf = svc.resolve_workflow(issue)
        if wf is None:
            return success_response({
                "workflow": None, "current_state": _state_brief(issue.state),
                "available": None, "fallback": True,
            })
        edges = list(
            wf.transitions.filter(from_state__state_id=issue.state_id)
            .select_related("to_state__state", "from_state__state")
            .order_by("sort_order"))
        # WF-004 §3.2（补口轮）：当前状态的字段锁（前端锁定灰显数据源）
        from plane.db.models import WorkflowState

        current_locks = list(WorkflowState.objects.filter(
            workflow=wf, state_id=issue.state_id)
            .values_list("field_locks", flat=True).first() or [])
        from plane.db.services.field_schema import resolve_fields
        from plane.workflow.guards import has_any_role, run_guards

        definitions = {d.field_key: d for d in resolve_fields(project, issue.issue_type_id)}
        available = []
        for e in edges:
            guards = e.guards or []
            requires = []
            for g in guards:
                if g.get("type") == "required_fields":
                    requires += (g.get("config") or {}).get("fields") or []
                if g.get("type") == "estimate_required":
                    requires.append("estimate_minutes")
            # role_allowed 预览（§4.6）：不满足 → allowed:false + deny_reason（不进 blocked_by）
            role_guard = next((g for g in guards if g.get("type") == "role_allowed"), None)
            allowed, deny = True, None
            if role_guard is not None:
                roles = (role_guard.get("config") or {}).get("roles") or []
                if not has_any_role(request.user, project.id, roles):
                    allowed, deny = False, "PERM_TRANSITION_NOT_ALLOWED"
            # blocked_by 执行态轻量求值（计数模式；role_allowed 已单独表达）
            preview_guards = [g for g in guards if g.get("type") != "role_allowed"]
            failures = run_guards(preview_guards, issue=issue, actor=request.user,
                                  to_state=e.to_state.state, payload=None,
                                  definitions=definitions)
            blocked_by = [{"type": f.type, "count": len(f.items)} for f in failures]
            item = {
                "transition_id": str(e.id), "name": e.name,
                "to_state": _state_brief(e.to_state.state),
                "requires_payload": requires,
                "has_approval": e.approval_flow_id is not None,
                "allowed": allowed,
                "blocked_by": blocked_by,
            }
            if deny:
                item["deny_reason"] = deny
            available.append(item)
        return success_response({
            "workflow": {"id": str(wf.id), "name": wf.name, "version": wf.version},
            "current_state": _state_brief(issue.state),
            "current_locks": [lk.get("field") for lk in current_locks if isinstance(lk, dict)],
            "available": available,
            "fallback": False,
        })


class IssueTransitionExecuteView(APIView):
    """POST 执行流转（§4.8②）：200 成功 / 202 审批挂起；错误矩阵见 §4.8③。"""

    permission_classes = [IsAuthenticated, ProjectPermission]

    def post(self, request, slug, project_id, issue_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        try:
            issue = Issue.objects.get(id=issue_id, project=project, deleted_at__isnull=True)
        except Issue.DoesNotExist:
            raise AppException("RESOURCE_NOT_FOUND", message="任务不存在") from None
        to_state_id = request.data.get("to_state_id")
        if not to_state_id:
            raise AppException("VALIDATION_ERROR",
                               details=[{"field": "to_state_id", "code": "REQUIRED"}],
                               message="to_state_id 必填")
        try:
            result = WorkflowService().transition(
                issue_id=issue.id, to_state_id=to_state_id, actor=request.user,
                transition_id=request.data.get("transition_id"),
                guard_payload=request.data.get("guard_payload"),
                force=bool(request.data.get("force")),
                force_comment=request.data.get("comment") or "",
            )
        except ApprovalError as exc:  # WF-002 立案失败（EMPTY_APPROVERS/DISABLED）→ 信封
            raise AppException(exc.code,
                               details=([{"field": exc.field, "code": exc.sub}]
                                        if exc.field and exc.sub else None),
                               message=exc.message) from None
        except TransitionBlockedError as exc:  # TASK-005 409 BLOCKED（blockers[] 同 §4 格式）
            raise AppException("RESOURCE_TRANSITION_BLOCKED",
                               details=[{"field": "blockers", "code": "INVALID",
                                         "message": ", ".join(b["issue_key"] for b in exc.blockers)}],
                               message=str(exc)) from None
        except TransitionError as exc:
            raise AppException(exc.code, details=exc.details or None,
                               message=exc.message) from None
        if result.pending_approval is not None:
            inst = result.pending_approval
            return success_response({
                "pending_approval": {
                    "instance_id": str(inst.id),
                    "status": inst.status,
                    "current_level": inst.current_level,
                    "flow_name": inst.flow_snapshot["name"],
                },
            }, status_code=status.HTTP_202_ACCEPTED)
        issue.refresh_from_db()
        return success_response({
            "issue": {"id": str(issue.id), "state": _state_brief(issue.state)},
            "applied_transition": result.edge.name if result.edge else None,
        })


def _state_brief(s: State | None) -> dict | None:
    if s is None:
        return None
    return {"id": str(s.id), "name": s.name, "group": s.group, "color": s.color}
