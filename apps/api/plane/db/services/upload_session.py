"""分片上传会话 / 版本账本 / 预览调度（FILE-003 §4.3 核心）。

职责：
- 会话生命周期五端点服务（init / status / chunk 换发与登记 / complete / abort，
  §4.3.1）——片的真相在 S3 multipart，本层只维护会话索引与断点片表；
- 版本只增账本（§4.3.2）：``_new_version`` 单点收口（分片 complete / 直传 complete
  成功回调 §4.3.4 / 回滚三入口同构），行级镜像不变量（storage_path/size/attributes
  恒随 current_version）与 BR-08 淘汰 / BR-13 事件钩子在此继承；
- 预览调度（§2.3 决策链 → §4.2.2 形状）与衍生物登记（BR-11：键
  ``derivatives/{asset}/{version}/…``，登记挂 FileVersion.attributes.derivatives，
  零新表）。

与 file_library 的依赖方向：本模块 import file_library（配额/类型分类）；
file_library.complete_file 对本模块的调用（直传版本接线）走函数内懒 import，
避免环。
"""
from __future__ import annotations

import logging
import math
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from plane.base.middleware import ulid_new
from plane.db.models import FileAsset, FileFolder, FileVersion, UploadSession
from plane.db.services import file_library as flib
from plane.storage import minio as storage

logger = logging.getLogger("plane.db.services.upload_session")

# ── 常量（FILE-003 §2.6 边界条件 / §4.3.1）──────────────────────────
CHUNK_SIZE = 8 * 1024 * 1024                    # BR-02：8MB/片（末片可小）
MAX_FILE_SIZE = 5 * 1024 ** 3                    # 单文件 5GB
SESSION_TTL = timedelta(hours=24)                # BR-05：会话 24h TTL
MAX_ACTIVE_SESSIONS_PER_USER = 3                 # §2.6：活动会话/用户 3
VERSION_LIMIT = 20                               # BR-08：版本上限
PART_URL_EXPIRES = 1800                          # 片预签名 30 分钟
DOWNLOAD_URL_TTL = 300                           # BR-10：换发预签名 5 分钟
TEXT_PREVIEW_MAX = 2 * 1024 * 1024               # BR-12：文本预览 ≤2MB
OFFICE_TRANSCODE_MAX = 100 * 1024 * 1024         # §2.6：Office 转码输入 ≤100MB
THUMB_MAX_PX = 512                               # §2.6：缩略 ≤512px WebP
TRANSCODE_ETA_SECONDS = 30                       # §4.2.2：排队态预计时长

#: 流式播放容器（BR-12：视频仅 mp4/webm 流式，其余引导下载）
STREAMABLE_VIDEO_EXTS = {".mp4", ".webm"}
#: 分片上传白名单 = FILE-001 附件白名单 ∪ 本规格预览通道所需的媒体扩展
#: （视频五容器 + svg/bmp——FILE-001 清单是任务附件域 25MB 量程的产物，不含
#: 视频；FILE-003 §1.4 #5 视频预览以 mp4 上传为前提。直传 presign 白名单
#: 零回改（FILE-002 既有行为），差异见任务报告偏差登记）
LIBRARY_CHUNK_EXTS = frozenset(
    set(flib.ALLOWED_EXTS)
    | {".mp4", ".webm", ".mov", ".avi", ".mkv", ".svg", ".bmp"}
)
#: Office 转码域（§4.3.3 OFFICE；LibreOffice 可处理的三件套 + 旧二进制格式）
OFFICE_EXTS = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}
#: 文本预览域（Monaco 只读 / MD 渲染）
TEXT_EXTS = {".txt", ".md", ".log", ".json", ".xml", ".csv"}
ARCHIVE_EXTS = {".zip", ".7z", ".tar", ".gz", ".rar"}

#: BR-11 换发路径 kind → 对象键产物后缀（扩展名只留对象键，不进 API 路径）
DERIV_SUFFIX: dict[str, str] = {
    "preview": "preview.pdf",
    "thumbnail": "thumb.webp",
    "poster": "poster.jpg",
}
DERIVATIVE_PREFIX = "derivatives"


# ── 服务级异常（view 层捕获转 AppException；§2.5 异常表）──────────────
class SessionStateError(Exception):
    """会话状态不允许该操作（过期复用 / 非 uploading）→ 409 RESOURCE_STATE_INVALID。"""


class ActiveSessionConflict(Exception):
    """同文件并发第二会话 → 409 RESOURCE_ALREADY_EXISTS / UNIQUE（BR-15）。"""


class SessionLimitExceeded(Exception):
    """用户活动会话数达上限（§2.6：3 个）→ 409 RESOURCE_LIMIT_EXCEEDED / LIMIT。"""


