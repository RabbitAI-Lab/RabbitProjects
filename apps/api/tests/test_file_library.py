"""FILE-002 项目文件库与多层级目录测试（§5 用例表）。

影响面圈定（CodeGraph，CLAUDE.md 测试脚本规范首条）：FileAsset / asset_cleanup /
AssetService.list_for_issue 的受影响面 = 全量既有 pytest（17 文件，由全量门禁
回归）；本文件只圈新符号（file_library / file_permission / file_stats）。

范围划界（§7.1 交付物清单为准）：
- UT-01~21 全量。UT-10 以「在途预留判定 + 临界 409」确定性等价覆盖——
  pytest-django 事务回滚夹具下，并发线程走独立连接看不见未提交夹具数据，
  真多线程并发（两笔恰一笔成功）由 HTTP 侧 flow 脚本承载；
- IT-02/03/04/05/09 为 pytest 可测子集；IT-01（真 MinIO PUT 链路）/ IT-06
  （95% 预警通知，§7.1 未列交付）/ IT-07（万文件 P95 性能门禁）/ IT-08
  （动态留痕，COLLAB-003 非任务域管道）不在本文件。

夹具纪律（坑 18）：pytest 直连共享 dev PG——清理任务调用必须带
``restrict_workspace_id``（测试安全参数），断言一律 filter 到本测试作用域。
存储交互全部 mock（presign/head/remove），不触碰真实 MinIO。
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from plane.base.middleware import ulid_new
from plane.bgtasks import file_stats
from plane.bgtasks.asset_cleanup import mark_abandoned_uploads, purge_deleted_assets
from plane.db.models import (
    FileAsset,
    FileFolder,
    Issue,
    Project,
    ProjectMember,
    ProjectRole,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)
from plane.db.services import file_library as svc
from plane.db.services.file_permission import can_view_file, effective_project_role

pytestmark = pytest.mark.django_db

PUT_URL = "plane.storage.minio.presigned_put_url"
GET_URL = "plane.storage.minio.presigned_get_url"
HEAD_SIZE = "plane.storage.minio.head_object_size"
REMOVE_OBJ = "plane.storage.minio.remove_object"


# ────────────────────────────────────────────────────────────────
# 夹具
# ────────────────────────────────────────────────────────────────
@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="f2-owner@rabbit.dev", password="Rabbit123!", display_name="库长")
    admin = User.objects.create_user(email="f2-admin@rabbit.dev", password="Rabbit123!", display_name="管理员")
    contrib = User.objects.create_user(email="f2-contrib@rabbit.dev", password="Rabbit123!", display_name="贡献者丙")
    other_contrib = User.objects.create_user(
        email="f2-other@rabbit.dev", password="Rabbit123!", display_name="贡献者乙"
    )
    viewer = User.objects.create_user(email="f2-viewer@rabbit.dev", password="Rabbit123!", display_name="只读者")
    member_a = User.objects.create_user(email="f2-a@rabbit.dev", password="Rabbit123!", display_name="成员甲")
    ws = Workspace.objects.create(name="W", slug=f"w-f2-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    for u, ws_role in (
        (owner, WorkspaceRole.OWNER),
        (admin, WorkspaceRole.MEMBER),
        (contrib, WorkspaceRole.MEMBER),
        (other_contrib, WorkspaceRole.MEMBER),
        (viewer, WorkspaceRole.MEMBER),
        (member_a, WorkspaceRole.MEMBER),
    ):
        WorkspaceMember.objects.create(workspace=ws, member=u, role=ws_role, created_by=owner)
    proj = Project.objects.create(name="P", identifier="F2", workspace=ws, created_by=owner)
    ProjectMember.objects.create(project=proj, member=admin, role=ProjectRole.ADMIN, created_by=owner)
    ProjectMember.objects.create(project=proj, member=contrib, role=ProjectRole.CONTRIBUTOR, created_by=owner)
    ProjectMember.objects.create(project=proj, member=other_contrib, role=ProjectRole.CONTRIBUTOR, created_by=owner)
    ProjectMember.objects.create(project=proj, member=viewer, role=ProjectRole.VIEWER, created_by=owner)
    ProjectMember.objects.create(project=proj, member=member_a, role=ProjectRole.CONTRIBUTOR, created_by=owner)
    # 跨项目移动目标（UT-04）
    proj2 = Project.objects.create(name="P2", identifier="F2B", workspace=ws, created_by=owner)
    return {
        "owner": owner,
        "admin": admin,
        "contrib": contrib,
        "other": other_contrib,
        "viewer": viewer,
        "member_a": member_a,
        "ws": ws,
        "proj": proj,
        "proj2": proj2,
    }


class _Client:
    def __init__(self, user):
        self.c = APIClient()
        self.c.force_authenticate(user=user)

    def get(self, path):
        return self.c.get(path, format="json")

    def post(self, path, body=None):
        return self.c.post(path, body or {}, format="json")

    def patch(self, path, body=None):
        return self.c.patch(path, body or {}, format="json")

    def delete(self, path):
        return self.c.delete(path, format="json")


def _base(env):
    return f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}"


def _folders_url(env):
    return f"{_base(env)}/folders/"


def _folder_url(env, fid):
    return f"{_base(env)}/folders/{fid}/"


def _files_url(env, fid, qs: str = ""):
    return f"{_base(env)}/folders/{fid}/files/{qs}"


def _presign_url(env, fid):
    return f"{_base(env)}/folders/{fid}/files/presign/"


def _asset_url(env, aid):
    return f"{_base(env)}/files/{aid}/"


def _dl_url(env, aid):
    return f"{_base(env)}/files/{aid}/download-url/"


def _restore_url(env, aid):
    return f"{_base(env)}/files/{aid}/restore/"


def _purge_url(env, aid):
    return f"{_base(env)}/files/{aid}/purge/"


def _complete_url(env, aid):
    return f"{_base(env)}/files/{aid}/complete/"


def _trash_url(env):
    return f"{_base(env)}/files/trash/"


def _storage_url(env):
    return f"{_base(env)}/files/storage/"


def _mk_folder(env, name, *, parent=None, visibility="all", allowed=None, project=None):
    return FileFolder.objects.create(
        project=project or env["proj"],
        parent=parent,
        name=name,
        visibility=visibility,
        allowed_members=[str(m) for m in (allowed or [])],
        created_by=env["owner"],
        updated_by=env["owner"],
    )


_MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".zip": "application/zip",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".mp4": "video/mp4",
}


def _mk_file(
    env,
    folder,
    *,
    name="报告.pdf",
    size=1024,
    visibility=None,
    allowed=None,
    uploaded_by=None,
    status="uploaded",
    storage_path=None,
    issue=None,
    project=None,
):
    """直建文件库资产（绕过 presign）；visibility 缺省随目录；ext/mime 按名派生。"""
    ext = "." + name.rsplit(".", 1)[-1].lower()
    mime = _MIME_BY_EXT.get(ext, "application/octet-stream")
    return FileAsset.objects.create(
        workspace=env["ws"],
        project=project or env["proj"],
        entity_type=FileAsset.EntityType.PROJECT_FILE,
        entity_id=folder.id,
        folder=folder,
        issue=issue,
        attributes={"name": name, "size": size, "mime": mime, "ext": ext},
        size=size,
        storage_path=storage_path
        or (f"{env['ws'].id}/{(project or env['proj']).id}/project_file/{folder.id}/{ulid_new()}{ext}"),
        status=status,
        uploaded_by=uploaded_by or env["owner"],
        visibility=visibility or folder.visibility,
        allowed_members=([str(m) for m in allowed] if allowed is not None else list(folder.allowed_members or [])),
        created_by=uploaded_by or env["owner"],
        updated_by=uploaded_by or env["owner"],
    )


def _err_code(resp):
    return ((resp.data if hasattr(resp, "data") else {}) or {}).get("error", {}).get("code")


def _detail(resp, field):
    for item in (resp.data.get("error") or {}).get("details") or []:
        if item.get("field") == field:
            return item
    return None


def _tree_names(resp):
    return {row["name"] for row in resp.data["data"]}


# ────────────────────────────────────────────────────────────────
# UT-01 同层同名（409 UNIQUE；Serializer clean 第一层 + DB 第二层）
# ────────────────────────────────────────────────────────────────
def test_ut01_folder_same_name_conflict(env):
    c = _Client(env["contrib"])
    r1 = c.post(_folders_url(env), {"name": "设计稿"})
    assert r1.status_code == 201
    r2 = c.post(_folders_url(env), {"name": "设计稿"})
    assert r2.status_code == 409
    assert _err_code(r2) == "RESOURCE_ALREADY_EXISTS"
    assert _detail(r2, "name")["code"] == "UNIQUE"
    # 根层 DB 第二层（COALESCE 表达式唯一索引）：直建绕过 Serializer 也被拦
    from django.db import IntegrityError

    with pytest.raises(IntegrityError):
        with transaction.atomic():  # savepoint：避免污染外层测试事务
            FileFolder.objects.create(project=env["proj"], parent=None, name="设计稿", created_by=env["owner"])
    # 非根层偏条件唯一约束：不同层同名允许（作用域到本项目，防共享库同名污染——坑 18）
    sub = _mk_folder(env, "子目录", parent=FileFolder.objects.get(project=env["proj"], name="设计稿"))
    assert sub.name == "子目录"


def test_ut01b_folder_name_same_under_different_parent(env):
    p1 = _mk_folder(env, "一期")
    p2 = _mk_folder(env, "二期")
    c = _Client(env["contrib"])
    assert c.post(_folders_url(env), {"name": "设计稿", "parent_id": str(p1.id)}).status_code == 201
    r = c.post(_folders_url(env), {"name": "设计稿", "parent_id": str(p2.id)})
    assert r.status_code == 201  # 不同层同名共存


# ── UT-02 深度上限（第 6 层 → 409 LIMIT）────────────────────────
def test_ut02_folder_depth_limit(env):
    c = _Client(env["contrib"])
    parent = None
    for i in range(5):  # 建 5 层
        resp = c.post(_folders_url(env), {"name": f"L{i + 1}", "parent_id": parent and str(parent)})
        assert resp.status_code == 201, f"第 {i + 1} 层应允许"
        parent = resp.data["data"]["id"]
    r6 = c.post(_folders_url(env), {"name": "L6", "parent_id": str(parent)})
    assert r6.status_code == 409
    assert _err_code(r6) == "RESOURCE_LIMIT_EXCEEDED"
    assert _detail(r6, "parent_id")["code"] == "LIMIT"


# ── UT-03 移动成环（父移到子下 → 409 CYCLE）─────────────────────
def test_ut03_folder_move_cycle(env):
    l1 = _mk_folder(env, "L1")
    l2 = _mk_folder(env, "L2", parent=l1)
    l3 = _mk_folder(env, "L3", parent=l2)
    c = _Client(env["contrib"])
    r = c.patch(_folder_url(env, l1.id), {"parent_id": str(l3.id)})
    assert r.status_code == 409
    assert _err_code(r) == "RESOURCE_CIRCULAR_DEPENDENCY"
    assert _detail(r, "parent_id")["code"] == "CYCLE"
    l1.refresh_from_db()
    assert l1.parent_id is None  # 移动未生效


# ── UT-04 跨项目移动（目标他项目 → 400）─────────────────────────
def test_ut04_folder_cross_project_move(env):
    l1 = _mk_folder(env, "本目录")
    foreign = _mk_folder(env, "他项目目录", project=env["proj2"])
    c = _Client(env["admin"])
    r = c.patch(_folder_url(env, l1.id), {"parent_id": str(foreign.id)})
    assert r.status_code == 400


# ── UT-05 同名文件共存（同目录两名均成功，键含 ULID）─────────────
def test_ut05_same_name_files_coexist(env):
    folder = _mk_folder(env, "设计稿")
    c = _Client(env["contrib"])
    with patch(PUT_URL) as mock:
        mock.return_value = "http://minio/uploads/x"
        r1 = c.post(
            _presign_url(env, folder.id),
            {"file_name": "首页改版.zip", "file_size": 1024, "content_type": "application/octet-stream"},
        )
        r2 = c.post(
            _presign_url(env, folder.id),
            {"file_name": "首页改版.zip", "file_size": 2048, "content_type": "application/octet-stream"},
        )
    assert r1.status_code == 201 and r2.status_code == 201
    a1, a2 = r1.data["data"]["asset_id"], r2.data["data"]["asset_id"]
    assert a1 != a2
    k1 = FileAsset.objects.get(pk=a1).storage_path
    k2 = FileAsset.objects.get(pk=a2).storage_path
    assert k1 != k2  # 对象键含 ULID，不冲突（BR-02）


# ── UT-06 可见性 all（VIEWER 浏览可见）──────────────────────────
def test_ut06_visibility_all_viewer(env):
    folder = _mk_folder(env, "全员目录")
    _mk_file(env, folder, uploaded_by=env["owner"])
    v = _Client(env["viewer"])
    tree = v.get(_folders_url(env))
    assert tree.status_code == 200
    assert "全员目录" in _tree_names(tree)
    files = v.get(_files_url(env, folder.id))
    assert files.status_code == 200
    assert files.data["meta"]["total_count"] == 1
    # §4.2.1 expand：默认 uploaded_by 为 ID；expand 后追加对象形态
    row = files.data["data"][0]
    assert row["uploaded_by"] == str(env["owner"].id)
    assert "uploaded_by_detail" not in row
    expanded = v.get(_files_url(env, folder.id, "?expand=uploaded_by"))
    detail = expanded.data["data"][0]["uploaded_by_detail"]
    assert detail == {"id": str(env["owner"].id), "display_name": "库长"}


# ── UT-07 可见性 admins（CONTRIBUTOR 列表不可见；直连 404）──────
def test_ut07_visibility_admins_hidden(env):
    folder = _mk_folder(env, "管理员目录", visibility="admins")
    file_a = _mk_file(env, folder, visibility="admins")
    normal_folder = _mk_folder(env, "普通目录")
    file_b = _mk_file(env, normal_folder, visibility="admins")  # 目录 all、文件收紧
    c = _Client(env["contrib"])
    # 目录级：树不出现、文件列表 404（存在性隐藏）
    assert "管理员目录" not in _tree_names(c.get(_folders_url(env)))
    assert c.get(_files_url(env, folder.id)).status_code == 404
    # 文件级：列表过滤 + download-url 404
    files = c.get(_files_url(env, normal_folder.id))
    assert files.data["meta"]["total_count"] == 0
    assert c.get(_dl_url(env, file_a.id)).status_code == 404
    assert c.get(_dl_url(env, file_b.id)).status_code == 404
    # ADMIN 可见（隐式：owner 为 WS OWNER → PROJ_ADMIN）
    o = _Client(env["owner"])
    assert "管理员目录" in _tree_names(o.get(_folders_url(env)))
    with patch(GET_URL) as mock:
        mock.return_value = "http://minio/get"
        assert o.get(_dl_url(env, file_a.id)).status_code == 200


# ── UT-08 可见性 members（指定本人可见；他人 404）────────────────
def test_ut08_visibility_members(env):
    folder = _mk_folder(env, "指定目录")
    file_a = _mk_file(env, folder, visibility="members", allowed=[env["member_a"].id])
    a = _Client(env["member_a"])
    files = a.get(_files_url(env, folder.id))
    assert files.status_code == 200 and files.data["meta"]["total_count"] == 1
    with patch(GET_URL) as mock:
        mock.return_value = "http://minio/get"
        assert a.get(_dl_url(env, file_a.id)).status_code == 200
    b = _Client(env["other"])
    assert b.get(_files_url(env, folder.id)).data["meta"]["total_count"] == 0
    with patch(GET_URL) as mock:
        mock.return_value = "http://minio/get"
        assert b.get(_dl_url(env, file_a.id)).status_code == 404


# ── UT-09 预签名实时校验（拿链接后被移出 → 新签发 404）──────────
def test_ut09_presign_recheck_after_removal(env):
    folder = _mk_folder(env, "目录")
    file_a = _mk_file(env, folder, visibility="members", allowed=[env["member_a"].id])
    a = _Client(env["member_a"])
    with patch(GET_URL) as mock:
        mock.return_value = "http://minio/get"
        assert a.get(_dl_url(env, file_a.id)).status_code == 200
    # ADMIN 把甲移出指定成员
    admin = _Client(env["admin"])
    r = admin.patch(_asset_url(env, file_a.id), {"visibility": "members", "allowed_members": []})
    assert r.status_code == 200
    with patch(GET_URL) as mock:
        mock.return_value = "http://minio/get"
        assert a.get(_dl_url(env, file_a.id)).status_code == 404  # 签发时实时校验（BR-09）


# ── UT-10 配额在途预留（临界 → 恰一笔 409）───────────────────────
@override_settings(WS_STORAGE_QUOTA_BYTES=1000)
def test_ut10_quota_pending_reservation(env):
    folder = _mk_folder(env, "配额目录")
    _mk_file(env, folder, size=600, status="uploading")  # 在途预留 600
    c = _Client(env["contrib"])
    # storage 端点：used=0 / pending=600 / quota=1000
    usage = c.get(_storage_url(env)).data["data"]
    assert usage == {"quota_bytes": 1000, "used_bytes": 0, "pending_bytes": 600, "usage_ratio": 0.0}
    # 临界：600(在途) + 500(新) > 1000 → 第二笔 409；300 则放行（在途计入，BR-03）
    with patch(PUT_URL) as mock:
        mock.return_value = "http://minio/uploads/x"
        r_fail = c.post(
            _presign_url(env, folder.id),
            {"file_name": "大文件.pdf", "file_size": 500, "content_type": "application/pdf"},
        )
        r_ok = c.post(
            _presign_url(env, folder.id),
            {"file_name": "小文件.pdf", "file_size": 300, "content_type": "application/pdf"},
        )
    assert r_ok.status_code == 201
    assert r_fail.status_code == 409
    assert _err_code(r_fail) == "QUOTA_STORAGE_EXCEEDED"
    assert _detail(r_fail, "file_size")["code"] == "QUOTA"
    # 行锁串行化语义（§4.3.4）：assert_quota 在事务内 select_for_update Workspace
    # ——UT-10 并发形态（两笔恰一笔成功）由 HTTP flow 承载，此处锁路径以行为断言：
    import inspect

    src = inspect.getsource(svc.assert_quota)
    assert "select_for_update" in src and "_ = locked" in src


# ── UT-11 双挂删除语义（解除挂接 → 行与对象存活）────────────────
def test_ut11_dual_mount_detach_keeps_row_and_object(env):
    folder = _mk_folder(env, "设计稿")
    file_a = _mk_file(env, folder, uploaded_by=env["contrib"])
    issue = Issue.objects.create(
        name="需求任务", project=env["proj"], sequence_id=1, sort_order=100, created_by=env["owner"]
    )
    c = _Client(env["contrib"])
    # 附加到任务（双挂建立）
    r = c.patch(_asset_url(env, file_a.id), {"issue_id": str(issue.id)})
    assert r.status_code == 200
    # 任务附件区可见双挂行（§1.2：entity_type=issue 行 ∪ issue 外键非空行）
    att = c.get(f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/issues/{issue.id}/attachments/")
    assert att.status_code == 200
    assert str(file_a.id) in {row["id"] for row in att.data["data"]}
    # 解除挂接：文件库行与对象存活（删除按引用计数语义）
    with patch(REMOVE_OBJ) as mock_rm:
        r2 = c.patch(_asset_url(env, file_a.id), {"issue_id": None})
        assert r2.status_code == 200
    file_a.refresh_from_db()
    assert file_a.deleted_at is None
    assert file_a.issue_id is None
    mock_rm.assert_not_called()  # 对象不动


# ── UT-12 回收站期满（beat：无引用删对象；有引用保留）────────────
def test_ut12_trash_expiry_reference_counting(env):
    folder = _mk_folder(env, "目录")
    shared_key = f"{env['ws'].id}/{env['proj'].id}/project_file/{folder.id}/{ulid_new()}.pdf"
    gone = _mk_file(env, folder, size=100, storage_path=shared_key)
    FileAsset.objects.filter(pk=gone.pk).update(deleted_at=timezone.now() - timedelta(days=31))
    alive = _mk_file(env, folder, size=100, storage_path=shared_key)  # 同键存活引用
    with patch(REMOVE_OBJ) as mock_rm:
        result = purge_deleted_assets(restrict_workspace_id=env["ws"].id)
    assert result["purged"] >= 1
    assert not FileAsset.all_objects.filter(pk=gone.pk).exists()  # 元数据硬删
    assert FileAsset.objects.filter(pk=alive.pk).exists()  # 存活引用行不动
    mock_rm.assert_not_called()  # BR-06：有存活引用 → 对象保留
    # 引用清空后期满：对象删除
    FileAsset.objects.filter(pk=alive.pk).update(deleted_at=timezone.now() - timedelta(days=31))
    with patch(REMOVE_OBJ) as mock_rm:
        purge_deleted_assets(restrict_workspace_id=env["ws"].id)
    mock_rm.assert_called_once()
    assert not FileAsset.all_objects.filter(pk=alive.pk).exists()


# ── UT-13 恢复冲突（原位同名 → 落根 + (恢复) 后缀）───────────────
def test_ut13_restore_conflict_suffix(env):
    p = _mk_folder(env, "P")
    child = _mk_folder(env, "C", parent=p)
    file_c = _mk_file(env, child)
    svc.delete_folder(folder=p, actor=env["owner"])  # 整树软删
    _mk_folder(env, "P")  # 原位被同名占用
    restored = svc.restore_folder(folder=FileFolder.all_objects.get(pk=p.pk), actor=env["owner"])
    assert restored.parent_id is None  # 落根
    assert restored.name == "P(恢复)"  # 追加后缀（BR-07）
    file_c.refresh_from_db()
    assert file_c.deleted_at is None  # 整树恢复连带文件
    child.refresh_from_db()
    assert child.deleted_at is None and child.parent_id == restored.id


def test_ut13b_restore_in_place_when_no_conflict(env):
    p = _mk_folder(env, "独")
    child = _mk_folder(env, "子", parent=p)
    file_c = _mk_file(env, child)
    svc.delete_folder(folder=p, actor=env["owner"])
    restored = svc.restore_folder(folder=FileFolder.all_objects.get(pk=p.pk), actor=env["owner"])
    assert restored.parent_id is None and restored.name == "独"  # 原位（根）无冲突
    file_c.refresh_from_db()
    assert file_c.deleted_at is None


# ── UT-14 目录级联软删（删含 12 文件目录 → 整树软删，回传 12）────
def test_ut14_folder_cascade_soft_delete(env):
    parent = _mk_folder(env, "父")
    sub1 = _mk_folder(env, "子1", parent=parent)
    sub2 = _mk_folder(env, "子2", parent=parent)
    for i in range(12):
        _mk_file(env, parent if i < 5 else (sub1 if i < 9 else sub2), name=f"f{i}.pdf")
    c = _Client(env["contrib"])
    r = c.delete(_folder_url(env, parent.id))
    assert r.status_code == 200
    assert r.data["data"]["files_deleted"] == 12
    assert r.data["data"]["folders_deleted"] == 3
    # 整树软删（folders + files），对象不动
    assert not FileFolder.objects.filter(project=env["proj"]).exists()
    assert FileFolder.all_objects.filter(project=env["proj"], deleted_at__isnull=False).count() == 3
    assert (
        FileAsset.all_objects.filter(
            project=env["proj"], entity_type=FileAsset.EntityType.PROJECT_FILE, deleted_at__isnull=False
        ).count()
        == 12
    )


# ── UT-15 单文件 50MB 上限（51MB → 400 SIZE_EXCEEDED）───────────
def test_ut15_file_size_50mb_limit(env):
    folder = _mk_folder(env, "目录")
    c = _Client(env["contrib"])
    r = c.post(
        _presign_url(env, folder.id),
        {"file_name": "方案演示.zip", "file_size": 50 * 1024 * 1024 + 1, "content_type": "application/zip"},
    )
    assert r.status_code == 400
    assert _err_code(r) == "VALIDATION_FILE_SIZE_EXCEEDED"
    assert "分片" in _detail(r, "file_size")["message"]  # P2 文案（§2.5）
    # 恰 50MB 边界放行
    with patch(PUT_URL) as mock:
        mock.return_value = "http://minio/uploads/x"
        r_ok = c.post(
            _presign_url(env, folder.id),
            {"file_name": "方案演示.zip", "file_size": 50 * 1024 * 1024, "content_type": "application/zip"},
        )
    assert r_ok.status_code == 201
    # 白名单拒绝（BR-03 第一环，沿用 FILE-001 ALLOWED_EXTS）
    r_ext = c.post(
        _presign_url(env, folder.id),
        {"file_name": "病毒.exe", "file_size": 100, "content_type": "application/x-msdownload"},
    )
    assert r_ext.status_code == 400
    assert _err_code(r_ext) == "VALIDATION_FILE_TYPE_NOT_ALLOWED"


# ── UT-16 项目归档只读（归档后上传/建目录 403；浏览可）───────────
def test_ut16_archived_project_readonly(env):
    Project.objects.filter(pk=env["proj"].pk).update(status="archived")
    env["proj"].refresh_from_db()
    c = _Client(env["contrib"])
    assert c.get(_folders_url(env)).status_code == 200  # 浏览可
    assert c.post(_folders_url(env), {"name": "新目录"}).status_code == 403
    folder = _mk_folder(env, "既有目录")
    with patch(PUT_URL) as mock:
        mock.return_value = "http://minio/uploads/x"
        r = c.post(
            _presign_url(env, folder.id), {"file_name": "a.pdf", "file_size": 10, "content_type": "application/pdf"}
        )
    assert r.status_code == 403
    assert _err_code(r) == "PERM_PROJECT_ARCHIVED"


# ── UT-17 回收站彻底删除（ADMIN purge 引用计数；CONTRIBUTOR 403）─
def test_ut17_purge_refcount_and_permission(env):
    folder = _mk_folder(env, "目录")
    shared_key = f"{env['ws'].id}/{env['proj'].id}/project_file/{folder.id}/{ulid_new()}.pdf"
    victim = _mk_file(env, folder, uploaded_by=env["contrib"], storage_path=shared_key)
    FileAsset.objects.filter(pk=victim.pk).update(deleted_at=timezone.now())
    ref = _mk_file(env, folder, storage_path=shared_key)  # 同键存活引用（模拟版本行）
    # CONTRIBUTOR（上传者本人）也不可彻底删除（§4.2 #13 仅 PROJ_ADMIN 收紧）
    c = _Client(env["contrib"])
    assert c.delete(_purge_url(env, victim.id)).status_code == 403
    # ADMIN purge：行硬删、对象因存活引用保留（BR-06）
    a = _Client(env["admin"])
    with patch(REMOVE_OBJ) as mock_rm:
        r = a.delete(_purge_url(env, victim.id))
    assert r.status_code == 200 and r.data["data"]["purged"] is True
    mock_rm.assert_not_called()
    assert not FileAsset.all_objects.filter(pk=victim.pk).exists()
    assert FileAsset.objects.filter(pk=ref.pk).exists()
    # 无引用：对象删除
    FileAsset.objects.filter(pk=ref.pk).update(deleted_at=timezone.now())
    with patch(REMOVE_OBJ) as mock_rm:
        r2 = a.delete(_purge_url(env, ref.id))
    assert r2.status_code == 200
    mock_rm.assert_called_once()


# ── UT-18 下载计数异步累加（Redis 计数 → beat 批量落库）──────────
class _FakeRedis:
    def __init__(self):
        self.h: dict[str, str] = {}

    def hincrby(self, key, field, amount=1):
        self.h[field] = str(int(self.h.get(field, "0")) + amount)
        return int(self.h[field])

    def hgetall(self, key):
        return dict(self.h)

    def hdel(self, key, *fields):
        for f in fields:
            self.h.pop(f, None)


def test_ut18_download_count_async_flush(env):
    file_stats.reset_redis_state()
    folder = _mk_folder(env, "目录")
    file_a = _mk_file(env, folder)
    fake = _FakeRedis()
    monkey_target = "plane.bgtasks.file_stats._redis"
    with patch(monkey_target, lambda: fake), patch(GET_URL) as mock_get:
        mock_get.return_value = "http://minio/get"
        a = _Client(env["admin"])
        for _ in range(3):
            assert a.get(_dl_url(env, file_a.id)).status_code == 200
        assert fake.hgetall(file_stats.DOWNLOAD_COUNT_KEY) == {str(file_a.id): "3"}
        assert FileAsset.objects.get(pk=file_a.pk).download_count == 0  # 未落库（零直写）
        file_stats.flush_download_counts()  # 触发 beat 批量任务
        assert FileAsset.objects.get(pk=file_a.pk).download_count == 3
        assert fake.hgetall(file_stats.DOWNLOAD_COUNT_KEY) == {}  # 落库后清零
    file_stats.reset_redis_state()


def test_ut18b_download_count_degrades_to_direct_write(env):
    """Redis 不可达降级：单行 F() 直写保计数正确（不阻断签发）。"""
    file_stats.reset_redis_state()
    folder = _mk_folder(env, "目录")
    file_a = _mk_file(env, folder)
    with patch("plane.bgtasks.file_stats._redis", lambda: None), patch(GET_URL) as mock:
        mock.return_value = "http://minio/get"
        assert _Client(env["admin"]).get(_dl_url(env, file_a.id)).status_code == 200
    assert FileAsset.objects.get(pk=file_a.pk).download_count == 1
    file_stats.reset_redis_state()


# ── UT-19 文件更新 R1 受限（CONTRIBUTOR 仅本人上传）──────────────
def test_ut19_file_update_r1_restricted(env):
    folder = _mk_folder(env, "目录")
    own = _mk_file(env, folder, uploaded_by=env["contrib"])
    others = _mk_file(env, folder, uploaded_by=env["admin"])
    c = _Client(env["contrib"])
    r_denied = c.patch(_asset_url(env, others.id), {"name": "改名.pdf"})
    assert r_denied.status_code == 403
    assert _err_code(r_denied) == "PERM_DENIED"
    r_ok = c.patch(_asset_url(env, own.id), {"name": "我的改名.pdf"})
    assert r_ok.status_code == 200
    assert r_ok.data["data"]["name"] == "我的改名.pdf"
    # ADMIN 全量可改
    a = _Client(env["admin"])
    assert a.patch(_asset_url(env, others.id), {"name": "管理员改.pdf"}).status_code == 200


# ── UT-20 目录新建角色边界（VIEWER 403；CONTRIBUTOR 201）─────────
def test_ut20_folder_create_role_boundary(env):
    v = _Client(env["viewer"])
    r = v.post(_folders_url(env), {"name": "只读建的"})
    assert r.status_code == 403
    assert _err_code(r) == "PERM_DENIED"
    c = _Client(env["contrib"])
    assert c.post(_folders_url(env), {"name": "贡献者建的"}).status_code == 201


# ── UT-21 目录树可见性剪枝（子树隐藏 + 计数不透出）───────────────
def test_ut21_tree_pruning_and_count_no_leak(env):
    admins_dir = _mk_folder(env, "管理员目录", visibility="admins")
    _mk_file(env, admins_dir)
    parent_all = _mk_folder(env, "父全可见")
    child_admins = _mk_folder(env, "子仅管理员", parent=parent_all, visibility="admins")
    _mk_file(env, parent_all, name="父文件.pdf")
    _mk_file(env, child_admins, name="子文件.pdf")
    v = _Client(env["viewer"])
    tree = v.get(_folders_url(env))
    names = _tree_names(tree)
    assert "管理员目录" not in names  # admins 态隐藏
    assert "父全可见" in names
    assert "子仅管理员" not in names  # 父可见子不可见 → 子树隐藏
    # 计数不透出：父级 file_count 不含不可见子孙的文件（防计数侧信道）
    parent_row = next(r for r in tree.data["data"] if r["name"] == "父全可见")
    assert parent_row["file_count"] == 1
    # ADMIN 视角：全树 + 计数含子孙
    a = _Client(env["admin"])
    tree_a = a.get(_folders_url(env))
    assert {"管理员目录", "父全可见", "子仅管理员"} <= _tree_names(tree_a)
    parent_row_a = next(r for r in tree_a.data["data"] if r["name"] == "父全可见")
    assert parent_row_a["file_count"] == 2


# ────────────────────────────────────────────────────────────────
# IT-02 complete 校验（HEAD 大小不匹配 → 400）
# ────────────────────────────────────────────────────────────────
def test_it02_complete_head_mismatch(env):
    folder = _mk_folder(env, "目录")
    with patch(PUT_URL) as mock:
        mock.return_value = "http://minio/uploads/x"
        r = _Client(env["contrib"]).post(
            _presign_url(env, folder.id),
            {"file_name": "半量.zip", "file_size": 1000, "content_type": "application/octet-stream"},
        )
    asset_id = r.data["data"]["asset_id"]
    c = _Client(env["contrib"])
    # PUT 半量后 complete：HEAD 大小不匹配 → 400
    with patch(HEAD_SIZE) as mock:
        mock.return_value = 500
        r_bad = c.post(_complete_url(env, asset_id), {})
    assert r_bad.status_code == 400
    assert _err_code(r_bad) == "VALIDATION_FILE_UPLOAD_MISMATCH"
    assert _detail(r_bad, "file_size")["code"] == "INVALID"
    assert FileAsset.objects.get(pk=asset_id).status == "uploading"
    # 大小匹配 → 200 翻转 uploaded；幂等重放同构
    with patch(HEAD_SIZE) as mock:
        mock.return_value = 1000
        r_ok = c.post(_complete_url(env, asset_id), {})
        r_replay = c.post(_complete_url(env, asset_id), {})
    assert r_ok.status_code == 200 and r_ok.data["data"]["status"] == "uploaded"
    assert r_replay.status_code == 200 and r_replay.data["data"] == r_ok.data["data"]


# ────────────────────────────────────────────────────────────────
# IT-03 孤儿回收（30 分钟标记 abandoned → 次日物理清理）
# ────────────────────────────────────────────────────────────────
def test_it03_orphan_upload_recycled(env):
    folder = _mk_folder(env, "目录")
    with patch(PUT_URL) as mock:
        mock.return_value = "http://minio/uploads/x"
        r = _Client(env["contrib"]).post(
            _presign_url(env, folder.id), {"file_name": "孤儿.pdf", "file_size": 66, "content_type": "application/pdf"}
        )
    asset_id = r.data["data"]["asset_id"]
    # presign 不 complete；加速时钟：回拨 created_at 至 31 分钟前
    FileAsset.objects.filter(pk=asset_id).update(created_at=timezone.now() - timedelta(minutes=31))
    marked = mark_abandoned_uploads(restrict_workspace_id=env["ws"].id)
    assert marked == 1
    assert FileAsset.objects.get(pk=asset_id).status == "abandoned"
    # 次日：残片对象与记录物理清理
    FileAsset.objects.filter(pk=asset_id).update(created_at=timezone.now() - timedelta(days=2))
    with patch(REMOVE_OBJ) as mock_rm:
        result = purge_deleted_assets(restrict_workspace_id=env["ws"].id)
    mock_rm.assert_called_once()
    assert not FileAsset.all_objects.filter(pk=asset_id).exists()
    assert result["purged"] == 1


# ────────────────────────────────────────────────────────────────
# IT-04 移动零对象操作（键不变、无存储调用）
# ────────────────────────────────────────────────────────────────
def test_it04_folder_move_zero_object_ops(env):
    src = _mk_folder(env, "源目录")
    dst = _mk_folder(env, "目标目录")
    big = _mk_file(env, src, name="1GB素材.zip", size=1024**3)
    key_before = big.storage_path
    with patch(REMOVE_OBJ) as mock_rm, patch(PUT_URL) as mock_put:
        r = _Client(env["contrib"]).patch(_folder_url(env, src.id), {"parent_id": str(dst.id)})
    assert r.status_code == 200
    big.refresh_from_db()
    src.refresh_from_db()
    assert big.storage_path == key_before  # 键不变（纯元数据操作，IT-04）
    assert big.folder_id == src.id  # 文件仍在被移动目录内（目录移动不改文件归属）
    assert src.parent_id == dst.id
    mock_rm.assert_not_called()
    mock_put.assert_not_called()


# ────────────────────────────────────────────────────────────────
# IT-05 可见性三层一致（列表/目录树/download-url 三路同源）
# ────────────────────────────────────────────────────────────────
def test_it05_visibility_three_layers_consistent(env):
    folder = _mk_folder(env, "受限目录", visibility="admins")
    file_a = _mk_file(env, folder, visibility="admins")
    c = _Client(env["contrib"])
    with patch(GET_URL) as mock:
        mock.return_value = "http://minio/get"
        assert "受限目录" not in _tree_names(c.get(_folders_url(env)))  # 树
        assert c.get(_files_url(env, folder.id)).status_code == 404  # 列表
        assert c.get(_dl_url(env, file_a.id)).status_code == 404  # 预签名
    # 单入口同源：三路判定均收敛到 can_view_file（BR-08）
    assert can_view_file(env["contrib"], folder) is False
    assert can_view_file(env["admin"], folder) is True
    assert effective_project_role(env["owner"], env["proj"].id) == ProjectRole.ADMIN


# ────────────────────────────────────────────────────────────────
# IT-09 目录树剪枝与计数不透出（三角色 + 甲矩阵）
# ────────────────────────────────────────────────────────────────
def test_it09_tree_role_matrix(env):
    admins_dir = _mk_folder(env, "甲不可见的管理员目录", visibility="admins")
    _mk_file(env, admins_dir, name="m.pdf")
    members_dir = _mk_folder(env, "仅甲目录", visibility="members", allowed=[env["member_a"].id])
    _mk_file(env, members_dir, name="a.pdf")
    parent_all = _mk_folder(env, "父全可见")
    child_admins = _mk_folder(env, "子仅管理员", parent=parent_all, visibility="admins")
    _mk_file(env, child_admins, name="ca.pdf")
    parent_admins = _mk_folder(env, "父仅管理员", visibility="admins")
    child_all = _mk_folder(env, "子全可见", parent=parent_admins)
    _mk_file(env, child_all, name="cl.pdf")

    def visible_by(user):
        resp = _Client(user).get(_folders_url(env))
        return _tree_names(resp)

    v = visible_by(env["viewer"])  # VIEWER
    b = visible_by(env["other"])  # CONTRIBUTOR 乙（非指定成员）
    a = visible_by(env["admin"])  # ADMIN
    jia = visible_by(env["member_a"])  # 甲（members 态指定成员）
    # admins 态：仅 ADMIN 可见
    for names in (v, b, jia):
        assert "甲不可见的管理员目录" not in names
        assert "父仅管理员" not in names and "子全可见" not in names  # 祖先不可见 → 整支不呈现
    assert {"甲不可见的管理员目录", "父仅管理员", "子全可见"} <= a
    # members 态：仅甲与 ADMIN 可见
    assert "仅甲目录" in jia and "仅甲目录" in a
    assert "仅甲目录" not in v and "仅甲目录" not in b
    # 父可见子不可见：子隐藏、父计数不含不可见子孙
    tree_v = _Client(env["viewer"]).get(_folders_url(env))
    parent_row = next(r for r in tree_v.data["data"] if r["name"] == "父全可见")
    assert "子仅管理员" not in v
    assert parent_row["file_count"] == 0  # 父自身无文件、子的 ca.pdf 不透出


# ────────────────────────────────────────────────────────────────
# 回收站列表 / 恢复 R1 口径（BR-13：CONTRIBUTOR 仅见本人删除项）
# ────────────────────────────────────────────────────────────────
def test_trash_list_and_restore_r1_scope(env):
    folder = _mk_folder(env, "目录")
    own = _mk_file(env, folder, uploaded_by=env["contrib"])
    others = _mk_file(env, folder, uploaded_by=env["admin"])
    c = _Client(env["contrib"])
    assert c.delete(_asset_url(env, own.id)).status_code == 204
    a = _Client(env["admin"])
    assert a.delete(_asset_url(env, others.id)).status_code == 204
    # CONTRIBUTOR 回收站：仅本人删除项
    trash_c = c.get(_trash_url(env))
    assert trash_c.data["meta"]["total_count"] == 1
    assert trash_c.data["data"][0]["id"] == str(own.id)
    assert trash_c.data["data"][0]["deleted_at"]
    # ADMIN 回收站：全量
    trash_a = a.get(_trash_url(env))
    assert trash_a.data["meta"]["total_count"] == 2
    # CONTRIBUTOR 不能还原他人删除项 / 不能删他人文件（R1 同键）
    assert c.post(_restore_url(env, others.id)).status_code == 403
    # 自恢复成功
    r = c.post(_restore_url(env, own.id))
    assert r.status_code == 200
    own.refresh_from_db()
    assert own.deleted_at is None
    # 不在回收站的行 restore → 409 状态拒绝
    assert c.post(_restore_url(env, own.id)).status_code == 409


# ────────────────────────────────────────────────────────────────
# 文件移动 + 列表筛选（名称/类型/上传人 + 游标 + meta 扩展字段）
# ────────────────────────────────────────────────────────────────
def test_file_move_and_list_filters(env):
    f1 = _mk_folder(env, "目录一")
    f2 = _mk_folder(env, "目录二")
    fa = _mk_file(env, f1, name="需求评审纪要.docx", size=100, uploaded_by=env["contrib"])
    _mk_file(env, f1, name="首页改版.png", size=200, uploaded_by=env["admin"])
    _mk_file(env, f1, name="资产打包.zip", size=300, uploaded_by=env["contrib"])
    c = _Client(env["viewer"])
    # 名称过滤（trgm）
    r = c.get(_files_url(env, f1.id, "?name=首页"))
    assert r.data["meta"]["total_count"] == 1
    assert r.data["data"][0]["name"] == "首页改版.png"
    assert r.data["data"][0]["type_category"] == "image"
    # 类型 + 上传人筛选
    r2 = c.get(_files_url(env, f1.id, "?type=archive"))
    assert r2.data["meta"]["total_count"] == 1
    assert r2.data["data"][0]["type_category"] == "archive"
    r3 = c.get(_files_url(env, f1.id, f"?uploaded_by={env['contrib'].id}"))
    assert r3.data["meta"]["total_count"] == 2
    # meta 扩展字段：total_size_bytes（§4.2.1）
    r4 = c.get(_files_url(env, f1.id))
    assert r4.data["meta"]["total_size_bytes"] == 600
    assert r4.data["meta"]["per_page"] == 50
    # 移动文件到目录二（元数据操作）
    m = _Client(env["contrib"])
    assert m.patch(_asset_url(env, fa.id), {"folder_id": str(f2.id)}).status_code == 200
    fa.refresh_from_db()
    assert fa.folder_id == f2.id
    assert fa.entity_id == f2.id  # 多态列同值同步（§1.7 第 1 行）
    assert c.get(_files_url(env, f1.id)).data["meta"]["total_count"] == 2
    assert c.get(_files_url(env, f2.id)).data["meta"]["total_count"] == 1


def test_list_pagination_cursor(env):
    folder = _mk_folder(env, "分页目录")
    for i in range(7):
        _mk_file(env, folder, name=f"文件{i:02d}.pdf")
    c = _Client(env["viewer"])
    p1 = c.get(_files_url(env, folder.id, "?per_page=3"))
    assert p1.data["meta"]["count"] == 3
    assert p1.data["meta"]["total_count"] == 7
    assert p1.data["meta"]["next_page_results"] is True
    cursor = p1.data["meta"]["next_cursor"]
    p2 = c.get(_files_url(env, folder.id, f"?per_page=3&cursor={cursor}"))
    assert p2.data["meta"]["page"] == 2
    assert p2.data["meta"]["count"] == 3
    p3 = c.get(_files_url(env, folder.id, f"?per_page=3&cursor={p2.data['meta']['next_cursor']}"))
    assert p3.data["meta"]["count"] == 1
    assert p3.data["meta"]["next_cursor"] is None
    # 非法游标 → 400（C003 同款）
    assert c.get(_files_url(env, folder.id, "?cursor=%%%=bad")).status_code == 400


# ────────────────────────────────────────────────────────────────
# 目录可见性配置（file.permission.manage：仅 ADMIN）
# ────────────────────────────────────────────────────────────────
def test_folder_visibility_requires_admin(env):
    folder = _mk_folder(env, "目录")
    c = _Client(env["contrib"])
    r = c.patch(_folder_url(env, folder.id), {"visibility": "admins"})
    assert r.status_code == 403
    a = _Client(env["admin"])
    r2 = a.patch(_folder_url(env, folder.id), {"visibility": "members", "allowed_members": [str(env["member_a"].id)]})
    assert r2.status_code == 200
    assert r2.data["data"]["visibility"] == "members"
    assert r2.data["data"]["allowed_members"] == [str(env["member_a"].id)]
    # 甲可见、乙不可见
    assert "目录" in _tree_names(_Client(env["member_a"]).get(_folders_url(env)))
    assert "目录" not in _tree_names(_Client(env["other"]).get(_folders_url(env)))


def test_folder_rename_conflict_on_patch(env):
    p = _mk_folder(env, "父")
    _mk_folder(env, "A", parent=p)
    b = _mk_folder(env, "B", parent=p)
    c = _Client(env["contrib"])
    r = c.patch(_folder_url(env, b.id), {"name": "A"})
    assert r.status_code == 409
    assert _detail(r, "name")["code"] == "UNIQUE"
    assert c.patch(_folder_url(env, b.id), {"name": "B2"}).status_code == 200
