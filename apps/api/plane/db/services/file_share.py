"""文件分享服务（FILE-004 §4.3 核心）。

职责：
- 读时四查（§4.3.1：存在 → 状态 → 有效期 → 源存活；四种失败统一 410，
  不泄露原因——防枚举区分，§2.4）；
- 创建（slug 生成 / Argon2id 密码 / BR-11 两维上限）/ 列表 / 延期（BR-15
  ``select_for_update`` 串行）/ 吊销；
- 2h HMAC token 签发与校验（BR-08，SECRET_KEY 派生键的单点实现）；
- (IP, slug) 防爆破计数（BR-07——Valkey 固定窗口 600s，键
  ``share-unlock:{ip}:{slug}``；INFRA-004 不含限流框架，本文自带端点级实现，
  INFRA-005 Sprint 6 仅收编配置不改语义）；
- 访问留痕与计数（BR-09：留痕 + ``F()`` 原子自增同一事务，失败尝试不计）。

安全底线（§1.2）：链接即能力（不绑账号）；slug 22 位 base64url 不可枚举；
错误响应不泄露文件元信息（BR-10）。
"""
from __future__ import annotations

import hmac
import logging
import re
import time
from datetime import timedelta
from typing import Any
from urllib.parse import quote

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.crypto import salted_hmac

from plane.base.exception import AppException
from plane.db.models import FileAsset, FileShareAccess, FileShareLink
from plane.storage import minio as storage

logger = logging.getLogger("plane.db.services.file_share")

# ── 常量（§2.3 / §2.5）───────────────────────────────────────────────
#: token_urlsafe 产物的格式拦截（对齐 AUTH-004 先例）：22 位 base64url
SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")
#: 密码尝试限速（BR-07）：5 次 / 10 分钟 / (IP, slug)
PASSWORD_ATTEMPTS = 5
ATTEMPT_WINDOW = 600
#: unlock cookie（BR-08）：2 小时短 TTL 即泄露兜底
SHARE_COOKIE_NAME = "share_token"
SHARE_TOKEN_TTL = 7200
#: BR-11 两维上限（均只计 status=active）
MAX_SHARES_PER_ASSET = 10
MAX_SHARES_PER_USER_PROJECT = 100
#: 有效期上限（创建与延期同口径，§2.5）
MAX_EXPIRY_DAYS = 365
#: 匿名下载 / 预览预签名窗口（§1.2 底线 3：5 分钟自然过期）
SHARE_PRESIGN_TTL = 300
#: 四态一页的统一文案（UT-17：同码同文案，仅 request_id 不同）
GONE_MESSAGE = "链接不存在或已失效"
#: HMAC salt（签发与校验同键派生；与 Django 其他 salt 空间隔离）
_SHARE_TOKEN_SALT = "plane.file_share.share_token"


# ── 服务级异常 ───────────────────────────────────────────────────────
class ShareLimitExceeded(Exception):
    """BR-11 两维上限 → 409 RESOURCE_LIMIT_EXCEEDED。"""

    def __init__(self, dimension: str, limit: int):
        self.dimension = dimension  # "asset" | "user_project"
        self.limit = limit
        super().__init__(f"dimension={dimension} limit={limit}")


class ShareStateError(Exception):
    """延期目标状态非法（非 active，BR-15）→ 409 RESOURCE_STATE_INVALID。"""

    def __init__(self, status: str):
        self.status = status
        super().__init__(f"status={status}")


class SharePermanentLink(Exception):
    """永久链接延期无意义（§4.2.5）→ 400 VALIDATION_ERROR（field=expires_at）。"""


