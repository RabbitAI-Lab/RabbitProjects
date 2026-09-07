"""项目域 Activity 写入（PROJ-003 §4.3.1 + ADR-0022 D-2 管道扩域收口）。

与 ``issue_activity`` 平行的独立轨道（为何不复用——PROJ-003 §4.3.1 三点）：

  ① issue 域任务以 issue_id 为必填键，project 域事件无 issue；
  ② 幂等键空间独立：sha256(verb|field|project_id|actor_id|epoch)，与 issue 域
     键天然隔离（file.* 事件带 field 参键——同 actor 同毫秒的两个不同文件操作
     不互斥去重；lifecycle 事件 field='status' / None 同公式入键）；
  ③ verb 仍限 created/updated/deleted 三值（模型 choices），file.*/lifecycle
     语义全部由 field 列承载（'file.uploaded' / 'status' / …）。

失败语义与 issue_activity 同款：不 ack（reject 交 DLX 路由 activity.dlq），
退避 1s/4s/16s。落库成功后尾部扇出 ``activity.created`` 水位锚（COLLAB-004
project 房间——动态流页面前端按 stream_cursor 增量拉取收敛）。
"""
from __future__ import annotations

import hashlib
import logging
import uuid

from celery import shared_task
from django.core.cache import cache
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from plane.db.models import IssueActivity

logger = logging.getLogger(__name__)

LOCK_TTL = 300
DONE_TTL = 86400


