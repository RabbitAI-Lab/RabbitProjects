"""项目文件库服务（FILE-002 §4.3 核心）。

职责：
- 目录树剪枝（§4.2 #1：逐目录 ``can_view_file`` 判定 +「到根的全部祖先均可见」
  前提 + 文件计数不透出不可见子孙）；
- 目录 CRUD / 移动环与深度校验（§4.3.2，复用 TASK-004 CTE 范式）；
- 文件上传 presign（50MB 项目库差异 + 配额在途预留 §4.3.4）/ complete
  （HEAD 协议复用 FILE-001 §4.3.2）/ 下载预签名（BR-09 签发时实时校验）；
- 删除（软删）/ 恢复（BR-07 冲突落根加 ``(恢复)``）/ 回收站 / 彻底删除（BR-06 引用计数）；
- 配额（Workspace 行锁串行化 + 在途预留，对象键 DISTINCT 去重）。

移动是纯元数据操作——对象键（四段 + ULID，FILE-001 §1.4 锁定）不含路径语义，
移动目录毫秒级零对象操作（§6.3 决策 3）。
"""
from __future__ import annotations

import base64
import logging
import uuid
from collections import Counter
from datetime import timedelta
from pathlib import Path

from django.conf import settings as dj_settings
from django.db import IntegrityError, connection, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from plane.base.exception import AppException
from plane.base.middleware import ulid_new
from plane.db.models import FileAsset, FileFolder, Issue, Workspace
from plane.db.models.roles import ProjectRole
from plane.db.services.file_permission import (
    can_view_file,
    effective_project_role,
)
from plane.storage import minio as storage

logger = logging.getLogger("plane.db.services.file_library")

# ── 常量（FILE-002 §1.7 第 4 行 / §2.6 边界条件）────────────────────
#: 文件库单文件直传上限 50MB（项目库差异；任务附件 25MB 不回改；更大走 FILE-003 分片）
LIBRARY_MAX_FILE_SIZE = 50 * 1024 * 1024
#: 下载预签名有效期（BR-09：FILE-001 DOWNLOAD_URL_TTL 同源 5 分钟）
DOWNLOAD_URL_TTL = 300
#: 回收站保留天数（§2.6；期满由 purge_deleted_assets 按引用计数清理）
TRASH_RETENTION_DAYS = 30
BUCKET = "rp-uploads"
#: 工作空间存储配额缺省 10GB（BR-11「存 WS 设置」——仓库无 WS 设置模型，
#: 以 settings.WS_STORAGE_QUOTA_BYTES 环境级配置承载，偏差登记见任务报告）
DEFAULT_STORAGE_QUOTA_BYTES = 10 * 1024 ** 3

# 名称/大小/类型白名单复用 FILE-001 单一定义点（§1.7 第 4 行：ALLOWED_EXTS 沿用）
from plane.app.services.asset import (  # noqa: E402
    ALLOWED_EXTS,
    _rewrite_to_uploads_prefix,
)


# ── 服务级异常（view 层捕获转 AppException；§2.5 异常表）──────────────
class FolderNameConflict(Exception):
    """同层同名 → 409 RESOURCE_ALREADY_EXISTS / UNIQUE（BR-01）。"""


class FolderDepthExceeded(Exception):
    """目录深度超限 → 409 RESOURCE_LIMIT_EXCEEDED / LIMIT（BR-01）。"""


class FolderCycleError(Exception):
    """移动成环 → 409 RESOURCE_CIRCULAR_DEPENDENCY / CYCLE（BR-04）。"""


class CrossProjectMoveError(Exception):
    """跨项目移动 → 400（UT-04）。"""


class QuotaExceededError(Exception):
    """配额余量不足（含在途预留）→ 409 QUOTA_STORAGE_EXCEEDED / QUOTA（BR-03）。"""

    def __init__(self, *, used: int, pending: int, quota: int, incoming: int):
        self.used = used
        self.pending = pending
        self.quota = quota
        self.incoming = incoming
        super().__init__(f"used={used} pending={pending} quota={quota} incoming={incoming}")


# ── 目录树 CTE 辅助（复用 TASK-004 issue_hierarchy 范式）──────────────
def _ancestor_chain(folder_id: uuid.UUID) -> list:
    """祖先链（含自身，自身 depth=0）上行 CTE；链长触达保险丝视为脏数据告警。"""
    guard = dj_settings.CTE_GUARD_DEPTH
    with connection.cursor() as cursor:
        cursor.execute(
            """
            WITH RECURSIVE chain(id, parent_id, depth) AS (
                SELECT id, parent_id, 0 FROM file_folders WHERE id = %(start)s
                UNION ALL
                SELECT f.id, f.parent_id, c.depth + 1
                  FROM file_folders f JOIN chain c ON f.id = c.parent_id
                 WHERE c.depth < %(guard)s
            )
            SELECT id FROM chain ORDER BY depth DESC""",
            {"start": folder_id, "guard": guard},
        )
        chain = [row[0] for row in cursor.fetchall()]
    if len(chain) >= guard:
        logger.error("file_library.guard_triggered op=ancestor_chain folder_id=%s", folder_id)
    return chain


