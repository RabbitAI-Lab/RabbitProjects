"""项目成员 / 收藏 / 归档视图（PROJ-002 §4.2）。

端点（嵌套在 project 下，全部强制尾斜杠）：
  GET    .../projects/{id}/members/        列表 + 搜索
  POST   .../projects/{id}/members/        批量添加（≤ 20）
  PATCH  .../projects/{id}/members/{mid}/  角色调整
  DELETE .../projects/{id}/members/{mid}/  移除
  POST   .../projects/{id}/favorite/       收藏（幂等）
  DELETE .../projects/{id}/favorite/       取消收藏（幂等）
  POST   .../projects/{id}/archive/        归档（幂等）
  DELETE .../projects/{id}/archive/        恢复（幂等）

权限：
  members:  read = project.member.read (VIEWER) / write = project.member.manage (ADMIN)
  favorite: project.favorite (VIEWER，rbac §7.4 隐式 WS_ADMIN+ 全部通过)
  archive:  project.archive (ADMIN)
"""
from __future__ import annotations

from django.db.models import Count, OuterRef, Subquery
from django.db.models.functions import Coalesce
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListCreateAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated
from plane.app.serializers.project_member import (
    ProjectMemberBulkAddSerializer,
    ProjectMemberRoleChangeSerializer,
    ProjectMemberSerializer,
)
from plane.app.views._access import get_project_or_404
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.db.models import Issue, Project, ProjectMember
from plane.db.models.roles import ProjectRole
from plane.db.services.project_member import ProjectMemberService


class _ScopedHelper:
    """项目作用域解析（view 内部复用）"""

    @staticmethod
    def resolve(slug, project_id, user):
        """返回 (project, pm_or_None, ws_member)；不可见一律 404。"""
        return get_project_or_404(slug, project_id, user)

    @staticmethod
    def ensure_can_manage(project, user) -> None:
        """project.member.manage 角色校验 —— 隐式 WS_ADMIN+ 由 get_project_or_404 已提升。"""
        role = getattr(project, "current_user_role", 0) or 0
        if role < ProjectRole.ADMIN:
            raise AppException("PERM_ROLE_INSUFFICIENT",
                               message="需要项目管理员权限")


# ─────────────────────────────────────────────────────────────────────────
# 项目成员：list + bulk create
# ─────────────────────────────────────────────────────────────────────────
class ProjectMemberListCreateView(ListCreateAPIView):
    """GET .../projects/{id}/members/  ｜  POST .../projects/{id}/members/"""

    permission_classes = [IsAuthenticated]
    serializer_class = ProjectMemberSerializer

    def get_project(self, slug, project_id, user):
        return _ScopedHelper.resolve(slug, project_id, user)

    def list(self, request, *args, **kwargs):
        project, _, _ = self.get_project(kwargs["slug"], kwargs["project_id"], request.user)
        # rbac §6.2：仅 visible 项目可读其成员（get_project_or_404 已隐式 ADMIN）
        # assigned_issue_count：本项目中指派给该成员的任务数（移除确认弹窗
        # 「其名下 N 个任务指派将保留」需要，PROJ-002 §3.2 BR-07）；子查询注解，无 N+1。
        assigned_count = (
            Issue.objects.filter(
                project_id=OuterRef("project_id"),
                assignees=OuterRef("member_id"),
                deleted_at__isnull=True,
            )
            .values("project_id")
            .annotate(n=Count("*"))
            .values("n")[:1]
        )
        qs = (
            ProjectMember.objects.filter(project=project, is_active=True, deleted_at__isnull=True)
            .select_related("member")
            .annotate(assigned_issue_count=Coalesce(Subquery(assigned_count), 0))
            .order_by("-role", "created_at")
        )
        return success_response(ProjectMemberSerializer(qs, many=True).data)

    def create(self, request, *args, **kwargs):
        project, _, _ = self.get_project(kwargs["slug"], kwargs["project_id"], request.user)
        _ScopedHelper.ensure_can_manage(project, request.user)
        s = ProjectMemberBulkAddSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        svc = ProjectMemberService()
        results = svc.add_members(
            project=project,
            actor=request.user,
            member_ids=[str(m) for m in s.validated_data["member_ids"]],
            role=s.validated_data["role"],
        )
        added = sum(1 for r in results if r["status"] == "added")
        skipped = sum(1 for r in results if r["status"] == "skipped")
        failed = sum(1 for r in results if r["status"] == "failed")
        return success_response(results, meta={"summary": {"added": added,
                                                            "skipped": skipped,
                                                            "failed": failed}})


