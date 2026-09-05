"""IssueComment —— 任务评论，参见 `docs/sprint-1-mvp/COLLAB-001-comment-notify.md` §4.1.1。

扁平单层（P1）；P2 COLLAB-002 启用 parent 楼中楼与 accessory 表情/图片。
列一次建齐（unified-issue-model.md §2.1 ER 基线 + P0 全列原则），P2 零 DDL。
"""
from django.db import models
from django.utils.html import strip_tags

from plane.db.models.base import BaseModel


class IssueComment(BaseModel):
    """任务评论 —— 对标 Plane IssueComment。"""

    # P2+ 来源预留（P1 恒为 in_app，Service 层不外暴露）
    # 不在本迭代体现为字段——COLLAB-001 §4.1.1 当前不含 source 列；如需引入走后续 ADR。

    issue = models.ForeignKey(
        "db.Issue",
        on_delete=models.CASCADE,
        related_name="comments",
        verbose_name="所属任务",
    )
    actor = models.ForeignKey(
        "db.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="issue_comments",
        verbose_name="评论人",
    )
    # ── 楼中楼预留（P2 COLLAB-002 启用；P1 强制 NULL，Serializer 拒绝非空）──
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="replies",
        verbose_name="父评论",
        help_text="P1 恒为 NULL；P2 楼中楼两级结构",
    )

    # ── 内容三列（与 Issue 描述三列同纪律：json/html 成对提交，stripped 服务端派生）──
    comment_json = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="评论-ProseMirror JSON",
        help_text="Tiptap 编辑器原生格式，编辑回显用",
    )
    comment_html = models.TextField(verbose_name="评论 HTML（净化后）")
    comment_stripped = models.TextField(
        verbose_name="纯文本",
        help_text="长度校验 / 搜索 / 通知摘要",
    )

    accessory = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="扩展数据",
        help_text='P2: {"reactions":[{"emoji":"👍","user_ids":[…]}],"images":[asset_id,…]}',
    )

    is_edited = models.BooleanField(default=False, verbose_name="是否编辑过")

    class Meta(BaseModel.Meta):
        db_table = "issue_comments"
        verbose_name = "任务评论"
        ordering = ("created_at",)  # 会话流正序（COLLAB-001 §2.5 BR-13）
        indexes = [
            # 评论列表取数：WHERE issue_id=? AND deleted_at IS NULL ORDER BY created_at
            models.Index(fields=["issue", "created_at"], name="idx_comment_issue_time"),
        ]

    def save(self, *args, **kwargs):
        if self.comment_html:
            self.comment_stripped = strip_tags(self.comment_html)
        super().save(*args, **kwargs)
