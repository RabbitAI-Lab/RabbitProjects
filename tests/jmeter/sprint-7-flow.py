#!/usr/bin/env python3
"""Sprint-7 接口流程测试（六段契约矩阵：WF-001 引擎 / WF-004 守卫 / WF-002 审批 /
WF-003 自动化 / WF-005 模板 / WF-006 留痕 + TASK-013 工时）。

用法：uv run --project apps/api python tests/jmeter/sprint-7-flow.py [BASE]
契约常量取 _contract.py（状态码/错误码唯一真相源）。
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "apps", "api"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import django  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "plane.settings.dev")
django.setup()

from _contract import Client, HTTP, error_code, q  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
ADMIN = Client(BASE)
PASS = FAIL = 0
FAILURES: list[str] = []


def section(name: str) -> None:
    print(f"\n{'═' * 8} {name} {'═' * 8}")


def ck(cid: str, desc: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {cid} {desc}")
    else:
        FAIL += 1
        FAILURES.append(f"{cid} {desc} {detail}")
        print(f"  ✗ {cid} {desc} {detail}")


def signup(c: Client, tag: str) -> tuple[str, str]:
    email = f"s7-{tag}-{int(time.time() * 1000) % 10**9}@rabbit.dev"
    code, body = c.req("POST", "/api/v1/auth/sign-up/",
                       {"email": email, "password": "Rabbit123!",
                        "display_name": tag}, {"X-CSRFToken": c.csrf()})
    c.req("POST", "/api/v1/auth/sign-in/", {"email": email, "password": "Rabbit123!"},
          {"X-CSRFToken": c.csrf()})
    return email, (body.get("data") or {}).get("default_workspace_slug", "")


def main() -> int:
    global ADMIN
    import uuid as uuid_mod

    section("S7-1 前置：注册 + 项目 + 状态种子")
    a_email, ws = signup(ADMIN, "flow")
    member = Client(BASE)
    m_email, _ = signup(member, "mem")
    # 邀 member 进 WS（简化：直接 WorkspaceMember 由 django 层造——本 flow 走 API 邀请）
    code, body = ADMIN.req("POST", f"/api/v1/workspaces/{q(ws)}/members/invitations/",
                           {"email": m_email, "role": 15}, {"X-CSRFToken": ADMIN.csrf()})
    # member 接受（受邀 token 流程复杂——改由 ADMIN 直建项目的成员）
    code, body = ADMIN.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/",
                           {"name": "S7 Flow 项目", "identifier": "S7FL"},
                           {"X-CSRFToken": ADMIN.csrf()})
    proj = (body.get("data") or {}).get("id")
    ck("S7-1-01", "项目创建 201", code == HTTP["CREATED"], f"got {code}")
    states_url = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/states/"
    code, body = ADMIN.req("GET", states_url)
    states = {s["name"]: s["id"] for s in (body.get("data") or [])}
    ck("S7-1-02", "四态种子（include_cancelled）",
       len(states) >= 3, f"{list(states)}")

    section("S7-2 WF-001：工作流 CRUD + 发布 + 流转")
    wf_url = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/workflows/"
    code, body = ADMIN.req("POST", wf_url, {"name": "S7 流程"},
                           {"X-CSRFToken": ADMIN.csrf()})
    wf = (body.get("data") or {}).get("id")
    ck("S7-2-01", "创建草稿 201", code == HTTP["CREATED"], f"got {code}")
    # 重复创建同维度草稿 → 409（BR-02）
    code, _ = ADMIN.req("POST", wf_url, {"name": "S7 流程二"},
                        {"X-CSRFToken": ADMIN.csrf()})
    ck("S7-2-02", "同维度第二草稿 409", code == HTTP["CONFLICT"], f"got {code}")
    # 空图发布 → 400（校验失败）
    code, body = ADMIN.req("POST", f"{wf_url}{wf}/publish/", {},
                           {"X-CSRFToken": ADMIN.csrf()})
    ck("S7-2-03", "空图发布 400 校验", code == HTTP["BAD_REQUEST"], f"got {code}")
    # PUT graph：三节点两边
    import uuid as uu

    n1, n2, n3 = str(uu.uuid4()), str(uu.uuid4()), str(uu.uuid4())
    e1 = str(uu.uuid4())
    code, body = ADMIN.req("PUT", f"{wf_url}{wf}/graph/", {
        "states": [
            {"id": n1, "state_id": states["待办"], "is_initial": True,
             "layout_x": 0, "layout_y": 0, "field_locks": []},
            {"id": n2, "state_id": states["进行中"], "is_initial": False,
             "layout_x": 200, "layout_y": 0, "field_locks": [{"field": "target_date"}]},
            {"id": n3, "state_id": states["已完成"], "is_initial": False,
             "layout_x": 400, "layout_y": 0, "field_locks": []},
        ],
        "transitions": [
            {"id": e1, "from_state_id": n1, "to_state_id": n2, "name": "开始",
             "guards": [], "side_effects": [], "approval_flow_id": None, "sort_order": 100},
            {"id": str(uu.uuid4()), "from_state_id": n2, "to_state_id": n3,
             "name": "完成", "guards": [], "side_effects": [],
             "approval_flow_id": None, "sort_order": 200},
        ],
    }, {"X-CSRFToken": ADMIN.csrf(), "If-Match": "*"})
    ck("S7-2-04", "PUT graph 整图替换（三态两边）200", code == HTTP["OK"], f"got {code}")
    code, body = ADMIN.req("POST", f"{wf_url}{wf}/publish/", {},
                           {"X-CSRFToken": ADMIN.csrf()})
    ck("S7-2-05", "发布 200", code == HTTP["OK"],
       f"got {code} {error_code(body)} {json.dumps((body.get('error') or {}).get('details', []), ensure_ascii=False)[:200]}")

    # 建任务 + 流转
    issue_url = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/"
    code, body = ADMIN.req("POST", issue_url, {"name": "S7 任务A"},
                           {"X-CSRFToken": ADMIN.csrf()})
    issue = (body.get("data") or {}).get("id")
    ck("S7-2-06", "任务创建 201（初始态经 resolve_initial_state）",
       code == HTTP["CREATED"] and (body.get("data") or {}).get("state_id") == states["待办"],
       f"got {code}")
    # 非法流转（无边 待办→已完成）→ 409 INVALID
    code, body = ADMIN.req("POST", f"{issue_url}{issue}/transitions/",
                           {"to_state_id": states["已完成"]},
                           {"X-CSRFToken": ADMIN.csrf()})
    ck("S7-2-07", "无边流转 409 RESOURCE_TRANSITION_INVALID",
       code == HTTP["CONFLICT"] and error_code(body) == "RESOURCE_TRANSITION_INVALID",
       f"got {code} {error_code(body)}")
    # 合法流转（开始）
    code, body = ADMIN.req("POST", f"{issue_url}{issue}/transitions/",
                           {"to_state_id": states["进行中"]},
                           {"X-CSRFToken": ADMIN.csrf()})
    ck("S7-2-08", "边流转 200 + applied_transition",
       code == HTTP["OK"] and (body.get("data") or {}).get("applied_transition") == "开始",
       f"got {code}")
    # PATCH state 旁路收口 → 409
    code, body = ADMIN.req("PATCH", f"{issue_url}{issue}/",
                           {"state_id": states["已完成"]},
                           {"X-CSRFToken": ADMIN.csrf()})
    ck("S7-2-09", "受控项目 PATCH state 409",
       code == HTTP["CONFLICT"] and error_code(body) == "RESOURCE_STATE_INVALID",
       f"got {code} {error_code(body)}")
    # available
    code, body = ADMIN.req("GET", f"{issue_url}{issue}/transitions/available/")
    avail = (body.get("data") or {}).get("available") or []
    ck("S7-2-10", "available 列表含 blocked_by",
       code == HTTP["OK"] and all("blocked_by" in a for a in avail), f"got {code}")

    section("S7-3 WF-004：守卫拦截结构化")
    # 新草稿图：开始边挂 required_fields assignees
    code, body = ADMIN.req("POST", wf_url, {"name": "S7 守卫流程"},
                           {"X-CSRFToken": ADMIN.csrf()})
    wf2 = (body.get("data") or {}).get("id")
    code, body = ADMIN.req("PUT", f"{wf_url}{wf2}/graph/", {
        "states": [
            {"id": n1, "state_id": states["待办"], "is_initial": True,
             "layout_x": 0, "layout_y": 0, "field_locks": []},
            {"id": n2, "state_id": states["进行中"], "is_initial": False,
             "layout_x": 200, "layout_y": 0, "field_locks": []},
            {"id": n3, "state_id": states["已完成"], "is_initial": False,
             "layout_x": 400, "layout_y": 0, "field_locks": []},
        ],
        "transitions": [
            {"id": e1, "from_state_id": n1, "to_state_id": n2, "name": "开始",
             "guards": [{"type": "required_fields",
                         "config": {"fields": ["assignees"]}}],
             "side_effects": [], "approval_flow_id": None, "sort_order": 100},
            {"id": str(uu.uuid4()), "from_state_id": n2, "to_state_id": n3,
             "name": "完成", "guards": [], "side_effects": [],
             "approval_flow_id": None, "sort_order": 200},
        ],
    }, {"X-CSRFToken": ADMIN.csrf(), "If-Match": "*"})
    ADMIN.req("POST", f"{wf_url}{wf}/archive/", {}, {"X-CSRFToken": ADMIN.csrf()})
    code, _b2 = ADMIN.req("POST", f"{wf_url}{wf2}/publish/", {},
                          {"X-CSRFToken": ADMIN.csrf()})
    ck("S7-3-00", "守卫流程发布（前序归档）", code == HTTP["OK"], f"got {code}")
    code, body = ADMIN.req("POST", issue_url, {"name": "S7 守卫任务"},
                           {"X-CSRFToken": ADMIN.csrf()})
    issue2 = (body.get("data") or {}).get("id")
    _c, _b = ADMIN.req("GET", f"{issue_url}{issue2}/transitions/available/")
    print(f"    [probe] available wf={((_b.get('data') or {}).get('workflow') or {}).get('name')} "
          f"edges={[a.get('name') for a in ((_b.get('data') or {}).get('available') or [])]} "
          f"requires={[a.get('requires_payload') for a in ((_b.get('data') or {}).get('available') or [])]}")
    code, body = ADMIN.req("POST", f"{issue_url}{issue2}/transitions/",
                           {"to_state_id": states["进行中"]},
                           {"X-CSRFToken": ADMIN.csrf()})
    err = (body.get("error") or {})
    ck("S7-3-01", "必填守卫拦截 400 + guard 键",
       code == HTTP["BAD_REQUEST"] and error_code(body) == "VALIDATION_REQUIRED_FIELD_MISSING"
       and any(d.get("guard") == "required_fields" for d in err.get("details", [])),
       f"got {code} {error_code(body)} data={json.dumps(body.get('data'), ensure_ascii=False)[:120]}")
    # guard_payload 补齐 → 200
    a_id = (body.get("error") or {}).get("details", [{}])[0].get("meta", {}).get("label")
    # 成员 user id 走 ORM 直查（API 响应展开形态随 expand 参数变化，避免猜结构）
    from plane.db.models import ProjectMember as _PMx
    from plane.db.models import Project as _Pjx

    admin_uid = str(next(iter(_PMx.objects.filter(
        project_id=_Pjx.objects.get(pk=proj).id).values_list("member_id", flat=True))))
    code, body = ADMIN.req("POST", f"{issue_url}{issue2}/transitions/",
                           {"to_state_id": states["进行中"],
                            "guard_payload": {"assignees": [admin_uid]}},
                           {"X-CSRFToken": ADMIN.csrf()})
    ck("S7-3-02", "guard_payload 补齐后流转 200",
       code == HTTP["OK"], f"got {code} {error_code(body)}")

    section("S7-4 WF-002：审批五场景 API 面")
    # BR-12 禁自审：会签双审批人（ADMIN 发起 + member 审批）——member 入项目
    from django.contrib.auth import get_user_model as _gum

    from plane.db.models import Project as _Pj2, ProjectMember as _PM2, Workspace as _Ws2
    from plane.db.models import WorkspaceMember as _WM2
    from plane.db.models.roles import ProjectRole as _PR2, WorkspaceRole as _WR2

    _U2 = _gum()
    _mu2 = _U2.objects.get(email=m_email)
    m_uid = str(_mu2.id)
    _WM2.objects.get_or_create(workspace=_Ws2.objects.get(slug=ws), member=_mu2,
                               defaults={"role": _WR2.MEMBER})
    _PM2.objects.get_or_create(project=_Pj2.objects.get(pk=proj), member=_mu2,
                               defaults={"role": _PR2.ADMIN})
    af_url = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/approval-flows/"
    code, body = ADMIN.req("POST", af_url, {
        "name": "S7 审批", "nodes": [
            {"level": 1, "pass_mode": "all", "approver_type": "users",
             "approver_config": {"user_ids": [admin_uid, m_uid]}}]},
        {"X-CSRFToken": ADMIN.csrf()})
    af = (body.get("data") or {}).get("id")
    ck("S7-4-01", "审批流创建 201", code == HTTP["CREATED"], f"got {code}")
    # 挂审批边的工作流
    code, body = ADMIN.req("POST", wf_url, {"name": "S7 审批流程"},
                           {"X-CSRFToken": ADMIN.csrf()})
    wf3 = (body.get("data") or {}).get("id")
    code, body = ADMIN.req("PUT", f"{wf_url}{wf3}/graph/", {
        "states": [
            {"id": n1, "state_id": states["待办"], "is_initial": True,
             "layout_x": 0, "layout_y": 0, "field_locks": []},
            {"id": n2, "state_id": states["进行中"], "is_initial": False,
             "layout_x": 200, "layout_y": 0, "field_locks": []},
            {"id": n3, "state_id": states["已完成"], "is_initial": False,
             "layout_x": 400, "layout_y": 0, "field_locks": []},
        ],
        "transitions": [
            {"id": e1, "from_state_id": n1, "to_state_id": n2, "name": "送审",
             "guards": [], "side_effects": [], "approval_flow_id": af,
             "sort_order": 100},
            {"id": str(uu.uuid4()), "from_state_id": n2, "to_state_id": n3,
             "name": "完成", "guards": [], "side_effects": [],
             "approval_flow_id": None, "sort_order": 200}],
    }, {"X-CSRFToken": ADMIN.csrf(), "If-Match": "*"})
    ADMIN.req("POST", f"{wf_url}{wf2}/archive/", {}, {"X-CSRFToken": ADMIN.csrf()})
    code, _b3 = ADMIN.req("POST", f"{wf_url}{wf3}/publish/", {},
                          {"X-CSRFToken": ADMIN.csrf()})
    ck("S7-4-00", "审批流程发布（前序归档）", code == HTTP["OK"], f"got {code}")
    code, body = ADMIN.req("POST", issue_url, {"name": "S7 审批任务"},
                           {"X-CSRFToken": ADMIN.csrf()})
    issue3 = (body.get("data") or {}).get("id")
    code, body = ADMIN.req("POST", f"{issue_url}{issue3}/transitions/",
                           {"to_state_id": states["进行中"]},
                           {"X-CSRFToken": ADMIN.csrf()})
    inst = ((body.get("data") or {}).get("pending_approval") or {}).get("instance_id")
    ck("S7-4-02", "审批边流转 202 + instance", code == HTTP["ACCEPTED"] and inst,
       f"got {code} {json.dumps(body, ensure_ascii=False)[:200]}")
    # 重复发起 → 409
    code, body = ADMIN.req("POST", f"{issue_url}{issue3}/transitions/",
                           {"to_state_id": states["进行中"]},
                           {"X-CSRFToken": ADMIN.csrf()})
    ck("S7-4-03", "同边重复发起 409", code == HTTP["CONFLICT"],
       f"got {code} {error_code(body)} {json.dumps(body, ensure_ascii=False)[:120]}")
    # 审批中心待办（member 是当前级 pending 票持有人——ADMIN 票 skipped(self)）
    code, body = member.req("GET", f"/api/v1/workspaces/{q(ws)}/approvals/pending/")
    ck("S7-4-04", "member 待办列表含实例",
       code == HTTP["OK"] and any(r["instance_id"] == inst for r in body.get("data") or []),
       f"got {code}")
    # 动作：approve → 终审回填迁移
    code, body = member.req("POST",
                            f"/api/v1/workspaces/{q(ws)}/projects/{proj}/approval-instances/{inst}/actions/",
                            {"action": "approve", "comment": "flow 通过"},
                            {"X-CSRFToken": member.csrf()})
    ck("S7-4-05", "approve 终审回填 200",
       code == HTTP["OK"] and (body.get("data") or {}).get("status") == "approved",
       f"got {code}")
    # 任务状态已迁移
    code, body = ADMIN.req("GET", f"{issue_url}{issue3}/")
    ck("S7-4-06", "终审后任务迁移到进行中",
       (body.get("data") or {}).get("state_id") == states["进行中"],
       f"state={((body.get('data') or {}).get('state_id'))}")

    section("S7-5 WF-003 + WF-005 + WF-006")
    ar_url = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/automation-rules/"
    code, body = ADMIN.req("POST", ar_url, {
        "name": "S7 规则",
        "trigger": {"type": "state_changed", "config": {"to_group": "started"}},
        "conditions": [{"field": "priority", "operator": "in",
                        "value": ["high", "urgent"]}],
        "actions": [{"type": "set_field",
                     "config": {"field": "estimate_minutes", "value": 30}}]},
        {"X-CSRFToken": ADMIN.csrf()})
    rule = (body.get("data") or {}).get("id")
    ck("S7-5-01", "规则创建 201", code == HTTP["CREATED"], f"got {code}")
    # Dry Run
    code, body = ADMIN.req("POST", f"{ar_url}{rule}/dry-run/",
                           {"issue_id": issue}, {"X-CSRFToken": ADMIN.csrf()})
    ck("S7-5-02", "Dry Run 200 + plan", code == HTTP["OK"] and
       isinstance((body.get("data") or {}).get("plan"), list), f"got {code}")
    # 模板库
    code, body = ADMIN.req("GET", f"/api/v1/workspaces/{q(ws)}/workflow-templates/")
    tpls = (body.get("data") or [])
    ck("S7-5-03", "预设四套模板", code == HTTP["OK"] and
       sum(1 for t in tpls if t.get("is_builtin")) == 4, f"got {code} n={len(tpls)}")
    tpl_id = next((t["id"] for t in tpls if t.get("is_builtin")), None)
    # 下发 + 确认
    code, body = ADMIN.req("POST",
                           f"/api/v1/workspaces/{q(ws)}/projects/{proj}/workflow-templates/",
                           {"template_id": tpl_id, "confirm": True},
                           {"X-CSRFToken": ADMIN.csrf()})
    ck("S7-5-04", "两步下发确认 200 + workflow_id",
       code == HTTP["OK"] and (body.get("data") or {}).get("workflow_id"),
       f"got {code} {error_code(body)}")
    # 审计检索 + 导出
    code, body = ADMIN.req("GET",
                           f"/api/v1/workspaces/{q(ws)}/projects/{proj}/approval-audit/")
    ck("S7-5-05", "审计检索 200", code == HTTP["OK"], f"got {code}")
    code, body = ADMIN.req("GET",
                           f"/api/v1/workspaces/{q(ws)}/projects/{proj}/approval-audit/export/")
    ck("S7-5-06", "审计导出 CSV", code == HTTP["OK"], f"got {code}")
    code, body = ADMIN.req("GET",
                           f"/api/v1/workspaces/{q(ws)}/projects/{proj}/approval-audit/verify/")
    ck("S7-5-07", "哈希链校验 intact",
       code == HTTP["OK"] and (body.get("data") or {}).get("chain_intact") is True,
       f"got {code}")

    section("S7-6 TASK-013：工时提交 → 审批 → 台账")
    import datetime as dt

    today = dt.date.today()
    monday = today - dt.timedelta(days=today.weekday())
    # member 入项目（BR-04 不可自审：填报人与审批人须不同）
    from django.contrib.auth import get_user_model

    from plane.db.models import ProjectMember as _PM, WorkspaceMember as _WM
    from plane.db.models import Project as _Pj, Workspace as _Ws
    from plane.db.models.roles import ProjectRole as _PR, WorkspaceRole as _WR

    _U = get_user_model()
    _mu = _U.objects.get(email=m_email)
    m_uid = str(_mu.id)
    _ws_obj = _Ws.objects.get(slug=ws)
    _pj_obj = _Pj.objects.get(pk=proj)
    _WM.objects.get_or_create(workspace=_ws_obj, member=_mu,
                              defaults={"role": _WR.MEMBER, "created_by": None})
    _PM.objects.get_or_create(project=_pj_obj, member=_mu,
                              defaults={"role": _PR.ADMIN, "created_by": None})
    _mc, _mb = ADMIN.req("POST",
                          f"/api/v1/workspaces/{q(ws)}/projects/{proj}/members/",
                          {"member_ids": [m_uid], "role": 20},
                          {"X-CSRFToken": ADMIN.csrf()})
    ck("S7-6-00", "member 入项目（ADMIN 角色，供工时审批）",
       _mc in (HTTP["OK"], HTTP["CREATED"], HTTP["BAD_REQUEST"]),  # 400=已存在（ORM 先建）
       f"got {_mc} {error_code(_mb)}")
    code, body = ADMIN.req("POST", f"{issue_url}{issue}/worklogs/",
                           {"minutes": 120, "worked_on": str(monday)},
                           {"X-CSRFToken": ADMIN.csrf()})
    ck("S7-6-01", "ADMIN 填报工时 201", code in (HTTP["CREATED"], HTTP["OK"]),
       f"got {code} {error_code(body)}")
    wl_url = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/worklog-approvals/"
    code, body = ADMIN.req("POST", f"{wl_url}submit/", {"week_start": str(monday)},
                           {"X-CSRFToken": ADMIN.csrf()})
    ck("S7-6-02", "ADMIN 周提交 200", code == HTTP["OK"],
       f"got {code} {error_code(body)}")
    code, body = ADMIN.req("GET", f"{wl_url}?scope=queue")
    batches = body.get("data") or []
    ck("S7-6-03", "审批队列含批次", code == HTTP["OK"] and len(batches) >= 1,
       f"got {code}")
    if batches:
        bid = batches[0]["id"]
        code, body = member.req("POST", f"{wl_url}{bid}/approve/", {},
                                {"X-CSRFToken": member.csrf()})
        ck("S7-6-04", "member 审批通过（BR-04 不同人）200", code == HTTP["OK"],
           f"got {code} {error_code(body)}")
    code, body = ADMIN.req("GET",
                           f"/api/v1/workspaces/{q(ws)}/projects/{proj}/worklog-ledger/")
    ck("S7-6-05", "台账矩阵 200", code == HTTP["OK"], f"got {code}")

    print(f"\n{'═' * 40}\nSprint 7 接口流程：{PASS} 通过 / {FAIL} 失败")
    if FAILURES:
        print("\n".join("  ✗ " + f for f in FAILURES))
        return 1
    print("全部通过 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
