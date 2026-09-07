"""FILE-004 文件分享链接与权限管控测试（§5 用例表）。

影响面圈定（CodeGraph，CLAUDE.md 测试脚本规范首条）：
- ``preview_dispatch`` 加 ``anonymous`` 参数（默认 False）——既有调用方
  ``FilePreviewView`` 与 test_file_versions 预览用例不回归（全量门禁兜底）；
- ``event_publisher.EVENT_MAP`` 增三事件（纯增量，无既有键改动）；
- ``constants.permissions`` 增 ``file.share``（test_smoke 的 labels==keys 断言
  由本任务同步补 label 保持绿）；
- 新符号（FileShareLink/FileShareAccess/file_share 服务/space 三视图/
  sweep_expired_shares）全部圈入本文件。

范围划界：
- UT-01~21 全量；IT-01~09 为 pytest 可测子集（IT-02/03 的「S3 403 自然过期」
  与「已签发链接仍可用」以预签名窗口参数断言承载——真实 MinIO 过期交互归
  HTTP 侧脚本；IT-05 内外部隔离为匿名调内部端点 401）。
- 存储交互全部 mock（presigned_get_url），不触碰真实 MinIO。
- BR-07 限流用真实 Valkey（rp-redis）计数：键含随机 slug 天然隔离，
  夹具 teardown 显式清键（坑 18 同款纪律——不留跨用例状态）。
- 夹具纪律（坑 18）：断言一律 filter 到本测试作用域（share id / asset id /
  workspace），禁全表 count()。

夹具建 WorkspaceMember（CLAUDE.md 硬性纪律 3）。
"""
from __future__ import annotations

import re
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from plane.bgtasks.share_sweep import sweep_expired_shares
from plane.db.models import (
    FileAsset,
    FileShareAccess,
    FileShareLink,
    FileVersion,
    Project,
    ProjectMember,
    ProjectRole,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)
from plane.db.models.file_share import generate_share_slug
from plane.db.services import file_library as flib
from plane.db.services import file_share as svc

pytestmark = pytest.mark.django_db

GET_URL = "plane.storage.minio.presigned_get_url"

#: mock 预签名回放（IT-02：窗口参数即「自然过期」的诚实上界声明）
FAKE_PRESIGN = "http://minio.local/rp-uploads/__KEY__?X-Amz-Expires=300&sig=test"


def _fake_get_url(*, bucket, key, expires=300, response_headers=None):
    return FAKE_PRESIGN.replace("__KEY__", key)


# ────────────────────────────────────────────────────────────────
# 夹具
# ────────────────────────────────────────────────────────────────
@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="f4-owner@rabbit.dev", password="Rabbit123!",
                                     display_name="分享主")
    admin = User.objects.create_user(email="f4-admin@rabbit.dev", password="Rabbit123!",
                                     display_name="管理员")
    jia = User.objects.create_user(email="f4-jia@rabbit.dev", password="Rabbit123!",
                                   display_name="贡献者")
    viewer = User.objects.create_user(email="f4-viewer@rabbit.dev", password="Rabbit123!",
                                      display_name="只读者")
    ws = Workspace.objects.create(name="W", slug=f"w-f4-{owner.id.hex[:8]}",
                                  owner=owner, created_by=owner)
    for u in ((owner, WorkspaceRole.OWNER), (admin, WorkspaceRole.MEMBER),
              (jia, WorkspaceRole.MEMBER), (viewer, WorkspaceRole.MEMBER)):
        WorkspaceMember.objects.create(workspace=ws, member=u[0], role=u[1], created_by=owner)
    proj = Project.objects.create(name="P", identifier="F4", workspace=ws, created_by=owner)
    for u, role in ((admin, ProjectRole.ADMIN), (jia, ProjectRole.CONTRIBUTOR),
                    (viewer, ProjectRole.VIEWER)):
        ProjectMember.objects.create(project=proj, member=u, role=role, created_by=owner)
    folder = flib.create_folder(project=proj, actor=owner, name="分享目录", parent_id=None)
    return {"owner": owner, "admin": admin, "jia": jia, "viewer": viewer,
            "ws": ws, "proj": proj, "folder": folder}


@pytest.fixture()
def redis_clean():
    """BR-07 计数键测试隔离——INFRA-005 收编后计数走 django cache（LocMem），
    前后置整体清空即等效原「重置进程态 + 清 share-unlock:* 键」。"""
    from django.core.cache import cache
    cache.clear()
    yield
    cache.clear()