class UploadMismatchError(Exception):
    """complete 片级完整性校验失败 → 400 VALIDATION_FILE_UPLOAD_MISMATCH（BR-04）。"""

    def __init__(self, *, missing: list[int], mismatched: list[int]):
        self.missing = missing
        self.mismatched = mismatched
        super().__init__(f"missing={missing} mismatched={mismatched}")


class ChunkInvalidError(Exception):
    """片号越界 / ETag-MD5 不符 → 400 VALIDATION_ERROR / INVALID（BR-02/BR-03）。"""


# ── 纯函数（UT-01/UT-02 量程锚）────────────────────────────────────
def total_chunks_of(file_size: int, chunk_size: int = CHUNK_SIZE) -> int:
    """BR-02：总片数 = ceil(size/8MB)，片号 1-based（末片可小）。"""
    return math.ceil(file_size / chunk_size)


def deriv_key(version: FileVersion, kind: str) -> str:
    """BR-11：衍生物对象键 ``derivatives/{asset}/{version}/{suffix}``。"""
    return f"{DERIVATIVE_PREFIX}/{version.asset_id}/{version.id}/{DERIV_SUFFIX[kind]}"


# ── 同名匹配（BR-07：匹配键 = 同 folder + name（大小写不敏感）+ 均非软删）──
def _match_live_asset(*, project_id, folder_id, name: str, exclude_pk=None):
    """命中「已上传」同目录同名行则复用（uploaded 不变）；新名返回 None。

    仅匹配 ``status=uploaded``：直传并发暂存行（uploading）互不匹配——后完成者
    在 complete 时刻匹配先完成者（§4.3.4 匹配时机 rationale）。
    """
    qs = FileAsset.objects.filter(
        project_id=project_id,
        entity_type=FileAsset.EntityType.PROJECT_FILE,
        folder_id=folder_id,
        status=FileAsset.Status.UPLOADED,
        deleted_at__isnull=True,
        attributes__name__iexact=name,
    )
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return qs.first()


def _match_or_create_asset(*, project, folder: FileFolder, name: str, ext: str,
                           size: int, actor) -> FileAsset:
    """分片 init 侧 BR-07：命中复用（可见性沿用既有行）；新名建 uploading 行。

    匹配序：① uploaded 同名行（同名换版）；② uploading 同名行——带活跃会话则
    BR-15 冲突（复用在途会话续传），无活跃会话（stale 残行）复用该行防重复。
    """
    existing = _match_live_asset(
        project_id=project.id, folder_id=folder.id, name=name
    )
    if existing is not None:
        return existing
    pending = FileAsset.objects.filter(
        project_id=project.id,
        entity_type=FileAsset.EntityType.PROJECT_FILE,
        folder_id=folder.id,
        status=FileAsset.Status.UPLOADING,
        deleted_at__isnull=True,
        attributes__name__iexact=name,
    ).first()
    if pending is not None:
        if UploadSession.objects.filter(
            asset=pending, status=UploadSession.Status.UPLOADING
        ).exists():
            raise ActiveSessionConflict(name)  # BR-15：同文件并发第二会话
        return pending  # stale 残行复用（不重复建行）
    return FileAsset.objects.create(
        workspace_id=project.workspace_id,
        project_id=project.id,
        entity_type=FileAsset.EntityType.PROJECT_FILE,
        entity_id=folder.id,
        folder=folder,
        attributes={"name": name, "size": size, "mime": "", "ext": ext},
        size=size,
        storage_path="",  # 占位：init 建行在 key 生成前——见 init_session 回填
        uploaded_by=actor,
        visibility=folder.visibility,
        allowed_members=list(folder.allowed_members or []),
        created_by=actor,
        updated_by=actor,
    )


