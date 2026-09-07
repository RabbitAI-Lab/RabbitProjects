"""文件分享内部端点（FILE-004 §4.2 内部 API 表 #1~#4，Session）。

  POST   …/files/{asset_id}/share-links/        创建（file.share + can_view_file）
  GET    …/files/{asset_id}/share-links/        列表（file.share，含失效态）
  POST   …/share-links/{link_id}/extend/        延期（file.share + 创建者/ADMIN）
  DELETE …/share-links/{link_id}/               吊销（file.share + 创建者/ADMIN）

延期为动作子资源（POST 而非 PATCH——§4.2 注：时点推进而非字段部分更新）。
BR-13：创建/吊销/延期 on_commit 投 file.share.* 内部事件；匿名访问不投。
"""
from __future__ import annotations

from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.serializers.file_shares import (
    ShareCreateSerializer,
    ShareExtendSerializer,
)
from plane.app.views._access import get_project_or_404
from plane.app.views.file_library import _get_library_asset, _require_not_archived
from plane.base.exception import AppException
from plane.base.response import created_response, success_response
from plane.constants.permissions import threshold_of
from plane.db.models import FileShareLink, ProjectRole
from plane.db.services import file_share as svc

_FILE_SHARE = threshold_of("file.share")


def _require_share_permission(project, *, permission_key: str = "file.share") -> None:
    """门槛 → 403 ``PERM_ROLE_INSUFFICIENT``（§2.4「权限不足创建」行）。"""
    if (project.current_user_role or 0) < _FILE_SHARE:
        raise AppException(
            "PERM_ROLE_INSUFFICIENT",
            message=f"缺少权限：{permission_key}（需要更高项目角色）",
        )


def _get_share_link(*, project, link_id) -> FileShareLink:
    link = (
        FileShareLink.objects.select_related("asset")
        .filter(pk=link_id, asset__project_id=project.id)
        .first()
    )
    if link is None:
        raise NotFound("RESOURCE_NOT_FOUND")
    return link


def _require_link_owner(project, link: FileShareLink, user) -> None:
    """对象级归属（§4.2 表 #3/#4「创建者/ADMIN」）：他人 → 403 PERM_DENIED。"""
    if (project.current_user_role or 0) < ProjectRole.ADMIN and link.created_by_id != user.id:
        raise AppException("PERM_DENIED", message="仅分享创建者或项目管理员可操作该分享")


def _base_url(request) -> str:
    return request.build_absolute_uri("/")


def _publish(event: str, link: FileShareLink, actor) -> None:
    """BR-13：on_commit 投内部事件——WS（file.share.*）+ 动态流双半边
    （动态流为 Sprint-5 T5-02 管道扩域补齐）；匿名访问路径不经此——防刷屏。"""
    from plane.bgtasks.event_publisher import publish_share_event

    publish_share_event(
        event,
        project_id=str(link.asset.project_id),
        asset_id=str(link.asset_id),
        share_id=str(link.id),
        actor_id=str(actor.id) if getattr(actor, "id", None) else None,
    )
    from plane.db.services.file_stream import emit_file_activity

    name = (link.asset.attributes or {}).get("name", "")
    stream = {
        "file.share.created": ("share_created", f"创建了文件「{name}」的分享链接"),
        "file.share.revoked": ("share_revoked", f"吊销了文件「{name}」的分享链接"),
        "file.share.extended": ("share_extended", f"延长了文件「{name}」分享链接的有效期"),
    }
    action, comment = stream[event]
    emit_file_activity(
        project_id=link.asset.project_id, asset_id=link.asset_id,
        actor_id=getattr(actor, "id", None), action=action, comment=comment,
    )


