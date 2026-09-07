"""项目模板视图（PROJ-003 §4.2.4——Sprint-5 T5-07）。

端点（工作空间作用域；内置模板 workspace=null 全空间可见）：
  GET    …/workspaces/{slug}/project-templates/       列表（内置 + 本空间自定义）
  POST   …/workspaces/{slug}/project-templates/       创建自定义（WS_ADMIN+，BR-10）
  PATCH  …/workspaces/{slug}/project-templates/{id}/  更新（内置不可改）
  DELETE …/workspaces/{slug}/project-templates/{id}/  删除（内置不可删，软删）
"""
from __future__ import annotations

from rest_framework.exceptions import NotFound
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated
from plane.app.views._access import get_workspace_or_404
from plane.base.exception import AppException
from plane.base.response import created_response, success_response
from plane.db.models import ProjectTemplate, WorkspaceRole

_SNAPSHOT_FIELDS = ("states_snapshot", "labels_snapshot", "fields_snapshot", "folders_snapshot")


def _row(t: ProjectTemplate) -> dict:
    return {
        "id": str(t.id),
        "name": t.name,
        "description": t.description,
        "is_builtin": t.is_builtin,
        "workspace_id": str(t.workspace_id) if t.workspace_id else None,
        "states_snapshot": t.states_snapshot,
        "labels_snapshot": t.labels_snapshot,
        "fields_snapshot": t.fields_snapshot,
        "folders_snapshot": t.folders_snapshot,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


def _require_ws_admin(member) -> None:
    if member.role < WorkspaceRole.ADMIN:
        raise AppException("PERM_ROLE_INSUFFICIENT", message="需要工作空间管理员权限")


def _get_template(ws, template_id) -> ProjectTemplate:
    t = ProjectTemplate.objects.filter(pk=template_id).first()
    if t is None or (t.workspace_id and t.workspace_id != ws.id):
        raise NotFound("RESOURCE_NOT_FOUND") from None
    return t


def _validate_snapshots(data: dict) -> None:
    for field in _SNAPSHOT_FIELDS:
        value = data.get(field)
        if value is not None and not isinstance(value, list):
            raise AppException(
                "VALIDATION_ERROR", message=f"{field} 必须为数组快照",
                details=[{"field": field, "code": "INVALID",
                          "message": "快照字段必须为数组（模板为值快照非引用）"}])
    states = data.get("states_snapshot")
    if states is not None:
        for s in states:
            if not isinstance(s, dict) or "name" not in s or "group" not in s:
                raise AppException(
                    "VALIDATION_ERROR", message="states_snapshot 条目需含 name/group",
                    details=[{"field": "states_snapshot", "code": "INVALID",
                              "message": "每条状态需 {name, group, color?, sort_order?}"}])


class ProjectTemplateListCreateView(APIView):
    """GET / POST —— 内置 + 自定义模板。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        ws, _ = get_workspace_or_404(slug, request.user)
        rows = (ProjectTemplate.objects
                .filter(workspace__isnull=True) | ProjectTemplate.objects.filter(workspace=ws)
                ).order_by("-is_builtin", "name")
        data = [_row(t) for t in rows]
        return success_response(data, meta={"count": len(data), "total_count": len(data)})

    def post(self, request, slug):
        ws, member = get_workspace_or_404(slug, request.user)
        _require_ws_admin(member)
        name = str(request.data.get("name") or "").strip()
        if not name:
            raise AppException(
                "VALIDATION_ERROR", message="模板名不能为空",
                details=[{"field": "name", "code": "INVALID", "message": "name 必填"}])
        if ProjectTemplate.objects.filter(workspace=ws, name=name,
                                          is_builtin=False).exists():
            raise AppException(
                "RESOURCE_ALREADY_EXISTS", message="同空间已有同名模板",
                details=[{"field": "name", "code": "UNIQUE",
                          "message": f"模板名 {name} 已存在"}])
        _validate_snapshots(request.data)
        t = ProjectTemplate.objects.create(
            workspace=ws, name=name,
            description=str(request.data.get("description") or ""),
            states_snapshot=request.data.get("states_snapshot") or [],
            labels_snapshot=request.data.get("labels_snapshot") or [],
            fields_snapshot=request.data.get("fields_snapshot") or [],
            folders_snapshot=request.data.get("folders_snapshot") or [],
            created_by=request.user,
        )
        return created_response(_row(t), location=request.build_absolute_uri(
            f"/api/v1/workspaces/{ws.slug}/project-templates/{t.id}/"))


class ProjectTemplateDetailView(APIView):
    """PATCH / DELETE —— 内置不可改不可删（BR-10）。"""

    permission_classes = [IsAuthenticated]

    def patch(self, request, slug, template_id):
        ws, member = get_workspace_or_404(slug, request.user)
        _require_ws_admin(member)
        t = _get_template(ws, template_id)
        if t.is_builtin:
            raise AppException("PERM_DENIED", message="内置模板不可修改")
        _validate_snapshots(request.data)
        fields = ["updated_at"]
        if "name" in request.data:
            t.name = str(request.data["name"]).strip()
            fields.append("name")
        if "description" in request.data:
            t.description = str(request.data.get("description") or "")
            fields.append("description")
        for field in _SNAPSHOT_FIELDS:
            if field in request.data:
                setattr(t, field, request.data[field] or [])
                fields.append(field)
        t.updated_by = request.user
        t.save(update_fields=fields + ["updated_by"])
        return success_response(_row(t))

    def delete(self, request, slug, template_id):
        ws, member = get_workspace_or_404(slug, request.user)
        _require_ws_admin(member)
        t = _get_template(ws, template_id)
        if t.is_builtin:
            raise AppException("PERM_DENIED", message="内置模板不可删除")
        t.delete()  # BaseModel 默认软删
        from rest_framework import status
        from rest_framework.response import Response
        return Response(status=status.HTTP_204_NO_CONTENT)