# ── 会话生命周期（§4.3.1）──────────────────────────────────────────
@transaction.atomic
def init_session(*, project, folder: FileFolder, payload: dict, actor) -> UploadSession:
    """发起分片会话（§4.2 #1）：上限 → 会话数上限 → 配额（在途口径）→ BR-07
    匹配/建行 → CreateMultipartUpload → 落会话行。"""
    name = Path(payload["file_name"]).name  # basename 化防路径注入（FILE-001 同款）
    ext = Path(name).suffix.lower()
    size = int(payload["file_size"])
    if ext not in LIBRARY_CHUNK_EXTS:
        raise flib.AppException(
            "VALIDATION_FILE_TYPE_NOT_ALLOWED",
            message="不支持的文件类型",
            details=[{"field": "file_name", "code": "INVALID",
                      "message": f"仅支持 {sorted(LIBRARY_CHUNK_EXTS)}"}],
        )
    if size <= 0 or size > MAX_FILE_SIZE:
        raise flib.AppException(
            "VALIDATION_FILE_SIZE_EXCEEDED",
            message="文件大小超出限制",
            details=[{"field": "file_size", "code": "TOO_LARGE",
                      "message": "单文件上限 5GB"}],
        )
    # §2.6 活动会话/用户 3：超限拒绝（前端排队提示的判定源）
    active_count = UploadSession.objects.filter(
        project__workspace_id=project.workspace_id,
        created_by=actor,
        status=UploadSession.Status.UPLOADING,
        deleted_at__isnull=True,
    ).count()
    if active_count >= MAX_ACTIVE_SESSIONS_PER_USER:
        raise SessionLimitExceeded(MAX_ACTIVE_SESSIONS_PER_USER)
    # 在途预留扩展口径（§1.6 第 2 条）：Σ 活跃会话 ∪ Σ 无活跃会话的 uploading 行
    flib.assert_quota(workspace_id=project.workspace_id, incoming=size)

    asset = _match_or_create_asset(
        project=project, folder=folder, name=name, ext=ext, size=size, actor=actor
    )
    if asset.pk and UploadSession.objects.filter(
        asset=asset, status=UploadSession.Status.UPLOADING
    ).exists():
        raise ActiveSessionConflict(name)  # BR-15：复用在途会话续传

    key = "/".join([
        str(project.workspace_id), str(project.id),
        FileAsset.EntityType.PROJECT_FILE, str(folder.id), f"{ulid_new()}{ext}",
    ])
    upload_id = storage.create_multipart_upload(
        bucket=flib.BUCKET, key=key,
        content_type=(payload.get("content_type") or "").lower(),
    )
    if not asset.storage_path:  # 新名分片行回填键（uploading 态不入列表，30min 扫描豁免）
        asset.storage_path = key
        asset.save(update_fields=["storage_path", "updated_at"])
    try:
        return UploadSession.objects.create(
            project=project,
            asset=asset,
            s3_upload_id=upload_id,
            object_key=key,
            file_name=name,
            file_size=size,
            content_type=(payload.get("content_type") or "").lower(),
            content_md5=payload.get("content_md5"),
            total_chunks=total_chunks_of(size),
            uploaded_chunks=[],
            created_by=actor,
        )
    except IntegrityError as exc:  # uniq_active_session_per_asset 并发竞态兜底
        raise ActiveSessionConflict(name) from exc


def _require_uploading(session: UploadSession) -> None:
    if session.status != UploadSession.Status.UPLOADING:
        raise SessionStateError(session.status)  # §2.5「会话过期复用」


def session_row(session: UploadSession) -> dict:
    """会话状态载荷（§2.1 断点续传取片表 / §4.2 #2）。"""
    return {
        "session_id": str(session.id),
        "file_name": session.file_name,
        "file_size": session.file_size,
        "status": session.status,
        "chunk_size": session.chunk_size,
        "total_chunks": session.total_chunks,
        "uploaded_chunks": [c["n"] for c in (session.uploaded_chunks or [])],
        "expires_at": _iso(session.created_at + SESSION_TTL),
        "asset_id": str(session.asset_id) if session.asset_id else None,
    }


def presign_chunk(*, session: UploadSession, chunk_number: int) -> dict:
    """换发片预签名 UploadPart URL（§4.2 #3——动作子资源）。"""
    _require_uploading(session)
    if not 1 <= chunk_number <= session.total_chunks:
        raise ChunkInvalidError(chunk_number)
    url = storage.presigned_upload_part_url(
        bucket=flib.BUCKET, key=session.object_key,
        upload_id=session.s3_upload_id, part_number=chunk_number,
        expires=PART_URL_EXPIRES,
    )
    return {
        "part_number": chunk_number,
        "upload_url": flib._rewrite_to_uploads_prefix(url),
        "expires_in": PART_URL_EXPIRES,
    }


@transaction.atomic
def register_chunk(*, session: UploadSession, chunk_number: int,
                   etag: str, md5: str | None = None) -> dict:
    """登记片完成（§4.2 #4）：ETag 落 uploaded_chunks 断点索引。

    BR-03：前端随片提交 ``md5``（Content-MD5 的十六进制）时与 MinIO 返回 ETag
    核对，不符 → 400（该片重传 ≤3 次由前端驱动）。

    并发正确性：并行 3 片的 PATCH 同时落库——必须行锁重读后合并（先前视图层
    传入的 session 快照直接改写保存，两个 PATCH 竞态时后写覆盖前写，uploaded_chunks
    丢片 → 断点续传基线残缺，BR-06）。
    """
    _require_uploading(session)
    if not 1 <= chunk_number <= session.total_chunks:
        raise ChunkInvalidError(chunk_number)
    etag = (etag or "").strip().strip('"')
    if not etag:
        raise ChunkInvalidError(chunk_number)
    if md5 and md5.strip().lower() != etag.lower():
        raise ChunkInvalidError(chunk_number)  # UT-04：篡改片服务端拒记
    locked_session = (
        UploadSession.objects.select_for_update()
        .filter(pk=session.pk, deleted_at__isnull=True)
        .first()
    )
    if locked_session is None or locked_session.status != UploadSession.Status.UPLOADING:
        raise SessionStateError(locked_session.status if locked_session else "completed")
    session = locked_session
    chunks = [c for c in (session.uploaded_chunks or []) if c["n"] != chunk_number]
    chunks.append({"n": chunk_number, "etag": etag,
                   "size": min(session.chunk_size,
                               session.file_size - (chunk_number - 1) * session.chunk_size)})
    session.uploaded_chunks = sorted(chunks, key=lambda c: c["n"])
    session.save(update_fields=["uploaded_chunks", "updated_at"])
    return {"part_number": chunk_number,
            "uploaded_chunks": [c["n"] for c in session.uploaded_chunks]}


