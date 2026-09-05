"""TASK-011 全量 FilterCompiler 测试（T3-07，§5 UT 矩阵的服务层兑现）。

覆盖：结构上限（UT-01/02/20）、白名单探测免疫（UT-03/12）、操作符 × 类型
（UT-04）、值域（UT-05/06）、@me 双用户（UT-07）、相对日期边界、类型名占位符
（§4.1.1）、合并等价性（UT-09，随机 12 棵树）、cf/内置全操作符行为、三源 AND
（BR-12）、meta.applied（BR-17/UT-18）、prune 任务（BR-15/UT-19）、越权 404 回归。
HTTP 信封全矩阵归 sprint-3-flow.py（Phase 4）。
"""
from __future__ import annotations

import json
import random
import uuid as uuid_module
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from plane.app.filters.compiler import (
    BUILTIN_FIELD_PATHS,
    CompileContext,
    build_issue_queryset,
    resolved_applied,
    validate_dsl,
)
from plane.app.filters.compiler import (
    compile as compile_dsl,
)
from plane.base.exception import AppException
from plane.bgtasks.field_cleanup import prune_views_referencing_field
from plane.db.models import (
    CustomFieldDefinition,
    Issue,
    IssueAssignee,
    IssueLabel,
    IssueLink,
    IssueType,
    IssueView,
    Label,
    Project,
    ProjectMember,
    ProjectRole,
    State,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.db.seeds.project_states import seed_project_states
from plane.utils.exceptions import CustomFieldValidationError

pytestmark = pytest.mark.django_db

OPTS_SEVERITY = [
    {"label": "致命", "value": "critical", "color": "#DC2626", "sort_order": 1},
    {"label": "严重", "value": "major", "color": "#F59E0B", "sort_order": 2},
    {"label": "一般", "value": "minor", "color": "#3B82F6", "sort_order": 3},
]
OPTS_VERSIONS = [
    {"label": "v1", "value": "v1", "color": "#999", "sort_order": 1},
    {"label": "v2", "value": "v2", "color": "#888", "sort_order": 2},
]


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(
        email="fc-owner@rabbit.dev", password="Rabbit123!", display_name="张三"
    )
    member = User.objects.create_user(
        email="fc-member@rabbit.dev", password="Rabbit123!", display_name="李四"
    )
    viewer = User.objects.create_user(
        email="fc-viewer@rabbit.dev", password="Rabbit123!", display_name="王五"
    )
    ws = Workspace.objects.create(name="W", slug=f"w-fc-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    proj = Project.objects.create(name="P", identifier="FC", workspace=ws, created_by=owner)
    from plane.db.models import WorkspaceRole

    for u, ws_role in ((owner, WorkspaceRole.OWNER), (member, WorkspaceRole.MEMBER), (viewer, WorkspaceRole.MEMBER)):
        WorkspaceMember.objects.create(workspace=ws, member=u, role=ws_role, created_by=owner)
    ProjectMember.objects.create(project=proj, member=member, role=ProjectRole.CONTRIBUTOR, created_by=owner)
    ProjectMember.objects.create(project=proj, member=viewer, role=ProjectRole.VIEWER, created_by=owner)
    seed_project_states(proj)
    types = {
        "requirement": IssueType.objects.create(workspace=ws, name="需求", is_default=True, created_by=owner),
        "bug": IssueType.objects.create(workspace=ws, name="缺陷", is_default=False, created_by=owner),
        "test": IssueType.objects.create(workspace=ws, name="测试", is_default=False, created_by=owner),
    }
    todo = State.objects.get(project=proj, group=State.Group.UNSTARTED)
    doing = State.objects.get(project=proj, group=State.Group.STARTED)
    done = State.objects.get(project=proj, group=State.Group.COMPLETED)
    label = Label.objects.create(project=proj, name="线上", color="#DC2626", created_by=owner)

    CustomFieldDefinition.objects.create(
        workspace=ws, project=proj, name="严重等级", field_key="cf_severity",
        field_type="select", options=OPTS_SEVERITY, created_by=owner,
    )
    CustomFieldDefinition.objects.create(
        workspace=ws, project=proj, name="影响版本", field_key="cf_versions",
        field_type="multi_select", options=OPTS_VERSIONS, created_by=owner,
    )
    CustomFieldDefinition.objects.create(
        workspace=ws, project=proj, name="点数", field_key="cf_points",
        field_type="number", created_by=owner,
    )
    CustomFieldDefinition.objects.create(
        workspace=ws, project=proj, name="评审日期", field_key="cf_due",
        field_type="date", created_by=owner,
    )
    CustomFieldDefinition.objects.create(
        workspace=ws, project=proj, name="金额", field_key="cf_price",
        field_type="currency", created_by=owner,
    )
    CustomFieldDefinition.objects.create(
        workspace=ws, project=proj, name="是否回归", field_key="cf_flag",
        field_type="checkbox", created_by=owner,
    )
    CustomFieldDefinition.objects.create(
        workspace=ws, project=proj, name="负责人", field_key="cf_owner",
        field_type="member", created_by=owner,
    )
    CustomFieldDefinition.objects.create(
        workspace=ws, project=proj, name="备注", field_key="cf_note",
        field_type="text", created_by=owner,
    )

    today = timezone.localdate()
    seq = iter(range(1, 100))

    def mk(name, *, state=todo, pri="none", assignees=(), cf=None, target=None, est=None,
           itype=None, creator=None):
        issue = Issue.objects.create(
            name=name, project=proj, state=state, priority=pri, issue_type=itype,
            sequence_id=next(seq), sort_order=next(seq) * 100,
            created_by=creator or owner, target_date=target, estimate_minutes=est,
            custom_fields=cf or {},
        )
        for a in assignees:
            IssueAssignee.objects.create(issue=issue, assignee=a, created_by=owner)
        return issue

    a = mk(
        "支付失败", state=todo, pri="urgent", assignees=[owner], target=today, est=60,
        itype=types["requirement"],
        cf={"cf_severity": "critical", "cf_versions": ["v1"], "cf_points": 3,
            "cf_due": today.isoformat(), "cf_price": {"amount": 100, "currency": "CNY"},
            "cf_flag": True, "cf_owner": str(member.id), "cf_note": "支付失败"},
    )
    b = mk(
        "登录崩溃", state=doing, pri="high", assignees=[member], target=today - timedelta(days=1), est=120,
        itype=types["bug"],
        cf={"cf_severity": "major", "cf_versions": ["v1", "v2"], "cf_points": 10,
            "cf_due": (today - timedelta(days=1)).isoformat(),
            "cf_price": {"amount": 500, "currency": "CNY"}, "cf_flag": False},
    )
    c = mk("测试用例", state=todo, pri="none", target=today + timedelta(days=30), itype=types["test"], creator=member)
    d = mk(
        "需求澄清", state=done, pri="medium", assignees=[owner, member], target=today + timedelta(days=5), est=240,
        itype=types["requirement"],
        cf={"cf_severity": "minor", "cf_versions": ["v2"], "cf_points": 7,
            "cf_due": (today + timedelta(days=5)).isoformat(),
            "cf_price": {"amount": 1000, "currency": "CNY"}, "cf_flag": True,
            "cf_owner": str(member.id), "cf_note": "x"},
    )
    IssueLabel.objects.create(issue=a, label=label, created_by=owner)
    # A 被 C 阻塞（C 未完成 → A._is_blocked = True；与 ?blocked=true 同向同语义）
    IssueLink.objects.create(issue=a, related_issue=c, relation_type="is_blocked_by", created_by=owner)

    return {
        "owner": owner, "member": member, "viewer": viewer, "ws": ws, "proj": proj,
        "todo": todo, "doing": doing, "done": done, "types": types, "label": label,
        "issues": {"A": a, "B": b, "C": c, "D": d}, "today": today,
    }


def _names(qs) -> set[str]:
    return set(qs.values_list("name", flat=True))


def _ids(tree, env, *, user=None, optimize=True):
    """单树编译 + 项目域 queryset → 命中名集合（merge 等价性与行为断言共用）。"""
    ctx = CompileContext.build(project=env["proj"], user=user or env["owner"])
    qs = build_issue_queryset(project=env["proj"], user=user or env["owner"], url_filters=tree)
    # build_issue_queryset 恒走 optimize=True；等价性对照需直接 compile 两遍
    if not optimize:
        q = compile_dsl(tree, ctx, optimize=False)
        qs = build_issue_queryset(project=env["proj"], user=user or env["owner"]).filter(q)
    return _names(qs)


def _cond(field, op, value=None):
    c = {"field": field, "operator": op}
    if value is not None or op in ("is_empty", "is_not_empty"):
        c["value"] = value
    return c


def _tree(*conditions, op="AND"):
    return {"op": op, "conditions": list(conditions)}


# ─────────────────────────────────────────────────────────────────────
# UT-01/02/20：结构上限
# ─────────────────────────────────────────────────────────────────────
class TestStructureLimits:
    def _raises(self, env, tree):
        with pytest.raises(AppException) as ei:
            validate_dsl(tree, project=env["proj"], user=env["owner"])
        assert ei.value.error_code == "VALIDATION_INVALID_PARAM"
        return ei.value

    def test_depth_4_rejected(self, env):
        tree = _tree(
            _cond("priority", "in", ["high"]),
        )
        for _ in range(3):
            tree = _tree(tree)
        exc = self._raises(env, tree)
        assert "嵌套层级" in exc.extra_details[0]["message"]

    def test_depth_3_allowed(self, env):
        tree = _tree(  # AND(1) > OR(2) > AND(3) > 条件 —— 恰为满嵌套形态（§1.2）
            _tree(
                _tree(_cond("name", "contains", "x"), op="AND"),
                op="OR",
            ),
        )
        validate_dsl(tree, project=env["proj"], user=env["owner"])

    def test_21_conditions_rejected(self, env):
        self._raises(env, _tree(*[_cond("priority", "in", ["high"])] * 21))

    def test_20_conditions_allowed(self, env):
        validate_dsl(_tree(*[_cond("priority", "in", ["high"])] * 20), project=env["proj"], user=env["owner"])

    def test_51_values_rejected(self, env):
        self._raises(env, _tree(_cond("priority", "in", [f"p{i}" for i in range(51)])))

    def test_bad_op_and_shape_rejected(self, env):
        self._raises(env, {"op": "XOR", "conditions": [_cond("priority", "in", ["high"])]})
        self._raises(env, {"op": "AND", "conditions": "not-a-list"})
        self._raises(env, {"op": "AND", "conditions": [{"field": "priority"}]})
        self._raises(env, ["not", "a", "dict"])

    def test_empty_tree_is_noop(self, env):
        validate_dsl({}, project=env["proj"], user=env["owner"])
        assert compile_dsl({}, CompileContext.build(project=env["proj"], user=env["owner"])) is not None


# ─────────────────────────────────────────────────────────────────────
# UT-03/12：白名单探测免疫
# ─────────────────────────────────────────────────────────────────────
class TestWhitelist:
    @pytest.mark.parametrize("probe", [
        "project__workspace__owner__password",
        "project__owner",
        "password",
        "sort_order",
        "cf_ghost",
    ])
    def test_probe_fields_rejected(self, env, probe):
        with pytest.raises(AppException) as ei:
            validate_dsl(_tree(_cond(probe, "in", ["x"])), project=env["proj"], user=env["owner"])
        assert ei.value.error_code == "VALIDATION_INVALID_PARAM"
        assert probe in ei.value.extra_details[0]["message"]

    def test_builtin_whitelist_shape(self):
        assert set(BUILTIN_FIELD_PATHS) == {
            "name", "state", "state.group", "issue_type", "priority", "assignees",
            "labels", "created_by", "start_date", "target_date", "created_at",
            "estimate", "sequence_id", "parent", "blocked",
        }
        assert BUILTIN_FIELD_PATHS["assignees"] == {"path": "assignees__id", "type": "member_multi"}
        assert BUILTIN_FIELD_PATHS["blocked"] == {"path": "_is_blocked", "type": "checkbox"}
        assert BUILTIN_FIELD_PATHS["state.group"] == {"path": "state__group", "type": "select"}

    def test_inactive_cf_rejected(self, env):
        CustomFieldDefinition.objects.create(
            workspace=env["ws"], project=env["proj"], name="停用", field_key="cf_off",
            field_type="text", is_active=False, created_by=env["owner"],
        )
        with pytest.raises(AppException) as ei:
            validate_dsl(_tree(_cond("cf_off", "eq", "x")), project=env["proj"], user=env["owner"])
        assert ei.value.error_code == "VALIDATION_INVALID_PARAM"


# ─────────────────────────────────────────────────────────────────────
# UT-04/05/06：操作符 × 类型 + 值域
# ─────────────────────────────────────────────────────────────────────
class TestOperatorAndValueDomain:
    def _raises(self, env, tree, code):
        with pytest.raises(AppException) as ei:
            validate_dsl(tree, project=env["proj"], user=env["owner"])
        assert ei.value.error_code == code, f"期望 {code}，实际 {ei.value.error_code}"
        return ei.value

    def test_checkbox_gt_rejected(self, env):  # UT-04
        self._raises(env, _tree(_cond("blocked", "gt", 1)), "VALIDATION_INVALID_PARAM")
        self._raises(env, _tree(_cond("cf_flag", "gt", 1)), "VALIDATION_INVALID_PARAM")

    def test_priority_operator_narrowed(self, env):
        self._raises(env, _tree(_cond("priority", "is_empty")), "VALIDATION_INVALID_PARAM")
        self._raises(env, _tree(_cond("priority", "eq", "high")), "VALIDATION_INVALID_PARAM")
        validate_dsl(_tree(_cond("priority", "not_in", ["high"])), project=env["proj"], user=env["owner"])

    def test_text_in_rejected(self, env):
        self._raises(env, _tree(_cond("name", "in", ["x"])), "VALIDATION_INVALID_PARAM")

    def test_select_contains_rejected(self, env):
        self._raises(env, _tree(_cond("cf_severity", "contains", "crit")), "VALIDATION_INVALID_PARAM")

    def test_state_group_enum_domain(self, env):
        self._raises(env, _tree(_cond("state.group", "in", ["unknown"])), "VALIDATION_INVALID_PARAM")
        self._raises(env, _tree(_cond("priority", "in", ["super"])), "VALIDATION_INVALID_PARAM")

    def test_cf_select_value_domain(self, env):  # UT-05
        with pytest.raises(CustomFieldValidationError):
            validate_dsl(_tree(_cond("cf_severity", "in", ["blocker"])), project=env["proj"], user=env["owner"])

    def test_cf_number_and_date_domain(self, env):
        with pytest.raises(CustomFieldValidationError):
            validate_dsl(_tree(_cond("cf_points", "eq", "abc")), project=env["proj"], user=env["owner"])
        with pytest.raises(CustomFieldValidationError):
            validate_dsl(_tree(_cond("cf_due", "eq", "2026/09/01")), project=env["proj"], user=env["owner"])

    def test_member_not_project_member(self, env):  # UT-06
        stranger = uuid_module.uuid4()
        with pytest.raises(AppException):
            validate_dsl(_tree(_cond("assignees", "in", [str(stranger)])), project=env["proj"], user=env["owner"])
        with pytest.raises(CustomFieldValidationError):
            validate_dsl(_tree(_cond("cf_owner", "in", [str(stranger)])), project=env["proj"], user=env["owner"])

    def test_member_me_placeholder_ok(self, env):
        validate_dsl(_tree(_cond("assignees", "in", ["@me"])), project=env["proj"], user=env["viewer"])

    def test_state_must_be_uuid(self, env):
        self._raises(env, _tree(_cond("state", "in", ["not-uuid"])), "VALIDATION_INVALID_PARAM")


# ─────────────────────────────────────────────────────────────────────
# 占位符（BR-05 / §4.1.1 / UT-07/08）
# ─────────────────────────────────────────────────────────────────────
class TestPlaceholders:
    def test_me_resolves_per_user(self, env):  # UT-07：同一 DSL，不同用户各自解析
        tree = _tree(_cond("assignees", "in", ["@me"]))
        assert _ids(tree, env, user=env["owner"]) == {"支付失败", "需求澄清"}
        assert _ids(tree, env, user=env["member"]) == {"登录崩溃", "需求澄清"}
        # DSL 原样持久化：resolved_applied 只在回显里展开
        ctx = CompileContext.build(project=env["proj"], user=env["owner"])
        assert resolved_applied(tree, ctx)["resolved_placeholders"]["@me"] == "@张三"
        assert tree["conditions"][0]["value"] == ["@me"]

    def test_relative_date_bounds(self, env):
        from plane.app.filters.compiler import _resolve_relative_date

        today = timezone.localdate()
        assert _resolve_relative_date("today") == (today, today)
        monday = today - timedelta(days=today.weekday())
        assert _resolve_relative_date("this_week") == (monday, monday + timedelta(days=6))
        first = today.replace(day=1)
        if first.month == 12:
            month_end = first.replace(year=first.year + 1, month=1) - timedelta(days=1)
        else:
            month_end = first.replace(month=first.month + 1) - timedelta(days=1)
        assert _resolve_relative_date("this_month") == (first, month_end)
        assert _resolve_relative_date("overdue") == (None, today)
        assert _resolve_relative_date("next_7_days") == (today, today + timedelta(days=7))
        assert _resolve_relative_date("next_90_days") == (today, today + timedelta(days=90))
        with pytest.raises(AppException):
            _resolve_relative_date("next_91_days")
        with pytest.raises(AppException):
            _resolve_relative_date("next_0_days")

    def test_this_week_queryset_bounds(self, env):
        # 边界用计算值锚定（服务器本地周口径——用户时区口径为 P3 登记项）
        monday = env["today"] - timedelta(days=env["today"].weekday())
        sunday = monday + timedelta(days=6)
        Issue.objects.filter(name="登录崩溃").update(target_date=monday)  # 周一恒在周内
        Issue.objects.filter(name="需求澄清").update(target_date=sunday + timedelta(days=1))  # 恒出周
        assert _ids(_tree(_cond("target_date", "between", ["this_week"])), env) == {"支付失败", "登录崩溃"}

    def test_next_n_days_queryset_bounds(self, env):
        within = _tree(_cond("target_date", "between", ["next_7_days"]))
        assert _ids(within, env) == {"支付失败", "需求澄清"}  # today / today+5
        assert _ids(_tree(_cond("target_date", "between", ["next_30_days"])), env) == {
            "支付失败", "需求澄清", "测试用例",
        }
        too_deep = _tree(_cond("target_date", "between", ["next_91_days"]))
        with pytest.raises(AppException):
            validate_dsl(too_deep, project=env["proj"], user=env["owner"])

    def test_type_name_placeholders(self, env):  # §4.1.1
        assert _ids(_tree(_cond("issue_type", "in", ["__bug__"])), env) == {"登录崩溃"}
        assert _ids(_tree(_cond("issue_type", "in", ["__requirement__"])), env) == {"支付失败", "需求澄清"}
        assert _ids(_tree(_cond("issue_type", "in", [str(env["types"]["test"].id)])), env) == {"测试用例"}
        # 无匹配类型名 → 条件命中零行（非放宽）
        assert _ids(_tree(_cond("issue_type", "in", ["__ghost__"])), env) == set()
        echo = resolved_applied(
            _tree(_cond("issue_type", "in", ["__bug__"])),
            CompileContext.build(project=env["proj"], user=env["owner"]),
        )
        assert echo["resolved_placeholders"]["__bug__"] == "缺陷"


# ─────────────────────────────────────────────────────────────────────
# cf 全操作符行为（造数后 queryset 计数断言）
# ─────────────────────────────────────────────────────────────────────
class TestCustomFieldOperators:
    def test_select_ops(self, env):
        assert _ids(_tree(_cond("cf_severity", "in", ["critical"])), env) == {"支付失败"}
        # 否定操作符含未填行（键缺失不匹配任何正值 → 「非 critical」含未填）
        assert _ids(_tree(_cond("cf_severity", "not_in", ["critical"])), env) == {"登录崩溃", "测试用例", "需求澄清"}
        assert _ids(_tree(_cond("cf_severity", "is_empty")), env) == {"测试用例"}
        assert _ids(_tree(_cond("cf_severity", "is_not_empty")), env) == {"支付失败", "登录崩溃", "需求澄清"}

    def test_multi_select_ops(self, env):
        assert _ids(_tree(_cond("cf_versions", "contains_any", ["v1"])), env) == {"支付失败", "登录崩溃"}
        assert _ids(_tree(_cond("cf_versions", "contains_any", ["v2"])), env) == {"登录崩溃", "需求澄清"}
        assert _ids(_tree(_cond("cf_versions", "contains_all", ["v1", "v2"])), env) == {"登录崩溃"}
        assert _ids(_tree(_cond("cf_versions", "not_contains", ["v1"])), env) == {"测试用例", "需求澄清"}
        assert _ids(_tree(_cond("cf_versions", "is_empty")), env) == {"测试用例"}

    def test_number_ops(self, env):
        assert _ids(_tree(_cond("cf_points", "eq", 10)), env) == {"登录崩溃"}
        assert _ids(_tree(_cond("cf_points", "neq", 10)), env) == {"支付失败", "测试用例", "需求澄清"}
        assert _ids(_tree(_cond("cf_points", "gt", 5)), env) == {"登录崩溃", "需求澄清"}
        assert _ids(_tree(_cond("cf_points", "gte", 7)), env) == {"登录崩溃", "需求澄清"}
        assert _ids(_tree(_cond("cf_points", "lt", 10)), env) == {"支付失败", "需求澄清"}
        assert _ids(_tree(_cond("cf_points", "lte", 7)), env) == {"支付失败", "需求澄清"}
        assert _ids(_tree(_cond("cf_points", "between", [4, 10])), env) == {"登录崩溃", "需求澄清"}
        assert _ids(_tree(_cond("cf_points", "is_empty")), env) == {"测试用例"}

    def test_date_ops(self, env):
        t = env["today"]
        assert _ids(_tree(_cond("cf_due", "eq", t.isoformat())), env) == {"支付失败"}
        assert _ids(_tree(_cond("cf_due", "before", t.isoformat())), env) == {"登录崩溃"}
        assert _ids(_tree(_cond("cf_due", "after", t.isoformat())), env) == {"需求澄清"}
        assert _ids(
            _tree(_cond("cf_due", "between", [(t - timedelta(days=1)).isoformat(), t.isoformat()])), env
        ) == {"支付失败", "登录崩溃"}
        # this_week 边界按服务器本地周动态锚定（用户时区口径为 P3 登记项）
        monday = t - timedelta(days=t.weekday())
        sunday = monday + timedelta(days=6)
        dues = {
            "支付失败": t,
            "登录崩溃": t - timedelta(days=1),
            "需求澄清": t + timedelta(days=5),
        }
        expected = {name for name, d in dues.items() if monday <= d <= sunday}
        assert _ids(_tree(_cond("cf_due", "between", ["this_week"])), env) == expected
        assert _ids(_tree(_cond("cf_due", "is_empty")), env) == {"测试用例"}

    def test_checkbox_currency_text_member_ops(self, env):
        assert _ids(_tree(_cond("cf_flag", "eq", True)), env) == {"支付失败", "需求澄清"}
        assert _ids(_tree(_cond("cf_flag", "eq", False)), env) == {"登录崩溃"}
        assert _ids(_tree(_cond("cf_price", "gte", 500)), env) == {"登录崩溃", "需求澄清"}
        assert _ids(_tree(_cond("cf_price", "lte", 500)), env) == {"支付失败", "登录崩溃"}
        assert _ids(_tree(_cond("cf_price", "between", [100, 500])), env) == {"支付失败", "登录崩溃"}
        assert _ids(_tree(_cond("cf_note", "contains", "支付")), env) == {"支付失败"}
        assert _ids(_tree(_cond("cf_note", "eq", "支付失败")), env) == {"支付失败"}
        assert _ids(_tree(_cond("cf_note", "is_empty")), env) == {"登录崩溃", "测试用例"}
        assert _ids(_tree(_cond("cf_note", "is_not_empty")), env) == {"支付失败", "需求澄清"}
        assert _ids(_tree(_cond("cf_owner", "in", ["@me"])), env, user=env["member"]) == {"支付失败", "需求澄清"}
        assert _ids(_tree(_cond("cf_owner", "in", ["@me"])), env, user=env["owner"]) == set()


# ─────────────────────────────────────────────────────────────────────
# 内置字段操作符行为（含 blocked 注解键 / assignees is_empty）
# ─────────────────────────────────────────────────────────────────────
class TestBuiltinOperators:
    def test_name_text_ops(self, env):
        assert _ids(_tree(_cond("name", "contains", "支付")), env) == {"支付失败"}
        assert _ids(_tree(_cond("name", "eq", "支付失败")), env) == {"支付失败"}
        assert _ids(_tree(_cond("name", "neq", "支付失败")), env) == {"登录崩溃", "测试用例", "需求澄清"}

    def test_state_and_group_ops(self, env):
        assert _ids(_tree(_cond("state", "in", [str(env["todo"].id)])), env) == {"支付失败", "测试用例"}
        assert _ids(_tree(_cond("state.group", "in", ["unstarted"])), env) == {"支付失败", "测试用例"}
        assert _ids(_tree(_cond("state.group", "not_in", ["unstarted"])), env) == {"登录崩溃", "需求澄清"}

    def test_priority_ops(self, env):
        assert _ids(_tree(_cond("priority", "in", ["urgent"])), env) == {"支付失败"}
        assert _ids(_tree(_cond("priority", "not_in", ["urgent"])), env) == {"登录崩溃", "测试用例", "需求澄清"}

    def test_assignees_ops(self, env):
        assert _ids(_tree(_cond("assignees", "in", ["@me"])), env, user=env["owner"]) == {"支付失败", "需求澄清"}
        assert _ids(_tree(_cond("assignees", "not_in", ["@me"])), env, user=env["owner"]) == {"登录崩溃", "测试用例"}
        assert _ids(_tree(_cond("assignees", "is_empty")), env) == {"测试用例"}
        assert _ids(
            _tree(_cond("assignees", "contains_all", [str(env["owner"].id), str(env["member"].id)])), env
        ) == {"需求澄清"}

    def test_labels_ops(self, env):
        assert _ids(_tree(_cond("labels", "in", [str(env["label"].id)])), env) == {"支付失败"}
        assert _ids(_tree(_cond("labels", "is_empty")), env) == {"登录崩溃", "测试用例", "需求澄清"}

    def test_created_by_and_dates(self, env):
        assert _ids(_tree(_cond("created_by", "in", [str(env["member"].id)])), env) == {"测试用例"}
        t = env["today"]
        assert _ids(_tree(_cond("target_date", "eq", t.isoformat())), env) == {"支付失败"}
        assert _ids(_tree(_cond("target_date", "before", t.isoformat())), env) == {"登录崩溃"}
        assert _ids(_tree(_cond("target_date", "after", t.isoformat())), env) == {"测试用例", "需求澄清"}
        week_ahead = [t.isoformat(), (t + timedelta(days=7)).isoformat()]
        assert _ids(_tree(_cond("target_date", "between", week_ahead)), env) == {"支付失败", "需求澄清"}
        assert _ids(_tree(_cond("target_date", "between", ["overdue"])), env) == {"支付失败", "登录崩溃"}
        assert _ids(_tree(_cond("target_date", "is_empty")), env) == set()

    def test_estimate_ops(self, env):
        assert _ids(_tree(_cond("estimate", "gte", 120)), env) == {"登录崩溃", "需求澄清"}
        assert _ids(_tree(_cond("estimate", "between", [60, 240])), env) == {"支付失败", "登录崩溃", "需求澄清"}
        assert _ids(_tree(_cond("estimate", "is_empty")), env) == {"测试用例"}

    def test_blocked_annotation_key(self, env):
        assert _ids(_tree(_cond("blocked", "eq", True)), env) == {"支付失败"}
        assert _ids(_tree(_cond("blocked", "eq", False)), env) == {"登录崩溃", "测试用例", "需求澄清"}

    def test_and_or_semantics(self, env):  # UT-10 黄金集口径
        tree = _tree(
            _cond("state.group", "in", ["unstarted", "started"]),
            _tree(
                _cond("cf_severity", "in", ["critical", "major"]),
                _cond("cf_versions", "contains_any", ["v1"]),
                op="OR",
            ),
        )
        assert _ids(tree, env) == {"支付失败", "登录崩溃"}


# ─────────────────────────────────────────────────────────────────────
# UT-09：等值合并等价性（随机 ≥10 棵树）
# ─────────────────────────────────────────────────────────────────────
class TestMergeEquivalence:
    def _random_tree(self, rng) -> dict:
        pool = [
            lambda: _cond("cf_severity", "in", [rng.choice(["critical", "major"])]),
            lambda: _cond("cf_points", "eq", rng.choice([3, 7, 10])),
            lambda: _cond("cf_flag", "eq", rng.choice([True, False])),
            lambda: _cond("cf_versions", "contains_any", [rng.choice(["v1", "v2"])]),
            lambda: _cond("priority", "in", [rng.choice(["urgent", "high", "medium"])]),
            lambda: _cond("state.group", "in", [rng.choice(["unstarted", "started"])]),
            lambda: _cond("assignees", "is_empty"),
        ]
        n = rng.randint(2, 5)

        def node(depth: int):
            if depth < 2 and rng.random() < 0.3:
                return _tree(*[node(depth + 1) for _ in range(rng.randint(1, 2))], op=rng.choice(["AND", "OR"]))
            return rng.choice(pool)()

        return _tree(*[node(0) for _ in range(n)], op=rng.choice(["AND", "OR"]))

    def test_random_trees_merge_equivalent(self, env):
        """UT-09：随机 12 棵树，逐条编译（optimize=False）与合并编译（True）结果一致。"""
        rng = random.Random(42)
        ctx = CompileContext.build(project=env["proj"], user=env["owner"])
        base = Issue.objects.filter(
            project=env["proj"], deleted_at__isnull=True, archived_at__isnull=True
        )
        for _ in range(12):
            tree = self._random_tree(rng)
            validate_dsl(tree, project=env["proj"], user=env["owner"])
            plain_ids = set(base.filter(compile_dsl(tree, ctx, optimize=False)).values_list("name", flat=True))
            merged_ids = set(base.filter(compile_dsl(tree, ctx, optimize=True)).values_list("name", flat=True))
            assert plain_ids == merged_ids, f"合并改变语义：{tree}"

    def test_merge_collapses_to_single_containment(self, env):
        ctx = CompileContext.build(project=env["proj"], user=env["owner"])
        tree = _tree(
            _cond("cf_severity", "eq", "critical"),
            _cond("cf_points", "eq", 3),
            _cond("cf_flag", "eq", True),
        )
        q = compile_dsl(tree, ctx)
        assert "custom_fields__contains" in str(q)
        assert _ids(tree, env) == {"支付失败"}
        # 同 key 重复等值不合并（保留矛盾语义 → 命中零行）
        tree2 = _tree(_cond("cf_severity", "eq", "critical"), _cond("cf_severity", "eq", "major"))
        assert "custom_fields__contains" not in str(compile_dsl(tree2, ctx))
        assert _ids(tree2, env) == set()
        # date eq（占位符敏感）不并入 @>
        tree3 = _tree(_cond("cf_due", "eq", env["today"].isoformat()), _cond("cf_points", "eq", 3))
        q3 = compile_dsl(tree3, ctx)
        assert "custom_fields__contains" not in str(q3)
        assert _ids(tree3, env) == {"支付失败"}


# ─────────────────────────────────────────────────────────────────────
# 三源 AND（BR-12 / UT-17）与 build_issue_queryset 层级
# ─────────────────────────────────────────────────────────────────────
class TestThreeSourceAnd:
    def test_build_issue_queryset_two_trees_and(self, env):
        qs = build_issue_queryset(
            project=env["proj"], user=env["owner"],
            view_filters=_tree(_cond("priority", "in", ["urgent", "high", "medium"])),
            url_filters=_tree(_cond("state.group", "in", ["unstarted", "started"])),
        )
        assert _names(qs) == {"支付失败", "登录崩溃"}

    def test_build_issue_queryset_extra_q_and(self, env):
        from django.db.models import Q

        qs = build_issue_queryset(
            project=env["proj"], user=env["owner"],
            view_filters=_tree(_cond("assignees", "in", ["@me"])),
            url_filters=_tree(_cond("cf_severity", "is_not_empty")),
            extra_q=Q(priority__in=["urgent"]),
        )
        assert _names(qs) == {"支付失败"}


class _Client:
    def __init__(self, user):
        self.c = APIClient()
        self.c.force_authenticate(user=user)

    def get(self, path, params=None):
        return self.c.get(path, params or {}, format="json")


class TestIssuesEndpoint:
    def _url(self, env, query=""):
        return f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/issues/{query}"

    def test_url_filters_source_filters_and_echoes(self, env):
        tree = _tree(_cond("assignees", "in", ["@me"]), _cond("cf_severity", "in", ["critical"]))
        resp = _Client(env["owner"]).get(self._url(env), {"filters": json.dumps(tree)})
        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["total_count"] == 1
        assert [r["name"] for r in body["data"]] == ["支付失败"]
        applied = body["meta"]["applied"]
        assert applied["filters"]["assignees"] == [str(env["owner"].id)]  # 解析后值
        assert applied["resolved_placeholders"]["@me"] == "@张三"
        assert applied["conditions_count"] == 2
        assert applied["groups_count"] == 1

    def test_url_filters_broken_json_400(self, env):
        resp = _Client(env["owner"]).get(self._url(env), {"filters": "{not-json"})
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "VALIDATION_INVALID_PARAM"
        assert "filters 不是合法 JSON" in body["error"]["message"]

    def test_url_filters_probe_400(self, env):
        resp = _Client(env["owner"]).get(
            self._url(env), {"filters": json.dumps(_tree(_cond("project__owner", "in", ["x"])))}
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_INVALID_PARAM"

    def test_no_filters_param_keeps_applied_shape(self, env):
        """回归红线：无 ?filters= 时 meta.applied 形态逐字节不变（仅 order_by 默认回显）。"""
        resp = _Client(env["owner"]).get(self._url(env))
        assert resp.status_code == 200
        applied = resp.json()["meta"]["applied"]
        assert applied == {"order_by": "-created_at"}
        assert "filters" not in applied and "resolved_placeholders" not in applied

    def test_three_sources_always_and(self, env):  # BR-12：view + filters + URL 平铺
        view = IssueView.objects.create(
            workspace=env["ws"], project=env["proj"], owner=env["owner"], name="中高优先",
            filters=_tree(_cond("priority", "in", ["urgent", "high", "medium"])),
        )
        tree = _tree(_cond("assignees", "in", ["@me"]))
        resp = _Client(env["owner"]).get(
            self._url(env),
            {"view_id": str(view.id), "filters": json.dumps(tree), "priority": "urgent,high"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["total_count"] == 1
        assert [r["name"] for r in body["data"]] == ["支付失败"]
        applied = body["meta"]["applied"]
        assert applied["priority"] == ["urgent", "high"]  # 平铺源
        assert applied["view"]["id"] == str(view.id)  # 视图源标识
        assert applied["filters"]["assignees"] == [str(env["owner"].id)]  # filters 源（解析后值）

    def test_nested_view_via_endpoint(self, env):
        view = IssueView.objects.create(
            workspace=env["ws"], project=env["proj"], owner=env["owner"], name="嵌套",
            filters=_tree(
                _cond("state.group", "in", ["unstarted", "started"]),
                _tree(_cond("cf_severity", "in", ["critical"]), _cond("priority", "in", ["high"]), op="OR"),
            ),
        )
        resp = _Client(env["owner"]).get(self._url(env), {"view_id": str(view.id)})
        assert resp.status_code == 200
        assert resp.json()["meta"]["total_count"] == 2

    def test_other_personal_view_404(self, env):
        """越权 view_id 存在性隐藏回归（BR-10 口径）。"""
        view = IssueView.objects.create(
            workspace=env["ws"], project=env["proj"], owner=env["member"], name="成员私有",
            filters=_tree(_cond("priority", "in", ["high"])),
        )
        assert _Client(env["viewer"]).get(self._url(env), {"view_id": str(view.id)}).status_code == 404
        assert _Client(env["member"]).get(self._url(env), {"view_id": str(view.id)}).status_code == 200


# ─────────────────────────────────────────────────────────────────────
# 视图保存端点（嵌套 201 + BR-17 meta.applied）
# ─────────────────────────────────────────────────────────────────────
class TestViewSaveEndpoint:
    def _url(self, env):
        return f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/views/"

    def test_nested_tree_saved_201_with_applied(self, env):
        tree = _tree(
            _cond("state.group", "in", ["unstarted", "started"]),
            _tree(_cond("cf_severity", "in", ["critical", "major"]), op="OR"),
            _cond("assignees", "in", ["@me"]),
        )
        resp = _Client(env["owner"]).c.post(
            self._url(env), {"name": "严重缺陷盯防", "access": "personal", "filters": tree}, format="json"
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["data"]["filters"] == tree  # DSL 原样持久化（占位符不展开）
        applied = body["meta"]["applied"]
        assert applied["view"]["name"] == "严重缺陷盯防"
        assert applied["resolved_placeholders"]["@me"] == "@张三"
        assert applied["conditions_count"] == 3
        assert applied["groups_count"] == 2  # 根 AND + 内层 OR

    def test_invalid_dsl_rejected_400(self, env):
        tree = _tree(_cond("blocked", "gt", 1))
        resp = _Client(env["owner"]).c.post(self._url(env), {"name": "非法", "filters": tree}, format="json")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_INVALID_PARAM"

    def test_cf_value_domain_rejected_400(self, env):
        tree = _tree(_cond("cf_severity", "in", ["blocker"]))
        resp = _Client(env["owner"]).c.post(self._url(env), {"name": "非法值", "filters": tree}, format="json")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_CUSTOM_FIELD_INVALID"


# ─────────────────────────────────────────────────────────────────────
# BR-15 / UT-19：prune_views_referencing_field（TASK-011 激活）
# ─────────────────────────────────────────────────────────────────────
class TestPruneTask:
    def test_prunes_filters_display_props_card_fields(self, env):
        definition = CustomFieldDefinition.objects.get(
            workspace=env["ws"], project=env["proj"], field_key="cf_severity"
        )
        view = IssueView.objects.create(
            workspace=env["ws"], project=env["proj"], owner=env["owner"], name="引用视图",
            filters=_tree(
                _cond("cf_severity", "in", ["critical"]),
                _tree(_cond("cf_severity", "eq", "major"), _cond("priority", "in", ["high"]), op="OR"),
            ),
            display_props={
                "group_by": "cf_severity", "sub_group_by": "cf_severity", "order_by": "-cf_severity",
                "columns": ["name", "cf_severity"], "card_fields": {"cf_severity": True, "priority": False},
            },
        )
        affected = prune_views_referencing_field(str(definition.id))
        assert affected == 1
        view.refresh_from_db()
        # filters：嵌套递归剔除，组内保留其余条件（降级而非报错）
        assert view.filters == {"op": "AND", "conditions": [
            {"op": "OR", "conditions": [{"field": "priority", "operator": "in", "value": ["high"]}]},
        ]}
        props = view.display_props
        assert props["group_by"] == "state_id"  # 回退默认分组维度
        assert props["sub_group_by"] is None and props["order_by"] is None
        assert props["columns"] == ["name"]
        assert props["card_fields"] == {"priority": False}
        # 幂等：二跑无变更
        assert prune_views_referencing_field(str(definition.id)) == 0

    def test_global_field_prunes_across_projects(self, env):
        global_def = CustomFieldDefinition.objects.create(
            workspace=env["ws"], project=None, name="全局", field_key="cf_global_rank",
            field_type="number", created_by=env["owner"],
        )
        proj2 = Project.objects.create(name="P2", identifier="F2", workspace=env["ws"], created_by=env["owner"])
        v1 = IssueView.objects.create(
            workspace=env["ws"], project=env["proj"], owner=env["owner"], name="主项目",
            filters=_tree(_cond("cf_global_rank", "gte", 1)),
        )
        v2 = IssueView.objects.create(
            workspace=env["ws"], project=proj2, owner=env["owner"], name="次项目",
            filters=_tree(_cond("cf_global_rank", "gte", 2)),
        )
        assert prune_views_referencing_field(str(global_def.id)) == 2
        v1.refresh_from_db()
        v2.refresh_from_db()
        assert v1.filters["conditions"] == []
        assert v2.filters["conditions"] == []

    def test_view_without_references_untouched(self, env):
        definition = CustomFieldDefinition.objects.get(
            workspace=env["ws"], project=env["proj"], field_key="cf_points"
        )
        IssueView.objects.create(
            workspace=env["ws"], project=env["proj"], owner=env["owner"], name="无关",
            filters=_tree(_cond("priority", "in", ["high"])),
        )
        assert prune_views_referencing_field(str(definition.id)) == 0
