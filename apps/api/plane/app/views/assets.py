"""附件视图（FILE-001 §4.3 端点表）。

端点（嵌套在 issue 下，全部强制尾斜杠）：
  POST   .../issues/{issue_id}/attachments/presign/   申请预签名直传 URL
  POST   .../issues/{issue_id}/attachments/{asset_id}/complete/   完成确认
  GET    .../issues/{issue_id}/attachments/           附件列表
  GET    .../issues/{issue_id}/attachments/{asset_id}/download/  换发下载 URL
  DELETE .../issues/{issue_id}/attachments/{asset_id}/ 删除附件（软删）

权限：presign/complete 需 file.upload（PROJ_CONTRIBUTOR+）；list/download 需
file.read（PROJ_VIEWER+）；delete 需 file.delete（PROJ_ADMIN 全量；PROJ_CONTRIBUTOR
仅本人上传，对应 FILE-001 §2.4 BR-10 / R1 受限项）。
"""
from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.permissions import FilePermission
from plane.app.serializers.asset import (
    CompleteSerializer,
    PresignSerializer,
)
from plane.app.services.asset import AssetService
from plane.app.views._access import get_project_or_404
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.db.models import FileAsset, Issue
from plane.db.models.roles import ProjectRole


class _ScopedHelper:
    """项目作用域解析 + issue 取数（共享给所有附件视图）。"""

    @staticmethod
    def resolve(slug, project_id, issue_id, user):
        project, _, _ = get_project_or_404(slug, project_id, user)
        try:
            issue = Issue.objects.get(
                id=issue_id,
                project_id=project.id,
                deleted_at__isnull=True,
            )
        except Issue.DoesNotExist as exc:
            raise NotFound("RESOURCE_NOT_FOUND") from exc
        return project, issue

    @staticmethod
    def get_asset(*, project, issue, asset_id):
        try:
            asset = FileAsset.objects.get(
                pk=asset_id,
                entity_type=FileAsset.EntityType.ISSUE,
                entity_id=issue.id,
            )
        except FileAsset.DoesNotExist as exc:
            raise NotFound("RESOURCE_NOT_FOUND") from exc
        return asset


class AttachmentPresignView(APIView):
    """POST .../issues/{id}/attachments/presign/ —— §4.3.1"""

    permission_classes = [FilePermission]

    def post(self, request, *args, **kwargs):
        project, issue = _ScopedHelper.resolve(
            kwargs["slug"], kwargs["project_id"], kwargs["issue_id"], request.user,
        )
        s = PresignSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        svc = AssetService()
        data = svc.presign(issue=issue, payload=s.validated_data, actor=request.user)
        return success_response(
            data,
            status_code=status.HTTP_201_CREATED,
            headers={
                "Location": request.build_absolute_uri(
                    f"/api/v1/workspaces/{kwargs['slug']}/projects/{project.id}/"
                    f"issues/{issue.id}/attachments/{data['asset_id']}/"
                )
            },
        )


class AttachmentCompleteView(APIView):
    """POST .../issues/{id}/attachments/{asset_id}/complete/ —— §4.3.2"""

    permission_classes = [FilePermission]

    def post(self, request, *args, **kwargs):
        project, issue = _ScopedHelper.resolve(
            kwargs["slug"], kwargs["project_id"], kwargs["issue_id"], request.user,
        )
        s = CompleteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        asset = _ScopedHelper.get_asset(
            project=project, issue=issue, asset_id=kwargs["asset_id"],
        )
        # 校验 actor == 上传人（防止 B 拿 A 的 asset_id 来完成）
        if asset.uploaded_by_id != request.user.id:
            raise NotFound("RESOURCE_NOT_FOUND")
        svc = AssetService()
        data = svc.complete(asset=asset, issue=issue)
        return success_response(data)


class AttachmentListView(ListAPIView):
    """GET .../issues/{id}/attachments/ —— §4.3.3 列表"""

    permission_classes = [FilePermission]

    def list(self, request, *args, **kwargs):
        project, issue = _ScopedHelper.resolve(
            kwargs["slug"], kwargs["project_id"], kwargs["issue_id"], request.user,
        )
        svc = AssetService()
        rows = svc.list_for_issue(issue=issue)
        return success_response(
            rows,
            meta={
                "count": len(rows),
                "total_count": len(rows),
                "total_pages": 1,
                "page": 1,
                "per_page": max(len(rows), 50),
            },
        )


class AttachmentDownloadView(APIView):
    """GET .../issues/{id}/attachments/{asset_id}/download/ —— §4.3.4 换发 302"""

    permission_classes = [FilePermission]

    def get(self, request, *args, **kwargs):
        project, issue = _ScopedHelper.resolve(
            kwargs["slug"], kwargs["project_id"], kwargs["issue_id"], request.user,
        )
        asset = _ScopedHelper.get_asset(
            project=project, issue=issue, asset_id=kwargs["asset_id"],
        )
        if asset.status != FileAsset.Status.UPLOADED:
            raise NotFound("RESOURCE_NOT_FOUND")
        svc = AssetService()
        url = svc.download_url(asset=asset)
        return Response(status=status.HTTP_302_FOUND, headers={"Location": url})


class AttachmentDeleteView(APIView):
    """DELETE .../issues/{id}/attachments/{asset_id}/ —— §4.3.5 软删"""

    permission_classes = [FilePermission]

    def delete(self, request, *args, **kwargs):
        project, issue = _ScopedHelper.resolve(
            kwargs["slug"], kwargs["project_id"], kwargs["issue_id"], request.user,
        )
        asset = _ScopedHelper.get_asset(
            project=project, issue=issue, asset_id=kwargs["asset_id"],
        )
        # BR-10 R1 受限项：CONTRIBUTOR 仅本人上传可删；ADMIN 全量
        if (
            project.current_user_role < ProjectRole.ADMIN
            and asset.uploaded_by_id != request.user.id
        ):
            raise AppException(
                "PERM_DENIED",
                message="仅本人上传可删除（PROJ_CONTRIBUTOR 受限项）",
            )
        svc = AssetService()
        new_count = svc.delete(asset=asset, issue=issue, actor=request.user)
        return success_response(
            {"id": str(asset.id), "attachment_count": new_count}
        )
