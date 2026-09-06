"""项目文件库视图（FILE-002 §4.2 端点表）。

端点（全部强制尾斜杠；folders/{id}/files/ 按 §4.2 契约路径嵌套至第 5 层）：
  GET    …/projects/{id}/folders/                      目录树（可见性剪枝）
  POST   …/projects/{id}/folders/                      新建目录
  PATCH  …/projects/{id}/folders/{folder_id}/          改名/移动/可见性
  DELETE …/projects/{id}/folders/{folder_id}/          删除（整树软删，回传文件数）
  GET    …/projects/{id}/folders/{folder_id}/files/    目录文件列表（游标+筛选）
  POST   …/projects/{id}/folders/{folder_id}/files/presign/   上传预签名
  GET    …/projects/{id}/files/{asset_id}/download-url/       下载预签名（5 分钟）
  PATCH  …/projects/{id}/files/{asset_id}/             重命名/移动/双挂/可见性
  DELETE …/projects/{id}/files/{asset_id}/             删除（软删）
  POST   …/projects/{id}/files/{asset_id}/restore/     回收站还原
  GET    …/projects/{id}/files/trash/                  回收站列表
  GET    …/projects/{id}/files/storage/                配额用量
  DELETE …/projects/{id}/files/{asset_id}/purge/       彻底删除（引用计数）
  POST   …/projects/{id}/files/{asset_id}/complete/    完成确认（三步第三步）

权限（BR-13，rbac §8.2 原码）：读=file.read；上传=file.upload；文件更新=file.update
（R1：CONTRIBUTOR 仅本人上传）；删除/恢复/回收站=file.delete（R1 同键过滤；
彻底删除仅 PROJ_ADMIN 收紧）；目录=folder.manage；可见性=file.permission.manage。
项目归档只读（BR-14）：写操作 403 PERM_PROJECT_ARCHIVED。
"""
from __future__ import annotations

import base64

from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.serializers.file_library import (
    FilePresignSerializer,
    FileUpdateSerializer,
    FolderCreateSerializer,
    FolderUpdateSerializer,
)
from plane.app.views._access import get_project_or_404
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.constants.permissions import threshold_of
from plane.db.models import FileAsset, FileFolder
from plane.db.models.roles import ProjectRole
from plane.db.services import file_library as svc

# ── 权限门槛（PERMISSION_MATRIX 单一数据源；R1 附加规则在视图叠加）──────
_FILE_READ = threshold_of("file.read")
_FILE_UPLOAD = threshold_of("file.upload")
_FILE_UPDATE = threshold_of("file.update")
_FILE_DELETE = threshold_of("file.delete")
_FILE_PERM_MANAGE = threshold_of("file.permission.manage")
_FOLDER_MANAGE = threshold_of("folder.manage")


def _require_role(project, min_role: int, *, permission_key: str) -> None:
    if (project.current_user_role or 0) < min_role:
        raise AppException(
            "PERM_DENIED",
            message=f"缺少权限：{permission_key}（需要更高项目角色）",
        )


def _require_not_archived(project) -> None:
    if project.status == "archived":  # BR-14：归档后文件库只读
        raise AppException("PERM_PROJECT_ARCHIVED", message="项目已归档，文件库只读")


def _get_folder(*, project, folder_id) -> FileFolder:
    folder = FileFolder.objects.filter(
        pk=folder_id, project=project, deleted_at__isnull=True
    ).first()
    if folder is None:
        raise NotFound("RESOURCE_NOT_FOUND")
    return folder


def _get_library_asset(*, project, asset_id, include_deleted: bool = False) -> FileAsset:
    """文件库域取资产：entity_type=project_file + 项目归属；不可见/不存在 404。"""
    qs = FileAsset.all_objects if include_deleted else FileAsset.objects
    asset = qs.filter(
        pk=asset_id,
        project=project,
        entity_type=FileAsset.EntityType.PROJECT_FILE,
    ).first()
    if asset is None:
        raise NotFound("RESOURCE_NOT_FOUND")
    return asset


def _decode_cursor(raw: str | None) -> int:
    """游标解码：``{value}:{offset}:{is_prev}`` Base64 → 偏移量（INFRA-003 格式）。"""
    if not raw:
        return 0
    try:
        return max(int(base64.b64decode(raw).decode().split(":")[1]), 0)
    except Exception:                                            # noqa: BLE001
        raise AppException("VALIDATION_INVALID_CURSOR") from None


def _int_param(request, key: str, default: int, *, lo: int, hi: int) -> int:
    raw = request.query_params.get(key)
    if raw is None:
        return default
    try:
        return min(max(int(raw), lo), hi)
    except (TypeError, ValueError):
        return default


