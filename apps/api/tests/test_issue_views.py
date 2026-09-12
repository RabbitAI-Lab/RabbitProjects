"""BOARD-003 视图单元测试（T3-02/T3-03 落点子集，UT 对应 §5 矩阵）。

覆盖：种子幂等与双触发核心（UT-08 口径）、保存防线 BR-01~09（数量上限 / access /
layout / 扁平 filters / group_by 白名单 / card_fields 键域 / icon 预设）、读取降级
resolve_view（BR-08 条件剔除 + 分组回退）、默认视图偏好合并（BR-10）。
HTTP 全矩阵（CRUD/409/403/404/204）在 sprint-3-flow.py（Phase 4）。
"""

from __future__ import annotations

import pytest

from plane.base.exception import AppException
from plane.db.models import (
    CustomFieldDefinition,
    IssueView,
    Project,
    ProjectMember,
    ProjectRole,
    User,
    Workspace,
)
from plane.db.seeds.project_views import BUILTIN_VIEWS, seed_project_views
from plane.db.services.view_service import (
    ICON_POOL,
    resolve_view,
    validate_view_payload,
)

pytestmark = pytest.mark.django_db

OPTS = [
    {"label": "致命", "value": "critical", "color": "#DC2626", "sort_order": 1},
    {"label": "严重", "value": "major", "color": "#F59E0B", "sort_order": 2},
]


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="view-owner@rabbit.dev", password="Rabbit123!")
    other = User.objects.create_user(email="view-other@rabbit.dev", password="Rabbit123!")
    ws = Workspace.objects.create(name="W", slug=f"w-view-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    proj = Project.objects.create(name="P", identifier="VW", workspace=ws, created_by=owner)
    from plane.db.models import WorkspaceMember, WorkspaceRole

    # HTTP 面用例（详情端点）需过 get_workspace_or_404 的 WS 成员校验
    WorkspaceMember.objects.create(workspace=ws, member=owner, role=WorkspaceRole.OWNER, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=other, role=WorkspaceRole.MEMBER, created_by=owner)
    ProjectMember.objects.create(project=proj, member=other, role=ProjectRole.CONTRIBUTOR, created_by=owner)
    return {"owner": owner, "other": other, "ws": ws, "proj": proj}


def _mk_view(env, name="我的视图", **kw) -> IssueView:
    return IssueView.objects.create(
        workspace=env["ws"],
        project=env["proj"],
        owner=kw.pop("owner", env["owner"]),
        name=name,
        **kw,
    )


def _payload(**over) -> dict:
    p = {
        "name": "救火看板",
        "layout": "kanban",
        "access": "personal",
        "filters": {
            "op": "AND",
            "conditions": [
                {"field": "priority", "operator": "in", "value": ["high", "urgent"]},
            ],
        },
        "display_props": {"icon": "🚒", "group_by": "priority"},
    }
    p.update(over)
    return p


def _raises_code(payload, env, code: str, instance=None):
    with pytest.raises(AppException) as ei:
        validate_view_payload(project=env["proj"], payload=payload, instance=instance)
    assert ei.value.error_code == code, f"期望 {code}，实际 {ei.value.error_code}"
    return ei.value


# ─────────────────────────────────────────────────────────────────────
# 种子（§4.1.2 双触发 · 幂等）
# ─────────────────────────────────────────────────────────────────────
class TestSeed:
    def test_builtin_views_integrity(self):
        assert len(BUILTIN_VIEWS) == 5
        assert {v["name"] for v in BUILTIN_VIEWS} == {"需求池", "缺陷列表", "我的待办", "本周到期", "测试执行"}
        seed_keys = {"name", "layout", "filters", "display_props", "sort_order"}
        for v in BUILTIN_VIEWS:
            assert v["display_props"]["icon"] in ICON_POOL
            # 种子 defaults 仅传模型字段（迁移 FieldError 防线，§4.1.2 注）
            assert set(v) == seed_keys

    def test_seed_idempotent(self, env):
        assert seed_project_views(env["proj"]) == 5
        assert seed_project_views(env["proj"]) == 0  # 幂等重跑
        names = list(
            IssueView.objects.filter(project=env["proj"], is_system=True)
            .order_by("sort_order")
            .values_list("name", flat=True)
        )
        assert names == [v["name"] for v in BUILTIN_VIEWS]

    def test_seed_owner_is_project_creator(self, env):
        seed_project_views(env["proj"])
        v = IssueView.objects.filter(project=env["proj"], is_system=True).first()
        assert v.owner_id == env["proj"].created_by_id


# ─────────────────────────────────────────────────────────────────────
# 保存防线（§4.3.1 validate_view_payload）
# ─────────────────────────────────────────────────────────────────────
class TestValidatePayload:
    def test_valid_flat_payload_passes(self, env):
        validate_view_payload(project=env["proj"], payload=_payload(), instance=None)

    def test_placeholder_conditions_pass(self, env):
        payload = _payload(
            filters={
                "op": "AND",
                "conditions": [
                    {"field": "assignees", "operator": "in", "value": ["@me"]},
                    {"field": "state.group", "operator": "in", "value": ["unstarted", "started"]},
                    {"field": "issue_type", "operator": "in", "value": ["__bug__"]},
                    {"field": "target_date", "operator": "between", "value": ["this_week"]},
                ],
            }
        )
        validate_view_payload(project=env["proj"], payload=payload, instance=None)

    def test_access_shared_rejected(self, env):
        _raises_code(_payload(access="shared"), env, "VALIDATION_ERROR")

    def test_layout_invalid_rejected(self, env):
        _raises_code(_payload(layout="timeline"), env, "VALIDATION_ERROR")

    def test_nested_conditions_allowed(self, env):
        """TASK-011 超集接管：嵌套 ≤3 放开（P2 扁平限制废止）；4 层仍拒。"""
        nested = {
            "op": "AND",
            "conditions": [
                {
                    "op": "OR",
                    "conditions": [
                        {"field": "priority", "operator": "in", "value": ["high"]},
                        {
                            "op": "AND",
                            "conditions": [
                                {"field": "name", "operator": "contains", "value": "x"},
                            ],
                        },
                    ],
                },
            ],
        }
        validate_view_payload(project=env["proj"], payload=_payload(filters=nested), instance=None)
        depth4 = {"op": "AND", "conditions": [nested]}  # 包一层 → 4 层，拒
        exc = _raises_code(_payload(filters=depth4), env, "VALIDATION_INVALID_PARAM")
        assert "嵌套层级" in exc.extra_details[0]["message"]

    def test_or_op_allowed(self, env):
        """TASK-011：顶层 OR 合法（AND/OR 递归放开）。"""
        filters = {"op": "OR", "conditions": [{"field": "priority", "operator": "in", "value": ["high"]}]}
        validate_view_payload(project=env["proj"], payload=_payload(filters=filters), instance=None)

    def test_too_many_conditions_rejected(self, env):
        conds = [{"field": "priority", "operator": "in", "value": ["high"]}] * 21
        _raises_code(_payload(filters={"op": "AND", "conditions": conds}), env, "VALIDATION_INVALID_PARAM")

    def test_unknown_builtin_field_rejected(self, env):
        conds = [{"field": "no_such", "operator": "in", "value": ["x"]}]
        _raises_code(_payload(filters={"op": "AND", "conditions": conds}), env, "VALIDATION_INVALID_PARAM")

    def test_unknown_cf_field_rejected(self, env):
        conds = [{"field": "cf_ghost", "operator": "in", "value": ["x"]}]
        _raises_code(_payload(filters={"op": "AND", "conditions": conds}), env, "VALIDATION_INVALID_PARAM")

    def test_active_cf_field_passes(self, env):
        CustomFieldDefinition.objects.create(
            workspace=env["ws"],
            project=env["proj"],
            name="严重等级",
            field_key="cf_severity",
            field_type="select",
            options=OPTS,
            created_by=env["owner"],
        )
        conds = [{"field": "cf_severity", "operator": "in", "value": ["critical"]}]
        payload = _payload(filters={"op": "AND", "conditions": conds})
        validate_view_payload(project=env["proj"], payload=payload, instance=None)

    def test_value_must_be_list(self, env):
        conds = [{"field": "priority", "operator": "in", "value": "high"}]
        _raises_code(_payload(filters={"op": "AND", "conditions": conds}), env, "VALIDATION_INVALID_PARAM")

    def test_value_over_50_rejected(self, env):
        conds = [{"field": "priority", "operator": "in", "value": [f"v{i}" for i in range(51)]}]
        _raises_code(_payload(filters={"op": "AND", "conditions": conds}), env, "VALIDATION_INVALID_PARAM")

    def test_group_by_nongroupable_rejected(self, env):
        CustomFieldDefinition.objects.create(
            workspace=env["ws"],
            project=env["proj"],
            name="根因",
            field_key="cf_rca",
            field_type="text",
            created_by=env["owner"],
        )
        _raises_code(_payload(display_props={"group_by": "cf_rca"}), env, "VALIDATION_ERROR")

    def test_group_by_builtin_and_select_pass(self, env):
        CustomFieldDefinition.objects.create(
            workspace=env["ws"],
            project=env["proj"],
            name="严重等级",
            field_key="cf_severity",
            field_type="select",
            options=OPTS,
            created_by=env["owner"],
        )
        for dim in ("state_id", "priority", "assignee_id", "label_id", "cf_severity"):
            validate_view_payload(project=env["proj"], payload=_payload(display_props={"group_by": dim}), instance=None)

    def test_card_fields_unknown_keys_stripped(self, env):
        payload = _payload(
            display_props={
                "group_by": "priority",
                "card_fields": {"labels": True, "sub_issues": True, "ghost_key": True, "timer": False},
            }
        )
        validate_view_payload(project=env["proj"], payload=payload, instance=None)
        assert payload["display_props"]["card_fields"] == {"labels": True, "sub_issues": True, "timer": False}

    def test_icon_outside_pool_rejected(self, env):
        _raises_code(_payload(display_props={"icon": "⭐"}), env, "VALIDATION_ERROR")

    def test_view_limit_20_including_system(self, env):
        seed_project_views(env["proj"])  # 内置 5
        for i in range(15):
            _mk_view(env, name=f"个人{i}")
        assert IssueView.objects.filter(project=env["proj"], deleted_at__isnull=True).count() == 20
        _raises_code(_payload(), env, "RESOURCE_LIMIT_EXCEEDED")

    def test_limit_not_hit_on_patch(self, env):
        seed_project_views(env["proj"])
        for i in range(15):
            _mk_view(env, name=f"个人{i}")
        view = IssueView.objects.filter(project=env["proj"], is_system=False).first()
        # PATCH 已有视图不占新名额（instance 非 None 跳过计数）
        validate_view_payload(project=env["proj"], payload=_payload(name="改名"), instance=view)


# ─────────────────────────────────────────────────────────────────────
# 读取降级（§4.3.1 resolve_view · BR-08）
# ─────────────────────────────────────────────────────────────────────
class TestResolveView:
    def _cf(self, env, key, ftype, active=True, **kw):
        CustomFieldDefinition.objects.create(
            workspace=env["ws"],
            project=env["proj"],
            name=kw.pop("name", key),
            field_key=key,
            field_type=ftype,
            options=OPTS if ftype in ("select", "multi_select") else [],
            is_active=active,
            created_by=env["owner"],
        )

    def test_inactive_cf_condition_dropped(self, env):
        self._cf(env, "cf_severity", "select", active=False)
        view = _mk_view(
            env,
            filters={
                "op": "AND",
                "conditions": [
                    {"field": "priority", "operator": "in", "value": ["high"]},
                    {"field": "cf_severity", "operator": "in", "value": ["critical"]},
                ],
            },
        )
        filters, degraded = resolve_view(view, project=env["proj"], user=env["owner"])
        assert [c["field"] for c in filters["conditions"]] == ["priority"]
        assert degraded and "已停用" in degraded["filters"]

    def test_inactive_group_by_falls_back_to_state(self, env):
        self._cf(env, "cf_severity", "select", active=False)
        view = _mk_view(env, display_props={"group_by": "cf_severity"})
        _, degraded = resolve_view(view, project=env["proj"], user=env["owner"])
        assert degraded and "state_id" in str(degraded) or degraded["group_by"]

    def test_active_view_no_degradation(self, env):
        self._cf(env, "cf_severity", "select")
        view = _mk_view(
            env,
            filters={
                "op": "AND",
                "conditions": [
                    {"field": "cf_severity", "operator": "in", "value": ["critical"]},
                ],
            },
            display_props={"group_by": "cf_severity"},
        )
        filters, degraded = resolve_view(view, project=env["proj"], user=env["owner"])
        assert degraded is None
        assert len(filters["conditions"]) == 1


# ─────────────────────────────────────────────────────────────────────
# 详情端点（GET/PATCH/DELETE HTTP 面——retrieve 缺省 for_write 回归锚）
# ─────────────────────────────────────────────────────────────────────
class TestViewDetailEndpoint:
    def _url(self, env, view_id):
        return f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/views/{view_id}/"

    def _client(self, user):
        from rest_framework.test import APIClient

        c = APIClient()
        c.force_authenticate(user=user)
        return c

    def test_get_own_view_200(self, env):
        view = _mk_view(env, name="我的", display_props={"icon": "🚒"})
        resp = self._client(env["owner"]).get(self._url(env, view.id))
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "我的"

    def test_get_nonexistent_404_not_500(self, env):
        """Phase 3-A 发现的回归：retrieve() 缺省 for_write 曾致 500（TypeError）。"""
        import uuid as uuid_module

        resp = self._client(env["owner"]).get(self._url(env, uuid_module.uuid4()))
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    def test_get_system_view_by_member_200(self, env):
        seed_project_views(env["proj"])
        view = IssueView.objects.filter(project=env["proj"], is_system=True).first()
        assert self._client(env["other"]).get(self._url(env, view.id)).status_code == 200

    def test_patch_own_view(self, env):
        view = _mk_view(env, name="旧名")
        resp = self._client(env["owner"]).patch(self._url(env, view.id), {"name": "新名"}, format="json")
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "新名"

    def test_delete_view_204(self, env):
        view = _mk_view(env, name="待删")
        assert self._client(env["owner"]).delete(self._url(env, view.id)).status_code == 204
        assert not IssueView.objects.filter(pk=view.pk).exists()


# ─────────────────────────────────────────────────────────────────────
# 默认视图偏好（BR-10 · PATCH users/me/settings/ 合并语义）
# ─────────────────────────────────────────────────────────────────────
class TestDefaultViewPreference:
    def _patch(self, user, body: dict):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user=user)  # 顺带置 _dont_enforce_csrf_checks（Session 认证 CSRF 豁免）
        return client.patch("/api/v1/users/me/settings/", body, format="json")

    def test_set_read_and_clear(self, env):
        seed_project_views(env["proj"])
        view = IssueView.objects.filter(project=env["proj"], is_system=True).first()
        resp = self._patch(env["owner"], {"board.default_view_id": {str(env["proj"].id): str(view.id)}})
        assert resp.status_code == 200
        assert env["owner"].preferences["board.default_view_id"][str(env["proj"].id)] == str(view.id)
        # 清除默认 = 删该条目
        resp = self._patch(env["owner"], {"board.default_view_id": {str(env["proj"].id): None}})
        assert resp.status_code == 200
        assert str(env["proj"].id) not in env["owner"].preferences["board.default_view_id"]

    def test_merge_keeps_other_projects(self, env):
        seed_project_views(env["proj"])
        v1, v2 = list(IssueView.objects.filter(project=env["proj"], is_system=True)[:2])
        self._patch(env["owner"], {"board.default_view_id": {str(env["proj"].id): str(v1.id)}})
        # 换默认视图（同项目覆盖，逐键合并不整表替换）
        self._patch(env["owner"], {"board.default_view_id": {str(env["proj"].id): str(v2.id)}})
        assert env["owner"].preferences["board.default_view_id"][str(env["proj"].id)] == str(v2.id)

    def test_unknown_preference_key_rejected(self, env):
        resp = self._patch(env["owner"], {"theme.dark": True})
        assert resp.status_code == 400

    def test_view_not_in_project_rejected(self, env):
        seed_project_views(env["proj"])
        view = IssueView.objects.filter(project=env["proj"], is_system=True).first()
        other_pid = "00000000-0000-0000-0000-000000000001"
        resp = self._patch(env["owner"], {"board.default_view_id": {other_pid: str(view.id)}})
        assert resp.status_code == 400
