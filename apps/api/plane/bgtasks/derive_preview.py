"""预览派生与会话治理任务（FILE-003 §4.3.3）。

四任务：
- ``derive_preview(version_id)`` —— 五通道按 type_category 分派：图片缩略（PIL，
  ≤512px WebP）/ Office 转码（soffice headless → PDF）/ 视频封面帧（ffmpeg -ss 0
  -frames:v 1）。``soft_time_limit=300 + max_retries=2``；工具缺失（FileNotFoundError）
  属环境性失败——登记 failed 不重试（重试无意义），重试入口在预览调度侧按需重排。
- ``sweep_cold_derivatives`` —— 30 天未访问衍生物冷清理（BR-11），按需重生成。
- ``expire_upload_sessions`` —— 24h 未完成会话 Abort + expired + 释放配额预留
  （BR-05；预留释放 = 会话行出 Σ 活跃会话，扩展口径见 file_library.assert_quota）。
- ``evict_old_versions`` —— BR-08 版本上限 20 滚动淘汰薄壳（逻辑在
  services.upload_session.evict_old_versions，供服务层单测复用）。

转码工具链：本机/镜像缺 soffice/ffmpeg 时走「转码失败」语义（failed 登记 +
预览侧重试/下载兜底，§2.5），不静默假装成功；worker 镜像 apt 分层见
apps/api/Dockerfile（compose worker 服务 INSTALL_TRANSCODE_TOOLS=1）。

清理任务均带 ``restrict_workspace_id`` 测试安全参数（坑 18：pytest 直连共享
dev PG，不限域的全表操作会误伤主栈数据）；beat 生产调用不传。
"""
from __future__ import annotations

import io
import logging
import shutil
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.utils import timezone

logger = logging.getLogger("plane.bgtasks.derive_preview")

#: soffice / ffmpeg 子进程超时（soft_time_limit=300 留 30s 余量做落库与上传）
TRANSCODE_PROC_TIMEOUT = 270
#: soffice 二进制候选名（Debian 分层镜像装的是 libreoffice-core + 组件）
SOFFICE_BIN = shutil.which("soffice") or shutil.which("libreoffice")
FFMPEG_BIN = shutil.which("ffmpeg")


class TranscodeToolMissing(Exception):
    """宿主缺 soffice/ffmpeg——环境性失败（登记 failed，不消耗重试）。"""


# ── 子进程封装（工具缺失 / 超时 / 非零退出三分）──────────────────────
def _run_tool(args: list[str], *, cwd: str | None = None) -> Path:
    """执行外部转码命令；返回产物所在目录（调用方按名查找）。"""
    if args[0] in ("soffice", "libreoffice") and not SOFFICE_BIN:
        raise TranscodeToolMissing("soffice")
    if args[0] == "ffmpeg" and not FFMPEG_BIN:
        raise TranscodeToolMissing("ffmpeg")
    proc = subprocess.run(  # noqa: S603 —— 参数全为服务端常量，无用户输入拼接
        [args[0] if args[0] not in ("soffice", "libreoffice") else SOFFICE_BIN or args[0]]
        + args[1:],
        cwd=cwd, capture_output=True, timeout=TRANSCODE_PROC_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"{args[0]} 退出码 {proc.returncode}: {proc.stderr.decode(errors='replace')[:200]}"
        )
    return Path(cwd or ".")


def _thumb_webp(body: bytes, *, max_px: int) -> bytes:
    """图片缩略：PIL 就地解码 → thumbnail（保持长宽比 ≤ max_px）→ WebP。"""
    from PIL import Image

    with Image.open(io.BytesIO(body)) as img:
        img.thumbnail((max_px, max_px))
        out = io.BytesIO()
        img.save(out, format="WEBP", quality=85)
        return out.getvalue()


