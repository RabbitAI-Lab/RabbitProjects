"""工作流域 Celery 任务（WF-002 §4.7）——审批通知与超时扫描。

`workflow` 队列为 Sprint-7 新增（WF-002/003/005/006 共用），随本文件登记
tech-stack §9 待补登；worker 侧 -Q 白名单需同步加 workflow（坑 20：改队列
参数前先杀旧 worker）。
"""
from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from plane.db.models import ApprovalInstance, ApprovalRecord, Notification

logger = logging.getLogger(__name__)


def _instance_payload(instance: ApprovalInstance) -> dict:
    issue = instance.issue
    project = issue.project
    return {
        "instance_id": str(instance.id),
        "issue_id": str(issue.id),
        "issue_key": f"{project.identifier}-{issue.sequence_id}",
        "issue_name": issue.name,
        "project_id": str(project.id),
        "workspace_slug": project.workspace.slug,
        "flow_name": instance.flow_snapshot["name"],
        "level": instance.current_level,
    }


@shared_task(queue="notifications", ignore_result=True)
def notify_approvers(instance_id: str, level: int) -> None:
    """当前级 pending 审批票 → 收件箱（COLLAB-001 通道）；去重键 instance+level。

    状态已变（非 pending / 级已推进）时通知作废（延迟窗口防护，§4.7）。
    """
    instance = ApprovalInstance.objects.select_related(
        "issue", "issue__project", "issue__project__workspace").get(id=instance_id)
    if instance.status != ApprovalInstance.Status.PENDING or instance.current_level != level:
        return
    payload = _instance_payload(instance)
    for rec in instance.records.filter(level=level, action=ApprovalRecord.Action.PENDING):
        title = f"审批待办：{payload['flow_name']} 第 {level} 级 · {payload['issue_key']}"
        try:
            Notification.objects.create(
                receiver_id=rec.approver_id,
                event="approval.todo",
                title=title[:200],
                data=payload,
                dedup_key=Notification.build_dedup_key(
                    event="approval.todo", issue_id=instance.issue_id,
                    actor_id=instance.initiator_id, epoch=f"{instance.id}:{level}",
                    receiver_id=str(rec.approver_id),
                ),
            )
        except Exception as exc:  # noqa: BLE001 —— 幂等冲突视为已投递
            if "uniq_notif_dedup" not in str(exc):
                logger.warning("approval.notify_failed instance=%s level=%s exc=%s",
                               instance_id, level, exc)


@shared_task(queue="workflow")
def approval_timeout_scan() -> None:
    """beat 每 15min（§2.4）：到达超时点提醒审批人；超时 24h 加报发起人。

    超时不自动通过/拒绝（企业审计不接受系统代决），仅提醒升级；
    去重窗口 24h 由 dedup_key（instance+level+档位）承载。
    """
    now = timezone.now()
    qs = ApprovalInstance.objects.filter(
        status=ApprovalInstance.Status.PENDING).select_related("issue", "issue__project")
    for inst in qs.iterator(chunk_size=200):
        node = inst.flow_snapshot["nodes"][inst.current_level - 1]
        hours = node.get("timeout_hours")
        if not hours:
            continue
        entered = inst.records.filter(level=inst.current_level).order_by("created_at").values_list(
            "created_at", flat=True).first()
        if entered is None:
            continue
        overdue_h = (now - entered).total_seconds() / 3600
        if overdue_h < hours:
            continue
        payload = _instance_payload(inst)
        receivers = list(inst.records.filter(
            level=inst.current_level, action=ApprovalRecord.Action.PENDING
        ).values_list("approver_id", flat=True))
        stage = "overdue_24h" if overdue_h >= hours + 24 else "overdue"
        if stage == "overdue_24h":
            receivers.append(inst.initiator_id)  # 加报发起人（管理员提醒 R5 面补）
        epoch = f"{inst.id}:{inst.current_level}:{stage}"
        for uid in dict.fromkeys(u for u in receivers if u):
            try:
                Notification.objects.create(
                    receiver_id=uid,
                    event="approval.timeout",
                    title=f"审批超时提醒：{payload['flow_name']} 第 {inst.current_level} 级 · "
                          f"{payload['issue_key']}",
                    data=payload,
                    dedup_key=Notification.build_dedup_key(
                        event="approval.timeout", issue_id=inst.issue_id,
                        actor_id=inst.initiator_id, epoch=epoch, receiver_id=str(uid),
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                if "uniq_notif_dedup" not in str(exc):
                    logger.warning("approval.timeout_notify_failed inst=%s exc=%s", inst.id, exc)