def build_project_event_key(payload: dict) -> str:
    """sha256(verb|field|project_id|actor_id|epoch)——issue 域键的同范式扩展。"""
    raw = "|".join(
        str(payload.get(k) or "") for k in ("verb", "field", "project_id", "actor_id", "epoch")
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _write_row(payload: dict) -> tuple[IssueActivity | None, bool]:
    """落库单行（幂等）：同键已存在 → (旧行, False)。

    外键竞态兜底（on_commit 异步窗内资源被硬删——清理脚本/注销竞态）：
    actor 已删 → 降级系统行（留痕优先，BR-13 is_system 口径）；project 已删 →
    行随级联无落点，放弃（重试无意义）。
    """
    event_key_where = {
        "project_id": payload["project_id"],
        "actor_id": payload.get("actor_id"),
        "verb": payload["verb"],
        "field": payload.get("field") or None,
        "epoch": payload["epoch"],
    }
    existing = IssueActivity.objects.filter(**event_key_where).first()
    if existing is not None:
        return existing, False
    create_kwargs = dict(
        **event_key_where,
        old_value=payload.get("old_value"),
        new_value=payload.get("new_value"),
        new_identifier=payload.get("new_identifier"),
        comment=payload.get("comment") or "",
    )
    try:
        with transaction.atomic():
            # FK 为 DEFERRABLE INITIALLY DEFERRED（Django 迁移默认）——违例会推迟
            # 到事务提交点（worker 场景 = create 后的隐式 commit，可捕获；TestCase
            # 场景 = teardown，不可捕获且污染外层事务）。savepoint 内切 IMMEDIATE
            # 统一为写入即抛（savepoint 回滚同时还原约束时序，PostgreSQL 语义）。
            with connection.cursor() as cur:
                cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
            return IssueActivity.objects.create(**create_kwargs), True
    except IntegrityError as exc:
        msg = str(exc)
        if "issue_activities_actor_id" in msg:
            create_kwargs["actor_id"] = None
            return IssueActivity.objects.create(**create_kwargs), True
        if "issue_activities_project_id" in msg:
            logger.warning("project_activity.project_gone project=%s",
                           payload.get("project_id"))
            return None, False
        raise


@shared_task(bind=True, max_retries=3, acks_late=True, acks_on_failure_or_timeout=False)
def project_activity(self, payload: dict) -> None:
    """payload = {project_id, actor_id?, verb, epoch, field?, old_value?,
    new_value?, new_identifier?, comment?}。

    失败不 ack（reject 交 DLX 路由 activity.dlq，与 issue_activity 同轨）；
    退避 1s/4s/16s 显式 countdown。
    """
    event_key = build_project_event_key(payload)
    lock_key, done_key = f"activity-lock:{event_key}", f"activity-dedup:{event_key}"
    if cache.get(done_key):  # ① 完成标记（读）
        return
    if IssueActivity.objects.filter(  # ② DB 同键：终局事实源（Redis 过期后仍准确）
            project_id=payload["project_id"], actor_id=payload.get("actor_id"),
            epoch=payload["epoch"], verb=payload["verb"],
            field=payload.get("field") or None).exists():
        return
    if not cache.add(lock_key, 1, timeout=LOCK_TTL):  # ③ 处理锁
        raise self.retry(countdown=LOCK_TTL + 10)
    try:
        row, _created = _write_row(payload)
        if row is not None:
            cache.set(done_key, 1, timeout=DONE_TTL)  # 仅落库成功后写 ①
            _publish_water_level(row)
    except Exception as exc:  # noqa: BLE001
        cache.delete(lock_key)  # 失败释放锁：重试/重放可重入
        if self.request.retries >= self.max_retries:
            raise  # 轨道走完 → reject 入 dlq（task_failure 信号落元数据）
        raise self.retry(countdown=4**self.request.retries, exc=exc) from exc


def _publish_water_level(row: IssueActivity) -> None:
    """落库尾部：activity.created 水位锚（COLLAB-004 project 房间，尽力而为）。

    project 域行无 issue——载荷不带 issue_id（issue 域发布器 publish_activity_created
    的签名字段不适用），直接走 dispatch_event 装配同构载荷。
    """
    try:
        from plane.bgtasks.event_publisher import EVENT_MAP, dispatch_event

        occurred_at = row.created_at.isoformat()
        dispatch_event(
            "activity.created",
            {"project_id": str(row.project_id),
             "stream_cursor": f"{occurred_at}:{row.id}",
             "actor_id": str(row.actor_id) if row.actor_id else None,
             "occurred_at": occurred_at},
            EVENT_MAP["activity.created"]({"project_id": str(row.project_id)}),
        )
    except Exception as exc:  # noqa: BLE001 —— 扇出尽力而为，绝不阻断落库主流程
        logger.warning("project_activity.water_level_failed project=%s err=%s",
                       row.project_id, exc)


def enqueue_project_activity(
    *,
    project_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    verb: str,
    field: str | None = None,
    old_value: str | None = None,
    new_value: str | None = None,
    new_identifier: uuid.UUID | None = None,
    comment: str = "",
    epoch: float | None = None,
) -> None:
    """写路径投递入口（on_commit 回调中调用；broker 不可用时降级直写不阻塞主请求）。

    epoch 缺省 = 当前毫秒（TASK-010 BR-04 同范式：Service 入口生成一次、
    Worker 仅消费不重算）。
    """
    payload = {
        "project_id": str(project_id),
        "actor_id": str(actor_id) if actor_id else None,
        "verb": verb,
        "field": field,
        "old_value": old_value,
        "new_value": new_value,
        "new_identifier": str(new_identifier) if new_identifier else None,
        "comment": comment,
        "epoch": epoch if epoch is not None else timezone.now().timestamp() * 1000.0,
    }
    try:
        project_activity.delay(payload)
    except Exception:  # noqa: BLE001 —— 降级：同步落库（保「业务成功可追溯」底线）
        _write_row(payload)
        logger.warning("project_activity.dispatch_fallback_sync project=%s", project_id)


@shared_task(bind=True, max_retries=3, acks_late=True, acks_on_failure_or_timeout=False)
def record_project_activity(
    self, project_id: str, verb: str, new_status: str, *, actor_id: str,
    milestone: bool = False, old_status: str = "", epoch: float | None = None,
) -> None:
    """生命周期事件薄壳（PROJ-003 §4.3.1 规格签名，T5-07 消费）——转发
    ``project_activity`` 幂等轨道。参数语义见规格：verb='updated'（迁移）/
    'created'（新建），field='status'，milestone=True → comment='milestone'
    （COLLAB-003 菱形节点读取位）。"""
    enqueue_project_activity(
        project_id=uuid.UUID(str(project_id)),
        actor_id=uuid.UUID(str(actor_id)) if actor_id else None,
        verb=verb, field="status", old_value=old_status or None, new_value=new_status,
        comment="milestone" if milestone else "",
        epoch=epoch,
    )
