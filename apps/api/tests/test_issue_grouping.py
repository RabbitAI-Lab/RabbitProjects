"""BOARD-003 分组端点泛化测试（T3-04：维度解析 / 列配置生成 / 双计数 / 端点冒烟）。

UT 对应 §5：UT-14（列集合断言零 SQL 扫表——配置源直查）、UT-19（别名归一）、
IT-01/IT-02 的服务层前置。HTTP 分组信封全矩阵在 sprint-3-flow.py（Phase 4）。
"""
from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from plane.base.exception import AppException
from plane.db.models import (
    CustomFieldDefinition,
    Issue,
    IssueType,
    Project,
    ProjectMember,
    ProjectRole,
    State,
    User,
    Workspace,
)
from plane.db.seeds.project_states import seed_project_states
from plane.db.seeds.project_views import seed_project_views
from plane.db.services.issue_grouping import (
    column_counts,
    get_group_columns,
    resolve_dimension,
)

pytestmark = pytest.mark.django_db

OPTS = [
    {"label": "致命", "value": "critical", "color": "#DC2626", "sort_order": 1},
    {"label": "严重", "value": "major", "color": "#F59E0B", "sort_order": 2},
]


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="grp-owner@rabbit.dev", password="Rabbit123!")
    member = User.objects.create_user(email="grp-member@rabbit.dev", password="Rabbit123!")
    viewer = User.objects.create_user(email="grp-viewer@rabbit.dev", password="Rabbit123!")
    ws = Workspace.objects.create(name="W", slug=f"w-grp-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    proj = Project.objects.create(name="P", identifier="GP", workspace=ws, created_by=owner)
    from plane.db.models import WorkspaceMember, WorkspaceRole

    for u, ws_role in ((owner, WorkspaceRole.OWNER), (member, WorkspaceRole.MEMBER), (viewer, WorkspaceRole.MEMBER)):
        WorkspaceMember.objects.create(workspace=ws, member=u, role=ws_role, created_by=owner)
    ProjectMember.objects.create(project=proj, member=member, role=ProjectRole.CONTRIBUTOR, created_by=owner)
    ProjectMember.objects.create(project=proj, member=viewer, role=ProjectRole.VIEWER, created_by=owner)
    seed_project_states(proj)
    IssueType.objects.create(workspace=ws, name="需求", is_default=True, created_by=owner)
    IssueType.objects.create(workspace=ws, name="缺陷", is_default=False, created_by=owner)
    todo = State.objects.get(project=proj, group=State.Group.UNSTARTED)
    doing = State.objects.get(project=proj, group=State.Group.STARTED)
    CustomFieldDefinition.objects.create(
        workspace=ws, project=proj, name="严重等级", field_key="cf_severity",
        field_type="select", options=OPTS, created_by=owner,
    )
    seq = iter(range(1, 100))

    def mk(name, *, state=todo, pri="none", assignees=(), cf=None, target=None):
        issue = Issue.objects.create(
            name=name, project=proj, state=state, priority=pri,
            sequence_id=next(seq), sort_order=next(seq) * 100, created_by=owner,
            target_date=target, custom_fields=cf or {},
        )
        from plane.db.models import IssueAssignee

        for a in assignees:
            IssueAssignee.objects.create(issue=issue, assignee=a, created_by=owner)
        return issue

    issues = {
        "urgent": mk("U1", pri="urgent", assignees=[owner], cf={"cf_severity": "critical"}),
        "high": mk("H1", pri="high", state=doing, assignees=[member], cf={"cf_severity": "major"}),
        "none_unassigned": mk("N1", pri="none"),
    }
    return {
        "owner": owner, "member": member, "viewer": viewer, "ws": ws, "proj": proj,
        "todo": todo, "doing": doing, "issues": issues,
    }


class TestResolveDimension:
    def test_builtin_and_alias(self, env):
        p = env["proj"]
        assert resolve_dimension(p, "state_id") == "state_id"
        assert resolve_dimension(p, "priority") == "priority"
        assert resolve_dimension(p, "state") == "state_id"       # Schema 键别名归一（UT-19）
        assert resolve_dimension(p, "assignees") == "assignee_id"
        assert resolve_dimension(p, "labels") == "label_id"
        assert resolve_dimension(p, "cf_severity") == "cf_severity"

    def test_invalid_dimensions(self, env):
        for dim in ("cf_ghost", "sort_order", ""):
            with pytest.raises(AppException) as ei:
                resolve_dimension(env["proj"], dim)
            assert ei.value.error_code == "VALIDATION_INVALID_PARAM"

    def test_nongroupable_cf_rejected(self, env, db):
        CustomFieldDefinition.objects.create(
            workspace=env["ws"], project=env["proj"], name="根因",
            field_key="cf_rca", field_type="text", created_by=env["owner"],
        )
        with pytest.raises(AppException) as ei:
            resolve_dimension(env["proj"], "cf_rca")
        assert "不支持分组" in ei.value.extra_details[0]["message"]


class TestGroupColumns:
    def test_state_no_none_column(self, env):
        cols = get_group_columns(env["proj"], "state_id")
        assert len(cols) == 4 and all(c["key"] != "__none__" for c in cols)

    def test_priority_fixed_enum(self, env):
        cols = get_group_columns(env["proj"], "priority")
        assert [c["key"] for c in cols] == ["urgent", "high", "medium", "low", "none"]
        assert cols[-1]["key"] == "none"  # 合法枚举值列，非哨兵

    def test_assignee_columns_with_none_last(self, env):
        cols = get_group_columns(env["proj"], "assignee_id")
        assert cols[-1]["key"] == "__none__" and cols[-1]["label"] == "未指派"
        # 列源 = active ProjectMember（PROJ-002）；owner 走 WS 隐式 ADMIN、无成员行故不设列
        assert {c["key"] for c in cols[:-1]} == {str(env["member"].id), str(env["viewer"].id)}

    def test_label_columns_with_none(self, env):
        cols = get_group_columns(env["proj"], "label_id")
        assert cols[-1]["key"] == "__none__"

    def test_cf_columns_from_options(self, env):
        cols = get_group_columns(env["proj"], "cf_severity")
        assert [c["key"] for c in cols] == ["critical", "major", "__none__"]
        assert cols[0]["label"] == "致命"


class TestColumnCounts:
    def test_priority_counts(self, env):
        base = Issue.objects.filter(project=env["proj"], deleted_at__isnull=True, archived_at__isnull=True)
        counts = column_counts(base, "priority", env["proj"])
        assert counts == {"urgent": 1, "high": 1, "none": 1}

    def test_assignee_none_count(self, env):
        base = Issue.objects.filter(project=env["proj"], deleted_at__isnull=True, archived_at__isnull=True)
        counts = column_counts(base, "assignee_id", env["proj"])
        assert counts[str(env["owner"].id)] == 1
        assert counts[str(env["member"].id)] == 1
        assert counts["__none__"] == 1

    def test_cf_counts(self, env):
        base = Issue.objects.filter(project=env["proj"], deleted_at__isnull=True, archived_at__isnull=True)
        counts = column_counts(base, "cf_severity", env["proj"])
        assert counts == {"critical": 1, "major": 1, "__none__": 1}


class _Client:
    def __init__(self, user):
        self.c = APIClient()
        self.c.force_authenticate(user=user)

    def get(self, path):
        return self.c.get(path, format="json")


class TestGroupedEndpoint:
    def _url(self, env, query=""):
        return f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/issues/{query}"

    def test_priority_grouping_envelope(self, env):
        resp = _Client(env["owner"]).get(self._url(env, "?group_by=priority"))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert list(data.keys()) == ["urgent", "high", "medium", "low", "none"]
        assert data["urgent"]["total_results"] == 1
        assert data["urgent"]["results"][0]["name"] == "U1"
        assert data["medium"]["total_results"] == 0           # 空列恒在
        assert data["medium"]["results"] == []
        assert data["urgent"]["unfiltered_total_results"] == 1
        meta = resp.json()["meta"]
        assert meta["grouped_by"] == "priority"

    def test_assignee_grouping_none_column(self, env):
        resp = _Client(env["owner"]).get(self._url(env, "?group_by=assignee_id"))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["__none__"]["total_results"] == 1
        assert data["__none__"]["results"][0]["name"] == "N1"

    def test_invalid_group_by_400(self, env):
        resp = _Client(env["owner"]).get(self._url(env, "?group_by=cf_ghost"))
        body = resp.json()
        assert resp.status_code == 400
        assert body["error"]["code"] == "VALIDATION_INVALID_PARAM"
        assert body["error"]["details"][0]["field"] == "group_by"

    def test_state_grouping_backward_compatible(self, env):
        """BOARD-002 契约回归：state_id 维度键 = State 裸 UUID。"""
        resp = _Client(env["owner"]).get(self._url(env, "?group_by=state_id"))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert str(env["todo"].id) in data
        assert data[str(env["todo"].id)]["total_results"] == 2

    def test_view_id_applies_filters_and_meta(self, env):
        seed_project_views(env["proj"])
        from plane.db.models import IssueView

        mine = IssueView.objects.get(project=env["proj"], name="我的待办", is_system=True)
        resp = _Client(env["owner"]).get(self._url(env, f"?group_by=state_id&view_id={mine.id}"))
        assert resp.status_code == 200
        body = resp.json()
        # 我的待办 = assignees in [@me] + state.group in [unstarted, started]
        assert body["meta"]["view_id"] == str(mine.id)
        assert body["meta"]["applied"].get("assignees") == ["@me"]
        assert body["meta"]["applied"].get("state.group") == ["unstarted", "started"]
        assert body["meta"]["total_count"] == 1  # 仅 U1（owner + unstarted）

    def test_other_personal_view_access_matrix(self, env):
        """他人个人视图：本人/成员(CONTRIBUTOR+ = board.manage 审计)可见，VIEWER 404 隐藏。"""
        from plane.db.models import IssueView

        other_view = IssueView.objects.create(
            workspace=env["ws"], project=env["proj"], owner=env["member"],
            name="成员私有", filters={}, display_props={},
        )
        url = self._url(env, f"?group_by=state_id&view_id={other_view.id}")
        assert _Client(env["member"]).get(url).status_code == 200       # 本人
        # 应用面收严（ADR-0021）：board.manage 审计仅限 views/{id}/ CRUD 面，
        # 列表/分组消费一律存在性隐藏——含 WS 隐式 ADMIN
        assert _Client(env["owner"]).get(url).status_code == 404
        assert _Client(env["viewer"]).get(url).status_code == 404
