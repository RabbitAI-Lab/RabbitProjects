"""任务基线测试（TASK-015，P4 R3 门禁）。

覆盖 §5.1 核心：创建（reason 必填 BR-13/上限 BR-02/重名）、快照只读
（BR-01 API 无写路径）、对比四类差异 + 两标志（§2.3 全集 BR-05）、
统计口径（§2.4）、删除整套、purged 兜底（UT-15 语义）。
"""

from __future__ import annotations

import datetime as dt

import pytest
from rest_framework.test import APIClient

from plane.db.models import (
    Baseline,
    BaselineItem,
    Issue,
    Project,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.roles import WorkspaceRole
from plane.db.services.baseline import build_compare_rows, compute_stats, fill_baseline_sync

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="bl-owner@rabbit.dev", password="Rabbit123!", display_name="PM")
    member = User.objects.create_user(email="bl-member@rabbit.dev", password="Rabbit123!", display_name="员")
    ws = Workspace.objects.create(name="BL", slug=f"w-bl-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=owner, role=WorkspaceRole.OWNER, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=member, role=WorkspaceRole.MEMBER, created_by=member)
    proj = Project.objects.create(workspace=ws, name="P", identifier="BL1", created_by=owner)
    return {"owner": owner, "member": member, "ws": ws, "proj": proj}


def _mk_issue(env, seq, name, *, target=None, start=None, assignees=()):
    from plane.db.models import IssueAssignee

    issue = Issue.objects.create(
        project=env["proj"], name=name, sequence_id=seq, start_date=start, target_date=target, created_by=env["owner"]
    )
    for a in assignees:
        IssueAssignee.objects.create(issue=issue, assignee=a, created_by=env["owner"])
    return issue


def _c(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user)
    return c


def _base(env):
    return f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/baselines/"


def test_create_requires_reason_and_caps(env):
    c = _c(env["owner"])
    r = c.post(_base(env), {"name": "V1"}, format="json")
    assert r.status_code == 400 and r.json()["error"]["details"][0]["field"] == "reason"  # BR-13
    r2 = c.post(_base(env), {"name": "V1", "reason": "合同签订版"}, format="json")
    assert r2.status_code == 201 and r2.json()["data"]["status"] == "ready"
    r3 = c.post(_base(env), {"name": "V1", "reason": "重名"}, format="json")
    assert r3.status_code == 400  # 重名拒绝


def test_baseline_cap_11(env):
    from plane.db.models import Baseline as B

    for i in range(11):
        B.objects.create(project=env["proj"], name=f"B{i}", reason="r", status="ready")
    r = _c(env["owner"]).post(_base(env), {"name": "B12", "reason": "超"}, format="json")
    assert r.status_code == 409  # BR-02
    # failed 不占配额
    B.objects.filter(name="B10").update(status="failed")
    r2 = _c(env["owner"]).post(_base(env), {"name": "B12", "reason": "补位"}, format="json")
    assert r2.status_code == 201


def test_snapshot_and_compare_diff_types(env):
    i1 = _mk_issue(env, 1, "稳定任务", target=dt.date(2026, 9, 20))
    i2 = _mk_issue(env, 2, "延期任务", target=dt.date(2026, 9, 18))
    i3 = _mk_issue(env, 3, "被删任务", target=dt.date(2026, 9, 25))
    baseline = Baseline.objects.create(project=env["proj"], name="V1", reason="对比基线", created_by=env["owner"])
    fill_baseline_sync(str(baseline.id))
    baseline.refresh_from_db()
    assert baseline.issue_count == 3
    assert baseline.status == "ready"
    # 变更：i2 延期 3 天；i3 软删；新增 i4；i1 换人（assignee_changed）
    i2.target_date = dt.date(2026, 9, 21)
    i2.save(update_fields=["target_date"])
    i3.deleted_at = dt.datetime.now(dt.UTC)
    i3.save(update_fields=["deleted_at"])
    _mk_issue(env, 4, "新增任务")
    from plane.db.models import IssueAssignee

    IssueAssignee.objects.create(issue=i1, assignee=env["member"], created_by=env["owner"])
    rows = {r["name"]: r for r in build_compare_rows(baseline)}
    assert rows["稳定任务"]["diff_type"] == "on_track"
    assert rows["稳定任务"]["assignee_changed"] is True  # 换人标志
    assert rows["延期任务"]["diff_type"] == "delayed"
    assert rows["延期任务"]["variance_days"] == 3
    assert rows["被删任务"]["diff_type"] == "deleted"
    assert rows["新增任务"]["diff_type"] == "added"
    assert len(rows) == 4  # 全集不灭失（BR-05）


def test_unscheduled_bucket(env):
    _mk_issue(env, 1, "无日期任务")
    baseline = Baseline.objects.create(project=env["proj"], name="V1", reason="未排期", created_by=env["owner"])
    fill_baseline_sync(str(baseline.id))
    rows = build_compare_rows(baseline)
    assert rows[0]["diff_type"] == "unscheduled"  # 不落 on_track
    assert rows[0]["variance_days"] is None


def test_stats_aggregation(env):
    _mk_issue(env, 1, "按期", target=dt.date(2026, 9, 20))
    _mk_issue(env, 2, "延一天", target=dt.date(2026, 9, 19))
    _mk_issue(env, 3, "延三天", target=dt.date(2026, 9, 19))
    baseline = Baseline.objects.create(project=env["proj"], name="V1", reason="统计", created_by=env["owner"])
    fill_baseline_sync(str(baseline.id))
    Issue.objects.filter(sequence_id=2).update(target_date=dt.date(2026, 9, 20))
    Issue.objects.filter(sequence_id=3).update(target_date=dt.date(2026, 9, 22))
    stats = compute_stats(baseline)
    assert stats["dated_count"] == 3
    assert stats["delayed_count"] == 2
    assert stats["delay_rate"] == round(2 / 3, 4)
    assert stats["avg_delay_days"] == 2.0  # (1+3)/2


def test_delete_baseline_cascade_and_permission(env):
    from plane.db.models import ProjectMember

    _mk_issue(env, 1, "t")
    # 成员入项目（可见性前置——否则 get_project_or_404 按防枚举 404）
    ProjectMember.objects.create(
        project=env["proj"], workspace=env["ws"], member=env["member"], role=10, created_by=env["owner"]
    )
    baseline = Baseline.objects.create(project=env["proj"], name="V1", reason="删除", created_by=env["owner"])
    fill_baseline_sync(str(baseline.id))
    # 普通成员无 gantt.baseline.manage → 403
    r = _c(env["member"]).delete(f"{_base(env)}{baseline.id}/")
    assert r.status_code == 403
    r2 = _c(env["owner"]).delete(f"{_base(env)}{baseline.id}/")
    assert r2.status_code == 200
    assert not Baseline.objects.filter(pk=baseline.id).exists()
    assert not BaselineItem.objects.exists()  # 级联清理


def test_purged_row_survives(env):
    """UT-15 语义：issue 物理删除后快照行保留且可辨认（SET_NULL 兜底）。"""
    issue = _mk_issue(env, 1, "会被物理删")
    baseline = Baseline.objects.create(project=env["proj"], name="V1", reason="purged", created_by=env["owner"])
    fill_baseline_sync(str(baseline.id))
    issue.delete()  # 物理删
    item = BaselineItem.objects.get(baseline=baseline)
    assert item.issue_id is None  # SET_NULL 保留行
    assert item.name_snapshot == "会被物理删"  # 可辨认
    rows = build_compare_rows(baseline)
    assert rows[0]["diff_type"] == "deleted" and rows[0]["purged"] is True


def test_compare_endpoint_filters(env):
    _mk_issue(env, 1, "稳定", target=dt.date(2026, 9, 20))
    baseline = Baseline.objects.create(project=env["proj"], name="V1", reason="筛选", created_by=env["owner"])
    fill_baseline_sync(str(baseline.id))
    _mk_issue(env, 2, "新增")
    c = _c(env["owner"])
    r = c.get(f"{_base(env)}{baseline.id}/compare/?diff_type=added")
    assert r.status_code == 200
    assert [row["name"] for row in r.json()["data"]] == ["新增"]
    r_bad = c.get(f"{_base(env)}{baseline.id}/compare/?diff_type=bad")
    assert r_bad.status_code == 400


def test_export_csv(env):
    _mk_issue(env, 1, "导出行", target=dt.date(2026, 9, 20))
    baseline = Baseline.objects.create(project=env["proj"], name="V1", reason="导出", created_by=env["owner"])
    fill_baseline_sync(str(baseline.id))
    r = _c(env["owner"]).get(f"{_base(env)}{baseline.id}/export/")
    assert r.status_code == 200
    assert "text/csv" in r["Content-Type"]
    assert "导出行" in r.content.decode("utf-8")