# ── 创建 / 列表（§4.2.1 / §4.2.4）────────────────────────────────────
class FileShareListCreateView(APIView):
    """POST / GET …/files/{asset_id}/share-links/ —— 创建 / 我的管理列表。"""

    def post(self, request, *args, **kwargs):
        from plane.db.services.file_permission import assert_can_view

        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        asset = _get_library_asset(project=project, asset_id=kwargs["asset_id"])
        assert_can_view(request.user, asset)  # BR-01：不能分享自己看不见的文件（404 隐藏）
        _require_share_permission(project)
        _require_not_archived(project)  # FILE-002 BR-14 归档只读口径
        s = ShareCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            link = svc.create_share(
                asset=asset, payload=dict(s.validated_data), actor=request.user
            )
        except svc.ShareLimitExceeded as exc:
            if exc.dimension == "asset":
                raise AppException(
                    "RESOURCE_LIMIT_EXCEEDED",
                    message="该文件有效分享数量已达上限",
                    details=[{"field": "asset_id", "code": "LIMIT",
                              "message": f"上限 {exc.limit} 条"}],
                ) from exc
            raise AppException(
                "RESOURCE_LIMIT_EXCEEDED",
                message="你在该项目的有效分享已达上限",
                details=[{"field": "created_by", "code": "LIMIT",
                          "message": f"上限 {exc.limit} 条"}],
            ) from exc
        transaction.on_commit(lambda: _publish("file.share.created", link, request.user))
        row = svc.share_row(link, base_url=_base_url(request))
        return created_response(row, location=row["share_url"])

    def get(self, request, *args, **kwargs):
        from plane.db.services.file_permission import assert_can_view

        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        asset = _get_library_asset(project=project, asset_id=kwargs["asset_id"])
        assert_can_view(request.user, asset)
        _require_share_permission(project)
        rows, meta = svc.list_shares(
            asset=asset, params=request.query_params.dict(), base_url=_base_url(request)
        )
        return success_response(rows, meta=meta)


# ── 延期 / 吊销（§4.2.5 / §4.2 #4）───────────────────────────────────
class FileShareExtendView(APIView):
    """POST …/share-links/{link_id}/extend/ —— 延期（BR-15，非幂等动作）。"""

    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        _require_share_permission(project)
        link = _get_share_link(project=project, link_id=kwargs["link_id"])
        _require_link_owner(project, link, request.user)
        _require_not_archived(project)
        s = ShareExtendSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            link = svc.extend_share(
                link=link, extend_days=s.validated_data["extend_days"]
            )
        except svc.ShareStateError as exc:
            raise AppException(
                "RESOURCE_STATE_INVALID",
                message="仅有效分享可延期",
                details=[{"field": "status", "code": "INVALID",
                          "message": f"当前状态 {exc.status} 不允许延期"}],
            ) from exc
        except svc.SharePermanentLink as exc:
            raise AppException(
                "VALIDATION_ERROR",
                message="永久链接无需延期",
                details=[{"field": "expires_at", "code": "INVALID",
                          "message": "永久分享（expires_at=null）没有可延期的期限"}],
            ) from exc
        transaction.on_commit(lambda: _publish("file.share.extended", link, request.user))
        return success_response({
            "id": str(link.id),
            "expires_at": svc.iso(link.expires_at) if link.expires_at else None,
            "status": link.status,
        })


class FileShareRevokeView(APIView):
    """DELETE …/share-links/{link_id}/ —— 吊销（终态；BR-14 预签名 5 分钟自然过期）。"""

    def delete(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        _require_share_permission(project)
        link = _get_share_link(project=project, link_id=kwargs["link_id"])
        _require_link_owner(project, link, request.user)
        _require_not_archived(project)
        svc.revoke_share(link=link, actor=request.user)
        transaction.on_commit(lambda: _publish("file.share.revoked", link, request.user))
        # 204 禁带 body（C1 例外）——success_response(None, 204) 渲染 32 字节 JSON 会
        # 造成 keep-alive 响应流错位（file_library.py / file_versions.py 同款 bugfix：
        # 浏览器侧表现为 204 后继请求被误读为 500 "No Content"）
        return Response(status=status.HTTP_204_NO_CONTENT)