@transaction.atomic
def complete_session(*, session: UploadSession, actor) -> FileAsset:
    """合并 + 落库（§4.2 #5）：ListParts 片级核对（BR-04）→ 合并 → 版本落库 →
    五态收口（新名行 uploading → uploaded）。操作者可为非会话发起人（协作续传，
    §4.2.4 注——免属主校验为有意设计）。"""
    _require_uploading(session)
    if session.asset is None:  # 防御：会话行恒挂资产（init 保证），SET_NULL 仅资产硬删后
        raise SessionStateError(session.status)
    parts = storage.list_parts(
        bucket=flib.BUCKET, key=session.object_key, upload_id=session.s3_upload_id
    )
    have = {int(p["PartNumber"]): p["ETag"] for p in parts}
    registered = {int(c["n"]): c["etag"] for c in (session.uploaded_chunks or [])}
    missing = [n for n in range(1, session.total_chunks + 1) if n not in have]
    mismatched = [n for n, e in registered.items()
                  if n in have and have[n] != e]
    if missing or mismatched:  # BR-04 整件完整性闭环（片级 ETag；合并 ETag 非全文 MD5）
        raise UploadMismatchError(missing=missing[:20], mismatched=mismatched[:20])
    storage.complete_multipart_upload(
        bucket=flib.BUCKET, key=session.object_key,
        upload_id=session.s3_upload_id,
        parts=[{"PartNumber": n, "ETag": e} for n, e in sorted(have.items())],
    )
    version = _new_version(session.asset, session, key=session.object_key, actor=actor)
    session.status = UploadSession.Status.COMPLETED
    session.save(update_fields=["status", "updated_at"])
    # 五态迁移收口（§1.2/§2.1）：新名行 init 建 uploading → 此处回写 uploaded
    # （FILE-002 列表仅显 uploaded）；同名行本就 uploaded，回写幂等
    session.asset.status = FileAsset.Status.UPLOADED
    session.asset.is_uploaded = True
    session.asset.save(update_fields=["status", "is_uploaded", "updated_at"])
    transaction.on_commit(lambda: _enqueue_derive(str(version.id)))
    return session.asset


def abort_session(*, session: UploadSession) -> None:
    """取消（§4.2 #6）：AbortMultipartUpload + aborted。

    会话失效后，新名分片的 uploading 资产行自动回归 FILE-001 30min 孤儿扫描
    管辖（IT-09 第二段）。"""
    _require_uploading(session)
    storage.abort_multipart_upload(
        bucket=flib.BUCKET, key=session.object_key, upload_id=session.s3_upload_id
    )
    session.status = UploadSession.Status.ABORTED
    session.save(update_fields=["status", "updated_at"])


# ── 版本账本（§4.3.2 只增；三入口：分片 complete / 直传 complete / 回滚）──
def _enqueue_derive(version_id: str) -> None:
    from plane.bgtasks.derive_preview import derive_preview

    try:
        derive_preview.delay(version_id)
    except Exception as exc:  # noqa: BLE001 —— 预览派生是尽力而为，不阻断上传主流程
        logger.warning("derive_enqueue_failed version=%s err=%s", version_id, exc)


def _publish_version_created(version: FileVersion, *, actor_id, source_number: int | None) -> None:
    from plane.bgtasks.event_publisher import publish_file_version_created

    publish_file_version_created(
        project_id=str(version.asset.project_id),
        asset_id=str(version.asset_id),
        version_number=version.version_number,
        actor_id=str(actor_id) if actor_id else None,
        source_version_number=source_number,
    )


