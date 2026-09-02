from django.db import transaction
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView

from plane.app.permissions import IsAuthenticated
from plane.app.serializers.common import envelope
from plane.app.serializers.project import ProjectSerializer, ProjectWriteSerializer, StateSerializer
from plane.app.views.workspaces import _get_workspace_or_404
from plane.db.models import Project, ProjectMember, State
from plane.db.models.roles import ProjectRole, WorkspaceRole
from plane.db.seeds.project_states import seed_project_states


def _get_project_or_404(slug, project_id, user):
    ws, member = _get_workspace_or_404(slug, user)
    try:
        project = Project.objects.get(id=project_id, workspace_id=ws.id, deleted_at__isnull=True)
    except Project.DoesNotExist:
        raise NotFound("RESOURCE_NOT_FOUND")
    pm = ProjectMember.objects.filter(project=project, member=user, is_active=True).first()
    if pm is None:
        # WS_OWNER/ADMIN 隐式 = 项目 ADMIN（INFRA-003 §4.5 决策）
        if member.role < WorkspaceRole.ADMIN:
            raise NotFound("RESOURCE_NOT_FOUND")
        proj_role = ProjectRole.ADMIN
    else:
        proj_role = pm.role
    project.current_user_role = proj_role
    return project, pm, member


def _serialize_project(project, user):
    """组装响应：annotate 统计 + 默认 state id"""
    total_members = ProjectMember.objects.filter(project=project, is_active=True).count()
    total_issues = project.issues.filter(deleted_at__isnull=True).count() if hasattr(project, "issues") else 0
    default_state = State.objects.filter(project=project, is_default=True, deleted_at__isnull=True).first()
    data = ProjectSerializer(project).data
    data["total_members"] = total_members
    data["total_issues"] = total_issues
    data["default_state_id"] = str(default_state.id) if default_state else None
    return data


class ProjectListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ProjectSerializer

    def list(self, request, *args, **kwargs):
        ws, _ = _get_workspace_or_404(kwargs["slug"], request.user)
        # 列出所有项目；DB 层 ProjectMember 是补充过滤（P0 仅 WS_OWNER/ADMIN 可见全员，P1 再做数据隔离）
        qs = Project.objects.filter(workspace_id=ws.id, deleted_at__isnull=True).order_by("-created_at")
        return envelope(True, [_serialize_project(p, request.user) for p in qs])

    def create(self, request, *args, **kwargs):
        ws, member = _get_workspace_or_404(kwargs["slug"], request.user)
        if member.role < WorkspaceRole.MEMBER:
            return envelope(False, None, {"code": "PERM_WORKSPACE_MEMBER_REQUIRED"}, http_status=403)
        s = ProjectWriteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        identifier = s.validated_data["identifier"]
        if Project.objects.filter(workspace_id=ws.id, identifier=identifier, deleted_at__isnull=True).exists():
            return envelope(
                False,
                None,
                {"code": "PROJECT_IDENTIFIER_EXISTS", "message": f"标识符 {identifier} 已被占用，请换一个"},
                http_status=409,
            )
        with transaction.atomic():
            project = Project.objects.create(
                workspace_id=ws.id,
                name=s.validated_data["name"],
                description=s.validated_data.get("description", ""),
                identifier=identifier,
                created_by=request.user,
            )
            # 创建者写一条 ProjectMember(ADMIN)
            ProjectMember.objects.create(
                project=project,
                workspace_id=ws.id,
                member=request.user,
                role=ProjectRole.ADMIN,
                created_by=request.user,
            )
            # 种子四态（待办/进行中/已完成/已取消）
            seed_project_states(project)
        return envelope(True, _serialize_project(project, request.user), http_status=201)


class ProjectDetailView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ProjectSerializer

    def get_object(self):
        project, _, _ = _get_project_or_404(self.kwargs["slug"], self.kwargs["project_id"], self.request.user)
        return project

    def update(self, request, *args, **kwargs):
        project, _, member = _get_project_or_404(kwargs["slug"], kwargs["project_id"], request)
        # PATCH 需 PROJ_ADMIN 或 WS_ADMIN+
        ws_member_role = _get_workspace_or_404(kwargs["slug"], request.user)[1].role
        proj_admin = project.current_user_role >= ProjectRole.ADMIN
        ws_admin = ws_member_role >= WorkspaceRole.ADMIN
        if not (proj_admin or ws_admin):
            return envelope(False, None, {"code": "PERM_PROJECT_ADMIN_REQUIRED"}, http_status=403)
        s = ProjectWriteSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        if "name" in s.validated_data:
            project.name = s.validated_data["name"]
        if "description" in s.validated_data:
            project.description = s.validated_data["description"]
        # identifier 不可修改（PROJ-001 §4.3.5 ID-7：read_only）
        project.save()
        return envelope(True, _serialize_project(project, request.user))


class ProjectStateListView(ListCreateAPIView):
    """GET /api/v1/.../projects/{project_id}/states/ —— 看板列定义来源"""

    permission_classes = [IsAuthenticated]
    serializer_class = StateSerializer

    def list(self, request, *args, **kwargs):
        project, _, _ = _get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        states = State.objects.filter(project=project, deleted_at__isnull=True).order_by("sort_order")
        # 排除 cancelled（仅用于状态机，不渲染为看板列 —— sprint-overview §2.1）

        visible = states.exclude(group=State.Group.CANCELLED)
        return envelope(True, StateSerializer(visible, many=True).data)
