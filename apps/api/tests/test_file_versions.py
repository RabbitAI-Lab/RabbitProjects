"""FILE-003 分片续传 / 在线预览 / 多版本测试（§5 用例表）。

影响面圈定（CodeGraph，CLAUDE.md 测试脚本规范首条）：FileAsset / assert_quota /
mark_abandoned_uploads / purge_deleted_assets / complete_file 的受影响面 =
test_file_library 全量（由全量门禁回归）；本文件圈新符号（upload_session /
derive_preview / event_publisher 的 file 域扩展）。

范围划界：
- UT-01~18 全量；IT-01~09 为 pytest 可测子集（IT-01 真实 MinIO PUT 链路由
  HTTP 侧 flow 脚本承载——本文件以 mock S3 multipart 走全协议：片级 ETag
  登记 → ListParts 核对 → complete）。
- 存储/子进程交互全部 mock（multipart 五函数 / put_object / remove_object /
  head / presign_get），不触碰真实 MinIO 与 soffice/ffmpeg（本机均未安装——
  UT-15 正是工具缺失语义的用例）。

夹具纪律（坑 18）：pytest 直连共享 dev PG——清理任务（expire/sweep/purge/
mark_abandoned）一律带 ``restrict_workspace_id``；断言一律 filter 到本测试
作用域（asset/session/version id 或 workspace），禁全表 count。
on_commit 钩子（BR-13 事件 / derive 排队）经 ``django_capture_on_commit_callbacks``
捕获。
"""
from __future__ import annotations

import hashlib
import io
import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from plane.bgtasks import derive_preview as dp
from plane.bgtasks.asset_cleanup import mark_abandoned_uploads, purge_deleted_assets
from plane.db.models import (
    FileAsset,
    FileVersion,
    Project,
    ProjectMember,
    ProjectRole,
    UploadSession,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)
from plane.db.services import file_library as flib
from plane.db.services import upload_session as usvc

pytestmark = pytest.mark.django_db

# ── mock 目标（plane.storage.minio 直引）────────────────────────────
MPU_CREATE = "plane.storage.minio.create_multipart_upload"
MPU_PART_URL = "plane.storage.minio.presigned_upload_part_url"
MPU_LIST = "plane.storage.minio.list_parts"
MPU_COMPLETE = "plane.storage.minio.complete_multipart_upload"
MPU_ABORT = "plane.storage.minio.abort_multipart_upload"
PUT_URL = "plane.storage.minio.presigned_put_url"
GET_URL = "plane.storage.minio.presigned_get_url"
HEAD_SIZE = "plane.storage.minio.head_object_size"
PUT_OBJ = "plane.storage.minio.put_object"
GET_BYTES = "plane.storage.minio.get_object_bytes"
REMOVE_OBJ = "plane.storage.minio.remove_object"
DISPATCH = "plane.bgtasks.event_publisher.dispatch_event"

CHUNK = usvc.CHUNK_SIZE


# ────────────────────────────────────────────────────────────────
# 夹具
# ────────────────────────────────────────────────────────────────
@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="f3-owner@rabbit.dev", password="Rabbit123!",
                                     display_name="库长")
    admin = User.objects.create_user(email="f3-admin@rabbit.dev", password="Rabbit123!",
                                     display_name="管理员")
    jia = User.objects.create_user(email="f3-jia@rabbit.dev", password="Rabbit123!",
                                   display_name="贡献者甲")
    yi = User.objects.create_user(email="f3-yi@rabbit.dev", password="Rabbit123!",
                                  display_name="贡献者乙")
    viewer = User.objects.create_user(email="f3-viewer@rabbit.dev", password="Rabbit123!",
                                      display_name="只读者")
    commenter = User.objects.create_user(email="f3-cm@rabbit.dev", password="Rabbit123!",
                                         display_name="评论者")
    ws = Workspace.objects.create(name="W", slug=f"w-f3-{owner.id.hex[:8]}",
                                  owner=owner, created_by=owner)
    for u, ws_role in ((owner, WorkspaceRole.OWNER), (admin, WorkspaceRole.MEMBER),
                       (jia, WorkspaceRole.MEMBER), (yi, WorkspaceRole.MEMBER),
                       (viewer, WorkspaceRole.MEMBER), (commenter, WorkspaceRole.MEMBER)):
        WorkspaceMember.objects.create(workspace=ws, member=u, role=ws_role, created_by=owner)
    proj = Project.objects.create(name="P", identifier="F3", workspace=ws, created_by=owner)
    for u, role in ((admin, ProjectRole.ADMIN), (jia, ProjectRole.CONTRIBUTOR),
                    (yi, ProjectRole.CONTRIBUTOR), (viewer, ProjectRole.VIEWER),
                    (commenter, ProjectRole.COMMENTER)):
        ProjectMember.objects.create(project=proj, member=u, role=role, created_by=owner)
    folder = flib.create_folder(project=proj, actor=owner, name="分片目录", parent_id=None)
    folder2 = flib.create_folder(project=proj, actor=owner, name="第二目录", parent_id=None)
    return {"owner": owner, "admin": admin, "jia": jia, "yi": yi, "viewer": viewer,
            "commenter": commenter, "ws": ws, "proj": proj,
            "folder": folder, "folder2": folder2}


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


def _sessions_url(env):
    return f"{_base(env)}/upload-sessions/"


def _session_url(env, sid):
    return f"{_base(env)}/upload-sessions/{sid}/"


def _chunk_url(env, sid, n):
    return f"{_base(env)}/upload-sessions/{sid}/chunks/{n}/"


def _complete_url(env, sid):
    return f"{_base(env)}/upload-sessions/{sid}/complete/"


def _versions_url(env, aid):
    return f"{_base(env)}/files/{aid}/versions/"


def _rollback_url(env, aid, vid):
    return f"{_base(env)}/files/{aid}/versions/{vid}/rollback/"


def _content_url(env, aid, vid):
    return f"{_base(env)}/files/{aid}/versions/{vid}/content/"


def _preview_url(env, aid):
    return f"{_base(env)}/files/{aid}/preview/"


def _deriv_url(env, aid, kind):
    return f"{_base(env)}/files/{aid}/derivatives/{kind}/"


