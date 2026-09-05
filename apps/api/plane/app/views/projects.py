from django.db import transaction
from rest_framework import status
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.response import Response

from plane.app.permissions import IsAuthenticated
from plane.app.serializers.project import ProjectSerializer, ProjectWriteSerializer, StateSerializer
from plane.app.serializers.project_member import ProjectSearchQuerySerializer
from plane.app.views._access import get_project_or_404, get_workspace_or_404
from plane.base.exception import AppException
from plane.base.response import created_response, success_response
from plane.db.models import Project, ProjectMember, State
from plane.db.models.roles import ProjectRole, WorkspaceRole
from plane.db.seeds.project_states import seed_project_states

#: 兼容既有 import 路径；唯一定义在 plane.app.views._access
_get_workspace_or_404 = get_workspace_or_404
_get_project_or_404 = get_project_or_404


def _serialize_project(project, user, *, is_favorite: bool = False):
    """组装响应：annotate 统计 + 默认 state id + 收藏注水"""
    total_members = ProjectMember.objects.filter(project=project, is_active=True).count()
    total_issues = project.issues.filter(deleted_at__isnull=True).count() if hasattr(project, "issues") else 0
    default_state = State.objects.filter(project=project, is_default=True, deleted_at__isnull=True).first()
    data = ProjectSerializer(project).data
    data["total_members"] = total_members
    data["total_issues"] = total_issues
    data["default_state_id"] = str(default_state.id) if default_state else None
    data["is_favorite"] = is_favorite
    return data


class ProjectListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ProjectSerializer

    def list(self, request, *args, **kwargs):
        ws, _ = _get_workspace_or_404(kwargs["slug"], request.user)
        # PROJ-002 §4.2.1：搜索（?q=）+ 状态筛选（?status=）+ 收藏过滤 / 置顶（?favorite / ?favorite_first）
        from plane.db.services.project_query import list_for_user
        s = ProjectSearchQuerySerializer(data=request.query_params)
        s.is_valid(raise_exception=True)
        q = s.validated_data.get("q") or None
        status_param = s.validated_data.get("status")
        favorite_only = bool(s.validated_data.get("favorite"))
        favorite_first = bool(s.validated_data.get("favorite_first"))

        qs, favorite_ids, visible_favorite_count = list_for_user(
            user=request.user,
            workspace=ws,
            q=q,
            status=status_param,
            favorite_only=favorite_only,
            favorite_first=favorite_first,
        )
        # 简单分页：默认 20，未引入游标（与现有 sprint-0 列表一致）。
        # PROJ-002 §4.2.1 meta 字段约定保留在响应中以便后续接入游标分页。
        try:
            per_page = min(int(request.query_params.get("per_page", 50)), 100)
        except (TypeError, ValueError):
            per_page = 50
        try:
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except (TypeError, ValueError):
            offset = 0
        page_qs = qs[offset: offset + per_page]
        data = [_serialize_project(p, request.user,
                                   is_favorite=str(p.id) in favorite_ids)
                for p in page_qs]
        meta = {
            "count": len(data),
            "total_count": qs.count(),
            "favorite_count": visible_favorite_count,
            "page": (offset // per_page) + 1 if per_page else 1,
            "per_page": per_page,
            "next_page_results": (offset + per_page) < qs.count(),
            "prev_page_results": offset > 0,
            "next_cursor": None,
            "prev_cursor": None,
            "total_pages": (qs.count() + per_page - 1) // per_page if per_page else 1,
        }
        return success_response(data, meta=meta)

    def create(self, request, *args, **kwargs):
        ws, member = _get_workspace_or_404(kwargs["slug"], request.user)
        if member.role < WorkspaceRole.MEMBER:
            # sprint-0 用了不存在的 PERM_WORKSPACE_MEMBER_REQUIRED；注册表 §8.3 无此码，
            # 收口到 PERM_ROLE_INSUFFICIENT（详见 INFRA-004 §4.2 角色不足映射）。
            raise AppException("PERM_ROLE_INSUFFICIENT")
        s = ProjectWriteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        identifier = s.validated_data["identifier"]
        if Project.objects.filter(workspace_id=ws.id, identifier=identifier, deleted_at__isnull=True).exists():
            # PROJ-001 §3.2：生成可用建议供前端「试试 XXX」一键采纳。
            # sprint-0 把 suggestion 塞在 meta；新合约把字段塞进 details[] 条目，
            # 前端从 error.details[0].suggestion 读取（见 projects-list.tsx）。
            suggestion, sfx = identifier, "A"
            while Project.objects.filter(workspace_id=ws.id, identifier=suggestion, deleted_at__isnull=True).exists():
                suggestion = identifier + sfx
                sfx = chr(ord(sfx) + 1)
            raise AppException(
                "RESOURCE_ALREADY_EXISTS",
                message=f"标识符 {identifier} 已被占用，请换一个",
                details=[{
                        "field": "identifier",
                        "code": "UNIQUE",
                        "message": f"标识符 {identifier} 已被占用",
                        "suggestion": suggestion,
                    }],
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
        return created_response(
            _serialize_project(project, request.user),
            location=request.build_absolute_uri(f"/api/v1/workspaces/{ws.slug}/projects/{project.id}/"),
        )


class ProjectDetailView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ProjectSerializer

    def get_object(self):
        project, _, _ = _get_project_or_404(self.kwargs["slug"], self.kwargs["project_id"], self.request.user)
        return project

    def retrieve(self, request, *args, **kwargs):
        """GET 详情 —— 统一信封（api-conventions §4；裸 JSON 的 status 字段会与信封 status 键冲突）。"""
        project = self.get_object()
        return success_response(_serialize_project(project, request.user))

    def update(self, request, *args, **kwargs):
        project, _, member = _get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        # PATCH 需 PROJ_ADMIN 或 WS_ADMIN+
        ws_member_role = _get_workspace_or_404(kwargs["slug"], request.user)[1].role
        proj_admin = project.current_user_role >= ProjectRole.ADMIN
        ws_admin = ws_member_role >= WorkspaceRole.ADMIN
        if not (proj_admin or ws_admin):
            raise AppException("PERM_PROJECT_ADMIN_REQUIRED")
        s = ProjectWriteSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        if "name" in s.validated_data:
            project.name = s.validated_data["name"]
        if "description" in s.validated_data:
            project.description = s.validated_data["description"]
        # identifier 不可修改（PROJ-001 §4.3.5 ID-7：read_only）
        project.save()
        return success_response(_serialize_project(project, request.user))

    def destroy(self, request, *args, **kwargs):
        """DELETE —— 软删除（deleted_at）+ PROJ_ADMIN/WS_ADMIN 角色校验（PROJ-001 §4.2 第 5 行）。"""
        project, _, _ = _get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        ws_member_role = _get_workspace_or_404(kwargs["slug"], request.user)[1].role
        if not (project.current_user_role >= ProjectRole.ADMIN or ws_member_role >= WorkspaceRole.ADMIN):
            raise AppException("PERM_PROJECT_ADMIN_REQUIRED")
        project.soft_delete(actor_id=request.user.id)
        return Response(status=status.HTTP_204_NO_CONTENT)  # 204 禁带 body（Vite proxy 对 204+body 挂起）


class ProjectStateListView(ListCreateAPIView):
    """GET /api/v1/.../projects/{project_id}/states/ —— 看板列定义来源"""

    permission_classes = [IsAuthenticated]
    serializer_class = StateSerializer

    def list(self, request, *args, **kwargs):
        project, _, _ = _get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        states = State.objects.filter(project=project, deleted_at__isnull=True).order_by("sort_order")
        # 默认排除 cancelled（不渲染为看板列，sprint-overview §2.1 / TC-PROJ1-006）；
        # ?include_cancelled=1 供创建弹窗状态下拉取全四态（TASK-001 §3.2.2）
        if request.query_params.get("include_cancelled") not in ("1", "true"):
            states = states.exclude(group=State.Group.CANCELLED)
        return success_response(StateSerializer(states, many=True).data)
