"""TASK-004 层级服务单元测试（UT-004 关键异常子集；直连 PG，service 层）。

覆盖：深度口径（UT-01）、直接/间接环与自引用（UT-02/03/04）、移动子树高度整体
校验（UT-16）、级联软删事务性（IT-05 的 service 侧）。HTTP 侧正负例在
tests/jmeter/sprint-2-flow.py TASK-004 段（19 断言）；CTE 保险丝（UT-10）需
构造 101 层脏数据，落收口阶段 bench 专项（成本高，不入常规单测）。
"""
from __future__ import annotations

import pytest

from plane.db.models import Issue, IssueAssignee, Project, User, Workspace
from plane.db.services import issue_hierarchy as hier

pytestmark = pytest.mark.django_db


def _make_tree(owner: User, depth: int = 5, proj: Project | None = None) -> list[Issue]:
    """建一条 depth 层直线链，返回 [L1, L2, ...]（L1 为根）；可传 proj 复用项目。"""
    if proj is None:
        ws = Workspace.objects.create(
            name="W", slug=f"w-hier-{owner.id.hex[:8]}", owner=owner, created_by=owner)
        proj = Project.objects.create(
            name="P", identifier="HIER", workspace=ws, created_by=owner)
    chain: list[Issue] = []
    prev = None
    seq = Issue.objects.filter(project=proj).count()
    for i in range(depth):
        node = Issue.objects.create(
            name=f"L{i + 1}", project=proj, parent=prev,
            sequence_id=seq + i + 1, sort_order=(seq + i + 1) * 100, created_by=owner)
        chain.append(node)
        prev = node
    return chain


@pytest.fixture()
def owner(db) -> User:
    return User.objects.create_user(email="hier-owner@rabbit.dev", password="Rabbit123!")


def test_depth_of_and_ancestor_chain(owner):
    chain = _make_tree(owner, depth=5)
    assert hier.depth_of(chain[0].id) == 1      # 根 = 第 1 层
    assert hier.depth_of(chain[-1].id) == 5     # 第 5 层节点链长 5


def test_move_direct_and_indirect_cycle(owner):
    chain = _make_tree(owner, depth=4)
    locked = Issue.objects.select_for_update().get(pk=chain[0].id)
    # 直接：根挂到自己（A→A 自引用短路）
    with pytest.raises(hier.CircularDependencyError):
        hier.check_move(locked, chain[0].id)
    # 间接：根挂到自己的后代
    with pytest.raises(hier.CircularDependencyError) as ei:
        hier.check_move(locked, chain[3].id)
    assert "环路径" in ei.value.path


def test_move_subtree_height_counted(owner):
    """UT-16：仅校验 depth(新父)+1 会漏算子树高度——3 层子树挂到第 3 层 = 最深 6 层。"""
    chain_a = _make_tree(owner, depth=3)   # 根 A（3 层子树）
    chain_b = _make_tree(owner, depth=3, proj=chain_a[0].project)  # 同项目 B 链
    locked = Issue.objects.select_for_update().get(pk=chain_a[0].id)
    with pytest.raises(hier.DepthLimitExceeded):
        hier.check_move(locked, chain_b[2].id)
    # 同一子树挂到第 2 层（2+3=5）合法
    hier.check_move(locked, chain_b[1].id)


def test_move_to_archived_parent_rejected(owner):
    chain = _make_tree(owner, depth=2)
    from django.utils import timezone
    Issue.objects.filter(pk=chain[1].id).update(archived_at=timezone.now())
    locked = Issue.objects.select_for_update().get(pk=chain[0].id)
    with pytest.raises(hier.StateInvalidError):
        hier.check_move(locked, chain[1].id)


def test_fetch_subtree_root_stats_scope(owner):
    chain = _make_tree(owner, depth=4)
    data = hier.fetch_subtree(chain[0].id)
    assert data["root"]["id"] == str(chain[0].id)
    assert data["root"]["depth"] == 0
    assert len(data["nodes"]) == 3
    assert data["stats"]["total"] == 4          # 含根口径
    assert data["stats"]["max_depth"] == 3


def test_fetch_subtree_archived_root_404_shape(owner):
    """BR-09：归档根整树不可见——fetch_subtree 过滤归档根（视图层据此 404）。"""
    from django.utils import timezone
    chain = _make_tree(owner, depth=2)
    Issue.objects.filter(pk=chain[0].id).update(archived_at=timezone.now())
    data = hier.fetch_subtree(chain[0].id)
    assert data["root"] is None and data["nodes"] == []


def test_delete_subtree_cascades_and_purges_join_tables(owner):
    chain = _make_tree(owner, depth=3)
    ia = IssueAssignee.objects.create(issue=chain[1], assignee=owner, assigned_by=owner)
    result = hier.delete_subtree(chain[0].id, owner.id)
    assert result["deleted_count"] == 3
    assert len(result["descendant_ids"]) == 2
    assert not Issue.objects.filter(
        id__in=[c.id for c in chain], deleted_at__isnull=True).exists()
    assert not IssueAssignee.objects.filter(id=ia.id).exists()  # 中间表物理删


def test_count_annotations_exclude_archived(owner):
    """§4.3.5：计数过滤器追加 archived_at——归档子任务退出 x/y。"""
    from django.utils import timezone
    chain = _make_tree(owner, depth=3)
    Issue.objects.filter(pk=chain[1].id).update(archived_at=timezone.now())
    root = Issue.objects.filter(pk=chain[0].id).annotate(**hier.issue_count_annotations()).first()
    assert root.sub_issues_count == 0