def _init_payload(env, name="大文件.zip", size=CHUNK * 3, md5=None, folder=None):
    payload = {
        "file_name": name,
        "file_size": size,
        "content_type": "application/octet-stream",
        "folder_id": str((folder or env["folder"]).id),
    }
    if md5:
        payload["content_md5"] = md5
    return payload


def _fake_mpu_parts(session, parts_map: dict[int, str] | None = None):
    """构造 ListParts 返回：默认按 session.uploaded_chunks 全量回放。"""
    chunks = session.uploaded_chunks or []
    if parts_map is None:
        parts_map = {c["n"]: c["etag"] for c in chunks}

    def _list(*, bucket, key, upload_id):
        return [
            {"PartNumber": n, "ETag": e, "Size": CHUNK}
            for n, e in sorted(parts_map.items())
        ]

    return _list


def _do_init(env, client, payload):
    with patch(MPU_CREATE, return_value="mpu-test-1"):
        resp = client.post(_sessions_url(env), payload)
    assert resp.status_code == 201, resp.content
    return resp.json()["data"]


def _upload_all_chunks(env, client, session_id, total, etags: dict[int, str]):
    for n in range(1, total + 1):
        with patch(MPU_PART_URL, return_value=f"http://minio/part/{n}"):
            r = client.post(_chunk_url(env, session_id, n))
        assert r.status_code == 200, r.content
        r = client.patch(_chunk_url(env, session_id, n), {"etag": etags[n]})
        assert r.status_code == 200, r.content


def _chunk_md5s(size: int) -> tuple[dict[int, str], str]:
    """按 8MB 分片计算各片 MD5 与整件 MD5（流式，不求全读内存——IT-01 测试侧口径）。"""
    etags: dict[int, str] = {}
    whole = hashlib.md5()
    n = 0
    block = b"x" * (256 * 1024)
    remain = size
    while remain > 0:
        n += 1
        take = min(CHUNK, remain)
        part = hashlib.md5()
        got = 0
        while got < take:
            step = min(len(block), take - got)
            part.update(block[:step])
            whole.update(block[:step])
            got += step
        etags[n] = part.hexdigest()
        remain -= take
    return etags, whole.hexdigest()


def _mk_versioned_asset(env, *, name="v链.zip", versions=1, key_prefix="k"):
    """直造 uploaded 资产 + N 个版本（服务层直连，便于淘汰/回滚量程用例）。"""
    asset = FileAsset.objects.create(
        workspace=env["ws"], project=env["proj"],
        entity_type=FileAsset.EntityType.PROJECT_FILE,
        entity_id=env["folder"].id, folder=env["folder"],
        attributes={"name": name, "size": 100, "mime": "application/octet-stream", "ext": ".bin"},
        size=100, storage_path=f"{key_prefix}-0", status=FileAsset.Status.UPLOADED,
        is_uploaded=True, uploaded_by=env["jia"], created_by=env["jia"],
    )
    from types import SimpleNamespace

    for i in range(1, versions + 1):
        usvc._new_version(
            asset,
            SimpleNamespace(file_name=name, file_size=100 + i,
                            content_type="application/octet-stream",
                            content_md5=None, created_by=env["jia"]),
            key=f"{key_prefix}-{i}", actor=env["jia"],
        )
    asset.refresh_from_db()  # _new_version 内部重取行写镜像——外层实例需刷新
    return asset


# ────────────────────────────────────────────────────────────────
# UT-01 阈值分流 / UT-02 片数计算
# ────────────────────────────────────────────────────────────────
def test_ut01_threshold_split(env):
    """>50MB 强制分片：分片会话接受 51MB；直传 presign 同尺寸拒收（BR-01 对称面）。"""
    c = _Client(env["jia"])
    data = _do_init(env, c, _init_payload(env, "51mb.zip", 51 * 1024 * 1024))
    assert data["total_chunks"] == 7
    # 直传侧：FILE-002 presign 拒收 >50MB
    with pytest.raises(Exception) as ei:
        with patch(PUT_URL, return_value="http://minio/put"):
            flib.presign_file(
                folder=env["folder"],
                payload={"file_name": "51mb.zip", "file_size": 51 * 1024 * 1024,
                         "content_type": "application/octet-stream"},
                actor=env["jia"],
            )
    assert ei.value.error_code == "VALIDATION_FILE_SIZE_EXCEEDED"


def test_ut01b_over_5gb_rejected(env):
    c = _Client(env["jia"])
    resp = c.post(_sessions_url(env), _init_payload(env, "huge.zip", 5 * 1024**3 + 1))
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_FILE_SIZE_EXCEEDED"


def test_ut02_chunk_count(env):
    """2GiB 整除 256 片（末片=8MB）；2.1GiB 2254857830B → 269 片（末片 6710886B）。"""
    assert usvc.total_chunks_of(2 * 1024**3) == 256
    assert usvc.total_chunks_of(2 * 1024**3) * CHUNK == 2 * 1024**3
    size = 2254857830
    assert usvc.total_chunks_of(size) == 269
    assert size - 268 * CHUNK == 6710886  # 末片


# ────────────────────────────────────────────────────────────────
# UT-03 断点续传 / UT-04 MD5 校验 / UT-05 complete 缺片
# ────────────────────────────────────────────────────────────────
def test_ut03_resume_from_breakpoint(env):
    c = _Client(env["jia"])
    etags, _ = _chunk_md5s(CHUNK * 3)
    data = _do_init(env, c, _init_payload(env, "resume.zip", CHUNK * 3))
    sid = data["session_id"]
    _upload_all_chunks(env, c, sid, 2, etags)  # 中断：仅传 2/3 片
    status = c.get(_session_url(env, sid)).json()["data"]
    assert status["uploaded_chunks"] == [1, 2]  # 断点片表可回传（不重传已传片）

    session = UploadSession.objects.get(pk=sid)
    with patch(MPU_LIST, side_effect=_fake_mpu_parts(session, {1: etags[1], 2: etags[2]})):
        resp = c.post(_complete_url(env, sid))
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_FILE_UPLOAD_MISMATCH"
    assert json.dumps(resp.json()["error"]["details"]).find("3") != -1  # 缺片 3 入清单


