"""Celery 实例（RabbitMQ 唯一 broker —— tech-stack.md 决策；P0 无异步任务，编排见 INFRA-002）。"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "plane.settings.local")

app = Celery(
    "rabbit_projects", broker=os.environ.get("CELERY_BROKER_URL", "amqp://rp:rp@localhost:5672//")
)
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
