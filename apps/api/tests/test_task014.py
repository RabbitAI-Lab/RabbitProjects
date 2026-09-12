"""公式/级联/跨项目关联字段测试（TASK-014，P4 R3 门禁）。

覆盖 §5.1 核心口径（编号见 docstring）：DSL 安全面与复杂度（BR-03）、
环检测（BR-02）、失效传播（BR-04 最终一致 + 读时兜底）、错误值显式
（BR-05）、级联树校验（BR-06/09）、关联越域（BR-07）与端点权限。
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from plane.db.models import CustomFieldDefinition, Issue, Project, User, Workspace, WorkspaceMember
from plane.db.models.roles import WorkspaceRole
from plane.formula.derived import (
    affected_formulas,
    build_dep_graph,
    detect_cycle,
    recompute_issue,
)
from plane.formula.dsl import (
    EvalContext,
    FormulaComplexityError,
    FormulaRuntimeError,
    FormulaSyntaxError,
    evaluate,
    infer_result_type,
    parse,
)

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="fx-owner@rabbit.dev", password="Rabbit123!", display_name="主")
    ws = Workspace.objects.create(name="FX", slug=f"w-fx-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    WorkspaceMember.objects.create(workspace=ws, member=owner, role=WorkspaceRole.OWNER, created_by=owner)
    proj = Project.objects.create(workspace=ws, name="P1", identifier="FX1", created_by=owner)
    CustomFieldDefinition.objects.create(
        workspace=ws, name="实际工时", field_key="cf_actual", field_type="number", created_by=owner
    )
    CustomFieldDefinition.objects.create(
        workspace=ws, name="预估工时", field_key="cf_planned", field_type="number", created_by=owner
    )
    issue = Issue.objects.create(
        project=proj,
        name="公式目标",
        sequence_id=1,
        custom_fields={"cf_actual": 120, "cf_planned": 90},
        created_by=owner,
    )
    return {"owner": owner, "ws": ws, "proj": proj, "issue": issue}


def _c(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user)
    return c


def _ctx(env):
    cf = dict(env["issue"].custom_fields or {})
    cf.pop("_meta", None)
    return EvalContext(props={"name": env["issue"].name}, custom=cf)


# ── DSL 安全面 ─────────────────────────────────────────────────────


def test_dsl_arithmetic_and_condition(env):
    assert evaluate(parse("subtract(prop_cf('cf_actual'), prop_cf('cf_planned'))"), _ctx(env)) == 30
    assert evaluate(parse("round(prop_cf('cf_planned')/30, 1)"), _ctx(env)) == 3.0
    assert evaluate(parse("if(gt(prop_cf('cf_actual'), 100), '超', '正常')"), _ctx(env)) == "超"


def test_dsl_whitelist_and_complexity():
    for evil in ("__import__('os')", "eval('1')", "lambda: 1", "open('/etc')"):
        with pytest.raises(FormulaSyntaxError):
            parse(evil)
    with pytest.raises(FormulaComplexityError):
        parse("+".join(["1"] * 201))  # 节点 > 200
    with pytest.raises(FormulaComplexityError):
        parse("*".join([f"prop_cf('cf_{i}')" for i in range(21)]))  # 引用 > 20
    with pytest.raises(FormulaComplexityError):
        parse("-" * 11 + "1")  # 一元负链深度 > 10


def test_dsl_type_inference():
    assert infer_result_type(parse("prop_cf('a') + 1")) == "number"
    assert infer_result_type(parse("concat('a', 'b')")) == "text"
    with pytest.raises(FormulaSyntaxError):
        infer_result_type(parse("if(gt(prop_cf('a'),1), 1, 'x')"))  # 混合分支拒绝


def test_dsl_runtime_errors():
    with pytest.raises(FormulaRuntimeError):
        evaluate(parse("1/prop_cf('z')"), EvalContext(props={}, custom={"cf_z": 0}))  # 除零
    with pytest.raises(FormulaRuntimeError):
        evaluate(parse("upper(prop_cf('n')) + 1"), EvalContext(props={}, custom={"cf_n": 5}))  # text+number


# ── 依赖图 ─────────────────────────────────────────────────────────


def test_dependency_cycle_detection(env):
    CustomFieldDefinition.objects.create(
        workspace=env["ws"],
        name="F1",
        field_key="cf_f1",
        field_type="formula",
        formula="prop_cf('cf_f2') + prop_cf('cf_actual')",
        created_by=env["owner"],
    )
    CustomFieldDefinition.objects.create(
        workspace=env["ws"],
        name="F2",
        field_key="cf_f2",
        field_type="formula",
        formula="prop_cf('cf_f1') + prop_cf('cf_planned')",
        created_by=env["owner"],
    )
    graph = build_dep_graph(env["ws"].id)
    assert detect_cycle(graph)  # 环检出
    assert affected_formulas(graph, {"cf_actual"}) >= {"cf_f1", "cf_f2"}
    assert detect_cycle({"cf_a": {"cf_b"}, "cf_b": set()}) is None


# ── 失效传播与重算 ─────────────────────────────────────────────────


def test_recompute_materializes_value(env):
    CustomFieldDefinition.objects.create(
        workspace=env["ws"],
        name="偏差",
        field_key="cf_dev",
        field_type="formula",
        formula="subtract(prop_cf('cf_actual'), prop_cf('cf_planned'))",
        created_by=env["owner"],
    )
    result = recompute_issue(env["issue"].id)
    assert result["cf_dev"] == {"ok": True, "value": 30}
    env["issue"].refresh_from_db()
    assert env["issue"].custom_fields["cf_dev"] == 30  # 物化落库


def test_recompute_error_explicit_no_key(env):
    CustomFieldDefinition.objects.create(
        workspace=env["ws"],
        name="坏公式",
        field_key="cf_bad",
        field_type="formula",
        formula="prop_cf('cf_actual') / prop_cf('cf_missing')",
        created_by=env["owner"],
    )
    result = recompute_issue(env["issue"].id)
    assert result["cf_bad"]["ok"] is False  # 错误显式
    env["issue"].refresh_from_db()
    assert "cf_bad" not in env["issue"].custom_fields  # 空值不落键（BR-05）
    meta = env["issue"].custom_fields.get("_meta", {})
    assert "期望 number" in meta["formula"]["cf_bad"]["error"]  # null 类型错显式


# ── 端点 ───────────────────────────────────────────────────────────


def test_formula_field_create_and_cycle_rejected(env):
    c = _c(env["owner"])
    base = f"/api/v1/workspaces/{env['ws'].slug}/issue-properties"
    r = c.post(
        f"{base}/formula/",
        {"name": "偏差", "field_key": "cf_dev", "formula": "subtract(prop_cf('cf_actual'), prop_cf('cf_planned'))"},
        format="json",
    )
    assert r.status_code == 201
    assert r.json()["data"]["result_type"] == "number"
    # 环拒绝（BR-02）
    c.post(f"{base}/formula/", {"name": "G1", "field_key": "cf_g1", "formula": "prop_cf('cf_dev') + 1"}, format="json")
    r2 = c.post(
        f"{base}/formula/",
        {"name": "G2", "field_key": "cf_g2", "formula": "prop_cf('cf_g1') - prop_cf('cf_dev')"},
        format="json",
    )
    assert r2.status_code == 201  # 无环（DAG 合法）
    c.post(
        f"{base}/formula/",
        {"name": "G3", "field_key": "cf_g3", "formula": "prop_cf('cf_g2') + prop_cf('cf_dev')"},
        format="json",
    )
    # 真环（自引）：拒绝（BR-02）
    r_cycle = c.post(
        f"{base}/formula/",
        {"name": "环", "field_key": "cf_loop", "formula": "prop_cf('cf_g1') + prop_cf('cf_loop')"},
        format="json",
    )
    assert r_cycle.status_code == 409
    assert r_cycle.json()["error"]["code"] == "RESOURCE_CIRCULAR_DEPENDENCY"


def test_validate_and_preview_endpoints(env):
    c = _c(env["owner"])
    base = f"/api/v1/workspaces/{env['ws'].slug}/issue-properties"
    r = c.post(f"{base}/validate-expression/", {"formula": "prop_cf('cf_actual') * 2"}, format="json")
    assert r.status_code == 200 and r.json()["data"]["valid"] is True
    r2 = c.post(f"{base}/validate-expression/", {"formula": "__import__('os')"}, format="json")
    assert r2.json()["data"]["valid"] is False
    r3 = c.post(
        f"/api/v1/workspaces/{env['ws'].slug}/projects/{env['proj'].id}/issues/{env['issue'].id}/preview-expression/",
        {"formula": "subtract(prop_cf('cf_actual'), prop_cf('cf_planned'))"},
        format="json",
    )
    assert r3.status_code == 200 and r3.json()["data"]["value"] == 30


def test_cascade_field_create(env):
    c = _c(env["owner"])
    levels = [
        {"name": "省", "options": [{"label": "浙江", "value": "zj"}]},
        {"name": "市", "options": [{"label": "杭州", "value": "hz"}]},
        {"name": "区", "options": [{"label": "西湖", "value": "xh"}]},
    ]
    r = c.post(
        f"/api/v1/workspaces/{env['ws'].slug}/issue-properties/cascade/",
        {"name": "区域", "field_key": "cf_region", "levels": levels},
        format="json",
    )
    assert r.status_code == 201 and r.json()["data"]["levels"] == 3
    r2 = c.post(
        f"/api/v1/workspaces/{env['ws'].slug}/issue-properties/cascade/",
        {"name": "一级", "field_key": "cf_one", "levels": levels[:1]},
        format="json",
    )
    assert r2.status_code == 400  # < 2 级拒绝


def test_relation_field_cross_domain(env):
    other_owner = User.objects.create_user(email="fx-other@rabbit.dev", password="Rabbit123!")
    other_ws = Workspace.objects.create(
        name="别家", slug=f"w-oth-{other_owner.id.hex[:6]}", owner=other_owner, created_by=other_owner
    )
    other_proj = Project.objects.create(workspace=other_ws, name="OP", identifier="OP1", created_by=other_owner)
    c = _c(env["owner"])
    r = c.post(
        f"/api/v1/workspaces/{env['ws'].slug}/issue-properties/relation/",
        {"name": "关联", "field_key": "cf_links", "target_project_ids": [str(other_proj.id)]},  # 越域项目
        format="json",
    )
    assert r.status_code == 400
    assert "DOES_NOT_EXIST" in str(r.json()["error"]["details"])
    r2 = c.post(
        f"/api/v1/workspaces/{env['ws'].slug}/issue-properties/relation/",
        {"name": "关联", "field_key": "cf_links", "target_project_ids": [str(env["proj"].id)]},
        format="json",
    )
    assert r2.status_code == 201
