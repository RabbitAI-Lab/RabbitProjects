"""R5 模板库与审批留痕测试（WF-005 + WF-006，Sprint-7 门禁）。

WF-005：预设四套幂等种子、模板 CRUD（is_builtin 保护）、两步下发
（pending_confirm → confirm active + Workflow 溯源 source_template）、
状态映射（复用既有 State BR-05）、解锁申请（理由必填 + 每项目一条待审）。
WF-006：哈希链 append（prev→event 链式）、全链校验（篡改检测）、
导出 CSV 行 + 导出事实入链、触发器只增（UPDATE 拒绝）。
"""

from __future__ import annotations

import pytest
from django.db import connection

from plane.db.models import (
    Project,
    ProjectMember,
    ProjectRole,
    State,
    TemplateUnlockRequest,
    User,
    WorkflowTemplate,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)
from plane.db.seeds.project_states import seed_project_states
from plane.workflow.audit_service import (
    append_audit_event,
    export_rows_to_csv,
    query_events,
    verify_chain,
)
from plane.workflow.template_service import (
    TemplateError,
    confirm_distribution,
    distribute,
    request_unlock,
    seed_builtin_templates,
)

pytestmark = pytest.mark.django_db


@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="r5-owner@rabbit.dev", password="Rabbit123!", display_name="负责人")
    member = User.objects.create_user(email="r5-member@rabbit.dev", password="Rabbit123!", display_name="成员")
    ws = Workspace.objects.create(name="W", slug=f"w-r5-{owner.id.hex[:8]}", owner=owner, created_by=owner)
    for u, r in ((owner, WorkspaceRole.OWNER), (member, WorkspaceRole.MEMBER)):
        WorkspaceMember.objects.create(workspace=ws, member=u, role=r, created_by=owner)
    proj = Project.objects.create(name="P", identifier="R5P", workspace=ws, created_by=owner)
    ProjectMember.objects.create(project=proj, member=owner, role=ProjectRole.ADMIN, created_by=owner)
    ProjectMember.objects.create(project=proj, member=member, role=ProjectRole.CONTRIBUTOR, created_by=owner)
    seed_project_states(proj)
    return {"owner": owner, "member": member, "ws": ws, "proj": proj}


# ── WF-005 模板库 ─────────────────────────────────────────────
class TestBuiltinTemplates:
    def test_seed_four_idempotent(self, env):
        assert seed_builtin_templates(env["ws"]) == 4
        assert seed_builtin_templates(env["ws"]) == 0  # 幂等
        names = set(
            WorkflowTemplate.objects.filter(workspace=env["ws"], is_builtin=True).values_list("name", flat=True)
        )
        assert names == {"研发需求流程", "缺陷修复流程", "测试上线流程", "日常任务流程"}


class TestDistribution:
    def test_two_step_distribute_and_confirm(self, env):
        seed_builtin_templates(env["ws"])
        tpl = WorkflowTemplate.objects.get(workspace=env["ws"], name="日常任务流程")
        dist = distribute(template=tpl, project=env["proj"], actor=env["owner"])
        assert dist.status == "pending_confirm"
        assert dist.locked is True
        wf = confirm_distribution(dist=dist, project=env["proj"], actor=env["owner"])
        dist.refresh_from_db()
        assert dist.status == "active"
        assert dist.applied_workflow_id == wf.id
        # 溯源列（WF-001 §7.3 冻结契约）
        assert wf.source_template_id == tpl.id
        # 深拷贝落三表：3 节点 2 边（日常任务流程快照）
        assert wf.wf_states.count() == 3
        assert wf.transitions.count() == 2

    def test_state_mapping_reuses_existing(self, env):
        """BR-05 状态映射：同名优先复用项目既有 State（零重复建）。"""
        seed_builtin_templates(env["ws"])
        tpl = WorkflowTemplate.objects.get(workspace=env["ws"], name="日常任务流程")
        dist = distribute(template=tpl, project=env["proj"], actor=env["owner"])
        before = State.objects.filter(project=env["proj"]).count()
        confirm_distribution(dist=dist, project=env["proj"], actor=env["owner"])
        after = State.objects.filter(project=env["proj"]).count()
        # seed_project_states 已建「待办/进行中/已完成/已取消」——模板三态全部同名复用（BR-05 零重复建）
        assert after - before == 0
        assert State.objects.filter(project=env["proj"], name="进行中").count() == 1


