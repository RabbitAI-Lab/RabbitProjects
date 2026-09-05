"""TASK-006 工时服务单元测试（窗口/权限/汇总无放大）。HTTP 全矩阵在 sprint-2-flow.py。"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from plane.db.models import Issue, Project, User, Workspace
from plane.db.services.worklog import (
    WorklogPermissionError,
    WorklogValidationError,
    log_work,
    spent_minutes_of,
    subtree_worklog_summary,
    update_worklog,
)

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="wl-owner@rabbit.dev", password="Rabbit123!")
    ws = Workspace.objects.create(name="W", slug=f"w-wl-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    proj = Project.objects.create(name="P", identifier="WL", workspace=ws, created_by=owner)
    issue = Issue.objects.create(name="T", project=proj, sequence_id=1, sort_order=100,
                                 created_by=owner, estimate_minutes=480)
    return owner, proj, issue


def _d(n: int) -> date:  # n 天前
    from django.utils import timezone
    return timezone.localdate() - timedelta(days=n)


def test_window_and_minutes_validation(env):
    owner, _, issue = env
    with pytest.raises(WorklogValidationError):
        log_work(issue=issue, actor_id=owner.id, minutes=120, worked_on=_d(31))
    with pytest.raises(WorklogValidationError):
        log_work(issue=issue, actor_id=owner.id, minutes=0, worked_on=_d(0))
    with pytest.raises(WorklogValidationError):
        log_work(issue=issue, actor_id=owner.id, minutes=1441, worked_on=_d(0))
    from datetime import timedelta as _td
    from django.utils import timezone as _tz
    with pytest.raises(WorklogValidationError):  # 未来日期（UT-15）
        log_work(issue=issue, actor_id=owner.id, minutes=30,
                 worked_on=_tz.localdate() + _td(days=1))
    log_work(issue=issue, actor_id=owner.id, minutes=1440, worked_on=_d(30))  # 边界放行


def test_edit_revalidates_window_on_new_value(env):
    owner, _, issue = env
    log, _ = log_work(issue=issue, actor_id=owner.id, minutes=30, worked_on=_d(29))
    with pytest.raises(WorklogValidationError):  # UT-16：对新值重校验
        update_worklog(log_id=log.id, issue_id=issue.id, actor_id=owner.id,
                       is_admin=False, worked_on=_d(31))
    _, spent = update_worklog(log_id=log.id, issue_id=issue.id, actor_id=owner.id,
                              is_admin=False, minutes=60)
    assert spent == 60


def test_owner_only_edit(env):
    owner, proj, issue = env
    other = User.objects.create_user(email="wl-other@rabbit.dev", password="Rabbit123!")
    from plane.db.models import ProjectMember, ProjectRole
    ProjectMember.objects.create(project=proj, member=other, role=ProjectRole.CONTRIBUTOR,
                                 created_by=owner)
    log, _ = log_work(issue=issue, actor_id=owner.id, minutes=30, worked_on=_d(0))
    with pytest.raises(WorklogPermissionError):
        update_worklog(log_id=log.id, issue_id=issue.id, actor_id=other.id, is_admin=False,
                       minutes=60)
    update_worklog(log_id=log.id, issue_id=issue.id, actor_id=other.id, is_admin=True,
                   minutes=90)  # ADMIN 可改他人


def test_subtree_summary_no_join_amplification(env):
    """UT-09 同源：多笔工时不得放大 estimate（规格 SQL 偏差 ADR-0014 的锚定）。"""
    owner, proj, issue = env
    sub = Issue.objects.create(name="S", project=proj, parent=issue, sequence_id=2,
                               sort_order=200, created_by=owner, estimate_minutes=120)
    for i in range(3):
        log_work(issue=issue, actor_id=owner.id, minutes=60, worked_on=_d(i))
    out = subtree_worklog_summary(issue.id)
    assert out == {"subtree_spent_minutes": 180, "subtree_estimate_minutes": 600}  # 480+120，非 ×4
    assert spent_minutes_of(issue.id) == 180
