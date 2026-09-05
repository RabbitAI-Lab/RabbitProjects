"""评论序列化器（COLLAB-001 §4.3 + COLLAB-002 §4.2.2 两层结构）。

CLAUDE.md 教训 #3：SerializerMethodField 必须列入 Meta.fields。

两层装配约定（COLLAB-002 §4.2.2 契约要点）：
  - 顶层项含 ``replies[]``（全量 ≤100 正序内联）与 ``reply_count``（= replies.length）；
  - ``reactions`` 默认聚合（emoji/count/reacted_by_me），``?expand=reactions``
    追加 ``user_ids``——数据由视图层按页面评论集一次聚合后经 context 注入
    （``reactions_map``），避免逐评论 N+1；
  - ``reply_to`` 归并语境（被回复人）存于 accessory，序列化经 context
    ``reply_to_map`` 注入（POST 单条路径直接传 map）。
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
    """GET 两层列表 / PATCH / POST 响应 —— 含 actor + mention_ids + 线程与聚合。"""

    id = serializers.UUIDField(read_only=True)
    parent_id = serializers.SerializerMethodField()
    root_id = serializers.SerializerMethodField()
    actor = serializers.SerializerMethodField()
    comment_html = serializers.SerializerMethodField()
    mention_ids = serializers.SerializerMethodField()
    is_deleted = serializers.SerializerMethodField()
    reply_to_actor = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()
    reactions = serializers.SerializerMethodField()
    replies = serializers.SerializerMethodField()
    reply_count = serializers.SerializerMethodField()

    class Meta:
        model = IssueComment
        fields = (
            "id",
            "parent_id",
            "root_id",
            "actor",
            "comment_html",
            "comment_stripped",
            "mention_ids",
            "reply_to_actor",
            "images",
            "reactions",
            "replies",
            "reply_count",
            "is_edited",
            "is_deleted",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def _ctx_map(self, key: str) -> dict:
        return self.context.get(key) or {}

    def get_parent_id(self, obj):
        return str(obj.parent_id) if obj.parent_id else None

    def get_root_id(self, obj):
        """归并后的顶层 ID（与 parent_id 恒等——两级封顶的实现投影）。"""
        return str(obj.parent_id) if obj.parent_id else None

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

        if obj.deleted_at:                 # 软删占位：正文衍生数据不再可见
            return []
        return sorted(extract_mention_ids(obj.comment_html))

    def get_comment_html(self, obj):
        if obj.deleted_at:                 # 软删占位行：is_deleted=true 驱动前端占位
            return ""
        return obj.comment_html

    def get_is_deleted(self, obj):
        return obj.deleted_at is not None

    def get_reply_to_actor(self, obj):
        return self._ctx_map("reply_to_map").get(str(obj.id))

    def get_images(self, obj):
        if obj.deleted_at:                 # 软删占位：图片引用不再可见
            return []
        return (obj.accessory or {}).get("images", [])

    def get_reactions(self, obj):
        return self._ctx_map("reactions_map").get(str(obj.id), [])

    def get_replies(self, obj):
        from plane.app.serializers.comment import CommentSerializer

        rows = self._ctx_map("replies_map").get(str(obj.id))
        if not rows:
            return []
        return CommentSerializer(rows, many=True, context=self.context).data

    def get_reply_count(self, obj):
        return len(self._ctx_map("replies_map").get(str(obj.id), []))


class CommentWriteSerializer(serializers.Serializer):
    """POST / PATCH 入参 —— 不在 Meta 内（裸 Serializer）；comment_html 必填。

    COLLAB-002 BR-14：parent_id 起合法（P1 强制 NULL 锁解除）；归并与上限
    校验在 CommentService（跨层需要 issue 域）。accessory 由服务端聚合，
    客户端直传值被忽略（UT-18）。
    """

    comment_html = serializers.CharField(allow_blank=False, max_length=100_000)
    comment_json = serializers.DictField(required=False, default=dict)
    accessory = serializers.DictField(required=False, default=dict)
    parent_id = serializers.UUIDField(required=False, allow_null=True)
