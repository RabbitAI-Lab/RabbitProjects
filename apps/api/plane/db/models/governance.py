"""租户治理与风控模型（AUTH-012 §4.1/§4.2，P4 R1）。

SaaS 形态治理层：Tenant 归集 Workspace、TenantQuota 两层配额的租户层
（L-T 硬上限）、RiskRule/RiskEvent 六类规则与事件、GovernanceTicket L2
授权工单、RiskAppeal 误报申诉、BoundaryReport 边界证明报告。

全部门控 TENANT_GOVERNANCE_ENABLED（BR-11）：False（私有化）时治理 API
SERVER_NOT_IMPLEMENTED、引擎不调度、模型存在但无数据；行级隔离底座归
AUTH-006，本域查询同样走 accessible_by 族（BR-01）。
"""

from django.db import models

from plane.db.models.base import BaseModel


class Tenant(BaseModel):
    """客户法人级租户；Workspace 通过 tenant_id 归集（§2.1）。"""

    class Plan(models.TextChoices):
        FREE = "free", "免费"
        STANDARD = "standard", "标准"
        ENTERPRISE = "enterprise", "企业"
        FLAGSHIP = "flagship", "旗舰"  # §2.3 TenantPlan 四档；FILE-006「旗舰档」即本档

    name = models.CharField(max_length=128)
    tier = models.CharField(max_length=12, choices=Plan.choices, default=Plan.FREE)
    seats = models.PositiveIntegerField(default=10)  # 合同席位
    is_frozen = models.BooleanField(default=False)
    frozen_at = models.DateTimeField(null=True, blank=True)
    frozen_reason = models.CharField(max_length=255, blank=True)

    class Meta(BaseModel.Meta):
        db_table = "gov_tenant"

    def __str__(self) -> str:
        return f"{self.name}({self.tier})"


class TenantQuota(BaseModel):
    """租户级配额（§2.3 两层模型的 L-T 租户层，硬上限）；null 表示跟随 tier 默认值。"""

    tenant = models.OneToOneField(Tenant, on_delete=models.CASCADE, related_name="quota")
    storage_bytes = models.BigIntegerField(null=True, blank=True)
    member_limit = models.PositiveIntegerField(null=True, blank=True)
    api_rate_per_minute = models.PositiveIntegerField(null=True, blank=True)
    export_rows_per_day = models.PositiveIntegerField(null=True, blank=True)
    webhook_limit = models.PositiveIntegerField(null=True, blank=True)
    project_limit = models.PositiveIntegerField(null=True, blank=True)
    downgrade_grace_until = models.DateField(null=True, blank=True)  # §2.3 宽限期

    class Meta(BaseModel.Meta):
        db_table = "gov_tenant_quota"

    def __str__(self) -> str:
        return f"quota({self.tenant_id})"


class RiskRule(BaseModel):
    """预置六类规则的平台默认 + 租户覆盖（只紧不松，BR-06）。

    tenant=null → 平台默认行（六条种子由迁移写入）；PG 对 NULL 互异，
    复合 (code, tenant) 唯一约束对平台默认行不去重——拆两条偏条件唯一
    约束（FILE-002 BR-01 根层同款范式）。
    """

    code = models.CharField(max_length=8)  # R-01..R-06
    tenant = models.ForeignKey(Tenant, null=True, blank=True, on_delete=models.CASCADE)
    threshold = models.JSONField()  # {"count":200,"window":"10m"}
    action = models.CharField(
        max_length=12,
        # deny=硬拒：配额类规则（R-03）的截断档——单次请求直接 409 拒绝，
        # 与 freeze 分档（freeze 针对租户整体状态、需双人审批；deny 不置 is_frozen）
        choices=[("alert", "仅告警"), ("throttle", "限流"), ("freeze", "冻结"), ("deny", "硬拒")],
        default="alert",
    )
    is_enabled = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        db_table = "gov_risk_rule"
        constraints = [
            models.UniqueConstraint(
                fields=["code"], condition=models.Q(tenant__isnull=True), name="uq_risk_rule_platform"
            ),
            models.UniqueConstraint(
                fields=["code", "tenant"], condition=models.Q(tenant__isnull=False), name="uq_risk_rule_tenant"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code}({self.tenant_id or 'platform'})"


class RiskEvent(BaseModel):
    """规则触发事件；证据快照触发即固化（BR-05），处置动作全链路审计。"""

    class Status(models.TextChoices):
        OPEN = "open", "待处置"
        ACTIONED = "actioned", "已处置"
        DISMISSED = "dismissed", "误报关闭"

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="risk_events")
    rule_code = models.CharField(max_length=8)
    severity = models.CharField(max_length=8)  # low/medium/high
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    evidence = models.JSONField()  # BR-05 证据快照
    aggregate_key = models.CharField(max_length=128)  # BR-08 聚合键（含主体段，§4.3）
    actions = models.JSONField(default=list)  # [{action, by, at, note}]
    freeze_approval = models.JSONField(null=True, blank=True)
    # {"initiator": "...", "approver": "...", "approved_at": "..."}

    class Meta(BaseModel.Meta):
        db_table = "gov_risk_event"
        indexes = [
            models.Index(fields=["tenant", "-created_at"], name="idx_risk_event_tenant"),
            models.Index(fields=["status", "-created_at"], name="idx_risk_event_open"),
            models.Index(
                fields=["rule_code", "-created_at"], name="idx_risk_event_rule"
            ),  # ?rule= 开放筛选必须有索引（api-conventions §5.3）
            models.Index(fields=["aggregate_key", "-created_at"], name="idx_risk_event_agg"),
        ]

    def __str__(self) -> str:
        return f"{self.rule_code}:{self.aggregate_key[:24]}"


