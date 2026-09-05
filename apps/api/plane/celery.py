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
        "plane.bgtasks.asset_cleanup",
        "plane.bgtasks.comments",
        "plane.bgtasks.field_cleanup",     # TASK-008：删除字段值清理 / 视图剔除
        "plane.bgtasks.field_index",       # TASK-008：表达式偏索引 CONCURRENTLY 建/删
        "plane.bgtasks.issue_assignee",
        "plane.bgtasks.issue_hierarchy",
        "plane.bgtasks.issue_link",
        "plane.bgtasks.notifications",
        "plane.bgtasks.worklog",
        "plane.bgtasks.workspace_invite",
    ],
)
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
