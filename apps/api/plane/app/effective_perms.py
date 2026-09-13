"""有效权限并集解析器（AUTH-008 §4.3，Sprint-8 R2）。

effective_codes(user, project) = 固定角色码集 ∪ 全部已挂自定义角色码集
（BR-01 只加不减）。热路径：1 次 Redis GET（命中 0 次 DB）；未命中 2 条
索引查询；变更面（挂接/卸除/角色更新/成员移出）全部主动 DEL，TTL 300s
兜底；Redis 不可用时回源 DB 判定仍正确（§2.4 降级口径）。

判定接线（require_permission 的并集分支）用 custom_codes（仅挂接并集，
不含固定推导）——固定层仍走 threshold 比较，custom_codes=[] 时第二分支
恒 False，与标准版行为零差异（§7.2.2 零差异门禁的实现保证）。
"""
from __future__ import annotations

import json
import logging

from django.conf import settings as dj_settings

logger = logging.getLogger("plane.api.permissions")

#: 缓存 TTL 兜底（秒）——变更面主动 DEL，TTL 只兜 Redis 抖动下的脏读
CACHE_TTL = 300
_KEY_PREFIX = "perm"


_client_singleton: object | None = None
_client_checked = False


def _perm_cache():
    """Redis 客户端（模块级单例 + ping 探测）；不可用返回 None（回源 DB）。

    单例复用连接：<1ms 判定门禁的必要条件（每次 from_url 建连 ≈0.5ms 起）；
    首次探测失败后进程内不再重试（每请求重试 ping 会放大 Redis 故障抖动）。
    """
    global _client_singleton, _client_checked
    if _client_checked:
        return _client_singleton
    _client_checked = True
    try:
        import redis

        client = redis.Redis.from_url(
            dj_settings.REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5,
        )
        client.ping()
        _client_singleton = client
    except Exception:  # noqa: BLE001 —— 任何 Redis 故障均回源（§2.4）
        _client_singleton = None
    return _client_singleton


def _key(user_id: str, project_id: str) -> str:
    return f"{_KEY_PREFIX}:{user_id}:{project_id}"


def _fixed_codes(fixed_role: int | None) -> frozenset[str]:
    """固定角色的码集（按 PERMISSION_MATRIX 阈值推导：threshold ≤ 角色）。"""
    from plane.constants.permissions import PERMISSION_MATRIX

    if fixed_role is None:
        return frozenset()
    return frozenset(
        code for code, threshold in PERMISSION_MATRIX["project"].items()
        if threshold <= fixed_role
    )


def custom_codes(user_id, project_id) -> frozenset[str]:
    """挂接并集（不含固定推导）——require_permission 提升分支的数据源。"""
    from plane.db.models import ProjectRoleAssignment

    codes: set[str] = set()
    for perms in (ProjectRoleAssignment.objects
                  .filter(project_id=project_id, user_id=user_id,
                          deleted_at__isnull=True)
                  .values_list("role__permissions", flat=True)):
        codes.update(perms or [])
    return frozenset(codes)


def custom_codes_bulk(user_id, project_ids) -> dict[str, frozenset[str]]:
    """挂接并集批量版（known-debt #2 收口，P4 R1）：一次 IN 查询取全部
    项目的挂接码集——/users/me/permissions/ 快照接口原逐项目调用
    custom_codes 成 N+1（实测 206 查询 @ 2511 项目）。"""
    from plane.db.models import ProjectRoleAssignment

    ids = list(project_ids)
    if not ids:
        return {}
    result: dict[str, set[str]] = {str(pid): set() for pid in ids}
    rows = (ProjectRoleAssignment.objects
            .filter(project_id__in=ids, user_id=user_id,
                    deleted_at__isnull=True)
            .values_list("project_id", "role__permissions"))
    for pid, perms in rows:
        result[str(pid)].update(perms or [])
    return {k: frozenset(v) for k, v in result.items()}


def effective_codes(user_id, project_id) -> frozenset[str]:
    """有效权限并集（BR-01）：固定 ∪ 自定义（「我的权限」面板与排障口径）。"""
    from plane.db.models import ProjectMember

    key = _key(str(user_id), str(project_id))
    client = _perm_cache()
    if client is not None:
        cached = client.get(key)
        if cached is not None:
            return frozenset(json.loads(cached))
    fixed_role = (ProjectMember.objects
                  .filter(project_id=project_id, member_id=user_id,
                          is_active=True, deleted_at__isnull=True)
                  .values_list("role", flat=True).first())
    codes = _fixed_codes(fixed_role) | custom_codes(user_id, project_id)
    if client is not None:
        try:
            client.setex(key, CACHE_TTL, json.dumps(sorted(codes)))
        except Exception:  # noqa: BLE001 —— 写缓存失败不影响判定
            logger.warning("perm.cache.set_failed user=%s project=%s",
                           user_id, project_id)
    return codes


def has_custom_code(user_id, project_id, code: str) -> bool:
    """require_permission 提升分支：该码是否在挂接并集内（带 Redis 缓存）。"""
    key = _key(str(user_id), str(project_id))
    client = _perm_cache()
    if client is not None:
        cached = client.get(key)
        if cached is not None:
            return code in json.loads(cached)
    # 未命中缓存：只查挂接并集（固定层已由 threshold 分支覆盖）
    result = code in custom_codes(user_id, project_id)
    if client is not None and result:
        # 仅在确有挂接时预热（无挂接者保持零缓存足迹——零差异）
        try:
            fixed = effective_codes(user_id, project_id)
            client.setex(key, CACHE_TTL, json.dumps(sorted(fixed)))
        except Exception:  # noqa: BLE001
            pass
    return result


def invalidate(user_ids, project_id) -> None:
    """变更面主动失效（BR-07）：DEL 而非等 TTL。"""
    client = _perm_cache()
    if client is None:
        return
    keys = [_key(str(u), str(project_id)) for u in user_ids]
    try:
        client.delete(*keys)
    except Exception:  # noqa: BLE001
        logger.warning("perm.cache.invalidate_failed project=%s", project_id)


def invalidate_many(pairs) -> None:
    """批量失效：[(user_id, project_id), ...]。"""
    client = _perm_cache()
    if client is None:
        return
    keys = [_key(str(u), str(p)) for u, p in pairs]
    try:
        for i in range(0, len(keys), 100):
            client.delete(*keys[i:i + 100])
    except Exception:  # noqa: BLE001
        logger.warning("perm.cache.invalidate_many_failed")
