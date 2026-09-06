"""MinIO 预签名直传封装 —— boto3 S3v4 客户端（INFRA-002 §4.4）。

**降级语义**：开发环境 MinIO 未启动时，`presign()` / `head_object()` /
`remove_object()` 抛 ``StorageUnavailable`` —— View 层捕获并映射到
``SERVER_STORAGE_ERROR``（架构 §8.6 登记 HTTP 500，与 FILE-001 §2.6 同款）。
presign 链路一致：缺少存储后端时所有上传路径同样 500，不假装成功。
"""
from __future__ import annotations

import logging
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError

logger = logging.getLogger("plane.storage")

DEFAULT_PRESIGN_EXPIRES = 1800   # 30 分钟，与 FILE-001 §2.4 BR-07 / 架构 §13.2 对齐


class StorageUnavailable(Exception):
    """MinIO 不可达 / 凭证缺失 —— 映射 SERVER_STORAGE_ERROR。"""


class StorageObjectNotFound(Exception):
    """HEAD / GET 取不到对象 —— 映射 VALIDATION_FILE_UPLOAD_MISMATCH。"""


def _client() -> Any:
    """构造 S3 客户端；缺凭证抛 StorageUnavailable（开发环境常见）。"""
    from django.conf import settings

    endpoint = getattr(settings, "AWS_S3_ENDPOINT_URL", "")
    access_key = getattr(settings, "AWS_ACCESS_KEY_ID", "")
    secret_key = getattr(settings, "AWS_SECRET_ACCESS_KEY", "")
    if not endpoint or not access_key or not secret_key:
        raise StorageUnavailable("对象存储未配置（AWS_S3_* 环境变量缺失）")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
    )


def presigned_put_url(
    *,
    bucket: str,
    key: str,
    content_type: str,
    expires: int = DEFAULT_PRESIGN_EXPIRES,
    content_length_range: tuple[int, int] | None = None,
) -> str:
    """签发 PUT 预签名 URL。

    ``content_length_range`` 在 Policy 条件里限制对象字节数（FILE-001 BR-01
    / AUTH-004 BR-03 双保险：即使绕过前端校验直传超大文件，MinIO 也拒绝）。
    返回的 URL 形如 ``https://host/<key>?X-Amz-…``，浏览器零跨域预检需经
    Nginx ``/uploads/`` 反代（同源，FILE-001 §4.7 / AUTH-004 §4.2.2 对齐说明）。
    """
    client = _client()
    params: dict[str, Any] = {
        "Bucket": bucket,
        "Key": key,
        "ContentType": content_type,
    }
    conditions: list[Any] = [
        {"Content-Type": content_type},
    ]
    if content_length_range is not None:
        lo, hi = content_length_range
        conditions.append(["content-length-range", lo, hi])
    try:
        return client.generate_presigned_url(
            "put_object",
            Params=params,
            ExpiresIn=expires,
            HttpMethod="PUT",
        )
    except (BotoCoreError, ClientError, EndpointConnectionError) as exc:
        logger.warning("storage_presign_failed bucket=%s key=%s err=%s", bucket, key, exc)
        raise StorageUnavailable(str(exc)) from exc


def head_object_size(*, bucket: str, key: str) -> int:
    """HEAD 对象取真实大小；不存在抛 StorageObjectNotFound。"""
    client = _client()
    try:
        resp = client.head_object(Bucket=bucket, Key=key)
        return int(resp.get("ContentLength", 0))
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            raise StorageObjectNotFound(key) from exc
        logger.warning("storage_head_failed bucket=%s key=%s err=%s", bucket, key, exc)
        raise StorageUnavailable(str(exc)) from exc
    except (BotoCoreError, EndpointConnectionError) as exc:
        raise StorageUnavailable(str(exc)) from exc


def remove_object(*, bucket: str, key: str) -> None:
    """物理删除对象；不存在静默成功（幂等，cleanup 任务复用）。"""
    client = _client()
    try:
        client.delete_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return
        raise StorageUnavailable(str(exc)) from exc
    except (BotoCoreError, EndpointConnectionError) as exc:
        raise StorageUnavailable(str(exc)) from exc


def get_object_bytes(*, bucket: str, key: str) -> bytes:
    """GET 对象全量字节（缩略图派生读原图用，COLLAB-002 §1.6）。

    不存在抛 ``StorageObjectNotFound``。
    """
    client = _client()
    try:
        resp = client.get_object(Bucket=bucket, Key=key)
        with resp["Body"] as body:
            return body.read()
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            raise StorageObjectNotFound(key) from exc
        logger.warning("storage_get_failed bucket=%s key=%s err=%s", bucket, key, exc)
        raise StorageUnavailable(str(exc)) from exc
    except (BotoCoreError, EndpointConnectionError) as exc:
        raise StorageUnavailable(str(exc)) from exc


def put_object(*, bucket: str, key: str, body: bytes, content_type: str) -> None:
    """PUT 对象字节（缩略图产物缓存写回 MinIO，COLLAB-002 §1.6）。"""
    client = _client()
    try:
        client.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)
    except (BotoCoreError, ClientError, EndpointConnectionError) as exc:
        logger.warning("storage_put_failed bucket=%s key=%s err=%s", bucket, key, exc)
        raise StorageUnavailable(str(exc)) from exc


