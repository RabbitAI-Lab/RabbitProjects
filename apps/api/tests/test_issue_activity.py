"""TASK-010 审计单元测试（diff builder / Worker 三层幂等 / 死信元数据推导）。"""

from __future__ import annotations

import uuid as uuid_mod

import pytest

from plane.bgtasks.issue_activity import build_event_key, issue_activity
from plane.db.models import Issue, IssueActivity, Project, User, Workspace
from plane.db.services.activity_builder import (
    DESCRIPTION_MARKER,
    SENSITIVE_PATTERNS,
    build_activities,
    clip,
)

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="act-owner@rabbit.dev", password="Rabbit123!")
    ws = Workspace.objects.create(name="W", slug=f"w-act-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    proj = Project.objects.create(name="P", identifier="ACT", workspace=ws, created_by=owner)
    issue = Issue.objects.create(name="T", project=proj, sequence_id=1, sort_order=100, created_by=owner)
    return owner, proj, issue


def test_builder_scalar_fk_m2m_custom_and_marker(env):
    owner, proj, issue = env
    rows = build_activities(
        issue_id=issue.id,
        actor_id=owner.id,
        before={"name": "旧", "priority": "low", "description_html": "<p>a</p>", "custom_fields": {"sev": "major"}},
        after={
            "name": "新",
            "priority": "urgent",
            "description_html": "<p>b</p>",
            "custom_fields": {"sev": "critical"},
        },
        epoch=1000.0,
    )
    fields = {r.field for r in rows}
    assert {"name", "priority", "description", "cf_sev"} <= fields
    desc = next(r for r in rows if r.field == "description")
    assert desc.old_value == DESCRIPTION_MARKER  # BR-06 不落全文
    sev = next(r for r in rows if r.field == "cf_sev")
    assert sev.old_value == "major" and sev.new_value == "critical"


def test_clip_sensitive_and_truncation():
    assert clip("cf_webhook_url", "https://x") == "***"  # 字段名命中
    assert clip("note", "password=123") == "***"  # 值命中
    assert clip("name", "长" * 600).endswith("…")  # 500 截断
    assert clip("name", None) is None
    assert SENSITIVE_PATTERNS.search("My_Secret_Token")


def test_worker_idempotent_double_dispatch(env):
    """② DB 同键去重：同一 payload 派发两次落库恰一次（at-least-once 语义）。"""
    owner, proj, issue = env
    payload = {
        "issue_id": str(issue.id),
        "actor_id": str(owner.id),
        "verb": "updated",
        "epoch": 2000.0,
        "before": {"name": "a"},
        "after": {"name": "b"},
    }
    issue_activity.apply(args=[payload], kwargs={})  # eager 同步执行
    issue_activity.apply(args=[payload], kwargs={})  # 重复投递
    n = IssueActivity.objects.filter(issue=issue, epoch=2000.0, field="name").count()
    assert n == 1


def test_worker_lock_conflict_retries(env):
    """③ 处理锁占用 → retry(310) 而非丢弃（消息不丢；锁 TTL 300s）。"""
    owner, proj, issue = env
    from django.core.cache import cache

    payload = {
        "issue_id": str(issue.id),
        "actor_id": str(owner.id),
        "verb": "updated",
        "epoch": 3000.0,
        "before": {"name": "a"},
        "after": {"name": "b"},
    }
    key = f"activity-lock:{build_event_key(payload)}"
    cache.add(key, 1, timeout=300)  # 模拟他方持有（硬崩溃锁残留）
    # apply() 本地同步：锁占用 → retry 轨道（RETRY/FAILURE 状态而非静默成功），
    # 锁占用期间绝不落库、消息不被消费丢弃——真实 worker 下经 310s 重试收敛
    result = issue_activity.apply(args=[payload], kwargs={})
    assert result.status in ("RETRY", "FAILURE"), result.status
    assert not IssueActivity.objects.filter(issue=issue, epoch=3000.0).exists()
    cache.delete(key)


def test_dead_letter_metadata_retries_inference():
    """§4.3.4：MaxRetriesExceededError → max_retries；其余 → 0。"""
    from celery.exceptions import MaxRetriesExceededError

    from plane.bgtasks.activity_dlq import DLQ_KEY_PREFIX, dlq_client, record_dead_letter

    class FakeTask:
        name = "plane.bgtasks.issue_activity.issue_activity"
        max_retries = 3

    payload = {"issue_id": str(uuid_mod.uuid4()), "actor_id": str(uuid_mod.uuid4()), "verb": "updated", "epoch": 1.0}
    mid = str(uuid_mod.uuid4())
    r = dlq_client()
    r.delete(f"{DLQ_KEY_PREFIX}{mid}")
    record_dead_letter(sender=FakeTask(), task_id=mid, exception=MaxRetriesExceededError(), args=(payload,))
    h = {k.decode(): v.decode() for k, v in r.hgetall(f"{DLQ_KEY_PREFIX}{mid}").items()}
    assert h["retries"] == "3" and "error_summary" in h and "payload" in h
    r.delete(f"{DLQ_KEY_PREFIX}{mid}")
