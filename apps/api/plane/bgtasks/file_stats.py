"""文件下载计数（FILE-002 BR-10）。

口径：Redis 计数 → beat 批量落库（避免热点行零同步直写）。
- ``incr_download_count(asset_id)``：download-url 签发路径同步调用（O(1) Redis
  HINCRBY，无 DB 写）；Redis 不可达时降级为单行 F() 直写（保计数正确，牺牲
  「零同步直写」目标——与 event_publisher 同款降级纪律，只告警不阻断）。
- ``flush_download_counts``：beat 周期任务，HGETALL 全量 → F("download_count")+delta
  → HDEL（读删非原子窗口的重叠投递由 beat 单实例调度天然规避）。
"""
from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.db.models import F

logger = logging.getLogger("plane.bgtasks.file_stats")

#: Redis Hash 键：field=asset_id，value=待落库的下载增量
DOWNLOAD_COUNT_KEY = "rp:file:download_counts"

_redis_client: Any = None
_redis_unavailable = False


def _redis():
    """Redis 连接（进程级缓存 + 不可达降级短路，范式同 event_publisher）。"""
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
        except Exception as exc:  # noqa: BLE001 —— 降级路径：Redis 故障不阻断签发
            _redis_unavailable = True
            logger.warning("file_stats.redis_unavailable degrade=direct_write exc=%s", exc)
            return None
    return _redis_client


def reset_redis_state() -> None:
    """测试辅助：重置进程级 Redis 状态（每用例独立判定可用性）。"""
    global _redis_client, _redis_unavailable
    _redis_client = None
    _redis_unavailable = False


def incr_download_count(asset_id) -> None:
    """下载计数 +1：Redis 缓冲；不可达降级单行直写（BR-10）。"""
    client = _redis()
    if client is None:
        from plane.db.models import FileAsset

        FileAsset.objects.filter(pk=asset_id).update(
            download_count=F("download_count") + 1
        )
        return
    client.hincrby(DOWNLOAD_COUNT_KEY, str(asset_id), 1)


@shared_task(
    bind=True,
    max_retries=3,
    name="plane.bgtasks.file_stats.flush_download_counts",
)
def flush_download_counts(self) -> dict:
    """beat 批量落库：Redis 计数 → download_count 累加（BR-10 异步累加终点）。"""
    client = _redis()
    if client is None:
        return {"flushed": 0, "skipped_no_redis": True}
    from plane.db.models import FileAsset

    pending = client.hgetall(DOWNLOAD_COUNT_KEY)
    flushed = 0
    for asset_id, delta_text in pending.items():
        try:
            delta = int(delta_text)
        except (TypeError, ValueError):
            logger.warning("file_stats.bad_delta asset=%s value=%r", asset_id, delta_text)
            client.hdel(DOWNLOAD_COUNT_KEY, asset_id)
            continue
        if delta <= 0:
            client.hdel(DOWNLOAD_COUNT_KEY, asset_id)
            continue
        updated = FileAsset.objects.filter(pk=asset_id).update(
            download_count=F("download_count") + delta
        )
        if updated:
            flushed += 1
        # 行不存在（已硬删）：计数一并丢弃
        client.hdel(DOWNLOAD_COUNT_KEY, asset_id)
    return {"flushed": flushed}
