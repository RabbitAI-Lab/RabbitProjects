"""项目文件库序列化器（FILE-002 §4.2）。

CLAUDE.md 教训 #3：``SerializerMethodField`` 必须列入 ``Meta.fields``。
业务校验（错误码可映射）统一放 service 层，Serializer 只做形状校验
（FILE-001 PresignSerializer 同款边界）。
"""
from __future__ import annotations

from rest_framework import serializers

from plane.db.models import FileFolder


class FolderCreateSerializer(serializers.Serializer):
    """POST …/folders/ —— name 1~64（BR-01）；同名同层拒绝在 service 层（409）。"""

    name = serializers.CharField(max_length=64, min_length=1, trim_whitespace=True)
    parent_id = serializers.UUIDField(required=False, allow_null=True)


class FolderUpdateSerializer(serializers.Serializer):
    """PATCH …/folders/{id}/ —— 改名/移动（folder.manage）/ 可见性（file.permission.manage）。"""

    name = serializers.CharField(max_length=64, min_length=1, trim_whitespace=True,
                                 required=False)
    parent_id = serializers.UUIDField(required=False, allow_null=True)
    visibility = serializers.ChoiceField(choices=FileFolder.Visibility.choices, required=False)
    allowed_members = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=True,
    )


class FilePresignSerializer(serializers.Serializer):
    """POST …/folders/{id}/files/presign/ —— 形状校验；50MB/白名单/配额在 service。"""

    file_name = serializers.CharField(max_length=255)
    file_size = serializers.IntegerField(min_value=1)
    content_type = serializers.CharField(max_length=64, required=False, allow_blank=True)


class FileUpdateSerializer(serializers.Serializer):
    """PATCH …/files/{asset_id}/ —— 重命名（BR-02 1~255）/ 移动 / 双挂 / 可见性。"""

    name = serializers.CharField(max_length=255, min_length=1, trim_whitespace=True,
                                 required=False)
    folder_id = serializers.UUIDField(required=False, allow_null=True)
    issue_id = serializers.UUIDField(required=False, allow_null=True)
    visibility = serializers.ChoiceField(choices=FileFolder.Visibility.choices, required=False)
    allowed_members = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=True,
    )
