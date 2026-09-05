#!/usr/bin/env python3
"""Sprint 0 全接口契约覆盖（api-full-coverage）——每个端点 × 每个方法 × 正/负例。
用法：python3 tests/jmeter/api-full-coverage.py [http://localhost:8000]
前置：API + 真实 PG。与 sprint-0-flow.py（10 步动线 CI gate）互补：
  本脚本按端点矩阵逐个打满，任何一例失败 exit 1。
HTTP/CODES 真相源与 tests/e2e/no-console-errors.ts 镜像（CLAUDE.md 测试脚本规范 ①）。

Sprint-1 INFRA-004 收口：
  - 成功信封 status 字段由布尔 → 字符串 "success"
  - 错误信封由 {status:false, meta:{code,message,...}} → {status:"error", error:{code,message,details?,request_id}}
  - 业务级冲突码（AUTH_EMAIL_EXISTS / PROJECT_IDENTIFIER_EXISTS）按 §4.2 映射到
    RESOURCE_ALREADY_EXISTS；suggestion 透传到 error.details[0].suggestion。
"""
import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
HTTP = {"OK": 200, "CREATED": 201, "NO_CONTENT": 204, "UNAUTHORIZED": 401, "FORBIDDEN": 403,
        "NOT_FOUND": 404, "CONFLICT": 409, "BAD": 400, "TOO_MANY": 429}
# 真相源：sprint-1 INFRA-004 收口后，业务冲突码统一到 RESOURCE_ALREADY_EXISTS；
# 错误字段由 meta 迁到 error。e2e 端镜像（tests/e2e/no-console-errors.ts CODES）。
CODES = {"emailExists": "RESOURCE_ALREADY_EXISTS",
         "invalidCreds": "AUTH_INVALID_CREDENTIALS",
         "csrf": "AUTH_CSRF_FAILED",
         "projectExists": "RESOURCE_ALREADY_EXISTS"}

PASS = FAIL = 0
FAILURES = []

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def req(method, path, data=None, headers=None, authed=True):
    url = f"{BASE}{path}"
    body = json.dumps(data).encode() if data is not None else None
    h = {"Accept": "application/json", "Content-Type": "application/json", "Referer": BASE + "/"}
    if headers:
        h.update(headers)
    if not authed:
        saved = list(cj)
        cj.clear()
    r = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with opener.open(r, timeout=15) as resp:
            raw = resp.read().decode() or "null"
            out = (resp.status, json.loads(raw) if raw.strip().startswith(("{", "[")) else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode() or "null"
        out = (e.code, json.loads(raw) if raw.strip().startswith(("{", "[")) else None)
    finally:
        if not authed:
            cj.clear()
            for c in saved:
                cj.set_cookie(c)
    return out


def csrf():
    return req("GET", "/api/v1/auth/csrf-token/")[1]["data"]["csrf_token"]


def err_field(b, field, code):
    """从新信封 error.details[] 读 (field, code) 命中的条目 —— 没命中返回 None。"""
    items = ((b or {}).get("error") or {}).get("details") or []
    return next((it for it in items if it.get("field") == field and it.get("code") == code), None)


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
case("SU-6", "error.code == RESOURCE_ALREADY_EXISTS", ((b or {}).get("error") or {}).get("code") == CODES["emailExists"])
case("SU-7", "error.details 含 (field=email, code=UNIQUE)", err_field(b, "email", "UNIQUE") is not None)
expect("SU-8", "弱密码 → 400", "POST", "/api/v1/auth/sign-up/", HTTP["BAD"],
       {"email": f"x{ts}@x.dev", "password": "abc"}, {"X-CSRFToken": csrf()})
expect("SU-9", "非法邮箱 → 400", "POST", "/api/v1/auth/sign-up/", HTTP["BAD"],
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
case("PR-5", "error.code == RESOURCE_ALREADY_EXISTS", ((b or {}).get("error") or {}).get("code") == CODES["projectExists"])
detail = err_field(b, "identifier", "UNIQUE")
case("PR-6", "error.details 含 (field=identifier, code=UNIQUE)", detail is not None)
case("PR-7", "error.details[0].suggestion 非空", bool(detail and detail.get("suggestion")))
expect("PR-8", "identifier 1 位 → 400", "POST", f"/api/v1/workspaces/{ws}/projects/", HTTP["BAD"],
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
expect("ID-8", "软删除 → 204", "DELETE", f"/api/v1/workspaces/{ws}/projects/{proj}/issues/{i2}/", HTTP["NO_CONTENT"], None, {"X-CSRFToken": csrf()})
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

print(f"\n{'═' * 40}\n接口契约覆盖：{PASS} 通过 / {FAIL} 失败（13 端点 × 方法 × 正/负例）")
if FAILURES:
    print("\n".join("  ✗ " + f for f in FAILURES))
    sys.exit(1)
print("全部通过 ✓")