# ── 读时四查（§4.3.1）────────────────────────────────────────────────
def resolve_active_share(slug: str) -> FileShareLink:
    """读时四查：存在 → 状态 → 有效期 → 源存活（BR-04/06）。

    四种失败对外统一 410 ``RESOURCE_GONE``（BR-10 不泄露原因——无效 slug 与
    失效三态同码同文案，防「从未存在」与「曾有效后被吊销」被状态码区分）。
    """
    if not SLUG_RE.fullmatch(slug):
        raise AppException("RESOURCE_GONE", message=GONE_MESSAGE)
    link = (
        FileShareLink.objects.select_related("asset", "asset__project")
        .filter(slug=slug)
        .first()
    )
    if link is None:
        raise AppException("RESOURCE_GONE", message=GONE_MESSAGE)
    if link.status != FileShareLink.Status.ACTIVE:
        raise AppException("RESOURCE_GONE", message=GONE_MESSAGE)
    if link.expires_at is not None and link.expires_at < timezone.now():
        _mark_status(link, FileShareLink.Status.EXPIRED)  # 惰性标记（beat 兜底）
        raise AppException("RESOURCE_GONE", message=GONE_MESSAGE)
    asset = link.asset
    if (
        asset is None
        or asset.status != FileAsset.Status.UPLOADED
        or asset.deleted_at is not None
        or asset.project is None
        or asset.project.status != "active"
    ):
        # FILE-001 §1.4 五态机口径：非 uploaded（uploading/abandoned/deleted）
        # 或软删（deleted_at 非空；purged 已硬删无行）即源失效；项目归档同判。
        # 恢复不复活（BR-06）：状态单向迁移，restore_file 不回写本字段。
        _mark_status(link, FileShareLink.Status.INVALIDATED)
        raise AppException("RESOURCE_GONE", message=GONE_MESSAGE)
    return link


def _mark_status(link: FileShareLink, status: str) -> None:
    FileShareLink.objects.filter(pk=link.pk, status=FileShareLink.Status.ACTIVE).update(
        status=status, updated_at=timezone.now()
    )


# ── 创建 / 列表 / 延期 / 吊销（内部 API，§4.2.1 / §4.2.4 / §4.2.5）────
def create_share(*, asset: FileAsset, payload: dict, actor) -> FileShareLink:
    """创建分享：slug 生成（唯一冲突重生成，BR-02）+ Argon2id（BR-03）+
    两维上限（BR-11）+ 有效期（BR-04）。

    上限判定在 asset 行锁内串行（FILE-002 ``assert_quota`` 同款「求值即取锁」
    纪律）——同文件的并发创建不会双双越过 10 条边界；用户 × 项目维度跨文件
    并发的极小竞窗 P2 接受（超限由下一笔创建拦住，不产生数据损坏）。
    """
    from django.contrib.auth.hashers import make_password
    from django.db import IntegrityError

    if asset.project_id is None:  # 文件库域行恒有项目；防御多态域误用
        from rest_framework.exceptions import NotFound

        raise NotFound("RESOURCE_NOT_FOUND")

    permission = payload.get("permission") or FileShareLink.Permission.DOWNLOAD
    password = (payload.get("password") or "").strip()
    expires_in_days = payload.get("expires_in_days")
    expires_at = (
        timezone.now() + timedelta(days=int(expires_in_days))
        if expires_in_days is not None else None
    )

    with transaction.atomic():
        locked = (
            FileAsset.objects.select_for_update().filter(pk=asset.pk).only("id").first()
        )
        _ = locked  # 求值即获取行锁（FILE-001 _check_task_limit 先例）
        # BR-11 两维上限（只计 active；吊销/过期/源失效释放额度）
        if (
            FileShareLink.objects.filter(
                asset=asset, status=FileShareLink.Status.ACTIVE
            ).count()
            >= MAX_SHARES_PER_ASSET
        ):
            raise ShareLimitExceeded("asset", MAX_SHARES_PER_ASSET)
        if (
            FileShareLink.objects.filter(
                asset__project_id=asset.project_id,
                created_by=actor,
                status=FileShareLink.Status.ACTIVE,
            ).count()
            >= MAX_SHARES_PER_USER_PROJECT
        ):
            raise ShareLimitExceeded("user_project", MAX_SHARES_PER_USER_PROJECT)
        password_hash = make_password(password) if password else ""
        for _ in range(5):  # slug 唯一冲突重生成（128bit 熵下碰撞概率可忽略，兜底）
            try:
                return FileShareLink.objects.create(
                    asset=asset,
                    permission=permission,
                    password_hash=password_hash,
                    expires_at=expires_at,
                    created_by=actor,
                    updated_by=actor,
                )
            except IntegrityError:
                continue  # default=generate_share_slug 每次调用重新求值
        raise AppException("SERVER_ERROR", message="分享标识生成失败，请重试")


