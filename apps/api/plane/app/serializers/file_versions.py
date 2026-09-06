"""分片会话 / 版本序列化器（FILE-003 §4.2）。

业务校验（错误码可映射）统一放 service 层，Serializer 只做形状校验
（FILE-002 file_library 同款边界；CLAUDE.md 教训 #3 不适用——无 MethodField）。
"""
from __future__ import annotations

from rest_framework import serializers


class SessionInitSerializer(serializers.Serializer):
    """POST …/upload-sessions/ —— 形状校验；5GB 上限/白名单/配额在 service。"""

    file_name = serializers.CharField(max_length=255, min_length=1)
    file_size = serializers.IntegerField(min_value=1)
    content_type = serializers.CharField(max_length=128, required=False,
                                         allow_blank=True, default="")
    folder_id = serializers.UUIDField()
    content_md5 = serializers.RegexField(
        r"^[0-9a-fA-F]{32}$", required=False, allow_null=True,
    )


class ChunkRegisterSerializer(serializers.Serializer):
    """PATCH …/chunks/{n}/ —— 登记片完成：etag 必填（MinIO UploadPart 返回值，
    带不带引号均可）；md5 选填（前端 Content-MD5 十六进制，BR-03 服务端核对）。"""

    etag = serializers.CharField(max_length=64, min_length=1)
    md5 = serializers.RegexField(r"^[0-9a-fA-F]{32}$", required=False, allow_null=True)
