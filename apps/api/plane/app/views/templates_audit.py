"""模板库与审批审计端点（WF-005 §4.4 / WF-006 §4.5）。

模板：WS 级 CRUD + 预设种子 + 项目域下发/确认/申请；审计：检索 + 导出
（流式 CSV 代理 + 哈希链校验端点）。权限：模板面 workspace.setting.manage
（WS_ADMIN+）；审计读项目成员、approval.audit 门槛（rbac 附录 B）。
"""
from __future__ import annotations

from django.http import HttpResponse
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated, ProjectAdminPermission
from plane.app.views._access import get_project_or_404, get_workspace_or_404
from plane.base.exception import AppException
from plane.base.response import created_response, success_response
from plane.db.models import TemplateDistribution, WorkflowTemplate
from plane.workflow.audit_service import (
    append_audit_event,
    export_rows_to_csv,
    query_events,
    verify_chain,
)
from plane.workflow.template_service import (
    TemplateError,
    confirm_distribution,
    distribute,
    request_unlock,
    seed_builtin_templates,
)


def _tpl_err(exc: TemplateError) -> AppException:
    details = ([{"code": exc.sub}] if exc.sub else None)
    return AppException(exc.code, message=exc.message, details=details)


class WorkflowTemplateListCreateView(APIView):
    """GET 列表（含预设种子幂等重灌）/ POST 自定义保存（WS 级）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        ws, member = get_workspace_or_404(slug, request.user)
        seed_builtin_templates(ws)  # 幂等：首次访问补种预设四套（§2.4）
        rows = WorkflowTemplate.objects.filter(workspace=ws).order_by(
            "-is_builtin", "-updated_at")
        return success_response([{
            "id": str(t.id), "name": t.name, "description": t.description,
            "version": t.version, "status": t.status, "is_builtin": t.is_builtin,
            "graph_snapshot": t.graph_snapshot, "updated_at": t.updated_at,
        } for t in rows])

    def post(self, request, slug):
        ws, _ = get_workspace_or_404(slug, request.user)
        from plane.app.permissions import WorkspaceAdminPermission

        if not WorkspaceAdminPermission().has_permission(request, self):
            raise AppException("PERM_ROLE_INSUFFICIENT",
                               message="workspace.setting.manage 权限不足")
        name = (request.data.get("name") or "").strip()
        if not name:
            raise AppException("VALIDATION_ERROR",
                               details=[{"field": "name", "code": "REQUIRED"}],
                               message="模板名称必填")
        if WorkflowTemplate.objects.filter(
                workspace=ws, name=name, deleted_at__isnull=True).exists():
            raise AppException("RESOURCE_ALREADY_EXISTS", message="同名模板已存在")
        tpl = WorkflowTemplate.objects.create(
            workspace=ws, name=name, description=request.data.get("description") or "",
            status="draft", is_builtin=False,
            graph_snapshot=request.data.get("graph_snapshot") or {},
            created_by=request.user)
        return created_response(
            {"id": str(tpl.id), "name": tpl.name, "version": tpl.version},
            location=f"/api/v1/workspaces/{slug}/workflow-templates/{tpl.id}/")


class WorkflowTemplateDetailView(APIView):
    """GET / PATCH（is_builtin 403 保护 BR-07）/ DELETE（软删）。"""

    permission_classes = [IsAuthenticated]

    def _get(self, slug, tid, user):
        ws, _ = get_workspace_or_404(slug, user)
        try:
            return ws, WorkflowTemplate.objects.get(id=tid, workspace=ws, deleted_at__isnull=True)
        except WorkflowTemplate.DoesNotExist:
            raise AppException("RESOURCE_NOT_FOUND", message="模板不存在") from None

    def get(self, request, slug, tid):
        _, tpl = self._get(slug, tid, request.user)
        return success_response({
            "id": str(tpl.id), "name": tpl.name, "description": tpl.description,
            "version": tpl.version, "status": tpl.status, "is_builtin": tpl.is_builtin,
            "graph_snapshot": tpl.graph_snapshot})

    def patch(self, request, slug, tid):
        from plane.app.permissions import WorkspaceAdminPermission

        if not WorkspaceAdminPermission().has_permission(request, self):
            raise AppException("PERM_ROLE_INSUFFICIENT")
        _, tpl = self._get(slug, tid, request.user)
        if tpl.is_builtin:
            raise AppException("PERM_DENIED", message="预设模板不可修改（BR-07）")
        for f in ("name", "description", "graph_snapshot"):
            if f in request.data:
                setattr(tpl, f, request.data[f])
        tpl.updated_by = request.user
        tpl.save()
        return success_response({"id": str(tpl.id)})

    def delete(self, request, slug, tid):
        from plane.app.permissions import WorkspaceAdminPermission

        if not WorkspaceAdminPermission().has_permission(request, self):
            raise AppException("PERM_ROLE_INSUFFICIENT")
        _, tpl = self._get(slug, tid, request.user)
        if tpl.is_builtin:
            raise AppException("PERM_DENIED", message="预设模板不可删除（BR-07）")
        tpl.deleted_at = tpl.updated_at
        tpl.save(update_fields=["deleted_at", "updated_at"])
        from django.http import HttpResponse

        return HttpResponse(status=204)


class TemplateDistributeView(APIView):
    """POST …/projects/{pid}/workflow-templates/ {template_id, issue_type_id?, state_mapping?}
    ——两步下发：body 带 confirm=true 时一步确认实例化（开发便捷口）。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, slug, project_id):
        if not ProjectAdminPermission().has_permission(request, self):
            raise AppException("PERM_ROLE_INSUFFICIENT")
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        tpl_id = (request.data or {}).get("template_id")
        if not tpl_id:
            raise AppException("VALIDATION_ERROR",
                               details=[{"field": "template_id", "code": "REQUIRED"}])
        try:
            tpl = WorkflowTemplate.objects.get(id=tpl_id, workspace=project.workspace,
                                               deleted_at__isnull=True)
        except WorkflowTemplate.DoesNotExist:
            raise AppException("RESOURCE_NOT_FOUND", message="模板不存在") from None
        dist = distribute(template=tpl, project=project, actor=request.user)
        out = {"distribution_id": str(dist.id), "status": dist.status,
               "template_version": dist.template_version}
        if (request.data or {}).get("confirm"):
            wf = confirm_distribution(
                dist=dist, project=project, actor=request.user,
                issue_type=request.data.get("issue_type_id"),
                state_mapping=(request.data or {}).get("state_mapping"))
            out.update({"status": "active", "workflow_id": str(wf.id),
                        "workflow_status": wf.status})
        return success_response(out)


