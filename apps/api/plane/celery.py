"""Celery 实例（RabbitMQ 唯一 broker —— tech-stack.md 决策；P0 无异步任务，编排见 INFRA-002）。

任务注册：``plane.bgtasks.*`` 不是 Django app 内的 ``tasks.py``，``autodiscover_tasks``
（按 app 找 ``<app>.tasks``）发现不到它们——web 进程在调用点 import 后注册、
worker 进程却从未 import，会全部 ``Received unregistered task``。故用 ``include``
显式列出 bgtasks 模块（新增模块必须同步登记，TASK-008 起 worker 侧真实消费）。
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "plane.settings.dev")

app = Celery(
    "rabbit_projects",
    broker=os.environ.get("CELERY_BROKER_URL", "amqp://rp:rp@localhost:5672//"),
    include=[
        "plane.bgtasks.activity_dlq",      # TASK-010：task_failure 信号（worker 进程加载）
        "plane.account.tasks",           # 密码重置邮件（sprint-1 历史遗漏，worker 侧 unregistered 修复）
        "plane.bgtasks.asset_cleanup",
        "plane.bgtasks.comments",
        "plane.bgtasks.derive_preview",     # FILE-003：预览派生 + 会话/衍生物/版本治理
        "plane.bgtasks.event_publisher",   # COLLAB-004 T3-11：实时事件扇出（rp:events）
        "plane.bgtasks.field_cleanup",     # TASK-008：删除字段值清理 / 视图剔除
        "plane.bgtasks.github_sync",       # INTG-001：GitHub 入站事件路由（Sprint-5）
        "plane.bgtasks.field_index",       # TASK-008：表达式偏索引 CONCURRENTLY 建/删
        "plane.bgtasks.file_stats",        # FILE-002：下载计数 Redis→beat 批量落库
        "plane.bgtasks.issue_assignee",
        "plane.bgtasks.issue_activity",    # TASK-010：幂等三层 Worker（activity 队列）
        "plane.bgtasks.issue_hierarchy",
        "plane.bgtasks.issue_link",
        "plane.bgtasks.notifications",
        "plane.bgtasks.project_activity",  # Sprint-5：project 域 Activity 幂等轨道（activity 队列）
        "plane.bgtasks.share_sweep",       # FILE-004：过期分享清扫（beat 每小时）
        "plane.bgtasks.worklog",
        "plane.bgtasks.workspace_invite",
        "plane.db.services.webhook_outbound",   # INTG-002：出站投递/清理/到期扫描
    ],
)
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# ── FILE-004 §4.3.4：beat 调度注册（sweep_expired_shares 每小时整点）──
# 仓库首个原生 beat_schedule（此前各任务的周期触发未在代码内登记——compose
# beat 入口用 django_celery_beat DatabaseScheduler，而该库未装依赖，dev 环境以
# `celery -A plane beat` 默认 PersistentScheduler 消费本表；偏差登记 ADR-0022）。
from celery.schedules import crontab  # noqa: E402

app.conf.beat_schedule = {
    "sweep-expired-shares": {
        "task": "plane.bgtasks.share_sweep.sweep_expired_shares",
        "schedule": crontab(minute=0),
    },
    "purge-webhook-deliveries": {
        "task": "plane.db.services.webhook_outbound.purge_webhook_deliveries",
        "schedule": crontab(hour=3, minute=30),       # 每日 03:30 滚动清理（§4.1）
    },
    "retry-due-webhook-deliveries": {
        "task": "plane.db.services.webhook_outbound.retry_due_deliveries",
        "schedule": crontab(minute="*/5"),             # 自调度丢失兜底（§4.3）
    },
}


# ── TASK-010 §4.3.2：activity 队列 + DLX 死信路由（本迭代交付，非默认行为）──
# task_acks_on_failure_or_timeout 必须 False：失败消息才以 basic.reject 交给
# x-dead-letter-exchange 而非被 ack 丢弃；worker 侧 -Q 白名单需含 activity
# （apps/api/bin/docker-entrypoint-worker.sh 与 INFRA-002 CT-05 已同步回改）。
from kombu import Exchange, Queue  # noqa: E402

app.conf.task_acks_on_failure_or_timeout = False
app.conf.task_routes = {
    "plane.bgtasks.issue_activity.issue_activity": {"queue": "activity"},
    # Sprint-5：project 域轨道与薄壳同入 activity 队列（共用 DLX activity.dlq）
    "plane.bgtasks.project_activity.project_activity": {"queue": "activity"},
    "plane.bgtasks.project_activity.record_project_activity": {"queue": "activity"},
}
app.conf.task_queues = (
    Queue("activity", Exchange("activity", type="direct"), routing_key="activity",
          queue_arguments={
              "x-dead-letter-exchange": "activity.dlx",
              "x-dead-letter-routing-key": "activity.dlq"}),
    Queue("activity.dlq", Exchange("activity.dlx", type="direct"), routing_key="activity.dlq"),
)