def _folder_row(folder: FileFolder) -> dict:
    return {
        "id": str(folder.id),
        "name": folder.name,
        "parent_id": str(folder.parent_id) if folder.parent_id else None,
        "visibility": folder.visibility,
        "allowed_members": [str(m) for m in (folder.allowed_members or [])],
        "created_at": folder.created_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "updated_at": folder.updated_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }


def _map_folder_service_error(exc: Exception) -> None:
    """service 异常 → AppException（§2.5 异常表：UNIQUE/LIMIT/CYCLE/400）。"""
    if isinstance(exc, svc.FolderNameConflict):
        raise AppException(
            "RESOURCE_ALREADY_EXISTS",
            message="同层已存在同名目录",
            details=[{"field": "name", "code": "UNIQUE", "message": "该层已存在同名目录"}],
        ) from exc
    if isinstance(exc, svc.FolderDepthExceeded):
        raise AppException(
            "RESOURCE_LIMIT_EXCEEDED",
            message="目录层级已达上限",
            details=[{"field": "parent_id", "code": "LIMIT", "message": "目录最多 5 层"}],
        ) from exc
    if isinstance(exc, svc.FolderCycleError):
        raise AppException(
            "RESOURCE_CIRCULAR_DEPENDENCY",
            message="不能移动到自身或后代目录之下",
            details=[{"field": "parent_id", "code": "CYCLE", "message": "目标目录是被移动目录的后代"}],
        ) from exc
    if isinstance(exc, svc.CrossProjectMoveError):
        raise AppException(
            "VALIDATION_ERROR",
            message="跨项目移动不允许",
            details=[{"field": "parent_id", "code": "INVALID", "message": "目标目录不属于当前项目"}],
        ) from exc
    raise exc


# ── 目录（§4.2 #1~#4）───────────────────────────────────────────────
class FolderListCreateView(APIView):
    """GET/POST …/projects/{id}/folders/ —— 目录树（可见性剪枝）/ 新建目录。"""

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        _require_role(project, _FILE_READ, permission_key="file.read")
        return success_response(svc.folder_tree(project=project, user=request.user))

    def post(self, request, *args, **kwargs):
        """新建目录（folder.manage，BR-01/UT-20）。"""
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        _require_role(project, _FOLDER_MANAGE, permission_key="folder.manage")
        _require_not_archived(project)
        s = FolderCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            folder = svc.create_folder(
                project=project,
                actor=request.user,
                name=s.validated_data["name"],
                parent_id=s.validated_data.get("parent_id"),
            )
        except Exception as exc:  # noqa: BLE001 —— _map 内收窄类型后重抛
            _map_folder_service_error(exc)
        return success_response(
            _folder_row(folder),
            status_code=status.HTTP_201_CREATED,
            headers={
                "Location": request.build_absolute_uri(
                    f"/api/v1/workspaces/{kwargs['slug']}/projects/{project.id}/"
                    f"folders/{folder.id}/"
                )
            },
        )


