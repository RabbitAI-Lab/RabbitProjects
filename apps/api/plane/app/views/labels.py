"""项目标签视图（TASK-002 §4.3.1）。

端点：
  GET    /workspaces/{slug}/projects/{pid}/labels/            列表（含 is_active=false，BR-05）
  POST   /workspaces/{slug}/projects/{pid}/labels/            创建（PROJ_ADMIN）
  PATCH  /workspaces/{slug}/projects/{pid}/labels/{lid}/      改名/改色/排序/启停（PROJ_ADMIN）
  DELETE /workspaces/{slug}/projects/{pid}/labels/{lid}/      ?force=true 强制物理软删，否则被引用默认停用

权限：PROJ_ADMIN+（含隐式 WS_ADMIN+ 提升，rbac §7.4）。
"""
from __future__ import annotations

from datetime import UTC, datetime

from django.db import transaction
from django.db.models import Count, Q
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated
from plane.app.serializers.label import LabelSerializer, LabelWriteSerializer
from plane.app.views._access import get_project_or_404
from plane.base.exception import AppException
from plane.base.response import created_response, success_response
from plane.db.models import Label
from plane.db.models.roles import ProjectRole


class LabelListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        qs = (
            Label.objects
            .filter(project=project, deleted_at__isnull=True)
            .annotate(_usage=Count(
                "issue_labels",
                filter=Q(issue_labels__deleted_at__isnull=True),
                distinct=True,
            ))
            .order_by("sort_order", "created_at")
        )
        # 直接以 serialize 形式返回（含 usage_count 通过 SerializerMethodField 走 attr）
        data = []
        for label in qs:
            d = LabelSerializer(label).data
            d["usage_count"] = label._usage
            data.append(d)
        return success_response(
            data,
            meta={
                "count": len(data),
                "total_count": len(data),
                "page": 1, "per_page": 100,
                "next_cursor": None, "prev_cursor": None,
                "next_page_results": False, "prev_page_results": False,
                "total_pages": 1,
            },
        )

    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        if project.current_user_role < ProjectRole.ADMIN:
            raise AppException(
                "PERM_ROLE_INSUFFICIENT",
                message="需要项目管理员权限",
            )
        s = LabelWriteSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        name = s.validated_data["name"]
        if Label.objects.filter(
            project=project, name=name, deleted_at__isnull=True
        ).exists():
            raise AppException(
                "RESOURCE_ALREADY_EXISTS",
                message=f"标签 {name} 已存在",
                details=[{"field": "name", "code": "DUPLICATE_NAME",
                          "message": f"标签 {name} 已存在"}],
            )

        # 项目标签总数限制（BR 100 个，TASK-002 §2.7）
        total = Label.objects.filter(
            project=project, deleted_at__isnull=True
        ).count()
        if total >= 100:
            raise AppException(
                "RESOURCE_LIMIT_EXCEEDED",
                details=[{"field": "name", "code": "TOO_LARGE",
                          "message": "项目标签数量已达上限（100）",
                          "limit": 100}],
            )

        label = Label.objects.create(
            project=project,
            name=name,
            color=s.validated_data.get("color", "#6B7280"),
            is_active=s.validated_data.get("is_active", True),
            sort_order=s.validated_data.get("sort_order", 65535.0),
            created_by=request.user,
        )
        return created_response(
            LabelSerializer(label).data,
            location=request.build_absolute_uri(
                f"/api/v1/workspaces/{kwargs['slug']}/projects/{project.id}/labels/{label.id}/"
            ),
        )


class LabelDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get(self, project, label_id):
        try:
            return Label.objects.get(
                id=label_id, project=project, deleted_at__isnull=True
            )
        except (Label.DoesNotExist, ValueError):
            raise NotFound("RESOURCE_NOT_FOUND") from None

    def patch(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        if project.current_user_role < ProjectRole.ADMIN:
            raise AppException(
                "PERM_ROLE_INSUFFICIENT",
                message="需要项目管理员权限",
            )
        label = self._get(project, kwargs["label_id"])
        s = LabelWriteSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        data = s.validated_data

        # 改名唯一性
        if "name" in data and data["name"] != label.name:
            if Label.objects.filter(
                project=project, name=data["name"],
                deleted_at__isnull=True,
            ).exclude(pk=label.pk).exists():
                raise AppException(
                    "RESOURCE_ALREADY_EXISTS",
                    message=f"标签 {data['name']} 已存在",
                    details=[{"field": "name", "code": "DUPLICATE_NAME",
                              "message": f"标签 {data['name']} 已存在"}],
                )
            label.name = data["name"]
        if "color" in data:
            label.color = data["color"]
        if "is_active" in data:
            label.is_active = data["is_active"]
        if "sort_order" in data:
            label.sort_order = data["sort_order"]
        label.save()
        return success_response(LabelSerializer(label).data)

    def delete(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        if project.current_user_role < ProjectRole.ADMIN:
            raise AppException(
                "PERM_ROLE_INSUFFICIENT",
                message="需要项目管理员权限",
            )
        label = self._get(project, kwargs["label_id"])
        force = (request.query_params.get("force", "false").lower()
                 in ("1", "true", "yes"))
        usage = label.issue_labels.filter(deleted_at__isnull=True).count()
        if usage and not force:
            # BR-05：默认停用而非物理删
            label.is_active = False
            label.save(update_fields=["is_active", "updated_at"])
            return success_response({"result": "deactivated", "usage_count": usage})
        with transaction.atomic():
            label.issue_labels.update(deleted_at=datetime.now(tz=UTC))
            label.soft_delete(actor_id=request.user.id)
        return Response(status=status.HTTP_204_NO_CONTENT)
