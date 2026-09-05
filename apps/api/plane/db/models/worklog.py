"""工时填报模型（TASK-006 §4.1.2）——一人一天一任务可多笔（BR-06）。"""
from __future__ import annotations

from django.db import models

from plane.db.models.base import BaseModel


class WorkLog(BaseModel):
    """一条记录 = 一人一天在一个任务上的一笔投入。

    同一 (issue, actor, worked_on) 可多条（上午/下午分笔真实存在）。
    actor 由服务端注入（BR-03），created_by 即填报人（BaseModel 审计同源）。
    """

    issue = models.ForeignKey(
        "db.Issue", on_delete=models.CASCADE, related_name="work_logs", verbose_name="所属工作项"
    )
    actor = models.ForeignKey(
        "db.User", on_delete=models.CASCADE, related_name="work_logs", verbose_name="填报人"
    )
    worked_on = models.DateField(db_index=True, verbose_name="工作日期")
    minutes = models.PositiveIntegerField(
        verbose_name="时长（分钟）", help_text="1~1440，整数分钟")
    note = models.CharField(max_length=2000, blank=True, verbose_name="备注")

    class Meta(BaseModel.Meta):
        db_table = "work_logs"
        verbose_name = "工时记录"
        verbose_name_plural = "工时记录"
        ordering = ("-worked_on", "-created_at")  # type: ignore[assignment,misc]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(minutes__gte=1) & models.Q(minutes__lte=1440),  # type: ignore[call-arg]
                name="chk_worklog_minutes_range",
            ),
        ]
        indexes = [
            models.Index(fields=["issue"], name="idx_worklog_issue"),
            models.Index(fields=["actor", "worked_on"], name="idx_worklog_actor_day"),
        ]

    def __str__(self) -> str:
        return f"{self.issue_id} {self.actor_id} {self.worked_on} {self.minutes}m"