def test_ut04_md5_mismatch_rejected(env):
    c = _Client(env["jia"])
    etags, _ = _chunk_md5s(CHUNK * 2)
    data = _do_init(env, c, _init_payload(env, "tamper.zip", CHUNK * 2))
    sid = data["session_id"]
    with patch(MPU_PART_URL, return_value="http://minio/p1"):
        assert c.post(_chunk_url(env, sid, 1)).status_code == 200
    # 篡改片：前端算得 md5 与 MinIO ETag 不符 → 400 INVALID（该片重传由前端 ≤3 次）
    resp = c.patch(_chunk_url(env, sid, 1),
                   {"etag": etags[1], "md5": "0" * 32})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    session = UploadSession.objects.get(pk=sid)
    assert session.uploaded_chunks == []  # 未登记

    resp = c.patch(_chunk_url(env, sid, 1), {"etag": etags[1], "md5": etags[1]})
    assert resp.status_code == 200  # 正常登记


def test_ut05_complete_missing_and_mismatch(env):
    c = _Client(env["jia"])
    etags, _ = _chunk_md5s(CHUNK * 3)
    data = _do_init(env, c, _init_payload(env, "miss.zip", CHUNK * 3))
    sid = data["session_id"]
    _upload_all_chunks(env, c, sid, 3, etags)
    session = UploadSession.objects.get(pk=sid)
    # 场景 1：MinIO 缺片 2（登记 3 片）
    with patch(MPU_LIST, side_effect=_fake_mpu_parts(session, {1: etags[1], 3: etags[3]})), \
         patch(MPU_COMPLETE) as m_complete:
        resp = c.post(_complete_url(env, sid))
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_FILE_UPLOAD_MISMATCH"
    m_complete.assert_not_called()  # 核对不过不合并不落库
    # 场景 2：片在但 ETag 与登记值不符（片 3 被覆盖为异值）
    with patch(MPU_LIST, side_effect=_fake_mpu_parts(
            session, {1: etags[1], 2: etags[2], 3: "f" * 32})):
        resp = c.post(_complete_url(env, sid))
    assert resp.status_code == 400


# ────────────────────────────────────────────────────────────────
# UT-06 会话过期（24h TTL → Abort + expired + 配额释放）
# ────────────────────────────────────────────────────────────────
def test_ut06_session_expiry(env):
    c = _Client(env["jia"])
    size = CHUNK * 2
    data = _do_init(env, c, _init_payload(env, "ttl.zip", size))
    sid = data["session_id"]
    session = UploadSession.objects.get(pk=sid)
    # 在途预留以会话行计（扩展口径）：init 后 pending == size
    assert flib._inflight_pending(env["ws"].id) == size

    UploadSession.objects.filter(pk=sid).update(
        created_at=timezone.now() - timedelta(hours=25)
    )
    with patch(MPU_ABORT) as m_abort:
        result = dp.expire_upload_sessions.run(restrict_workspace_id=env["ws"].id)
    assert result["expired"] == 1
    m_abort.assert_called_once()
    session.refresh_from_db()
    assert session.status == UploadSession.Status.EXPIRED
    # BR-05 + §1.6 扩展口径：会话行出 Σ 活跃会话；新名 uploading 资产行此时
    # 回归「无会话直传在途」侧（30min 孤儿扫描管辖，IT-09 同构）——预留转记不释放
    assert flib._inflight_pending(env["ws"].id) == size
    FileAsset.all_objects.filter(pk=session.asset_id).update(
        status=FileAsset.Status.ABANDONED,
        created_at=timezone.now() - timedelta(minutes=31),
    )
    assert flib._inflight_pending(env["ws"].id) == 0  # abandoned 后全额释放

    # 过期会话复用 → 409 RESOURCE_STATE_INVALID（§2.5）
    resp = c.post(_complete_url(env, sid))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "RESOURCE_STATE_INVALID"


# ────────────────────────────────────────────────────────────────
# UT-07/UT-08 同名并入 / 大小写不敏感；UT-18 首会话完成后同名再传
# ────────────────────────────────────────────────────────────────
def _complete_one(env, c, name, size, folder=None, actor_etags=None):
    etags, whole = _chunk_md5s(size)
    payload = _init_payload(env, name, size, md5=whole, folder=folder)
    data = _do_init(env, c, payload)
    sid = data["session_id"]
    _upload_all_chunks(env, c, sid, data["total_chunks"], etags)
    session = UploadSession.objects.get(pk=sid)
    with patch(MPU_LIST, side_effect=_fake_mpu_parts(session)), \
         patch(MPU_COMPLETE), patch(MPU_ABORT):
        resp = c.post(_complete_url(env, sid))
    assert resp.status_code == 201, resp.content
    return resp.json()["data"], whole


def test_ut07_same_name_merges(env):
    c = _Client(env["jia"])
    first, _ = _complete_one(env, c, "首页改版.zip", 100)
    aid = first["file"]["id"]
    second, _ = _complete_one(env, c, "首页改版.zip", 200)
    assert second["file"]["id"] == aid  # 无新行，并入既有
    assert FileAsset.objects.filter(pk=aid).count() == 1
    assert FileVersion.objects.filter(asset_id=aid).count() == 2  # 版本 +1
    asset = FileAsset.objects.get(pk=aid)
    assert asset.current_version.version_number == 2
    assert asset.size == 200  # 行级镜像前移


def test_ut08_case_insensitive_match(env):
    c = _Client(env["jia"])
    first, _ = _complete_one(env, c, "report.docx", 100)
    aid = first["file"]["id"]
    _complete_one(env, c, "REPORT.DOCX", 100)
    assert FileVersion.objects.filter(asset_id=aid).count() == 2  # 大小写不敏感并入


def test_ut18_second_session_after_completed(env):
    """BR-15：首会话 completed 后同名再传——新会话创建无唯一约束冲突，版本 +1。"""
    c = _Client(env["jia"])
    first, _ = _complete_one(env, c, "again.zip", 100)
    aid = first["file"]["id"]
    data, _ = _complete_one(env, c, "again.zip", 100)
    assert data["file"]["id"] == aid
    assert FileVersion.objects.filter(asset_id=aid).count() == 2
    active = UploadSession.objects.filter(asset_id=aid, status=UploadSession.Status.UPLOADING)
    assert active.count() == 0  # 历史会话不阻塞（会话是账本）


def test_br15_active_session_conflict(env):
    """同文件并发第二会话（首会话在途）→ 409 RESOURCE_ALREADY_EXISTS / UNIQUE。"""
    c = _Client(env["jia"])
    _do_init(env, c, _init_payload(env, "conflict.zip", 200))
    resp = c.post(_sessions_url(env), _init_payload(env, "conflict.zip", 200))
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "RESOURCE_ALREADY_EXISTS"
    assert err["details"][0]["code"] == "UNIQUE"


