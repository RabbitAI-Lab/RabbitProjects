"""分片会话 / 版本 / 预览视图（FILE-003 §4.2 端点表 #1~#11）。

端点（全部强制尾斜杠；与 FILE-002 file_library 同嵌套基座）：
  POST   …/projects/{id}/upload-sessions/                       发起分片会话
  GET    …/projects/{id}/upload-sessions/{session_id}/          会话状态（断点片表）
  DELETE …/projects/{id}/upload-sessions/{session_id}/          取消（Abort）
  POST   …/projects/{id}/upload-sessions/{sid}/chunks/{n}/      换发片预签名
  PATCH  …/projects/{id}/upload-sessions/{sid}/chunks/{n}/      登记片完成（etag）
  POST   …/projects/{id}/upload-sessions/{sid}/complete/        合并 + 落库
  GET    …/projects/{id}/files/{asset_id}/versions/             版本列表
  POST   …/projects/{id}/files/{asset_id}/versions/{vid}/rollback/   回滚
  GET    …/projects/{id}/files/{asset_id}/versions/{vid}/content/    版本内容换发（302）
  GET    …/projects/{id}/files/{asset_id}/preview/              预览调度（200/202）
  GET    …/projects/{id}/files/{asset_id}/derivatives/{kind}/   衍生物换发（302）

权限（§4.2.4 矩阵 + BR-16）：会话族=file.upload（CONTRIBUTOR+）；#2/#6 叠加对象级
属主（created_by == 本人 或 ADMIN，他人 403 PERM_DENIED）；#3/4/5 免属主校验为
有意设计（协作续传语义，操作者经 FileVersion.uploaded_by 留痕）；读路径=file.read
+ can_view_file 实时校验（不可见 404 存在性隐藏）；回滚=file.version.manage。
3xx 换发无响应体、不套信封（api-conventions §4.1 仅约束 2xx）。
"""
from __future__ import annotations

from django.db import transaction
from django.http import HttpResponseRedirect
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.serializers.file_versions import (
    ChunkRegisterSerializer,
    SessionInitSerializer,
)
from plane.app.views._access import get_project_or_404
from plane.app.views.file_library import (
    _get_folder,
    _get_library_asset,
    _require_not_archived,
)
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.base.throttling import BASE_THROTTLES, PresignRateThrottle
from plane.constants.permissions import threshold_of
from plane.db.models import FileAsset, FileVersion, ProjectRole, UploadSession
from plane.db.services import upload_session as svc
from plane.storage import minio as storage

_FILE_READ = threshold_of("file.read")
_FILE_UPLOAD = threshold_of("file.upload")
_FILE_VERSION_MANAGE = threshold_of("file.version.manage")


def _require_role_strict(project, min_role: int, *, permission_key: str) -> None:
    """角色门槛 → 403 ``PERM_ROLE_INSUFFICIENT``（§4.2.4：与属主不匹配的
    ``PERM_DENIED`` 两码区分；FILE-002 既有端点沿用 PERM_DENIED 口径不回改）。"""
    if (project.current_user_role or 0) < min_role:
        raise AppException(
            "PERM_ROLE_INSUFFICIENT",
            message=f"缺少权限：{permission_key}（需要更高项目角色）",
        )


def _get_session(*, project, session_id) -> UploadSession:
    session = UploadSession.objects.filter(
        pk=session_id, project=project, deleted_at__isnull=True
    ).first()
    if session is None:
        raise NotFound("RESOURCE_NOT_FOUND")
    return session


def _require_session_owner(project, session: UploadSession, user) -> None:
    """BR-16 #2/#6 对象级属主：本人或 ADMIN；他人 403 PERM_DENIED（防会话劫持）。"""
    if (project.current_user_role or 0) < ProjectRole.ADMIN and session.created_by_id != user.id:
        raise AppException("PERM_DENIED", message="无权操作该上传（仅会话发起人或管理员）")


