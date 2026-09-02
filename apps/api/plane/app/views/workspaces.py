from django.db.models import Count, Q
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateAPIView

from plane.app.permissions import IsAuthenticated
from plane.app.serializers.auth import WorkspaceSerializer
from plane.app.serializers.common import envelope
from plane.db.models import Workspace, WorkspaceMember
from plane.db.models.roles import WorkspaceRole


def _get_workspace_or_404(slug, user):
    """三防护（rbac §5）：UI 隐藏 / API 403-NotFound / DB 层 404。"""
    try:
        ws = Workspace.objects.get(slug=slug, deleted_at__isnull=True)
    except Workspace.DoesNotExist:
        raise NotFound("RESOURCE_NOT_FOUND")
    member = WorkspaceMember.objects.filter(workspace=ws, member=user, is_active=True).first()
    if member is None:
        raise NotFound("RESOURCE_NOT_FOUND")
    return ws, member


class WorkspaceListCreateView(ListCreateAPIView):
    """GET /api/v1/workspaces/ 列表（仅我所在）/ POST 创建"""

    permission_classes = [IsAuthenticated]
    serializer_class = WorkspaceSerializer

    def get_queryset(self):
        return (
            Workspace.objects.filter(workspace_member__member=self.request.user, workspace_member__is_active=True)
            .annotate(total_projects=Count("projects", filter=Q(projects__deleted_at__isnull=True)))
            .order_by("-created_at")
        )

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        data = []
        for w in qs:
            member = WorkspaceMember.objects.get(workspace=w, member=request.user)
            data.append(
                {
                    "id": str(w.id),
                    "name": w.name,
                    "slug": w.slug,
                    "logo": w.logo,
                    "role": member.role,
                    "current_user_role": member.role,
                    "total_projects": w.total_projects,
                }
            )
        return envelope(True, data)

    def create(self, request, *args, **kwargs):
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        from django.utils.text import slugify

        base = slugify(s.validated_data["name"]) or "workspace"
        slug, n = base, 2
        while Workspace.objects.filter(slug=slug, deleted_at__isnull=True).exists():
            slug = f"{base}-{n}"
            n += 1
        ws = Workspace.objects.create(
            name=s.validated_data["name"],
            slug=slug,
            description=s.validated_data.get("description", ""),
            owner=request.user,
            created_by=request.user,
        )
        WorkspaceMember.objects.create(
            workspace=ws, member=request.user, role=WorkspaceRole.OWNER, created_by=request.user
        )
        return envelope(True, WorkspaceSerializer(ws).data, http_status=201)


class WorkspaceDetailView(RetrieveUpdateAPIView):
    """GET/PATCH /api/v1/workspaces/{slug}/"""

    permission_classes = [IsAuthenticated]
    serializer_class = WorkspaceSerializer

    def get_object(self):
        ws, member = _get_workspace_or_404(self.kwargs["slug"], self.request.user)
        ws.current_user_role = member.role
        return ws

    def retrieve(self, request, *args, **kwargs):
        """GET 详情 —— 统一信封（api-conventions §4；与 Project retrieve 同款修复）。"""
        return envelope(True, self.get_serializer(self.get_object()).data)
        return ws

    def update(self, request, *args, **kwargs):
        ws, member = _get_workspace_or_404(kwargs["slug"], request.user)
        if member.role < WorkspaceRole.ADMIN:
            return envelope(False, None, {"code": "PERM_WORKSPACE_ADMIN_REQUIRED"}, http_status=403)
        s = self.get_serializer(ws, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        return envelope(True, s.data)
