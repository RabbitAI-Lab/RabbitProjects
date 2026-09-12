"""文件合规测试（FILE-006，P4 R6 门禁）。

覆盖 §5 核心：四级继承解析（asset 覆盖 project、恢复继承）、下载/分享
闸门（BR-03/04）、LegalHold 双人放置/解除（BR-07/UT-09）、DLP 规则
配额/正则验证/回溯拒（UT-11）、留存 sweeper 与 Hold 豁免对冲（BR-06）、
水印合成（平铺不可去语汇 + 失败不阻断）。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from plane.db.models import (
    CompliancePolicy,
    DlpRule,
    FileAsset,
    Issue,
    LegalHold,
    Project,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.roles import WorkspaceRole
from plane.db.services.file_compliance import (
    retention_sweep,
    resolve_policy,
    scan_text,
    validate_dlp_pattern,
    watermark_text,
)

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="fc-owner@rabbit.dev", password="Rabbit123!", display_name="主")
    confirmer = User.objects.create_user(email="fc-conf@rabbit.dev", password="Rabbit123!", display_name="确认人")
    ws = Workspace.objects.create(name="FC", slug=f"w-fc-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=owner, role=WorkspaceRole.OWNER, created_by=owner)
    proj = Project.objects.create(workspace=ws, name="P", identifier="FC1", created_by=owner)
    import uuid as _uuid

    asset = FileAsset.objects.create(
        workspace=ws,
        project=proj,
        entity_type="issue",
        entity_id=_uuid.uuid4(),
        size=1024,
        storage_path="fc/a1",
        created_by=owner,
    )
    return {"owner": owner, "confirmer": confirmer, "ws": ws, "proj": proj, "asset": asset}


def _c(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user)
    return c


def _base(env):
    return f"/api/v1/workspaces/{env['ws'].slug}/file-compliance"


# ── 四级策略 ───────────────────────────────────────────────────────


def test_policy_inheritance_and_override(env):
    c = _c(env["owner"])
    base = _base(env)
    # project 级：禁下载
    r = c.patch(f"{base}/policy/", {"project_id": str(env["proj"].id), "download": "deny"}, format="json")
    assert r.status_code == 200 and r.json()["data"]["download"] == "deny"
    assert resolve_policy(env["asset"])["download"] == "deny"  # 继承生效
    # asset 级覆盖：允许（最细覆盖）
    c.patch(f"{base}/policy/", {"asset_id": str(env["asset"].id), "download": "allow"}, format="json")
    assert resolve_policy(env["asset"])["download"] == "allow"
    # 恢复继承（清空覆盖）
    c.patch(f"{base}/policy/", {"asset_id": str(env["asset"].id), "download": None}, format="json")
    assert resolve_policy(env["asset"])["download"] == "deny"  # 回落项目级
    # 一域一策：同域二策 409 UNIQUE 预检层在唯一约束
    with pytest.raises(Exception):
        CompliancePolicy.objects.create(project=env["proj"], download="allow", created_by=env["owner"])


def test_policy_version_optimistic_lock(env):
    c = _c(env["owner"])
    base = _base(env)
    r1 = c.patch(f"{base}/policy/", {"watermark": "preview_only"}, format="json")
    v = r1.json()["data"]["version"]
    r2 = c.patch(f"{base}/policy/", {"watermark": "off", "version": v}, format="json")
    assert r2.status_code == 200
    r3 = c.patch(f"{base}/policy/", {"watermark": "off", "version": v}, format="json")
    assert r3.status_code == 409 and r3.json()["error"]["code"] == "RESOURCE_STATE_INVALID"


def test_download_and_share_gates(env):
    CompliancePolicy.objects.create(asset=env["asset"], download="deny", share_link="deny", created_by=env["owner"])
    from plane.db.services.file_compliance import assert_download_allowed, assert_share_allowed
    from plane.base.exception import AppException

    with pytest.raises(AppException) as exc:
        assert_download_allowed(env["asset"])  # BR-03
    assert exc.value.error_code == "PERM_DENIED"
    with pytest.raises(AppException):
        assert_share_allowed(env["asset"])  # BR-04


# ── LegalHold ──────────────────────────────────────────────────────


def test_legal_hold_dual_confirmation(env):
    c = _c(env["owner"])
    base = _base(env)
    r_self = c.post(
        f"{base}/legal-holds/",
        {"asset_id": str(env["asset"].id), "reason": "诉讼保全", "confirm_user_id": str(env["owner"].id)},
        format="json",
    )
    assert r_self.status_code == 403  # 同人确认拒
    r = c.post(
        f"{base}/legal-holds/",
        {"asset_id": str(env["asset"].id), "reason": "诉讼保全", "confirm_user_id": str(env["confirmer"].id)},
        format="json",
    )
    assert r.status_code == 201
    hold = LegalHold.objects.get(pk=r.json()["data"]["id"])
    # 解除：同人拒 → 异人过
    r_bad = c.delete(f"{base}/legal-holds/{hold.id}/", {"confirm_user_id": str(env["owner"].id)}, format="json")
    assert r_bad.status_code == 403
    r_ok = c.delete(f"{base}/legal-holds/{hold.id}/", {"confirm_user_id": str(env["confirmer"].id)}, format="json")
    assert r_ok.status_code == 200
    hold.refresh_from_db()
    assert hold.released_at is not None
    assert hold.released_confirmed_by == env["confirmer"]


# ── DLP ────────────────────────────────────────────────────────────


def test_dlp_rules_quota_and_pattern(env):
    c = _c(env["owner"])
    base = _base(env)
    r_bad = c.post(f"{base}/dlp-rules/", {"name": "嵌套回溯", "pattern": "(a+)+$"}, format="json")
    assert r_bad.status_code == 400  # 回溯面拒
    r_ok = c.post(f"{base}/dlp-rules/", {"name": "手机号", "pattern": r"1[3-9]\d{9}"}, format="json")
    assert r_ok.status_code == 201
    with pytest.raises(ValueError):
        validate_dlp_pattern("[unclosed")
    assert scan_text("联系 13800138000", DlpRule.objects.all()) == ["手机号"]
    assert scan_text("无敏感", DlpRule.objects.all()) == []
    # 配额 20
    DlpRule.objects.all().delete()
    for i in range(20):
        DlpRule.objects.create(workspace=env["ws"], name=f"R{i}", pattern=r"\d{4}", created_by=env["owner"])
    r_over = c.post(f"{base}/dlp-rules/", {"name": "R21", "pattern": "x"}, format="json")
    assert r_over.status_code == 409  # UT-11


# ── 留存 sweeper ───────────────────────────────────────────────────


def test_retention_sweep_and_hold_exemption(env):
    import uuid as _uuid

    # created_at 为 auto_now_add——创建后 queryset 直改（绕 auto_now_add）
    old = FileAsset.objects.create(
        workspace=env["ws"],
        project=env["proj"],
        entity_type="issue",
        entity_id=_uuid.uuid4(),
        size=10,
        storage_path="fc/old",
        created_by=env["owner"],
    )
    held = FileAsset.objects.create(
        workspace=env["ws"],
        project=env["proj"],
        entity_type="issue",
        entity_id=_uuid.uuid4(),
        size=10,
        storage_path="fc/held",
        created_by=env["owner"],
    )
    _stale = timezone.now() - timedelta(days=100)
    FileAsset.objects.filter(pk__in=[old.id, held.id]).update(created_at=_stale)
    CompliancePolicy.objects.create(workspace=env["ws"], retention_days=30, created_by=env["owner"])
    LegalHold.objects.create(
        asset=held, reason="保全", placed_by=env["owner"], confirmed_by=env["confirmer"], created_by=env["owner"]
    )
    result = retention_sweep()
    old.refresh_from_db()
    held.refresh_from_db()
    assert old.deleted_at is not None  # 过期软删
    assert held.deleted_at is None  # Hold 豁免（BR-06）
    assert result["soft_deleted"] >= 1


# ── 水印 ───────────────────────────────────────────────────────────


def test_watermark_text_and_render(env):
    text = watermark_text("张三", "z@corp.com")
    assert "张三" in text and "z@corp.com" in text  # BR-02 语汇
    from plane.db.services.file_compliance import render_watermarked_png

    png = open("/tmp/tiny.png", "rb").read()
    out = render_watermarked_png(png, text)
    assert out is not None and out[:4] == b"\x89PNG"  # 平铺合成成功
    assert render_watermarked_png(b"not-image", text) is None  # 失败不阻断
