"""评论序列化器（COLLAB-001 §4.3）。

CLAUDE.md 教训 #3：SerializerMethodField 必须列入 Meta.fields。
"""
from __future__ import annotations

from rest_framework import serializers

from plane.db.models import IssueComment


class CommentActorSerializer(serializers.Serializer):
    """评论人最小闭包 —— 前端渲染头像与展示名。"""

    id = serializers.UUIDField()
    display_name = serializers.CharField()
    avatar_url = serializers.URLField(allow_null=True, required=False)


class CommentSerializer(serializers.ModelSerializer):
    """GET 列表 / PATCH / POST 响应 —— 含 actor + mention_ids + is_deleted。"""

    id = serializers.UUIDField(read_only=True)
    actor = serializers.SerializerMethodField()
    mention_ids = serializers.SerializerMethodField()
    is_deleted = serializers.SerializerMethodField()

    class Meta:
        model = IssueComment
        fields = (
            "id",
            "actor",
            "comment_html",
            "comment_stripped",
            "mention_ids",
            "is_edited",
            "is_deleted",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "comment_stripped", "is_edited", "is_deleted",
                              "created_at", "updated_at")

    def get_actor(self, obj):
        if not obj.actor_id:
            return {"id": None, "display_name": "已注销用户", "avatar_url": None}
        return {
            "id": str(obj.actor_id),
            "display_name": obj.actor.display_name,
            "avatar_url": obj.actor.avatar_url or None,
        }

    def get_mention_ids(self, obj):
        from plane.app.comments.sanitize import extract_mention_ids

        return sorted(extract_mention_ids(obj.comment_html))

    def get_is_deleted(self, obj):
        return obj.deleted_at is not None


class CommentWriteSerializer(serializers.Serializer):
    """POST / PATCH 入参 —— 不在 Meta 内（裸 Serializer）；comment_html 必填。"""

    comment_html = serializers.CharField(allow_blank=False, max_length=100_000)
    comment_json = serializers.DictField(required=False, default=dict)
    accessory = serializers.DictField(required=False, default=dict)
    # P1 强制 NULL（COLLAB-001 §2.5 BR-14）；落库前由 Service 拒绝非空
    parent_id = serializers.UUIDField(required=False, allow_null=True)