def list_shares(
    *, asset: FileAsset, params: dict, base_url: str = ""
) -> tuple[list[dict], dict]:
    """分享列表（§4.2.4：返回该文件全部分享，含失效态供管理弹层展示）。"""
    import base64 as _b64

    qs = FileShareLink.objects.filter(asset=asset).order_by("-created_at", "-id")
    rows_all = list(qs)
    per_page = min(int(params.get("per_page") or 100), 100)
    offset = int(params.get("offset") or 0)
    page_rows = rows_all[offset:offset + per_page]
    total = len(rows_all)

    def _enc(off: int):
        return _b64.b64encode(f"c:{off}:0".encode()).decode() if 0 <= off < total else None

    next_cursor = _enc(offset + per_page) if offset + per_page < total else None
    prev_cursor = _enc(offset - per_page) if offset > 0 else None
    meta = {
        "next_cursor": next_cursor,
        "prev_cursor": prev_cursor,
        "next_page_results": next_cursor is not None,
        "prev_page_results": prev_cursor is not None,
        "count": len(page_rows),
        "total_count": total,
        "total_pages": (total + per_page - 1) // per_page,
        "page": offset // per_page + 1,
        "per_page": per_page,
    }
    return [share_row(r, base_url=base_url) for r in page_rows], meta


def extend_share(*, link: FileShareLink, extend_days: int) -> FileShareLink:
    """延期（BR-15）：``select_for_update`` 串行并发；非幂等（每次叠加）。

    ``expires_at_new = max(now, 当前 expires_at) + extend_days``（已过期边界以
    now 为基准）；结果不得晚于 ``now + 365d``；永久链接 400；仅 active 可延期。
    """
    now = timezone.now()
    with transaction.atomic():
        locked = (
            FileShareLink.objects.select_for_update()
            .filter(pk=link.pk)
            .first()
        )
        if locked is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND")
        if locked.status != FileShareLink.Status.ACTIVE:
            raise ShareStateError(locked.status)
        if locked.expires_at is None:
            raise SharePermanentLink()
        base = max(now, locked.expires_at)
        new_expires = base + timedelta(days=extend_days)
        if new_expires > now + timedelta(days=MAX_EXPIRY_DAYS):
            raise AppException(
                "VALIDATION_ERROR",
                message="延期后有效期超出上限",
                details=[{
                    "field": "expires_at", "code": "TOO_LARGE",
                    "message": f"延期结果不得晚于当前时间 + {MAX_EXPIRY_DAYS} 天",
                }],
            )
        locked.expires_at = new_expires
        locked.save(update_fields=["expires_at", "updated_at"])
        return locked


def revoke_share(*, link: FileShareLink, actor) -> FileShareLink:
    """吊销（§2.2 active → revoked；终态不可逆）。

    BR-14：已签发的 5 分钟预签名自然过期——S3 预签名不可撤回，5 分钟窗口为
    诚实声明（架构约束，非缺陷）。
    """
    link.status = FileShareLink.Status.REVOKED
    link.updated_by = actor
    link.save(update_fields=["status", "updated_by", "updated_at"])
    return link


# ── 行序列化（§4.2.1 / §4.2.4）──────────────────────────────────────
def share_row(link: FileShareLink, *, base_url: str = "") -> dict:
    """内部管理行。``share_url`` 绝对地址 = 视图层传入的请求基座（宿主随部署
    域变化，不落库不配置——偏差见任务报告：无 SPACE_BASE_URL 设置项）。"""
    prefix = f"{base_url.rstrip('/')}/s/" if base_url else "/s/"
    return {
        "id": str(link.id),
        "slug": link.slug,
        "share_url": f"{prefix}{link.slug}",
        "permission": link.permission,
        "has_password": bool(link.password_hash),
        "expires_at": iso(link.expires_at) if link.expires_at else None,
        "status": link.status,
        "access_count": link.access_count,
        "created_at": iso(link.created_at),
    }


