"""工作流域信号（WF-001 §4.5 / WF-002 §2.3）——缓存失效与审批终止钩子。

- Workflow post_save/post_delete → 解析缓存失效（范式同 TASK-008 字段定义缓存）；
- Issue pre_save 快照 + post_save 比较 → deleted_at / archived_at 写入即终止
  该任务 pending 审批实例（issue_deleted / issue_archived 挂点；注册点
  apps.py.ready()——软删走信号而非 post_delete，TASK-009 §2.3 口径）。
"""
from __future__ import annotations

import logging

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from plane.db.models import Issue, Workflow
from plane.workflow.services import invalidate_workflow_cache

logger = logging.getLogger(__name__)


@receiver([post_save, post_delete], sender=Workflow)
def workflow_cache_invalidate(sender, instance: Workflow, **kwargs) -> None:
    invalidate_workflow_cache(instance)


@receiver(pre_save, sender=Issue)
def issue_lifecycle_snapshot(sender, instance: Issue, **kwargs) -> None:
    """pre_save 快照 deleted_at / archived_at 旧值（post_save 比较写入沿）。

    注意 pk 带 default（uuid4），未落库实例 pk 已有值——须用 _state.adding 判新建
    （同 custom_field.py 的教训）。软删行走 save()（TASK-009），快照有效；
    queryset.update() 不触发本信号（终止由引擎/视图显式调用承载）。
    """
    if not instance._state.adding:
        prev = Issue.all_objects.filter(pk=instance.pk).values_list(
            "deleted_at", "archived_at").first()
        instance._wf_prev_deleted, instance._wf_prev_archived = (  # type: ignore[attr-defined]  # 瞬态快照
            prev if prev is not None else (None, None))
    else:
        instance._wf_prev_deleted, instance._wf_prev_archived = None, None  # type: ignore[attr-defined]


@receiver(post_save, sender=Issue)
def issue_lifecycle_terminate_approvals(sender, instance: Issue, **kwargs) -> None:
    """WF-002 §2.3：任务软删/归档写入钩子 → 终止 pending 审批实例（单 UPDATE）。"""
    from plane.workflow.approval import terminate_pending_instances

    prev_deleted = getattr(instance, "_wf_prev_deleted", None)
    prev_archived = getattr(instance, "_wf_prev_archived", None)
    if prev_deleted is None and instance.deleted_at is not None:
        n = terminate_pending_instances(instance.id, "issue_deleted")
        if n:
            logger.info("approval.terminated_by_issue_deleted issue=%s n=%s", instance.id, n)
    if prev_archived is None and instance.archived_at is not None:
        n = terminate_pending_instances(instance.id, "issue_archived")
        if n:
            logger.info("approval.terminated_by_issue_archived issue=%s n=%s", instance.id, n)


def register_signals() -> None:
    """apps.py.ready() 调用入口（显式注册，防重复 import 语义漂移）。"""
    # @receiver 装饰器在模块导入时已注册；本函数保留为显式锚点（同 field_schema 范式）。
