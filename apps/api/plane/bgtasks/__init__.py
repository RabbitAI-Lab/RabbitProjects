"""Celery 任务模块 —— plane.celery.app autodiscover_tasks 自动发现。

本包承载所有 ``@shared_task`` 装饰的异步任务。当前承载：
- notifications：站内通知落库
- workspace_invite：邀请邮件投递 + beat 过期清理

约定：
1. 任务只接受 ID/Token 等不可变参数；不接受 ORM 对象（避免序列化过期快照，
   api-conventions.md §10.5）。
2. 任务必须幂等 —— MQ 可能重复投递（idempotency_keys、状态守卫）。
3. broker 不可达时 ``task.delay()`` 会抛 ``OperationalError``；调用方负责
   ``try/except`` 收口，本模块不静默吞错。
"""
