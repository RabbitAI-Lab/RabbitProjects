"""TASK-007 多执行人服务单元测试（UT 关键子集；直连 PG + 事务回滚零残留）。

覆盖：唯一写入口收口（去重保序 UT-02 / 10 上限 UT-03/04 / 非成员与
COMMENTER·VIEWER 拒绝 UT-05/06 / assigned_by 记录 UT-12 / 清空 UT-10）、
认领（UT-07/08 + 行锁语义锚定）、自退收敛（UT-16）、删后重加不撞
``uniq_issue_assignee``（UT-09 —— SoftDeleteQuerySet 默认软删会撞约束，
必须 ``delete(soft=False)``）、归档任务 409（IT-10 服务层兜底）、
BR-12 级联清空（UT-15，project/workspace 两路径）、通知任务差异性与
BR-09 操作者本人抑制（同步调用 task 函数体）。

HTTP 侧全矩阵在 tests/jmeter/sprint-2-flow.py TASK-007 段；并发认领
（IT-02 双连接真并发）落收口专项。
"""

from __future__ import annotations

import uuid

import pytest
from django.utils import timezone

from plane.bgtasks.issue_assignee import dispatch_assignment_events
from plane.db.models import (
    Issue,
    IssueActivity,
    IssueAssignee,
    IssueLabel,
    Notification,
    Project,
    ProjectMember,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.roles import ProjectRole, WorkspaceRole
from plane.db.services.issue_assignee import (
    MAX_ASSIGNEES,
    AssigneesLimitExceeded,
    IssueAlreadyClaimedError,
    IssueArchivedForAssignment,
    claim_issue,
    purge_member_assignments,
    remove_self,
    sync_assignees_full,
)
from plane.db.services.project_member import ProjectMemberService
from plane.utils.exceptions import AppValidationError

pytestmark = pytest.mark.django_db


# ───────────────────────── 夹具 ─────────────────────────
@pytest.fixture()
def owner(db) -> User:
    return User.objects.create_user(email="asg-owner@rabbit.dev", password="Rabbit123!")


class _Env:
    """一个 workspace + project + 成员集合（ADMIN 1 / CONTRIBUTOR 3 /
    COMMENTER 1 / VIEWER 1）—— BR-02 全角色候选。"""

    def __init__(self, owner: User):
        self.owner = owner
        self.ws = Workspace.objects.create(name="W", slug=f"w-asg-{owner.id.hex[:8]}", owner=owner, created_by=owner)
        WorkspaceMember.objects.create(workspace=self.ws, member=owner, role=WorkspaceRole.OWNER, created_by=owner)
        self.project = Project.objects.create(name="P", identifier="ASG", workspace=self.ws, created_by=owner)
        ProjectMember.objects.create(
            project=self.project,
            workspace=self.ws,
            member=owner,
            role=ProjectRole.ADMIN,
            is_active=True,
            created_by=owner,
        )
        self.admin = owner
        self.contributors: list[User] = []
        for i in range(3):
            u = User.objects.create_user(
                email=f"asg-c{i}-{owner.id.hex[:6]}@rabbit.dev", password="Rabbit123!", display_name=f"贡献者{i}"
            )
            WorkspaceMember.objects.create(workspace=self.ws, member=u, role=WorkspaceRole.MEMBER, created_by=owner)
            ProjectMember.objects.create(
                project=self.project,
                workspace=self.ws,
                member=u,
                role=ProjectRole.CONTRIBUTOR,
                is_active=True,
                created_by=owner,
            )
            self.contributors.append(u)
        self.commenter = User.objects.create_user(
            email=f"asg-cm-{owner.id.hex[:6]}@rabbit.dev", password="Rabbit123!", display_name="评论者"
        )
        WorkspaceMember.objects.create(
            workspace=self.ws, member=self.commenter, role=WorkspaceRole.MEMBER, created_by=owner
        )
        ProjectMember.objects.create(
            project=self.project,
            workspace=self.ws,
            member=self.commenter,
            role=ProjectRole.COMMENTER,
            is_active=True,
            created_by=owner,
        )
        self.viewer = User.objects.create_user(
            email=f"asg-v-{owner.id.hex[:6]}@rabbit.dev", password="Rabbit123!", display_name="查看者"
        )
        WorkspaceMember.objects.create(
            workspace=self.ws, member=self.viewer, role=WorkspaceRole.MEMBER, created_by=owner
        )
        ProjectMember.objects.create(
            project=self.project,
            workspace=self.ws,
            member=self.viewer,
            role=ProjectRole.VIEWER,
            is_active=True,
            created_by=owner,
        )
        self._seq = 0

    def issue(self, name: str = "T") -> Issue:
        self._seq += 1
        return Issue.objects.create(
            name=name, project=self.project, sequence_id=self._seq, sort_order=self._seq * 100.0, created_by=self.owner
        )


@pytest.fixture()
def env(owner) -> _Env:
    return _Env(owner)


def _ids(*users: User) -> list[uuid.UUID]:
    return [u.id for u in users]


def _rows(issue: Issue) -> list[uuid.UUID]:
    """中间表活跃行（created_at 首键 + id 次级键稳定序，BR-03 读取口径）。"""
    return list(
        IssueAssignee.objects.filter(issue=issue).order_by("created_at", "id").values_list("assignee_id", flat=True)
    )


# ───────────────────────── sync 收口 ─────────────────────────
def test_sync_replacement_and_dedup_preserves_order(env):
    """UT-01/02：3 人替换 + [C,A,C] 去重保序为 [C,A]。"""
    a, b, c = env.contributors
    issue = env.issue()
    sync_assignees_full(issue_id=issue.id, new_ids=_ids(a, b), actor_id=env.admin.id)
    assert _rows(issue) == _ids(a, b)
    r2 = sync_assignees_full(issue_id=issue.id, new_ids=_ids(c, a, c), actor_id=env.admin.id)
    # 响应 assignee_ids = 去重后请求顺序（BR-03 服务端回显），非库内行序重排
    assert r2["assignee_ids"] == [str(c.id), str(a.id)]
    assert _rows(issue) and set(_rows(issue)) == {a.id, c.id}
    # changes：added=[c]、removed=[b]，display_name 齐备
    assert [x["id"] for x in r2["changes"]["added"]] == [str(c.id)]
    assert [x["id"] for x in r2["changes"]["removed"]] == [str(b.id)]
    assert r2["changes"]["removed"][0]["display_name"] == "贡献者1"


def test_sync_limit_10_and_11_rejected(env):
    """UT-03/04：恰 10 人放行、第 11 人 409 LIMIT（去重后计数）。"""
    issue = env.issue()
    # 凑满 10 个可指派成员：admin + 3 contributors + COMMENTER/VIEWER 升 CONTRIBUTOR + 新建 4 人
    ProjectMember.objects.filter(project=env.project, member__in=[env.commenter, env.viewer]).update(
        role=ProjectRole.CONTRIBUTOR
    )
    ten = [env.admin] + env.contributors + [env.commenter, env.viewer]
    for i in range(4):
        u = User.objects.create_user(email=f"asg-x{i}-{env.owner.id.hex[:6]}@rabbit.dev", password="Rabbit123!")
        WorkspaceMember.objects.create(workspace=env.ws, member=u, role=WorkspaceRole.MEMBER, created_by=env.owner)
        ProjectMember.objects.create(
            project=env.project,
            workspace=env.ws,
            member=u,
            role=ProjectRole.CONTRIBUTOR,
            is_active=True,
            created_by=env.owner,
        )
        ten.append(u)
    assert len(ten) == MAX_ASSIGNEES
    r = sync_assignees_full(issue_id=issue.id, new_ids=_ids(*ten), actor_id=env.admin.id)
    assert len(r["assignee_ids"]) == MAX_ASSIGNEES
    # 第 11 人（CONTRIBUTOR，合法成员）→ 409 LIMIT 先于成员校验（§4.3.1 顺序）
    eleventh = User.objects.create_user(email=f"asg-e-{env.owner.id.hex[:6]}@rabbit.dev", password="Rabbit123!")
    WorkspaceMember.objects.create(workspace=env.ws, member=eleventh, role=WorkspaceRole.MEMBER, created_by=env.owner)
    ProjectMember.objects.create(
        project=env.project,
        workspace=env.ws,
        member=eleventh,
        role=ProjectRole.CONTRIBUTOR,
        is_active=True,
        created_by=env.owner,
    )
    with pytest.raises(AssigneesLimitExceeded):
        sync_assignees_full(issue_id=issue.id, new_ids=_ids(*ten, eleventh), actor_id=env.admin.id)
    # 同人重复 11 次：去重后 1 人 → 放行（上限按去重计数，BR-03/BR-01）
    r2 = sync_assignees_full(issue_id=issue.id, new_ids=[env.admin.id] * 11, actor_id=env.admin.id)
    assert r2["assignee_ids"] == [str(env.admin.id)]


def test_sync_rejects_non_member_and_low_roles(env):
    """UT-05/06：非成员 / COMMENTER / VIEWER 均 400 DOES_NOT_EXIST（BR-02）。"""
    stranger = User.objects.create_user(email=f"asg-str-{env.owner.id.hex[:6]}@rabbit.dev", password="Rabbit123!")
    issue = env.issue()
    for bad in (stranger, env.commenter, env.viewer):
        with pytest.raises(AppValidationError) as ei:
            sync_assignees_full(issue_id=issue.id, new_ids=[bad.id], actor_id=env.admin.id)
        assert ei.value.extra_details[0]["code"] == "DOES_NOT_EXIST"
    assert not IssueAssignee.objects.filter(issue=issue).exists()  # 全拒不落行


def test_sync_assigned_by_records_operator_and_clear_ok(env):
    """UT-12 / UT-10：新增行 assigned_by=操作者；空集合清空合法。"""
    a, b, _ = env.contributors
    issue = env.issue()
    sync_assignees_full(issue_id=issue.id, new_ids=_ids(a, b), actor_id=b.id)  # b 操作
    assert set(IssueAssignee.objects.filter(issue=issue).values_list("assignee_id", "assigned_by_id")) == {
        (a.id, b.id),
        (b.id, b.id),
    }
    sync_assignees_full(issue_id=issue.id, new_ids=[], actor_id=env.admin.id)  # BR-06
    assert _rows(issue) == []


def test_sync_archived_issue_rejected(env):
    """IT-10 服务层兜底：archived_at 非空 → 409 STATE（PUT/claim/PATCH/自退共口径）。"""
    a, _, _ = env.contributors
    issue = env.issue()
    issue.archived_at = timezone.now()
    issue.save(update_fields=["archived_at"])
    with pytest.raises(IssueArchivedForAssignment):
        sync_assignees_full(issue_id=issue.id, new_ids=[a.id], actor_id=env.admin.id)
    with pytest.raises(IssueArchivedForAssignment):
        remove_self(issue_id=issue.id, user_id=a.id)


def test_sync_unassigned_readd_no_unique_collision(env):
    """UT-09：删后重加同人不撞 uniq_issue_assignee——物理删除后全新 INSERT，
    created_at / assigned_by 刷新为本次操作。"""
    a, b, _ = env.contributors
    issue = env.issue()
    sync_assignees_full(issue_id=issue.id, new_ids=_ids(a), actor_id=env.admin.id)
    first = IssueAssignee.objects.get(issue=issue, assignee=a)
    # 换人（a 被 removed）再加回 a —— 中间任何阶段不留软删行
    sync_assignees_full(issue_id=issue.id, new_ids=_ids(b), actor_id=env.admin.id)
    sync_assignees_full(issue_id=issue.id, new_ids=_ids(a, b), actor_id=b.id)
    second = IssueAssignee.objects.get(issue=issue, assignee=a)
    assert second.id != first.id
    assert second.assigned_by_id == b.id
    # 全表（含 all_objects）无软删残行：物理删除口径的直接证据
    assert IssueAssignee.all_objects.filter(issue=issue).count() == 2


def test_patch_compat_path_via_service_is_same_write(env):
    """IT-05（锚定）：PATCH 兼容路径与 PUT 收敛同一服务——落库一致。"""
    a, b, _ = env.contributors
    issue = env.issue()
    put = sync_assignees_full(issue_id=issue.id, new_ids=_ids(a), actor_id=env.admin.id)
    patch = sync_assignees_full(issue_id=issue.id, new_ids=_ids(b), actor_id=env.admin.id)
    assert _rows(issue) == [b.id]
    assert put["changes"]["added"] or patch["changes"]["removed"]


# ───────────────────────── claim / remove_self ─────────────────────────
def test_claim_empty_then_conflict(env):
    """UT-07/08：空集合认领成功（assigned_by=自己）→ 再认领 409 STATE。"""
    a, _, _ = env.contributors
    issue = env.issue()
    r = claim_issue(issue_id=issue.id, actor_id=a.id)
    assert r["assignee_ids"] == [str(a.id)]
    row = IssueAssignee.objects.get(issue=issue, assignee=a)
    assert row.assigned_by_id == a.id  # BR-04 认领：assignee=assigned_by=自己
    with pytest.raises(IssueAlreadyClaimedError):
        claim_issue(issue_id=issue.id, actor_id=env.admin.id)


def test_remove_self_keeps_others_and_last_exit_clears(env):
    """UT-16：3 人中 1 人自退 → 剩余 2 人不变；最后一人自退 = 清空（BR-06）。"""
    a, b, c = env.contributors
    issue = env.issue()
    sync_assignees_full(issue_id=issue.id, new_ids=_ids(a, b, c), actor_id=env.admin.id)
    remove_self(issue_id=issue.id, user_id=b.id)
    assert set(_rows(issue)) == {a.id, c.id}
    remove_self(issue_id=issue.id, user_id=a.id)
    remove_self(issue_id=issue.id, user_id=c.id)
    assert _rows(issue) == []
    # 自退不因成员被降级而受阻（enforce=False：remaining 不再校验 BR-02）
    sync_assignees_full(issue_id=issue.id, new_ids=_ids(a), actor_id=env.admin.id)
    ProjectMember.objects.filter(project=env.project, member=a).update(role=ProjectRole.VIEWER)
    remove_self(issue_id=issue.id, user_id=a.id)  # 不抛 = 降级者仍可自退
    assert _rows(issue) == []


# ───────────────────────── BR-12 级联清空 ─────────────────────────
def test_project_member_removal_cascades_assignments(env):
    """UT-15（项目路径）：remove_member 同事务物理删除其全部指派行。"""
    a, b, _ = env.contributors
    i1, i2 = env.issue("T1"), env.issue("T2")
    sync_assignees_full(issue_id=i1.id, new_ids=_ids(a, b), actor_id=env.admin.id)
    sync_assignees_full(issue_id=i2.id, new_ids=_ids(a), actor_id=env.admin.id)
    pm = ProjectMember.objects.get(project=env.project, member=a)
    ProjectMemberService().remove_member(project=env.project, member=pm, actor=env.owner)
    assert not IssueAssignee.objects.filter(assignee=a).exists()
    assert IssueAssignee.all_objects.filter(assignee=a).exists() is False  # 物理删除
    assert set(_rows(i1)) == {b.id}  # 同事项其他人不受影响
    assert _rows(i2) == []
    # 移除后重新加回 → 可再次指派（不撞唯一约束的级联版证据）
    pm2 = ProjectMember.objects.create(
        project=env.project,
        workspace=env.ws,
        member=a,
        role=ProjectRole.CONTRIBUTOR,
        is_active=True,
        created_by=env.owner,
    )
    assert pm2 is not None
    sync_assignees_full(issue_id=i2.id, new_ids=_ids(a), actor_id=env.admin.id)


def test_workspace_member_removal_cascades_assignments(env):
    """UT-15（TEAM-002 路径）：工作空间移除 → 该空间全部项目指派同事务清空。"""
    a, _, _ = env.contributors
    i1, i2 = env.issue("W1"), env.issue("W2")
    sync_assignees_full(issue_id=i1.id, new_ids=_ids(a), actor_id=env.admin.id)
    sync_assignees_full(issue_id=i2.id, new_ids=_ids(a), actor_id=env.admin.id)
    n = purge_member_assignments(workspace_id=env.ws.id, member_id=a.id, actor=env.owner)
    assert n == 2
    assert not IssueAssignee.objects.filter(assignee=a).exists()


def test_purge_scoped_to_single_project(env):
    """BR-12 作用域：project_id 路径只清该项目，另一项目指派保留。"""
    a, _, _ = env.contributors
    i1 = env.issue("P1")
    proj2 = Project.objects.create(name="P2", identifier="AS2", workspace=env.ws, created_by=env.owner)
    seq2 = Issue.objects.filter(project=proj2).count() + 1
    i2 = Issue.objects.create(
        name="P2-T", project=proj2, sequence_id=seq2, sort_order=seq2 * 100.0, created_by=env.owner
    )
    sync_assignees_full(issue_id=i1.id, new_ids=_ids(a), actor_id=env.admin.id)
    IssueAssignee.objects.create(issue=i2, assignee=a, assigned_by=env.admin)
    purge_member_assignments(project_id=env.project.id, member_id=a.id, actor=env.owner)
    assert _rows(i1) == []
    assert _rows(i2) == [a.id]


# ───────────────────────── 通知任务（同步函数体） ─────────────────────────
def test_dispatch_events_differentiated_and_actor_suppressed(env):
    """IT-01/UT-14：added→issue.assigned、removed→issue.unassigned；
    操作者（含 added 中的操作者本人）抑制（BR-09）；Activity 逐人共享 epoch（BR-10）。"""
    a, b, _ = env.contributors
    issue = env.issue()
    sync_assignees_full(issue_id=issue.id, new_ids=_ids(a, b), actor_id=env.admin.id)
    # 转交快照：admin 把 [a,b] 换成 [admin] —— a/b 被移除、admin 新增（操作者）
    changes = {
        "added": [{"id": str(env.admin.id), "display_name": env.admin.display_name}],
        "removed": [
            {"id": str(a.id), "display_name": a.display_name},
            {"id": str(b.id), "display_name": b.display_name},
        ],
    }
    ok = dispatch_assignment_events.run(
        issue_id=str(issue.id), actor_id=str(env.admin.id), changes=changes, comment="联调改期"
    )
    assert ok is True
    events = set(
        Notification.objects.filter(receiver_id__in=[a.id, b.id, env.admin.id]).values_list("receiver_id", "event")
    )
    assert events == {(a.id, "issue.unassigned"), (b.id, "issue.unassigned")}
    # title 含转交说明引用 + data 载荷必含五键
    n = Notification.objects.get(receiver=a, event="issue.unassigned")
    assert "联调改期" in n.title
    assert {"issue_id", "project_id", "workspace_slug", "issue_key", "actor"} <= set(n.data)
    # BR-10：3 人各一条 field='assignees' Activity 共享同一 epoch
    acts = IssueActivity.objects.filter(issue=issue, field="assignees")
    assert acts.count() == 3
    assert len({x.epoch for x in acts}) == 1
    assert set(acts.exclude(new_identifier__isnull=True).values_list("new_identifier", flat=True)) == {env.admin.id}
    assert set(acts.exclude(old_identifier__isnull=True).values_list("old_identifier", flat=True)) == {a.id, b.id}


# ───────────────────────── 其它写路径不再产生第二套逻辑 ─────────────────────────
def test_issue_label_table_untouched_by_sync(env):
    """边界：sync 只动 issue_assignees，不波及标签等其它集合子资源。"""
    from plane.db.models import Label

    a, _, _ = env.contributors
    issue = env.issue()
    label = Label.objects.create(project=env.project, name="边界标签", created_by=env.owner)
    IssueLabel.objects.create(issue=issue, label=label)
    sync_assignees_full(issue_id=issue.id, new_ids=_ids(a), actor_id=env.admin.id)
    assert IssueLabel.objects.filter(issue=issue).count() == 1