def _map_session_error(exc: Exception) -> None:
    """service 异常 → AppException（§2.5 异常表）。"""
    if isinstance(exc, svc.ActiveSessionConflict):
        raise AppException(
            "RESOURCE_ALREADY_EXISTS",
            message="该文件已有进行中的上传会话",
            details=[{"field": "file_name", "code": "UNIQUE",
                      "message": "存在在途会话，请复用其断点续传"}],
        ) from exc
    if isinstance(exc, svc.SessionLimitExceeded):
        raise AppException(
            "RESOURCE_LIMIT_EXCEEDED",
            message="进行中的上传会话过多",
            details=[{"field": "session", "code": "LIMIT",
                      "message": "每个用户同时至多 3 个活动会话"}],
        ) from exc
    if isinstance(exc, svc.SessionStateError):
        raise AppException(
            "RESOURCE_STATE_INVALID",
            message="会话已过期或已完成，请新建会话",
            details=[{"field": "session", "code": "STATE",
                      "message": "仅 uploading 态会话可执行该操作"}],
        ) from exc
    if isinstance(exc, svc.ChunkInvalidError):
        raise AppException(
            "VALIDATION_ERROR",
            message="分片校验失败",
            details=[{"field": "chunk", "code": "INVALID",
                      "message": "片号越界或 MD5 与 ETag 不符，请重传该片"}],
        ) from exc
    if isinstance(exc, svc.UploadMismatchError):
        details = []
        if exc.missing:
            details.append({"field": "chunks", "code": "MISSING",
                            "message": f"缺失片号 {exc.missing}"})
        if exc.mismatched:
            details.append({"field": "chunks", "code": "MISMATCH",
                            "message": f"ETag 不符片号 {exc.mismatched}"})
        raise AppException(
            "VALIDATION_FILE_UPLOAD_MISMATCH",
            message="片级完整性校验失败，请续传缺失分片",
            details=details,
        ) from exc
    if isinstance(exc, storage.StorageUnavailable):
        raise AppException("SERVER_STORAGE_ERROR", message="对象存储暂时不可用，请稍后重试") from exc
    raise exc


# ── 会话五端点（§4.2 #1~#6）────────────────────────────────────────
class UploadSessionInitView(APIView):
    """POST …/upload-sessions/ —— 发起分片会话（file.upload，§4.2.1）。"""
    # §7.2 文件预签名申请 30/min·user（INFRA-005）——挂会话发起；chunks/
    # 换发属会话内合法高频（大文件百片级）不挂，防刷对象是会话不是片
    throttle_classes = [*BASE_THROTTLES, PresignRateThrottle]


    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        _require_role_strict(project, _FILE_UPLOAD, permission_key="file.upload")
        _require_not_archived(project)
        s = SessionInitSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        folder = _get_folder(project=project, folder_id=s.validated_data["folder_id"])
        try:
            from plane.db.services import file_library as flib

            session = svc.init_session(
                project=project, folder=folder,
                payload=s.validated_data, actor=request.user,
            )
        except flib.QuotaExceededError as exc:
            raise AppException(
                "QUOTA_STORAGE_EXCEEDED",
                message="工作空间存储空间不足",
                details=[{"field": "file_size", "code": "QUOTA",
                          "message": f"含在途预留后超出配额，本次需 {exc.incoming}B"}],
            ) from exc
        except Exception as exc:  # noqa: BLE001 —— _map 内收窄类型后重抛
            _map_session_error(exc)
        return success_response(
            svc.session_row(session),
            status_code=status.HTTP_201_CREATED,
            headers={
                "Location": request.build_absolute_uri(
                    f"/api/v1/workspaces/{kwargs['slug']}/projects/{project.id}/"
                    f"upload-sessions/{session.id}/"
                )
            },
        )