# ─────────────────────────────────────────────────────────────────────────
# 项目成员：detail（PATCH 角色 / DELETE 移除）
# ─────────────────────────────────────────────────────────────────────────
class ProjectMemberDetailView(APIView):
    """PATCH / DELETE .../projects/{id}/members/{member_id}/"""

    permission_classes = [IsAuthenticated]

    def _get_member(self, project, member_id):
        try:
            m = ProjectMember.objects.select_related("member").get(
                id=member_id,
                project=project,
                deleted_at__isnull=True,
            )
        except ProjectMember.DoesNotExist:
            raise NotFound("RESOURCE_NOT_FOUND") from None
        return m

    def patch(self, request, *args, **kwargs):
        project, _, _ = _ScopedHelper.resolve(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        _ScopedHelper.ensure_can_manage(project, request.user)
        s = ProjectMemberRoleChangeSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        member = self._get_member(project, kwargs["member_id"])
        svc = ProjectMemberService()
        member = svc.change_role(
            project=project,
            member=member,
            new_role=s.validated_data["role"],
            actor=request.user,
        )
        return success_response(ProjectMemberSerializer(member).data)

    def delete(self, request, *args, **kwargs):
        project, _, _ = _ScopedHelper.resolve(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        _ScopedHelper.ensure_can_manage(project, request.user)
        member = self._get_member(project, kwargs["member_id"])
        svc = ProjectMemberService()
        svc.remove_member(project=project, member=member, actor=request.user)
        # 204 禁带 body（C1 例外）
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─────────────────────────────────────────────────────────────────────────
# 收藏 / 取消收藏（动作子资源，幂等）
# ─────────────────────────────────────────────────────────────────────────
class ProjectFavoriteView(APIView):
    """POST/DELETE .../projects/{id}/favorite/"""

    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        project, _, _ = _ScopedHelper.resolve(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        # project.favorite VIEWER 门槛 → 当前用户已 visible（resolve 守门）即满足
        data = ProjectMemberService.favorite(user=request.user, project=project)
        return success_response(data)

    def delete(self, request, *args, **kwargs):
        project, _, _ = _ScopedHelper.resolve(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        ProjectMemberService.unfavorite(user=request.user, project=project)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─────────────────────────────────────────────────────────────────────────
# 归档 / 恢复（动作子资源，幂等）
# ─────────────────────────────────────────────────────────────────────────
class ProjectArchiveView(APIView):
    """POST/DELETE .../projects/{id}/archive/"""

    permission_classes = [IsAuthenticated]

    def _ensure_can_archive(self, project, user) -> None:
        # project.archive ADMIN 门槛；隐式 WS_ADMIN+ 由 get_project_or_404 已提升 current_user_role
        role = getattr(project, "current_user_role", 0) or 0
        if role < ProjectRole.ADMIN:
            raise AppException("PERM_ROLE_INSUFFICIENT",
                               message="需要项目管理员权限")

    def post(self, request, *args, **kwargs):
        project, _, _ = _ScopedHelper.resolve(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        self._ensure_can_archive(project, request.user)
        from django.utils import timezone
        # 幂等：已 archived → 直接返回当前状态
        if project.status == Project.Status.ARCHIVED:
            return success_response({"status": "archived",
                                      "archived_at": timezone.now().isoformat()})
        project = ProjectMemberService.set_archived(
            project=project, actor=request.user, archived=True
        )
        return success_response({"status": project.status,
                                  "archived_at": timezone.now().isoformat()})

    def delete(self, request, *args, **kwargs):
        project, _, _ = _ScopedHelper.resolve(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        self._ensure_can_archive(project, request.user)
        if project.status == Project.Status.ACTIVE:
            return success_response({"status": "active",
                                      "archived_at": None})
        project = ProjectMemberService.set_archived(
            project=project, actor=request.user, archived=False
        )
        return success_response({"status": project.status,
                                  "archived_at": None})