@shared_task(
    bind=True,
    max_retries=2,
    soft_time_limit=300,
    name="plane.bgtasks.derive_preview.derive_preview",
)
def derive_preview(self, version_id: str) -> dict:
    """预览派生主任务（§4.3.3）：image→缩略 / office→PDF / video→封面帧。

    运行期失败语义：
    - 工具缺失（环境性）→ failed 登记，不重试；
    - 子进程失败/超时 → self.retry 退避重试，耗尽后 failed 登记；
    - 原文件对象恒不动（失败无损，§2.5）。
    """
    from plane.db.models import FileVersion
    from plane.db.services import file_library as flib
    from plane.db.services import upload_session as usvc
    from plane.storage import minio as storage

    try:
        version = FileVersion.objects.select_related("asset").get(id=version_id)
    except FileVersion.DoesNotExist:
        logger.warning("derive_preview.version_gone id=%s", version_id)
        return {"version_id": version_id, "skipped": True}
    attrs = version.attributes or {}
    kind = usvc._preview_kind(
        attrs.get("mime") or attrs.get("content_type") or "",
        attrs.get("ext") or Path(str(attrs.get("name", ""))).suffix.lower(),
    )
    size = int(attrs.get("size") or 0)
    try:
        if kind == "image":
            body = storage.get_object_bytes(bucket=flib.BUCKET, key=version.object_key)
            usvc.mark_derivative(version, "thumbnail", status="pending")
            storage.put_object(
                bucket=flib.BUCKET, key=usvc.deriv_key(version, "thumbnail"),
                body=_thumb_webp(body, max_px=usvc.THUMB_MAX_PX),
                content_type="image/webp",
            )
            _finish(usvc, version, "thumbnail")
            return {"version_id": version_id, "derived": "thumbnail"}
        if kind == "office":
            if size > usvc.OFFICE_TRANSCODE_MAX:
                usvc.mark_derivative(version, "preview", status="unsupported")
                return {"version_id": version_id, "skipped": "office_too_large"}
            _office_pdf(version)
            _finish(usvc, version, "preview")
            return {"version_id": version_id, "derived": "preview"}
        if kind == "video":
            _video_poster(version)
            _finish(usvc, version, "poster")
            return {"version_id": version_id, "derived": "poster"}
        usvc.touch_derivative(version)  # 其他类型：无衍生物，仅续期已有登记
        return {"version_id": version_id, "derived": None}
    except TranscodeToolMissing as exc:
        # 工具缺失：环境性失败——登记 failed（前端「转码失败 · 重试」+ 下载兜底）
        _mark_failed(usvc, version, kind, str(exc))
        return {"version_id": version_id, "failed": str(exc)}
    except SoftTimeLimitExceeded:
        _mark_failed(usvc, version, kind, "soft_time_limit")
        raise
    except Exception as exc:  # noqa: BLE001 —— 子进程抖动：两次退避重试后放弃
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=5 * (self.request.retries + 1), exc=exc) from exc
        _mark_failed(usvc, version, kind, str(exc))
        logger.warning("derive_preview.giveup version=%s err=%s", version_id, exc)
        return {"version_id": version_id, "failed": str(exc)}


def _finish(usvc, version, kind: str) -> None:
    """成功登记 + file.transcode.completed 事件（§4.4 表：含冷清理后重生成）。"""
    usvc.mark_derivative(version, kind, status="ready")
    from plane.bgtasks.event_publisher import publish_file_transcode_completed

    publish_file_transcode_completed(
        project_id=str(version.asset.project_id),
        asset_id=str(version.asset_id),
        derivative_kind=kind,
    )


def _mark_failed(usvc, version, kind: str, error: str) -> None:
    deriv_kind = {"image": "thumbnail", "office": "preview", "video": "poster"}.get(kind)
    if deriv_kind:
        usvc.mark_derivative(version, deriv_kind, status="failed", error=error)


def _office_pdf(version) -> None:
    """LibreOffice headless：下载源对象（流式落盘）→ soffice 转 PDF → 上传产物。"""
    import uuid as _uuid

    from plane.db.services import file_library as flib
    from plane.storage import minio as storage

    src_ext = Path(str((version.attributes or {}).get("name", ""))).suffix or ".docx"
    with tempfile.TemporaryDirectory(prefix=f"rp-deriv-{_uuid.uuid4().hex}") as tmp:
        src = Path(tmp) / f"src{src_ext}"
        _download_streaming(bucket=flib.BUCKET, key=version.object_key, dest=src)
        _run_tool(
            ["soffice", "--headless", "--norestore", "--convert-to", "pdf",
             "--outdir", tmp, str(src)],
            cwd=tmp,
        )
        out = Path(tmp) / f"{src.stem}.pdf"
        if not out.exists():
            raise RuntimeError("soffice 未产出 PDF")
        from plane.db.services import upload_session as usvc

        dest_key = usvc.deriv_key(version, "preview")
        storage.put_object(bucket=flib.BUCKET, key=dest_key, body=out.read_bytes(),
                           content_type="application/pdf")


def _video_poster(version) -> None:
    """ffmpeg 封面帧：-ss 1 抽 1 帧 jpg（流式读源，不落盘整个视频）。"""
    import uuid as _uuid

    from plane.db.services import file_library as flib
    from plane.db.services import upload_session as usvc
    from plane.storage import minio as storage

    with tempfile.TemporaryDirectory(prefix=f"rp-poster-{_uuid.uuid4().hex}") as tmp:
        src = Path(tmp) / "src.video"
        _download_streaming(bucket=flib.BUCKET, key=version.object_key, dest=src)
        poster = Path(tmp) / "poster.jpg"
        _run_tool(["ffmpeg", "-y", "-ss", "1", "-i", str(src),
                   "-frames:v", "1", "-q:v", "2", str(poster)])
        if not poster.exists():
            raise RuntimeError("ffmpeg 未产出封面帧")
        storage.put_object(bucket=flib.BUCKET, key=usvc.deriv_key(version, "poster"),
                           body=poster.read_bytes(), content_type="image/jpeg")


