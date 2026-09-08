#!/usr/bin/env python3
"""Sprint-7 验收演示数据准备（幂等可重跑）。

建「S7 验收演示」项目并铺齐六幕录屏数据（幕映射见 SCENARIOS.md）：
  幕01 画布：已发布三态工作流（待办→评审中→已完成，评审边挂守卫）+ 可编辑草稿
  幕02 守卫：无负责人的任务（守卫弹窗补齐演示）
  幕03 审批：审批流（会签 lisi+zhangsan？——单审 lisi）挂「发布」边 + 任务
  幕04 自动化：state_changed 规则（高优→改优先级为 urgent）+ Dry Run 样本任务
  幕05 工时：lisi 的本周工时 2 笔（供提交→驳回→通过链）
  幕06 模板/审计：无需种子（模板 GET 幂等补种 + 审计链运行时产生）

账号：zhangsan（演示，ADMIN）+ lisi（CONTRIBUTOR，幕03/05 双身份）。
用法：python3 scripts/seed_acceptance_s7.py [http://localhost:8000]
"""
from __future__ import annotations

import datetime as dt
import sys
import uuid as uuid_mod

sys.path.insert(0, "tests/jmeter")
sys.path.insert(0, "apps/api")
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "plane.settings.dev")
django.setup()

from _contract import HTTP, Client, q

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
TAG = "S7 验收演示"
IDENT = "S7AC"