def folder_depth(folder_id: uuid.UUID) -> int:
    """目录业务深度（根=1）= 祖先链长度。"""
    return len(_ancestor_chain(folder_id))


def _is_descendant(candidate_id: uuid.UUID, of_id: uuid.UUID) -> bool:
    if candidate_id == of_id:
        return True
    return of_id in _ancestor_chain(candidate_id)


def _subtree_height(folder_id: uuid.UUID) -> int:
    """子树高度（子树根自身=1）下行 CTE。"""
    guard = dj_settings.CTE_GUARD_DEPTH
    with connection.cursor() as cursor:
        cursor.execute(
            """
            WITH RECURSIVE subtree(id, depth) AS (
                SELECT id, 0 FROM file_folders WHERE id = %(root)s
                UNION ALL
                SELECT f.id, st.depth + 1 FROM file_folders f
                  JOIN subtree st ON f.parent_id = st.id
                 WHERE st.depth < %(guard)s
            )
            SELECT max(depth) FROM subtree""",
            {"root": folder_id, "guard": guard},
        )
        max_depth = cursor.fetchone()[0] or 0
    if max_depth >= guard:
        logger.error("file_library.guard_triggered op=subtree_height folder_id=%s", folder_id)
    return max_depth + 1


def _subtree_ids(folder_id: uuid.UUID, *, alive_only: bool = True) -> list:
    """子树全部目录 id（含根）；级联软删只作用于存活子树。"""
    where = "WHERE f.deleted_at IS NULL" if alive_only else ""
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            WITH RECURSIVE subtree(id) AS (
                SELECT id FROM file_folders WHERE id = %(root)s
                UNION ALL
                SELECT f.id FROM file_folders f
                  JOIN subtree s ON f.parent_id = s.id {where}
            )
            SELECT id FROM subtree""",
            {"root": folder_id},
        )
        return [row[0] for row in cursor.fetchall()]


# ── 目录树（§4.2 #1：剪枝 + 计数不透出）──────────────────────────────
def folder_tree(*, project, user) -> list[dict]:
    """按请求者可见性剪枝的目录树（扁平行 + parent_id，前端组树）。

    - 逐目录独立判定（``can_view_file`` 单入口，BR-08）；
    - 「到根的全部祖先均可见」为呈现前提：父不可见而子可见的目录整支不呈现；
    - 文件计数为可见子树聚合：不可见子孙的文件不透出（防计数侧信道）。
    """
    folders = list(FileFolder.objects.filter(project=project).order_by("created_at"))
    by_id = {f.id: f for f in folders}
    role = effective_project_role(user, project.id)
    visible = {f.id for f in folders if can_view_file(user, f, role=role)}

    def chain_visible(folder: FileFolder) -> bool:
        cur: FileFolder | None = folder
        while cur is not None:
            if cur.id not in visible:
                return False
            cur = by_id.get(cur.parent_id) if cur.parent_id else None
        return True

    kept = [f for f in folders if chain_visible(f)]
    kept_ids = {f.id for f in kept}

    # 逐文件可见性过滤后按目录聚合（文件可独立收紧可见性，§4.1.1 字段注）
    files = FileAsset.objects.filter(
        project=project,
        entity_type=FileAsset.EntityType.PROJECT_FILE,
        status=FileAsset.Status.UPLOADED,
    ).only("id", "folder_id", "visibility", "allowed_members", "project")
    own_counts: Counter = Counter(
        f.folder_id for f in files if f.folder_id in kept_ids and can_view_file(user, f, role=role)
    )

    # 子树聚合：kept 内父→子传播（父计数 ⊇ 可见子孙计数，不含被剪支）
    totals: dict = dict(own_counts)
    for folder in sorted(kept, key=lambda f: -_tree_depth(f, by_id)):
        if folder.parent_id in totals:
            totals[folder.parent_id] += totals[folder.id]

    children_map: Counter = Counter(f.parent_id for f in kept)
    return [
        {
            "id": str(f.id),
            "name": f.name,
            "parent_id": str(f.parent_id) if f.parent_id else None,
            "depth": _tree_depth(f, by_id),
            "visibility": f.visibility,
            "allowed_members": [str(m) for m in (f.allowed_members or [])],
            "file_count": totals.get(f.id, 0),
            "children_count": children_map.get(f.id, 0),
            "created_at": _iso(f.created_at),
            "updated_at": _iso(f.updated_at),
        }
        for f in kept
    ]


def _tree_depth(folder: FileFolder, by_id: dict) -> int:
    depth = 1
    cur = folder
    while cur.parent_id and cur.parent_id in by_id:
        depth += 1
        cur = by_id[cur.parent_id]
    return depth


# ── 目录 CRUD（§4.2 #2/#3/#4）────────────────────────────────────────
def create_folder(*, project, actor, name: str, parent_id) -> FileFolder:
    """新建目录：Serializer clean 同款查重（全层含根层，BR-01 第一层）。"""
    from rest_framework.exceptions import NotFound

    parent = None
    if parent_id is not None:
        parent = FileFolder.objects.filter(
            pk=parent_id, project=project, deleted_at__isnull=True
        ).first()
        if parent is None:
            raise NotFound("RESOURCE_NOT_FOUND")
    if parent is not None and folder_depth(parent.id) + 1 > dj_settings.MAX_FOLDER_DEPTH:
        raise FolderDepthExceeded(dj_settings.MAX_FOLDER_DEPTH)
    if FileFolder.objects.filter(project=project, parent=parent, name=name).exists():
        raise FolderNameConflict(name)
    try:
        return FileFolder.objects.create(
            project=project, parent=parent, name=name,
            created_by=actor, updated_by=actor,
        )
    except IntegrityError as exc:
        # 第二层：DB 偏条件唯一（非根）/ COALESCE 表达式唯一索引（根）兜并发竞态
        raise FolderNameConflict(name) from exc


def rename_folder(*, folder: FileFolder, name: str) -> None:
    if FileFolder.objects.filter(
        project_id=folder.project_id, parent_id=folder.parent_id, name=name
    ).exclude(pk=folder.pk).exists():
        raise FolderNameConflict(name)
    try:
        folder.name = name
        folder.save(update_fields=["name", "updated_at"])
    except IntegrityError as exc:
        raise FolderNameConflict(name) from exc


def move_folder(*, folder_id: uuid.UUID, new_parent_id, project_id) -> FileFolder:
    """移动目录：环防护 + 深度预算（整棵子树高度），纯元数据操作（§4.3.2）。"""
    with transaction.atomic():
        folder = FileFolder.objects.select_for_update().get(
            id=folder_id, deleted_at__isnull=True
        )
        if new_parent_id is not None:
            parent = FileFolder.objects.filter(
                id=new_parent_id, deleted_at__isnull=True
            ).first()
            if parent is None or parent.project_id != folder.project_id:
                raise CrossProjectMoveError(str(new_parent_id))
            if _is_descendant(candidate_id=parent.id, of_id=folder.id):  # BR-04 CTE 上行
                raise FolderCycleError()
            if (
                folder_depth(parent.id) + 1 + _subtree_height(folder.id) - 1
                > dj_settings.MAX_FOLDER_DEPTH
            ):
                raise FolderDepthExceeded(dj_settings.MAX_FOLDER_DEPTH)
        folder.parent_id = new_parent_id
        folder.save(update_fields=["parent", "updated_at"])
        return folder  # 对象键不动（键结构 §1.4 锁定条款 ①，移动不改键）


def set_folder_visibility(*, folder: FileFolder, visibility: str, allowed_members: list) -> None:
    folder.visibility = visibility
    folder.allowed_members = allowed_members
    folder.save(update_fields=["visibility", "allowed_members", "updated_at"])


def delete_folder(*, folder: FileFolder, actor) -> dict:
    """整树软删（BR-05）——目录连带子树、文件置 deleted_at；对象存储不动（引用计数语义）。"""
    with transaction.atomic():
        subtree = _subtree_ids(folder.id, alive_only=True)
        file_count = FileAsset.objects.filter(
            entity_type=FileAsset.EntityType.PROJECT_FILE,
            folder_id__in=subtree,
            deleted_at__isnull=True,
        ).count()
        now = timezone.now()
        FileFolder.objects.filter(id__in=subtree).update(deleted_at=now, updated_by=actor)
        FileAsset.objects.filter(
            entity_type=FileAsset.EntityType.PROJECT_FILE,
            folder_id__in=subtree,
            deleted_at__isnull=True,
        ).update(deleted_at=now)
    return {"folders_deleted": len(subtree), "files_deleted": file_count}


def restore_folder(*, folder: FileFolder, actor) -> FileFolder:
    """目录整树恢复（BR-07）：原位存在同名目录 → 落根目录并追加 ``(恢复)`` 后缀。"""
    with transaction.atomic():
        parent = (
            FileFolder.all_objects.filter(pk=folder.parent_id).first()
            if folder.parent_id else None
        )
        target_parent: FileFolder | None = None
        if parent is not None:
            # 原位父链：自下而上恢复已删祖先段（整树恢复语义——父在回收站则连父救回；
            # 存活节点之上的链按级联软删不变量恒存活，循环自然终止）
            ancestor: FileFolder | None = parent
            while ancestor is not None and ancestor.deleted_at is not None:
                ancestor.deleted_at = None
                ancestor.save(update_fields=["deleted_at", "updated_at"])
                ancestor = (
                    FileFolder.all_objects.filter(pk=ancestor.parent_id).first()
                    if ancestor.parent_id else None
                )
            target_parent = parent
        name = folder.name
        if FileFolder.objects.filter(
            project_id=folder.project_id, parent=target_parent, name=name
        ).exclude(pk=folder.pk).exists():
            target_parent = None  # 冲突 → 落根 + (恢复) 后缀
            name = f"{name}(恢复)"
            while FileFolder.objects.filter(
                project_id=folder.project_id, parent=None, name=name
            ).exclude(pk=folder.pk).exists():
                name = f"{name}(恢复)"
        subtree = _subtree_ids(folder.id, alive_only=False)
        # 顺序敏感：必须先落根行（含冲突改名），再恢复其余子树——若先整树置活，
        # 根行会以旧名撞根层唯一索引（uniq_folder_name_root，BR-01 第二层）
        FileFolder.all_objects.filter(pk=folder.pk).update(
            parent=target_parent, name=name, deleted_at=None, updated_by=actor
        )
        FileFolder.all_objects.filter(id__in=subtree, deleted_at__isnull=False).update(
            deleted_at=None
        )
        # 注意必须 all_objects：默认管理器已过滤 deleted_at IS NULL，软删行不可达
        FileAsset.all_objects.filter(
            entity_type=FileAsset.EntityType.PROJECT_FILE,
            folder_id__in=subtree,
            deleted_at__isnull=False,
        ).update(deleted_at=None)
    folder.refresh_from_db()
    return folder


# ── 文件列表（§4.2 #5 / §4.2.1）─────────────────────────────────────
_TYPE_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("archive", (".zip", ".7z", ".tar", ".gz", ".rar")),
    ("video", (".mp4", ".mov", ".avi", ".mkv", ".webm")),
    ("image", (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp")),
    ("document", (".pdf", ".txt", ".md", ".log", ".json", ".xml", ".csv",
                  ".xls", ".xlsx", ".doc", ".docx", ".ppt", ".pptx")),
)


def type_category_of(asset: FileAsset) -> str:
    """content_type/ext → image/document/video/archive/other（§4.2.1 type_category）。"""
    attrs = asset.attributes or {}
    mime = (attrs.get("mime") or "").lower()
    ext = (attrs.get("ext") or "").lower()
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("text/") or "pdf" in mime or "word" in mime or "officedocument" in mime:
        return "document"
    if "zip" in mime or "compressed" in mime or "tar" in mime or "gzip" in mime:
        return "archive"
    for category, exts in _TYPE_CATEGORY_RULES:
        if ext in exts:
            return category
    return "other"


def _category_filter(category: str) -> Q:
    if category == "image":
        return Q(attributes__mime__istartswith="image/")
    if category == "video":
        return Q(attributes__mime__istartswith="video/")
    if category == "document":
        return (
            Q(attributes__mime__istartswith="text/")
            | Q(attributes__mime__icontains="pdf")
            | Q(attributes__mime__icontains="word")
            | Q(attributes__mime__icontains="officedocument")
            | Q(attributes__ext__in=[".pdf", ".txt", ".md", ".log", ".json", ".xml",
                                     ".csv", ".xls", ".xlsx", ".doc", ".docx", ".ppt", ".pptx"])
        )
    if category == "archive":
        return (
            Q(attributes__ext__in=[".zip", ".7z", ".tar", ".gz", ".rar"])
            | Q(attributes__mime__icontains="zip")
            | Q(attributes__mime__icontains="compressed")
            | Q(attributes__mime__icontains="gzip")
        )
    # other：排除前四类（取反集）
    return ~(
        Q(attributes__mime__istartswith="image/")
        | Q(attributes__mime__istartswith="video/")
        | Q(attributes__mime__istartswith="text/")
        | Q(attributes__mime__icontains="pdf")
        | Q(attributes__mime__icontains="word")
        | Q(attributes__mime__icontains="officedocument")
        | Q(attributes__mime__icontains="zip")
        | Q(attributes__mime__icontains="compressed")
        | Q(attributes__mime__icontains="gzip")
        | Q(attributes__ext__in=[".zip", ".7z", ".tar", ".gz", ".rar",
                                 ".mp4", ".mov", ".avi", ".mkv", ".webm",
                                 ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp",
                                 ".pdf", ".txt", ".md", ".log", ".json", ".xml", ".csv",
                                 ".xls", ".xlsx", ".doc", ".docx", ".ppt", ".pptx"])
    )


def list_files(*, folder, user, params: dict) -> tuple[list[dict], dict]:
    """目录文件列表（游标 + 筛选 + 逐文件可见性过滤）→ (rows, meta 九字段 + total_size_bytes)。"""
    qs = FileAsset.objects.filter(
        project_id=folder.project_id,
        entity_type=FileAsset.EntityType.PROJECT_FILE,
        folder=folder,
        status=FileAsset.Status.UPLOADED,
    )
    name = params.get("name")
    if name:
        qs = qs.filter(attributes__name__icontains=name)  # trgm 索引取数（§4.1.3）
    category = params.get("type")
    if category:
        qs = qs.filter(_category_filter(category))
    uploader = params.get("uploaded_by")
    if uploader:
        qs = qs.filter(uploaded_by_id=uploader)
    ordering = params.get("ordering") or "-created_at"
    order_map = {
        "created_at": "created_at", "updated_at": "updated_at",
        "name": "attributes__name", "size": "size",
    }
    key = ordering.lstrip("-")
    if key not in order_map:
        ordering = "-created_at"
        key = "created_at"
    direction = "" if ordering.startswith("-") else "-"
    qs = qs.order_by(f"{direction}{order_map[key]}", "-id")

    rows_all = list(qs.select_related("uploaded_by"))
    role = effective_project_role(user, folder.project_id)
    visible_rows = [a for a in rows_all if can_view_file(user, a, role=role)]

    per_page = params.get("per_page") or 50
    offset = params.get("offset") or 0
    page_rows = visible_rows[offset:offset + per_page]
    total_count = len(visible_rows)
    total_size = sum(a.size for a in visible_rows)

    def _enc(off: int) -> str | None:
        return base64.b64encode(f"c:{off}:0".encode()).decode() if 0 <= off < total_count else None

    next_cursor = _enc(offset + per_page) if offset + per_page < total_count else None
    prev_cursor = _enc(offset - per_page) if offset > 0 else None
    expand = params.get("expand") == "uploaded_by"
    meta = {
        "next_cursor": next_cursor,
        "prev_cursor": prev_cursor,
        "next_page_results": next_cursor is not None,
        "prev_page_results": prev_cursor is not None,
        "count": len(page_rows),
        "total_count": total_count,
        "total_pages": (total_count + per_page - 1) // per_page,
        "page": offset // per_page + 1,
        "per_page": per_page,
        "total_size_bytes": total_size,  # §4.2.1 扩展字段：目录容量展示
    }
    return [file_row(a, expand_uploaded_by=expand) for a in page_rows], meta


# ── 上传三步（§4.2 #6/#14；协议复用 FILE-001 §4.3.1/§4.3.2）──────────
def presign_file(*, folder: FileFolder, payload: dict, actor) -> dict:
    """文件库 presign：扩展名白名单（FILE-001 沿用）→ ≤50MB → 配额（含在途预留）。

    落库 entity_type=project_file、entity_id=folder_id，folder 外键同值绑定
    （§1.7 第 1 行）；可见性默认随目录（§4.1.1 字段注）。
    """
    name = Path(payload["file_name"]).name  # basename 化防路径注入
    ext = Path(name).suffix.lower()
    mime = (payload.get("content_type") or "").lower()
    size = int(payload["file_size"])

    if ext not in ALLOWED_EXTS:
        raise AppException(
            "VALIDATION_FILE_TYPE_NOT_ALLOWED",
            message="不支持的文件类型",
            details=[{"field": "file_name", "code": "INVALID",
                      "message": f"仅支持 {sorted(ALLOWED_EXTS)}"}],
        )
    if size <= 0:
        raise AppException(
            "VALIDATION_FILE_SIZE_EXCEEDED",
            message="不接受空文件",
            details=[{"field": "file_size", "code": "TOO_SMALL", "message": "空文件"}],
        )
    if size > LIBRARY_MAX_FILE_SIZE:
        raise AppException(
            "VALIDATION_FILE_SIZE_EXCEEDED",
            message="文件大小超出限制",
            details=[{"field": "file_size", "code": "TOO_LARGE",
                      "message": "单文件不能超过 50MB，超大文件请等待分片上传支持"}],
        )
    assert_quota(workspace_id=folder.project.workspace_id, incoming=size)  # BR-03

    key = "/".join([
        str(folder.project.workspace_id), str(folder.project_id),
        FileAsset.EntityType.PROJECT_FILE, str(folder.id), f"{ulid_new()}{ext}",
    ])
    asset = FileAsset.objects.create(
        workspace_id=folder.project.workspace_id,
        project_id=folder.project_id,
        entity_type=FileAsset.EntityType.PROJECT_FILE,
        entity_id=folder.id,
        folder=folder,
        attributes={"name": name, "size": size, "mime": mime, "ext": ext},
        size=size,
        storage_path=key,
        uploaded_by=actor,
        visibility=folder.visibility,          # 默认随目录（可独立收紧）
        allowed_members=list(folder.allowed_members or []),
        created_by=actor,
        updated_by=actor,
    )
    try:
        upload_url = storage.presigned_put_url(
            bucket=BUCKET, key=key,
            content_type=mime or "application/octet-stream",
            content_length_range=(1, LIBRARY_MAX_FILE_SIZE),
        )
    except storage.StorageUnavailable as exc:
        logger.warning("file_library.presign_failed folder=%s err=%s", folder.id, exc)
        raise AppException("SERVER_STORAGE_ERROR", message="对象存储暂时不可用，请稍后重试") from exc
    upload_url = _rewrite_to_uploads_prefix(upload_url)
    return {
        "asset_id": str(asset.id),
        "upload_url": upload_url,
        "fields": {"Content-Type": mime or "application/octet-stream"},
        "expires_at": _iso(timezone.now() + timedelta(seconds=storage.DEFAULT_PRESIGN_EXPIRES)),
        "expires_in": storage.DEFAULT_PRESIGN_EXPIRES,  # §4.2.2 结构；expires_at 与 FILE-001 同源
    }


def complete_file(*, asset: FileAsset) -> dict:
    """完成确认（FILE-001 §4.3.2 协议复用）：HEAD 校验 → 条件 UPDATE 翻转；幂等。"""
    if asset.status == FileAsset.Status.UPLOADED:
        return file_row(asset)  # 幂等快路径：重放同构
    try:
        stat = storage.head_object_size(bucket=BUCKET, key=asset.storage_path)
    except storage.StorageObjectNotFound as exc:
        raise AppException(
            "VALIDATION_FILE_UPLOAD_MISMATCH",
            message="对象校验失败，请重新上传",
            details=[{"field": "asset", "code": "DOES_NOT_EXIST",
                      "message": "存储中未找到该文件，请重新上传"}],
        ) from exc
    except storage.StorageUnavailable as exc:
        logger.warning("file_library.head_failed asset=%s err=%s", asset.id, exc)
        raise AppException("SERVER_STORAGE_ERROR", message="对象存储暂时不可用，请稍后重试") from exc
    if stat != asset.size:
        raise AppException(
            "VALIDATION_FILE_UPLOAD_MISMATCH",
            message="对象大小与声明不一致，请重新上传",
            details=[{"field": "file_size", "code": "INVALID",
                      "message": "对象大小与声明不一致，请重新上传"}],
        )
    with transaction.atomic():
        FileAsset.objects.filter(
            pk=asset.pk, status=FileAsset.Status.UPLOADING
        ).update(status=FileAsset.Status.UPLOADED, is_uploaded=True)
    asset.refresh_from_db()
    return file_row(asset)


# ── 下载预签名（§4.2 #7；BR-09 签发时实时校验 + BR-10 计数）──────────
def file_download_url(*, asset: FileAsset, user) -> dict:
    """预签名 GET（5 分钟）：签发时执行与列表同源的 ``can_view_file`` 判定（§4.3.1）。"""
    from plane.db.services.file_permission import assert_can_view

    if asset.status != FileAsset.Status.UPLOADED:
        from rest_framework.exceptions import NotFound

        raise NotFound("RESOURCE_NOT_FOUND")
    assert_can_view(user, asset)  # → 404 存在性隐藏
    from plane.bgtasks.file_stats import incr_download_count

    incr_download_count(asset.id)  # BR-10 异步累加（Redis 计数 → beat 批量落库）
    from urllib.parse import quote

    filename = (asset.attributes or {}).get("name", "download")
    try:
        url = storage.presigned_get_url(
            bucket=BUCKET,
            key=asset.storage_path,
            expires=DOWNLOAD_URL_TTL,
            response_headers={
                "response-content-disposition": (
                    f"attachment; filename*=UTF-8''{quote(filename)}"
                ),
            },
        )
    except storage.StorageUnavailable as exc:
        logger.warning("file_library.sign_get_failed asset=%s err=%s", asset.id, exc)
        raise AppException("SERVER_STORAGE_ERROR", message="对象存储暂时不可用，请稍后重试") from exc
    return {"download_url": _rewrite_to_uploads_prefix(url), "expires_in": DOWNLOAD_URL_TTL}


# ── 文件更新 / 删除 / 恢复 / 彻底删除（§4.2 #8~#13）───────────────────
def update_file(*, asset: FileAsset, payload: dict) -> FileAsset:
    """重命名 / 移动（folder 同值同步 entity_id，§1.7 第 1 行）/ 双挂（附加任务）。"""
    from rest_framework.exceptions import NotFound

    project_id = asset.project_id
    if project_id is None:  # 文件库域行恒有项目；防御多态域误用
        raise NotFound("RESOURCE_NOT_FOUND")
    with transaction.atomic():
        fields = ["updated_at"]
        if "name" in payload and payload["name"] is not None:
            attrs = dict(asset.attributes or {})
            attrs["name"] = payload["name"]
            asset.attributes = attrs
            fields.append("attributes")
        if "folder_id" in payload:
            new_folder_id = payload["folder_id"]
            if new_folder_id is not None:
                target = FileFolder.objects.filter(
                    pk=new_folder_id, project_id=project_id, deleted_at__isnull=True
                ).first()
                if target is None:
                    raise NotFound("RESOURCE_NOT_FOUND")
                asset.folder = target
                asset.entity_id = target.id  # 多态列同值同步（§1.7 演进登记）
            else:
                asset.folder = None
            fields.extend(["folder", "entity_id"])
        if "issue_id" in payload:
            new_issue_id = payload["issue_id"]
            if new_issue_id is not None:
                issue = Issue.objects.filter(
                    pk=new_issue_id, project_id=project_id, deleted_at__isnull=True
                ).first()
                if issue is None:
                    raise NotFound("RESOURCE_NOT_FOUND")
                asset.issue = issue  # 双挂：第二视图入口（§1.2）
            else:
                asset.issue = None  # 解除挂接：对象与文件库行均无损（UT-11）
            fields.append("issue")
        if "visibility" in payload and payload["visibility"] is not None:
            asset.visibility = payload["visibility"]
            fields.append("visibility")
        if "allowed_members" in payload and payload["allowed_members"] is not None:
            asset.allowed_members = payload["allowed_members"]
            fields.append("allowed_members")
        asset.save(update_fields=fields)
    return asset


def soft_delete_file(*, asset: FileAsset, actor) -> None:
    """软删进回收站：对象不动（BR-06 引用计数语义，30 天期满由 beat 清理）。"""
    FileAsset.objects.filter(pk=asset.pk).update(
        deleted_at=timezone.now(), updated_by=actor,
    )


def restore_file(*, asset: FileAsset, actor) -> FileAsset:
    """回收站还原：原目录存活即原位；目录仍在回收站 → 先整链恢复目录（BR-07）。"""
    folder = (
        FileFolder.all_objects.filter(pk=asset.folder_id).first() if asset.folder_id else None
    )
    if folder is not None and folder.deleted_at is not None:
        restore_folder(folder=folder, actor=actor)
    # 注意必须 all_objects：默认管理器已过滤 deleted_at IS NULL，软删行不可达
    FileAsset.all_objects.filter(pk=asset.pk).update(deleted_at=None)
    asset.refresh_from_db()
    return asset


def has_live_references(storage_path: str, *, exclude_pk=None) -> bool:
    """BR-06 键级引用计数：同 ``storage_path`` 是否还有其他存活 file_assets 行。

    （FILE-003 版本行 file_versions 落地后在此追加第二段判定——当前无该表。）
    """
    qs = FileAsset.objects.filter(storage_path=storage_path)  # 默认管理器 = 存活行
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return qs.exists()


def purge_asset(*, asset: FileAsset) -> None:
    """彻底删除：硬删元数据行（purged 终态）+ 无存活引用才删对象（BR-06）。"""
    if not has_live_references(asset.storage_path, exclude_pk=asset.pk):
        try:
            storage.remove_object(bucket=BUCKET, key=asset.storage_path)
        except storage.StorageUnavailable as exc:
            logger.warning("file_library.purge_remove_failed asset=%s err=%s", asset.id, exc)
            raise AppException("SERVER_STORAGE_ERROR", message="对象存储暂时不可用，请稍后重试") from exc
    asset.delete()  # 实例删除 = 硬删


# ── 回收站列表（§4.2 #11：R1 同键口径过滤）──────────────────────────
def trash_rows(*, project, user, params: dict) -> tuple[list[dict], dict]:
    """回收站：ADMIN/WS_ADMIN 见全量；CONTRIBUTOR 仅见本人删除项（uploaded_by == 本人，
    BR-13 与 restore 同码同键过滤——CONTRIBUTOR 删自己文件后必须能看到并自恢复）。"""
    qs = FileAsset.all_objects.filter(
        project=project,
        entity_type=FileAsset.EntityType.PROJECT_FILE,
        deleted_at__isnull=False,
    ).select_related("uploaded_by")
    role = effective_project_role(user, project.id)
    if role is None or role < ProjectRole.ADMIN:
        qs = qs.filter(uploaded_by_id=user.id)
    ordering = params.get("ordering") or "-deleted_at"
    if ordering.lstrip("-") not in ("deleted_at", "created_at"):
        ordering = "-deleted_at"
    qs = qs.order_by(ordering, "-id")
    rows_all = list(qs)
    per_page = params.get("per_page") or 50
    offset = params.get("offset") or 0
    page_rows = rows_all[offset:offset + per_page]
    total = len(rows_all)

    def _enc(off: int):
        return base64.b64encode(f"c:{off}:0".encode()).decode() if 0 <= off < total else None

    next_cursor = _enc(offset + per_page) if offset + per_page < total else None
    prev_cursor = _enc(offset - per_page) if offset > 0 else None
    meta = {
        "next_cursor": next_cursor,
        "prev_cursor": prev_cursor,
        "next_page_results": next_cursor is not None,
        "prev_page_results": prev_cursor is not None,
        "count": len(page_rows),
        "total_count": total,
        "total_pages": (total + per_page - 1) // per_page,
        "page": offset // per_page + 1,
        "per_page": per_page,
    }
    out = []
    for a in page_rows:
        row = file_row(a)
        row["deleted_at"] = _iso(a.deleted_at) if a.deleted_at else None
        out.append(row)
    return out, meta


# ── 配额（§4.3.4：行锁串行化 + 在途预留 + 对象键 DISTINCT）────────────
def get_workspace_quota(workspace_id) -> int:
    """配额来源：settings.WS_STORAGE_QUOTA_BYTES（默认 10GB，BR-11「可配置」）。"""
    return int(getattr(dj_settings, "WS_STORAGE_QUOTA_BYTES", DEFAULT_STORAGE_QUOTA_BYTES))


def _distinct_key_sum(qs) -> int:
    """按 DISTINCT storage_path 求和——双挂/引用计数下同键多行只计一次体积。"""
    ids = list(qs.order_by("storage_path").distinct("storage_path").values_list("id", flat=True))
    return FileAsset.objects.filter(id__in=ids).aggregate(s=Sum("size"))["s"] or 0


def workspace_storage_usage(workspace_id) -> dict:
    """§4.2.3 配额用量：{quota_bytes, used_bytes, pending_bytes, usage_ratio}。"""
    quota = get_workspace_quota(workspace_id)
    used = _distinct_key_sum(
        FileAsset.objects.filter(
            workspace_id=workspace_id, status=FileAsset.Status.UPLOADED,
            deleted_at__isnull=True,
        )
    )
    pending = _distinct_key_sum(
        FileAsset.objects.filter(
            workspace_id=workspace_id, status=FileAsset.Status.UPLOADING,
        )
    )
    ratio = round(used / quota, 2) if quota > 0 else 1.0
    return {"quota_bytes": quota, "used_bytes": used, "pending_bytes": pending,
            "usage_ratio": ratio}


def assert_quota(*, workspace_id, incoming: int) -> None:
    """配额判定（§4.3.4 原文实现）：Workspace 行锁串行化并发判定 + 在途预留。

    行锁必须求值（赋给 ``_``），否则 Django 丢弃仅 SELECT 的裸锁、失去串行化
    语义（FILE-001 ``_check_task_limit`` 同一先例）。两笔临界并发 presign 一先
    一后进入判定，后到者 Sum 即读到先行者落库的 uploading 行 → 恰一笔 409。
    """
    with transaction.atomic():
        locked = (
            Workspace.objects.select_for_update()
            .filter(pk=workspace_id)
            .only("id")
            .first()
        )
        _ = locked  # 求值即获取行锁
        quota = get_workspace_quota(workspace_id)
        # 按 workspace 直查（非 project__workspace），杜绝按项目聚合漏掉的无项目行
        used = _distinct_key_sum(
            FileAsset.objects.filter(
                workspace_id=workspace_id, status=FileAsset.Status.UPLOADED,
                deleted_at__isnull=True,
            )
        )
        pending = _distinct_key_sum(
            FileAsset.objects.filter(
                workspace_id=workspace_id, status=FileAsset.Status.UPLOADING,
            )
        )
        if used + pending + incoming > quota:  # 在途计入（BR-03）
            raise QuotaExceededError(used=used, pending=pending, quota=quota, incoming=incoming)


# ── 行序列化（§4.2.1）───────────────────────────────────────────────
def file_row(asset: FileAsset, *, expand_uploaded_by: bool = False) -> dict:
    attrs = asset.attributes or {}
    row: dict = {
        "id": str(asset.id),
        "name": attrs.get("name", ""),
        "size_bytes": asset.size,
        "content_type": attrs.get("mime", ""),
        "type_category": type_category_of(asset),
        "visibility": asset.visibility,
        "folder_id": str(asset.folder_id) if asset.folder_id else None,
        "issue_id": str(asset.issue_id) if asset.issue_id else None,
        "uploaded_by": str(asset.uploaded_by_id) if asset.uploaded_by_id else None,
        "download_count": asset.download_count,
        "status": asset.status,
        "created_at": _iso(asset.created_at),
        "updated_at": _iso(asset.updated_at),
    }
    if expand_uploaded_by and asset.uploaded_by is not None:
        # §4.2.1 注：对象形态仅在 ?expand=uploaded_by 时追加（原 ID 字段照常保留）
        row["uploaded_by_detail"] = {
            "id": str(asset.uploaded_by.id),
            "display_name": asset.uploaded_by.display_name,
        }
    return row


def _iso(dt) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