def public_meta(link: FileShareLink, *, unlocked: bool) -> dict:
    """匿名元信息（§4.2.2 / BR-10）：未解锁仅 ``requires_password``，零文件信息。"""
    if not unlocked:
        return {"requires_password": True}
    from plane.db.services.file_library import type_category_of

    asset = link.asset
    attrs = asset.attributes or {}
    return {
        "requires_password": False,
        "file": {
            "name": attrs.get("name", ""),
            "size_bytes": asset.size,
            "type_category": type_category_of(asset),
        },
        "permission": link.permission,
        "expires_at": iso(link.expires_at) if link.expires_at else None,
    }


def iso(dt) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ── HMAC token（BR-08：2h，仅对本 slug 有效）─────────────────────────
def sign_share_token(slug: str, *, ttl: int = SHARE_TOKEN_TTL) -> str:
    """签发 ``{exp}:{HMAC-SHA256(slug:exp)}``；键自 SECRET_KEY 派生（单点实现）。"""
    exp = int(time.time()) + ttl
    mac = salted_hmac(_SHARE_TOKEN_SALT, f"{slug}:{exp}", algorithm="sha256").hexdigest()
    return f"{exp}:{mac}"


def verify_share_token(slug: str, token: str | None) -> bool:
    """校验 token 对本 slug 有效且未过期（常数时间比较）。"""
    if not token or ":" not in token:
        return False
    exp_str, _, mac = token.partition(":")
    try:
        exp = int(exp_str)
    except ValueError:
        return False
    if exp < int(time.time()):
        return False
    expected = salted_hmac(_SHARE_TOKEN_SALT, f"{slug}:{exp}", algorithm="sha256").hexdigest()
    return hmac.compare_digest(expected, mac)


def is_unlocked(link: FileShareLink, request) -> bool:
    """密码门通过判定：无密码直通，或有本 slug 的有效 token cookie（§4.3.3）。"""
    if not link.password_hash:
        return True
    token = request.COOKIES.get(SHARE_COOKIE_NAME)
    if not token:
        return False
    return verify_share_token(link.slug, token)


# ── 访问留痕与计数（BR-09）───────────────────────────────────────────
def record_access(link: FileShareLink, request, *, action: str, success: bool = True) -> None:
    """留痕与计数同一事务——``F()`` 原子自增，并发不丢计数；仅成功访问计数。

    P2 量级下单链接访问频率远低于热点路径，单行 UPDATE 由行锁保证原子；
    直写使管理弹层计数与留痕明细强一致（IT-07）。Redis 聚合方案 P4 再评估。
    """
    ua = request.META.get("HTTP_USER_AGENT", "")[:256]
    with transaction.atomic():
        FileShareAccess.objects.create(
            share=link,
            action=action,
            ip=client_ip(request),
            user_agent=ua,
            success=success,
        )
        if action in ("view", "download"):
            FileShareLink.objects.filter(pk=link.pk).update(
                access_count=F("access_count") + 1
            )


def client_ip(request) -> str | None:
    """X-Forwarded-For 首段优先（与 AUTH-001 ``_client_ip`` 同范式）。"""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