def main() -> None:
    admin = Client(BASE)
    code, body = admin.req("POST", "/api/v1/auth/sign-in/",
                           {"email": "zhangsan@rabbit.dev", "password": "Rabbit123"},
                           {"X-CSRFToken": admin.csrf()})
    assert code == HTTP["OK"], (code, body)
    data = body["data"]
    ws = data.get("workspace", {}).get("slug") or data["default_workspace_slug"]
    admin_uid = data["user"]["id"]

    # lisi：不存在则注册并邀请进 WS + 项目（幕03 审批人 / 幕05 填报人）
    from plane.db.models import Project, ProjectMember, User, WorkspaceMember
    from plane.db.models.roles import ProjectRole, WorkspaceRole
    lisi = User.objects.filter(email="lisi@rabbit.dev").first()
    if lisi is None:
        lisi = User.objects.create_user(email="lisi@rabbit.dev", password="Rabbit123!",
                                        display_name="李四")
        ws_obj = Project.objects.none().model and None  # placeholder
    from plane.db.models import Workspace as _WS
    ws_obj = _WS.objects.get(slug=ws)
    WorkspaceMember.objects.get_or_create(
        workspace=ws_obj, member=lisi,
        defaults={"role": WorkspaceRole.MEMBER, "created_by_id": admin_uid})
    # WS 默认任务类型（IssueWriteSerializer BR-15 兜底要求——无默认行时创建任务 400）
    from plane.db.models import IssueType
    IssueType.objects.get_or_create(
        workspace=ws_obj, is_default=True, deleted_at__isnull=True,
        defaults={"name": "任务", "created_by_id": admin_uid})

    # 幂等清场：软删旧 S7AC 项目
    old = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/?status=all")[1]["data"] or []
    for p in old:
        if p.get("identifier") == IDENT:
            admin.req("DELETE", f"/api/v1/workspaces/{q(ws)}/projects/{p['id']}/",
                      {}, {"X-CSRFToken": admin.csrf()})
    # ORM 清残留（软删行）
    from plane.db.models import Workflow, WorkflowState, WorkflowTransition
    for p in Project.objects.filter(identifier=IDENT):
        for w in Workflow.all_objects.filter(project=p):
            WorkflowTransition.objects.filter(workflow=w).delete()
            WorkflowState.objects.filter(workflow=w).delete()
        w.delete()
        p.hard_delete() if hasattr(p, "hard_delete") else p.delete()

    # 建项目
    code, body = admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/",
                           {"name": TAG, "identifier": IDENT},
                           {"X-CSRFToken": admin.csrf()})
    assert code in (HTTP["CREATED"], HTTP["OK"]), (code, body)
    proj = body["data"]["id"]
    ProjectMember.objects.get_or_create(
        project_id=proj, member=lisi,
        defaults={"role": ProjectRole.ADMIN, "created_by_id": admin_uid})
    print(f"  项目 {IDENT}（{proj}）；成员：zhangsan ADMIN / lisi ADMIN（审批双身份）")

    iu = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/"
    wu = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/workflows/"
    code, body = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/states/?include_cancelled=1")
    states = {s["name"]: s["id"] for s in body["data"]}
    print(f"  状态种子：{list(states)}")

    # ── 幕01：画布工作流（三态：待办→评审中→已完成；评审边挂守卫）──
    code, body = admin.req("POST", wu, {"name": "需求交付流"},
                           {"X-CSRFToken": admin.csrf()})
    wf = body["data"]["id"]
    n = {k: str(uuid_mod.uuid4()) for k in ("todo", "review", "done")}
    e = {k: str(uuid_mod.uuid4()) for k in ("submit", "pass")}
    code, body = admin.req("PUT", f"{wu}{wf}/graph/", {
        "states": [
            {"id": n["todo"], "state_id": states["待办"], "is_initial": True,
             "layout_x": 80, "layout_y": 200, "field_locks": []},
            {"id": n["review"], "state_id": states["进行中"], "is_initial": False,
             "layout_x": 360, "layout_y": 200,
             "field_locks": [{"field": "target_date"}]},
            {"id": n["done"], "state_id": states["已完成"], "is_initial": False,
             "layout_x": 640, "layout_y": 200, "field_locks": []},
        ],
        "transitions": [
            {"id": e["submit"], "from_state_id": n["todo"], "to_state_id": n["review"],
             "name": "提交评审", "sort_order": 100,
             "guards": [{"type": "required_fields",
                         "config": {"fields": ["assignees"]}}],
             "side_effects": [], "approval_flow_id": None},
            {"id": e["pass"], "from_state_id": n["review"], "to_state_id": n["done"],
             "name": "评审通过", "sort_order": 200,
             "guards": [], "side_effects": [], "approval_flow_id": None},
        ],
    }, {"X-CSRFToken": admin.csrf(), "If-Match": "*"})
    assert code == HTTP["OK"], ("graph", code, body)
    code, body = admin.req("POST", f"{wu}{wf}/publish/", {},
                           {"X-CSRFToken": admin.csrf()})
    assert code == HTTP["OK"], ("publish", code, body)
    print(f"  幕01 工作流已发布（v{body['data'].get('version')}，评审边挂 assignees 守卫 + 评审中锁 target_date）")

    # ── 幕02：守卫任务（无 assignee）──
    code, body = admin.req("POST", iu, {"name": "购物车重构——结算页"},
                           {"X-CSRFToken": admin.csrf()})
    g_issue = body["data"]["id"]
    print(f"  幕02 守卫任务（无负责人）：{g_issue}")

    # ── 幕03：审批工作流（评审中→已完成 挂 lisi 单审）──
    af_url = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/approval-flows/"
    code, body = admin.req("POST", af_url, {
        "name": "发布审批", "nodes": [
            {"level": 1, "pass_mode": "all", "approver_type": "users",
             "approver_config": {"user_ids": [str(lisi.id)]}}]},
        {"X-CSRFToken": admin.csrf()})
    af = body["data"]["id"]
    # 新草稿：待办→评审中（守卫）→已完成（lisi 审批）
    admin.req("POST", f"{wu}{wf}/archive/", {}, {"X-CSRFToken": admin.csrf()})
    code, body = admin.req("POST", wu, {"name": "需求交付流（审批版）"},
                           {"X-CSRFToken": admin.csrf()})
    wf2 = body["data"]["id"]
    m = {k: str(uuid_mod.uuid4()) for k in ("todo", "review", "done")}
    e2 = {k: str(uuid_mod.uuid4()) for k in ("submit", "release")}
    code, body = admin.req("PUT", f"{wu}{wf2}/graph/", {
        "states": [
            {"id": m["todo"], "state_id": states["待办"], "is_initial": True,
             "layout_x": 80, "layout_y": 200, "field_locks": []},
            {"id": m["review"], "state_id": states["进行中"], "is_initial": False,
             "layout_x": 360, "layout_y": 200, "field_locks": []},
            {"id": m["done"], "state_id": states["已完成"], "is_initial": False,
             "layout_x": 640, "layout_y": 200, "field_locks": []},
        ],
        "transitions": [
            {"id": e2["submit"], "from_state_id": m["todo"], "to_state_id": m["review"],
             "name": "提交评审", "sort_order": 100,
             "guards": [{"type": "required_fields",
                         "config": {"fields": ["assignees"]}}],
             "side_effects": [], "approval_flow_id": None},
            {"id": e2["release"], "from_state_id": m["review"], "to_state_id": m["done"],
             "name": "发布上线", "sort_order": 200,
             "guards": [], "side_effects": [], "approval_flow_id": af},
        ],
    }, {"X-CSRFToken": admin.csrf(), "If-Match": "*"})
    code, body = admin.req("POST", f"{wu}{wf2}/publish/", {},
                           {"X-CSRFToken": admin.csrf()})
    assert code == HTTP["OK"], ("publish2", code, body)
    # 幕03 任务：流转到评审中（待审批发起）——指派 lisi 过 assignees 守卫（状态写路径统一过守卫）
    code, body = admin.req("POST", iu, {"name": "支付网关接入",
                                        "assignee_ids": [str(lisi.id)]},
                           {"X-CSRFToken": admin.csrf()})
    a_issue = body["data"]["id"]
    code, body = admin.req("POST", f"{iu}{a_issue}/transitions/",
                           {"to_state_id": states["进行中"]},
                           {"X-CSRFToken": admin.csrf()})
    assert code == HTTP["OK"], ("to review", code, body)
    print(f"  幕03 审批任务（已到评审中，待发起发布审批）：{a_issue}")

    # ── 幕04：自动化规则（高优进 started → 优先级改 urgent）──
    ar_url = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/automation-rules/"
    code, body = admin.req("POST", ar_url, {
        "name": "高优需求提级", "trigger": {"type": "state_changed",
                                            "config": {"to_group": "started"}},
        "conditions": [{"field": "priority", "operator": "in",
                        "value": ["high"]}],
        "actions": [{"type": "set_field",
                     "config": {"field": "priority", "value": "urgent"}}]},
        {"X-CSRFToken": admin.csrf()})
    rule = body["data"]["id"]
    code, body = admin.req("POST", iu, {"name": "首页性能优化", "priority": "high"},
                           {"X-CSRFToken": admin.csrf()})
    r_issue = body["data"]["id"]
    print(f"  幕04 规则 + Dry Run 样本任务（high）：{rule} / {r_issue}")

    # ── 幕05：工时（lisi 本周 2 笔）──
    today = dt.datetime.now().astimezone().date()  # 本地时区当天（工时周口径）
    monday = today - dt.timedelta(days=today.weekday())
    lisi_c = Client(BASE)
    lisi_c.req("POST", "/api/v1/auth/sign-in/",
               {"email": "lisi@rabbit.dev", "password": "Rabbit123!"},
               {"X-CSRFToken": lisi_c.csrf()})
    for day, mins in ((0, 180), (1, 240)):
        code, body = lisi_c.req("POST", f"{iu}{a_issue}/worklogs/",
                                {"minutes": mins,
                                 "worked_on": str(monday + dt.timedelta(days=day)),
                                 "note": "接入联调"},
                                {"X-CSRFToken": lisi_c.csrf()})
        assert code in (HTTP["CREATED"], HTTP["OK"]), ("worklog", code, body)
    print(f"  幕05 lisi 本周工时 2 笔（{monday} 起 180+240 分钟）")

    print("  种子完成。工作流画布 URL：")
    print(f"    /{ws}/projects/{proj}/workflows/{wf2}/canvas")
    print("  幕01 画布用草稿（同 wf2）｜种子不预建草稿——录制幕01 时经 UI 克隆演示")


if __name__ == "__main__":
    main()