def _new_version(asset: FileAsset, upload, *, key: str,
                 source: FileVersion | None = None, actor=None) -> FileVersion:
    """版本落库单点（§4.3.2）：select_for_update 串行化版本号分配 + 行级镜像。

    - 版本号 max+1 而非 count()+1（BR-08 淘汰后 count 减少，count()+1 会与在册
      版本撞 uniq_version_per_asset；max+1 恒递增不复用，UT-17）。
    - attributes 同时携带 content_type（规格形态）与 mime/ext（FILE-002 行级
      镜像兼容键——列表筛选与 type_category 读 attributes.mime/ext）。
    - 镜像不变量（§4.3.2 注）：storage_path/size/attributes 恒随 current_version，
      与 update_fields 一批落库。
    """
    asset = FileAsset.objects.select_for_update().get(pk=asset.pk)
    number = (asset.versions.aggregate(m=Max("version_number"))["m"] or 0) + 1
    name = upload.file_name
    mime = getattr(upload, "content_type", "") or ""
    version = FileVersion.objects.create(
        asset=asset,
        version_number=number,
        object_key=key,
        attributes={
            "name": name,
            "size": upload.file_size,
            "content_type": mime,
            "mime": mime,
            "ext": Path(name).suffix.lower(),
            "md5": getattr(upload, "content_md5", None),
        },
        source_version=source,
        uploaded_by=actor or upload.created_by,  # 操作者优先，缺省回会话发起人（§4.3.2）
        created_by=actor or upload.created_by,
    )
    asset.current_version = version
    asset.attributes = version.attributes  # 镜像（列表免 JOIN）
    asset.storage_path = version.object_key  # 镜像不变量：presign_download 按行取数
    asset.size = version.attributes["size"]
    asset.uploaded_by = asset.uploaded_by or version.uploaded_by
    asset.save(update_fields=["current_version", "attributes", "storage_path",
                              "size", "uploaded_by", "updated_at"])
    actor_id = (actor or upload.created_by).id if (actor or upload.created_by) else None
    source_number = source.version_number if source else None
    transaction.on_commit(lambda: _publish_version_created(
        version, actor_id=actor_id, source_number=source_number))
    transaction.on_commit(lambda: _enqueue_evict(str(asset.id)))  # BR-08
    return version


def _enqueue_evict(asset_id: str) -> None:
    from plane.bgtasks.derive_preview import evict_old_versions

    try:
        evict_old_versions.delay(asset_id)
    except Exception as exc:  # noqa: BLE001 —— 淘汰治理异步兜底，不阻断主流程
        logger.warning("evict_enqueue_failed asset=%s err=%s", asset_id, exc)


@transaction.atomic
def rollback(*, asset: FileAsset, target: FileVersion, actor) -> FileVersion:
    """回滚 = 新版本行复用目标对象（零拷贝，BR-09）——历史只增不删。

    atomic 与 complete 同规（本模块 175/307 行同款装饰）：_new_version 内
    select_for_update 在无事务上下文（dev runserver autocommit）会抛
    TransactionManagementError——pytest 因 TestCase 事务包裹而掩盖此路径。
    """
    return _new_version(
        asset,
        SimpleNamespace(
            file_name=target.attributes["name"],
            file_size=target.attributes["size"],
            content_type=target.attributes.get("mime")
            or target.attributes.get("content_type", ""),
            content_md5=target.attributes.get("md5"),
            created_by=actor,
        ),
        key=target.object_key,
        source=target,
        actor=actor,
    )


def version_rows(asset: FileAsset) -> list[dict]:
    """版本列表（§4.2 #7）：新→旧，is_current 标当前指针。"""
    current_id = asset.current_version_id
    versions = list(
        FileVersion.objects.filter(asset=asset, deleted_at__isnull=True)
        .select_related("uploaded_by", "source_version")
        .order_by("-version_number")
    )
    return [_version_row(v, is_current=(v.id == current_id)) for v in versions]


def version_row(v: FileVersion, *, is_current: bool = False) -> dict:
    """单版本载荷（complete 响应内嵌当前版本，§2.1「201 文件元数据（含 version）」）。"""
    return _version_row(v, is_current=is_current)


def _version_row(v: FileVersion, *, is_current: bool = False) -> dict:
    attrs = v.attributes or {}
    src = v.source_version if v.source_version_id else None
    return {
        "version_id": str(v.id),
        "version_number": v.version_number,
        "size_bytes": attrs.get("size", 0),
        "content_type": attrs.get("mime") or attrs.get("content_type", ""),
        "md5": attrs.get("md5"),
        "source_version_id": str(v.source_version_id) if v.source_version_id else None,
        "source_version_number": src.version_number if src is not None else None,
        "uploaded_by": str(v.uploaded_by_id) if v.uploaded_by_id else None,
        "is_current": is_current,
        "created_at": _iso(v.created_at),
    }


def version_content_url(*, asset: FileAsset, version: FileVersion) -> str:
    """指定版本内容换发（§4.2 #9）：302 跳预签名 GET（5 分钟，BR-10）。

    前端文本 diff（E2E-04）以两版本本端点取正文后双栏渲染（§1.3：diff 在前端）。
    """
    from urllib.parse import quote

    if version.asset_id != asset.id or version.deleted_at is not None:
        from rest_framework.exceptions import NotFound

        raise NotFound("RESOURCE_NOT_FOUND")
    filename = (version.attributes or {}).get("name", "download")
    url = storage.presigned_get_url(
        bucket=flib.BUCKET,
        key=version.object_key,
        expires=DOWNLOAD_URL_TTL,
        response_headers={"response-content-disposition": (
            f"inline; filename*=UTF-8''{quote(filename)}")},
    )
    return flib._rewrite_to_uploads_prefix(url)


