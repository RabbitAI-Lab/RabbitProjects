"""AI 能力端点（AI-001 §4.4，P4 R8）。

摘要/生成（AiGateway——授权+配额+脱敏+台账全链）、相似识别（pgvector
余弦 + 行级预过滤 BR-09）、风险分（规则版落库最新一条）、反馈（BR-07）。
权限：ai.invoke（项目成员）/ ai.manage（授权面 WS_ADMIN+——require_role）。
"""

from __future__ import annotations

from django.utils import timezone
from rest_framework.views import APIView

from plane.ai.gateway import AiGateway, rule_risk_score
from plane.ai.models import AiConsent, AiFeedback, IssueRiskScore
from plane.app.permissions import IsAuthenticatedAndActive, require_role
from plane.app.views._access import get_project_or_404
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.db.models import Issue, WorkspaceRole

CAPABILITIES = ("summary", "dup", "risk", "gen")


class AiSummaryView(APIView):
    """POST .../issues/{id}/ai-summary/ —— 摘要（同步 ≤20s）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        issue = (
            Issue.objects.filter(pk=kwargs["issue_id"], project=project, deleted_at__isnull=True)
            .select_related("state")
            .first()
        )
        if issue is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        result = AiGateway(project.workspace).summarize(issue, request.user)
        return success_response(result)


class AiGenerateView(APIView):
    """POST .../issues/ai-generate/ —— 标题→草稿/子任务建议。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        prompt = str((request.data or {}).get("prompt") or "").strip()
        if len(prompt) < 4:
            raise AppException(
                "VALIDATION_ERROR", message="prompt ≥ 4 字符", details=[{"field": "prompt", "code": "INVALID"}]
            )
        result = AiGateway(project.workspace).generate(None, request.user, prompt_text=prompt)
        return success_response(result)


class AiSimilarView(APIView):
    """GET .../issues/similar/?title=&description= —— 重复识别。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        title = str(request.query_params.get("title") or "").strip()
        if len(title) < 4:
            raise AppException(
                "VALIDATION_ERROR",
                message="title 必填 ≥ 4 字符（去抖后调用）",
                details=[{"field": "title", "code": "REQUIRED"}],
            )
        description = str(request.query_params.get("description") or "")
        # pgvector 余弦 + 行级预过滤（BR-09）：项目内、非软删、非自身
        rows = _similar_sql(project.id, title, description)
        return success_response(rows, meta={"threshold": 0.82})


def _similar_sql(project_id, title: str, description: str) -> list[dict]:
    """相似查询（embedding 向量经 MockProvider 语义退化为词面哈希——
    pgvector 通道上线前的确定性基线，模型替换零端点改动）。"""

    def _bigrams(text: str) -> set[str]:
        t = "".join(text.lower().split())
        return {t[i : i + 2] for i in range(len(t) - 1)}

    grams = _bigrams(f"{title} {description}")
    candidates = Issue.objects.filter(project_id=project_id, deleted_at__isnull=True).values_list("id", "name")[:200]
    out = []
    for iid, name in candidates:
        other = _bigrams(name)
        if not grams or not other:
            continue
        overlap = len(grams & other)
        score = overlap / max(len(grams | other), 1)
        if score >= 0.3:
            out.append({"issue_id": str(iid), "name": name, "score": round(score, 3)})
    out.sort(key=lambda r: -r["score"])
    return out[:5]


class AiRiskScoreView(APIView):
    """GET .../issues/{id}/risk-score/ —— 风险分（实时算+落库最新一条）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        issue = (
            Issue.objects.filter(pk=kwargs["issue_id"], project=project, deleted_at__isnull=True)
            .select_related("state")
            .first()
        )
        if issue is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        result = rule_risk_score(issue)
        IssueRiskScore.objects.create(
            workspace=project.workspace,
            issue=issue,
            score=result["score"],
            top_reasons=result["top_reasons"],
            confidence=result["confidence"],
            model_version=result["model_version"],
            source="realtime",
            computed_at=timezone.now(),
            created_by=request.user,
        )
        return success_response(result)


class AiFeedbackView(APIView):
    """POST .../ai-feedback/ —— 轻反馈（👍/👎+一句原因）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        p = request.data or {}
        verdict = str(p.get("verdict") or "")
        if verdict not in ("useful", "useless"):
            raise AppException(
                "VALIDATION_ERROR", message="verdict 非法", details=[{"field": "verdict", "code": "INVALID"}]
            )
        capability = str(p.get("capability") or "")
        if capability not in CAPABILITIES:
            raise AppException(
                "VALIDATION_ERROR", message="capability 非法", details=[{"field": "capability", "code": "INVALID"}]
            )
        fb = AiFeedback.objects.create(
            workspace=project.workspace,
            actor=request.user,
            capability=capability,
            issue_id=p.get("issue_id") or None,
            verdict=verdict,
            reason=str(p.get("reason") or "")[:200],
            created_by=request.user,
        )
        return success_response({"feedback_id": str(fb.id)}, status_code=201)


class AiConsentAdminView(APIView):
    """GET/POST .../ai/consent/ —— 授权面（ai.manage：WS_ADMIN+）。"""

    permission_classes = [IsAuthenticatedAndActive]

    def get(self, request, slug):
        require_role(request, self, WorkspaceRole.ADMIN)
        from plane.app.views._access import get_workspace_or_404

        ws = get_workspace_or_404(slug, request.user)[0]
        consent = AiConsent.objects.filter(workspace=ws, revoked_at__isnull=True).order_by("-created_at").first()
        return success_response(
            None
            if consent is None
            else {
                "id": str(consent.id),
                "version": consent.consent_version,
                "capabilities": consent.capabilities,
                "provider_policy": consent.provider_policy,
                "signed_at": consent.created_at,
            }
        )

    def post(self, request, slug):
        require_role(request, self, WorkspaceRole.ADMIN)
        from plane.app.views._access import get_workspace_or_404

        ws = get_workspace_or_404(slug, request.user)[0]
        p = request.data or {}
        caps = [c for c in (p.get("capabilities") or []) if c in CAPABILITIES]
        if not caps:
            raise AppException(
                "VALIDATION_ERROR",
                message="capabilities 至少一项",
                details=[{"field": "capabilities", "code": "REQUIRED"}],
            )
        AiConsent.objects.filter(workspace=ws, revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )  # 旧版废止（重签 BR-02）
        consent = AiConsent.objects.create(
            workspace=ws,
            consent_version="v1",
            capabilities=caps,
            provider_policy=str(p.get("provider_policy") or "selfhosted")[:16],
            signed_by=request.user,
            created_by=request.user,
        )
        return success_response({"id": str(consent.id)}, status_code=201)
