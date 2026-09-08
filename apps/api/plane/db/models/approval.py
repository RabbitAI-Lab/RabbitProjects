"""审批流模型（WF-002 §4.2/§4.3）——审批定义（Flow/Node）与执行（Instance/Record）两级。

定义与执行解耦：实例持立案时刻 flow_snapshot（BR-11 定义冻结），定义后续修改
不影响在途实例。ApprovalRecord 只增不可变语义由表结构（无 updated_at）+
WF-006 §4.2 触发器（approval_records_guard）双层承载。
"""
from django.conf import settings
from django.db import models

from plane.db.models.base import BaseModel


class ApprovalFlow(BaseModel):
    """审批流定义（项目级）——被流转边引用（SET_NULL 摘挂，WF-001 §4.2）；
    治理口径先停用再删除（BR-13：停用后新发起 409，存量实例凭快照走完）。"""

    project = models.ForeignKey(
        "db.Project", on_delete=models.CASCADE, related_name="approval_flows", verbose_name="所属项目"
    )
    name = models.CharField(max_length=64, verbose_name="审批流名称")
    description = models.CharField(max_length=255, blank=True, default="", verbose_name="说明")
    is_active = models.BooleanField(default=True, verbose_name="启用中")
    forbid_self_approve = models.BooleanField(default=True, verbose_name="禁止自审（BR-12）")

    class Meta(BaseModel.Meta):
        db_table = "approval_flows"
        verbose_name = "审批流定义"
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name"], condition=models.Q(deleted_at__isnull=True),
                name="uniq_approval_flow_name",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.project_id}:{self.name}"


class ApprovalNode(BaseModel):
    """审批节点：level 顺序推进（1..10）；pass_mode 决定或签/会签。"""

    class PassMode(models.TextChoices):
        ANY = "any", "或签"
        ALL = "all", "会签"

    class ApproverType(models.TextChoices):
        USERS = "users", "指定成员"
        ROLE = "role", "项目角色组"
        FIELD = "field", "任务字段"

    flow = models.ForeignKey(
        ApprovalFlow, on_delete=models.CASCADE, related_name="nodes", verbose_name="所属审批流"
    )
    level = models.PositiveSmallIntegerField(verbose_name="级号（1..10，流内连续递增）")
    pass_mode = models.CharField(max_length=8, choices=PassMode.choices, verbose_name="通过模式")
    approver_type = models.CharField(max_length=8, choices=ApproverType.choices, verbose_name="审批人来源")
    approver_config = models.JSONField(
        default=dict, verbose_name="审批人配置",
        help_text='users: {"user_ids":[…]}；role: {"role":"PROJ_ADMIN"}；field: {"field":"reporter|assignees"}',
    )
    timeout_hours = models.PositiveIntegerField(null=True, blank=True, verbose_name="超时小时（NULL=不超时）")

    class Meta(BaseModel.Meta):
        db_table = "approval_nodes"
        verbose_name = "审批节点"
        verbose_name_plural = verbose_name
        ordering = ("level",)  # type: ignore[assignment]
        constraints = [
            models.UniqueConstraint(fields=["flow", "level"], name="uniq_node_level_per_flow"),
            models.CheckConstraint(
                check=models.Q(level__gte=1, level__lte=10),  # type: ignore[call-arg]  # 同上 stub 先例
                name="chk_node_level_range"),
        ]

    def __str__(self) -> str:
        return f"{self.flow_id}:L{self.level}({self.pass_mode})"