# ────────────────────────────────────────────────────────────────
# UT-09 回滚零拷贝 / UT-10 淘汰 / UT-11 引用保护 / UT-17 淘汰后版本号
# ────────────────────────────────────────────────────────────────
def test_ut09_rollback_zero_copy(env):
    c = _Client(env["jia"])
    asset = _mk_versioned_asset(env, versions=3)
    v1 = FileVersion.objects.get(asset=asset, version_number=1)
    v3 = FileVersion.objects.get(asset=asset, version_number=3)
    resp = c.post(_rollback_url(env, asset.id, v1.id))
    assert resp.status_code == 201, resp.content
    body = resp.json()["data"]
    assert body["source_version_number"] == 1
    asset.refresh_from_db()
    assert FileVersion.objects.filter(asset=asset).count() == 4  # v3 保留（只增账本）
    v4 = FileVersion.objects.get(asset=asset, version_number=4)
    assert v4.object_key == v1.object_key  # 零拷贝复用 v1 对象
    assert asset.storage_path == v1.object_key  # 列表镜像更新（E2E-03）
    assert v3.deleted_at is None


def test_ut10_evict_oldest_non_current(env):
    asset = _mk_versioned_asset(env, versions=21)
    with patch(REMOVE_OBJ) as m_rm:
        evicted = usvc.evict_old_versions(asset.id)
    assert evicted == 1
    live = FileVersion.objects.filter(asset=asset, deleted_at__isnull=True)
    assert live.count() == 20
    assert not live.filter(version_number=1).exists()  # 最旧非当前淘汰
    assert live.filter(version_number=21).exists()  # 当前保留
    m_rm.assert_called_once()  # 淘汰对象无他引用 → 物理删


def test_ut11_eviction_reference_guard(env):
    """对象被回滚链引用（v5 复用 v1 键）→ 淘汰 v1 不删对象。"""
    asset = _mk_versioned_asset(env, versions=4)
    v1 = FileVersion.objects.get(asset=asset, version_number=1)
    usvc.rollback(asset=asset, target=v1, actor=env["jia"])  # v5 复用 v1 键
    # 手工把 v1 顶出 20 版窗口（造 17 个新版本）
    from types import SimpleNamespace

    for i in range(17):
        usvc._new_version(
            asset,
            SimpleNamespace(file_name="v链.zip", file_size=1,
                            content_type="application/octet-stream",
                            content_md5=None, created_by=env["jia"]),
            key=f"guard-{i}", actor=env["jia"],
        )
    with patch(REMOVE_OBJ) as m_rm:
        usvc.evict_old_versions(asset.id)
    removed_keys = [call.kwargs["key"] for call in m_rm.call_args_list]
    assert v1.object_key not in removed_keys  # BR-08：仍被 v5（当前）引用 → 不删
    assert removed_keys  # 其余无引用键正常删


def test_ut17_version_number_after_eviction(env):
    """淘汰最旧后再传：新版本号 = max+1，不与在册行撞 uniq_version_per_asset。"""
    asset = _mk_versioned_asset(env, versions=21)
    usvc.evict_old_versions(asset.id)
    from types import SimpleNamespace

    v = usvc._new_version(
        asset,
        SimpleNamespace(file_name="v链.zip", file_size=1,
                        content_type="application/octet-stream",
                        content_md5=None, created_by=env["jia"]),
        key="post-evict", actor=env["jia"],
    )
    assert v.version_number == 22  # max(21)+1，不复用被淘汰的 1


# ────────────────────────────────────────────────────────────────
# UT-12 配额去重 / 会话在途口径（突变锚：口径错即红）
# ────────────────────────────────────────────────────────────────
def test_ut12_quota_distinct_and_session_inflight(env):
    asset = _mk_versioned_asset(env, versions=1, key_prefix="q")
    v1 = FileVersion.objects.get(asset=asset, version_number=1)
    # 回滚产生零拷贝 v2（同键）——同对象多版本只计一次（BR-14）
    usvc.rollback(asset=asset, target=v1, actor=env["jia"])
    usage = flib.workspace_storage_usage(env["ws"].id)
    assert usage["used_bytes"] == v1.attributes["size"]  # 一键一计

    # 会话在途：新名分片行（uploading）+ 活跃会话 → 预留以会话行为准、不双计
    c = _Client(env["jia"])
    data = _do_init(env, c, _init_payload(env, "inflight.zip", CHUNK * 2, folder=env["folder2"]))
    sid = data["session_id"]
    session = UploadSession.objects.get(pk=sid)
    assert session.asset.status == FileAsset.Status.UPLOADING
    pending = flib._inflight_pending(env["ws"].id)
    assert pending == CHUNK * 2  # 恰一笔（会话行），资产行侧排除防同笔双计


# ────────────────────────────────────────────────────────────────
# UT-13 预览权限 / UT-14 文本上限
# ────────────────────────────────────────────────────────────────
def test_ut13_preview_visibility_hidden(env):
    asset = _mk_versioned_asset(env, versions=1)
    FileAsset.objects.filter(pk=asset.id).update(visibility="admins")
    c = _Client(env["jia"])  # CONTRIBUTOR 非管理员 → admins 态不可见
    assert c.get(_preview_url(env, asset.id)).status_code == 404
    assert c.get(_versions_url(env, asset.id)).status_code == 404
    assert _Client(env["admin"]).get(_preview_url(env, asset.id)).status_code == 200


def test_ut14_text_over_2mb_fallback(env):
    asset = _mk_versioned_asset(env, versions=1, key_prefix="t")
    v = asset.current_version
    v.attributes = {**v.attributes, "name": "大日志.log", "mime": "text/plain",
                    "ext": ".log", "size": 2 * 1024 * 1024 + 1}
    v.save(update_fields=["attributes"])
    status_code, data = usvc.preview_dispatch(asset=FileAsset.objects.get(pk=asset.id))
    assert status_code == 200
    assert data["kind"] == "text" and data["ready"] is False
    assert data["state"] == "too_large" and data["fallback_download"] is True
    # 边界内（2MB）→ ready + 正文换发路径
    v.attributes = {**v.attributes, "size": 2 * 1024 * 1024}
    v.save(update_fields=["attributes"])
    _, data = usvc.preview_dispatch(asset=FileAsset.objects.get(pk=asset.id))
    assert data["ready"] is True and data["preview_url"].endswith("/content/")


