"""跨组织经营分析测试（RPT-006，P4 R7）。"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from plane.db.models import Issue, Project, SystemAdmin, User, Workspace

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    admin = User.objects.create_user(email="xg-admin@rabbit.dev", password="Rabbit123!", display_name="集团管理员")
    SystemAdmin.objects.create(user=admin, is_active=True, created_by=admin)
    plain = User.objects.create_user(email="xg-plain@rabbit.dev", password="Rabbit123!", display_name="普通")
    wss = []
    for i in range(2):
        owner = User.objects.create_user(email=f"xg-o{i}@rabbit.dev", password="Rabbit123!")
        ws = Workspace.objects.create(name=f"ORG{i}", slug=f"w-xg{i}-{owner.id.hex[:6]}", owner=owner, created_by=owner)
        proj = Project.objects.create(workspace=ws, name="P", identifier=f"XG{i}", created_by=owner)
        for s in range(i + 2):
            Issue.objects.create(project=proj, name=f"t{s}", sequence_id=s + 1, created_by=owner)
        wss.append(ws)
    return {"admin": admin, "plain": plain, "wss": wss}


def _c(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_ut01_summary(env):
    ids = ",".join(str(w.id) for w in env["wss"])
    r = _c(env["admin"]).get(f"/api/v1/instances/cross-org/summary/?ids={ids}")
    assert r.status_code == 200
    rows = r.json()["data"]
    assert len(rows) >= 2
    org0 = next(x for x in rows if x["slug"].startswith("w-xg0"))
    org1 = next(x for x in rows if x["slug"].startswith("w-xg1"))
    assert org0["issues_total"] == 2 and org1["issues_total"] == 3


def test_ut02_forbidden(env):
    r = _c(env["plain"]).get("/api/v1/instances/cross-org/summary/")
    assert r.status_code == 403


def test_ut03_compare(env):
    ids = ",".join(str(w.id) for w in env["wss"])
    r = _c(env["admin"]).get(f"/api/v1/instances/cross-org/compare/?ids={ids}&metric=issues_total")
    assert r.status_code == 200
    rows = r.json()["data"]
    assert len(rows) == 2
    assert rows[0]["value"] >= rows[1]["value"]  # 降序
    r_bad = _c(env["admin"]).get(f"/api/v1/instances/cross-org/compare/?ids={ids}&metric=bogus")
    assert r_bad.status_code == 400
