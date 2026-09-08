"""工作流引擎数据模型（WF-001 §4.2）——Sprint 7 企业工作流核心的模型基座。

三表：Workflow（绑定项目×类型）/ WorkflowState（状态节点，引用既有 State）/
WorkflowTransition（流转边，携带 guards/side_effects 两 JSONB 协议 + approval_flow
FK 挂点）。协议字段在 WF-001 冻结，WF-002/003/004 只扩展 type 枚举不改结构。

零迁移兜底（BR：V1.0 兼容约束）：未配置工作流的项目不建任何行，引擎兜底链
第 3 级自动回落 V1.0 自由流转——`Issue.state` 语义全程未变。
"""
from django.conf import settings
from django.db import models

from plane.db.models.base import BaseModel


class Workflow(BaseModel):
    """工作流 —— 绑定 项目×类型 的受控流转图（P3 企业版核心）。

    issue_type=NULL 表示「项目默认工作流」，对未单独配置类型的任务生效；
    三级兜底：类型专属 → 项目默认 → V1.0 自由流转（WF-001 §2.2）。
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "草稿"
        PUBLISHED = "published", "已发布"
        ARCHIVED = "archived", "已归档"

    project = models.ForeignKey(
        "db.Project", on_delete=models.CASCADE, related_name="workflows", verbose_name="所属项目"
    )
    issue_type = models.ForeignKey(
        "db.IssueType", on_delete=models.CASCADE, null=True, blank=True,
        related_name="workflows", verbose_name="绑定任务类型",
        help_text="NULL = 项目默认工作流",
    )
    name = models.CharField(max_length=64, verbose_name="工作流名称")
    description = models.TextField(blank=True, verbose_name="说明")
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True, verbose_name="状态"
    )
    version = models.PositiveIntegerField(default=1, verbose_name="版本号", help_text="每次发布 +1")
    based_on_version = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="克隆来源版本",
        help_text="基于已发布版本克隆草稿时写入其 version；首次建草稿 NULL（WF-001 §4.8⑤ 注）")
    published_at = models.DateTimeField(null=True, blank=True, verbose_name="最近发布时间")
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name="发布人",
    )
    source_template = models.ForeignKey(
        "db.WorkflowTemplate", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="instances", verbose_name="来源模板", help_text="WF-005 模板实例化溯源",
    )

    class Meta(BaseModel.Meta):
        db_table = "workflows"
        verbose_name = "工作流"
        verbose_name_plural = verbose_name
        # BR-02：同一 (project, issue_type) 至多一个 published / 一个 draft。
        # PG NULLS DISTINCT 语义下这两条仅对类型专属（issue_type 非空）完全生效；
        # 项目默认工作流（NULL）由 Service 层事务内 SELECT…FOR UPDATE 锁项目行兜底。
        constraints = [
            models.UniqueConstraint(
                fields=["project", "issue_type"],
                condition=models.Q(status="published", deleted_at__isnull=True),
                name="uniq_published_workflow_per_type",
            ),
            models.UniqueConstraint(
                fields=["project", "issue_type"],
                condition=models.Q(status="draft", deleted_at__isnull=True),
                name="uniq_draft_workflow_per_type",
            ),
        ]
        indexes = [models.Index(fields=["project", "status"], name="idx_workflow_project_status")]

    def __str__(self) -> str:
        return f"{self.name}({self.status}v{self.version})"


class WorkflowState(BaseModel):
    """工作流状态节点 —— 引用既有 State，group 语义与全部下游报表零影响。"""

    workflow = models.ForeignKey(
        Workflow, on_delete=models.CASCADE, related_name="wf_states", verbose_name="所属工作流"
    )
    state = models.ForeignKey(
        "db.State", on_delete=models.PROTECT, related_name="workflow_nodes", verbose_name="引用状态",
        help_text="PROTECT：BR-15 被引用状态禁止删除",
    )
    is_initial = models.BooleanField(default=False, verbose_name="是否初始状态")
    layout_x = models.FloatField(default=0.0, verbose_name="画布 X")
    layout_y = models.FloatField(default=0.0, verbose_name="画布 Y")
    field_locks = models.JSONField(
        default=list, blank=True, verbose_name="进入本状态锁定的字段",
        help_text='WF-004 协议：[{"field": "target_date"}, {"field": "cf_<key>（TASK-008 field_key 语义名形态）"}]',
    )

    class Meta(BaseModel.Meta):
        db_table = "workflow_states"
        verbose_name = "工作流状态节点"
        verbose_name_plural = verbose_name
        # BR-03：同一工作流内 state 不重复
        constraints = [
            models.UniqueConstraint(fields=["workflow", "state"], name="uniq_state_per_workflow"),
        ]

    def __str__(self) -> str:
        return f"{self.workflow_id}:{self.state_id}{'*initial' if self.is_initial else ''}"


class WorkflowTransition(BaseModel):
    """流转边 —— guards/side_effects 两个 JSONB 协议字段 + approval_flow FK
    是 WF-002/003/004 的全部挂接点（协议冻结见 WF-001 §4.7）。"""

    workflow = models.ForeignKey(
        Workflow, on_delete=models.CASCADE, related_name="transitions", verbose_name="所属工作流"
    )
    from_state = models.ForeignKey(
        WorkflowState, on_delete=models.CASCADE, related_name="outgoing", verbose_name="源节点"
    )
    to_state = models.ForeignKey(
        WorkflowState, on_delete=models.CASCADE, related_name="incoming", verbose_name="目标节点"
    )
    name = models.CharField(max_length=64, verbose_name="流转名称", help_text="按钮文案，如「提交评审」")
    guards = models.JSONField(
        default=list, blank=True, verbose_name="守卫协议",
        help_text="schema 见 WF-001 §4.7，守卫矩阵见 WF-004"
    )
    side_effects = models.JSONField(
        default=list, blank=True, verbose_name="副作用协议",
        help_text="schema 见 WF-001 §4.7，规则引擎见 WF-003"
    )
    approval_flow = models.ForeignKey(
        "db.ApprovalFlow", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="transitions", verbose_name="触发审批流",
        help_text="WF-002：非空则流转挂起待审批",
    )
    sort_order = models.PositiveIntegerField(default=1000, verbose_name="按钮排序")

    class Meta(BaseModel.Meta):
        db_table = "workflow_transitions"
        verbose_name = "流转边"
        verbose_name_plural = verbose_name
        # BR-06：同两端多条不同名边合法；BR-07 禁自环
        constraints = [
            models.UniqueConstraint(
                fields=["workflow", "from_state", "to_state", "name"],
                name="uniq_transition_edge_name",
            ),
            models.CheckConstraint(
                check=~models.Q(from_state=models.F("to_state")),  # type: ignore[call-arg]  # stub 声明过窄（同 Issue chk_issue_start_before_target）
                name="chk_transition_no_self_loop",
            ),
        ]
        indexes = [models.Index(fields=["workflow", "from_state"], name="idx_transition_from")]

    def __str__(self) -> str:
        return f"{self.workflow_id}:{self.name}"


class WorkflowTemplate(BaseModel):
    """工作流模板（Workspace 级；is_builtin 预设不可改，BR-07）——表结构随 WF-001
    迁移先行落地（Workflow.source_template FK 需要），模板功能面（下发/解锁/
    预设四套）归属 WF-005 第 10 周交付。"""

    class Status(models.TextChoices):
        DRAFT = "draft", "草稿"
        PUBLISHED = "published", "已发布"
        ARCHIVED = "archived", "已归档"

    workspace = models.ForeignKey(
        "db.Workspace", on_delete=models.CASCADE, related_name="workflow_templates", verbose_name="所属工作空间"
    )
    name = models.CharField(max_length=64, verbose_name="模板名称")
    description = models.CharField(max_length=255, blank=True, default="", verbose_name="说明")
    version = models.PositiveIntegerField(default=1, verbose_name="模板版本（发布递增）")
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True, verbose_name="状态"
    )
    is_builtin = models.BooleanField(default=False, verbose_name="预设模板")
    graph_snapshot = models.JSONField(verbose_name="图快照", help_text="WF-005 §2.1 序列化协议；BR-02 保存即校验")

    class Meta(BaseModel.Meta):
        db_table = "workflow_templates"
        verbose_name = "工作流模板"
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=["workspace", "name", "version"], name="uniq_template_name_version"),
        ]
        indexes = [models.Index(fields=["workspace", "status"], name="idx_template_ws")]

    def __str__(self) -> str:
        return f"{self.workspace_id}:{self.name}v{self.version}"


class TemplateDistribution(BaseModel):
    """下发记录：模板 × 项目（锁定引用的唯一事实源，WF-005 BR-04/06）——两步下发：
    受理 pending_confirm → 确认实例化 active；升级推送 upgrade_available。"""

    template = models.ForeignKey(
        WorkflowTemplate, on_delete=models.CASCADE, related_name="distributions", verbose_name="模板")
    project = models.ForeignKey(
        "db.Project", on_delete=models.CASCADE, related_name="template_distributions", verbose_name="项目")
    template_version = models.PositiveIntegerField(verbose_name="下发时模板版本")
    locked = models.BooleanField(default=False, verbose_name="锁定")
    applied_workflow = models.ForeignKey(
        Workflow, null=True, on_delete=models.SET_NULL, related_name="+", verbose_name="实例化产物")
    status = models.CharField(
        max_length=16, default="pending_confirm", db_index=True, verbose_name="状态",
        help_text="pending_confirm|active|upgrade_available|withdrawn|unlocked")
    state_mapping = models.JSONField(default=dict, verbose_name="状态映射单留痕（BR-05）")

    class Meta(BaseModel.Meta):
        db_table = "template_distributions"
        verbose_name = "模板下发记录"
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=["template", "project"], name="uniq_distribution"),
        ]

    def __str__(self) -> str:
        return f"dist({self.template_id}:{self.project_id}:{self.status})"


class TemplateUnlockRequest(BaseModel):
    """项目侧解锁/升级申请（WF-005 §4.4）：每项目至多一条待审（部分唯一约束）。"""

    class Kind(models.TextChoices):
        UNLOCK = "unlock", "解锁（转独立副本）"
        UPGRADE = "upgrade", "升级（替换式升级到新版本）"

    class Status(models.TextChoices):
        PENDING = "pending", "待审"
        APPROVED = "approved", "已批准"
        REJECTED = "rejected", "已驳回"

    distribution = models.ForeignKey(
        TemplateDistribution, on_delete=models.CASCADE, related_name="unlock_requests", verbose_name="下发记录")
    project = models.ForeignKey(
        "db.Project", on_delete=models.CASCADE, related_name="template_unlock_requests", verbose_name="项目")
    kind = models.CharField(max_length=8, choices=Kind.choices, verbose_name="申请类型")
    reason = models.CharField(max_length=255, verbose_name="申请理由（必填）")
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING, verbose_name="状态")
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+", verbose_name="处理人（WS_ADMIN）")
    handled_at = models.DateTimeField(null=True, blank=True, verbose_name="处理时间")

    class Meta(BaseModel.Meta):
        db_table = "template_unlock_requests"
        verbose_name = "模板解锁/升级申请"
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=["project"],
                condition=models.Q(status="pending", deleted_at__isnull=True),
                name="uniq_unlock_request_pending_per_project"),
        ]
        indexes = [models.Index(fields=["project", "status"], name="idx_unlock_req_proj")]

    def __str__(self) -> str:
        return f"unlock-req({self.project_id}:{self.kind}:{self.status})"