# ── 直传路径版本接线（§4.3.4——版本全称规则第二个挂接点）──────────────
def wire_direct_version(*, staging: FileAsset, stat_size: int, actor) -> FileAsset:
    """FILE-002 直传 complete 成功回调内的版本接线（HEAD 校验通过后调用）。

    BR-07 直传同名匹配在 complete 侧收口（presign 仅登记暂存行，零回改）：
    新名 → 暂存行即最终行（五态翻转）；同名 → 并入既有行，暂存行硬删（从未
    入列表，不产生回收站条目；对象由新版本行引用，零孤儿）。

    事务包裹（bugfix）：``_new_version`` 内 ``select_for_update`` 要求事务
    上下文——分片路径 ``complete_session`` 有 ``@transaction.atomic`` 而
    本直传路径缺包裹，runserver（ATOMIC_REQUESTS=False）下 complete 恒
    TransactionManagementError → 500；pytest 事务包裹曾掩盖该缺陷。
    """
    with transaction.atomic():
        key = staging.storage_path  # 先取值——同名分支将删暂存行
        desc = SimpleNamespace(
            file_name=(staging.attributes or {}).get("name", ""),
            file_size=stat_size,  # size 以 HEAD 实测为准（presign 声明值仅暂存）
            content_type=(staging.attributes or {}).get("mime", ""),
            content_md5=None,  # 直传无整件 MD5（分片路径才有，§4.2.1）
            created_by=staging.created_by,
        )
        existing = _match_live_asset(
            project_id=staging.project_id,
            folder_id=staging.folder_id,
            name=desc.file_name,
            exclude_pk=staging.pk,
        )
        if existing is None:  # 新名：暂存行即最终行（FILE-001 原语义）
            target = staging
            staging.status = FileAsset.Status.UPLOADED
            staging.is_uploaded = True
            staging.save(update_fields=["status", "is_uploaded", "updated_at"])
        else:  # 同名：并入既有行（v2+，沿用原可见性 BR-07）
            target = existing
            staging.delete()  # 硬删不产生回收站条目；对象由新版本行引用
        version = _new_version(target, desc, key=key, actor=actor)
    transaction.on_commit(lambda: _enqueue_derive(str(version.id)))
    return target


# ── 淘汰治理（BR-08：服务层逻辑，Celery 薄壳调用）────────────────────
def object_has_other_references(key: str, *, exclude_version_ids: set) -> bool:
    """键级引用计数：其他存活版本行 / 存活资产行（storage_path 镜像或双挂）是否引用。"""
    from plane.db.models import FileVersion as FV

    if (FV.objects.filter(object_key=key, deleted_at__isnull=True)
            .exclude(id__in=exclude_version_ids).exists()):
        return True
    return FileAsset.objects.filter(storage_path=key, deleted_at__isnull=True).exists()


def evict_old_versions(asset_id) -> int:
    """>20 版时淘汰最旧非当前（软删留审计）；对象无他引用才物理删（BR-08/UT-11）。

    返回淘汰数。空转（≤20 版）零副作用。
    """
    asset = FileAsset.all_objects.filter(pk=asset_id).first()
    if asset is None:
        return 0
    live = list(
        FileVersion.objects.filter(asset=asset, deleted_at__isnull=True)
        .order_by("version_number")
    )
    overflow = len(live) - VERSION_LIMIT
    if overflow <= 0:
        return 0
    victims = [v for v in live if v.id != asset.current_version_id][:overflow]
    evicted = 0
    for victim in victims:
        victim.deleted_at = timezone.now()
        victim.save(update_fields=["deleted_at", "updated_at"])
        evicted += 1
    # 引用判定只排除「随本批消失」的淘汰行——存活版本（回滚链复用同键）与
    # 资产行（storage_path 镜像）仍是有效引用（BR-08/UT-11）
    victim_ids = {v.id for v in victims}
    for victim in victims:
        if not object_has_other_references(victim.object_key, exclude_version_ids=victim_ids):
            try:
                storage.remove_object(bucket=flib.BUCKET, key=victim.object_key)
            except storage.StorageUnavailable as exc:
                logger.warning("evict_remove_failed version=%s err=%s", victim.id, exc)
    return evicted