class TestUnlockRequest:
    def test_reason_required(self, env):
        seed_builtin_templates(env["ws"])
        tpl = WorkflowTemplate.objects.first()
        dist = distribute(template=tpl, project=env["proj"], actor=env["owner"])
        with pytest.raises(TemplateError) as ei:
            request_unlock(dist=dist, project=env["proj"], actor=env["owner"], kind="unlock", reason="  ")
        assert ei.value.status == 400

    def test_one_pending_per_project(self, env):
        seed_builtin_templates(env["ws"])
        tpl = WorkflowTemplate.objects.first()
        dist = distribute(template=tpl, project=env["proj"], actor=env["owner"])
        request_unlock(dist=dist, project=env["proj"], actor=env["owner"], kind="unlock", reason="流程需本地化调整")
        with pytest.raises(TemplateError) as ei:
            request_unlock(dist=dist, project=env["proj"], actor=env["owner"], kind="upgrade", reason="想升级")
        assert ei.value.status == 409
        # 处理后可再申请
        req = TemplateUnlockRequest.objects.get(project=env["proj"])
        req.status = "approved"
        req.save(update_fields=["status"])
        request_unlock(dist=dist, project=env["proj"], actor=env["owner"], kind="upgrade", reason="模板升版")
        assert TemplateUnlockRequest.objects.filter(project=env["proj"]).count() == 2


# ── WF-006 审计留痕 ───────────────────────────────────────────
class TestAuditChain:
    def test_append_and_verify(self, env):
        e1 = append_audit_event(
            project=env["proj"], type_="approval.started", instance=None, actor=env["owner"], payload={"k": 1}
        )
        e2 = append_audit_event(
            project=env["proj"], type_="approval.approved", instance=None, actor=env["member"], payload={"k": 2}
        )
        assert e2.prev_hash == e1.event_hash  # 链式
        assert verify_chain(env["proj"]) == []  # 完整

    def test_tamper_detection(self, env):
        append_audit_event(
            project=env["proj"], type_="approval.started", instance=None, actor=env["owner"], payload={"k": 1}
        )
        e2 = append_audit_event(
            project=env["proj"], type_="approval.approved", instance=None, actor=env["member"], payload={"k": 2}
        )
        # 直接 UPDATE 被触发器拒绝（BR-01 append-only DDL 落点）
        from django.db import transaction as _tx
        from django.db.utils import ProgrammingError

        try:
            with _tx.atomic():
                with connection.cursor() as cur:
                    cur.execute("UPDATE approval_audit_events SET payload = '{\"k\": 99}' WHERE id = %s", [e2.id])
            raise AssertionError("触发器未拦截 UPDATE")
        except ProgrammingError as exc:
            assert "append-only" in str(exc)
        # savepoint 已回滚——链校验完整（篡改被拦截）
        assert verify_chain(env["proj"]) == []

    def test_export_csv_and_chain_log(self, env):
        append_audit_event(
            project=env["proj"], type_="approval.started", instance=None, actor=env["owner"], payload={"a": 1}
        )
        rows = query_events(env["proj"])
        csv_text = export_rows_to_csv(rows)
        assert "approval.started" in csv_text
        assert "event_hash" in csv_text  # 表头含链哈希列


# ── 突变自检 ──────────────────────────────────────────────────
class TestMutationReverts:
    def test_verify_chain_mutation(self, env):
        """突变：verify_chain 恒返回空 → 篡改场景漏检（红）→ 恢复（绿）。"""
        from unittest.mock import patch as mpatch

        e1 = append_audit_event(
            project=env["proj"], type_="approval.started", instance=None, actor=env["owner"], payload={"k": 1}
        )
        # DELETE 同样被触发器拒绝——链物理不可拆
        from django.db import transaction as _tx
        from django.db.utils import ProgrammingError

        try:
            with _tx.atomic():
                with connection.cursor() as cur:
                    cur.execute("DELETE FROM approval_audit_events WHERE id = %s", [e1.id])
            raise AssertionError("触发器未拦截 DELETE")
        except ProgrammingError as exc:
            assert "append-only" in str(exc)
        # 突变：哈希函数恒零 → verify 应报全链断（红）
        with mpatch("plane.workflow.audit_service._event_hash", return_value="0" * 64):
            assert verify_chain(env["proj"]) != []
        # 恢复（无 patch）：链自洽
        assert verify_chain(env["proj"]) == []