class TemplateUnlockRequestView(APIView):
    """POST …/workflow-templates/unlock-requests/ {distribution_id, kind, reason}。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        dist_id = (request.data or {}).get("distribution_id")
        try:
            dist = TemplateDistribution.objects.get(id=dist_id, project=project)
        except TemplateDistribution.DoesNotExist:
            raise AppException("RESOURCE_NOT_FOUND", message="下发记录不存在") from None
        try:
            req = request_unlock(
                dist=dist, project=project, actor=request.user,
                kind=(request.data or {}).get("kind") or "unlock",
                reason=(request.data or {}).get("reason") or "")
        except TemplateError as exc:
            raise _tpl_err(exc) from None
        return created_response(
            {"id": str(req.id), "kind": req.kind, "status": req.status},
            location=f"/api/v1/workspaces/{slug}/projects/{project_id}/"
                     f"workflow-templates/unlock-requests/{req.id}/")


class ApprovalAuditView(APIView):
    """GET …/approval-audit/ ——检索（instance/actor/type 过滤，成员可读）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        qs = query_events(
            project,
            instance_id=request.query_params.get("instance_id") or None,
            actor_id=request.query_params.get("actor_id") or None,
            type_=request.query_params.get("type") or None)
        rows = list(qs[:500])  # 检索页上限（导出走 CSV 端点）
        return success_response([{
            "id": e.id, "created_at": e.created_at, "type": e.type,
            "instance_id": str(e.instance_id) if e.instance_id else None,
            "actor_id": str(e.actor_id) if e.actor_id else None,
            "payload": e.payload, "event_hash": e.event_hash,
        } for e in rows], meta={"count": len(rows)})


class ApprovalAuditExportView(APIView):
    """GET …/approval-audit/export/ ——CSV 流式下载（approval.audit 门槛；
    导出事实入哈希链 approval.exported，BR-01/§2.3）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        if not ProjectAdminPermission().has_permission(request, self):
            raise AppException("PERM_ROLE_INSUFFICIENT",
                               message="approval.audit 权限不足（PROJ_ADMIN+）")
        qs = query_events(
            project,
            instance_id=request.query_params.get("instance_id") or None,
            actor_id=request.query_params.get("actor_id") or None,
            type_=request.query_params.get("type") or None)
        append_audit_event(
            project=project, type_="approval.exported", instance=None,
            actor=request.user,
            payload={"rows": qs.count(), "query": dict(request.query_params)})
        csv_text = export_rows_to_csv(qs)
        resp = HttpResponse(csv_text, content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = (
            f'attachment; filename="approval-audit-{project.id}.csv"')
        return resp


class ApprovalAuditVerifyView(APIView):
    """GET …/approval-audit/verify/ ——哈希链完整性校验（§2.2 篡改检测）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        broken = verify_chain(project)
        return success_response({
            "chain_intact": not broken, "broken_event_ids": broken})