# ── 预览调度（§2.3 决策链 / §4.2.2 形状）────────────────────────────
def _preview_kind(mime: str, ext: str) -> str:
    mime = (mime or "").lower()
    ext = (ext or "").lower()
    if mime.startswith("image/") or ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}:
        return "image"
    if mime.startswith("video/") or ext in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
        return "video"
    if "pdf" in mime or ext == ".pdf":
        return "pdf"
    if mime.startswith("text/") or ext in TEXT_EXTS:
        return "text"
    if "word" in mime or "officedocument" in mime or "ms-excel" in mime or ext in OFFICE_EXTS:
        return "office"
    if "zip" in mime or "compressed" in mime or "gzip" in mime or ext in ARCHIVE_EXTS:
        return "archive"
    return "other"


def _deriv_state(version: FileVersion, kind: str) -> str:
    """衍生物登记态（零新表：挂 attributes.derivatives，BR-11 登记侧）。"""
    entry = ((version.attributes or {}).get("derivatives") or {}).get(kind)
    return (entry or {}).get("status", "absent")


def _set_derivative(version: FileVersion, kind: str, **fields) -> None:
    attrs = dict(version.attributes or {})
    derivs = dict(attrs.get("derivatives") or {})
    entry = dict(derivs.get(kind) or {})
    entry.update(fields)
    derivs[kind] = entry
    attrs["derivatives"] = derivs
    version.attributes = attrs
    version.save(update_fields=["attributes", "updated_at"])


def ensure_derivative(version: FileVersion, kind: str) -> str:
    """派生需量登记 + 排队（幂等）：absent/failed → pending + 派生任务。"""
    state = _deriv_state(version, kind)
    if state in ("ready", "pending", "unsupported"):
        return state
    now = _iso(timezone.now())
    _set_derivative(version, kind, key=deriv_key(version, kind),
                    status="pending", generated_at=None, last_access_at=now)
    transaction.on_commit(lambda: _enqueue_derive(str(version.id)))
    return "pending"


def mark_derivative(version: FileVersion, kind: str, *, status: str,
                    error: str | None = None) -> None:
    """派生任务回写登记（ready/failed/unsupported）——Worker 侧调用。"""
    now = _iso(timezone.now())
    fields: dict = {"key": deriv_key(version, kind), "status": status,
                    "generated_at": now if status == "ready" else None,
                    "last_access_at": now}
    if error:
        fields["error"] = error[:200]
    _set_derivative(version, kind, **fields)


def touch_derivative(version: FileVersion, kind: str | None = None) -> None:
    """访问续期（BR-11 30 天冷清理的计时锚）。"""
    now = _iso(timezone.now())
    attrs = dict(version.attributes or {})
    derivs = dict(attrs.get("derivatives") or {})
    targets = [kind] if kind else list(derivs)
    touched = False
    for k in targets:
        if k in derivs and derivs[k].get("status") == "ready":
            derivs[k]["last_access_at"] = now
            touched = True
    if touched:
        attrs["derivatives"] = derivs
        version.attributes = attrs
        version.save(update_fields=["attributes", "updated_at"])


def derivative_redirect_url(*, asset: FileAsset, version: FileVersion, kind: str) -> str | None:
    """衍生物换发（§4.2 #11）：ready 才 302（5 分钟）；未就绪返回 None → 404。"""
    if kind not in DERIV_SUFFIX:
        from rest_framework.exceptions import NotFound

        raise NotFound("RESOURCE_NOT_FOUND")
    entry = ((version.attributes or {}).get("derivatives") or {}).get(kind)
    if not entry or entry.get("status") != "ready":
        return None
    touch_derivative(version, kind)  # 换发即续期（BR-11 冷清理计时重置）
    url = storage.presigned_get_url(
        bucket=flib.BUCKET, key=entry["key"], expires=DOWNLOAD_URL_TTL,
    )
    return flib._rewrite_to_uploads_prefix(url)


def _ws_slug(asset: FileAsset) -> str:
    """资产所属工作空间 slug（文件库域行恒有项目；缺省空串由 reverse 404 兜底）。"""
    from plane.db.models import Project

    if asset.project_id is None:
        return ""
    return str(
        Project.objects.filter(pk=asset.project_id)
        .values_list("workspace__slug", flat=True)
        .first()
        or ""
    )


def _content_redirect(asset: FileAsset) -> str:
    """当前版本正文换发路径（PDF 透传 / 视频流式 / 文本预览共用，§4.2.2 注）。"""
    from django.urls import reverse

    return reverse("app:file-version-content", kwargs={
        "slug": _ws_slug(asset), "project_id": asset.project_id,
        "asset_id": asset.id, "version_id": asset.current_version_id,
    })


def _anon_deriv_url(asset: FileAsset, version: FileVersion, kind: str) -> str:
    """匿名变体：衍生物直签 5 分钟预签名（FILE-004 §1.2 底线 3——每次签发、
    不缓存长链接）；None 防御回退内部路径（ready 已判，理论不可达）。"""
    return (
        derivative_redirect_url(asset=asset, version=version, kind=kind)
        or _deriv_path(asset, kind)
    )