def presigned_get_url(
    *,
    bucket: str,
    key: str,
    expires: int = 300,
    response_headers: dict | None = None,
) -> str:
    """签发 GET 预签名 URL —— FILE-001 §2.3 / AUTH-004 §4.3 共享。

    ``response_headers`` 内嵌 ``response-content-disposition`` 实现 RFC 5987 文件名编码。
    """
    client = _client()
    params: dict[str, Any] = {"Bucket": bucket, "Key": key}
    if response_headers:
        params["ResponseContentDisposition"] = response_headers.get(
            "response-content-disposition", ""
        )
    try:
        return client.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=expires,
            HttpMethod="GET",
        )
    except (BotoCoreError, ClientError, EndpointConnectionError) as exc:
        logger.warning(
            "storage_presign_get_failed bucket=%s key=%s err=%s", bucket, key, exc,
        )
        raise StorageUnavailable(str(exc)) from exc


# ── S3 Multipart（FILE-003 §4.3 分片会话；真相在 S3，表只记索引）────────


def create_multipart_upload(*, bucket: str, key: str, content_type: str) -> str:
    """发起 multipart 上传 → 返回 upload_id（init 端点，§4.2 #1）。"""
    client = _client()
    try:
        resp = client.create_multipart_upload(
            Bucket=bucket, Key=key,
            ContentType=content_type or "application/octet-stream",
        )
    except (BotoCoreError, ClientError, EndpointConnectionError) as exc:
        logger.warning("storage_create_mpu_failed bucket=%s key=%s err=%s", bucket, key, exc)
        raise StorageUnavailable(str(exc)) from exc
    return str(resp["UploadId"])


def presigned_upload_part_url(
    *, bucket: str, key: str, upload_id: str, part_number: int, expires: int = 1800,
) -> str:
    """签发 UploadPart 预签名 URL（§4.2 #3——浏览器直传单片，含 Content-MD5 头由前端加）。"""
    client = _client()
    try:
        return client.generate_presigned_url(
            "upload_part",
            Params={"Bucket": bucket, "Key": key,
                    "UploadId": upload_id, "PartNumber": part_number},
            ExpiresIn=expires,
            HttpMethod="PUT",
        )
    except (BotoCoreError, ClientError, EndpointConnectionError) as exc:
        logger.warning(
            "storage_presign_part_failed bucket=%s key=%s part=%s err=%s",
            bucket, key, part_number, exc,
        )
        raise StorageUnavailable(str(exc)) from exc


def list_parts(*, bucket: str, key: str, upload_id: str) -> list[dict[str, Any]]:
    """列已传片（BR-04 complete 核对源）→ ``[{PartNumber, ETag, Size}]``（ETag 去引号）。"""
    client = _client()
    parts: list[dict[str, Any]] = []
    try:
        paginator = client.get_paginator("list_parts")
        for page in paginator.paginate(Bucket=bucket, Key=key, UploadId=upload_id):
            for p in page.get("Parts", []):
                parts.append({
                    "PartNumber": int(p["PartNumber"]),
                    "ETag": str(p["ETag"]).strip('"'),
                    "Size": int(p.get("Size", 0)),
                })
        return parts
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchUpload",):
            raise StorageObjectNotFound(f"{key}?uploadId={upload_id}") from exc
        logger.warning("storage_list_parts_failed bucket=%s key=%s err=%s", bucket, key, exc)
        raise StorageUnavailable(str(exc)) from exc
    except (BotoCoreError, EndpointConnectionError) as exc:
        raise StorageUnavailable(str(exc)) from exc


def complete_multipart_upload(
    *, bucket: str, key: str, upload_id: str, parts: list[dict[str, Any]],
) -> None:
    """合并片（parts 按片号升序：``[{PartNumber, ETag}]``，ETag 带不带引号均可）。"""
    client = _client()
    payload = [{"PartNumber": int(p["PartNumber"]), "ETag": p["ETag"]} for p in parts]
    try:
        client.complete_multipart_upload(
            Bucket=bucket, Key=key, UploadId=upload_id,
            MultipartUpload={"Parts": payload},
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchUpload", "InvalidPart", "InvalidPartOrder"):
            raise StorageObjectNotFound(f"{key}?uploadId={upload_id}") from exc
        logger.warning("storage_complete_mpu_failed bucket=%s key=%s err=%s", bucket, key, exc)
        raise StorageUnavailable(str(exc)) from exc
    except (BotoCoreError, EndpointConnectionError) as exc:
        raise StorageUnavailable(str(exc)) from exc


def abort_multipart_upload(*, bucket: str, key: str, upload_id: str) -> None:
    """取消 multipart（BR-05 24h TTL / 用户 abort）——NoSuchUpload 幂等成功。"""
    client = _client()
    try:
        client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchUpload", "404", "NotFound"):
            return
        logger.warning("storage_abort_mpu_failed bucket=%s key=%s err=%s", bucket, key, exc)
        raise StorageUnavailable(str(exc)) from exc
    except (BotoCoreError, EndpointConnectionError) as exc:
        raise StorageUnavailable(str(exc)) from exc