# ────────────────────────────────────────────────────────────────
# UT-15 转码失败（工具缺失语义）/ 重试入口
# ────────────────────────────────────────────────────────────────
def test_ut15_transcode_tool_missing_failed_and_retry(env):
    asset = _mk_versioned_asset(env, versions=1)
    v = asset.current_version
    v.attributes = {**v.attributes, "name": "文档.docx", "mime":
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "ext": ".docx", "size": 1024}
    v.save(update_fields=["attributes"])
    usvc.ensure_derivative(v, "preview")
    assert v.attributes["derivatives"]["preview"]["status"] == "pending"

    def _fake_download(*, bucket, key, dest, chunk=CHUNK):
        from pathlib import Path as P

        P(dest).write_bytes(b"fake-docx")

    with patch.object(dp, "SOFFICE_BIN", None), \
         patch.object(dp, "_download_streaming", side_effect=_fake_download), \
         patch(PUT_OBJ) as m_put, patch(DISPATCH):
        result = dp.derive_preview.run(str(v.id))
    assert result["failed"] == "soffice"
    v.refresh_from_db()
    assert v.attributes["derivatives"]["preview"]["status"] == "failed"
    m_put.assert_not_called()  # 无产物写回；原文件无损（remove 零调用）

    # 重试入口：失败后再次预览 → 重新排队（§3.4「重试」）
    state = usvc.ensure_derivative(v, "preview")
    assert state == "pending"
    v.refresh_from_db()
    assert v.attributes["derivatives"]["preview"]["status"] == "pending"


def test_ut15b_transcode_office_too_large(env):
    asset = _mk_versioned_asset(env, versions=1)
    v = asset.current_version
    v.attributes = {**v.attributes, "name": "巨.docx", "ext": ".docx", "size": 101 * 1024 * 1024}
    v.save(update_fields=["attributes"])
    _, data = usvc.preview_dispatch(asset=asset)
    assert data["state"] == "unsupported" and data["fallback_download"] is True


# ────────────────────────────────────────────────────────────────
# UT-16 衍生物冷清理（31 天未访问）
# ────────────────────────────────────────────────────────────────
def test_ut16_cold_derivative_sweep(env):
    asset = _mk_versioned_asset(env, versions=1)
    v = asset.current_version
    stale = (timezone.now() - timedelta(days=31)).isoformat()
    usvc.mark_derivative(v, "thumbnail", status="ready")
    attrs = dict(v.attributes)
    attrs["derivatives"]["thumbnail"].update(
        {"key": f"derivatives/{asset.id}/{v.id}/thumb.webp", "last_access_at": stale})
    v.attributes = attrs
    v.save(update_fields=["attributes"])
    with patch(REMOVE_OBJ) as m_rm:
        result = dp.sweep_cold_derivatives.run(restrict_workspace_id=env["ws"].id)
    assert result["swept"] == 1
    m_rm.assert_called_once()
    v.refresh_from_db()
    assert "thumbnail" not in (v.attributes.get("derivatives") or {})  # 登记随对象清
    # 冷清理后按需重生成（BR-11）
    assert usvc.ensure_derivative(v, "thumbnail") == "pending"


# ────────────────────────────────────────────────────────────────
# IT-01 分片全链路（mock S3 multipart 全协议）
# ────────────────────────────────────────────────────────────────
def test_it01_full_chain(env):
    size = CHUNK * 3 + 12345  # 末片非整
    etags, whole_md5 = _chunk_md5s(size)
    assert whole_md5 == hashlib.md5(b"x" * size).hexdigest()  # 测试侧口径自检
    c = _Client(env["jia"])
    payload = _init_payload(env, "索引整库导出.mp4", size, md5=whole_md5)
    payload["content_type"] = "video/mp4"
    data = _do_init(env, c, payload)
    sid = data["session_id"]
    assert data["total_chunks"] == 4
    _upload_all_chunks(env, c, sid, 4, etags)
    session = UploadSession.objects.get(pk=sid)
    # 合并对象 MD5（演示侧等价校验）：分片拼接的 MD5 == 声明整件 MD5
    assert hashlib.md5(b"x" * size).hexdigest() == session.content_md5
    with patch(MPU_LIST, side_effect=_fake_mpu_parts(session)), \
         patch(MPU_COMPLETE) as m_complete:
        resp = c.post(_complete_url(env, sid))
    assert resp.status_code == 201, resp.content
    m_complete.assert_called_once()
    parts_arg = m_complete.call_args.kwargs["parts"]
    assert [p["PartNumber"] for p in parts_arg] == [1, 2, 3, 4]  # 按登记序号升序合并
    body = resp.json()["data"]
    asset = FileAsset.objects.get(pk=body["file"]["id"])
    assert asset.status == FileAsset.Status.UPLOADED  # 五态收口
    v1 = asset.current_version
    assert v1.version_number == 1 and v1.uploaded_by_id == env["jia"].id
    assert v1.attributes["md5"] == whole_md5  # 整件 MD5 落版本元数据（§4.2.1）
    assert v1.attributes["size"] == size
    session.refresh_from_db()
    assert session.status == UploadSession.Status.COMPLETED


# ────────────────────────────────────────────────────────────────
# IT-02 会话并发上限（第 4 个 → 拒绝排队）
# ────────────────────────────────────────────────────────────────
def test_it02_active_session_limit(env):
    c = _Client(env["jia"])
    for i in range(3):
        _do_init(env, c, _init_payload(env, f"s{i}.zip", 100, folder=env["folder2"]))
    resp = c.post(_sessions_url(env), _init_payload(env, "s3.zip", 100))
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "RESOURCE_LIMIT_EXCEEDED"
    assert err["details"][0]["code"] == "LIMIT"
    # 其他用户不受该用户上限影响
    with patch(MPU_CREATE, return_value="mpu-yi-1"):
        assert _Client(env["yi"]).post(
            _sessions_url(env), _init_payload(env, "other.zip", 100)
        ).status_code == 201


