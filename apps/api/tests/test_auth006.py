"""AUTH-006 行级隔离体系化测试（T5-03）。

覆盖：矩阵可见性（含 BR-11 draft 行、公开项目当前态=私有）、accessible_by
通道与未注册族报错、unsafe_all 守卫、WS/项目批量角色（§2.3 分态语义 +
BR-14 上限 + BR-15 降级联动）、账号启停（§2.4/§4.5——自我禁用 400 /
末位 OWNER 409 / 幂等 / 真会话吊销后 401）、lint_access 守护自检。

公开项目对 WS_ONLY 可见通道 = 架构待回改（§2.1 注 ①）——本迭代对公开项目
行按当前态断言（视同私有），IT-02 公开行解锁归回改任务。
"""
from __future__ import annotations

import pytest
from django.contrib.auth import authenticate
from rest_framework.test import APIClient

from plane.db.models import (
    Issue,
    Project,
    ProjectMember,
    ProjectRole,
    SystemAdmin,
    User,
    WebhookEndpoint,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.roles import WorkspaceRole
from plane.db.seeds.project_states import seed_project_states

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="a6-owner@rabbit.dev", password="Rabbit123!",
                                     display_name="张三")
    admin = User.objects.create_user(email="a6-admin@rabbit.dev", password="Rabbit123!",
                                     display_name="李管理")
    member = User.objects.create_user(email="a6-member@rabbit.dev", password="Rabbit123!",
                                      display_name="王成员")
    wsonly = User.objects.create_user(email="a6-wsonly@rabbit.dev", password="Rabbit123!",
                                      display_name="赵仅空间")
    outsider = User.objects.create_user(email="a6-out@rabbit.dev", password="Rabbit123!",
                                        display_name="钱外人")
    ws = Workspace.objects.create(name="W", slug=f"w-a6-{owner.id.hex[:8]}",
                                  owner=owner, created_by=owner)
    for u, r in ((owner, WorkspaceRole.OWNER), (admin, WorkspaceRole.ADMIN),
                 (member, WorkspaceRole.MEMBER), (wsonly, WorkspaceRole.MEMBER)):
        WorkspaceMember.objects.create(workspace=ws, member=u, role=r, created_by=owner)
    proj = Project.objects.create(name="P", identifier="A06", workspace=ws, created_by=owner)
    ProjectMember.objects.create(project=proj, member=member, role=ProjectRole.CONTRIBUTOR,
                                 created_by=owner)
    ProjectMember.objects.create(project=proj, member=admin, role=ProjectRole.ADMIN,
                                 created_by=owner)
    seed_project_states(proj)
    todo = None
    from plane.db.models import State
    todo = State.objects.get(project=proj, group=State.Group.UNSTARTED)
    issue = Issue.objects.create(name="任务甲", project=proj, state=todo, priority="none",
                                 sequence_id=1, sort_order=100, created_by=owner)
    return {"owner": owner, "admin": admin, "member": member, "wsonly": wsonly,
            "outsider": outsider, "ws": ws, "proj": proj, "issue": issue}


def _client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user=user)
    return c


# ────────────────────────────────────────────────────────────────
# 矩阵与 accessible_by 通道（§2.1/§4.2）
# ────────────────────────────────────────────────────────────────
def test_matrix_visibility_branches(env):
    from plane.access.matrix import visible_project_ids

    draft = Project.objects.create(name="draft-p", identifier="D06", workspace=env["ws"],
                                   status="draft", created_by=env["owner"])
    # draft 项目即便是显式成员也不可见（BR-11）——突变咬合点
    ProjectMember.objects.create(project=draft, member=env["member"],
                                 role=ProjectRole.CONTRIBUTOR, created_by=env["owner"])
    pub = Project.objects.create(name="pub-p", identifier="U06", workspace=env["ws"],
                                 created_by=env["member"], visibility="public")

    def ids(user):
        return {str(r["id"]) for r in visible_project_ids(user, env["ws"].id)}

    assert str(env["proj"].id) in ids(env["member"])       # 显式成员可见
    assert str(env["proj"].id) in ids(env["admin"])        # WS_ADMIN 隐式全权
    assert str(env["proj"].id) not in ids(env["wsonly"])   # 非项目成员不可见
    # draft 行（BR-11）：创建者可见、WS_ADMIN 可见、显式成员不可见
    assert str(draft.id) in ids(env["owner"]) and str(draft.id) in ids(env["admin"])
    assert str(draft.id) not in ids(env["member"])
    # 公开项目当前态 = 私有（§2.1 注 ① 架构待回改；落地前行为一致）
    assert str(pub.id) not in ids(env["wsonly"])