def _mk_asset(env, name="首页改版-v3.fig", mime="image/png", ext=".png",
              visibility="all", folder=None, actor=None, size=8388608):
    from plane.base.middleware import ulid_new

    actor = actor or env["owner"]
    folder = folder or env["folder"]
    key = "/".join([str(env["ws"].id), str(env["proj"].id), "project_file",
                    str(folder.id), f"{ulid_new()}{ext}"])
    asset = FileAsset.objects.create(
        workspace=env["ws"], project=env["proj"],
        entity_type=FileAsset.EntityType.PROJECT_FILE,
        entity_id=folder.id, folder=folder,
        attributes={"name": name, "size": size, "mime": mime, "ext": ext},
        size=size, storage_path=key,
        status=FileAsset.Status.UPLOADED, is_uploaded=True,
        uploaded_by=actor, visibility=visibility,
        created_by=actor, updated_by=actor,
    )
    version = FileVersion.objects.create(
        asset=asset, version_number=1, object_key=key,
        attributes={"name": name, "size": size, "mime": mime, "ext": ext},
        uploaded_by_id=actor.id, created_by_id=actor.id,
    )
    asset.current_version = version
    asset.save(update_fields=["current_version"])
    return asset


class _Client:
    def __init__(self, user=None):
        self.c = APIClient()
        if user is not None:
            self.c.force_authenticate(user=user)

    def get(self, path, **kw):
        return self.c.get(path, format="json", **kw)

    def post(self, path, body=None, **kw):
        return self.c.post(path, body or {}, format="json", **kw)

    def delete(self, path):
        return self.c.delete(path, format="json")


def _base(env):
    return f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}"


def _links_url(env, asset):
    return f"{_base(env)}/files/{asset.id}/share-links/"


def _extend_url(env, link_id):
    return f"{_base(env)}/share-links/{link_id}/extend/"


def _revoke_url(env, link_id):
    return f"{_base(env)}/share-links/{link_id}/"


def _pub(slug):
    return f"/api/v1/public/shares/{slug}"


def _mk_share(env, asset, *, password=None, permission="download",
              expires_in_days=None, actor=None):
    return svc.create_share(
        asset=asset,
        payload={"permission": permission,
                 "password": password,
                 "expires_in_days": expires_in_days},
        actor=actor or env["owner"],
    )


def _expired_link(env, asset):
    link = _mk_share(env, asset)
    FileShareLink.objects.filter(pk=link.pk).update(
        expires_at=timezone.now() - timedelta(hours=1))
    link.refresh_from_db()
    return link


# ────────────────────────────────────────────────────────────────
# UT-01 slug 熵与唯一
# ────────────────────────────────────────────────────────────────
def test_ut01_slug_entropy_and_alphabet(env):
    slugs = [generate_share_slug() for _ in range(500)]
    assert len(set(slugs)) == 500  # 无碰撞
    assert all(len(s) == 22 for s in slugs)  # 恰 22 字符
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{22}", s) for s in slugs)  # base64url 字母表
    # 不可枚举性（对顺序计数器/时间戳前缀等可预测实现的否定断言）：
    chars = {c for s in slugs for c in s}
    assert len(chars) >= 40  # 字符集广度（顺序数字串仅 10 个符号）
    assert len({s[0] for s in slugs}) >= 30  # 首字符分散（计数器恒 '0'）
    assert slugs != sorted(slugs)  # 生成序非单调（可预测序列恒有序）


# ────────────────────────────────────────────────────────────────
# UT-02 密码哈希存储（Argon2id）
# ────────────────────────────────────────────────────────────────
def test_ut02_password_argon2id_storage(env):
    asset = _mk_asset(env)
    client = _Client(env["owner"])
    resp = client.post(_links_url(env, asset), {
        "permission": "download", "password": "demo-2026", "expires_in_days": 30})
    assert resp.status_code == 201, resp.content
    slug = resp.json()["data"]["slug"]
    row = FileShareLink.objects.get(slug=slug)
    assert row.password_hash.startswith("argon2$argon2id$")  # 库中仅 Argon2id 哈希
    assert "demo-2026" not in row.password_hash  # 绝不明文/可逆
    assert svc.check_password(row, "demo-2026")
    assert not svc.check_password(row, "demo-2027")


# ────────────────────────────────────────────────────────────────
# UT-03 / UT-04 / UT-05 / IT-08 unlock 面（防爆破）
# ────────────────────────────────────────────────────────────────
def test_ut03_no_password_direct_pass(env, redis_clean):
    asset = _mk_asset(env)
    _mk_share(env, asset)  # 无密码
    pub = _Client()
    resp = pub.post(f"{_pub(asset.share_links.first().slug)}/unlock/", {"password": ""})
    assert resp.status_code == 200
    assert resp.json()["data"] == {"unlocked": True}


def test_ut04_wrong_password_401_with_remaining(env, redis_clean):
    asset = _mk_asset(env)
    _mk_share(env, asset, password="demo-2026")
    slug = asset.share_links.first().slug
    pub = _Client()
    resp = pub.post(f"{_pub(slug)}/unlock/", {"password": "wrong-pw"})
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "AUTH_INVALID_CREDENTIALS"
    assert body["error"]["details"][0]["field"] == "password"
    assert "剩余 4 次尝试" in body["error"]["details"][0]["message"]
    # 失败尝试落痕（BR-09：unlock_failed，success=False）
    assert FileShareAccess.objects.filter(
        share__slug=slug, action="unlock_failed", success=False).count() == 1


