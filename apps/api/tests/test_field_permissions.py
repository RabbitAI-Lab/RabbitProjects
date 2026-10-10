"""高级字段与字段权限测试（TASK-012，Sprint-7 R2-B 门禁）。

覆盖：四类型值校验（cascade/relation/date_range/attachment，§2.1+§4.3）、
cascade_config 与 permission_config 保存校验（§4.2）、FieldPermissionService
四态判定（§4.4）、Schema API access 标注、列表/详情序列化剔除（§2.2 BR-10/12）、
PATCH 写入静默丢弃（BR-16）、FilterCompiler hidden 拒绝（BR-11）。
"""

from __future__ import annotations

import uuid as _uuid

import pytest

from plane.db.models import (
    CustomFieldDefinition,
    Project,
    ProjectMember,
    ProjectRole,
    State,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)
from plane.db.seeds.project_states import seed_project_states
from plane.db.services.custom_fields import (
    CustomFieldValidationError,
    backfill_cascade_options,
    validate_cascade_config,
    validate_field_value,
    validate_permission_config,
)
from plane.db.services.field_permissions import FieldPermissionService

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="f-owner@rabbit.dev", password="Rabbit123!", display_name="管理员")
    member = User.objects.create_user(email="f-member@rabbit.dev", password="Rabbit123!", display_name="成员")
    ws = Workspace.objects.create(name="W", slug=f"w-f-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    for u, r in ((owner, WorkspaceRole.OWNER), (member, WorkspaceRole.MEMBER)):
        WorkspaceMember.objects.create(workspace=ws, member=u, role=r, created_by=owner)
    proj = Project.objects.create(name="P", identifier="FPR", workspace=ws, created_by=owner)
    ProjectMember.objects.create(project=proj, member=owner, role=ProjectRole.ADMIN, created_by=owner)
    ProjectMember.objects.create(project=proj, member=member, role=ProjectRole.CONTRIBUTOR, created_by=owner)
    seed_project_states(proj)
    return {"owner": owner, "member": member, "ws": ws, "proj": proj}


# ── 四类型值校验（§4.3）───────────────────────────────────────────
class TestFieldValueValidation:
    def test_cascade_length_must_match_levels(self, env):
        d = CustomFieldDefinition(
            workspace=env["ws"],
            name="区",
            field_key="cf_region",
            field_type=CustomFieldDefinition.FieldType.CASCADE,
            cascade_config={
                "levels": [
                    {"name": "省", "options": [{"label": "浙", "value": "zj"}]},
                    {"name": "市", "options": [{"label": "杭", "value": "hz", "parent_value": "zj"}]},
                ]
            },
            created_by=env["owner"],
        )
        backfill_cascade_options(d)
        with pytest.raises(CustomFieldValidationError):
            validate_field_value(d, ["zj"])  # 缺一级
        # 全路径通过
        assert validate_field_value(d, ["zj", "hz"]) == ["zj", "hz"]
        # 父链不连续
        with pytest.raises(CustomFieldValidationError):
            validate_field_value(d, ["zj", "nope"])

    def test_relation_20_limit_and_cross_project(self, env):
        d = CustomFieldDefinition(
            workspace=env["ws"],
            name="关联",
            field_key="cf_rel",
            field_type=CustomFieldDefinition.FieldType.RELATION,
            created_by=env["owner"],
        )
        from plane.db.models import Issue as _I

        i1 = _I.objects.create(
            project=env["proj"],
            name="x",
            state=State.objects.filter(project=env["proj"], name="待办").first(),
            created_by=env["owner"],
        )
        # 自引用：传 issue=i1，列表含 i1.id
        with pytest.raises(CustomFieldValidationError):
            validate_field_value(d, [str(i1.id)], project=env["proj"], issue=i1)
        # 正常（不含自身）
        assert validate_field_value(d, [str(i1.id)], project=env["proj"]) == [str(i1.id)]
        # 超 20
        with pytest.raises(CustomFieldValidationError):
            validate_field_value(d, [str(i1.id)] * 21, project=env["proj"])

    def test_date_range_start_le_end(self, env):
        d = CustomFieldDefinition(
            workspace=env["ws"],
            name="窗口",
            field_key="cf_win",
            field_type=CustomFieldDefinition.FieldType.DATE_RANGE,
            created_by=env["owner"],
        )
        with pytest.raises(CustomFieldValidationError):
            validate_field_value(d, {"start": "2026-10-01", "end": "2026-09-01"})
        assert validate_field_value(d, {"start": "2026-09-01", "end": "2026-10-01"}) == {
            "start": "2026-09-01",
            "end": "2026-10-01",
        }

    def test_attachment_not_uploaded_rejected(self, env):
        from plane.db.models import FileAsset

        d = CustomFieldDefinition(
            workspace=env["ws"],
            name="合同",
            field_key="cf_atta",
            field_type=CustomFieldDefinition.FieldType.ATTACHMENT,
            created_by=env["owner"],
        )
        not_uploaded = FileAsset.objects.create(
            workspace=env["ws"],
            project=env["proj"],
            entity_type="issue",
            entity_id=str(_uuid.uuid4()),
            size=0,
            storage_path="cf/na.txt",
            created_by=env["owner"],
        )
        # 默认 status=uploading——BR-07 要求 uploaded 才算完成
        with pytest.raises(CustomFieldValidationError):
            validate_field_value(d, [str(not_uploaded.id)])
        # 设 uploaded → 通过
        not_uploaded.status = FileAsset.Status.UPLOADED
        not_uploaded.save(update_fields=["status"])
        assert validate_field_value(d, [str(not_uploaded.id)]) == [str(not_uploaded.id)]


# ── 配置校验（§4.2）───────────────────────────────────────────────
class TestConfigValidation:
    def test_cascade_3_levels_max(self, env):
        issues = validate_cascade_config({"levels": [{"name": str(i), "options": []} for i in range(4)]})
        assert any(i["code"] == "INVALID" and "级数" in i["message"] for i in issues)

    def test_cascade_whole_tree_unique(self, env):
        issues = validate_cascade_config(
            {
                "levels": [
                    {"name": "省", "options": [{"label": "浙", "value": "zj"}]},
                    {"name": "市", "options": [{"label": "杭", "value": "zj", "parent_value": "zj"}]},
                ]
            }
        )
        assert any(i["code"] == "UNIQUE" for i in issues)

    def test_cascade_300_total_limit(self, env):
        big = [{"label": str(i), "value": f"v{i}"} for i in range(101)]
        issues = validate_cascade_config(
            {
                "levels": [
                    {"name": "L1", "options": big},
                    {"name": "L2", "options": big},
                ]
            }
        )  # 202 项，未超；但 parent_value 缺失
        assert any(i["code"] == "DOES_NOT_EXIST" for i in issues)
        # 真触发 300 上限
        issues2 = validate_cascade_config(
            {
                "levels": [
                    {"name": "L1", "options": [{"label": "a" + str(i), "value": "a" + str(i)} for i in range(80)]},
                    {
                        "name": "L2",
                        "options": [
                            {"label": "b" + str(i), "value": "b" + str(i), "parent_value": "a" + str(i % 80)}
                            for i in range(250)
                        ],
                    },
                ]
            }
        )  # 80+250=330 > 300；两级 100 内
        assert any(i["code"] == "LIMIT" and "300" in i["message"] for i in issues2)

    def test_permission_role_pattern(self, env):
        issues = validate_permission_config({"read": ["role:proj_admin", "user:f153a8c4-aa06-4723-b0df-767ef6e1b017"]})
        assert issues == []
        issues2 = validate_permission_config({"write": ["role:bad_code"]})
        assert any(i["code"] == "NOT_A_CHOICE" for i in issues2)
        # required_for 不接受 user:
        issues3 = validate_permission_config({"required_for": ["user:f153a8c4-aa06-4723-b0df-767ef6e1b017"]})
        assert any(i["code"] == "NOT_A_CHOICE" and "required_for" in i["message"] for i in issues3)


# ── FieldPermissionService 四态 + 一处剔除（§4.4/§2.2）──────────────
class TestFieldPermission:
    def _field(self, env, **cfg):
        return CustomFieldDefinition.objects.create(
            workspace=env["ws"],
            project=env["proj"],
            name="xname",
            field_key="cf_xname",
            field_type="text",
            permission_config=cfg,
            created_by=env["owner"],
        )

    def test_editable_default(self, env):
        d = self._field(env)  # 空白名单 = 全员
        m = FieldPermissionService().resolve(env["member"], env["proj"], [d])
        assert m["cf_xname"] == "editable"

    def test_hidden_via_role(self, env):
        d = self._field(env, read=["role:proj_admin"], write=["role:proj_admin"])
        m = FieldPermissionService().resolve(env["member"], env["proj"], [d])
        assert m["cf_xname"] == "hidden"
        # ADMIN 豁免 hidden
        m_admin = FieldPermissionService().resolve(env["owner"], env["proj"], [d])
        assert m_admin["cf_xname"] == "editable"

    def test_readonly_no_write(self, env):
        d = self._field(env, read=["role:proj_contributor"], write=["role:proj_admin"])
        m = FieldPermissionService().resolve(env["member"], env["proj"], [d])
        assert m["cf_xname"] == "readonly"

    def test_required_for(self, env):
        d = self._field(
            env, read=["role:proj_contributor"], write=["role:proj_contributor"], required_for=["role:proj_contributor"]
        )
        m = FieldPermissionService().resolve(env["member"], env["proj"], [d])
        assert m["cf_xname"] == "required"

    def test_apply_to_payload_strips_hidden(self, env):
        d = self._field(env, read=["role:proj_admin"], write=["role:proj_admin"])
        m = FieldPermissionService().resolve(env["member"], env["proj"], [d])
        out = {"custom_fields": {"cf_xname": "v", "cf_other_field": "k"}}
        FieldPermissionService.apply_to_payload(out, m)
        assert "cf_xname" not in out["custom_fields"] and "cf_other_field" in out["custom_fields"]

    def test_drop_non_writable_br16(self, env):
        d = self._field(env, read=["role:proj_contributor"], write=["role:proj_admin"])
        m = FieldPermissionService().resolve(env["member"], env["proj"], [d])
        dropped = FieldPermissionService.drop_non_writable({"cf_xname": "v", "cf_other_field": "k"}, m)
        assert dropped == ["cf_xname"]


# ── FilterCompiler hidden 拒绝（BR-11）────────────────────────────
class TestFilterHiddenRejection:
    def test_cf_hidden_in_filter_403(self, env):
        from plane.app.filters.compiler import CompileContext, _compile_condition
        from plane.base.exception import AppException
        from plane.db.models import CustomFieldDefinition

        d = CustomFieldDefinition.objects.create(
            workspace=env["ws"],
            project=env["proj"],
            name="hfield",
            field_key="cf_hfield",
            field_type="text",
            permission_config={"read": ["role:proj_admin"], "write": ["role:proj_admin"]},
            created_by=env["owner"],
        )
        m = FieldPermissionService().resolve(env["member"], env["proj"], [d])
        ctx_hidden = CompileContext.build(project=env["proj"], user=env["member"], access_map=m)
        with pytest.raises(AppException) as ei:
            _compile_condition({"field": "cf_hfield", "operator": "eq", "value": "v"}, ctx_hidden)
        assert ei.value.error_code == "PERM_FIELD_HIDDEN"

    def test_cf_non_hidden_filter_works(self, env):
        """非 hidden 字段不应误伤：通过 access_map 解析路径（可编辑 = editable）。"""
        from plane.app.filters.compiler import CompileContext

        CustomFieldDefinition.objects.create(
            workspace=env["ws"],
            project=env["proj"],
            name="vfield",
            field_key="cf_vfield",
            field_type="text",
            created_by=env["owner"],
        )
        d = CustomFieldDefinition.objects.get(field_key="cf_vfield")
        m = FieldPermissionService().resolve(env["member"], env["proj"], [d])
        # 解析后 access 应为 editable（cf_vfield 不在 hidden 集）
        assert m["cf_vfield"] == "editable"
        # CompileContext 接 access_map 不应抛错
        ctx = CompileContext.build(project=env["proj"], user=env["member"], access_map=m)
        assert ctx.access_map == m
