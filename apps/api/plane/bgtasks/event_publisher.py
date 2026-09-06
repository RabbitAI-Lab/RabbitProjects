"""实时事件扇出（COLLAB-004 §4.3.4，T3-11）——api → live 唯一通道 Redis Pub/Sub。

``publish_event``（shared_task）：``{event, rooms, payload, occurred_at}`` →
channel ``rp:events``。语义：

  - **推送是提示不是数据源**（§1.1 红线一）：载荷 ≤ 2KB（BR-05，超限丢弃 + ERROR），
    全量实体禁入 payload；正文一律 REST 拉取收敛；
  - **幂等无害**（BR-06）：无订阅者（live 宕机）自然丢弃，不落库不重放；
  - **尽力而为**（§4.3.4）：broker/Redis 不可用 warning 不抛——推送失败仅意味着
    对端「晚一轮 SWR 拉取收敛」，拉取兜底保数据正确；
  - rooms 由业务侧给定（挂点 helper ``publish_for_issue`` 等收口，业务代码一行调用）。

EVENT_MAP 为六类事件契约的**单一口径**（§2.3 事件协议表；与 packages/types 的
``LiveEventName`` 常量跨端对齐）。
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

#: Redis Pub/Sub channel（BR-09：api→live 唯一通道；live 无状态可横扩）
CHANNEL = "rp:events"

#: 载荷红线（BR-05）：整条消息（信封含 rooms）≤ 2KB，超限丢弃 + ERROR
MAX_MESSAGE_BYTES = 2048

#: 六类核心事件（§2.3）。room 映射 lambda 供挂点 helper 复用——p 为 payload dict。
EVENT_MAP: dict[str, Callable[[dict[str, Any]], list[str]]] = {
    # project + 涉事 issue（看板列 / 列表行 / 详情定向 patch）
    "issue.updated": lambda p: [f"project:{p['project_id']}", f"issue:{p['issue_id']}"],
    # project + issue（列计数迁移 / 统计卡）
    "issue.state.changed": lambda p: [f"project:{p['project_id']}", f"issue:{p['issue_id']}"],
    # 仅 project（同列远端顺序修正，BOARD-003 消费）
    "board.moved": lambda p: [f"project:{p['project_id']}"],
    # issue（详情评论流）+ project 摘要（看板卡片徽标）
    "comment.created": lambda p: [f"issue:{p['issue_id']}", f"project:{p['project_id']}"],
    # project（动态流水位推进，正文按 stream_cursor 拉取）
    "activity.created": lambda p: [f"project:{p['project_id']}"],
    # user 个人房间（铃铛秒达，COLLAB-001 轮询通道的加速器）
    "notification.created": lambda p: [f"user:{p['receiver_id']}"],
    # ── FILE-003 §4.4：file 域两事件（rooms = project + file:{asset_id} 第四类
    #    房间——live 侧消费由 T4-08 接线；api 侧 on_commit 投递本任务交付）──
    "file.version.created": lambda p: [f"project:{p['project_id']}", f"file:{p['asset_id']}"],
    "file.transcode.completed": lambda p: [f"project:{p['project_id']}", f"file:{p['asset_id']}"],
    # ── FILE-004 BR-13：分享生命周期三事件（同 file 域房间模型；创建/吊销/延期
    #    入内部视角，匿名访问**不入**——防匿名刷接口刷屏事件流）──
    "file.share.created": lambda p: [f"project:{p['project_id']}", f"file:{p['asset_id']}"],
    "file.share.revoked": lambda p: [f"project:{p['project_id']}", f"file:{p['asset_id']}"],
    "file.share.extended": lambda p: [f"project:{p['project_id']}", f"file:{p['asset_id']}"],
}


# ─────────────────────────────────────────────────────────────────────
# Redis 客户端（进程级懒加载；不可用降级标记，不反复重连放大延迟）
# ─────────────────────────────────────────────────────────────────────
_redis_client: Any = None
_redis_unavailable = False


def _redis():
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
        except Exception as exc:  # noqa: BLE001 —— 降级路径：Redis 故障不阻断推送链路
            _redis_unavailable = True
            logger.warning("event_publisher.redis_unavailable degrade=drop exc=%s", exc)
            return None
    return _redis_client


def reset_redis_state() -> None:
    """测试辅助：重置进程级 Redis 状态（每用例独立判定可用性）。"""
    global _redis_client, _redis_unavailable
    _redis_client = None
    _redis_unavailable = False


def build_message(event: str, payload: dict[str, Any], rooms: list[str],
                  occurred_at: str | None = None) -> str:
    """装配 Redis 消息（§4.2.2 格式）；occurred_at 缺省 = 当前时刻 ISO。"""
    return json.dumps(
        {
            "event": event,
            "rooms": rooms,
            "payload": payload,
            "occurred_at": occurred_at or timezone.now().isoformat(),
        },
        ensure_ascii=False, default=str,
    )


@shared_task(bind=True, max_retries=2, name="plane.bgtasks.event_publisher.publish_event")
def publish_event(self, event: str, payload: dict[str, Any], rooms: list[str],
                  occurred_at: str | None = None) -> bool:
    """事件发布（Worker 内执行）：2KB 红线校验 → Redis publish。

    返回 False = 丢弃（超限 / Redis 不可用重试耗尽）——均不抛错（尽力而为）。
    """
    if event not in EVENT_MAP:
        logger.error("event_publisher.unknown_event event=%s", event)
        return False
    message = build_message(event, payload, rooms, occurred_at)
    if len(message.encode("utf-8")) > MAX_MESSAGE_BYTES:  # BR-05 前置红线
        logger.error("event_payload_oversize event=%s bytes=%d",
                     event, len(message.encode("utf-8")))
        return False
    try:
        client = _redis()
        if client is None:
            return False
        client.publish(CHANNEL, message)
        return True
    except Exception as exc:  # noqa: BLE001 —— Redis 抖动：两次退避重试后放弃
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=1, exc=exc) from exc
        logger.warning("event_publisher.giveup event=%s exc=%s", event, exc)
        return False


def dispatch_event(event: str, payload: dict[str, Any], rooms: list[str],
                   occurred_at: str | None = None) -> None:
    """投递入口（挂点 helper 统一走此）：broker 不可用 warning 不抛——
    推送尽力而为，拉取兜底（§4.3.4）。"""
    try:
        publish_event.apply_async(
            args=[event, payload, rooms, occurred_at],
            retry=False,  # 同步路径不做 publish 期重试（broker 抖动交给业务重试语义）
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("event_publisher.dispatch_failed event=%s exc=%s", event, exc)


# ─────────────────────────────────────────────────────────────────────
# 挂点 helper（业务代码一行调用收口，T3-11 接线）
# ─────────────────────────────────────────────────────────────────────
def publish_for_issue(event: str, *, issue_id: str, project_id: str,
                      actor_id: str | None, payload: dict[str, Any] | None = None,
                      batch_id: float | None = None) -> None:
    """issue 域事件（issue.updated / issue.state.changed / board.moved）：
    rooms = [project, issue]（board.moved 仅 project）；payload 附 actor_id / batch_id。"""
    body: dict[str, Any] = {"issue_id": str(issue_id), "actor_id": actor_id and str(actor_id)}
    body.update(payload or {})
    if batch_id is not None:
        body["batch_id"] = batch_id          # BR-15：批量操作逐实体共享 epoch 同值
    dispatch_event(event, body, EVENT_MAP[event]({
        "project_id": str(project_id), "issue_id": str(issue_id)}))


def publish_comment_created(*, comment_id: str, issue_id: str, project_id: str,
                            actor_id: str) -> None:
    """评论发表（comment.py 落库后 on_commit 调用）：issue 房间 + project 摘要。"""
    dispatch_event("comment.created",
                   {"comment_id": str(comment_id), "issue_id": str(issue_id),
                    "project_id": str(project_id), "actor_id": str(actor_id)},
                   EVENT_MAP["comment.created"]({
                       "issue_id": str(issue_id), "project_id": str(project_id)}))


def publish_activity_created(*, project_id: str, issue_id: str,
                             stream_cursor: str, actor_id: str | None,
                             occurred_at: str) -> None:
    """动态流落库（Worker 尾部）：payload 仅水位锚——正文由前端按 stream_cursor
    增量拉取（COLLAB-003 BR-12 契约）。"""
    dispatch_event("activity.created",
                   {"project_id": str(project_id), "issue_id": str(issue_id),
                    "stream_cursor": stream_cursor, "actor_id": actor_id and str(actor_id),
                    "occurred_at": occurred_at},
                   EVENT_MAP["activity.created"]({"project_id": str(project_id)}))


def publish_notifications_created(rows) -> None:
    """通知落库后（notify.py）：按 receiver 查回 id（bulk_create + ignore_conflicts
    不回填 PK），逐条 notification.created → user 个人房间（unread_delta=1，
    前端按 notification_id 去重）。"""
    if not rows:
        return
    try:
        from plane.db.models import Notification

        keys = [r.dedup_key for r in rows if r.dedup_key]
        if not keys:
            return
        created = Notification.objects.filter(
            dedup_key__in=keys,
        ).values_list("id", "receiver_id")
        for nid, rid in created:
            dispatch_event("notification.created",
                           {"notification_id": str(nid), "unread_delta": 1},
                           [f"user:{rid}"])
    except Exception as exc:  # noqa: BLE001 —— 推送尽力而为，不阻断通知主流程
        logger.warning("event_publisher.notifications_created_failed exc=%s", exc)


# ─────────────────────────────────────────────────────────────────────
# file 域挂点 helper（FILE-003 §4.4 事件登记表；on_commit 调用收口）
# ─────────────────────────────────────────────────────────────────────
def publish_file_version_created(*, project_id: str, asset_id: str,
                                 version_number: int, actor_id: str | None,
                                 source_version_number: int | None = None) -> None:
    """新版本落库 / 回滚（services.upload_session._new_version 的 on_commit）。

    payload 要点（§4.4）：asset_id / version_number / actor_id /
    source_version_number（回滚时携带）——VersionPanel mutate 与动态流水位增量。
    """
    payload: dict[str, Any] = {
        "asset_id": asset_id,
        "version_number": version_number,
        "actor_id": actor_id,
    }
    if source_version_number is not None:
        payload["source_version_number"] = source_version_number
    dispatch_event(
        "file.version.created", payload,
        EVENT_MAP["file.version.created"]({
            "project_id": str(project_id), "asset_id": str(asset_id)}),
    )


def publish_file_transcode_completed(*, project_id: str, asset_id: str,
                                     derivative_kind: str) -> None:
    """转码 / 缩略 / 封面帧任务成功（含冷清理后重生成，BR-11）——Worker 尾部
    直接调用（无事务上下文，不走 on_commit）；排队态预览自动刷新（202 → 就绪）。"""
    dispatch_event(
        "file.transcode.completed",
        {"asset_id": str(asset_id), "derivative_kind": derivative_kind},
        EVENT_MAP["file.transcode.completed"]({
            "project_id": str(project_id), "asset_id": str(asset_id)}),
    )


# ─────────────────────────────────────────────────────────────────────
# file.share 域挂点 helper（FILE-004 BR-13；on_commit 调用收口）
# ─────────────────────────────────────────────────────────────────────
def publish_share_event(event: str, *, project_id: str, asset_id: str,
                        share_id: str, actor_id: str | None,
                        extra: dict[str, Any] | None = None) -> None:
    """分享生命周期事件（created / revoked / extended）——内部视角专用，
    匿名访问路径（meta/unlock/content）**不投递**（BR-13 防刷屏）。"""
    payload: dict[str, Any] = {
        "share_id": str(share_id),
        "asset_id": str(asset_id),
        "actor_id": actor_id and str(actor_id),
    }
    payload.update(extra or {})
    dispatch_event(event, payload, EVENT_MAP[event]({
        "project_id": str(project_id), "asset_id": str(asset_id)}))


# ─────────────────────────────────────────────────────────────────────
# Worker 尾部：Activity 行 → 六类事件映射（TASK-010 尾部扇出挂点）
# ─────────────────────────────────────────────────────────────────────
#: field → 事件（issue.state.changed / board.moved / 其余 issue.updated）。
#: brief = 字段族提示（§1.4：前端据此选择定向 patch 范围）。
_STATE_FIELD = "state"
_SORT_FIELD = "sort_order"


def publish_activity_events(*, issue_id: str, actor_id: str | None, verb: str,
                            field: str | None,
                            activity_id: str, activity_created_at: datetime,
                            old_identifier: str | None = None,
                            new_identifier: str | None = None,
                            batch_id: float | None = None) -> None:
    """``_write_activity_row`` 落库成功后调用（COLLAB-004 §4.3.4 Worker 尾部）。

    映射（T3-11 契约）：
      field == state       → issue.state.changed（rooms = project + issue）
      field == sort_order  → board.moved（rooms = 仅 project，§2.3）
      其余任意行          → issue.updated（rooms = project + issue）
      恒定                 → activity.created（rooms = project，仅水位锚载荷）
    批量载荷（record_activity_batch）传 batch_id=同批共享 epoch（BR-15：逐实体
    复用既有事件 + batch_id，单条操作不携带该字段；100ms 合批由 live 收敛）。
    扇出尽力而为：任何异常 warning 吞掉，绝不阻断 Activity 落库主流程。
    """
    try:
        from plane.db.models import Issue, State

        issue = (Issue.objects.select_related("state")
                 .only("id", "project_id", "updated_at", "state__group")
                 .filter(id=issue_id).first())
        if issue is None:  # 任务已删：房间无消费者，静默跳过
            return
        pid, iid = str(issue.project_id), str(issue.id)
        actor = str(actor_id) if actor_id else None
        occurred_at = activity_created_at.isoformat()
        # 水位锚 = COLLAB-003 stream_cursor 同形态（activity_stream.py：{created_at}:{id}）
        publish_activity_created(
            project_id=pid, issue_id=iid,
            stream_cursor=f"{occurred_at}:{activity_id}",
            actor_id=actor, occurred_at=occurred_at)

        common: dict[str, Any] = {"version": issue.updated_at.isoformat()}
        if field == _STATE_FIELD:
            groups: dict[str, Any] = {"from_group": None, "to_group": None}
            ids = [i for i in (old_identifier, new_identifier) if i]
            if ids:
                group_by_id = {
                    str(sid): group
                    for sid, group in State.objects.filter(
                        id__in=ids).values_list("id", "group")
                }
                groups = {
                    "from_group": old_identifier and group_by_id.get(str(old_identifier)),
                    "to_group": new_identifier and group_by_id.get(str(new_identifier)),
                }
            publish_for_issue(
                "issue.state.changed", issue_id=iid, project_id=pid, actor_id=actor,
                payload={"brief": "state", **common, **groups}, batch_id=batch_id)
        elif field == _SORT_FIELD:
            # 同列排序（from == to = 当前列组）；column_version = 本次写入时刻
            # （§2.3 注 3：目标列排序版本，前端列粒度比对防同列并发乱序）
            group = issue.state.group if issue.state else None
            publish_for_issue(
                "board.moved", issue_id=iid, project_id=pid, actor_id=actor,
                payload={"brief": "sort_order", "from_group": group, "to_group": group,
                         "column_version": occurred_at},
                batch_id=batch_id)
        else:
            publish_for_issue(
                "issue.updated", issue_id=iid, project_id=pid, actor_id=actor,
                payload={"brief": field or verb, **common}, batch_id=batch_id)
    except Exception as exc:  # noqa: BLE001 —— 扇出尽力而为，绝不阻断 Activity 落库主流程
        logger.warning("event_publisher.activity_hook_failed issue=%s exc=%s", issue_id, exc)