# ────────────────────────────────────────────────────────────────
# IT-03 版本链完整（3 版 + 回滚 v1 → 4 行；当前内容 = v1）
# ────────────────────────────────────────────────────────────────
def test_it03_version_chain(env):
    c = _Client(env["jia"])
    a1, _ = _complete_one(env, c, "链.zip", 100)
    aid = a1["file"]["id"]
    _complete_one(env, c, "链.zip", 200)
    _complete_one(env, c, "链.zip", 300)
    assert FileVersion.objects.filter(asset_id=aid).count() == 3
    v1 = FileVersion.objects.get(asset_id=aid, version_number=1)
    resp = c.get(_versions_url(env, aid))
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert [r["version_number"] for r in rows] == [3, 2, 1]  # 新 → 旧
    assert rows[0]["is_current"] is True
    c.post(_rollback_url(env, aid, v1.id))
    assert FileVersion.objects.filter(asset_id=aid).count() == 4
    asset = FileAsset.objects.get(pk=aid)
    assert asset.storage_path == v1.object_key  # 当前内容 = v1（镜像不变量）
    assert asset.attributes["size"] == v1.attributes["size"]


# ────────────────────────────────────────────────────────────────
# IT-04 Office 直传量程（≤50MB 三步 → complete 版本接线 → 预览 202 排队）
# ────────────────────────────────────────────────────────────────
def test_it04_direct_upload_wires_version_and_queue(env):
    c = _Client(env["jia"])
    with patch(PUT_URL, return_value="http://minio/put"):
        resp = c.post(f"{_base(env)}/folders/{env['folder'].id}/files/presign/", {
            "file_name": "设计稿.docx", "file_size": 5 * 1024 * 1024,
            "content_type": "application/docx",
        })
    assert resp.status_code == 201, resp.content
    asset_id = resp.json()["data"]["asset_id"]
    with patch(HEAD_SIZE, return_value=5 * 1024 * 1024):
        resp = c.post(f"{_base(env)}/files/{asset_id}/complete/")
    assert resp.status_code == 200, resp.content
    asset = FileAsset.objects.get(pk=asset_id)
    assert asset.status == FileAsset.Status.UPLOADED
    assert asset.current_version is not None  # 直传 complete 落 v1 版本行（§4.3.4）
    assert asset.current_version.version_number == 1
    assert asset.current_version.attributes["size"] == 5 * 1024 * 1024  # HEAD 实测为准
    # 同名直传二次（8.2MB 量程，BR-07 complete 侧收口）→ v2，暂存行硬删（E2E-02）
    with patch(PUT_URL, return_value="http://minio/put"):
        resp = c.post(f"{_base(env)}/folders/{env['folder'].id}/files/presign/", {
            "file_name": "设计稿.docx", "file_size": 8_200_000, "content_type": "x/y"})
    staging_id = resp.json()["data"]["asset_id"]
    with patch(HEAD_SIZE, return_value=8_200_000):
        c.post(f"{_base(env)}/files/{staging_id}/complete/")
    assert not FileAsset.all_objects.filter(pk=staging_id).exists()  # 暂存行硬删
    assert FileVersion.objects.filter(asset=asset).count() == 2
    asset.refresh_from_db()
    assert asset.current_version.attributes["size"] == 8_200_000
    # 预览：Office 无产物 → 202 排队（derive 入队）
    resp = c.get(_preview_url(env, asset.id))
    assert resp.status_code == 202
    data = resp.json()["data"]
    assert data["state"] == "transcoding" and data["eta_seconds"] > 0


# ────────────────────────────────────────────────────────────────
# IT-05 视频封面（mock ffmpeg 成功路径 + 事件投递）
# ────────────────────────────────────────────────────────────────
def test_it05_video_poster_and_event(env):
    asset = _mk_versioned_asset(env, versions=1)
    v = asset.current_version
    v.attributes = {**v.attributes, "name": "clip.mp4", "mime": "video/mp4",
                    "ext": ".mp4", "size": 1024}
    v.save(update_fields=["attributes"])

    def _fake_download(*, bucket, key, dest, chunk=CHUNK):
        from pathlib import Path as P

        P(dest).write_bytes(b"fake-mp4")

    def _fake_run_tool(args, *, cwd=None):
        from pathlib import Path as P

        poster = P(args[-1])  # 视频分支不传 cwd——由命令末位产物路径推导
        poster.write_bytes(b"fake-jpg")  # ffmpeg「产出」封面帧
        return poster.parent

    status_code, data = usvc.preview_dispatch(asset=asset)
    assert status_code == 200 and data["kind"] == "video"
    assert data["poster_state"] == "pending"  # 封面未就绪不阻塞播放
    assert data["preview_url"].endswith("/content/")
    with patch.object(dp, "_download_streaming", side_effect=_fake_download), \
         patch.object(dp, "_run_tool", side_effect=_fake_run_tool), \
         patch(PUT_OBJ) as m_put, patch(DISPATCH) as m_dispatch:
        result = dp.derive_preview.run(str(v.id))
    assert result["derived"] == "poster"
    m_put.assert_called_once()
    assert m_put.call_args.kwargs["content_type"] == "image/jpeg"
    v.refresh_from_db()
    assert v.attributes["derivatives"]["poster"]["status"] == "ready"
    # file.transcode.completed 事件（§4.4：转码成功）
    m_dispatch.assert_called_once()
    assert m_dispatch.call_args.args[0] == "file.transcode.completed"
    # 非流式视频（.mov）→ 400 引导下载
    v.attributes = {**v.attributes, "name": "clip.mov", "ext": ".mov"}
    v.save(update_fields=["attributes"])
    resp = _Client(env["jia"]).get(_preview_url(env, asset.id))
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_INVALID_PARAM"