class UploadSessionDetailView(APIView):
    """GET/DELETE …/upload-sessions/{sid}/ —— 状态（断点续传）/ 取消。

    file.upload + 对象级属主（BR-16 #2/#6：他人窥探片表或恶意终止 → 403）。"""

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        _require_role_strict(project, _FILE_UPLOAD, permission_key="file.upload")
        session = _get_session(project=project, session_id=kwargs["session_id"])
        _require_session_owner(project, session, request.user)
        return success_response(svc.session_row(session))

    def delete(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        _require_role_strict(project, _FILE_UPLOAD, permission_key="file.upload")
        session = _get_session(project=project, session_id=kwargs["session_id"])
        _require_session_owner(project, session, request.user)
        _require_not_archived(project)
        try:
            svc.abort_session(session=session)
        except Exception as exc:  # noqa: BLE001
            _map_session_error(exc)
        # 204 禁带 body（C1 例外）——success_response(None, 204) 渲染 32 字节 JSON
        # 会造成 keep-alive 响应流错位（file_library.py 同款 bugfix）
        return Response(status=status.HTTP_204_NO_CONTENT)


class UploadSessionChunkView(APIView):
    """POST/PATCH …/upload-sessions/{sid}/chunks/{n}/ —— 换发片预签名 / 登记完成。

    免属主校验（BR-16 有意设计）：续传是协作语义，任一 CONTRIBUTOR 可推进团队
    会话（甲发起、乙接力 complete），操作者经版本行留痕。"""

    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        _require_role_strict(project, _FILE_UPLOAD, permission_key="file.upload")
        session = _get_session(project=project, session_id=kwargs["session_id"])
        try:
            data = svc.presign_chunk(session=session, chunk_number=int(kwargs["chunk_number"]))
        except Exception as exc:  # noqa: BLE001
            _map_session_error(exc)
        return success_response(data)

    def patch(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        _require_role_strict(project, _FILE_UPLOAD, permission_key="file.upload")
        session = _get_session(project=project, session_id=kwargs["session_id"])
        s = ChunkRegisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            data = svc.register_chunk(
                session=session,
                chunk_number=int(kwargs["chunk_number"]),
                etag=s.validated_data["etag"],
                md5=s.validated_data.get("md5"),
            )
        except Exception as exc:  # noqa: BLE001
            _map_session_error(exc)
        return success_response(data)


class UploadSessionCompleteView(APIView):
    """POST …/upload-sessions/{sid}/complete/ —— 合并 + 落库（BR-04 片级核对）。"""

    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        _require_role_strict(project, _FILE_UPLOAD, permission_key="file.upload")
        session = _get_session(project=project, session_id=kwargs["session_id"])
        _require_not_archived(project)
        try:
            asset = svc.complete_session(session=session, actor=request.user)
        except Exception as exc:  # noqa: BLE001
            _map_session_error(exc)
        from plane.db.services import file_library as flib

        version = asset.current_version
        # FILE-002 BR-12 动态流半边（Sprint-5 T5-02）：分片首传（v1）投「上传」
        # 留痕；v2+/回滚的版本语义由 _publish_version_created 承载（防双行）。
        if version is not None and version.version_number == 1:
            from plane.db.services.file_stream import emit_file_activity

            name = (asset.attributes or {}).get("name", "")
            transaction.on_commit(lambda: emit_file_activity(
                project_id=project.id, asset_id=asset.id,
                actor_id=request.user.id, action="uploaded",
                comment=f"上传了文件「{name}」"))
        return success_response(
            {"file": flib.file_row(asset),
             "version": svc.version_row(version, is_current=True) if version else {}},
            status_code=status.HTTP_201_CREATED,
        )


# ── 版本三端点 + 预览两端点（§4.2 #7~#11）──────────────────────────
class FileVersionsListView(APIView):
    """GET …/files/{asset_id}/versions/ —— 版本列表（file.read + 可见性 404）。"""

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        _require_role_strict(project, _FILE_READ, permission_key="file.read")
        asset = _get_library_asset(project=project, asset_id=kwargs["asset_id"])
        from plane.db.services.file_permission import assert_can_view

        assert_can_view(request.user, asset)  # 不可见 404（存在性隐藏）
        return success_response(svc.version_rows(asset))


def _get_version(*, asset, version_id) -> FileVersion:
    version = FileVersion.objects.filter(
        asset=asset, pk=version_id, deleted_at__isnull=True
    ).first()
    if version is None:
        raise NotFound("RESOURCE_NOT_FOUND")
    return version


class FileVersionRollbackView(APIView):
    """POST …/versions/{vid}/rollback/ —— 回滚（file.version.manage，CONTRIBUTOR+）。"""

    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        asset = _get_library_asset(project=project, asset_id=kwargs["asset_id"])
        from plane.db.services.file_permission import assert_can_view

        assert_can_view(request.user, asset)  # 不可见 404（先于 403 隐藏存在性）
        _require_role_strict(project, _FILE_VERSION_MANAGE, permission_key="file.version.manage")
        _require_not_archived(project)
        target = _get_version(asset=asset, version_id=kwargs["version_id"])
        version = svc.rollback(asset=asset, target=target, actor=request.user)
        return success_response(
            {
                "version_id": str(version.id),
                "version_number": version.version_number,
                "source_version_number": (
                    version.source_version.version_number if version.source_version_id else None
                ),
                "object_key": version.object_key,
                "created_at": version.created_at.isoformat(
                    timespec="milliseconds").replace("+00:00", "Z"),
            },
            status_code=status.HTTP_201_CREATED,
        )


class FileVersionContentView(APIView):
    """GET …/versions/{vid}/content/ —— 指定版本内容换发（302 跳预签名 GET 5 分钟）。"""

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        _require_role_strict(project, _FILE_READ, permission_key="file.read")
        asset = _get_library_asset(project=project, asset_id=kwargs["asset_id"])
        from plane.db.services.file_permission import assert_can_view

        assert_can_view(request.user, asset)  # BR-10：签发时实时校验
        version = _get_version(asset=asset, version_id=kwargs["version_id"])
        try:
            url = svc.version_content_url(asset=asset, version=version)
        except storage.StorageUnavailable as exc:
            raise AppException("SERVER_STORAGE_ERROR", message="对象存储暂时不可用，请稍后重试") from exc
        return HttpResponseRedirect(url)  # 302：3xx 无响应体不套信封（§4.2 端点口径注）


class FilePreviewView(APIView):
    """GET …/files/{asset_id}/preview/ —— 预览调度（就绪 200 / 排队 202，§4.2.2）。"""

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        _require_role_strict(project, _FILE_READ, permission_key="file.read")
        asset = _get_library_asset(project=project, asset_id=kwargs["asset_id"])
        from plane.db.services.file_permission import assert_can_view

        assert_can_view(request.user, asset)
        if asset.status != FileAsset.Status.UPLOADED:
            raise NotFound("RESOURCE_NOT_FOUND")
        http_status, data = svc.preview_dispatch(asset=asset)
        return success_response(data, status_code=http_status)


class FileDerivativeView(APIView):
    """GET …/files/{asset_id}/derivatives/{kind}/ —— 预览产物换发（302，BR-10/BR-11）。

    kind ∈ preview/thumbnail/poster（无扩展名路径——扩展名只在对象键与 Content-Type）。
    未就绪 404（前端 202 排队态轮询的本端点探针）。"""

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        _require_role_strict(project, _FILE_READ, permission_key="file.read")
        asset = _get_library_asset(project=project, asset_id=kwargs["asset_id"])
        from plane.db.services.file_permission import assert_can_view

        assert_can_view(request.user, asset)  # BR-10：签发时实时校验
        version = asset.current_version
        if version is None:
            raise NotFound("RESOURCE_NOT_FOUND")
        try:
            url = svc.derivative_redirect_url(
                asset=asset, version=version, kind=kwargs["kind"]
            )
        except storage.StorageUnavailable as exc:
            raise AppException("SERVER_STORAGE_ERROR", message="对象存储暂时不可用，请稍后重试") from exc
        if url is None:
            raise NotFound("RESOURCE_NOT_FOUND")
        return HttpResponseRedirect(url)
