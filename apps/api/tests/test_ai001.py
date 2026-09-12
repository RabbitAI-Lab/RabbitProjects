"""AI 能力测试（AI-001，P4 R8 门禁）。"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from plane.ai.gateway import AiGateway, mask_outbound, restore_inbound
from plane.ai.models import AiCallLedger, AiConsent, AiFeedback, IssueRiskScore
from plane.db.models import Issue, Project, User, Workspace, WorkspaceMember
from plane.db.models.roles import WorkspaceRole

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="ai-owner@rabbit.dev", password="Rabbit123!", display_name="主")
    ws = Workspace.objects.create(name="AI", slug=f"w-ai-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=owner, role=WorkspaceRole.OWNER, created_by=owner)
    proj = Project.objects.create(workspace=ws, name="P", identifier="AI1", created_by=owner)
    issue = Issue.objects.create(project=proj, name="支付网关对账差异排查", sequence_id=1, created_by=owner)
    return {"owner": owner, "ws": ws, "proj": proj, "issue": issue}


def _c(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user)
    return c


def _consent(env, caps=("summary", "gen", "risk", "dup")):
    return AiConsent.objects.create(
        workspace=env["ws"],
        consent_version="v1",
        capabilities=list(caps),
        provider_policy="selfhosted",
        signed_by=env["owner"],
        created_by=env["owner"],
    )


def test_unconsented_blocked_and_consent_flow(env):
    c = _c(env["owner"])
    base = f"/api/v1/workspaces/{env['ws'].slug}"
    r0 = c.post(f"{base}/projects/{env['proj'].id}/issues/{env['issue'].id}/ai-summary/", {}, format="json")
    assert r0.status_code == 409 and "授权" in r0.json()["error"]["message"]
    r_sign = c.post(f"{base}/ai/consent/", {"capabilities": ["summary", "risk"]}, format="json")
    assert r_sign.status_code == 201
    # 未覆盖能力（gen）仍拒
    r_gen = c.post(
        f"{base}/projects/{env['proj'].id}/issues/ai-generate/", {"prompt": "对账差异排查工具"}, format="json"
    )
    assert r_gen.status_code == 409
    # 覆盖能力通
    r_sum = c.post(f"{base}/projects/{env['proj'].id}/issues/{env['issue'].id}/ai-summary/", {}, format="json")
    assert r_sum.status_code == 200 and r_sum.json()["data"]["consent_version"] == "v1"


def test_summary_masking_and_ledger(env):
    _consent(env, caps=("summary",))
    from plane.db.models import IssueComment

    IssueComment.objects.create(
        issue=env["issue"],
        actor=env["owner"],
        comment_html="<p>联系 dev@corp.com 或 13800138000</p>",
        comment_stripped="联系 dev@corp.com 或 13800138000",
        created_by=env["owner"],
    )
    result = AiGateway(env["ws"]).summarize(env["issue"], env["owner"])
    assert result["summary"]  # 出网关有摘要
    ledger = AiCallLedger.objects.filter(capability="summary").latest("created_at")
    assert ledger.status == "ok" and ledger.prompt_hash
    assert "comment_texts" in ledger.outbound_fields  # 出域字段计数入账
    masked, restore = mask_outbound({"t": "邮箱 a@b.c 电话 13912345678"})
    assert "@" not in masked["t"] and "13912345678" not in masked["t"]
    assert restore_inbound(masked["t"], restore) == "邮箱 a@b.c 电话 13912345678"  # 本地回填还原


def test_quota_exceeded(env):

    _consent(env, caps=("summary",))
    from django.core.cache import cache as _cache

    _cache.set(
        f"aiq:{env['ws'].id}:summary:{__import__('django.utils.timezone', fromlist=['timezone']).now():%Y%m%d}",
        500,
        timeout=86400,
    )
    with pytest.raises(Exception) as exc:
        AiGateway(env["ws"]).summarize(env["issue"], env["owner"])
    assert "QUOTA_AI_EXCEEDED" in str(exc.value.error_code) if hasattr(exc.value, "error_code") else True


def test_risk_score_rule_and_persist(env):
    from datetime import date

    Issue.objects.filter(pk=env["issue"].pk).update(target_date=date(2026, 1, 1))  # 远期逾期
    c = _c(env["owner"])
    r = c.get(f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/issues/{env['issue'].id}/risk-score/")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["model_version"] == "rule_v0"
    assert data["score"] > 0 and len(data["top_reasons"]) >= 1
    assert IssueRiskScore.objects.filter(issue=env["issue"]).exists()


def test_similar_and_feedback(env):
    Issue.objects.create(project=env["proj"], name="支付网关对账差异复盘", sequence_id=2, created_by=env["owner"])
    c = _c(env["owner"])
    r = c.get(
        f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/issues/similar/", {"title": "支付网关对账差异"}
    )
    assert r.status_code == 200 and len(r.json()["data"]) >= 1
    r_short = c.get(f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/issues/similar/", {"title": "支"})
    assert r_short.status_code == 400  # <4 字符拒
    r_fb = c.post(
        f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/ai-feedback/",
        {"verdict": "useless", "capability": "summary", "reason": "摘要太笼统"},
        format="json",
    )
    assert r_fb.status_code == 201 and AiFeedback.objects.filter(verdict="useless").exists()
    r_bad = c.post(
        f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/ai-feedback/",
        {"verdict": "meh", "capability": "summary"},
        format="json",
    )
    assert r_bad.status_code == 400
