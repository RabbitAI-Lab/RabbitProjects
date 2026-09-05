"""TASK-009 复制/归档单元测试（深拷贝事务 / id_map / BR-10 / 幂等 / 写保护）。HTTP 全矩阵在 flow T9-01~20。"""
from __future__ import annotations

import pytest

from plane.db.models import Issue, Project, User, Workspace
from plane.db.services.issue_archive import (
    IssueArchivedError,
    archive_subtree,
    assert_issue_writable,
    restore_subtree,
)
from plane.db.services.issue_copy import DuplicateOptions, duplicate_issue

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="cp-owner@rabbit.dev", password="Rabbit123!")
    ws = Workspace.objects.create(name="W", slug=f"w-cp-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    proj = Project.objects.create(name="P", identifier="CP", workspace=ws, created_by=owner)
    return owner, proj


def _issue(proj, name, parent=None, seq=None):
    seq = seq or Issue.objects.filter(project=proj).count() + 1
    return Issue.objects.create(name=name, project=proj, parent=parent, sequence_id=seq,
                                sort_order=seq * 100, created_by=proj.created_by)


def test_deep_copy_id_map_and_options(env):
    owner, proj = env
    root = _issue(proj, "R", seq=1)
    mid = _issue(proj, "M", parent=root, seq=2)
    _issue(proj, "L", parent=mid, seq=3)
    out = duplicate_issue(issue_id=root.id, actor_id=owner.id)
    new_root = out["root"]
    assert out["total_created"] == 3
    assert new_root.name == "R (副本)"
    kids = list(Issue.objects.filter(parent=new_root))
    assert len(kids) == 1 and kids[0].name == "M"
    grand = Issue.objects.get(parent=kids[0])
    assert grand.name == "L"
    # 五选项：关子树 → 1 节点；副本 2 后缀
    out2 = duplicate_issue(issue_id=root.id, actor_id=owner.id,
                           options=DuplicateOptions(include_subtrees=False))
    assert out2["total_created"] == 1 and out2["root"].name == "R (副本 2)"


def test_deep_copy_rollback_on_failure(env, monkeypatch):
    """BR-06：事务内任一步失败 → 整树回滚零残留（mock link 服务抛错）。"""
    owner, proj = env
    root = _issue(proj, "R", seq=1)
    _issue(proj, "S", parent=root, seq=2)
    import plane.db.services.issue_copy as cp

    def boom(**kw):
        raise RuntimeError("injected")

    monkeypatch.setattr(cp, "create_relation", boom)
    before = Issue.objects.filter(project=proj).count()
    with pytest.raises(RuntimeError):
        duplicate_issue(issue_id=root.id, actor_id=owner.id)
    assert Issue.objects.filter(project=proj).count() == before  # 零残留


def test_archive_keeps_first_timestamp_and_idempotent(env):
    """BR-10：首次归档时间不可变；重复归档 count=0。"""
    owner, proj = env
    import datetime

    from django.utils import timezone
    root = _issue(proj, "R", seq=1)
    sub = _issue(proj, "S", parent=root, seq=2)
    Issue.objects.filter(pk=root.id).update(
        archived_at=timezone.now() - datetime.timedelta(days=7))  # 根 7 天前已归档
    out = archive_subtree(issue_id=root.id, actor_id=owner.id)
    assert out["archived_count"] == 1  # 只补齐未归档的 sub
    root.refresh_from_db()
    sub.refresh_from_db()
    assert root.archived_at < sub.archived_at  # 根的首次时间未被覆写
    out2 = archive_subtree(issue_id=root.id, actor_id=owner.id)
    assert out2["archived_count"] == 0
    out3 = restore_subtree(issue_id=root.id, actor_id=owner.id)
    assert out3["restored_count"] == 2


def test_assert_issue_writable(env):
    owner, proj = env
    from django.utils import timezone
    live = _issue(proj, "live", seq=1)
    assert_issue_writable(live)  # 活跃任务不抛
    dead = _issue(proj, "dead", seq=2)
    Issue.objects.filter(pk=dead.id).update(archived_at=timezone.now())
    dead.refresh_from_db()
    with pytest.raises(IssueArchivedError):
        assert_issue_writable(dead)
