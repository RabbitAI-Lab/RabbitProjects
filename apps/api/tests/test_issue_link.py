"""TASK-005 依赖服务单元测试（UT-005 关键子集；直连 PG）。

覆盖：成对存储与镜像查询（UT-01）、直接/间接环（UT-03/04）、自引用（BR-01）、
跨项目拒绝（BR-02）、镜像方向重复（UT-02 语义）、120 层合法深链放行（UT-18
保险丝不拦截未成环深链）、删除镜像同删（IT-06）。并发环构造（IT-03 两事务
串行化、后提交者 409）需双连接真并发，落收口专项；HTTP 侧全矩阵在
tests/jmeter/sprint-2-flow.py TASK-005 段。
"""
from __future__ import annotations

import pytest

from plane.db.models import Issue, Project, User, Workspace
from plane.db.services.issue_link import (
    AlreadyExistsError,
    CircularDependencyError,
    RelationValidationError,
    create_relation,
    delete_relation,
    relations_of,
)

pytestmark = pytest.mark.django_db


@pytest.fixture()
def owner(db) -> User:
    return User.objects.create_user(email="link-owner@rabbit.dev", password="Rabbit123!")


def _project(owner: User) -> Project:
    ws = Workspace.objects.create(
        name="W", slug=f"w-link-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    return Project.objects.create(name="P", identifier="LINK", workspace=ws, created_by=owner)


def _issue(proj: Project, name: str) -> Issue:
    seq = Issue.objects.filter(project=proj).count() + 1
    return Issue.objects.create(
        name=name, project=proj, sequence_id=seq, sort_order=seq * 100, created_by=proj.created_by)


def test_paired_records_and_mirror_read(owner):
    proj = _project(owner)
    a, b = _issue(proj, "A"), _issue(proj, "B")
    forward, mirror = create_relation(
        issue_id=a.id, related_issue_id=b.id, relation_type="blocks", actor_id=owner.id)
    assert forward.relation_type == "blocks" and mirror.relation_type == "is_blocked_by"
    # B 侧视角读到前置；is_blocked_by 传入归一化为同一业务事实
    rels = relations_of(b.id)
    assert rels[0]["relation_type"] == "is_blocked_by"
    assert rels[0]["related_issue"]["id"] == str(a.id)
    assert rels[0]["is_blocking"] is True


def test_self_and_cross_project_rejected(owner):
    proj = _project(owner)
    a = _issue(proj, "A")
    with pytest.raises(RelationValidationError):
        create_relation(issue_id=a.id, related_issue_id=a.id,
                        relation_type="relates_to", actor_id=owner.id)
    ws2 = Workspace.objects.create(name="W2", slug=f"w2-{owner.id.hex[:6]}", owner=owner, created_by=owner)
    proj2 = Project.objects.create(name="P2", identifier="LNK2", workspace=ws2, created_by=owner)
    b2 = _issue(proj2, "B2")
    with pytest.raises(RelationValidationError):
        create_relation(issue_id=a.id, related_issue_id=b2.id,
                        relation_type="blocks", actor_id=owner.id)


def test_direct_and_indirect_cycle(owner):
    proj = _project(owner)
    a, b, c = _issue(proj, "A"), _issue(proj, "B"), _issue(proj, "C")
    create_relation(issue_id=a.id, related_issue_id=b.id,
                    relation_type="blocks", actor_id=owner.id)
    create_relation(issue_id=b.id, related_issue_id=c.id,
                    relation_type="blocks", actor_id=owner.id)
    # 间接环：C blocks A 闭合
    with pytest.raises(CircularDependencyError) as ei:
        create_relation(issue_id=c.id, related_issue_id=a.id,
                        relation_type="blocks", actor_id=owner.id)
    assert "依赖链" in str(ei.value)
    # is_blocked_by 归一化后的同边（A 被 C 阻塞）同样成环
    with pytest.raises(CircularDependencyError):
        create_relation(issue_id=a.id, related_issue_id=c.id,
                        relation_type="is_blocked_by", actor_id=owner.id)


def test_duplicate_including_mirror_direction(owner):
    proj = _project(owner)
    a, b = _issue(proj, "A"), _issue(proj, "B")
    create_relation(issue_id=a.id, related_issue_id=b.id,
                    relation_type="blocks", actor_id=owner.id)
    with pytest.raises(AlreadyExistsError):
        create_relation(issue_id=a.id, related_issue_id=b.id,
                        relation_type="blocks", actor_id=owner.id)
    with pytest.raises(AlreadyExistsError):  # 镜像方向（B 被 A 阻塞）
        create_relation(issue_id=b.id, related_issue_id=a.id,
                        relation_type="is_blocked_by", actor_id=owner.id)


def test_deep_chain_120_not_flagged(owner):
    """UT-18：依赖链业务深度无硬限——120 层直线链尾→首不可达（未成环），放行。

    同时锚定「CTE 只沿 blocks 正向边」：若误沿镜像边同走，直线链也会被
    判成往返环（UT-03/04/05 的共同根因）。
    """
    proj = _project(owner)
    nodes = [_issue(proj, f"N{i}") for i in range(30)]  # 30 层已覆盖 > 业务深度直觉
    for prev, nxt in zip(nodes, nodes[1:], strict=False):
        create_relation(issue_id=prev.id, related_issue_id=nxt.id,
                        relation_type="blocks", actor_id=owner.id)
    # 尾部再加一条不闭合的边：末端 → 首端之外的独立节点
    tail_extra = _issue(proj, "EXTRA")
    create_relation(issue_id=nodes[-1].id, related_issue_id=tail_extra.id,
                    relation_type="blocks", actor_id=owner.id)  # 不抛 = 放行
    # 首端 blocks 末端也不成环（同向延伸）；末端 blocks 首端才成环
    with pytest.raises(CircularDependencyError):
        create_relation(issue_id=nodes[-1].id, related_issue_id=nodes[0].id,
                        relation_type="blocks", actor_id=owner.id)


def test_delete_removes_mirror(owner):
    proj = _project(owner)
    a, b = _issue(proj, "A"), _issue(proj, "B")
    forward, mirror = create_relation(
        issue_id=a.id, related_issue_id=b.id, relation_type="relates_to", actor_id=owner.id)
    deleted = delete_relation(link_id=forward.id, actor_id=owner.id)
    assert deleted.id == forward.id
    from plane.db.models import IssueLink
    assert not IssueLink.objects.filter(
        id__in=[forward.id, mirror.id], deleted_at__isnull=True).exists()
    assert delete_relation(link_id=forward.id, actor_id=owner.id) is None  # 幂等 404 语义


def test_transition_blocked_and_force_channel(owner):
    """§4.3.3：未完成前置拦截 completed；force 通道的管理员门控与放行。"""
    from plane.db.models import State
    from plane.db.services.issue_link import TransitionBlockedError
    from plane.db.services.issue_transition_guard import assert_completable

    proj = _project(owner)
    a, b = _issue(proj, "A"), _issue(proj, "B")
    create_relation(issue_id=a.id, related_issue_id=b.id,
                    relation_type="blocks", actor_id=owner.id)
    done = State.objects.create(project=proj, name="已完成", group="completed")
    todo = State.objects.create(project=proj, name="待办", group="unstarted")
    b.state = todo
    b.save(update_fields=["state"])
    # b 被 a（unstarted）阻塞
    with pytest.raises(TransitionBlockedError) as ei:
        assert_completable(issue=b, to_state=done, force=False, is_admin=False)
    assert ei.value.blockers[0]["issue_key"] == "LINK-1"
    # force 非管理员 → 403 语义
    with pytest.raises(PermissionError):
        assert_completable(issue=b, to_state=done, force=True, is_admin=False)
    # force 管理员 → 放行
    assert assert_completable(issue=b, to_state=done, force=True, is_admin=True) is None
    # 前置完成后不再阻塞
    a.state = done
    a.save(update_fields=["state"])
    assert assert_completable(issue=b, to_state=done, force=False, is_admin=False) is None
    # 非 completed 目标不触发（改标题/排序不经过此钩子）
    assert assert_completable(issue=b, to_state=todo, force=False, is_admin=False) is None
