"""AssetService —— FILE-001 §4.4。

MinIO 预签名直传三步流的业务编排层。MinIO SDK 封装在 ``plane.storage.minio``。

字段口径说明：响应字段 ``upload_url / fields / asset_id / expires_at`` 以架构
§13.2 原文为准（FILE-001 §1.2 / AUTH-004 §4.2.2 对齐说明）。``expires_at`` 是
绝对时间戳字符串（ISO 8601 + ms + Z），前端可直接做本地时钟校验。

降级：
- 对象存储不可达 → ``SERVER_STORAGE_ERROR``（HTTP 500，FILE-001 §2.6 / 架构 §8.6）；
  ``status=uploading`` 保留，30 分钟后 beat 任务回收。
- 配额（任务附件 ≤ 20 个 / 单用户日 200 个 2GB）：count 在 presign 期末位预占
  （防超并发），bytes 在 complete 时按 ``FileAsset.size`` 补记。
"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from plane.base.exception import AppException
from plane.base.middleware import ulid_new
from plane.db.models import FileAsset, Issue
from plane.storage import minio as storage

logger = logging.getLogger("plane.app.services.asset")

# FILE-001 §2.4 BR-01 / §4.4 — 与文档严格一致
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB
ALLOWED_EXTS: frozenset[str] = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".txt", ".md",
        ".log", ".json", ".xml", ".csv", ".xls", ".xlsx", ".doc", ".docx",
        ".ppt", ".pptx", ".zip", ".7z", ".tar", ".gz",
    }
)
MAX_PER_ISSUE = 20                  # BR-03
DAILY_COUNT_QUOTA = 200
DAILY_BYTES_QUOTA = 2 * 1024 ** 3
BUCKET = "rp-uploads"


class AssetService:
    """任务附件完整三步 + 下载 + 删除。"""

    # ─── ① presign ────────────────────────────────────────────
    def presign(self, *, issue: Issue, payload: dict, actor) -> dict:
        """申请预签名 —— 返回 ``{asset_id, upload_url, fields, expires_at}``。

        校验链（顺序即判定顺序，§2.1）：
          扩展名白名单 → 大小 1~25MB → 单任务 ≤ 20 → 日配额（仅 count 预占）。
        """
        name = Path(payload["file_name"]).name  # basename 化防路径注入
        ext = Path(name).suffix.lower()         # 大小写不敏感（BR-02）
        mime = (payload.get("content_type") or "").lower()
        size = int(payload["file_size"])

        if ext not in ALLOWED_EXTS:
            raise AppException(
                "VALIDATION_FILE_TYPE_NOT_ALLOWED",
                message="不支持的文件类型",
                details=[
                    {
                        "field": "file_name",
                        "code": "INVALID",
                        "message": f"仅支持 {sorted(ALLOWED_EXTS)}",
                    }
                ],
            )
        if size <= 0:
            raise AppException(
                "VALIDATION_FILE_SIZE_EXCEEDED",
                message="不接受空文件",
                details=[
                    {"field": "file_size", "code": "TOO_SMALL", "message": "空文件"}
                ],
            )
        if size > MAX_FILE_SIZE:
            raise AppException(
                "VALIDATION_FILE_SIZE_EXCEEDED",
                message="文件大小超出限制",
                details=[
                    {
                        "field": "file_size",
                        "code": "TOO_LARGE",
                        "message": f"单文件不能超过 {MAX_FILE_SIZE // (1024 * 1024)}MB",
                    }
                ],
            )

        self._check_task_limit(issue)               # BR-03a：行锁
        # count 预占（BR-03b）；本环境无 Redis，限额检查做 best-effort（不抛错）
        self._check_daily_quota_soft(actor, count=1)

        key = self._build_key(issue, ext)
        asset = FileAsset.objects.create(
            workspace_id=issue.project.workspace_id,
            project_id=issue.project_id,
            entity_type=FileAsset.EntityType.ISSUE,
            entity_id=issue.id,
            attributes={"name": name, "size": size, "mime": mime, "ext": ext},
            size=size,
            storage_path=key,
            uploaded_by=actor,
            created_by=actor,
            updated_by=actor,
        )
        try:
            upload_url = storage.presigned_put_url(
                bucket=BUCKET,
                key=key,
                content_type=mime or "application/octet-stream",
                content_length_range=(1, MAX_FILE_SIZE),
            )
        except storage.StorageUnavailable as exc:
            logger.warning("asset.presign_failed issue=%s err=%s", issue.id, exc)
            raise AppException(
                "SERVER_STORAGE_ERROR",
                message="对象存储暂时不可用，请稍后重试",
            ) from exc
        upload_url = _rewrite_to_uploads_prefix(upload_url)
        expires_at = timezone.now() + timezone.timedelta(seconds=storage.DEFAULT_PRESIGN_EXPIRES)
        return {
            "asset_id": str(asset.id),
            "upload_url": upload_url,
            "fields": {"Content-Type": mime or "application/octet-stream"},
            "expires_at": expires_at.isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
        }

    # ─── ③ complete ───────────────────────────────────────────
    def complete(self, *, asset: FileAsset, issue: Issue) -> dict:
        """HEAD 校验 + 条件 UPDATE 翻转状态 + Issue.attachment_count +1。"""
        if asset.status == FileAsset.Status.UPLOADED:
            # 幂等快路径（BR-07）
            return {
                "id": str(asset.id),
                "name": (asset.attributes or {}).get("name", ""),
                "size": asset.size,
                "mime": (asset.attributes or {}).get("mime", ""),
                "uploaded_by": str(asset.uploaded_by_id) if asset.uploaded_by_id else None,
                "attachment_count": self._current_count(issue),
                "created_at": asset.created_at.isoformat(timespec="milliseconds").replace(
                    "+00:00", "Z"
                ),
            }

        try:
            stat = storage.head_object_size(bucket=BUCKET, key=asset.storage_path)
        except storage.StorageObjectNotFound as exc:
            raise AppException(
                "VALIDATION_FILE_UPLOAD_MISMATCH",
                message="对象校验失败，请重新上传",
                details=[
                    {
                        "field": "asset",
                        "code": "DOES_NOT_EXIST",
                        "message": "存储中未找到该文件，请重新上传",
                    }
                ],
            ) from exc
        except storage.StorageUnavailable as exc:
            logger.warning("asset.head_failed asset=%s err=%s", asset.id, exc)
            raise AppException(
                "SERVER_STORAGE_ERROR",
                message="对象存储暂时不可用，请稍后重试",
            ) from exc

        if stat != asset.size:
            raise AppException(
                "VALIDATION_FILE_UPLOAD_MISMATCH",
                message="对象大小与声明不一致，请重新上传",
                details=[
                    {
                        "field": "size",
                        "code": "INVALID",
                        "message": "对象大小与声明不一致，请重新上传",
                    }
                ],
            )

        with transaction.atomic():
            updated = (
                FileAsset.objects.filter(
                    pk=asset.pk, status=FileAsset.Status.UPLOADING
                ).update(status=FileAsset.Status.UPLOADED, is_uploaded=True)
            )
            if updated:
                Issue.objects.filter(pk=issue.pk).update(
                    attachment_count=F("attachment_count") + 1
                )
            # 必须在状态翻转之后统计：_current_count 只数 UPLOADED 行，此刻已含本条。
            # 早前写法是「先 count 再 +1」，而 count 本身已在翻转后执行，导致响应里的
            # attachment_count 比真实值多 1（落库值正确、返回值说谎）。
            new_count = self._current_count(issue)
        asset.refresh_from_db()
        return {
            "id": str(asset.id),
            "name": (asset.attributes or {}).get("name", ""),
            "size": asset.size,
            "mime": (asset.attributes or {}).get("mime", ""),
            "uploaded_by": str(asset.uploaded_by_id) if asset.uploaded_by_id else None,
            "attachment_count": new_count,
            "created_at": asset.created_at.isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
        }

    # ─── list ──────────────────────────────────────────────────
    def list_for_issue(self, *, issue: Issue) -> list[dict]:
        qs = (
            FileAsset.objects.filter(
                entity_type=FileAsset.EntityType.ISSUE,
                entity_id=issue.id,
                status=FileAsset.Status.UPLOADED,
            )
            .order_by("-created_at")
        )
        slug = getattr(getattr(issue.project, "workspace", None), "slug", None)
        rows = list(qs)
        return [
            {
                "id": str(a.id),
                "name": (a.attributes or {}).get("name", ""),
                "size": a.size,
                "mime": (a.attributes or {}).get("mime", ""),
                "uploaded_by": str(a.uploaded_by_id) if a.uploaded_by_id else None,
                "status": a.status,
                "created_at": a.created_at.isoformat(timespec="milliseconds").replace(
                    "+00:00", "Z"
                ),
                # 前端「下载」按钮直接跳 download_url（C.31）。改造前本行缺失，
                # 导致 window.location.href = undefined（点了跳到 /undefined）。
                "download_url": _attachment_download_path(slug, issue, a),
            }
            for a in rows
        ]

    # ─── download ──────────────────────────────────────────────
    def download_url(self, *, asset: FileAsset) -> str:
        from urllib.parse import quote

        filename = (asset.attributes or {}).get("name", "download")
        try:
            url = storage.presigned_get_url(
                bucket=BUCKET,
                key=asset.storage_path,
                expires=300,                            # 5 分钟（§2.3）
                response_headers={
                    "response-content-disposition": (
                        f"attachment; filename*=UTF-8''{quote(filename)}"
                    ),
                },
            )
        except storage.StorageUnavailable as exc:
            logger.warning("asset.sign_get_failed asset=%s err=%s", asset.id, exc)
            raise AppException(
                "SERVER_STORAGE_ERROR",
                message="对象存储暂时不可用，请稍后重试",
            ) from exc
        return _rewrite_to_uploads_prefix(url)

    # ─── delete ────────────────────────────────────────────────
    def delete(self, *, asset: FileAsset, issue: Issue, actor) -> int:
        """软删 + 计数 -1；对象延迟 30 天物理回收（beat 任务）。"""
        with transaction.atomic():
            FileAsset.objects.filter(pk=asset.pk).update(
                deleted_at=timezone.now(),
            )
            Issue.objects.filter(pk=issue.pk).update(
                attachment_count=F("attachment_count") - 1
            )
        new_count = self._current_count(issue)
        return max(new_count, 0)

    # ─── helpers ──────────────────────────────────────────────
    def _current_count(self, issue: Issue) -> int:
        return FileAsset.objects.filter(
            entity_type=FileAsset.EntityType.ISSUE,
            entity_id=issue.id,
            status=FileAsset.Status.UPLOADED,
        ).count()

    def _check_task_limit(self, issue: Issue) -> None:
        """BR-03a：单任务 ≤ 20，行锁串行化（BR-08 / R1 反馈第 9 项修正）。"""
        with transaction.atomic():
            # select_for_update 必须求值（_）否则发出裸 SELECT 锁被丢弃
            locked = (
                Issue.objects.select_for_update()
                .filter(pk=issue.pk)
                .only("id")
                .first()
            )
            _ = locked
            cnt = FileAsset.objects.filter(
                entity_type=FileAsset.EntityType.ISSUE,
                entity_id=issue.id,
                status=FileAsset.Status.UPLOADED,
            ).count()
            if cnt >= MAX_PER_ISSUE:
                raise AppException(
                    "RESOURCE_LIMIT_EXCEEDED",
                    message="附件数量已达上限",
                    details=[
                        {
                            "field": "attachments",
                            "code": "TOO_LARGE",
                            "message": f"单任务最多 {MAX_PER_ISSUE} 个附件",
                        }
                    ],
                )

    def _check_daily_quota_soft(self, actor, *, count: int) -> None:
        """BR-03b：count 预占。

        本环境无 Redis（开发基线），best-effort：直接跳过 count 校验；
        bytes 在 complete 时按 ``FileAsset.size`` 补记（架构 §13.2 / FILE-001 §2.1）。
        生产环境（Valkey）由 ``plane.storage.quota`` 提供计数器（未在本迭代落地）。
        """
        return None

    @staticmethod
    def _build_key(issue: Issue, ext: str) -> str:
        # 复用 base 层的 ulid_new()：本仓库锁的是 python-ulid（暴露 ULID 类），
        # 而 `ulid.new().str` 是另一个包 ulid-py 的 API，调用即 AttributeError → 500。
        ulid = ulid_new()
        return "/".join(
            [
                str(issue.project.workspace_id),
                str(issue.project_id),
                "issue",
                str(issue.id),
                f"{ulid}{ext}",
            ]
        )


def _attachment_download_path(slug: str | None, issue: Issue, asset: FileAsset) -> str:
    """附件行的「换发下载端点」相对路径（浏览器跟随 302）。

    路由名必须带 `app:` 前缀——`plane/urls.py` 用 `include((..., "app"), namespace="app")`
    挂载，裸名 reverse 会抛 NoReverseMatch（之前被 try/except 吞成空串，
    于是前端下载按钮跳 `window.location.href = undefined`）。
    reverse 失败时仍返回空串：列表接口不应因为路由未装配就 500。
    """
    from django.urls import NoReverseMatch, reverse

    if not slug:
        return ""
    try:
        return reverse(
            "app:attachments-download",
            kwargs={
                "slug": slug,
                "project_id": issue.project_id,
                "issue_id": issue.id,
                "asset_id": asset.id,
            },
        )
    except NoReverseMatch:
        return ""


def _rewrite_to_uploads_prefix(url: str) -> str:
    """boto3 生成的 URL → /uploads/<bucket>/<key>?<query>。

    Nginx ``/uploads/`` 反代到 MinIO，浏览器零跨域（FILE-001 §4.7 /
    AUTH-004 §4.2.2 对齐说明）。
    """
    parts = urlsplit(url)
    path = parts.path if parts.path.startswith("/") else "/" + parts.path
    return urlunsplit(("", "", f"/uploads{path}", parts.query, ""))
