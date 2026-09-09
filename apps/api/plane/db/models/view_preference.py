"""用户视图偏好（BOARD-005 §4.1.2，Sprint-8 R5）。

三功能唯一载体：①手动订阅（pin）②项目默认自动订阅（批量写入）
③订阅列表个人级排序（BR-11）。软删级联：视图删除/收回共享 → 软删
该视图全部偏好行；成员移出项目 → 软删其该项目偏好行（BR-16）。
"""
from django.db import models

from plane.db.models.base import BaseModel


class UserViewPreference(BaseModel):
    user = models.ForeignKey("db.User", on_delete=models.CASCADE,
                             related_name="view_preferences", verbose_name="用户")
    project = models.ForeignKey("db.Project", on_delete=models.CASCADE,
                                related_name="view_preferences", verbose_name="项目")
    view = models.ForeignKey("IssueView", on_delete=models.CASCADE,
                             related_name="preferences", verbose_name="视图")
    pinned = models.BooleanField(default=True, verbose_name="订阅（侧栏展示）")
    sort_order = models.FloatField(default=65535.0, verbose_name="个人订阅排序（BR-11）")

    class Meta(BaseModel.Meta):
        db_table = "user_view_preferences"
        verbose_name = "用户视图偏好"
        verbose_name_plural = "用户视图偏好"
        constraints = [
            # 存活行内「一人一视图至多一条」——重订阅 = 复活或重建（UPSERT 幂等基础）
            models.UniqueConstraint(
                fields=["user", "view"],
                condition=models.Q(deleted_at__isnull=True),
                name="uq_view_pref_user_view_alive"),
        ]
        indexes = [
            models.Index(fields=["user", "project", "pinned"],
                         name="idx_view_pref_user_proj"),
            models.Index(fields=["view", "pinned"], name="idx_view_pref_view"),
        ]

    def __str__(self) -> str:
        return f"pref(user={self.user_id}, view={self.view_id})"
