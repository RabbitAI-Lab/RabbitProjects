"""附件序列化器（FILE-001 §4.3）。

CLAUDE.md 教训 #3：``SerializerMethodField`` 必须列入 ``Meta.fields``。
"""
from __future__ import annotations

from rest_framework import serializers

from plane.db.models import FileAsset


# ──────────────────────────────────────────────────────────────────────
# 请求
# ──────────────────────────────────────────────────────────────────────
class PresignSerializer(serializers.Serializer):
    """POST …/attachments/presign/ —— 仅做形状校验。

    扩展名 / 大小 / MIME 的业务校验统一放 AssetService.presign（FILE-001 §4.4），
    让 ``VALIDATION_FILE_TYPE_NOT_ALLOWED`` / ``VALIDATION_FILE_SIZE_EXCEEDED`` 等
    注册错误码生效（DRF ValidationError 仅返回 ``VALIDATION_ERROR``，无法映射到
    业务码 —— CLAUDE.md 教训 #5 边界）。
    """

    file_name = serializers.CharField(max_length=255)
    # 不设 max_value —— 让 AssetService.presign 抛 ``VALIDATION_FILE_SIZE_EXCEEDED``
    file_size = serializers.IntegerField(min_value=1)
    content_type = serializers.CharField(max_length=64)


class CompleteSerializer(serializers.Serializer):
    """POST …/attachments/{asset_id}/complete/ —— etag 与 size 兼容保留。"""

    etag = serializers.CharField(required=False, allow_blank=True, max_length=128)
    size = serializers.IntegerField(required=False, min_value=1)


# ──────────────────────────────────────────────────────────────────────
# 响应
# ──────────────────────────────────────────────────────────────────────
class AttachmentRowSerializer(serializers.ModelSerializer):
    """单行附件（GET list / POST complete 响应）。

    ``name`` / ``mime`` 从 attributes 派生（S3 对象键不可还原原名）。
    """

    name = serializers.SerializerMethodField()
    mime = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = FileAsset
        fields = (
            "id",
            "name",
            "size",
            "mime",
            "uploaded_by_id",
            "status",
            "created_at",
            "download_url",
        )
        read_only_fields = fields

    def get_name(self, obj: FileAsset) -> str:
        return (obj.attributes or {}).get("name", "")

    def get_mime(self, obj: FileAsset) -> str:
        return (obj.attributes or {}).get("mime", "")

    def get_download_url(self, obj: FileAsset) -> str:
        # 仅返回「换发端点」路径 —— 实际下载由浏览器跟随 302。
        # 路由挂在 `app` 命名空间下（plane/urls.py 的 include(namespace="app")），
        # 裸名 reverse 必失败；早前的 `except Exception: return ""` 把这个错误吞成了
        # 静默的空串，是「下载按钮点了跳 /undefined」的源头。
        from django.urls import NoReverseMatch, reverse

        try:
            return reverse(
                "app:attachments-download",
                kwargs={
                    "slug": self.context.get("slug"),
                    "project_id": self.context.get("project_id"),
                    "issue_id": self.context.get("issue_id"),
                    "asset_id": obj.id,
                },
            )
        except NoReverseMatch:
            return ""
