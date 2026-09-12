"""私有化 License 与高可用部署件测试（INFRA-006，P4 R9）。"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from plane.db.models import User, Workspace, WorkspaceMember
from plane.db.models.roles import WorkspaceRole
from plane.license import GRACE_READWRITE, LicenseStatus, _verify

pytestmark = pytest.mark.django_db


def _write_license(tmp_path, *, expires, hostname="erp.customer.cn", seats=200, sig_tail=None):
    payload = {"hostname": hostname, "seats": seats, "expires_at": expires.isoformat()}
    payload_b64 = base64.b64encode(json.dumps(payload).encode()).decode()
    digest = hashlib.sha256(payload_b64.encode()).hexdigest()
    sig = sig_tail or digest  # 无公钥部署：尾 16 位一致性校验
    path = tmp_path / "license.json"
    path.write_text(json.dumps({"payload": payload_b64, "signature": sig}))
    return str(path), payload


def test_missing_license_full_feature(tmp_path):
    r = _verify(str(tmp_path / "none.json"), None)
    assert r["state"] == LicenseStatus.MISSING and not r["readonly"]


def test_valid_license_ok(tmp_path):
    path, payload = _write_license(tmp_path, expires=dt.date.today() + dt.timedelta(days=30))
    r = _verify(path, "erp.customer.cn")
    assert r["state"] == LicenseStatus.OK and r["seats"] == 200
    assert not r["readonly"]


def test_grace_then_expired(tmp_path):
    path, _ = _write_license(tmp_path, expires=dt.date.today() - dt.timedelta(days=2))
    r = _verify(path, None)
    assert r["state"] == LicenseStatus.GRACE and not r["readonly"]  # 宽限可写
    path2, _ = _write_license(tmp_path, expires=dt.date.today() - dt.timedelta(days=GRACE_READWRITE // 86400 + 1))
    r2 = _verify(path2, None)
    assert r2["state"] == LicenseStatus.EXPIRED and r2["readonly"]


def test_invalid_structure_and_hostname(tmp_path):
    # 坏 JSON 结构 → INVALID（RSA 签名面随交付包公钥激活，本形态验结构+域）
    bad = tmp_path / "bad.json"
    bad.write_text("{not-json")
    r = _verify(str(bad), None)
    assert r["state"] == LicenseStatus.INVALID and r["readonly"]
    path2, _ = _write_license(tmp_path, expires=dt.date.today() + dt.timedelta(days=30))
    r2 = _verify(path2, "other.host.cn")  # 域不符
    assert r2["state"] == LicenseStatus.INVALID


def test_readonly_middleware_blocks_writes(env_like, tmp_path, monkeypatch):
    path, _ = _write_license(tmp_path, expires=dt.date.today() - dt.timedelta(days=GRACE_READWRITE // 86400 + 1))
    monkeypatch.setenv("LICENSE_FILE", path)
    cache.delete("license:status")
    c = APIClient()
    c.force_authenticate(env_like["owner"])
    # 读放行
    r_get = c.get(f"/api/v1/workspaces/{env_like['ws'].slug}/projects/")
    assert r_get.status_code == 200
    # 写拒 409（BR-06 只读）
    r_post = c.post(
        f"/api/v1/workspaces/{env_like['ws'].slug}/projects/", {"name": "新项目", "identifier": "RO1"}, format="json"
    )
    assert r_post.status_code == 409
    body = r_post.json()["error"]
    assert body["code"] == "LICENSE_EXPIRED"
    assert "只读" in body["message"] and "导出" in body["message"]


@pytest.fixture()
def env_like(db):
    owner = User.objects.create_user(email="lc-owner@rabbit.dev", password="Rabbit123!", display_name="主")
    ws = Workspace.objects.create(name="LC", slug=f"w-lc-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=owner, role=WorkspaceRole.OWNER, created_by=owner)
    return {"owner": owner, "ws": ws}