def _download_streaming(*, bucket: str, key: str, dest: Path, chunk: int = 8 * 1024 * 1024) -> None:
    """流式下载对象到文件（8MB 分块，不求全读内存——100MB Office 输入量程）。"""
    from plane.storage import minio as storage

    client = storage._client()  # noqa: SLF001 —— 同包内部复用客户端构造
    try:
        resp = client.get_object(Bucket=bucket, Key=key)
        with resp["Body"] as body, open(dest, "wb") as fh:
            while True:
                block = body.read(chunk)
                if not block:
                    break
                fh.write(block)
    except storage.StorageObjectNotFound:
        raise
    except Exception as exc:  # noqa: BLE001 —— boto 检查面太宽，统一归一为不可用
        raise storage.StorageUnavailable(str(exc)) from exc


@shared_task(name="plane.bgtasks.derive_preview.sweep_cold_derivatives")
def sweep_cold_derivatives(*, restrict_workspace_id=None) -> dict:
    """BR-11：30 天未访问的衍生物删除；下次预览按需重生成（冷热分离，§6.3 决策 3）。

    登记态清理与对象删除同批：对象删除失败（StorageUnavailable）保留登记下轮重试。
    """
    from datetime import datetime

    from plane.db.models import FileVersion
    from plane.db.services import file_library as flib
    from plane.storage import minio as storage

    threshold = timezone.now() - timedelta(days=30)  # BR-11：30 天未访问
    qs = FileVersion.objects.filter(
        deleted_at__isnull=True, attributes__derivatives__isnull=False,
    )
    if restrict_workspace_id is not None:
        qs = qs.filter(asset__workspace_id=restrict_workspace_id)
    swept = objects_failed = 0
    for version in qs.iterator(chunk_size=200):
        derivs = dict((version.attributes or {}).get("derivatives") or {})
        changed = False
        for kind, entry in list(derivs.items()):
            last = entry.get("last_access_at") or entry.get("generated_at")
            if not last or entry.get("status") != "ready":
                continue
            try:
                last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            except ValueError:
                continue
            if last_dt >= threshold:
                continue
            try:
                storage.remove_object(bucket=flib.BUCKET, key=entry["key"])
            except storage.StorageUnavailable:
                objects_failed += 1
                continue
            derivs.pop(kind)
            swept += 1
            changed = True
        if changed:
            attrs = dict(version.attributes or {})
            attrs["derivatives"] = derivs
            version.attributes = attrs
            version.save(update_fields=["attributes", "updated_at"])
    return {"swept": swept, "objects_failed": objects_failed}


@shared_task(name="plane.bgtasks.derive_preview.expire_upload_sessions")
def expire_upload_sessions(*, restrict_workspace_id=None) -> dict:
    """BR-05：24h 未完成会话——AbortMultipartUpload + expired + 释放配额预留。

    对象存储不可达的会话跳过本轮（下轮重试）；会话失效后新名分片的 uploading
    资产行自动回归 FILE-001 30min 孤儿扫描（IT-09 闭环）。
    """
    from plane.db.models import UploadSession
    from plane.db.services import file_library as flib
    from plane.db.services.upload_session import SESSION_TTL
    from plane.storage import minio as storage

    threshold = timezone.now() - SESSION_TTL
    qs = UploadSession.objects.filter(
        status=UploadSession.Status.UPLOADING,
        deleted_at__isnull=True,
        created_at__lt=threshold,
    )
    if restrict_workspace_id is not None:
        qs = qs.filter(project__workspace_id=restrict_workspace_id)
    expired = failed = 0
    for session in qs.iterator(chunk_size=200):
        try:
            storage.abort_multipart_upload(
                bucket=flib.BUCKET, key=session.object_key,
                upload_id=session.s3_upload_id,
            )
        except storage.StorageUnavailable as exc:
            failed += 1
            logger.warning("expire_session_abort_failed session=%s err=%s", session.id, exc)
            continue
        session.status = UploadSession.Status.EXPIRED
        session.save(update_fields=["status", "updated_at"])
        expired += 1
    return {"expired": expired, "failed": failed}


@shared_task(name="plane.bgtasks.derive_preview.evict_old_versions")
def evict_old_versions(asset_id: str) -> int:
    """BR-08 薄壳：逻辑在 services.upload_session（on_commit 钩子 / 单测共用）。"""
    from plane.db.services.upload_session import evict_old_versions as _evict

    return _evict(asset_id)
