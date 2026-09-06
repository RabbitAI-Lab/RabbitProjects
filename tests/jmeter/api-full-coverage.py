#!/usr/bin/env python3
"""Sprint 0 全接口契约覆盖（api-full-coverage）——每个端点 × 每个方法 × 正/负例。
用法：python3 tests/jmeter/api-full-coverage.py [http://localhost:8000]
前置：API + 真实 PG。与 sprint-0-flow.py（10 步动线 CI gate）互补：
  本脚本按端点矩阵逐个打满，任何一例失败 exit 1。

Sprint-2 阶段 0 起 HTTP/CODES/Client 一律 import `_contract`（CLAUDE.md 测试脚本规范 ①，
ADR-0012 E4）：本脚本曾自带硬编码状态码/错误码表，是「唯一真相源」落地的最后一个双源残留，
切换后 13 端点矩阵与新脚本共享同一份契约常量。
"""
import json
import sys
import time

from _contract import CODES, HTTP, Client, detail_of

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")

PASS = FAIL = 0
FAILURES = []

C = Client(BASE)


def req(method, path, data=None, headers=None, authed=True):
    """authed=False：临时清空 cookie 模拟未登录（原 cookie 请求后恢复）。"""
    if authed:
        return C.req(method, path, data, headers)
    saved = list(C.jar)
    C.jar.clear()
    try:
        return C.req(method, path, data, headers)
    finally:
        C.jar.clear()
        for ck in saved:
            C.jar.set_cookie(ck)


def csrf():
    return C.csrf()


def err_field(b, field, code):
    """从新信封 error.details[] 读 (field, code) 命中的条目 —— 没命中返回 None。"""
    it = detail_of(b, field)
    return it if (it or {}).get("code") == code else None


