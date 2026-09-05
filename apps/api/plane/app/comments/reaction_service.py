"""评论表情 toggle 服务（COLLAB-002 §4.3.2）。

- emoji 白名单 24 枚（§3.2 常驻 8 + 展开 16，白名单外 → NOT_A_CHOICE）
- 幂等基座：``(comment, actor, emoji)`` 活跃行唯一约束 + ``get_or_create``
  （软删行复活语义：重按复活软删行或新建——与 IssueAssignee 同口径）
- 不产生通知、不写 IssueActivity 留痕（BR-09 降噪）
- 「换表情」由前端串联 toggle_off + toggle_on（两次幂等调用，无原子 PUT）
"""
from __future__ import annotations

from django.db.models import Count, Q
from django.utils import timezone

from plane.base.exception import AppException
from plane.db.models import CommentReaction, IssueComment

#: 白名单 24 枚（COLLAB-002 §3.2 / §4.3.1，规格原文取列表）
EMOJI_WHITELIST: frozenset[str] = frozenset(
    "👍 👎 ❤️ 😂 🎉 🚀 👀 ✅ 😕 😡 🤔 👏 🔥 💯 😢 🙏 ⛔ ⏰ 🍀 📌 🔁 ❓ 💤 🎯".split()
)


class ReactionService:
    """表情 toggle + 聚合 —— 独立表单行 INSERT/UPDATE，天然并发安全（§1.4）。"""

    def _require_whitelisted(self, emoji: str) -> None:
        if emoji not in EMOJI_WHITELIST:                            # BR-09
            raise AppException(
                "VALIDATION_ERROR",
                message="请求参数校验失败",
                details=[{"field": "emoji", "code": "NOT_A_CHOICE",
                          "message": "不支持的表情，请从选择器中选择"}],
            )

    def toggle_on(self, *, comment: IssueComment, actor, emoji: str) -> dict:
        """幂等添加（重复 POST 同值 → changed=false，count 不变）。"""
        self._require_whitelisted(emoji)
        row = (CommentReaction.objects
               .filter(comment=comment, actor=actor, emoji=emoji).first())
        changed = row is None
        if row is None:                                             # 复活或新建（BR-10）
            CommentReaction.objects.get_or_create(                   # 唯一约束兜底并发
                comment=comment, actor=actor, emoji=emoji,
                defaults={"created_by": actor, "updated_by": actor},
            )
        return self._aggregate(comment, actor, emoji, changed=changed)

    def toggle_off(self, *, comment: IssueComment, actor, emoji: str) -> dict:
        """幂等撤销（撤销未点过的 emoji → changed=false）。"""
        self._require_whitelisted(emoji)
        updated = (CommentReaction.objects
                   .filter(comment=comment, actor=actor, emoji=emoji)
                   .update(deleted_at=timezone.now(), updated_by=actor))
        return self._aggregate(comment, actor, emoji, changed=bool(updated))

    def _aggregate(self, comment: IssueComment, actor, emoji: str, *,
                   changed: bool) -> dict:
        row = (comment.reactions.filter(emoji=emoji)
               .aggregate(n=Count("id"),
                          mine=Count("id", filter=Q(actor=actor))))
        return {"emoji": emoji, "count": row["n"],
                "reacted_by_me": row["mine"] > 0, "changed": changed}
