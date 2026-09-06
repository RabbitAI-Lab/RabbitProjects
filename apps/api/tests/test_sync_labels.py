"""sync_labels 精确差分写回归（ADR-0020 · TASK-002 潜伏缺陷修复）。

BOARD-004 批量侧实测发现：原「软删全部 + 重建」在无条件唯一键 uniq_issue_label
下，集合重叠的重提交（幂等重 PUT / 部分交集替换）必 IntegrityError。
影响面（codegraph 圈定）：issues.py create/put/post 三调用点 + 14 个测试文件。
"""
from __future__ import annotations

import pytest
from django.utils import timezone

from plane.app.serializers.issue import sync_labels
from plane.db.models import Issue, IssueLabel, Label, Project, User, Workspace, WorkspaceMember, WorkspaceRole

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="lbl-owner@rabbit.dev", password="Rabbit123!")
    ws = Workspace.objects.create(name="W", slug=f"w-lbl-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    proj = Project.objects.create(name="P", identifier="LB", workspace=ws, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=owner, role=WorkspaceRole.OWNER, created_by=owner)
    labels = [Label.objects.create(project=proj, name=f"L{i}", color="#3B82F6", created_by=owner) for i in range(3)]
    issue = Issue.objects.create(name="T", project=proj, sequence_id=1, sort_order=100, created_by=owner)
    return {"owner": owner, "ws": ws, "proj": proj, "labels": labels, "issue": issue}


def _ids(env, *idx):
    return [env["labels"][i].id for i in idx]


class TestSyncLabels:
    def test_idempotent_reput_same_set(self, env):
        """幂等重 PUT（同集重叠）—— 原缺陷的直接复现场景：不再 IntegrityError，且行 id 不变。"""
        sync_labels(env["issue"], _ids(env, 0, 1), env["owner"].id)
        row_ids_before = set(
            IssueLabel.objects.filter(issue=env["issue"]).values_list("id", flat=True)
        )
        sync_labels(env["issue"], _ids(env, 0, 1), env["owner"].id)  # 重 PUT 同集
        row_ids_after = set(
            IssueLabel.objects.filter(issue=env["issue"]).values_list("id", flat=True)
        )
        assert row_ids_before == row_ids_after  # 零写：行 id 不变
        assert IssueLabel.objects.filter(issue=env["issue"]).count() == 2

    def test_partial_overlap_replace(self, env):
        sync_labels(env["issue"], _ids(env, 0, 1), env["owner"].id)
        sync_labels(env["issue"], _ids(env, 1, 2), env["owner"].id)  # 交集 {L1}
        got = set(
            IssueLabel.objects.filter(issue=env["issue"]).values_list("label_id", flat=True)
        )
        assert got == {_ids(env, 1)[0], _ids(env, 2)[0]}

    def test_clear_all(self, env):
        sync_labels(env["issue"], _ids(env, 0), env["owner"].id)
        sync_labels(env["issue"], [], env["owner"].id)
        assert IssueLabel.objects.filter(issue=env["issue"]).count() == 0

    def test_ghost_row_resurrect(self, env):
        """历史幽灵行（旧 bug 软删残留）复活而非撞键。"""
        sync_labels(env["issue"], _ids(env, 0), env["owner"].id)
        # 手工制造幽灵：模拟旧实现的软删
        IssueLabel.objects.filter(issue=env["issue"]).update(deleted_at=timezone.now())
        assert IssueLabel.objects.filter(issue=env["issue"]).count() == 0  # 软删后不可见
        sync_labels(env["issue"], _ids(env, 0), env["owner"].id)  # 重新添加同标签
        assert IssueLabel.objects.filter(issue=env["issue"]).count() == 1  # 复活，无 IntegrityError
        assert IssueLabel.all_objects.filter(issue=env["issue"], deleted_at__isnull=True).count() == 1

    def test_removed_rows_hard_deleted(self, env):
        """移除的关联物理删除——无条件唯一键位彻底释放（不留软删幽灵）。"""
        sync_labels(env["issue"], _ids(env, 0, 1), env["owner"].id)
        sync_labels(env["issue"], _ids(env, 1), env["owner"].id)
        assert IssueLabel.all_objects.filter(issue=env["issue"], label_id=_ids(env, 0)[0]).count() == 0