class GovernanceTicket(BaseModel):
    """L2 业务内容访问授权工单（§2.5 二级知情的落库闸门，BR-07）。

    状态机：pending → approved（granted_at 起 24h，expires_at 到点由 beat 置
    expired）/ rejected；approved → revoked（客户或运营任一方撤回——写路径为
    POST …/l2-tickets/{id}/revocation/，UT-18）。approved 双通道：客户在线
    批准（approval/ 端点）或书面授权运营确认（written-confirm/ 端点，
    written_ref 必填校验，§4.4）。
    """

    class Status(models.TextChoices):
        PENDING = "pending", "待客户批准"
        APPROVED = "approved", "已批准"
        REJECTED = "rejected", "已驳回"
        EXPIRED = "expired", "已过期"
        REVOKED = "revoked", "已撤回"

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="l2_tickets")
    risk_event = models.ForeignKey(RiskEvent, on_delete=models.CASCADE, related_name="l2_tickets")
    requested_by = models.ForeignKey("db.User", on_delete=models.CASCADE, related_name="l2_tickets_requested")
    scope = models.JSONField()  # 授权字段清单 + 事件对象 ID（最小必要）
    approve_channel = models.CharField(
        max_length=8,
        choices=[("online", "客户在线批准"), ("written", "书面授权")],
    )
    written_ref = models.CharField(max_length=128, blank=True)  # 书面授权工单号（written 必填）
    approver = models.ForeignKey(
        "db.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="l2_tickets_approved"
    )  # 客户 WS_ADMIN（online）
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    granted_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)  # granted_at + 24h
    note = models.CharField(max_length=255, blank=True)

    class Meta(BaseModel.Meta):
        db_table = "gov_l2_ticket"
        indexes = [models.Index(fields=["risk_event", "status", "-created_at"], name="idx_l2_ticket_event")]

    def __str__(self) -> str:
        return f"L2:{self.status}"


class RiskAppeal(BaseModel):
    """客户侧误报申诉（§4.4 appeals/ 端点落库）。

    状态机：pending → accepted（事件置 dismissed 并自动解除处置，复用
    releases/ 逻辑）/ rejected（事件维持原处置）。复核写路径唯一入口为
    POST /api/v1/instances/risk-appeals/{id}/review/。
    """

    class Status(models.TextChoices):
        PENDING = "pending", "待复核"
        ACCEPTED = "accepted", "申诉成立"
        REJECTED = "rejected", "申诉驳回"

    event = models.ForeignKey(RiskEvent, on_delete=models.CASCADE, related_name="appeals")
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="risk_appeals")
    applicant = models.ForeignKey("db.User", on_delete=models.CASCADE, related_name="risk_appeals")  # 客户 WS_ADMIN
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey(
        "db.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="risk_appeals_reviewed"
    )
    review_note = models.CharField(max_length=255, blank=True)

    class Meta(BaseModel.Meta):
        db_table = "gov_risk_appeal"
        indexes = [models.Index(fields=["tenant", "status", "-created_at"], name="idx_risk_appeal_tenant")]

    def __str__(self) -> str:
        return f"appeal:{self.status}"


class BoundaryReport(BaseModel):
    """数据边界证明报告（§4.4 202 异步产出物登记行，BR-10：系统生成，运营仅触发）。"""

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="boundary_reports")
    requested_by = models.ForeignKey("db.User", on_delete=models.CASCADE, related_name="boundary_reports")
    state = models.CharField(
        max_length=12,
        choices=[("queued", "排队"), ("processing", "生成中"), ("succeeded", "完成"), ("failed", "失败")],
        default="queued",
    )  # api-conventions §13.1 异步枚举（无 cancelled——本文无取消入口）
    # 近 90 天跨租户访问尝试（计数源=隔离渗透 beat 任务日探测回填，§4.3；
    # 应恒为 0，>0 即事故）
    cross_tenant_hits = models.PositiveIntegerField(default=0)
    file_path = models.CharField(max_length=255, blank=True)  # MinIO 私有前缀对象键（succeeded 后非空）

    class Meta(BaseModel.Meta):
        db_table = "gov_boundary_report"
        indexes = [models.Index(fields=["tenant", "-created_at"], name="idx_boundary_report_tenant")]

    def __str__(self) -> str:
        return f"report:{self.state}"
