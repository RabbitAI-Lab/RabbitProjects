#!/usr/bin/env python3
"""Sprint-2 验收演示数据准备（幂等可重跑）。

建「S2 验收演示」项目并铺齐 9 幕录屏所需数据：5 层任务树、依赖链、
工时、自定义字段、归档树、未指派任务。数据准备走 API（不属于被验收
场景本身），录屏场景全部从登录 UI 出发操作。

用法：python3 scripts/seed_acceptance.py [http://localhost:8000]
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, "tests/jmeter")
from _contract import Client, HTTP, q  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
TAG = "S2 验收演示"
IDENT = "S2AC"


def main() -> None:
    # 直接以演示账号（张三）为 owner 建——录屏登录演示账号即在默认 WS 项目列表看到，
    # 且演示账号为项目 ADMIN（强制完成等管理员通道可演示）
    admin = Client(BASE)
    code, body = admin.req(
        "POST", "/api/v1/auth/sign-in/",
        {"email": "zhangsan@rabbit.dev", "password": "Rabbit123"},
        {"X-CSRFToken": admin.csrf()})
    assert code == HTTP["OK"], (code, body)
    ws = body["data"]["workspace"]["slug"] if body["data"].get("workspace") else None
    if not ws:
        me = admin.req("GET", "/api/v1/users/me/")[1]["data"]
        ws = me["default_workspace_slug"]
    demo_uid = admin.req("GET", "/api/v1/users/me/")[1]["data"]["user"]["id"]

    # 幂等清场：软删字段（项目软删不级联字段——不先删会累积满 BR-10 的 50/WS 上限）→ 再删旧 S2AC 项目
    for scope in ("project", "global"):
        props = admin.req(
            "GET", f"/api/v1/workspaces/{q(ws)}/issue-properties/?scope={scope}")[1]["data"] or []
        for d in props:
            admin.req("DELETE", f"/api/v1/workspaces/{q(ws)}/issue-properties/{d['id']}/",
                      None, {"X-CSRFToken": admin.csrf()})
    old_list = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/")[1]["data"]
    for prj in old_list:
        if prj.get("identifier") == IDENT:
            admin.req("DELETE", f"/api/v1/workspaces/{q(ws)}/projects/{prj['id']}/",
                      None, {"X-CSRFToken": admin.csrf()})

    code, body = admin.req(
        "POST", f"/api/v1/workspaces/{q(ws)}/projects/",
        {"name": TAG, "identifier": IDENT}, {"X-CSRFToken": admin.csrf()})
    assert code == HTTP["CREATED"], (code, body)
    proj = body["data"]["id"]

    base = f"/api/v1/workspaces/{q(ws)}/projects/{proj}"
    st = admin.req("GET", base + "/states/?include_cancelled=1")[1]["data"]
    groups = {s["group"]: s["id"] for s in st}

    def mk(name, parent=None, **f):
        path = base + (f"/issues/{parent}/sub-issues/" if parent else "/issues/")
        payload = {"name": name, **f}
        c, b = admin.req("POST", path, payload, {"X-CSRFToken": admin.csrf()})
        assert c == HTTP["CREATED"], (name, c, b)
        return b["data"]

    # ── 幕1/2：5 层树（验收-导出功能）──────────────────────────────
    r1 = mk("验收-导出功能", priority="high", start_date="2026-09-01", target_date="2026-09-12")
    r2 = mk("验收-后端导出 API", parent=r1["id"], priority="high", state_id=groups["completed"])
    r3 = mk("验收-分页游标改造", parent=r2["id"])
    r4 = mk("验收-SQL 生成器抽象", parent=r3["id"])
    mk("验收-游标编码优化", parent=r4["id"])  # 第 5 层
    mk("验收-前端导出按钮", parent=r1["id"], target_date="2026-09-10")

    # ── 幕3：依赖链（前端联调 被两个未完成前置阻塞）──────────────────
    root = mk("验收-登录改造")
    pre1 = mk("验收-导出限流配置", parent=root["id"], state_id=groups["started"], priority="high")
    pre2 = mk("验收-导出灰度方案", parent=root["id"])
    blocked = mk("验收-前端联调", priority="high", target_date="2026-09-14")
    post = mk("验收-前端回归测试")
    # 以 blocked 为主语的成对语义：被限流/灰度阻塞（前置两）+ 阻塞回归测试（后置）+ 相关联导出功能
    for rid_, rel in ((pre1["id"], "is_blocked_by"), (pre2["id"], "is_blocked_by"),
                      (post["id"], "blocks"), (r1["id"], "relates_to")):
        c, b = admin.req("POST", f"{base}/issues/{blocked['id']}/relations/",
                         {"related_issue_id": rid_, "relation_type": rel},
                         {"X-CSRFToken": admin.csrf()})
        assert c == HTTP["CREATED"], (rel, c, b)

    # ── 幕4：工时（联调任务 est 240，已填 60+90 两笔）────────────────
    today = time.strftime("%Y-%m-%d")
    yday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
    admin.req("PATCH", f"{base}/issues/{blocked['id']}/",
              {"estimate_minutes": 240}, {"X-CSRFToken": admin.csrf()})
    admin.req("POST", f"{base}/issues/{blocked['id']}/worklogs/",
              {"minutes": 60, "worked_on": yday, "note": "联调环境搭建"},
              {"X-CSRFToken": admin.csrf()})
    wl = admin.req("POST", f"{base}/issues/{blocked['id']}/worklogs/",
                   {"minutes": 90, "worked_on": today, "note": "接口联调 + 缺陷修复"},
                   {"X-CSRFToken": admin.csrf()})
    assert wl[0] == HTTP["CREATED"], wl

    # ── 幕5：多执行人（导出功能派张三；回归测试留未指派供认领）────────
    admin.req("PUT", f"{base}/issues/{r1['id']}/assignees/",
              {"assignee_ids": [demo_uid]}, {"X-CSRFToken": admin.csrf()})

    # ── 幕6：自定义字段（严重等级 select + 上线日期 date + 公测 checkbox）──
    for payload in (
        {"name": "严重等级", "field_key": "cf_sev", "field_type": "select", "required": False,
         "options": [{"value": "critical", "label": "致命", "color": "#DC2626"},
                     {"value": "major", "label": "严重", "color": "#F59E0B"},
                     {"value": "minor", "label": "一般", "color": "#3B82F6"}]},
        {"name": "上线日期", "field_key": "cf_release", "field_type": "date"},
        {"name": "开启公测", "field_key": "cf_beta", "field_type": "checkbox"},
    ):
        c, b = admin.req("POST", f"{base}/issue-properties/", payload,
                         {"X-CSRFToken": admin.csrf()})
        assert c in (HTTP["CREATED"], HTTP["CONFLICT"]), (payload["name"], c, b)

    # ── 幕7：归档树（旧方案两节点已归档）+ 归档视图素材 ─────────────
    old = mk("验收-旧版导出方案", state_id=groups["completed"])
    mk("验收-旧方案文档", parent=old["id"], state_id=groups["completed"])
    admin.req("POST", f"{base}/issues/{old['id']}/archive/", {},
              {"X-CSRFToken": admin.csrf()})

    # ── 幕8：时间线素材（导出功能补一组多字段修改）───────────────────
    admin.req("PATCH", f"{base}/issues/{r1['id']}/",
              {"priority": "urgent", "target_date": "2026-09-15"},
              {"X-CSRFToken": admin.csrf()})

    print(f"✓ 数据就绪：WS={ws} 项目={TAG}（{IDENT}）")
    print(f"  幕内任务名前缀「验收-」；演示账号 zhangsan@rabbit.dev 已加入（CONTRIBUTOR）")


if __name__ == "__main__":
    main()
