"""权限快照接口回归（AUTH-005 §4.2 + known-debt #2 批量化收口，P4 R1）。

/users/me/permissions/ 原逐项目调用 custom_codes 成 N+1（实测 206 查询
@ 2511 项目）；custom_codes_bulk 一次 IN 预取后查询数与项目数无关——
本文件以 django_assert_numQueries 锁定常数基线（含会话/用户装载）。
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from plane.app.effective_perms import custom_codes_bulk
from plane.db.models import (
    CustomRole,
    Project,
    ProjectMember,
    ProjectRoleAssignment,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.roles import WorkspaceRole

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="perm-owner@rabbit.dev", password="Rabbit123!", display_name="主")
    ws = Workspace.objects.create(name="P", slug=f"w-perm-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=owner, role=WorkspaceRole.OWNER, created_by=owner)
    projects = [
        Project.objects.create(workspace=ws, name=f"P{i}", identifier=f"PM{i}", created_by=owner) for i in range(3)
    ]
    role = CustomRole.objects.create(
        project=projects[0], name="只读加强", permissions=["issue.view.list"], created_by=owner
    )
    ProjectRoleAssignment.objects.create(project=projects[0], role=role, user=owner, created_by=owner)
    ProjectMember.objects.create(project=projects[2], workspace=ws, member=owner, role=10, created_by=owner)
    return {"owner": owner, "ws": ws, "projects": projects, "role": role}


def test_snapshot_constant_queries(env, django_assert_num_queries):
    """三项目（含挂接/显式/隐式各一）→ 快照查询数恒定（force_authenticate
    免会话装载，恰为快照五查询：①WS 成员 ②SystemAdmin ③显式项目行
    ④候选项目扫描 ⑤挂接码集批量预取）。"""
    c = APIClient()
    c.force_authenticate(env["owner"])
    with django_assert_num_queries(5):
        r = c.get("/api/v1/users/me/permissions/")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["is_system_admin"] is False
    pids = {str(p.id) for p in env["projects"]}
    assert pids <= set(data["projects"])
    first = data["projects"][str(env["projects"][0].id)]
    assert first["custom_codes"] == ["issue.view.list"]  # 挂接并集下发


def test_bulk_matches_singular(env):
    """批量版与逐项目版语义一致（挂接并集口径）。"""
    ids = [p.id for p in env["projects"]]
    bulk = custom_codes_bulk(env["owner"].id, ids)
    from plane.app.effective_perms import custom_codes

    for pid in ids:
        assert bulk[str(pid)] == custom_codes(env["owner"].id, pid)
    assert bulk[str(ids[0])] == {"issue.view.list"}
    assert bulk[str(ids[1])] == frozenset()