class FolderDetailView(APIView):
    """PATCH …/folders/{id}/ —— 改名/移动=folder.manage；可见性=file.permission.manage。"""

    def patch(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        folder = _get_folder(project=project, folder_id=kwargs["folder_id"])
        s = FolderUpdateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        data = s.validated_data
        touches_visibility = "visibility" in data or "allowed_members" in data
        touches_structure = "name" in data or "parent_id" in data
        if touches_visibility:
            # 可见性配置：仅 ADMIN（file.permission.manage，目录与文件同码）
            _require_role(project, _FILE_PERM_MANAGE, permission_key="file.permission.manage")
        if touches_structure:
            _require_role(project, _FOLDER_MANAGE, permission_key="folder.manage")
        _require_not_archived(project)
        try:
            if "name" in data:
                svc.rename_folder(folder=folder, name=data["name"])
            if "parent_id" in data:
                svc.move_folder(
                    folder_id=folder.id,
                    new_parent_id=data["parent_id"],
                    project_id=project.id,
                )
            if touches_visibility:
                svc.set_folder_visibility(
                    folder=folder,
                    visibility=data.get("visibility", folder.visibility),
                    allowed_members=(
                        [str(m) for m in data["allowed_members"]]
                        if "allowed_members" in data else list(folder.allowed_members or [])
                    ),
                )
        except Exception as exc:  # noqa: BLE001 —— 收窄映射后重抛
            _map_folder_service_error(exc)
        folder.refresh_from_db()
        return success_response(_folder_row(folder))


    def delete(self, request, *args, **kwargs):
        """DELETE …/folders/{id}/ —— 整树软删（BR-05），回传文件数（UT-14）。"""
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        folder = _get_folder(project=project, folder_id=kwargs["folder_id"])
        _require_role(project, _FOLDER_MANAGE, permission_key="folder.manage")
        _require_not_archived(project)
        result = svc.delete_folder(folder=folder, actor=request.user)
        return success_response({"id": str(folder.id), **result})


# ── 目录文件（§4.2 #5/#6）───────────────────────────────────────────
class FolderFilesListView(APIView):
    """GET …/folders/{id}/files/ —— 目录文件列表（§4.2.1 形状 + 逐文件可见性过滤）。"""

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        _require_role(project, _FILE_READ, permission_key="file.read")
        from plane.db.services.file_permission import assert_can_view

        folder = _get_folder(project=project, folder_id=kwargs["folder_id"])
        assert_can_view(request.user, folder)  # 不可见目录 404（存在性隐藏）
        uploaded_by = request.query_params.get("uploaded_by")
        params = {
            "name": request.query_params.get("name") or None,
            "type": request.query_params.get("type") or None,
            "uploaded_by": uploaded_by,
            "ordering": request.query_params.get("ordering") or None,
            "per_page": _int_param(request, "per_page", 50, lo=1, hi=100),
            "offset": _decode_cursor(request.query_params.get("cursor")),
            "expand": request.query_params.get("expand"),
        }
        rows, meta = svc.list_files(folder=folder, user=request.user, params=params)
        return success_response(rows, meta=meta)


class FolderFilePresignView(APIView):
    """POST …/folders/{id}/files/presign/ —— 上传预签名（三步复用，§4.2.2/BR-03）。"""

    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        _require_role(project, _FILE_UPLOAD, permission_key="file.upload")
        _require_not_archived(project)
        folder = _get_folder(project=project, folder_id=kwargs["folder_id"])
        s = FilePresignSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            data = svc.presign_file(
                folder=folder, payload=s.validated_data, actor=request.user
            )
        except svc.QuotaExceededError as exc:
            # §2.5「配额耗尽」：409 QUOTA_STORAGE_EXCEEDED / details 子码 QUOTA
            raise AppException(
                "QUOTA_STORAGE_EXCEEDED",
                message="工作空间存储空间不足",
                details=[{
                    "field": "file_size", "code": "QUOTA",
                    "message": (
                        f"已用 {_human(exc.used + exc.pending)} / {_human(exc.quota)}，"
                        f"本次需 {_human(exc.incoming)}"
                    ),
                }],
            ) from exc
        return success_response(
            data,
            status_code=status.HTTP_201_CREATED,
            headers={
                "Location": request.build_absolute_uri(
                    f"/api/v1/workspaces/{kwargs['slug']}/projects/{project.id}/"
                    f"files/{data['asset_id']}/"
                )
            },
        )


# ── 文件（§4.2 #7~#14）──────────────────────────────────────────────
class FileDownloadUrlView(APIView):
    """GET …/files/{asset_id}/download-url/ —— 下载预签名（5 分钟，BR-09 实时校验）。"""

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        _require_role(project, _FILE_READ, permission_key="file.read")
        asset = _get_library_asset(project=project, asset_id=kwargs["asset_id"])
        data = svc.file_download_url(asset=asset, user=request.user)  # 计数 BR-10
        return success_response(data)


class FileDetailView(APIView):
    """PATCH …/files/{asset_id}/ —— 重命名/移动/双挂=file.update（R1）；可见性=ADMIN。"""

    def patch(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        asset = _get_library_asset(project=project, asset_id=kwargs["asset_id"])
        s = FileUpdateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        data = s.validated_data
        touches_visibility = "visibility" in data or "allowed_members" in data
        touches_content = any(k in data for k in ("name", "folder_id", "issue_id"))
        if touches_content:
            # R1 受限项（BR-13）：CONTRIBUTOR 仅本人上传；ADMIN 全量（UT-19）
            _require_role(project, _FILE_UPDATE, permission_key="file.update")
            if (
                (project.current_user_role or 0) < ProjectRole.ADMIN
                and asset.uploaded_by_id != request.user.id
            ):
                raise AppException(
                    "PERM_DENIED",
                    message="仅本人上传的文件可修改（PROJ_CONTRIBUTOR 受限项）",
                )
        if touches_visibility:
            _require_role(project, _FILE_PERM_MANAGE, permission_key="file.permission.manage")
        _require_not_archived(project)
        payload = dict(data)
        if "allowed_members" in payload:
            payload["allowed_members"] = [str(m) for m in payload["allowed_members"]]
        asset = svc.update_file(asset=asset, payload=payload)
        return success_response(svc.file_row(asset, expand_uploaded_by=False))


    def delete(self, request, *args, **kwargs):
        """DELETE …/files/{asset_id}/ —— 软删进回收站（R1：ADMIN 或上传者）。"""
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        asset = _get_library_asset(project=project, asset_id=kwargs["asset_id"])
        _require_role(project, _FILE_DELETE, permission_key="file.delete")
        if (
            (project.current_user_role or 0) < ProjectRole.ADMIN
            and asset.uploaded_by_id != request.user.id
        ):
            raise AppException(
                "PERM_DENIED",
                message="仅本人上传的文件可删除（PROJ_CONTRIBUTOR 受限项）",
            )
        _require_not_archived(project)
        svc.soft_delete_file(asset=asset, actor=request.user)
        # 204 禁带 body（C1 例外，auth.py 同款）——success_response(None, 204) 会给
        # 无体状态码渲染 32 字节 JSON，keep-alive 下游把残留字节解析成下一响应的
        # 状态行 → 代理层 500/连接错位（e2e 双 context 连续 DELETE 稳定复现）
        return Response(status=status.HTTP_204_NO_CONTENT)


class FileRestoreView(APIView):
    """POST …/files/{asset_id}/restore/ —— 回收站还原（BR-07；file.delete 同码）。"""

    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        _require_role(project, _FILE_DELETE, permission_key="file.delete")
        asset = _get_library_asset(
            project=project, asset_id=kwargs["asset_id"], include_deleted=True
        )
        if asset.deleted_at is None:
            raise AppException(
                "RESOURCE_STATE_INVALID", message="文件不在回收站中"
            )
        # R1 同键口径：CONTRIBUTOR 仅可还原本人删除项（与回收站列表过滤一致，BR-13）
        if (
            (project.current_user_role or 0) < ProjectRole.ADMIN
            and asset.uploaded_by_id != request.user.id
        ):
            raise AppException(
                "PERM_DENIED",
                message="仅本人上传的文件可还原（PROJ_CONTRIBUTOR 受限项）",
            )
        _require_not_archived(project)
        asset = svc.restore_file(asset=asset, actor=request.user)
        return success_response(svc.file_row(asset))


class FileTrashListView(APIView):
    """GET …/files/trash/ —— 回收站列表（R1 同键过滤：ADMIN 全量 / 本人删除项）。"""

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        _require_role(project, _FILE_DELETE, permission_key="file.delete")
        params = {
            "ordering": request.query_params.get("ordering") or None,
            "per_page": _int_param(request, "per_page", 50, lo=1, hi=100),
            "offset": _decode_cursor(request.query_params.get("cursor")),
        }
        rows, meta = svc.trash_rows(project=project, user=request.user, params=params)
        return success_response(rows, meta=meta)


class FileStorageView(APIView):
    """GET …/files/storage/ —— 配额用量（§4.2.3）。"""

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        _require_role(project, _FILE_READ, permission_key="file.read")
        return success_response(svc.workspace_storage_usage(project.workspace_id))


class FilePurgeView(APIView):
    """DELETE …/files/{asset_id}/purge/ —— 彻底删除（BR-06 引用计数；仅 PROJ_ADMIN）。"""

    def delete(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        asset = _get_library_asset(
            project=project, asset_id=kwargs["asset_id"], include_deleted=True
        )
        # §4.2 #13：file.delete 仅 PROJ_ADMIN 收紧（R1 的「本人」口径不适用彻底删除）
        if (project.current_user_role or 0) < ProjectRole.ADMIN:
            raise AppException(
                "PERM_DENIED",
                message="彻底删除仅项目管理员可执行",
            )
        _require_not_archived(project)
        svc.purge_asset(asset=asset)
        return success_response({"id": str(asset.id), "purged": True})


class FileCompleteView(APIView):
    """POST …/files/{asset_id}/complete/ —— 完成确认（FILE-001 §4.3.2 协议复用，
    幂等；FILE-003 §4.3.4 起成功回调挂版本接线：新名翻转五态 / 同名并入版本链）。"""

    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user
        )
        _require_role(project, _FILE_UPLOAD, permission_key="file.upload")
        asset = _get_library_asset(project=project, asset_id=kwargs["asset_id"])
        # 校验 actor == 上传人（防 B 拿 A 的 asset_id 完成确认，FILE-001 同款）
        if asset.uploaded_by_id != request.user.id:
            raise NotFound("RESOURCE_NOT_FOUND")
        _require_not_archived(project)
        data = svc.complete_file(asset=asset, actor=request.user)
        return success_response(data)


def _human(n: int) -> str:
    """字节数人性化（配额错误 message 用，§4.2.2 示例「9.8GB / 10GB，8MB」）。"""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)}B"
            return f"{size:.1f}{unit}".replace(".0", "")
        size /= 1024
    return f"{n}B"