def test_accessible_by_issue_family(env):
    qs = Issue.objects.accessible_by(env["member"], workspace_id=env["ws"].id)
    assert list(qs) == [env["issue"]]
    assert not Issue.objects.accessible_by(env["wsonly"], workspace_id=env["ws"].id)


def test_accessible_by_unregistered_family(env):
    from plane.db.models import Notification

    with pytest.raises(LookupError, match="未注册进 plane.access.matrix"):
        Notification.objects.accessible_by(env["member"], workspace_id=env["ws"].id)


def test_accessible_by_requires_workspace_scope(env):
    with pytest.raises(ValueError, match="workspace_id"):
        Issue.objects.accessible_by(env["member"])


def test_unsafe_all_requires_reason(env):
    with pytest.raises(TypeError):
        Issue.objects.unsafe_all()  # type: ignore[misc]
    qs = Issue.objects.unsafe_all(reason="system_task")
    assert env["issue"] in list(qs)


def test_workspace_list_via_accessible_by(env):
    """AC-01 修复面回归：工作空间列表经 accessible_by（仅我所在）。"""
    from plane.access.matrix import workspace_q

    qs = Workspace.objects.filter(workspace_q(env["member"]))
    assert list(qs) == [env["ws"]]


# ────────────────────────────────────────────────────────────────
# 批量角色（§2.3 / §4.4.1）
# ────────────────────────────────────────────────────────────────
def _bulk_url(env):
    return f"/api/v1/workspaces/{env['ws'].slug}/members/bulk-role/"


def test_bulk_role_partial_semantics(env):
    import uuid as uuid_mod

    admin2 = User.objects.create_user(email="a6-admin2@rabbit.dev", password="Rabbit123!",
                                      display_name="孙管理")
    WorkspaceMember.objects.create(workspace=env["ws"], member=admin2,
                                   role=WorkspaceRole.ADMIN, created_by=env["owner"])
    WorkspaceMember.objects.filter(
        workspace=env["ws"], member=env["wsonly"]).update(role=WorkspaceRole.GUEST)
    ghost = str(uuid_mod.uuid4())
    res = _client(env["admin"]).post(_bulk_url(env), format="json", data={
        "user_ids": [str(env["member"].id),   # MEMBER→GUEST：updated
                     str(env["wsonly"].id),    # 已是 GUEST：already_has_role
                     ghost,                    # 不存在：not_workspace_member
                     str(admin2.id),           # ADMIN 同级：hierarchy failed
                     str(env["admin"].id)],    # 操作者本人：self_target
        "role": WorkspaceRole.GUEST,
    })
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["updated"] == 1
    reasons = {s["reason"] for s in data["skipped"]}
    assert reasons == {"already_has_role", "not_workspace_member", "self_target"}
    assert [f["reason"] for f in data["failed"]] == ["hierarchy"]


def test_bulk_role_owner_target_skipped_not_counted(env):
    res = _client(env["admin"]).post(_bulk_url(env), format="json", data={
        "user_ids": [str(env["owner"].id)], "role": WorkspaceRole.MEMBER})
    data = res.json()["data"]
    assert data["updated"] == 0 and data["skipped"] == []  # owner_implicit_full 不计数不列出行
    assert data["failed"] == []