# ── 匿名下载 / 正文预签名（§4.3.3；复用 FILE-002 取数键=storage_path）──
def share_download_url(link: FileShareLink) -> str:
    """下载 302 目标：5 分钟预签名（attachment；302 换发范式同 FILE-001 §4.3.4）。

    取数键 = ``asset.storage_path``（行级镜像不变量下恒等于 current_version
    .object_key——分享跟随当前版本，BR-12）。
    """
    asset = link.asset
    filename = (asset.attributes or {}).get("name", "download")
    url = storage.presigned_get_url(
        bucket="rp-uploads",
        key=asset.storage_path,
        expires=SHARE_PRESIGN_TTL,
        response_headers={
            "response-content-disposition": (
                f"attachment; filename*=UTF-8''{quote(filename)}"
            ),
        },
    )
    from plane.app.services.asset import _rewrite_to_uploads_prefix

    return _rewrite_to_uploads_prefix(url)


# ── (IP, slug) 防爆破计数（BR-07，Valkey 固定窗口）────────────────────
_redis_client: Any = None
_redis_unavailable = False


def _redis():
    """Redis 连接（进程级缓存 + 不可达降级短路，范式同 file_stats）。

    降级语义：Redis 故障时限流**放行**（可用性优先，告警不阻断）——与
    event_publisher / file_stats 同一降级纪律；INFRA-005 收编时统一复核。
    """
    global _redis_client, _redis_unavailable
    if _redis_unavailable:
        return None
    if _redis_client is None:
        try:
            import redis
            from django.conf import settings

            _redis_client = redis.Redis.from_url(
                settings.REDIS_URL, decode_responses=True,
                socket_timeout=1, socket_connect_timeout=1,
            )
            _redis_client.ping()
        except Exception as exc:  # noqa: BLE001 —— 降级路径：不阻断匿名面
            _redis_unavailable = True
            logger.warning("file_share.redis_unavailable degrade=throttle_open exc=%s", exc)
            return None
    return _redis_client


def reset_redis_state() -> None:
    """测试辅助：重置进程级 Redis 状态（每用例独立判定可用性）。"""
    global _redis_client, _redis_unavailable
    _redis_client = None
    _redis_unavailable = False


def attempt_key(slug: str, ip: str | None) -> str:
    """BR-07 二维键：(IP, slug)——不按纯 IP（NAT 误伤）不按纯 slug（恶意锁死）。"""
    return f"share-unlock:{ip or 'unknown'}:{slug}"


def throttle_allow(slug: str, ip: str | None) -> tuple[bool, dict[str, int]]:
    """固定窗口计数：``INCR`` + 首次 ``EXPIRE``；超限返回 (False, 限流信息)。

    返回的 info 供 429 响应头（``Retry-After`` / ``X-RateLimit-*``，§7.3 模板）。
    """
    client = _redis()
    if client is None:
        return True, {"limit": PASSWORD_ATTEMPTS, "remaining": PASSWORD_ATTEMPTS,
                      "reset": int(time.time()) + ATTEMPT_WINDOW}
    key = attempt_key(slug, ip)
    count = client.incr(key)
    if count == 1:
        client.expire(key, ATTEMPT_WINDOW)
    ttl = client.ttl(key)
    wait = ttl if isinstance(ttl, int) and ttl > 0 else ATTEMPT_WINDOW
    info = {
        "limit": PASSWORD_ATTEMPTS,
        "remaining": max(0, PASSWORD_ATTEMPTS - count),
        "reset": int(time.time()) + wait,
        "wait": wait,
    }
    return count <= PASSWORD_ATTEMPTS, info


def remaining_attempts(slug: str, ip: str | None) -> int:
    """密码错误响应的「剩余 N 次尝试」（§4.2.3 details）。"""
    client = _redis()
    if client is None:
        return PASSWORD_ATTEMPTS
    count = int(client.get(attempt_key(slug, ip)) or 0)
    return max(0, PASSWORD_ATTEMPTS - count)


def clear_attempts(slug: str, ip: str | None) -> None:
    """解锁成功清零计数（失败才累计；成功访客不应被余量卡住）。"""
    client = _redis()
    if client is not None:
        client.delete(attempt_key(slug, ip))


def check_password(link: FileShareLink, password: str) -> bool:
    """Argon2id 校验（复用 AUTH-001 哈希器基线，BR-03）。"""
    from django.contrib.auth.hashers import check_password as _check

    return _check(password, link.password_hash)
