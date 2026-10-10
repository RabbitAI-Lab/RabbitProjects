"""TASK-008 自定义字段单元测试（UT-01~16 中 pytest 落点子集）。

覆盖：12 类型值校验逐类（UT-05 36 断言）、未知 key（UT-06）、类型作用域（UT-07）、
默认值填充（UT-08）、空值不落 key（UT-09）、必填存量不追溯（UT-10）、私有覆盖全局
（UT-11）、auto_increment 拒赋值/串行取号（UT-12/13）、缓存失效（UT-14，真 Redis，
不可用自动 skip）、数量上限（UT-15）、逐键 diff（UT-16）、合并语义、cleanup 任务。
HTTP 全矩阵（CRUD/409/202/ETag/?property./order_by）在 sprint-2-flow.py。
"""

from __future__ import annotations

import uuid as uuid_module

import pytest

from plane.db.models import CustomFieldDefinition, Issue, IssueType, Project, User, Workspace
from plane.db.services.custom_fields import (
    diff_custom_fields,
    merge_custom_fields,
    next_auto_increment,
    validate_custom_fields,
    validate_field_value,
)
from plane.db.services.field_schema import (
    FIELD_SCHEMA_CACHE_KEY,
    get_cached_schema,
    reset_redis_state,
    resolve_fields,
)
from plane.utils.exceptions import CustomFieldValidationError

pytestmark = pytest.mark.django_db