def test_bulk_role_structural_400(env):
    c = _client(env["owner"])
    assert c.post(_bulk_url(env), format="json",
                  data={"user_ids": [], "role": 10}).status_code == 400
    assert c.post(_bulk_url(env), format="json",
                  data={"user_ids": [str(env["member"].id)], "role": 20}).status_code == 400
    ids = [str(env["member"].id)] * 101
    res = c.post(_bulk_url(env), format="json", data={"user_ids": ids, "role": 5})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_BULK_LIMIT_EXCEEDED"


def test_bulk_role_permission_403(env):
    res = _client(env["member"]).post(_bulk_url(env), format="json", data={
        "user_ids": [str(env["wsonly"].id)], "role": WorkspaceRole.GUEST})
    assert res.status_code == 403


def test_bulk_role_cascade_br15(env):
    """WS_MEMBER→WS_GUEST 降级联动：项目 CONTRIBUTOR+ 同事务降为 COMMENTER。"""
    assert ProjectMember.objects.get(
        project=env["proj"], member=env["member"]).role == ProjectRole.CONTRIBUTOR
    res = _client(env["owner"]).post(_bulk_url(env), format="json", data={
        "user_ids": [str(env["member"].id)], "role": WorkspaceRole.GUEST})
    assert res.status_code == 200 and res.json()["data"]["updated"] == 1
    assert ProjectMember.objects.get(
        project=env["proj"], member=env["member"]).role == ProjectRole.COMMENTER


def test_bulk_role_cascade_last_admin_protected(env):
    """末位 PROJ_ADMIN 保护优先于联动（rbac §7.2）。"""
    solo = User.objects.create_user(email="a6-solo@rabbit.dev", password="Rabbit123!",
                                    display_name="周唯一")
    WorkspaceMember.objects.create(workspace=env["ws"], member=solo,
                                   role=WorkspaceRole.MEMBER, created_by=env["owner"])
    p2 = Project.objects.create(name="P2", identifier="B06", workspace=env["ws"],
                                created_by=env["owner"])
    ProjectMember.objects.create(project=p2, member=solo, role=ProjectRole.ADMIN,
                                 created_by=env["owner"])
    _client(env["owner"]).post(_bulk_url(env), format="json", data={
        "user_ids": [str(solo.id)], "role": WorkspaceRole.GUEST})
    assert ProjectMember.objects.get(project=p2, member=solo).role == ProjectRole.ADMIN


def test_project_bulk_role(env):
    url = f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/members/bulk-role/"
    res = _client(env["owner"]).post(url, format="json", data={
        "member_ids": [str(env["member"].id)], "role": ProjectRole.VIEWER})
    assert res.status_code == 200 and res.json()["data"]["updated"] == 1
    assert ProjectMember.objects.get(
        project=env["proj"], member=env["member"]).role == ProjectRole.VIEWER
    # 末位 ADMIN 保护
    res = _client(env["owner"]).post(url, format="json", data={
        "member_ids": [str(env["admin"].id)], "role": ProjectRole.VIEWER})
    failed = res.json()["data"]["failed"]
    assert failed and failed[0]["reason"] == "last_owner_demotion"


# ────────────────────────────────────────────────────────────────
# 账号启停（§2.4/§4.5）
# ────────────────────────────────────────────────────────────────
def _member_row(env, user):
    return WorkspaceMember.objects.get(workspace=env["ws"], member=user)


def test_disable_self_400(env):
    row = _member_row(env, env["admin"])
    res = _client(env["admin"]).post(
        f"/api/v1/workspaces/{env['ws'].slug}/members/{row.id}/disable/", format="json")
    assert res.status_code == 400


def test_disable_last_owner_409(env):
    row = _member_row(env, env["owner"])
    res = _client(env["admin"]).post(
        f"/api/v1/workspaces/{env['ws'].slug}/members/{row.id}/disable/", format="json")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "RESOURCE_STATE_INVALID"


