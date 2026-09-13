"""AI 能力模型（AI-001 §4.3，P4 R8）。

AiConsent 授权书（版本化重签 BR-02）、AiCallLedger 台账（出域字段摘要
+ prompt 哈希——内容不入账）、AiQuotaCounter 配额落库对账、IssueRiskScore
风险分（规则版与模型版双跑校准窗口）、AiFeedback 轻反馈。落位 plane/ai/
独立应用（INSTALLED_APPS 登记）。
"""

from django.db import models

from plane.db.models.base import BaseModel


class AiConsent(BaseModel):
    """AI 授权书签署记录；条款版本变更需重签（BR-02）。"""

    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE)
    consent_version = models.CharField(max_length=8)  # v3
    capabilities = models.JSONField(default=list)  # 四能力子集
    provider_policy = models.CharField(max_length=16)  # commercial/selfhosted
    signed_by = models.ForeignKey("db.User", on_delete=models.PROTECT)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        db_table = "ai_consents"
        indexes = [models.Index(fields=["workspace", "-created_at"], name="idx_ai_consent_ws")]


class AiCallLedger(BaseModel):
    """出域调用台账（BR-06 失败也记；prompt 哈希不入内容）。"""

    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE)
    actor = models.ForeignKey("db.User", null=True, on_delete=models.SET_NULL)
    capability = models.CharField(max_length=16)  # summary/dup/risk/gen
    provider = models.CharField(max_length=24)
    model = models.CharField(max_length=48)
    outbound_fields = models.JSONField(default=dict)  # {"comment_text": 32}
    prompt_hash = models.CharField(max_length=64)
    tokens = models.JSONField(default=dict)  # {"in":..,"out":..}
    latency_ms = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=8)  # ok/failed
    error = models.CharField(max_length=255, blank=True)

    class Meta(BaseModel.Meta):
        db_table = "ai_call_ledgers"
        indexes = [
            models.Index(fields=["workspace", "-created_at"], name="idx_ai_ledger_ws"),
        ]


class AiQuotaCounter(BaseModel):
    """BR-05 配额对账：workspace × 能力 × 日 计数（Redis 预检的落库镜像）。"""

    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE)
    capability = models.CharField(max_length=16)
    quota_date = models.DateField()
    used = models.PositiveIntegerField(default=0)

    class Meta(BaseModel.Meta):
        db_table = "ai_quota_counters"
        constraints = [
            models.UniqueConstraint(fields=["workspace", "capability", "quota_date"], name="uq_ai_quota_ws_cap_date")
        ]


class IssueRiskScore(BaseModel):
    """风险预警分数（§2.3）：实时重算插新行不覆盖（双跑校准窗口）。"""

    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE)
    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE)
    score = models.PositiveSmallIntegerField()  # 0-100
    top_reasons = models.JSONField(default=list)  # ≤3 条主因
    confidence = models.FloatField(default=0.0)
    model_version = models.CharField(max_length=16)  # rule_v0 / lgbm_v1
    source = models.CharField(max_length=8)  # daily / realtime
    computed_at = models.DateTimeField()

    class Meta(BaseModel.Meta):
        db_table = "issue_risk_scores"
        indexes = [models.Index(fields=["issue", "-computed_at"], name="idx_risk_score_issue")]


class AiFeedback(BaseModel):
    """BR-07 轻反馈（仅 verdict 与自填原因，不落 AI 产出内容）。"""

    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE)
    actor = models.ForeignKey("db.User", null=True, on_delete=models.SET_NULL)
    capability = models.CharField(max_length=16)
    issue = models.ForeignKey("db.Issue", null=True, on_delete=models.SET_NULL)
    verdict = models.CharField(max_length=8)  # useful / useless
    reason = models.CharField(max_length=200, blank=True)

    class Meta(BaseModel.Meta):
        db_table = "ai_feedbacks"
        indexes = [models.Index(fields=["workspace", "-created_at"], name="idx_ai_feedback_ws")]