OPTS = [
    {"label": "致命", "value": "critical", "color": "#DC2626", "sort_order": 1},
    {"label": "严重", "value": "major", "color": "#F59E0B", "sort_order": 2},
    {"label": "一般", "value": "minor", "color": "#3B82F6", "sort_order": 3},
]


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="cf-owner@rabbit.dev", password="Rabbit123!")
    member = User.objects.create_user(email="cf-member@rabbit.dev", password="Rabbit123!")
    ws = Workspace.objects.create(name="W", slug=f"w-cf-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    proj = Project.objects.create(name="P", identifier="CF", workspace=ws, created_by=owner)
    proj2 = Project.objects.create(name="P2", identifier="CF2", workspace=ws, created_by=owner)
    bug = IssueType.objects.create(workspace=ws, name="缺陷", is_default=False, created_by=owner)
    story = IssueType.objects.create(workspace=ws, name="需求", is_default=True, created_by=owner)
    from plane.db.models import ProjectMember, ProjectRole

    ProjectMember.objects.create(project=proj, member=member, role=ProjectRole.CONTRIBUTOR, created_by=owner)
    return {"owner": owner, "member": member, "ws": ws, "proj": proj, "proj2": proj2, "bug": bug, "story": story}


def _mk(env, key, ftype, **kw) -> CustomFieldDefinition:
    return CustomFieldDefinition.objects.create(
        workspace=env["ws"],
        project=kw.pop("project", env["proj"]),
        name=kw.pop("name", key),
        field_key=key,
        field_type=ftype,
        options=kw.pop("options", OPTS if ftype in ("select", "multi_select") else []),
        **kw,
    )


# ─────────────────────────────────────────────────────────────────────
# UT-05：12 类型值校验逐类（合法 / 非法 / 空 三组）
# ─────────────────────────────────────────────────────────────────────
CASES = [
    ("cf_text", "text", "hello", 123),
    ("cf_textarea", "textarea", "line1\nline2", 3.14),
    ("cf_number", "number", 42, "42"),
    ("cf_select", "select", "critical", "blocker"),
    ("cf_multi", "multi_select", ["critical", "major"], "critical"),
    ("cf_date", "date", "2026-09-01", "2026/09/01"),
    ("cf_member", "member", "MEMBER_UUID", "not-a-uuid"),
    ("cf_member_multi", "member_multi", ["MEMBER_UUID"], "MEMBER_UUID"),
    ("cf_checkbox", "checkbox", True, "true"),
    ("cf_url", "url", "https://example.com/a", "ftp://example.com"),
    ("cf_currency", "currency", {"amount": 100.5, "currency": "CNY"}, {"amount": "x"}),
    ("cf_seq", "auto_increment", 5, 999),  # 合法/非法都拒（系统分配）
]


@pytest.mark.parametrize("key,ftype,valid,invalid", CASES)
def test_ut05_validate_field_value_per_type(env, key, ftype, valid, invalid):
    d = _mk(env, key, ftype)
    # member 类的合法值替换为真实成员 id
    if ftype in ("member", "member_multi"):
        valid = [str(env["member"].id)] if ftype == "member_multi" else str(env["member"].id)
    if ftype == "auto_increment":
        # BR-09：客户端赋值一律拒（合法样本也拒）
        with pytest.raises(CustomFieldValidationError):
            validate_field_value(d, valid, project=env["proj"])
    else:
        assert validate_field_value(d, valid, project=env["proj"]) is not None
    with pytest.raises(CustomFieldValidationError):
        validate_field_value(d, invalid, project=env["proj"])
    # 空值：非必填返回 None（不落 key）
    assert validate_field_value(d, None, project=env["proj"]) is None


def test_ut05_member_requires_project_member(env):
    d = _mk(env, "cf_owner", "member")
    with pytest.raises(CustomFieldValidationError):
        validate_field_value(d, str(uuid_module.uuid4()), project=env["proj"])


# ─────────────────────────────────────────────────────────────────────
# UT-01/02/03/04：定义侧校验
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad_key", ["severity", "cf_Abc", "cf-bad", "cf_x", "cf_" + "a" * 62])
def test_ut01_key_format_branches(env, bad_key):
    d = _mk(env, "cf_ok", "text")
    d.field_key = bad_key
    from django.core.exceptions import ValidationError as DJValidation

    with pytest.raises(DJValidation):
        d.full_clean()


def test_ut02_key_immutable_after_create(env):
    d = _mk(env, "cf_orig", "text")
    d.field_key = "cf_changed"
    from django.core.exceptions import ValidationError as DJValidation

    with pytest.raises(DJValidation):
        d.save()


def test_ut03_field_type_immutable(env):
    d = _mk(env, "cf_keep", "text")
    d.field_type = "number"
    from django.core.exceptions import ValidationError as DJValidation

    with pytest.raises(DJValidation):
        d.save()


def test_ut04_duplicate_option_value(env):
    from django.core.exceptions import ValidationError as DJValidation

    # 未落库实例上做纯校验（落库路径 save→full_clean 同样拦截）
    d = CustomFieldDefinition(
        workspace=env["ws"],
        project=env["proj"],
        name="dup",
        field_key="cf_dup",
        field_type="select",
        options=[{"label": "a", "value": "x"}, {"label": "b", "value": "x"}],
    )
    with pytest.raises(DJValidation):
        d.full_clean()


# ─────────────────────────────────────────────────────────────────────
# 整体校验：未知 key / 类型作用域 / 默认值 / 空值 / 必填 / 合并语义
# ─────────────────────────────────────────────────────────────────────
def test_ut06_unknown_key_rejected(env):
    _mk(env, "cf_known", "text")
    with pytest.raises(CustomFieldValidationError):
        validate_custom_fields(env["proj"], None, {"cf_hack": "x"})


def test_ut07_applicable_type_scope(env):
    _mk(env, "cf_severity", "select", applicable_types=[str(env["bug"].id)])
    # 需求类型任务提交 → 拒（字段不适用于该类型）
    with pytest.raises(CustomFieldValidationError):
        validate_custom_fields(env["proj"], env["story"].id, {"cf_severity": "critical"})
    # 缺陷类型 → 过
    cleaned = validate_custom_fields(env["proj"], env["bug"].id, {"cf_severity": "critical"})
    assert cleaned == {"cf_severity": "critical"}


def test_ut08_default_value_fills(env):
    _mk(env, "cf_source", "select", default_value="major")
    cleaned = validate_custom_fields(env["proj"], None, {})
    assert cleaned == {"cf_source": "major"}


def test_ut09_null_value_drops_key(env):
    _mk(env, "cf_optional", "text")
    cleaned = validate_custom_fields(env["proj"], None, {"cf_optional": None})
    assert "cf_optional" not in cleaned


def test_ut10_required_not_retroactive(env):
    d = _mk(env, "cf_retro", "text")
    issue = Issue.objects.create(name="存量", project=env["proj"], sequence_id=1, sort_order=1, created_by=env["owner"])
    # 存量任务缺该字段：保存其他字段（合并语义，不触碰 cf_retro）→ 通过（BR-08 不追溯）
    merged = merge_custom_fields(env["proj"], None, {}, {})
    assert merged == {}
    d.is_required = True
    d.save()
    assert merge_custom_fields(env["proj"], None, issue.custom_fields, {}) == {}
    # 但清空必填字段被拒
    with pytest.raises(CustomFieldValidationError):
        merge_custom_fields(env["proj"], None, {"cf_retro": "x"}, {"cf_retro": None})


def test_required_missing_on_create_rejected(env):
    _mk(env, "cf_must", "text", is_required=True)
    with pytest.raises(CustomFieldValidationError):
        validate_custom_fields(env["proj"], None, {})


def test_merge_semantics(env):
    _mk(env, "cf_aa", "text")
    _mk(env, "cf_bb", "select")
    current = {"cf_aa": "old", "cf_bb": "critical"}
    merged = merge_custom_fields(env["proj"], None, current, {"cf_aa": "new", "cf_bb": None})
    assert merged == {"cf_aa": "new"}  # 未提及保留 / null 显式清空


def test_ut12_auto_increment_client_value_rejected(env):
    _mk(env, "cf_seq", "auto_increment")
    with pytest.raises(CustomFieldValidationError):
        validate_custom_fields(env["proj"], None, {"cf_seq": 999})


def test_ut13_auto_increment_serial_ten_unique(env):
    _mk(env, "cf_seq", "auto_increment")
    from django.db import transaction

    numbers = []
    with transaction.atomic():
        for i in range(10):
            n = next_auto_increment(env["proj"].id, "cf_seq")
            Issue.objects.create(
                name=f"seq-{i}",
                project=env["proj"],
                sequence_id=i + 1,
                sort_order=i + 1,
                created_by=env["owner"],
                custom_fields={"cf_seq": n},
            )
            numbers.append(n)
    assert numbers == list(range(1, 11))  # 1~10 无重（并发版 UT-13 在真库由 advisory lock 保证）


# ─────────────────────────────────────────────────────────────────────
# UT-11：私有覆盖全局
# ─────────────────────────────────────────────────────────────────────
def test_ut11_project_overrides_global(env):
    _mk(env, "cf_shared", "text", project=None, name="全局版", default_value="g")
    _mk(env, "cf_shared", "select", project=env["proj"], name="私有版")
    fields = resolve_fields(env["proj"])
    hit = [f for f in fields if f.field_key == "cf_shared"]
    assert len(hit) == 1 and hit[0].project_id == env["proj"].id and hit[0].field_type == "select"
    # 私有不外溢：另一项目解析到全局版
    fields2 = resolve_fields(env["proj2"])
    hit2 = [f for f in fields2 if f.field_key == "cf_shared"]
    assert len(hit2) == 1 and hit2[0].project_id is None


# ─────────────────────────────────────────────────────────────────────
# UT-14：缓存失效（真 Redis；不可用自动 skip）
# ─────────────────────────────────────────────────────────────────────
def _redis_or_skip():
    reset_redis_state()
    from plane.db.services import field_schema as fs

    client = fs._redis()
    if client is None:
        pytest.skip("Redis 不可用（UT-14 需要真 Redis，CLAUDE.md 环境表 rp-redis）")
    return client, fs


def test_ut14_global_field_change_invalidates_all_project_keys(env):
    client, fs = _redis_or_skip()
    g = _mk(env, "cf_global", "text", project=None)
    # 两个项目各访问一次 → 两个缓存 key
    assert any(i["key"] == "cf_global" for i in get_cached_schema(env["proj"]))
    assert any(i["key"] == "cf_global" for i in get_cached_schema(env["proj2"]))
    k1 = FIELD_SCHEMA_CACHE_KEY.format(workspace_id=env["ws"].id, project_id=env["proj"].id)
    k2 = FIELD_SCHEMA_CACHE_KEY.format(workspace_id=env["ws"].id, project_id=env["proj2"].id)
    assert client.get(k1) is not None and client.get(k2) is not None
    # 全局字段改名 → post_save 信号 → 该 WS 全部项目 key 失效（SCAN 模式批删）
    g.name = "全局版-改名"
    g.save()
    assert client.get(k1) is None and client.get(k2) is None
    # 重新回源看到新名
    assert any(i["name"] == "全局版-改名" for i in get_cached_schema(env["proj"]))
    client.delete(k1, k2)


def test_ut14_project_field_change_invalidates_single_key(env):
    client, fs = _redis_or_skip()
    _mk(env, "cf_projonly", "text")
    get_cached_schema(env["proj"])
    get_cached_schema(env["proj2"])
    k1 = FIELD_SCHEMA_CACHE_KEY.format(workspace_id=env["ws"].id, project_id=env["proj"].id)
    k2 = FIELD_SCHEMA_CACHE_KEY.format(workspace_id=env["ws"].id, project_id=env["proj2"].id)
    CustomFieldDefinition.objects.filter(field_key="cf_projonly").update(name="改名")
    # update() 不触发 post_save —— 直接触发一次失效器（信号由 save 路径覆盖，见上一用例）
    d = CustomFieldDefinition.objects.get(field_key="cf_projonly")
    fs.invalidate_field_schema_cache(d)
    assert client.get(k1) is None
    client.delete(k1, k2)


def test_cache_degrades_when_redis_down(env, monkeypatch):
    """Redis 不可用 → 降级直查，不抛异常（不阻断主流程）。"""
    monkeypatch.setattr("plane.db.services.field_schema._redis", lambda: None)
    _mk(env, "cf_degrade", "text")
    items = get_cached_schema(env["proj"])
    assert any(i["key"] == "cf_degrade" for i in items)


# ─────────────────────────────────────────────────────────────────────
# UT-15：数量上限（service 层；HTTP 409 在 flow T8）
# ─────────────────────────────────────────────────────────────────────
def test_ut15_limits(env):
    from plane.app.views.custom_fields import _check_limits
    from plane.base.exception import AppException
    from plane.settings.features import (
        MAX_CUSTOM_FIELDS_PER_WORKSPACE,
        MAX_INDEXED_CUSTOM_FIELDS_PER_WORKSPACE,
    )

    # 索引探测字段先建（它也是第 50 个启用字段），随后补满 50
    d = _mk(env, "cf_idx_probe", "number", is_indexed=True)
    for i in range(MAX_CUSTOM_FIELDS_PER_WORKSPACE - 1):
        _mk(env, f"cf_fill_{i:02d}", "text")
    with pytest.raises(AppException) as ei:
        _check_limits(env["ws"].id, creating=True, is_indexed=False)
    assert ei.value.error_code == "RESOURCE_LIMIT_EXCEEDED"

    for i in range(MAX_INDEXED_CUSTOM_FIELDS_PER_WORKSPACE):
        _mk(env, f"cf_idx_{i:02d}", "number", is_indexed=True)
    # 第 12 个标记（排除自身后已 10 个启用索引字段）→ 拒
    with pytest.raises(AppException) as ei2:
        _check_limits(env["ws"].id, creating=False, is_indexed=True, instance=d)
    assert ei2.value.error_code == "RESOURCE_LIMIT_EXCEEDED"


# ─────────────────────────────────────────────────────────────────────
# UT-16：逐键 diff
# ─────────────────────────────────────────────────────────────────────
def test_ut16_per_key_diff():
    changes = diff_custom_fields(
        {"cf_a": "x", "cf_b": 1},
        {"cf_a": "y", "cf_c": True},
    )
    assert [(c["key"], c["old"], c["new"]) for c in changes] == [
        ("cf_a", '"x"', '"y"'),
        ("cf_b", "1", None),
        ("cf_c", None, "true"),
    ]


# ─────────────────────────────────────────────────────────────────────
# cleanup 任务（BR-11）：分批清理 JSONB key（事务内直跑，回滚零残留）
# ─────────────────────────────────────────────────────────────────────
def test_cleanup_task_removes_key_in_batches(env):
    from plane.bgtasks.field_cleanup import cleanup_deleted_field_values

    d = _mk(env, "cf_legacy", "text", project=None)  # 全局字段 → WS 范围清理
    Issue.objects.create(
        name="a",
        project=env["proj"],
        sequence_id=1,
        sort_order=1,
        created_by=env["owner"],
        custom_fields={"cf_legacy": "v", "cf_keep": "k"},
    )
    d.deleted_at = d.created_at  # 任意非空；任务用 all_objects 取定义
    from django.utils import timezone

    d.deleted_at = timezone.now()
    d.save(update_fields=["deleted_at"])
    total = cleanup_deleted_field_values(str(d.id), batch_size=1)  # 极小批强迫多批循环
    assert total == 1
    issue = Issue.objects.get(sequence_id=1, project=env["proj"])
    assert "cf_legacy" not in issue.custom_fields and issue.custom_fields["cf_keep"] == "k"
    # 幂等：再跑一遍 0 行
    assert cleanup_deleted_field_values(str(d.id)) == 0


# ─────────────────────────────────────────────────────────────────────
# 排序/筛选编译（数值序 9<10<100 / 选项配置序 / property 等值与 null）
# ─────────────────────────────────────────────────────────────────────
def _seed_issues_for_order(env):
    for seq, num in enumerate([9, 10, 100, None], start=1):
        cf = {"cf_points": num} if num is not None else {}
        Issue.objects.create(
            name=f"o-{seq}",
            project=env["proj"],
            sequence_id=seq,
            sort_order=seq,
            created_by=env["owner"],
            custom_fields=cf,
        )


def test_order_by_cf_numeric_is_value_order(env):
    from plane.db.services.issue_query import IssueFilterSet

    _mk(env, "cf_points", "number")
    _seed_issues_for_order(env)
    fs = IssueFilterSet(None, project=env["proj"])
    qs, warning = fs.apply_order(Issue.objects.filter(project=env["proj"]), "cf_points")
    assert warning is None
    nums = [i.custom_fields.get("cf_points") for i in qs if i.custom_fields.get("cf_points") is not None]
    assert nums == [9, 10, 100]  # 数值序（字典序会是 10<100<9）


def test_order_by_cf_select_uses_config_order(env):
    from plane.db.services.issue_query import IssueFilterSet

    opts = [
        {"label": "B", "value": "b", "sort_order": 1},
        {"label": "A", "value": "a", "sort_order": 2},
        {"label": "C", "value": "c", "sort_order": 3},
    ]
    _mk(env, "cf_grade", "select", options=opts)
    for seq, grade in enumerate(["a", "b", "c"], start=1):
        Issue.objects.create(
            name=f"g-{seq}",
            project=env["proj"],
            sequence_id=seq,
            sort_order=seq,
            created_by=env["owner"],
            custom_fields={"cf_grade": grade},
        )
    fs = IssueFilterSet(None, project=env["proj"])
    qs, _ = fs.apply_order(Issue.objects.filter(project=env["proj"]), "cf_grade")
    assert [i.custom_fields["cf_grade"] for i in qs] == ["b", "a", "c"]  # 配置序非字典序


def test_property_filter_eq_and_null(env):
    from plane.db.services.issue_query import _find_definition, property_filter_q

    _mk(env, "cf_points", "number")
    _seed_issues_for_order(env)
    d = _find_definition(env["proj"], field_key="cf_points")
    qs = Issue.objects.filter(project=env["proj"]).filter(property_filter_q(d, "9,10"))
    assert sorted(i.custom_fields["cf_points"] for i in qs) == [9, 10]
    qs_null = Issue.objects.filter(project=env["proj"]).filter(property_filter_q(d, "null"))
    assert [i.sequence_id for i in qs_null] == [4]


def test_index_naming_idempotent():
    from plane.bgtasks.field_index import INDEX_EXPRESSIONS, index_name_for

    assert index_name_for("cf_points") == index_name_for("cf_points")
    assert len(index_name_for("cf_" + "x" * 60)) <= 63
    assert set(INDEX_EXPRESSIONS) >= {
        "number",
        "auto_increment",
        "currency",
        "date",
        "text",
        "select",
    }