def test_disable_revokes_session_and_401(env):
    """真会话链路：登录 → 管理员禁用 → 原会话下一请求 401（BR-05 API 面 0 秒）。"""
    victim = User.objects.create_user(email="a6-victim@rabbit.dev", password="Rabbit123!",
                                      display_name="吴被禁")
    WorkspaceMember.objects.create(workspace=env["ws"], member=victim,
                                   role=WorkspaceRole.MEMBER, created_by=env["owner"])
    victim_client = APIClient(enforce_csrf_checks=False)
    assert victim_client.login(email="a6-victim@rabbit.dev", password="Rabbit123!")
    assert victim_client.get("/api/v1/users/me/", format="json").status_code == 200

    row = WorkspaceMember.objects.get(workspace=env["ws"], member=victim)
    res = _client(env["owner"]).post(
        f"/api/v1/workspaces/{env['ws'].slug}/members/{row.id}/disable/", format="json")
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["is_active"] is False and data["revoked"]["sessions"] >= 1
    # DRF SessionAuthentication 拒 inactive → 401；且登录通道关闭
    assert victim_client.get("/api/v1/users/me/", format="json").status_code == 401
    assert authenticate(email="a6-victim@rabbit.dev", password="Rabbit123!") is None
    # 幂等重放：revoked 全 0
    res2 = _client(env["owner"]).post(
        f"/api/v1/workspaces/{env['ws'].slug}/members/{row.id}/disable/", format="json")
    assert res2.status_code == 200 and res2.json()["data"]["revoked"]["sessions"] == 0


def test_disable_owner_forbidden_even_multi(env):
    second = User.objects.create_user(email="a6-own2@rabbit.dev", password="Rabbit123!",
                                      display_name="郑二主")
    WorkspaceMember.objects.create(workspace=env["ws"], member=second,
                                   role=WorkspaceRole.OWNER, created_by=env["owner"])
    row = _member_row(env, second)
    res = _client(env["owner"]).post(
        f"/api/v1/workspaces/{env['ws'].slug}/members/{row.id}/disable/", format="json")
    assert res.status_code == 403  # ⚠️ 不可管 Owner


def test_enable_restores(env):
    victim = User.objects.create_user(email="a6-v2@rabbit.dev", password="Rabbit123!",
                                     display_name="冯复用")
    WorkspaceMember.objects.create(workspace=env["ws"], member=victim,
                                   role=WorkspaceRole.MEMBER, created_by=env["owner"])
    row = WorkspaceMember.objects.get(workspace=env["ws"], member=victim)
    _client(env["owner"]).post(
        f"/api/v1/workspaces/{env['ws'].slug}/members/{row.id}/disable/", format="json")
    res = _client(env["owner"]).post(
        f"/api/v1/workspaces/{env['ws'].slug}/members/{row.id}/enable/", format="json")
    assert res.status_code == 200 and res.json()["data"]["is_active"] is True
    victim.refresh_from_db()
    assert victim.is_active and victim.disabled_at is None
    assert authenticate(email="a6-v2@rabbit.dev", password="Rabbit123!") is not None


# ────────────────────────────────────────────────────────────────
# CI 守护（§4.3）
# ────────────────────────────────────────────────────────────────
def test_lint_access_scanner():
    """守护三面：自检反例命中 / 全仓扫描绿 / 矩阵漂移探测红。"""
    import subprocess
    import sys

    repo = __file__.rsplit("/apps/api/", 1)[0]
    st = subprocess.run([sys.executable, f"{repo}/scripts/lint_access.py", "--selftest"],
                        capture_output=True, text=True)
    assert st.returncode == 0, st.stdout + st.stderr
    st = subprocess.run([sys.executable, f"{repo}/scripts/lint_access.py"],
                        capture_output=True, text=True)
    assert st.returncode == 0, st.stdout + st.stderr