def _anon_content_url(asset: FileAsset, version: FileVersion) -> str:
    """匿名变体：当前版本正文直签 5 分钟预签名（inline；换发即续期同内部）。"""
    return version_content_url(asset=asset, version=version)


def preview_dispatch(*, asset: FileAsset, anonymous: bool = False) -> tuple[int, dict]:
    """预览调度（§4.2 #10）→ (http_status, data)；就绪 200 / 排队 202。

    决策链（§2.3）：image 缩略（未生成 202 排队）→ pdf 透传 → office 转码
    （产物就绪 200 / 排队 202 / 过大 unsupported）→ text ≤2MB / 超限引导下载 →
    video 流式 + 封面帧（非流式 400）→ archive/other 元数据卡。

    ``anonymous=True``（FILE-004 §4.3.3）：URL 构造切换为匿名直签——内部路径
    （reverse 的换发端点）对匿名访客不可达，改签 5 分钟预签名（决策链 / 排队
    语义不变，§1.2 底线 3「每次换取 5 分钟预签名」）。
    """
    version = asset.current_version
    if version is None:  # 防御：无版本指针的 uploaded 行（回填遗漏）→ 引导下载
        return 200, {"kind": "other", "ready": False, "state": "no_preview",
                     "fallback_download": True}
    attrs = version.attributes or {}
    mime = attrs.get("mime") or attrs.get("content_type") or ""
    ext = attrs.get("ext") or Path(str(attrs.get("name", ""))).suffix.lower()
    kind = _preview_kind(mime, ext)
    size = int(attrs.get("size") or 0)

    if kind == "image":
        state = ensure_derivative(version, "thumbnail")
        if state == "ready":
            url = (_anon_deriv_url(asset, version, "thumbnail") if anonymous
                   else _deriv_path(asset, "thumbnail"))
            return 200, {"kind": "image", "ready": True,
                         "preview_url": url,
                         "fallback_download": True}
        return 202, _queued(kind="image", state="transcoding")
    if kind == "pdf":
        url = _anon_content_url(asset, version) if anonymous else _content_redirect(asset)
        return 200, {"kind": "pdf", "ready": True,
                     "preview_url": url,
                     "fallback_download": True}
    if kind == "office":
        if size > OFFICE_TRANSCODE_MAX:
            return 200, {"kind": "pdf", "ready": False, "state": "unsupported",
                         "fallback_download": True}
        state = ensure_derivative(version, "preview")
        if state == "ready":
            url = (_anon_deriv_url(asset, version, "preview") if anonymous
                   else _deriv_path(asset, "preview"))
            return 200, {"kind": "pdf", "ready": True,
                         "preview_url": url,
                         "fallback_download": True}
        return 202, _queued(kind="pdf", state="transcoding")
    if kind == "text":
        if size > TEXT_PREVIEW_MAX:  # BR-12/UT-14：>2MB 引导下载
            return 200, {"kind": "text", "ready": False, "state": "too_large",
                         "fallback_download": True}
        url = _anon_content_url(asset, version) if anonymous else _content_redirect(asset)
        return 200, {"kind": "text", "ready": True,
                     "preview_url": url,
                     "fallback_download": True}
    if kind == "video":
        if ext not in STREAMABLE_VIDEO_EXTS:  # §2.5：非流式视频仅下载
            raise flib.AppException(
                "VALIDATION_INVALID_PARAM",
                message="该视频格式不支持在线播放，请下载查看",
                details=[{"field": "ext", "code": "INVALID",
                          "message": "仅支持 mp4/webm 流式播放"}],
            )
        data: dict = {"kind": "video", "ready": True,
                      "preview_url": (_anon_content_url(asset, version) if anonymous
                                      else _content_redirect(asset)),
                      "fallback_download": True}
        state = ensure_derivative(version, "poster")
        if state == "ready":
            data["poster_url"] = (_anon_deriv_url(asset, version, "poster") if anonymous
                                  else _deriv_path(asset, "poster"))
        else:
            data["poster_state"] = state  # 排队/失败不阻塞播放本体
        return 200, data
    # archive / other：元数据卡 + 下载按钮（§2.3 L）
    return 200, {"kind": kind, "ready": False, "state": "no_preview",
                 "fallback_download": True}


def _queued(*, kind: str, state: str) -> dict:
    return {"kind": kind, "ready": False, "state": state,
            "eta_seconds": TRANSCODE_ETA_SECONDS}


def _deriv_path(asset: FileAsset, kind: str) -> str:
    """衍生物换发端点路径（无扩展名——api-conventions §2.3，BR-11 kind 映射）。"""
    from django.urls import reverse

    return reverse("app:file-derivative", kwargs={
        "slug": _ws_slug(asset), "project_id": asset.project_id,
        "asset_id": asset.id, "kind": kind,
    })


def _iso(dt) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