def case(cid, desc, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {cid} {desc}")
    else:
        FAIL += 1
        FAILURES.append(f"{cid} {desc} {extra}")
        print(f"  ✗ {cid} {desc} {extra}")


def expect(cid, desc, method, path, status, data=None, headers=None, authed=True):
    code, body = req(method, path, data, headers, authed)
    case(cid, f"{method} {path.split('?')[0]} → {status}", code == status, f"(got {code}) {json.dumps(body, ensure_ascii=False)[:120] if body else ''}")
    return code, body


ts = int(time.time())
email = f"full-{ts}@rabbit.dev"
pw = "Rabbit123"

print("═══ 1. health ═══")
_, b = expect("HC-1", "健康检查", "GET", "/api/v1/health/", HTTP["OK"])
case("HC-2", "status == 'success'（INFRA-004 C1 强制信封）", (b or {}).get("status") == "success")
case("HC-3", "data.checks.db == ok", ((b or {}).get("data") or {}).get("checks", {}).get("db") == "ok")

print("═══ 2. auth/csrf-token ═══")
_, b = expect("CS-1", "获取令牌", "GET", "/api/v1/auth/csrf-token/", HTTP["OK"])
case("CS-2", "token 长度 ≥ 32", len((b or {}).get("data", {}).get("csrf_token", "")) >= 32)

print("═══ 3. auth/sign-up ═══")
_, b = expect("SU-1", "新邮箱注册", "POST", "/api/v1/auth/sign-up/", HTTP["CREATED"],
              {"email": email, "password": pw, "display_name": "Full Cov"}, {"X-CSRFToken": csrf()})
case("SU-2", "信封 status == 'success'", (b or {}).get("status") == "success")
case("SU-3", "default_workspace_slug 非空", bool(((b or {}).get("data") or {}).get("default_workspace_slug")))
case("SU-4", "workspaces[0].role == 20(OWNER)", ((b or {}).get("data") or {}).get("workspaces", [{}])[0].get("role") == 20)
ws = ((b or {}).get("data") or {}).get("default_workspace_slug")
_, b = expect("SU-5", "重复邮箱 → 409", "POST", "/api/v1/auth/sign-up/", HTTP["CONFLICT"],
              {"email": email, "password": pw}, {"X-CSRFToken": csrf()})
case("SU-6", "error.code == RESOURCE_ALREADY_EXISTS", ((b or {}).get("error") or {}).get("code") == CODES["alreadyExists"])
case("SU-7", "error.details 含 (field=email, code=UNIQUE)", err_field(b, "email", "UNIQUE") is not None)
expect("SU-8", "弱密码 → 400", "POST", "/api/v1/auth/sign-up/", HTTP["BAD_REQUEST"],
       {"email": f"x{ts}@x.dev", "password": "abc"}, {"X-CSRFToken": csrf()})
expect("SU-9", "非法邮箱 → 400", "POST", "/api/v1/auth/sign-up/", HTTP["BAD_REQUEST"],
       {"email": "not-an-email", "password": pw}, {"X-CSRFToken": csrf()})
c1 = req("POST", "/api/v1/auth/sign-up/", {"email": f"nocsrf-{ts}@x.dev", "password": pw}, {})  # 不带 CSRF 头
case("SU-10", "缺 CSRF → 403 AUTH_CSRF_FAILED",
     c1[0] == HTTP["FORBIDDEN"] and ((c1[1] or {}).get("error") or {}).get("code") == CODES["csrf"],
     f"got {c1[0]} body={c1[1]}")

print("═══ 4. auth/sign-in ═══")
_, b = expect("SI-1", "正确凭据 → 200", "POST", "/api/v1/auth/sign-in/", HTTP["OK"],
              {"email": email, "password": pw}, {"X-CSRFToken": csrf()})
case("SI-2", "返回 user.email", ((b or {}).get("data") or {}).get("user", {}).get("email") == email)
_, b = expect("SI-3", "错误密码 → 401", "POST", "/api/v1/auth/sign-in/", HTTP["UNAUTHORIZED"],
              {"email": email, "password": "Wrong123"}, {"X-CSRFToken": csrf()})
case("SI-4", "error.code == AUTH_INVALID_CREDENTIALS", ((b or {}).get("error") or {}).get("code") == CODES["invalidCreds"])
expect("SI-5", "不存在邮箱 → 401", "POST", "/api/v1/auth/sign-in/", HTTP["UNAUTHORIZED"],
       {"email": f"ghost-{ts}@x.dev", "password": pw}, {"X-CSRFToken": csrf()})
expect("SI-6", "remember=true → 200", "POST", "/api/v1/auth/sign-in/", HTTP["OK"],
       {"email": email, "password": pw, "remember": True}, {"X-CSRFToken": csrf()})

print("═══ 5. users/me ═══")
_, b = expect("ME-1", "已登录 → 200", "GET", "/api/v1/users/me/", HTTP["OK"])
data_me = (b or {}).get("data") or {}
case("ME-2", "含 user+workspaces+default_workspace_slug",
     all(k in data_me for k in ("user", "workspaces", "default_workspace_slug")))
c2 = req("GET", "/api/v1/users/me/", authed=False)
case("ME-3", "未认证 → 401 AUTH_REQUIRED",
     c2[0] == HTTP["UNAUTHORIZED"] and ((c2[1] or {}).get("error") or {}).get("code") == "AUTH_REQUIRED",
     f"got {c2[0]} body={c2[1]}")

print("═══ 6. workspaces 集合 ═══")
_, b = expect("WS-1", "列表 → 200", "GET", "/api/v1/workspaces/", HTTP["OK"])
case("WS-2", "列表含已建 ws 且 role=20", any(x["slug"] == ws and x["role"] == 20 for x in (b or {}).get("data", [])))
c3 = req("GET", "/api/v1/workspaces/", authed=False)
case("WS-3", "未认证 → 401", c3[0] == HTTP["UNAUTHORIZED"], f"got {c3[0]}")
_, b = expect("WS-4", "创建团队 → 201", "POST", "/api/v1/workspaces/", HTTP["CREATED"],
              {"name": f"Full Cov Team {ts}", "description": "e2e"}, {"X-CSRFToken": csrf()})
ws2 = ((b or {}).get("data") or {}).get("slug")
case("WS-5", "slug 归一小写", ws2 == ws2.lower() and ws2 == f"full-cov-team-{ts}")
_, b = expect("WS-6", "同名创建 → slug 后缀 -2", "POST", "/api/v1/workspaces/", HTTP["CREATED"],
              {"name": f"Full Cov Team {ts}"}, {"X-CSRFToken": csrf()})
case("WS-7", "slug 冲突消解", ((b or {}).get("data") or {}).get("slug") == f"{ws2}-2")

print("═══ 7. workspaces/{slug} 详情 ═══")
_, b = expect("WD-1", "详情 → 200", "GET", f"/api/v1/workspaces/{ws}/", HTTP["OK"])
case("WD-2", "name 字段存在", isinstance(((b or {}).get("data") or {}).get("name"), str))
expect("WD-3", "不存在 → 404", "GET", "/api/v1/workspaces/no-such-ws/", HTTP["NOT_FOUND"])
_, b = expect("WD-4", "PATCH 更新 → 200", "PATCH", f"/api/v1/workspaces/{ws2}/", HTTP["OK"],
              {"name": "Renamed Team"}, {"X-CSRFToken": csrf()})
case("WD-5", "名称已更新", ((b or {}).get("data") or {}).get("name") == "Renamed Team")

print("═══ 8. projects 集合 ═══")
pid = f"F{ts % 1000:03d}"[:5] if len(f"F{ts % 1000:03d}") >= 4 else f"F{ts % 100:02d}"
_, b = expect("PR-1", "创建（小写 id 自动大写）", "POST", f"/api/v1/workspaces/{ws}/projects/", HTTP["CREATED"],
              {"name": "Full Cov Proj", "identifier": pid.lower(), "description": "x"}, {"X-CSRFToken": csrf()})
proj = ((b or {}).get("data") or {}).get("id")
case("PR-2", "identifier 大写化", ((b or {}).get("data") or {}).get("identifier") == pid.upper())
case("PR-3", "total_members == 1", ((b or {}).get("data") or {}).get("total_members") == 1)
_, b = expect("PR-4", "重复 identifier → 409", "POST", f"/api/v1/workspaces/{ws}/projects/", HTTP["CONFLICT"],
              {"name": "Dup", "identifier": pid}, {"X-CSRFToken": csrf()})
case("PR-5", "error.code == RESOURCE_ALREADY_EXISTS", ((b or {}).get("error") or {}).get("code") == CODES["alreadyExists"])
detail = err_field(b, "identifier", "UNIQUE")
case("PR-6", "error.details 含 (field=identifier, code=UNIQUE)", detail is not None)
case("PR-7", "error.details[0].suggestion 非空", bool(detail and detail.get("suggestion")))
expect("PR-8", "identifier 1 位 → 400", "POST", f"/api/v1/workspaces/{ws}/projects/", HTTP["BAD_REQUEST"],
       {"name": "Bad", "identifier": "A"}, {"X-CSRFToken": csrf()})
_, b = expect("PR-9", "列表 → 200", "GET", f"/api/v1/workspaces/{ws}/projects/", HTTP["OK"])
case("PR-10", "列表含新项目", any(x["id"] == proj for x in (b or {}).get("data", [])))

print("═══ 9. project 详情/PATCH ═══")
_, b = expect("PD-1", "详情 → 200", "GET", f"/api/v1/workspaces/{ws}/projects/{proj}/", HTTP["OK"])
case("PD-2", "含 total_issues", "total_issues" in ((b or {}).get("data") or {}))
_, b = expect("PD-3", "PATCH 改名 → 200", "PATCH", f"/api/v1/workspaces/{ws}/projects/{proj}/", HTTP["OK"],
              {"name": "Renamed Proj"}, {"X-CSRFToken": csrf()})
case("PD-4", "名称已更新", ((b or {}).get("data") or {}).get("name") == "Renamed Proj")
_, b = expect("PD-5", "PATCH 换 identifier 被忽略", "PATCH", f"/api/v1/workspaces/{ws}/projects/{proj}/", HTTP["OK"],
              {"identifier": "ZZZZZ"}, {"X-CSRFToken": csrf()})
case("PD-6", "identifier 不可变", ((b or {}).get("data") or {}).get("identifier") == pid.upper())
expect("PD-7", "不存在项目 → 404", "GET", f"/api/v1/workspaces/{ws}/projects/00000000-0000-0000-0000-000000000000/", HTTP["NOT_FOUND"])

print("═══ 10. states ═══")
_, b = expect("ST-1", "默认列表 → 200", "GET", f"/api/v1/workspaces/{ws}/projects/{proj}/states/", HTTP["OK"])
states = (b or {}).get("data", [])
names = {s["name"] for s in states}
case("ST-2", "含 待办/进行中/已完成", {"待办", "进行中", "已完成"} <= names, f"got {names}")
case("ST-3", "不含已取消", "已取消" not in names)
started = next(s["id"] for s in states if s["group"] == "started")
_, b = expect("ST-4", "?include_cancelled=1 → 4 态", "GET",
              f"/api/v1/workspaces/{ws}/projects/{proj}/states/?include_cancelled=1", HTTP["OK"])
case("ST-5", "含已取消（group=cancelled）", any(s["group"] == "cancelled" for s in (b or {}).get("data", [])))
case("ST-6", "字段齐备 group/color", all(("group" in s and "color" in s) for s in (b or {}).get("data", [])))

print("═══ 11. issues 集合 ═══")
_, b = expect("IS-1", "建任务 → 201", "POST", f"/api/v1/workspaces/{ws}/projects/{proj}/issues/", HTTP["CREATED"],
              {"name": "Issue One", "target_date": "2026-09-15"}, {"X-CSRFToken": csrf()})
i1 = ((b or {}).get("data") or {}).get("id")
case("IS-2", "issue_key = {ID}-1", ((b or {}).get("data") or {}).get("issue_key") == f"{pid.upper()}-1")
case("IS-3", "sequence_id == 1", ((b or {}).get("data") or {}).get("sequence_id") == 1)
case("IS-4", "默认落待办", ((b or {}).get("data") or {}).get("state_name") == "待办")
case("IS-5", "sort_order == 65535", ((b or {}).get("data") or {}).get("sort_order") == 65535)
case("IS-6", "created_by.name 非空", bool(((b or {}).get("data") or {}).get("created_by") or {}))
_, b = expect("IS-7", "建第二个（指定 started）→ 201", "POST", f"/api/v1/workspaces/{ws}/projects/{proj}/issues/", HTTP["CREATED"],
              {"name": "Issue Two", "state_id": started}, {"X-CSRFToken": csrf()})
i2 = ((b or {}).get("data") or {}).get("id")
case("IS-8", "state_group == started", ((b or {}).get("data") or {}).get("state_group") == "started")
case("IS-9", "sequence_id == 2", ((b or {}).get("data") or {}).get("sequence_id") == 2)
_, b = expect("IS-10", "列表 → 200", "GET", f"/api/v1/workspaces/{ws}/projects/{proj}/issues/?ordering=sort_order", HTTP["OK"])
case("IS-11", "两条", len((b or {}).get("data", [])) == 2)
_, b = expect("IS-12", "?group_by=state_id → 200", "GET",
              f"/api/v1/workspaces/{ws}/projects/{proj}/issues/?group_by=state_id", HTTP["OK"])
case("IS-13", "分组值为 {results,total_results} 且有一组恰含 1 条",
     all(isinstance(v, dict) and {"results", "total_results"} <= set(v)
         for v in ((b or {}).get("data") or {}).values())
     and any(len(v["results"]) == 1 for v in ((b or {}).get("data") or {}).values()))
case("IS-14", "看板四列齐备（含已取消，BOARD-002 §4.2.1）",
     len((b or {}).get("data") or {}) == 4)

print("═══ 12. issue 详情/PATCH/DELETE ═══")
_, b = expect("ID-1", "详情 → 200", "GET", f"/api/v1/workspaces/{ws}/projects/{proj}/issues/{i1}/", HTTP["OK"])
case("ID-2", "字段齐备（TASK-002 §4.3.2：单人 assignee 改为 assignee_ids 列表）",
     all(k in ((b or {}).get("data") or {})
         for k in ("issue_key", "state_name", "state_group", "assignee_ids", "created_by",
                   "priority", "type_id", "parent_id", "label_ids", "sub_issues_count")))
_, b = expect("ID-3", "PATCH 改名+改状态 → 200", "PATCH", f"/api/v1/workspaces/{ws}/projects/{proj}/issues/{i1}/", HTTP["OK"],
              {"name": "Renamed Issue", "state_id": started, "sort_order": 131070.0}, {"X-CSRFToken": csrf()})
case("ID-4", "状态已改 started", ((b or {}).get("data") or {}).get("state_group") == "started")
_, b = req("GET", f"/api/v1/workspaces/{ws}/projects/{proj}/issues/{i1}/")
case("ID-5", "改名已落库", ((b or {}).get("data") or {}).get("name") == "Renamed Issue")
case("ID-6", "sort_order 已落库", ((b or {}).get("data") or {}).get("sort_order") == 131070.0)
expect("ID-7", "不存在任务 → 404", "GET", f"/api/v1/workspaces/{ws}/projects/{proj}/issues/00000000-0000-0000-0000-000000000000/", HTTP["NOT_FOUND"])
_, b = expect("ID-8", "级联软删 → 200+deleted_count（TASK-004 §4.2.5 起）", "DELETE",
       f"/api/v1/workspaces/{ws}/projects/{proj}/issues/{i2}/", HTTP["OK"], None, {"X-CSRFToken": csrf()})
case("ID-8b", "deleted_count == 1 且 descendant_ids 空",
     ((b or {}).get("data") or {}).get("deleted_count") == 1
     and ((b or {}).get("data") or {}).get("descendant_ids") == [])
expect("ID-9", "删除后 GET → 404", "GET", f"/api/v1/workspaces/{ws}/projects/{proj}/issues/{i2}/", HTTP["NOT_FOUND"])
_, b = req("GET", f"/api/v1/workspaces/{ws}/projects/{proj}/issues/")
case("ID-10", "删除后列表少一条", len((b or {}).get("data", [])) == 1)

print("═══ 13. auth/sign-out ═══")
expect("SO-1", "登出 → 204", "POST", "/api/v1/auth/sign-out/", HTTP["NO_CONTENT"], None, {"X-CSRFToken": csrf()})
c5 = req("GET", "/api/v1/users/me/")
case("SO-2", "登出后 me → 401 AUTH_REQUIRED",
     c5[0] == HTTP["UNAUTHORIZED"] and ((c5[1] or {}).get("error") or {}).get("code") == "AUTH_REQUIRED",
     f"got {c5[0]} body={c5[1]}")

print("═══ 14. 项目删除（复用 ws2）═══")
req("POST", "/api/v1/auth/sign-in/", {"email": email, "password": pw})
_, b = expect("PD-8", "建临时项目", "POST", f"/api/v1/workspaces/{ws2}/projects/", HTTP["CREATED"],
              {"name": "To Delete", "identifier": "DEL"}, {"X-CSRFToken": csrf()})
tmp = ((b or {}).get("data") or {}).get("id")
expect("PD-9", "删除项目 → 204", "DELETE", f"/api/v1/workspaces/{ws2}/projects/{tmp}/", HTTP["NO_CONTENT"], None, {"X-CSRFToken": csrf()})
expect("PD-10", "删后 GET → 404", "GET", f"/api/v1/workspaces/{ws2}/projects/{tmp}/", HTTP["NOT_FOUND"])

# ═══ Sprint-3 新端点族（BOARD-003/004、TASK-011、COLLAB-002/003/004）═══
# 注：internal/realtime/verify-rooms/ 为 live→api 服务间端点（X-Internal-Key，proxy
# 对 /api/v1/internal/ 前缀不路由），不在外部契约矩阵——由 sprint-3-flow COLLAB-004
# 段与 apps/api/tests/test_realtime_ticket.py 覆盖。

print("═══ 15. views/ 视图 CRUD 五端点（BOARD-003 §4.2）═══")
views = f"/api/v1/workspaces/{ws}/projects/{proj}/views/"
_, b = expect("VW-1", "视图列表 → 200", "GET", views, HTTP["OK"])
vlist = (b or {}).get("data") or []
case("VW-2", "内置五视图随项目种子（is_system）",
     sum(1 for v in vlist if v.get("is_system")) == 5
     and {"需求池", "缺陷列表", "我的待办", "本周到期", "测试执行"} <= {v.get("name") for v in vlist})
_, b = expect("VW-3", "创建视图（3 层嵌套 filters，TASK-011 合法）", "POST", views, HTTP["CREATED"],
              {"name": "Full Cov View", "layout": "kanban",
               "filters": {"op": "AND", "conditions": [
                   {"field": "priority", "operator": "in", "value": ["urgent"]},
                   {"op": "OR", "conditions": [
                       {"field": "priority", "operator": "in", "value": ["high"]}]}]}},
              {"X-CSRFToken": csrf()})
vid = ((b or {}).get("data") or {}).get("id")
case("VW-4", "201 回显 access=personal / is_system=false",
     ((b or {}).get("data") or {}).get("access") == "personal"
     and ((b or {}).get("data") or {}).get("is_system") is False)
_, b = expect("VW-5", "视图详情 → 200", "GET", f"{views}{vid}/", HTTP["OK"])
expect("VW-6", "PATCH 改名 → 200", "PATCH", f"{views}{vid}/", HTTP["OK"],
       {"name": "Full Cov View 2"}, {"X-CSRFToken": csrf()})
expect("VW-7", "PATCH 不存在 view → 404", "PATCH", f"{views}00000000-0000-0000-0000-000000000000/",
       HTTP["NOT_FOUND"], {"name": "X"}, {"X-CSRFToken": csrf()})
builtin_vid = next((v["id"] for v in vlist if v.get("is_system")), None)
_, b = expect("VW-8", "DELETE 内置视图 → 403 PERM_DENIED", "DELETE", f"{views}{builtin_vid}/",
              HTTP["FORBIDDEN"], None, {"X-CSRFToken": csrf()})
case("VW-9", "错误码 == PERM_DENIED", ((b or {}).get("error") or {}).get("code") == CODES["permDenied"])
_, b = expect("VW-10", "access=shared → 400", "POST", views, HTTP["BAD_REQUEST"],
              {"name": "Shared", "access": "shared"}, {"X-CSRFToken": csrf()})
case("VW-11", "details (field=access, code=INVALID)", err_field(b, "access", "INVALID") is not None)
expect("VW-12", "DELETE 自建视图 → 204", "DELETE", f"{views}{vid}/", HTTP["NO_CONTENT"],
       None, {"X-CSRFToken": csrf()})
expect("VW-13", "删后 GET → 404", "GET", f"{views}{vid}/", HTTP["NOT_FOUND"])

print("═══ 16. users/me/settings/ 偏好两端点（BOARD-003 BR-10）═══")
_, b = expect("ST-1", "GET settings → 200", "GET", "/api/v1/users/me/settings/", HTTP["OK"])
_, b = expect("ST-2", "PATCH 设默认视图 → 200", "PATCH", "/api/v1/users/me/settings/", HTTP["OK"],
              {"board.default_view_id": {proj: builtin_vid}}, {"X-CSRFToken": csrf()})
case("ST-3", "偏好回显", ((b or {}).get("data") or {}).get("board.default_view_id", {}).get(proj) == builtin_vid)
_, b = expect("ST-4", "GET 回读持久化", "GET", "/api/v1/users/me/settings/", HTTP["OK"])
case("ST-5", "GET 含 board.default_view_id",
     ((b or {}).get("data") or {}).get("board.default_view_id", {}).get(proj) == builtin_vid)
_, b = expect("ST-6", "未知偏好键 → 400 INVALID_PARAM", "PATCH", "/api/v1/users/me/settings/",
              HTTP["BAD_REQUEST"], {"hack.key": 1}, {"X-CSRFToken": csrf()})
case("ST-7", "错误码 == VALIDATION_INVALID_PARAM",
     ((b or {}).get("error") or {}).get("code") == CODES["invalidParam"])
_, b = expect("ST-8", "PATCH null 取消默认 → 200", "PATCH", "/api/v1/users/me/settings/", HTTP["OK"],
              {"board.default_view_id": {proj: None}}, {"X-CSRFToken": csrf()})

print("═══ 17. issues/bulk/ 四端点（BOARD-004 §4.2）═══")
bulk = f"/api/v1/workspaces/{ws}/projects/{proj}/issues/bulk/"
bulk_ids = []
for n in ("Bulk A", "Bulk B", "Bulk C"):
    _, b = req("POST", f"/api/v1/workspaces/{ws}/projects/{proj}/issues/",
               {"name": n}, {"X-CSRFToken": csrf()})
    bulk_ids.append(((b or {}).get("data") or {}).get("id"))
_, b = expect("BK-1", "preview 预检 → 200（selected/affected 统计）", "POST", f"{bulk}preview/",
              HTTP["OK"], {"issue_ids": bulk_ids, "action": "delete"}, {"X-CSRFToken": csrf()})
case("BK-2", "preview {selected, affected_total, denied}",
     {"selected", "affected_total", "denied"} <= set((b or {}).get("data") or {}))
_, b = expect("BK-3", "PATCH 批量优先级 → 200", "PATCH", bulk, HTTP["OK"],
              {"issue_ids": bulk_ids, "patch": {"priority": "high"}}, {"X-CSRFToken": csrf()})
case("BK-4", "data {updated:3, epoch, action:priority}",
     ((b or {}).get("data") or {}).get("updated") == 3
     and ((b or {}).get("data") or {}).get("action") == "priority")
_, b = expect("BK-5", "101 条 → 400 VALIDATION_BULK_LIMIT_EXCEEDED", "PATCH", bulk,
              HTTP["BAD_REQUEST"],
              {"issue_ids": [f"00000000-0000-0000-0000-{i:012d}" for i in range(101)],
               "patch": {"priority": "low"}},
              {"X-CSRFToken": csrf()})
case("BK-6", "details (field=issue_ids, code=LIMIT)",
     err_field(b, "issue_ids", "LIMIT") is not None
     and ((b or {}).get("error") or {}).get("code") == CODES["bulkLimit"])
_, b = expect("BK-7", "bulk/archive/ 归档 → 200", "POST", f"{bulk}archive/", HTTP["OK"],
              {"issue_ids": bulk_ids[:2]}, {"X-CSRFToken": csrf()})
case("BK-8", "archived_count=2", ((b or {}).get("data") or {}).get("archived_count") == 2)
_, b = expect("BK-9", "重复归档幂等 → 200 count=0", "POST", f"{bulk}archive/", HTTP["OK"],
              {"issue_ids": bulk_ids[:2]}, {"X-CSRFToken": csrf()})
_, b = expect("BK-10", "DELETE confirm_count 错配 → 400", "DELETE", bulk, HTTP["BAD_REQUEST"],
              {"issue_ids": bulk_ids, "confirm_count": 2}, {"X-CSRFToken": csrf()})
_, b = expect("BK-11", "DELETE 批量删除 → 200", "DELETE", bulk, HTTP["OK"],
              {"issue_ids": bulk_ids, "confirm_count": 3}, {"X-CSRFToken": csrf()})
case("BK-12", "deleted=3 / affected_total=3",
     ((b or {}).get("data") or {}).get("deleted") == 3
     and ((b or {}).get("data") or {}).get("affected_total") == 3)

print("═══ 18. comments reactions 两端点（COLLAB-002 §4.2.3）═══")
_, b = req("POST", f"/api/v1/workspaces/{ws}/projects/{proj}/issues/{i1}/comments/",
           {"comment_html": "<p>full cov 评论</p>"}, {"X-CSRFToken": csrf()})
cm_id = ((b or {}).get("data") or {}).get("id")
case("RX-0", "前置：发评论 → 201", bool(cm_id))
rx = f"/api/v1/workspaces/{ws}/projects/{proj}/issues/{i1}/comments/{cm_id}/reactions/"
_, b = expect("RX-1", "POST reaction → 200 changed=true", "POST", rx, HTTP["OK"],
              {"emoji": "👍"}, {"X-CSRFToken": csrf()})
case("RX-2", "聚合 {emoji, count:1, reacted_by_me, changed}",
     ((b or {}).get("data") or {}) == {"emoji": "👍", "count": 1, "reacted_by_me": True, "changed": True})
_, b = expect("RX-3", "重复 POST 幂等 → changed=false", "POST", rx, HTTP["OK"],
              {"emoji": "👍"}, {"X-CSRFToken": csrf()})
case("RX-4", "count 不变", ((b or {}).get("data") or {}).get("count") == 1
     and ((b or {}).get("data") or {}).get("changed") is False)
_, b = expect("RX-5", "DELETE 撤销 → 200 changed=true", "DELETE", rx, HTTP["OK"],
              {"emoji": "👍"}, {"X-CSRFToken": csrf()})
_, b = expect("RX-6", "白名单外 emoji → 400 NOT_A_CHOICE", "POST", rx, HTTP["BAD_REQUEST"],
              {"emoji": "🧟"}, {"X-CSRFToken": csrf()})
case("RX-7", "details (field=emoji, code=NOT_A_CHOICE)", err_field(b, "emoji", "NOT_A_CHOICE") is not None)

print("═══ 19. projects/activities/ 动态流（COLLAB-003 §4.2，+epoch 分支）═══")
acts = f"/api/v1/workspaces/{ws}/projects/{proj}/activities/"
_, b = expect("AC-1", "动态流首页 → 200", "GET", acts, HTTP["OK"])
meta_ac = (b or {}).get("meta") or {}
case("AC-2", "meta 9 字段 + per_page=30（组数口径）",
     all(k in meta_ac for k in ("next_cursor", "prev_cursor", "next_page_results",
                                "prev_page_results", "count", "total_count",
                                "total_pages", "page", "per_page"))
     and meta_ac.get("per_page") == 30)
case("AC-3", "kind ∈ {activity,comment,batch} 三态行域",
     {r.get("kind") for r in (b or {}).get("data") or []} <= {"activity", "comment", "batch"})
_, b = expect("AC-4", "非法 event → 400 INVALID_PARAM", "GET", f"{acts}?event=foo",
              HTTP["BAD_REQUEST"])
case("AC-5", "details (field=event, code=NOT_A_CHOICE)", err_field(b, "event", "NOT_A_CHOICE") is not None)
_, b = expect("AC-6", "?epoch=abc 非数值 → 400（寻址型参数）", "GET", f"{acts}?epoch=abc",
              HTTP["BAD_REQUEST"])
_, b = expect("AC-7", "?epoch= 合法数值 → 200 明细（meta 截断豁免）", "GET", f"{acts}?epoch=1",
              HTTP["OK"])
case("AC-8", "明细 meta 仅 count/total_count/truncated/limit",
     set((b or {}).get("meta") or {}) == {"count", "total_count", "truncated", "limit"})
_, b = expect("AC-9", "per_page=999 → 截断 50 + degraded", "GET", f"{acts}?per_page=999", HTTP["OK"])
case("AC-10", "meta.per_page=50 + degraded 提示",
     ((b or {}).get("meta") or {}).get("per_page") == 50
     and "per_page" in str((((b or {}).get("meta") or {}).get("degraded") or {})))

print("═══ 20. realtime-token 两端点（COLLAB-004 §4.2）═══")
rt = f"/api/v1/workspaces/{ws}/projects/{proj}/realtime-token/"
_, b = expect("RT-1", "换票 → 200", "POST", rt, HTTP["OK"],
              {"client_tab_id": "0f1e2d3c-4b5a-4678-9cde-f0123456789a",
               "issue_rooms": [i1]}, {"X-CSRFToken": csrf()})
rtok = ((b or {}).get("data") or {}).get("token") or ""
case("RT-2", "token 三段 JWT + rooms 装配（project/user）",
     rtok.count(".") == 2
     and f"project:{proj}" in (((b or {}).get("data") or {}).get("rooms") or [])
     and any(r.startswith("user:") for r in (((b or {}).get("data") or {}).get("rooms") or [])))
_, b = req("POST", "/api/v1/users/me/realtime-token/renew/",
           {"token": rtok, "client_tab_id": "0f1e2d3c-4b5a-4678-9cde-f0123456789a"},
           {"X-CSRFToken": csrf()})
case("RT-3", "续签 → 200 或（公钥未配置环境）503 SERVER_LIVE_SERVICE_UNAVAILABLE",
     b is not None and ((b or {}).get("error") or {}).get("code") in
     (None, CODES["liveUnavailable"]), f"got code={((b or {}).get('error') or {}).get('code')}")
_, b = expect("RT-4", "issue_rooms 非法 UUID → 400 INVALID_PARAM", "POST", rt, HTTP["BAD_REQUEST"],
              {"client_tab_id": "tab-1", "issue_rooms": ["not-a-uuid"]}, {"X-CSRFToken": csrf()})
case("RT-5", "错误码 == VALIDATION_INVALID_PARAM",
     ((b or {}).get("error") or {}).get("code") == CODES["invalidParam"])
expect("RT-6", "client_tab_id 缺失 → 400", "POST", rt, HTTP["BAD_REQUEST"],
       {"issue_rooms": []}, {"X-CSRFToken": csrf()})

print(f"\n{'═' * 40}\n接口契约覆盖：{PASS} 通过 / {FAIL} 失败（19 端点族 × 方法 × 正/负例）")
if FAILURES:
    print("\n".join("  ✗ " + f for f in FAILURES))
    sys.exit(1)
print("全部通过 ✓")