# ── QA-001 §2.4/§5.2 IT-SEC-01~64：越权矩阵 64 格（四主体×四资源层×四动作）──
# 期望表 = 本仓库 rbac 真源（视图声明 + 矩阵单源 plane/access/matrix.py）；
# 突变验证见 test_it_sec_mutation（放开一处装饰器必须红）。
_SUBJECTS = ("sysadmin", "owner", "member", "guest")
_RESOURCES = ("workspace", "project", "issue", "webhook")
_ACTIONS = ("create", "read", "update", "delete")

#: 期望状态码（""→2xx 通配）：主体对资源动作的判定
_EXPECT = {
    # workspace：任何认证者可建自己的；读=成员可见；改=WS ADMIN+；删=无端点(405)
    ("workspace", "create"): {"sysadmin": 201, "owner": 201, "member": 201, "guest": 201},
    ("workspace", "read"):   {"sysadmin": 200, "owner": 200, "member": 200, "guest": 404},
    ("workspace", "update"): {"sysadmin": 403, "owner": 200, "member": 403, "guest": 404},
    ("workspace", "delete"): {"sysadmin": 405, "owner": 405, "member": 405, "guest": 405},
    # project：建=WS MEMBER+；读=显式/隐式成员（guest 404）；改=PROJ_ADMIN+；
    # 删=PROJ_ADMIN+（member 403、guest 404）
    ("project", "create"): {"sysadmin": 201, "owner": 201, "member": 201, "guest": 404},
    ("project", "read"):   {"sysadmin": 404, "owner": 200, "member": 200, "guest": 404},
    ("project", "update"): {"sysadmin": 404, "owner": 200, "member": 403, "guest": 404},
    ("project", "delete"): {"sysadmin": 404, "owner": 204, "member": 403, "guest": 404},
    # issue：建/改=CONTRIBUTOR+（viewer 级 403——member 是 CONTRIBUTOR 故过）；
    # 读=项目可见；删=PROJ_ADMIN+（member 403）
    ("issue", "create"): {"sysadmin": 404, "owner": 201, "member": 201, "guest": 404},
    ("issue", "read"):   {"sysadmin": 404, "owner": 200, "member": 200, "guest": 404},
    ("issue", "update"): {"sysadmin": 404, "owner": 200, "member": 200, "guest": 404},
    ("issue", "delete"): {"sysadmin": 404, "owner": 200, "member": 403, "guest": 404},
    # webhook（integration.config=PROJ_ADMIN+；D2 收口——CRUD 逐端点）：
    # sysadmin（WS MEMBER 无项目角色）与 member（CONTRIBUTOR）一律 403/404，
    # guest 无 ws 成员 → 404 同构
    ("webhook", "create"): {"sysadmin": 404, "owner": 201, "member": 403, "guest": 403},
    ("webhook", "read"):   {"sysadmin": 404, "owner": 200, "member": 403, "guest": 403},
    ("webhook", "update"): {"sysadmin": 404, "owner": 200, "member": 403, "guest": 403},
    ("webhook", "delete"): {"sysadmin": 404, "owner": 204, "member": 403, "guest": 403},
}


@pytest.fixture()
def matrix_env(env, db):
    """env（test_auth006 主体）+ sysadmin/guest 别名 + webhook 端点 + 目标 issue。"""
    from django.core.cache import cache

    cache.clear()  # report/bulk throttle 计数清零（矩阵 61 次内不受限）
    sysadmin = User.objects.create_user(email="a6-sys@rabbit.dev",
                                        password="Rabbit123!", display_name="系统管")
    SystemAdmin.objects.create(user=sysadmin)
    WorkspaceMember.objects.create(workspace=env["ws"], member=sysadmin,
                                   role=WorkspaceRole.MEMBER, created_by=env["owner"])
    from plane.db.models.integration import encrypt_secret
    hook = WebhookEndpoint.objects.create(
        project=env["proj"], workspace=env["ws"], url="https://hooks.example.com/mx",
        events=["issue.updated"],
        secret_encrypted=encrypt_secret("x" * 32),
        created_by=env["owner"])
    issue = Issue.objects.filter(project=env["proj"]).first()
    from plane.db.models import IssueType
    issue_type = IssueType.objects.filter(workspace=env["ws"]).first()
    if issue_type is None:  # a6 env 不走 signup 种子——按需补一条任务类型
        issue_type = IssueType.objects.create(
            workspace=env["ws"], name="任务",
            created_by=env["owner"])
    return {**env, "sysadmin": sysadmin, "guest": env["outsider"],
            "hook": hook, "issue": issue, "issue_type": issue_type}