class ApprovalInstance(BaseModel):
    """审批实例：一次发起一条；flow_snapshot 冻结定义（BR-11）。"""

    class Status(models.TextChoices):
        PENDING = "pending", "审批中"
        APPROVED = "approved", "已通过"
        REJECTED = "rejected", "已驳回"
        WITHDRAWN = "withdrawn", "已撤回"
        TERMINATED = "terminated", "已终止"

    issue = models.ForeignKey(
        "db.Issue", on_delete=models.PROTECT, related_name="approval_instances", verbose_name="任务",
        help_text="PROTECT：硬删任务前须先终止/归档其实例；软删（deleted_at）不涉级联，"
                  "实例由 §2.3 钩子终止",
    )
    transition = models.ForeignKey(
        "db.WorkflowTransition", on_delete=models.PROTECT, related_name="instances", verbose_name="经由此边"
    )
    flow_snapshot = models.JSONField(verbose_name="立案时刻的流+节点定义快照")
    initiator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="发起人"
    )
    current_level = models.PositiveSmallIntegerField(default=1, verbose_name="当前级")
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True, verbose_name="状态"
    )
    is_terminal_passed = models.BooleanField(
        default=False, verbose_name="终审回填瞬态位",
        help_text="终审回填三文档时序闭环（WF-001 §4.4 / WF-002 §4.5 / WF-006 §4.2）：终级票据"
                  "全通过后、引擎 transition() 回调前置 True——守门放行「is_terminal_passed=True "
                  "AND status=pending」形态；迁移成功 finalize APPROVED / 守卫失败复位 False 并 "
                  "finalize TERMINATED(guard_failed_at_complete)。回调窗口外恒 False",
    )
    terminal_reason = models.CharField(
        max_length=32, blank=True, default="", verbose_name="终止原因",
        help_text="terminated 时：state_changed|guard_failed_at_complete|issue_deleted|issue_archived|admin",
    )
    from_state = models.ForeignKey(
        "db.State", on_delete=models.PROTECT, related_name="+", verbose_name="发起前状态（驳回回退语义锚点）"
    )
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name="完结时间")

    class Meta(BaseModel.Meta):
        db_table = "approval_instances"
        verbose_name = "审批实例"
        verbose_name_plural = verbose_name
        # BR-10：同任务同边仅一个 pending（部分唯一索引）
        constraints = [
            models.UniqueConstraint(
                fields=["issue", "transition"], condition=models.Q(status="pending"),
                name="uniq_pending_instance_per_edge",
            ),
        ]
        indexes = [
            models.Index(fields=["issue", "status"], name="idx_instance_issue"),
            models.Index(fields=["status", "updated_at"], name="idx_instance_scan"),
        ]

    def __str__(self) -> str:
        return f"{self.issue_id}:{self.status}L{self.current_level}"


class ApprovalRecord(models.Model):
    """审批票：开票 INSERT + 一次合法迁移 pending→终态（仅 action/comment/acted_at
    可写；BR-09，WF-006 §4.2 触发器强制；BigAutoField 主键保时序）。

    非 BaseModel：动作一次成型、无二次更新语义、无软删（BR-09 落到表结构）。
    """

    class Action(models.TextChoices):
        PENDING = "pending", "待审"
        APPROVE = "approve", "通过"
        REJECT = "reject", "驳回"
        SKIPPED = "skipped", "跳过（自审/离职）"

    id = models.BigAutoField(primary_key=True)
    instance = models.ForeignKey(
        ApprovalInstance, related_name="records", on_delete=models.CASCADE, verbose_name="所属实例"
    )
    level = models.PositiveSmallIntegerField(verbose_name="级号")
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="审批人"
    )
    action = models.CharField(max_length=8, choices=Action.choices, default=Action.PENDING, verbose_name="动作")
    comment = models.TextField(blank=True, default="", verbose_name="意见")
    acted_at = models.DateTimeField(null=True, blank=True, verbose_name="动作时间")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="开票时间")

    class Meta:
        db_table = "approval_records"
        verbose_name = "审批票"
        verbose_name_plural = verbose_name
        ordering = ("level", "id")  # type: ignore[assignment]
        constraints = [
            models.UniqueConstraint(fields=["instance", "level", "approver"], name="uniq_record_per_approver"),
        ]
        indexes = [
            models.Index(fields=["approver", "action", "created_at"], name="idx_record_todo"),
            models.Index(fields=["instance", "level"], name="idx_record_instance"),
        ]

    def __str__(self) -> str:
        return f"{self.instance_id}:L{self.level}:{self.action}"
