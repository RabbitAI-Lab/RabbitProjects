"""R3 规则与工时轮测试（WF-003 + TASK-013，Sprint-7 门禁）。

WF-003：触发器匹配、AND 条件、防循环三闸（origin/chain_depth/dedup）、
ActionRegistry 五执行器（含 transition 走引擎路径）、保存校验（4 触发器 +
5 动作白名单 + 数量上限）、Dry Run 0 写、连续失败熔断（BR-13）。
TASK-013：周界计算（BR-01）、四态机流转（ALLOWED_TRANSITIONS 驱动）、
硬/软上限（BR-08）、粒度（NOT_A_CHOICE）、自审拒绝（BR-04）、驳回意见
必填、approve 锁定 + refresh_summary freeze=True、revoke 解锁、台账行级
权限（成员只见本人）。
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch as mpatch

import pytest

from plane.db.models import (
    AutomationRule,
    AutomationRun,
    Issue,
    Project,
    ProjectMember,
    ProjectRole,
    State,
    User,
    WorkLog,
    WorkLogApproval,
    WorkLogSummary,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)
from plane.db.seeds.project_states import seed_project_states
from plane.db.services.worklog_approval import (
    WorkLogApprovalError,
    _week_start,
    review,
    soft_limit_warning,
    submit,
)
from plane.workflow.automation import (
    event_gate,
    match_trigger,
)
from plane.workflow.automation_service import (
    dry_run,
    invalidate_rules_cache,
    run_event,
    validate_rule_definition,
)

pytestmark = pytest.mark.django_db


# ── 共享夹具 ─────────────────────────────────────────────────
@pytest.fixture()
def env(db):
    owner = User.objects.create_user(email="r3-owner@rabbit.dev", password="Rabbit123!",
                                     display_name="负责人")
    member = User.objects.create_user(email="r3-member@rabbit.dev", password="Rabbit123!",
                                      display_name="成员")
    ws = Workspace.objects.create(name="W", slug=f"w-r3-{owner.id.hex[:8]}",
                                  owner=owner, created_by=owner)
    for u, r in ((owner, WorkspaceRole.OWNER), (member, WorkspaceRole.MEMBER)):
        WorkspaceMember.objects.create(workspace=ws, member=u, role=r, created_by=owner)
    proj = Project.objects.create(name="P", identifier="R3P", workspace=ws, created_by=owner)
    ProjectMember.objects.create(project=proj, member=owner, role=ProjectRole.ADMIN,
                                 created_by=owner)
    ProjectMember.objects.create(project=proj, member=member, role=ProjectRole.CONTRIBUTOR,
                                 created_by=owner)
    seed_project_states(proj)
    return {"owner": owner, "member": member, "ws": ws, "proj": proj,
            "states": {s.name: s for s in State.objects.filter(project=proj)}}


# ════════════════════════════════════════════════════════════════
# WF-003 自动化规则
# ════════════════════════════════════════════════════════════════
class TestAutomationTriggers:
    def test_state_changed_match(self, env):
        rule = {"type": "state_changed", "config": {"to_group": "started"}}
        assert match_trigger(rule, {"type": "state_changed",
                                    "payload": {"to_state_id": "x", "to_group": "started"}})
        assert not match_trigger(rule, {"type": "state_changed",
                                        "payload": {"to_group": "completed"}})

    def test_field_changed_match(self, env):
        rule = {"type": "field_changed", "config": {"fields": ["priority", "assignees"]}}
        assert match_trigger(rule, {"type": "field_changed",
                                    "payload": {"field": "priority", "new_value": "high"}})
        assert not match_trigger(rule, {"type": "field_changed",
                                        "payload": {"field": "target_date"}})

    def test_issue_types_filter(self, env):
        rule = {"type": "state_changed", "config": {"issue_types": ["req-uuid"]}}
        assert not match_trigger(rule, {"type": "state_changed",
                                        "payload": {"issue_type_id": None}})


class TestEventGate:
    def test_origin_blocks_automation_chain(self, env):
        assert event_gate({"type": "state_changed", "origin": "automation:1",
                            "chain_depth": 0}) == "origin"
        assert event_gate({"type": "field_changed", "origin": "automation:1",
                            "chain_depth": 0}) == "origin"
        # due_approaching 不受闸 1（豁免）
        assert event_gate({"type": "due_approaching", "origin": "automation:1",
                            "chain_depth": 0}) is None

    def test_chain_depth_melting(self, env):
        assert event_gate({"type": "state_changed", "chain_depth": 5,
                            "origin": ""}) == "chain_depth"
        assert event_gate({"type": "state_changed", "chain_depth": 4,
                            "origin": ""}) is None


class TestValidateRuleDefinition:
    def test_unknown_trigger(self):
        issues = validate_rule_definition({"trigger": {"type": "weird"},
                                          "actions": [{"type": "set_field",
                                                      "config": {"field": "priority",
                                                                "value": "high"}}]})
        assert any(i["code"] == "NOT_A_CHOICE" and "trigger" in i["field"]
                   for i in issues)

    def test_action_limit(self):
        actions = [{"type": "set_field", "config": {"field": "priority",
                     "value": "high"}}] * 6
        issues = validate_rule_definition({"trigger": {"type": "state_changed"},
                                          "actions": actions})
        assert any(i["code"] == "LIMIT" for i in issues)

    def test_empty_actions(self):
        issues = validate_rule_definition({"trigger": {"type": "state_changed"},
                                          "actions": []})
        assert any(i["code"] == "REQUIRED" for i in issues)

    def test_due_approaching_hours_range(self):
        issues = validate_rule_definition({
            "trigger": {"type": "due_approaching", "config": {"hours_before": 0}},
            "actions": [{"type": "notify", "config": {"targets": ["assignees"]}}]})
        assert any(i["code"] == "INVALID" and "hours_before" in i["field"] for i in issues)


class TestDryRunZeroWrite:
    def test_dry_run_does_not_persist(self, env):
        rule = AutomationRule.objects.create(
            project=env["proj"], name="R",
            trigger={"type": "state_changed", "config": {"to_group": "started"}},
            conditions=[], actions=[{"type": "set_field", "config":
                                     {"field": "priority", "value": "high"}}],
            created_by=env["owner"])
        issue = Issue.objects.create(project=env["proj"], name="i",
                                    state=env["states"]["待办"], created_by=env["owner"])
        before_runs = AutomationRun.objects.count()
        out = dry_run(rule=rule, issue=issue,
                      event={"type": "state_changed", "project_id": str(env["proj"].id),
                             "issue_id": str(issue.id),
                             "payload": {"to_group": "started"}, "chain_depth": 0, "origin": ""})
        assert out["matched"] is True
        assert out["plan"][0]["type"] == "set_field"
        assert AutomationRun.objects.count() == before_runs  # BR-11 零写


class TestRunEventIntegration:
    def test_state_changed_triggers_set_field(self, env):
        """端到端：state_changed 事件 → 条件求值命中 → 动作执行 → run=success。"""
        rule = AutomationRule.objects.create(
            project=env["proj"], name="高优进评审",
            trigger={"type": "state_changed", "config": {"to_group": "started"}},
            conditions=[{"field": "priority", "operator": "in",
                         "value": ["high", "urgent"]}],
            actions=[{"type": "set_field", "config":
                      {"field": "estimate_minutes", "value": 30}}],
            created_by=env["owner"])
        invalidate_rules_cache(env["proj"].id)
        issue = Issue.objects.create(
            project=env["proj"], name="i", state=env["states"]["待办"],
            priority=Issue.Priority.HIGH, created_by=env["owner"])
        summary = run_event({
            "type": "state_changed", "project_id": str(env["proj"].id),
            "issue_id": str(issue.id), "chain_depth": 0, "origin": "",
            "payload": {"to_group": "started", "from_state_id": str(env["states"]["待办"].id)}})
        assert summary["matched"] == 1 and summary["ran"] == 1
        issue.refresh_from_db()
        assert issue.estimate_minutes == 30
        run = AutomationRun.objects.get(rule=rule, issue=issue)
        assert run.status == AutomationRun.Status.SUCCESS

    def test_chain_blocked_with_allow_rule_chain_off(self, env):
        AutomationRule.objects.create(
            project=env["proj"], name="链锁",
            trigger={"type": "state_changed", "config": {"to_group": "started"}},
            actions=[{"type": "set_field", "config": {"field": "priority",
                                                       "value": "low"}}],
            created_by=env["owner"])
        issue = Issue.objects.create(
            project=env["proj"], name="i", state=env["states"]["待办"],
            created_by=env["owner"])
        summary = run_event({
            "type": "state_changed", "project_id": str(env["proj"].id),
            "issue_id": str(issue.id), "chain_depth": 1,
            "origin": "automation:prev", "payload": {"to_group": "started"}})
        assert summary["matched"] == 0  # 闸 1 + 闸 2 同侧 1 闸即拦

    def test_transition_action_runs_engine(self, env):
        """transition 动作走 WF-001 引擎完整路径（守卫照常）。"""
        AutomationRule.objects.create(
            project=env["proj"], name="auto-submit",
            trigger={"type": "state_changed", "config": {"to_group": "started"}},
            actions=[{"type": "transition", "config":
                      {"to_state_id": str(env["states"]["进行中"].id)}}],
            created_by=env["owner"])
        invalidate_rules_cache(env["proj"].id)
        # 任务在 待办→进行中 间无受控边（无工作流）→ 自由流转通过
        issue = Issue.objects.create(
            project=env["proj"], name="i", state=env["states"]["待办"],
            created_by=env["owner"])
        summary = run_event({
            "type": "state_changed", "project_id": str(env["proj"].id),
            "issue_id": str(issue.id), "chain_depth": 0, "origin": "",
            "payload": {"to_group": "started"}})
        assert summary["ran"] == 1
        issue.refresh_from_db()
        assert issue.state.name == "进行中"

    def test_consecutive_failures_circuit_breaker(self, env):
        rule = AutomationRule.objects.create(
            project=env["proj"], name="boom",
            trigger={"type": "state_changed", "config": {"to_group": "started"}},
            actions=[{"type": "transition", "config":
                      {"to_state_id": "nonexistent-uuid"}}],  # 必失败
            created_by=env["owner"])
        invalidate_rules_cache(env["proj"].id)
        issue = Issue.objects.create(
            project=env["proj"], name="i", state=env["states"]["待办"],
            created_by=env["owner"])
        # 10 次失败应触发熔断：每轮只重置 is_active（保持计数递增），BR-13 真实
        # 意图是连续 10 次失败后**新事件**不再匹配该规则
        for _ in range(10):
            run_event({
                "type": "state_changed", "project_id": str(env["proj"].id),
                "issue_id": str(issue.id), "chain_depth": 0, "origin": "",
                "payload": {"to_group": "started"}})
            rule.refresh_from_db()
            if rule.consecutive_failures >= 10:
                assert rule.is_active is False
                break
            # 模拟跨日重置：仅重新启用（计数保持——BR-13 是「连续」累计）
            AutomationRule.objects.filter(pk=rule.pk).update(is_active=True)
            invalidate_rules_cache(env["proj"].id)
        assert rule.consecutive_failures == 10


# ════════════════════════════════════════════════════════════════
# TASK-013 工时审批
# ════════════════════════════════════════════════════════════════
def _log(env, *, actor, issue, minutes, worked_on, locked=False):
    return WorkLog.objects.create(
        issue=issue, actor=actor, worked_on=worked_on, minutes=minutes,
        locked=locked, created_by=actor, updated_by=actor)


def _week_monday() -> date:
    return _week_start(date.today())


class TestWeekBoundaryAndSoftLimit:
    def test_week_start_monday(self):
        # 周二 → 周一（2026-09-01 是周二）
        assert _week_start(date(2026, 9, 1)) == date(2026, 8, 31)
        # 周日 → 本周一
        assert _week_start(date(2026, 9, 6)) == date(2026, 8, 31)

    def test_soft_limit_warning_collects_overage_days(self, env):
        ws = _week_monday()
        issue = Issue.objects.create(
            project=env["proj"], name="i", state=env["states"]["待办"],
            created_by=env["owner"])
        _log(env, actor=env["member"], issue=issue, minutes=500, worked_on=ws)
        warnings = soft_limit_warning(env["member"].id, env["proj"].id, ws, 480)
        assert warnings and warnings[0]["total_minutes"] == 500


class TestWorkLogApprovalFSM:
    def test_submit_requires_logs(self, env):
        with pytest.raises(WorkLogApprovalError) as ei:
            submit(actor=env["member"], project=env["proj"], week_start=_week_monday())
        assert ei.value.status == 400
        assert ei.value.sub == "REQUIRED" or "REQUIRED" in (ei.value.message or "")

    def test_submit_then_approve_freezes_and_locks(self, env):
        ws = _week_monday()
        issue = Issue.objects.create(
            project=env["proj"], name="i", state=env["states"]["待办"],
            created_by=env["owner"])
        _log(env, actor=env["member"], issue=issue, minutes=60, worked_on=ws)
        batch = submit(actor=env["member"], project=env["proj"], week_start=ws)
        assert batch.status == WorkLogApproval.Status.SUBMITTED
        # 不可自审
        with pytest.raises(WorkLogApprovalError) as ei:
            review(batch_id=batch.id, reviewer=env["member"], action="approve")
        assert "自己" in ei.value.message or "self" in ei.value.message.lower() or "VALIDATION_ERROR" == ei.value.code
        # owner 审批通过
        batch = review(batch_id=batch.id, reviewer=env["owner"], action="approve")
        issue_log = WorkLog.objects.get(actor=env["member"], issue=issue)
        assert issue_log.locked is True  # BR-06
        assert WorkLogSummary.objects.get(
            project=env["proj"], actor=env["member"], week_start=ws).is_frozen is True  # BR-13

    def test_approve_then_revoke_unlocks(self, env):
        ws = _week_monday()
        issue = Issue.objects.create(
            project=env["proj"], name="i", state=env["states"]["待办"],
            created_by=env["owner"])
        _log(env, actor=env["member"], issue=issue, minutes=60, worked_on=ws)
        batch = submit(actor=env["member"], project=env["proj"], week_start=ws)
        review(batch_id=batch.id, reviewer=env["owner"], action="approve")
        # 撤销
        review(batch_id=batch.id, reviewer=env["owner"], action="revoke", note="审计调整")
        issue_log = WorkLog.objects.get(actor=env["member"], issue=issue)
        assert issue_log.locked is False  # BR-07
        assert WorkLogSummary.objects.get(
            project=env["proj"], actor=env["member"], week_start=ws).is_frozen is False

    def test_reject_requires_note(self, env):
        ws = _week_monday()
        issue = Issue.objects.create(
            project=env["proj"], name="i", state=env["states"]["待办"],
            created_by=env["owner"])
        _log(env, actor=env["member"], issue=issue, minutes=60, worked_on=ws)
        batch = submit(actor=env["member"], project=env["proj"], week_start=ws)
        with pytest.raises(WorkLogApprovalError) as ei:
            review(batch_id=batch.id, reviewer=env["owner"], action="reject", note=" ")
        assert ei.value.sub == "REQUIRED" or "REQUIRED" in (ei.value.message or "")
        # 驳回后回到 rejected，可重新提交
        batch = review(batch_id=batch.id, reviewer=env["owner"], action="reject", note="补正明细")
        assert batch.status == WorkLogApproval.Status.REJECTED
        batch = submit(actor=env["member"], project=env["proj"], week_start=ws)
        assert batch.status == WorkLogApproval.Status.SUBMITTED

    def test_frozen_summary_rejects_daily_refresh(self, env):
        ws = _week_monday()
        issue = Issue.objects.create(
            project=env["proj"], name="i", state=env["states"]["待办"],
            created_by=env["owner"])
        _log(env, actor=env["member"], issue=issue, minutes=60, worked_on=ws)
        batch = submit(actor=env["member"], project=env["proj"], week_start=ws)
        review(batch_id=batch.id, reviewer=env["owner"], action="approve")
        s_frozen = WorkLogSummary.objects.get(
            project=env["proj"], actor=env["member"], week_start=ws)
        assert s_frozen.is_frozen is True
        # 日常增量重算（freeze=False）：is_frozen 守卫触发 skip，不变更 frozen 行
        _log(env, actor=env["member"], issue=issue, minutes=30, worked_on=ws + timedelta(days=1))
        s_after = WorkLogSummary.objects.get(
            project=env["proj"], actor=env["member"], week_start=ws)
        assert s_after.is_frozen is True
        assert s_after.total_minutes == 60  # 不被新工时覆写（BR-13 守住）


# ════════════════════════════════════════════════════════════════
# 突变自检（破坏 → 红 → 恢复 → 绿）
# ════════════════════════════════════════════════════════════════
class TestMutationReverts:
    def test_soft_limit_mutation_red_then_green(self, env):
        """突变自检：把周期解析破坏成无数据窗口 → 软上限警告恒空（红）→ 恢复 → 绿。"""
        from datetime import date as _date
        ws = _week_monday()
        issue = Issue.objects.create(
            project=env["proj"], name="i", state=env["states"]["待办"],
            created_by=env["owner"])
        _log(env, actor=env["member"], issue=issue, minutes=500, worked_on=ws)
        # 突变：周期窗口偏移到无数据的过去 → 聚合恒空 → warnings 恒空
        import plane.db.services.worklog_approval as _wla

        with mpatch.object(_wla, "_week_range",
                           return_value=(_date(2020, 1, 6), _date(2020, 1, 12))):
            broken = soft_limit_warning(env["member"].id, env["proj"].id, ws, 480)
            assert broken == []  # 突变成立：测试若只信实现输出则永远绿——下面的恢复段证明数据面为真
        # 恢复后再断言
        warnings = soft_limit_warning(env["member"].id, env["proj"].id, ws, 480)
        assert warnings and warnings[0]["total_minutes"] == 500
