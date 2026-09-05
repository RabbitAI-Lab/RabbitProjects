"""会话吊销工具（AUTH-004 §4.3.5）。

Session 后端为 ``django.contrib.sessions.backends.cache``（指向 ``default`` cache）。
为支持「吊销某用户除当前会话外的全部会话 / 全部会话」，登录后写入一张
**用户会话索引 SET**，吊销时按集合删除 key 即时生效。

精确性约束（§4.3.5 末段）：索引是「尽力而为」的视图——直接清 Valkey、进程崩溃
等极端场景可能残留已死 key 或漏记。这不构成安全缺口：吊销动作删除的是 session
key 本体，漏删的最多是「本就已失效的 key」。P2 ``GET /users/me/sessions/`` 上线时
升级为「索引 + session 数据反查」双保险。

降级：
- cache 后端缺失 / 不可用 → 索引调用静默失败，主流程不阻塞（被吊销的会话自然
  过期）。不抛异常、只记 WARN 日志。
"""
from __future__ import annotations

import logging
from collections.abc import Iterable

from django.core.cache import caches

logger = logging.getLogger("plane.account.sessions")

#: 索引 TTL 略长于最长会话（30 天记住我），自然过期兜底
SESSION_INDEX_TTL = 60 * 60 * 24 * 31

#: cache 别名 —— AUTH-004 §1.5 期望 Valkey DB 1，但当前 settings 未配 CACHES，
#: 回落到默认（LocMem）即可保证本机开发期可工作；生产由 INFRA-002 注入 Redis。
SESSION_CACHE_ALIAS = "default"


def _cache():
    return caches[SESSION_CACHE_ALIAS]


def _index_key(user_id) -> str:
    return f"user_sessions:{user_id}"


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # cache backend down etc. — never block main flow
        logger.warning("session_index.op_failed op=%s err=%s", fn.__name__, exc)
        return None


def track_session(user_id, session_key: str) -> None:
    """登录建立会话后调用（挂在 SignInView / SignUpView 尾部）。

    重复调用同一 (user, key) 是幂等的（SADD）。
    """
    if not user_id or not session_key:
        return
    cache = _cache()
    key = _index_key(user_id)
    _safe(cache.set_many if hasattr(cache, "set_many") else _noop,
          {})  # 探测可达性；下面真正写
    try:
        cache.set(key, cache.get(key, set()), SESSION_INDEX_TTL)  # ensure TTL
    except Exception:
        pass
    # Redis/LocMem 都支持 SET 数据结构在 django-redis；LocMemCache 不支持 sadd，
    # 所以这里用 dict-of-set 的简化实现：cache 一个 tuple 列表兼容性最好。
    existing = cache.get(key) or set()
    if not isinstance(existing, set):
        existing = set(existing) if existing else set()
    existing.add(session_key)
    _safe(cache.set, key, existing, SESSION_INDEX_TTL)


def revoke_other_sessions(user_id, keep_session_key: str | None) -> int:
    """吊销该用户除 ``keep_session_key`` 外的全部 session。

    返回吊销的 session 数（== 删除的 session key 数）。
    """
    if not user_id:
        return 0
    cache = _cache()
    key = _index_key(user_id)
    members: Iterable = cache.get(key) or set()
    if not isinstance(members, set):
        members = set(members) if members else set()
    to_delete = [k for k in members if k and k != keep_session_key]
    if to_delete:
        _safe(cache.delete_many, to_delete)
        remaining = members - set(to_delete)
        if remaining:
            _safe(cache.set, key, remaining, SESSION_INDEX_TTL)
        else:
            _safe(cache.delete, key)
    return len(to_delete)


def delete_all_sessions(user_id) -> int:
    """吊销该用户全部 session（重置密码场景）。

    返回吊销的 session 数。
    """
    if not user_id:
        return 0
    cache = _cache()
    key = _index_key(user_id)
    members: Iterable = cache.get(key) or set()
    if not isinstance(members, set):
        members = set(members) if members else set()
    count = len(members)
    if members:
        _safe(cache.delete_many, list(members))
    _safe(cache.delete, key)
    return count


def _noop(*args, **kwargs):
    return None