def _hit(client, resource, action, e):
    """单格 HTTP 请求（幂等可重入——create 格用独立 slug/name 防撞）。"""
    import uuid as u
    ws = e["ws"].slug
    pid, iid, hid = e["proj"].id, e["issue"].id, e["hook"].id
    if resource == "workspace":
        if action == "create":
            return client.post("/api/v1/workspaces/",
                               {"name": f"W{u.uuid4().hex[:6]}",
                                "identifier": f"W{u.uuid4().hex[:4].upper()}"}, format="json")
        if action == "read":
            return client.get(f"/api/v1/workspaces/{ws}/")
        if action == "update":
            return client.patch(f"/api/v1/workspaces/{ws}/",
                                {"name": e["ws"].name}, format="json")
        return client.delete(f"/api/v1/workspaces/{ws}/")
    if resource == "project":
        base = f"/api/v1/workspaces/{ws}/projects/"
        if action == "create":
            return client.post(base, {"name": f"P{u.uuid4().hex[:6]}",
                                      "identifier": f"X{u.uuid4().hex[:4].upper()}"},
                               format="json")
        if action == "read":
            return client.get(f"{base}{pid}/")
        if action == "update":
            return client.patch(f"{base}{pid}/", {"name": e["proj"].name}, format="json")
        return client.delete(f"{base}{pid}/")
    if resource == "issue":
        base = f"/api/v1/workspaces/{ws}/projects/{pid}/issues/"
        if action == "create":
            return client.post(base, {"name": f"I{u.uuid4().hex[:6]}",
                                      "priority": "none",
                                      "type_id": str(e["issue_type"].id)}, format="json")
        if action == "read":
            return client.get(f"{base}{iid}/")
        if action == "update":
            return client.patch(f"{base}{iid}/", {"name": e["issue"].name,
                                                  "type_id": str(e["issue_type"].id)},
                                format="json")
        return client.delete(f"{base}{iid}/")
    # webhook CRUD（integration.config；D2 收口逐端点）
    base = f"/api/v1/workspaces/{ws}/projects/{pid}/webhooks/"
    if action == "create":
        return client.post(base, {"url": f"https://h.example.com/{u.uuid4().hex[:6]}",
                                  "events": ["issue.updated"]}, format="json")
    if action == "read":
        return client.get(base)
    if action == "update":
        return client.patch(f"{base}{hid}/", {"is_active": "active"}, format="json")
    return client.delete(f"{base}{hid}/")


@pytest.mark.django_db
@pytest.mark.parametrize("subject", _SUBJECTS)
@pytest.mark.parametrize("resource", _RESOURCES)
@pytest.mark.parametrize("action", _ACTIONS)
def test_it_sec_matrix_64(subject, resource, action, matrix_env):
    """IT-SEC-{subject×resource×action 序号 01~64}：越权矩阵逐格断言。"""
    from django.core.cache import cache

    user = matrix_env[subject]
    c = APIClient()
    c.force_authenticate(user)
    cache.clear()
    r = _hit(c, resource, action, matrix_env)
    want = _EXPECT[(resource, action)][subject]
    ok = (200 <= r.status_code < 300) if want == "" else r.status_code == want
    assert ok, (f"IT-SEC[{subject}/{resource}/{action}] want={want} "
                f"got={r.status_code} body={getattr(r, 'data', '')!s:.500}")