def test_ut05_it08_brute_force_lockout_sixth_429(env, redis_clean):
    asset = _mk_asset(env)
    _mk_share(env, asset, password="demo-2026")
    slug = asset.share_links.first().slug
    pub = _Client()
    for i in range(5):  # 5 次错 → 逐次 401，剩余次数递减
        resp = pub.post(f"{_pub(slug)}/unlock/", {"password": "nope"})
        assert resp.status_code == 401, f"attempt {i}"
        remain = resp.json()["error"]["details"][0]["message"]
        assert f"剩余 {4 - i} 次尝试" in remain
    resp = pub.post(f"{_pub(slug)}/unlock/", {"password": "nope"})  # 第 6 次 → 429
    assert resp.status_code == 429
    body = resp.json()
    assert body["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert int(resp.headers["Retry-After"]) >= 1
    assert resp.headers["X-RateLimit-Limit"] == "5"      # §7.3 模板三件套
    assert resp.headers["X-RateLimit-Remaining"] == "0"
    assert int(resp.headers["X-RateLimit-Reset"]) > timezone.now().timestamp()
    # 键维度 (IP, slug)：另一 slug 不受牵连（同 IP）
    asset2 = _mk_asset(env, name="另一文件.png")
    _mk_share(env, asset2, password="demo-2026")
    slug2 = asset2.share_links.first().slug
    assert pub.post(f"{_pub(slug2)}/unlock/", {"password": "nope"}).status_code == 401
    # 加速时钟（清键 = 10 分钟窗口流逝）→ 恢复可试
    from django.core.cache import cache

    from plane.base.throttling import ShareUnlockRateThrottle
    cache.delete(ShareUnlockRateThrottle().key_for(slug, "127.0.0.1"))
    assert pub.post(f"{_pub(slug)}/unlock/", {"password": "nope"}).status_code == 401


def test_it08_success_unlock_resets_counter(env, redis_clean):
    asset = _mk_asset(env)
    _mk_share(env, asset, password="demo-2026")
    slug = asset.share_links.first().slug
    pub = _Client()
    for _ in range(4):
        assert pub.post(f"{_pub(slug)}/unlock/", {"password": "nope"}).status_code == 401
    assert pub.post(f"{_pub(slug)}/unlock/", {"password": "demo-2026"}).status_code == 200
    # 成功清零：后续仍有 5 次满额（INFRA-005 收编——计数键已删）
    from django.core.cache import cache

    from plane.base.throttling import ShareUnlockRateThrottle
    key = ShareUnlockRateThrottle().key_for(slug, "127.0.0.1")
    assert int(cache.get(key) or 0) == 0


# ────────────────────────────────────────────────────────────────
# UT-06 / UT-07 / UT-08 / UT-14 / UT-17 / IT-04：读时四查 + 四态一页
# ────────────────────────────────────────────────────────────────
def _gone_body(resp):
    assert resp.status_code == 410
    return resp.json()["error"]


def test_ut06_expiry_hard_check_and_lazy_mark(env):
    asset = _mk_asset(env)
    link = _expired_link(env, asset)
    err = _gone_body(_Client().get(_pub(link.slug) + "/"))
    assert err["code"] == "RESOURCE_GONE"
    assert err["message"] == svc.GONE_MESSAGE  # 同文案（防枚举区分）
    link.refresh_from_db()
    assert link.status == FileShareLink.Status.EXPIRED  # 惰性标记


def test_ut07_source_soft_delete_invalidates_and_no_revive(env):
    asset = _mk_asset(env)
    link = _mk_share(env, asset)
    flib.soft_delete_file(asset=asset, actor=env["owner"])  # 源软删
    assert _Client().get(_pub(link.slug) + "/").status_code == 410
    link.refresh_from_db()
    assert link.status == FileShareLink.Status.INVALIDATED
    flib.restore_file(asset=asset, actor=env["owner"])  # 恢复不复活（BR-06）
    asset.refresh_from_db()
    assert asset.deleted_at is None
    assert _Client().get(_pub(link.slug) + "/").status_code == 410
    link.refresh_from_db()
    assert link.status == FileShareLink.Status.INVALIDATED  # 状态单向


def test_ut08_project_archive_invalidates(env):
    asset = _mk_asset(env)
    link = _mk_share(env, asset)
    env["proj"].status = "archived"
    env["proj"].save(update_fields=["status"])
    assert _Client().get(_pub(link.slug) + "/").status_code == 410
    link.refresh_from_db()
    assert link.status == FileShareLink.Status.INVALIDATED


def test_ut14_revoke_immediate(env):
    asset = _mk_asset(env)
    link = _mk_share(env, asset)
    assert _Client().get(_pub(link.slug) + "/").status_code == 200
    resp = _Client(env["owner"]).delete(_revoke_url(env, link.id))
    assert resp.status_code == 204
    assert _Client().get(_pub(link.slug) + "/").status_code == 410


def test_ut17_it04_four_states_one_page(env):
    asset = _mk_asset(env)
    revoked = _mk_share(env, asset, password="demo-2026")
    expired = _expired_link(env, asset)
    soft_asset = _mk_asset(env, name="软删源.png")
    soft_link = _mk_share(env, soft_asset)
    flib.soft_delete_file(asset=soft_asset, actor=env["owner"])  # 源失效
    FileShareLink.objects.filter(pk=revoked.pk).update(status="revoked")
    responses = [
        _Client().get(_pub("0123456789abcdefghijklmnop") + "/"),  # 不存在（合法格式）
        _Client().get(_pub("BAD SLUG!") + "/"),                    # 格式非法
        _Client().get(_pub(revoked.slug) + "/"),                   # 吊销
        _Client().get(_pub(expired.slug) + "/"),                   # 过期
        _Client().get(_pub(soft_link.slug) + "/"),                 # 源失效
    ]
    bodies = [_gone_body(r) for r in responses]
    # 同码同文案（仅 request_id 不同，UT-17）——防「从未存在 / 曾有效」被区分
    assert all(b["code"] == "RESOURCE_GONE" for b in bodies)
    assert all(b["message"] == svc.GONE_MESSAGE for b in bodies)
    assert len({b["request_id"] for b in bodies}) == len(bodies)


# ────────────────────────────────────────────────────────────────
# UT-09 / UT-10 / UT-11：权限与脱敏
# ────────────────────────────────────────────────────────────────
def _unlock(pub, slug, password):
    return pub.post(f"{_pub(slug)}/unlock/", {"password": password})


def test_ut09_view_permission_rejects_download(env, redis_clean):
    asset = _mk_asset(env)
    link = _mk_share(env, asset, permission="view")
    pub = _Client()
    assert _unlock(pub, link.slug, "").status_code == 200  # 无密码直通
    resp = pub.get(_pub(link.slug) + "/content/?download=1")
    assert resp.status_code == 403
    body = resp.json()["error"]
    assert body["code"] == "PERM_DENIED"
    assert "下载" in body["message"]
    # 失败下载不计 access_count（BR-09 仅成功计数）
    link.refresh_from_db()
    assert link.access_count == 0


def test_ut10_token_scoped_to_slug(env, redis_clean):
    asset_a = _mk_asset(env, name="A.png")
    asset_b = _mk_asset(env, name="B.png")
    link_a = _mk_share(env, asset_a, password="pw-a-000")
    link_b = _mk_share(env, asset_b, password="pw-b-000")
    pub = _Client()
    assert _unlock(pub, link_a.slug, "pw-a-000").status_code == 200  # cookie 已入 jar
    # A 的 token 访 B → 401（HMAC 绑定 slug，UT-10）
    assert pub.get(_pub(link_b.slug) + "/content/").status_code == 401
    # 同 cookie 回 A → 通过（读时四查 + token 有效；未转码图片 → 202 排队）
    assert pub.get(_pub(link_a.slug) + "/content/").status_code == 202


def test_ut11_meta_minimization_locked(env):
    asset = _mk_asset(env)
    link = _mk_share(env, asset, password="demo-2026")
    resp = _Client().get(_pub(link.slug) + "/")
    assert resp.status_code == 200
    assert resp.json()["data"] == {"requires_password": True}  # 无文件名/项目名（BR-10）
    # 未持 token 的 content → 401（密码门）
    assert _Client().get(_pub(link.slug) + "/content/").status_code == 401


# ────────────────────────────────────────────────────────────────
# UT-12 / UT-20：BR-11 两维上限
# ────────────────────────────────────────────────────────────────
def test_ut12_limit_per_asset(env):
    asset = _mk_asset(env)
    client = _Client(env["owner"])
    for _ in range(10):
        assert client.post(_links_url(env, asset), {"permission": "view"}).status_code == 201
    resp = client.post(_links_url(env, asset), {"permission": "view"})  # 第 11 条
    assert resp.status_code == 409
    body = resp.json()["error"]
    assert body["code"] == "RESOURCE_LIMIT_EXCEEDED"
    assert body["details"] == [{"field": "asset_id", "code": "LIMIT", "message": "上限 10 条"}]
    # 吊销释放额度（仅 active 计数）
    first = FileShareLink.objects.filter(asset=asset).order_by("created_at").first()
    svc.revoke_share(link=first, actor=env["owner"])
    assert client.post(_links_url(env, asset), {"permission": "view"}).status_code == 201


def test_ut20_limit_per_user_project(env):
    # 10 文件 × 10 条 = 100 条同用户同项目 active（各文件维度均未触顶）
    assets = [_mk_asset(env, name=f"批量-{i}.png") for i in range(10)]
    rows = []
    for a in assets:
        for _ in range(10):
            rows.append(FileShareLink(asset=a, created_by=env["owner"],
                                      updated_by=env["owner"],
                                      permission=FileShareLink.Permission.VIEW))
    FileShareLink.objects.bulk_create(rows)
    eleventh_asset = _mk_asset(env, name="第 101 条宿主.png")
    resp = _Client(env["owner"]).post(_links_url(env, eleventh_asset), {"permission": "view"})
    assert resp.status_code == 409
    body = resp.json()["error"]
    assert body["code"] == "RESOURCE_LIMIT_EXCEEDED"
    # 与 UT-12 的 asset_id 维度区分断言（BR-11 对偶）
    assert body["details"] == [{"field": "created_by", "code": "LIMIT", "message": "上限 100 条"}]


# ────────────────────────────────────────────────────────────────
# UT-13 / UT-18 / UT-21：延期（BR-15）
# ────────────────────────────────────────────────────────────────
def test_ut13_extend_beyond_365_days(env):
    asset = _mk_asset(env)
    link = _mk_share(env, asset, expires_in_days=340)
    client = _Client(env["owner"])
    resp = client.post(_extend_url(env, link.id), {"extend_days": 30})  # 340+30 > 365
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert resp.json()["error"]["details"][0]["field"] == "expires_at"
    # 边界内成功：+25 → 365 整（不晚于 now+365d）
    resp = client.post(_extend_url(env, link.id), {"extend_days": 25})
    assert resp.status_code == 200, resp.content


def test_extend_expired_active_base_now(env):
    """BR-15：已过期边界以 now 为基准（不产生负偏移）。"""
    asset = _mk_asset(env)
    link = _expired_link(env, asset)  # status 仍 active、expires_at 已过
    resp = _Client(env["owner"]).post(_extend_url(env, link.id), {"extend_days": 1})
    assert resp.status_code == 200, resp.content
    link.refresh_from_db()
    assert link.expires_at > timezone.now()
    assert link.expires_at <= timezone.now() + timedelta(days=1, minutes=1)


def test_ut18_concurrent_extend_serialized(env):
    """两笔延期串行叠加（select_for_update 后提交者基于最新值），最终确定。"""
    asset = _mk_asset(env)
    link = _mk_share(env, asset, expires_in_days=10)
    client = _Client(env["owner"])
    assert client.post(_extend_url(env, link.id), {"extend_days": 30}).status_code == 200
    resp = client.post(_extend_url(env, link.id), {"extend_days": 30})
    assert resp.status_code == 200
    link.refresh_from_db()
    expected = timezone.now() + timedelta(days=70)  # 10 + 30 + 30（非幂等叠加）
    assert abs((link.expires_at - expected).total_seconds()) < 120
    # 非提交事务口径：service 内 select_for_update 重取行（无脏写基线）
    with patch("plane.db.services.file_share.FileShareLink.objects.select_for_update") as m:
        m.return_value.filter.return_value.first.return_value = None
        resp = client.post(_extend_url(env, link.id), {"extend_days": 1})
        assert resp.status_code == 404  # 行不可达 → NotFound（锁路径真实生效）


def test_ut21_permanent_link_extend_400(env):
    asset = _mk_asset(env)
    link = _mk_share(env, asset)  # expires_at=None 永久
    resp = _Client(env["owner"]).post(_extend_url(env, link.id), {"extend_days": 30})
    assert resp.status_code == 400
    body = resp.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"][0]["field"] == "expires_at"


def test_extend_non_active_409(env):
    asset = _mk_asset(env)
    link = _mk_share(env, asset, expires_in_days=1)
    svc.revoke_share(link=link, actor=env["owner"])
    resp = _Client(env["owner"]).post(_extend_url(env, link.id), {"extend_days": 30})
    assert resp.status_code == 409
    body = resp.json()["error"]
    assert body["code"] == "RESOURCE_STATE_INVALID"
    assert body["message"] == "仅有效分享可延期"
    assert "revoked" in body["details"][0]["message"]


# ────────────────────────────────────────────────────────────────
# UT-15 版本跟随（BR-12）
# ────────────────────────────────────────────────────────────────
def test_ut15_share_follows_current_version(env, redis_clean):
    asset = _mk_asset(env)
    link = _mk_share(env, asset, password="demo-2026")
    pub = _Client()
    assert _unlock(pub, link.slug, "demo-2026").status_code == 200
    with patch(GET_URL, side_effect=_fake_get_url) as mock_get:
        first = pub.get(_pub(link.slug) + "/content/?download=1")
        assert first.status_code == 302
        v1_key = asset.storage_path
        assert v1_key in first.headers["Location"]
        # 上传新版本：current_version 指针 + 行级镜像三列（_new_version 同构口径）
        v2_key = v1_key.replace(".png", "-v2.png")
        v2 = FileVersion.objects.create(
            asset=asset, version_number=2, object_key=v2_key,
            attributes=dict(asset.attributes, size=999),
            uploaded_by_id=env["owner"].id, created_by_id=env["owner"].id)
        FileAsset.objects.filter(pk=asset.pk).update(
            current_version=v2.id, storage_path=v2_key, size=999)
        second = pub.get(_pub(link.slug) + "/content/?download=1")
        assert second.status_code == 302
        assert v2_key in second.headers["Location"]  # 分享内容即新版本（不重建链接）
        assert mock_get.call_args.kwargs["expires"] == 300  # IT-02：5 分钟窗口


# ────────────────────────────────────────────────────────────────
# UT-16 创建权限（BR-01）
# ────────────────────────────────────────────────────────────────
def test_ut16_create_permission_404_403(env):
    hidden = _mk_asset(env, visibility="admins")  # admins 态：CONTRIBUTOR 不可见
    resp = _Client(env["jia"]).post(_links_url(env, hidden), {"permission": "view"})
    assert resp.status_code == 404  # 不可见文件 → 存在性隐藏
    visible = _mk_asset(env, visibility="all")
    resp = _Client(env["viewer"]).post(_links_url(env, visible), {"permission": "view"})
    assert resp.status_code == 403  # 无 file.share（默认 PROJ_ADMIN）
    assert resp.json()["error"]["code"] == "PERM_ROLE_INSUFFICIENT"
    # 管理员可见且可建；admins 态对 ADMIN 亦可见
    assert _Client(env["admin"]).post(
        _links_url(env, hidden), {"permission": "view"}).status_code == 201


def test_create_validation_password_and_expiry(env):
    asset = _mk_asset(env)
    client = _Client(env["owner"])
    resp = client.post(_links_url(env, asset), {"password": "ab"})
    assert resp.status_code == 400  # 密码 <4
    assert resp.json()["error"]["details"][0]["field"] == "password"
    resp = client.post(_links_url(env, asset), {"expires_in_days": 366})
    assert resp.status_code == 400  # >365 天
    assert resp.json()["error"]["details"][0]["field"] == "expires_in_days"
    assert resp.json()["error"]["details"][0]["code"] == "TOO_LARGE"


# ────────────────────────────────────────────────────────────────
# UT-19 计数原子自增（BR-09）
# ────────────────────────────────────────────────────────────────
class _FakeReq:
    META = {"HTTP_USER_AGENT": "pytest-agent", "REMOTE_ADDR": "203.0.113.9"}


def test_ut19_access_count_increment_converges(env):
    asset = _mk_asset(env)
    link = _mk_share(env, asset)
    req = _FakeReq()
    for i in range(50):
        svc.record_access(link, req, action="view" if i % 2 else "download")
    link.refresh_from_db()
    assert link.access_count == 50  # 仅成功 view/download 计数
    assert FileShareAccess.objects.filter(share=link).count() == 50  # 与留痕明细一致


# ────────────────────────────────────────────────────────────────
# IT-01 匿名全链路（密码门 → 预览 → 下载 → 留痕 → cookie）
# ────────────────────────────────────────────────────────────────
def test_it01_anonymous_full_chain(env, redis_clean):
    asset = _mk_asset(env, mime="image/png")
    # 预置就绪缩略（image 预览 200 路径）
    version = asset.current_version
    attrs = dict(version.attributes)
    attrs["derivatives"] = {"thumbnail": {
        "key": f"deriv/{asset.id}/{version.id}/thumbnail", "status": "ready"}}
    version.attributes = attrs
    version.save(update_fields=["attributes"])

    resp = _Client(env["owner"]).post(_links_url(env, asset), {
        "permission": "download", "password": "demo-2026", "expires_in_days": 30})
    assert resp.status_code == 201
    data = resp.json()["data"]
    slug = data["slug"]
    assert data["has_password"] is True
    assert data["share_url"].endswith(f"/s/{slug}")
    assert data["status"] == "active"

    pub = _Client()
    # 密码门：meta 信息最小化
    assert pub.get(_pub(slug) + "/").json()["data"] == {"requires_password": True}
    # unlock → 200 + cookie 三件套（HttpOnly / SameSite=Lax / Max-Age=7200）
    resp = _unlock(pub, slug, "demo-2026")
    assert resp.status_code == 200
    assert resp.json()["data"] == {"unlocked": True, "expires_in": 7200}
    cookie = resp.cookies[svc.SHARE_COOKIE_NAME]
    assert cookie["httponly"]
    assert cookie["samesite"] == "Lax"
    assert cookie["max-age"] == 7200
    # cookie 2h（BR-08）：token 内嵌 exp ≈ now+7200
    exp = int(cookie.value.split(":", 1)[0])
    assert 7100 < exp - timezone.now().timestamp() <= 7200
    # 解锁后 meta 展示文件信息（不含项目名，BR-10）
    meta = pub.get(_pub(slug) + "/").json()["data"]
    assert meta["requires_password"] is False
    assert meta["file"] == {"name": "首页改版-v3.fig", "size_bytes": 8388608,
                            "type_category": "image"}
    assert meta["permission"] == "download"
    # 预览：匿名直签（200）
    with patch(GET_URL, side_effect=_fake_get_url):
        preview = pub.get(_pub(slug) + "/content/")
        assert preview.status_code == 200
        pdata = preview.json()["data"]
        assert pdata["kind"] == "image" and pdata["ready"] is True
        assert pdata["preview_url"].startswith("/uploads/")  # 预签名换发，非内部路径
        # 下载：302 → 5 分钟预签名
        dl = pub.get(_pub(slug) + "/content/?download=1")
        assert dl.status_code == 302
        assert dl.headers["Location"].startswith("/uploads/rp-uploads/")
    # 留痕齐（unlock/view/download，失败 0）+ 计数
    link = FileShareLink.objects.get(slug=slug)
    actions = sorted(FileShareAccess.objects.filter(share=link)
                     .values_list("action", flat=True))
    assert actions == ["download", "unlock", "view"]
    assert FileShareAccess.objects.filter(share=link, success=False).count() == 0
    link.refresh_from_db()
    assert link.access_count == 2  # view + download


@override_settings(DEBUG=False)
def test_it01_cookie_secure_in_prod(env, redis_clean):
    asset = _mk_asset(env)
    _mk_share(env, asset, password="demo-2026")
    slug = asset.share_links.first().slug
    resp = _Client().post(f"{_pub(slug)}/unlock/", {"password": "demo-2026"})
    assert resp.status_code == 200
    assert resp.cookies[svc.SHARE_COOKIE_NAME]["secure"] is True  # 生产强制（§4.2.3）


# ────────────────────────────────────────────────────────────────
# IT-03 吊销窗口（BR-14：预签名 5 分钟为诚实上界）
# ────────────────────────────────────────────────────────────────
def test_it03_revoke_window_presign_ttl(env, redis_clean):
    asset = _mk_asset(env)
    link = _mk_share(env, asset)
    pub = _Client()
    with patch(GET_URL, side_effect=_fake_get_url) as mock_get:
        resp = pub.get(_pub(link.slug) + "/content/?download=1")
        assert resp.status_code == 302
        url_before = resp.headers["Location"]
        svc.revoke_share(link=link, actor=env["owner"])
        # 已签发 URL 不经服务端（客户端持有；S3 侧 5 分钟自然过期——窗口即上界）
        assert mock_get.call_args.kwargs["expires"] == 300
        assert url_before.startswith("/uploads/")
    assert pub.get(_pub(link.slug) + "/content/?download=1").status_code == 410


# ────────────────────────────────────────────────────────────────
# IT-05 内外部隔离
# ────────────────────────────────────────────────────────────────
def test_it05_anonymous_cannot_call_internal(env):
    asset = _mk_asset(env)
    resp = _Client().get(_links_url(env, asset))  # 匿名调内部端点
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_REQUIRED"


# ────────────────────────────────────────────────────────────────
# IT-06 匿名预览排队（未转码 → 202）
# ────────────────────────────────────────────────────────────────
def test_it06_anonymous_preview_queued_202(env, redis_clean):
    asset = _mk_asset(env, name="合同.docx",
                      mime="application/vnd.openxmlformats-officedocument"
                           ".wordprocessingml.document", ext=".docx")
    link = _mk_share(env, asset)
    pub = _Client()
    resp = pub.get(_pub(link.slug) + "/content/")
    assert resp.status_code == 202  # 排队轮询（derive 预注册 pending）
    data = resp.json()["data"]
    assert data["ready"] is False
    assert data["state"] == "transcoding"
    assert data["eta_seconds"] > 0
    version = FileVersion.objects.get(pk=asset.current_version_id)  # 重取（视图内已保存）
    derivs = (version.attributes or {}).get("derivatives") or {}
    assert derivs.get("preview", {}).get("status") == "pending"


# ────────────────────────────────────────────────────────────────
# IT-07 访问统计（计数与明细一致）
# ────────────────────────────────────────────────────────────────
def test_it07_access_stats_consistency(env, redis_clean):
    asset = _mk_asset(env, name="统计.pdf", mime="application/pdf", ext=".pdf")
    link = _mk_share(env, asset, permission="download")
    pub = _Client()
    with patch(GET_URL, side_effect=_fake_get_url):
        for i in range(42):
            if i % 2:
                assert pub.get(_pub(link.slug) + "/content/?download=1").status_code == 302
            else:
                assert pub.get(_pub(link.slug) + "/content/").status_code == 200
    rows, meta = svc.list_shares(asset=asset, params={})
    assert meta["total_count"] == 1
    assert rows[0]["access_count"] == 42  # 管理弹层计数
    assert FileShareAccess.objects.filter(
        share_id=link.id, action__in=["view", "download"]).count() == 42  # 明细一致


# ────────────────────────────────────────────────────────────────
# IT-09 分享动态留痕（BR-13）
# ────────────────────────────────────────────────────────────────
def test_it09_share_events_lifecycle(env, redis_clean, django_capture_on_commit_callbacks):
    asset = _mk_asset(env)
    client = _Client(env["owner"])
    events: list[tuple] = []
    with patch("plane.bgtasks.event_publisher.dispatch_event",
               side_effect=lambda e, p, r, o=None: events.append((e, p, r))):
        with django_capture_on_commit_callbacks(execute=True):
            resp = client.post(_links_url(env, asset),
                               {"permission": "download", "expires_in_days": 30})
            assert resp.status_code == 201
            link_id = resp.json()["data"]["id"]
            assert client.post(_extend_url(env, link_id),
                               {"extend_days": 7}).status_code == 200
            assert client.delete(_revoke_url(env, link_id)).status_code == 204
    names = [e[0] for e in events]
    assert names == ["file.share.created", "file.share.extended", "file.share.revoked"]
    rooms = {e[0]: e[2] for e in events}
    for room in rooms.values():
        assert f"project:{env['proj'].id}" in room  # 内部视角（项目房间）
        assert f"file:{asset.id}" in room
    # 匿名访问/unlock 不入事件（防刷屏）
    asset2 = _mk_asset(env, name="匿名面.pdf", mime="application/pdf", ext=".pdf")
    link2 = _mk_share(env, asset2, password="demo-2026")
    with patch("plane.bgtasks.event_publisher.dispatch_event",
               side_effect=lambda *a, **k: events.append(("leak", a, None))):
        with django_capture_on_commit_callbacks(execute=True):
            pub = _Client()
            assert pub.get(_pub(link2.slug) + "/").status_code == 200
            assert _unlock(pub, link2.slug, "demo-2026").status_code == 200
            with patch(GET_URL, side_effect=_fake_get_url):
                assert pub.get(_pub(link2.slug) + "/content/").status_code == 200
    assert "leak" not in [e[0] for e in events]


# ────────────────────────────────────────────────────────────────
# beat 清扫（§4.3.4）+ 注册证据
# ────────────────────────────────────────────────────────────────
def test_sweep_expired_shares_beat(env):
    from plane.celery import app as celery_app

    asset = _mk_asset(env)
    expired = _expired_link(env, asset)
    alive = _mk_share(env, asset, expires_in_days=30)
    swept = sweep_expired_shares(restrict_share_ids=[expired.id, alive.id])
    assert swept == 1
    expired.refresh_from_db()
    alive.refresh_from_db()
    assert expired.status == FileShareLink.Status.EXPIRED
    assert alive.status == FileShareLink.Status.ACTIVE  # 未到期不动
    # beat 调度注册证据（§4.3.4：每小时）
    assert "sweep-expired-shares" in celery_app.conf.beat_schedule
    assert (celery_app.conf.beat_schedule["sweep-expired-shares"]["task"]
            == "plane.bgtasks.share_sweep.sweep_expired_shares")


# ────────────────────────────────────────────────────────────────
# 内部列表端点（§4.2.4 形状）
# ────────────────────────────────────────────────────────────────
def test_list_endpoint_shape_and_states(env):
    asset = _mk_asset(env)
    _mk_share(env, asset, password="demo-2026", expires_in_days=30)
    _mk_share(env, asset, permission="view")
    expired = _expired_link(env, asset)
    FileShareLink.objects.filter(pk=expired.pk).update(
        status=FileShareLink.Status.EXPIRED)  # beat/惰性标记已跑过的终态
    resp = _Client(env["owner"]).get(_links_url(env, asset))
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["total_count"] == 3  # 含失效态（管理弹层展示）
    assert body["meta"]["per_page"] == 100
    pw_row = next(r for r in body["data"] if r["has_password"])
    active_plain = next(
        r for r in body["data"] if r["status"] == "active" and not r["has_password"])
    expired_row = next(r for r in body["data"] if r["status"] == "expired")
    assert pw_row["permission"] == "download" and pw_row["expires_at"]
    assert active_plain["permission"] == "view" and active_plain["expires_at"] is None
    assert expired_row["status"] == "expired"  # 失效态仅展示，不占 BR-11 额度
    assert all(r["share_url"].endswith(f"/s/{r['slug']}") for r in body["data"])
    assert all("password_hash" not in r for r in body["data"])  # 哈希不外泄
    # viewer 无 file.share → 403
    assert _Client(env["viewer"]).get(_links_url(env, asset)).status_code == 403


# ────────────────────────────────────────────────────────────────
# 安全单元：token 签发/校验（BR-08 单点）
# ────────────────────────────────────────────────────────────────
def test_share_token_forgery_and_expiry(env):
    slug = "a" * 22
    token = svc.sign_share_token(slug)
    assert svc.verify_share_token(slug, token) is True
    assert svc.verify_share_token("b" * 22, token) is False  # 换 slug（HMAC 绑定）
    assert svc.verify_share_token(slug, f"{token[:-4]}dead") is False  # 篡改
    assert svc.verify_share_token(slug, "garbage") is False
    assert svc.verify_share_token(slug, None) is False
    expired = svc.sign_share_token(slug, ttl=-1)  # 已过期
    assert svc.verify_share_token(slug, expired) is False
