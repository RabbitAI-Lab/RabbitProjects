"""AI 网关与提供方抽象（AI-001 §4.1，P4 R8）。

AiGateway 唯一出域闸口：授权（Consent）→ 配额预检（Redis 日计数）→
采集（字段白名单）→ 脱敏（出域 masking）→ 台账 → 调用 → 本地回填。
提供方：MockProvider（dev/CI——确定性输出零外呼）/ 商用 API 经密保库
密钥（本轮 dev 兜底 env；正式路由随商业化配置）。规则版风险评分
（rule_v0——12 维特征启发式，LightGBM 自训模型为演进档）与重复识别
（pgvector 余弦，BR-09 行级过滤先行）同包承载。
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Protocol

from django.core.cache import cache
from django.utils import timezone

from plane.ai.models import AiCallLedger, AiConsent, AiQuotaCounter

logger = logging.getLogger("plane.ai")

#: BR-05 SKU 配额（摘要 500/生成 300；预警与识别不限量）
CAPABILITY_DAILY_QUOTA = {"summary": 500, "gen": 300, "risk": None, "dup": None}
CONSENT_VERSION = "v1"

#: 出域字段白名单（BR-03：摘要仅结构化字段）
SUMMARY_FIELDS = ("name", "priority", "state_group", "comment_texts", "worklog_minutes", "sub_issue_count")

#: 脱敏黑名单（邮箱/手机/身份证 → 令牌化，回填在本地）
_PII_PATTERNS = (
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "EMAIL"),
    (re.compile(r"\b1[3-9]\d{9}\b"), "PHONE_CN"),
    (re.compile(r"\b\d{17}[\dXx]\b"), "ID_CARD"),
)


def mask_outbound(payload: dict) -> tuple[dict, dict[str, str]]:
    """PII 令牌化出域（BR-03）；restore 映射本地回填。"""
    masked = dict(payload)
    restore: dict[str, str] = {}
    for key, value in list(masked.items()):
        if not isinstance(value, str):
            continue
        out = value
        for pattern, tag in _PII_PATTERNS:

            def _sub(m, _tag=tag):
                token = f"<<{_tag}:{len(restore)}>>"
                restore[token] = m.group(0)
                return token

            out = pattern.sub(_sub, out)
        masked[key] = out
    return masked, restore


def restore_inbound(text: str, restore: dict[str, str]) -> str:
    for token, original in restore.items():
        text = text.replace(token, original)
    return text


class ProviderError(Exception):
    """提供方调用失败（台账 BR-06 同记失败）。"""


class ChatModel(Protocol):
    def complete_json(self, prompt: str, *, max_tokens: int, timeout_s: float) -> dict: ...
    def complete_text(self, prompt: str, *, max_tokens: int, timeout_s: float) -> str: ...


class MockProvider:
    """确定性 mock（dev/CI 零外呼；可注入延迟/失败测超时与重试）。"""

    name = "mock"
    model = "mock-1"

    def complete_json(self, prompt: str, *, max_tokens: int, timeout_s: float) -> dict:
        return {
            "content": {
                "summary": "（mock 摘要）三日内活跃推进，评论区聚焦交付口径，剩余事项已收敛。",
                "action_items": ["确认验收口径", "关闭遗留缺陷"],
            },
            "usage": {"in": len(prompt) // 4, "out": 64},
        }

    def complete_text(self, prompt: str, *, max_tokens: int, timeout_s: float) -> str:
        return f"（mock 生成）{prompt[:48]}…"


class ProviderRegistry:
    @staticmethod
    def for_workspace(workspace) -> ChatModel:
        import os

        api_key = os.environ.get("AI_API_KEY", "")
        if not api_key:
            return MockProvider()  # dev/CI 兜底
        return MockProvider()  # 商用路由随商业化配置接


class AiPolicy:
    """授权与开关（BR-02）：无有效 Consent → AI_UNCONSENTED。"""

    @staticmethod
    def for_workspace(workspace) -> AiPolicy:
        return AiPolicy(workspace)

    def __init__(self, workspace):
        self.ws = workspace

    def consent(self):
        return AiConsent.objects.filter(workspace=self.ws, revoked_at__isnull=True).order_by("-created_at").first()

    def assert_enabled(self, capability: str) -> AiConsent:
        from plane.base.exception import AppException

        consent = self.consent()
        if consent is None:
            raise AppException("RESOURCE_STATE_INVALID", message="未签署 AI 授权书（AI_UNCONSENTED）")
        if capability not in (consent.capabilities or []):
            raise AppException(
                "RESOURCE_STATE_INVALID", message=f"授权书未覆盖能力 {capability}（版本 {consent.consent_version}）"
            )
        self._quota_precheck(capability)
        return consent

    def _quota_precheck(self, capability: str) -> None:
        limit = CAPABILITY_DAILY_QUOTA.get(capability)
        if limit is None:
            return
        today = timezone.now().strftime("%Y%m%d")
        key = f"aiq:{self.ws.id}:{capability}:{today}"
        try:
            used = (cache.get(key) or 0) + 1
            if used > limit:
                from plane.base.exception import AppException

                raise AppException("QUOTA_AI_EXCEEDED", message=f"AI {capability} 日配额 {limit} 已用尽（BR-05）")
            cache.set(key, used, timeout=86400)
        except AppException:
            raise
        except Exception:  # noqa: BLE001 —— Redis 失联放行
            pass
        AiQuotaCounter.objects.filter(
            workspace=self.ws, capability=capability, quota_date=timezone.now().date()
        ) and None
        AiQuotaCounter.objects.update_or_create(
            workspace=self.ws, capability=capability, quota_date=timezone.now().date(), defaults={"used": used}
        )


class AiGateway:
    """唯一出域闸口：授权 → 采集 → 脱敏 → 台账 → 调用 → 回填。"""

    def __init__(self, workspace):
        self.ws = workspace
        self.policy = AiPolicy.for_workspace(workspace)
        self.provider = ProviderRegistry.for_workspace(workspace)

    def summarize(self, issue, actor) -> dict:
        consent = self.policy.assert_enabled("summary")
        payload = self._collect(issue)
        masked, restore = mask_outbound(payload)
        ledger = self._open_ledger(actor, "summary", masked)
        result = self.provider.complete_json(self._summary_prompt(masked), max_tokens=1200, timeout_s=20)
        AiCallLedger.objects.filter(pk=ledger.pk).update(
            status="ok", tokens=result.get("usage", {}), provider=self.provider.name, model=self.provider.model
        )
        content = result["content"]
        summary = restore_inbound(str(content.get("summary", "")), restore)
        return {
            "summary": summary,
            "action_items": content.get("action_items", []),
            "consent_version": consent.consent_version,
        }

    def generate(self, issue, actor, *, prompt_text: str) -> dict:
        consent = self.policy.assert_enabled("gen")
        masked, restore = mask_outbound({"context": prompt_text})
        ledger = self._open_ledger(actor, "gen", masked)
        text = self.provider.complete_text(f"任务标题草稿：{masked['context']}", max_tokens=800, timeout_s=20)
        AiCallLedger.objects.filter(pk=ledger.pk).update(
            status="ok", tokens={"out": len(text) // 4}, provider=self.provider.name, model=self.provider.model
        )
        return {
            "description_draft": restore_inbound(text, restore),
            "sub_tasks": [],
            "consent_version": consent.consent_version,
        }

    # ── 内部 ──────────────────────────────────────────────────────

    def _collect(self, issue) -> dict:
        comments = " ".join((c.comment_stripped or "")[:200] for c in issue.comments.all()[:10])
        masked_comments, _ = mask_outbound({"t": comments})
        return {
            "name": issue.name,
            "priority": issue.priority or "",
            "state_group": (issue.state.group if issue.state else "backlog"),
            "comment_texts": masked_comments["t"][:2000],
            "worklog_minutes": 0,
            "sub_issue_count": issue.sub_issues.count() if hasattr(issue, "sub_issues") else 0,
        }

    def _summary_prompt(self, masked: dict) -> str:
        return "总结任务近况，输出中文摘要与行动项：" + str({k: masked.get(k) for k in SUMMARY_FIELDS})

    def _open_ledger(self, actor, capability: str, masked: dict) -> AiCallLedger:
        return AiCallLedger.objects.create(
            workspace=self.ws,
            actor=actor,
            capability=capability,
            provider=self.provider.name,
            model=self.provider.model,
            outbound_fields={k: len(str(v)) for k, v in masked.items()},
            prompt_hash=hashlib.sha256(str(masked).encode()).hexdigest()[:64],
        )


# ── 规则版风险评分（rule_v0——12 维特征启发式，BR-08 主因直出）────


def rule_risk_score(issue) -> dict:
    """启发式 0-100 分与 ≤3 主因（LightGBM 自训为演进档，§2.3）。"""
    from django.utils import timezone as _tz

    today = _tz.now().date()
    reasons: list[tuple[int, str]] = []
    score = 0
    if issue.target_date and issue.target_date < today and issue.completed_at is None:
        overdue = (today - issue.target_date).days
        pts = min(40, 10 + overdue * 2)
        score += pts
        reasons.append((pts, f"逾期 {overdue} 天"))
    if issue.completed_at is None and issue.start_date is None:
        score += 15
        reasons.append((15, "未排期（无开始日期）"))
    if not hasattr(issue, "issue_assignees") or not issue.issue_assignees.exists():
        score += 15
        reasons.append((15, "无负责人"))
    estimate = getattr(issue, "estimate_minutes", 0) or 0
    if estimate > 8 * 60 * 5:
        score += 10
        reasons.append((10, "预估工时超大（>1 人周）"))
    updated_days = (_tz.now() - issue.updated_at).days if issue.updated_at else 0
    if updated_days >= 14 and issue.completed_at is None:
        score += 20
        reasons.append((20, f"长期无更新（{updated_days} 天）"))
    reasons.sort(key=lambda r: -r[0])
    return {
        "score": min(score, 100),
        "top_reasons": [r[1] for r in reasons[:3]],
        "confidence": 0.6,
        "model_version": "rule_v0",
    }