# ────────────────────────────────────────────────────────────────
# IT-06 动态事件（on_commit → file.version.created / 回滚携带 source）
# ────────────────────────────────────────────────────────────────
def test_it06_version_events(env, django_capture_on_commit_callbacks):
    c = _Client(env["jia"])
    with patch(DISPATCH) as m_dispatch, \
         django_capture_on_commit_callbacks(execute=True):
        etags, whole = _chunk_md5s(100)
        data = _do_init(env, c, _init_payload(env, "事件.zip", 100, md5=whole))
        sid = data["session_id"]
        _upload_all_chunks(env, c, sid, 1, etags)
        session = UploadSession.objects.get(pk=sid)
        with patch(MPU_LIST, side_effect=_fake_mpu_parts(session)), \
             patch(MPU_COMPLETE), patch(MPU_ABORT):
            assert c.post(_complete_url(env, sid)).status_code == 201
    upload_events = [call for call in m_dispatch.call_args_list
                     if call.args[0] == "file.version.created"]
    assert len(upload_events) == 1
    payload = upload_events[0].args[1]
    assert payload["version_number"] == 1
    assert payload["actor_id"] == str(env["jia"].id)
    assert upload_events[0].args[2] == [f"project:{env['proj'].id}",
                                        f"file:{payload['asset_id']}"]
    # 回滚 → 第二类事件（source_version_number 携带）
    asset = FileAsset.objects.get(pk=payload["asset_id"])
    v1 = asset.current_version
    with patch(DISPATCH) as m_dispatch2, \
         django_capture_on_commit_callbacks(execute=True):
        c.post(_rollback_url(env, asset.id, v1.id))
    rb = [call for call in m_dispatch2.call_args_list
          if call.args[0] == "file.version.created"]
    assert len(rb) == 1
    assert rb[0].args[1]["source_version_number"] == 1


# ────────────────────────────────────────────────────────────────
# IT-07 回收站与版本（删含 4 版文件 → 期满清理，逐键引用判定）
# ────────────────────────────────────────────────────────────────
def test_it07_purge_with_versions(env):
    asset = _mk_versioned_asset(env, versions=4)  # 4 版 4 键
    keys = set(FileVersion.objects.filter(asset=asset)
               .values_list("object_key", flat=True))
    keys.add(asset.storage_path)
    flib.soft_delete_file(asset=asset, actor=env["admin"])
    FileAsset.all_objects.filter(pk=asset.id).update(
        deleted_at=timezone.now() - timedelta(days=31)
    )
    with patch(REMOVE_OBJ) as m_rm:
        result = purge_deleted_assets.run(restrict_workspace_id=env["ws"].id)
    assert result["purged"] == 1
    removed = {call.kwargs["key"] for call in m_rm.call_args_list}
    assert removed == keys  # 4 个版本对象 + 行键全清（无存活引用）
    assert not FileAsset.all_objects.filter(pk=asset.id).exists()
    assert not FileVersion.objects.filter(asset_id=asset.id).exists()  # 级联硬删


def test_it07b_rollback_key_not_deleted_twice(env):
    """回滚零拷贝：v5 复用 v1 键——purge 只删一次，且无他资产引用时一并清理。"""
    asset = _mk_versioned_asset(env, versions=2)
    v1 = FileVersion.objects.get(asset=asset, version_number=1)
    usvc.rollback(asset=asset, target=v1, actor=env["jia"])
    flib.soft_delete_file(asset=asset, actor=env["admin"])
    FileAsset.all_objects.filter(pk=asset.id).update(
        deleted_at=timezone.now() - timedelta(days=31)
    )
    with patch(REMOVE_OBJ) as m_rm:
        purge_deleted_assets.run(restrict_workspace_id=env["ws"].id)
    keys = [call.kwargs["key"] for call in m_rm.call_args_list]
    assert keys.count(v1.object_key) == 1  # 同键只删一次（去重）


# ────────────────────────────────────────────────────────────────
# IT-08 端点角色矩阵与属主（BR-16 / §4.2.4）
# ────────────────────────────────────────────────────────────────
def test_it08_role_matrix_and_ownership(env):
    jia, yi = _Client(env["jia"]), _Client(env["yi"])
    # 甲持 1 条 uploading 会话（全部片已上传登记，complete 就绪）
    etags, _ = _chunk_md5s(CHUNK)
    data = _do_init(env, jia, _init_payload(env, "协作.zip", CHUNK))
    sid = data["session_id"]
    _upload_all_chunks(env, jia, sid, 1, etags)
    # 1 个 3 版本文件
    asset = _mk_versioned_asset(env, versions=3)
    v1 = FileVersion.objects.get(asset=asset, version_number=1)

    # VIEWER / COMMENTER：会话族 403 PERM_ROLE_INSUFFICIENT；rollback 403
    for client in (_Client(env["viewer"]), _Client(env["commenter"])):
        r1 = client.post(_sessions_url(env), _init_payload(env, "x.zip", 100))
        assert r1.status_code == 403
        assert r1.json()["error"]["code"] == "PERM_ROLE_INSUFFICIENT"
        r2 = client.get(_session_url(env, sid))
        assert r2.status_code == 403 and r2.json()["error"]["code"] == "PERM_ROLE_INSUFFICIENT"
        r3 = client.post(_rollback_url(env, asset.id, v1.id))
        assert r3.status_code == 403 and r3.json()["error"]["code"] == "PERM_ROLE_INSUFFICIENT"
        # versions / preview 200（受可见性过滤，本文件 all 态可见）
        assert client.get(_versions_url(env, asset.id)).status_code == 200
        assert client.get(_preview_url(env, asset.id)).status_code == 200

    # 甲：rollback 201；会话状态 200（属主）
    assert jia.post(_rollback_url(env, asset.id, v1.id)).status_code == 201
    assert jia.get(_session_url(env, sid)).status_code == 200
    # 乙非属主：status / abort 403 PERM_DENIED
    r = yi.get(_session_url(env, sid))
    assert r.status_code == 403 and r.json()["error"]["code"] == "PERM_DENIED"
    r = yi.delete(_session_url(env, sid))
    assert r.status_code == 403 and r.json()["error"]["code"] == "PERM_DENIED"
    # ADMIN：任意会话可读（属主校验豁免）
    assert _Client(env["admin"]).get(_session_url(env, sid)).status_code == 200
    # 乙调 complete：201（#5 免属主校验，协作续传语义）+ uploaded_by=乙留痕
    session = UploadSession.objects.get(pk=sid)
    with patch(MPU_LIST, side_effect=_fake_mpu_parts(session)), \
         patch(MPU_COMPLETE), patch(MPU_ABORT):
        resp = yi.post(_complete_url(env, sid))
    assert resp.status_code == 201, resp.content
    v_new = FileVersion.objects.get(
        asset_id=session.asset_id, version_number=1)
    assert v_new.uploaded_by_id == env["yi"].id  # 操作者留痕（§4.3.2）


# ────────────────────────────────────────────────────────────────
# IT-09 会话失效回归孤儿扫描（三段断言：豁免 → 回归 → 硬删）
# ────────────────────────────────────────────────────────────────
def _backdate(model, pk, **fields):
    model.all_objects.filter(pk=pk).update(**fields)


def test_it09_session_exemption_three_stages(env):
    ws_id = env["ws"].id
    # ① 新名 2GB 等价分片在途（uploading 资产行 + 活跃会话）超 30min
    jia = _Client(env["jia"])
    data = _do_init(env, jia, _init_payload(env, "2gb等价.zip", CHUNK * 2))
    sid = data["session_id"]
    session = UploadSession.objects.get(pk=sid)
    aid = session.asset_id
    old = timezone.now() - timedelta(minutes=40)
    _backdate(FileAsset, aid, created_at=old)
    UploadSession.objects.filter(pk=sid).update(created_at=old)
    # 对照组：无会话直传暂存行（presign 后弃传）
    with patch(PUT_URL, return_value="http://minio/put"):
        flib.presign_file(folder=env["folder2"],
                          payload={"file_name": "弃传.zip", "file_size": 100,
                                   "content_type": "application/octet-stream"},
                          actor=env["jia"])
    stray = FileAsset.objects.get(
        workspace_id=ws_id, folder=env["folder2"], attributes__name="弃传.zip"
    )
    _backdate(FileAsset, stray.id, created_at=old)

    # 第一段：30min 扫描——分片在途行豁免，无会话行标 abandoned
    marked = mark_abandoned_uploads.run(restrict_workspace_id=ws_id)
    assert marked == 1
    session.asset.refresh_from_db()
    assert session.asset.status == FileAsset.Status.UPLOADING  # NOT EXISTS 豁免
    stray.refresh_from_db()
    assert stray.status == FileAsset.Status.ABANDONED

    # 第二段：会话 abort（失效）→ uploading 资产行回归 30min 扫描
    with patch(MPU_ABORT):
        dp.expire_upload_sessions.run(restrict_workspace_id=ws_id) == {"expired": 0}
        # 40min 会话未到 24h——用 abort 端点语义（甲属主）
        assert jia.delete(_session_url(env, sid)).status_code == 204
    session.refresh_from_db()
    assert session.status == UploadSession.Status.ABORTED
    marked = mark_abandoned_uploads.run(restrict_workspace_id=ws_id)
    assert marked == 1
    session.asset.refresh_from_db()
    assert session.asset.status == FileAsset.Status.ABANDONED  # 回归管辖

    # 第三段：次日 purge——残片对象与行物理清理，无孤儿残留
    _backdate(FileAsset, session.asset_id, created_at=timezone.now() - timedelta(days=2))
    _backdate(FileAsset, stray.id, created_at=timezone.now() - timedelta(days=2))
    with patch(REMOVE_OBJ) as m_rm:
        result = purge_deleted_assets.run(restrict_workspace_id=ws_id)
    assert result["purged"] == 2
    assert {call.kwargs["key"] for call in m_rm.call_args_list} == {
        session.object_key, stray.storage_path}
    assert not FileAsset.all_objects.filter(
        pk__in=[session.asset_id, stray.id]).exists()
    # 会话行 SET_NULL 存续为历史账本（BR-15：不随资产硬删），asset 引用置空
    session_row = UploadSession.all_objects.get(pk=sid)
    assert session_row.status == UploadSession.Status.ABORTED
    assert session_row.asset_id is None


# ────────────────────────────────────────────────────────────────
# 补充：版本内容 / 衍生物换发 302 端点 + 权限实时校验（BR-10）
# ────────────────────────────────────────────────────────────────
def test_content_and_derivative_redirects(env):
    asset = _mk_versioned_asset(env, versions=1)
    v = asset.current_version
    c = _Client(env["jia"])
    with patch(GET_URL, return_value="http://minio:9000/rp-uploads/k1?X-Amz=1"):
        resp = c.get(_content_url(env, asset.id, v.id))
    assert resp.status_code == 302
    assert resp["Location"].startswith("/uploads/rp-uploads/")  # 同源反代改写（坑 13）

    usvc.mark_derivative(v, "thumbnail", status="ready")
    v.refresh_from_db()
    with patch(GET_URL, return_value="http://minio:9000/rp-uploads/thumb?X-Amz=1"):
        resp = c.get(_deriv_url(env, asset.id, "thumbnail"))
    assert resp.status_code == 302
    # 未就绪 kind → 404（前端排队态探针）；非法 kind → 404
    assert c.get(_deriv_url(env, asset.id, "poster")).status_code == 404
    assert c.get(_deriv_url(env, asset.id, "evil")).status_code == 404
    # 不可见资产换发 → 404 存在性隐藏（BR-10 实时校验）
    FileAsset.objects.filter(pk=asset.id).update(visibility="admins")
    assert c.get(_content_url(env, asset.id, v.id)).status_code == 404


def test_image_thumbnail_real_pil(env):
    """缩略通道真实 PIL 路径（本机无外部工具依赖）：PNG → ≤512px WebP 产物登记。"""
    from PIL import Image

    asset = _mk_versioned_asset(env, versions=1)
    v = asset.current_version
    v.attributes = {**v.attributes, "name": "图.png", "mime": "image/png", "ext": ".png"}
    v.save(update_fields=["attributes"])
    buf = io.BytesIO()
    Image.new("RGB", (1024, 768), (200, 30, 30)).save(buf, format="PNG")
    status_code, data = usvc.preview_dispatch(asset=asset)
    assert status_code == 202  # 未生成排队
    with patch(GET_BYTES, return_value=buf.getvalue()), \
         patch(PUT_OBJ) as m_put, patch(DISPATCH):
        result = dp.derive_preview.run(str(v.id))
    assert result["derived"] == "thumbnail"
    body = m_put.call_args.kwargs["body"]
    with Image.open(io.BytesIO(body)) as thumb:
        assert thumb.format == "WEBP"
        assert max(thumb.size) <= usvc.THUMB_MAX_PX  # ≤512px
    v.refresh_from_db()
    assert v.attributes["derivatives"]["thumbnail"]["status"] == "ready"
    _, data = usvc.preview_dispatch(asset=FileAsset.objects.get(pk=asset.id))
    assert data["preview_url"].endswith("/derivatives/thumbnail/")  # 无扩展名路径
