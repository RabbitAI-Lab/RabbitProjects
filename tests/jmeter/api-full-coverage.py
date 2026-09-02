"""Sprint 0 全接口契约覆盖（api-full-coverage）——每个端点 × 每个方法 × 正/负例。
用法：python3 tests/jmeter/api-full-coverage.py [http://localhost:8000]
前置：API + 真实 PG。与 sprint-0-flow.py（10 步动线 CI gate）互补：
  本脚本按端点矩阵逐个打满，任何一例失败 exit 1。
HTTP/CODES 真相源与 tests/e2e/no-console-errors.ts 镜像（CLAUDE.md 测试脚本规范 ①）。"""
import sys, json, time, urllib.request, urllib.error, http.cookiejar

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
HTTP = {"OK": 200, "CREATED": 201, "NO_CONTENT": 204, "UNAUTHORIZED": 401, "FORBIDDEN": 403,
        "NOT_FOUND": 404, "CONFLICT": 409, "BAD": 400, "TOO_MANY": 429}
CODES = {"emailExists": "AUTH_EMAIL_EXISTS", "invalidCreds": "AUTH_INVALID_CREDENTIALS",
         "csrf": "AUTH_CSRF_FAILED", "projectExists": "PROJECT_IDENTIFIER_EXISTS"}

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
expect("HC-1", "健康检查", "GET", "/api/v1/health/", HTTP["OK"])
_, b = req("GET", "/api/v1/health/")
case("HC-2", "checks.db == ok（health 不走信封）", b["checks"]["db"] == "ok")

print("═══ 2. auth/csrf-token ═══")
_, b = expect("CS-1", "获取令牌", "GET", "/api/v1/auth/csrf-token/", HTTP["OK"])
case("CS-2", "token 长度 ≥ 32", len(b["data"]["csrf_token"]) >= 32)

print("═══ 3. auth/sign-up ═══")
_, b = expect("SU-1", "新邮箱注册", "POST", "/api/v1/auth/sign-up/", HTTP["CREATED"],
              {"email": email, "password": pw, "display_name": "Full Cov"}, {"X-CSRFToken": csrf()})
case("SU-2", "信封 status=true", b["status"] is True)
case("SU-3", "default_workspace_slug 非空", bool(b["data"]["default_workspace_slug"]))
case("SU-4", "workspaces[0].role == 20(OWNER)", b["data"]["workspaces"][0]["role"] == 20)
ws = b["data"]["default_workspace_slug"]
_, b = expect("SU-5", "重复邮箱 → 409", "POST", "/api/v1/auth/sign-up/", HTTP["CONFLICT"],
              {"email": email, "password": pw}, {"X-CSRFToken": csrf()})
case("SU-6", "meta.code == AUTH_EMAIL_EXISTS", (b or {}).get("meta", {}).get("code") == CODES["emailExists"])
expect("SU-7", "弱密码 → 400", "POST", "/api/v1/auth/sign-up/", HTTP["BAD"],
       {"email": f"x{ts}@x.dev", "password": "abc"}, {"X-CSRFToken": csrf()})
expect("SU-8", "非法邮箱 → 400", "POST", "/api/v1/auth/sign-up/", HTTP["BAD"],
       {"email": "not-an-email", "password": pw}, {"X-CSRFToken": csrf()})
c1 = req("POST", "/api/v1/auth/sign-up/", {"email": f"nocsrf-{ts}@x.dev", "password": pw}, {})  # 不带 CSRF 头
case("SU-9", "缺 CSRF → 403", c1[0] == HTTP["FORBIDDEN"], f"got {c1[0]}")

print("═══ 4. auth/sign-in ═══")
_, b = expect("SI-1", "正确凭据 → 200", "POST", "/api/v1/auth/sign-in/", HTTP["OK"],
              {"email": email, "password": pw}, {"X-CSRFToken": csrf()})
case("SI-2", "返回 user.email", b["data"]["user"]["email"] == email)
_, b = expect("SI-3", "错误密码 → 401", "POST", "/api/v1/auth/sign-in/", HTTP["UNAUTHORIZED"],
              {"email": email, "password": "Wrong123"}, {"X-CSRFToken": csrf()})
case("SI-4", "meta.code == AUTH_INVALID_CREDENTIALS", (b or {}).get("meta", {}).get("code") == CODES["invalidCreds"])
expect("SI-5", "不存在邮箱 → 401", "POST", "/api/v1/auth/sign-in/", HTTP["UNAUTHORIZED"],
       {"email": f"ghost-{ts}@x.dev", "password": pw}, {"X-CSRFToken": csrf()})
expect("SI-6", "remember=true → 200", "POST", "/api/v1/auth/sign-in/", HTTP["OK"],
       {"email": email, "password": pw, "remember": True}, {"X-CSRFToken": csrf()})

print("═══ 5. users/me ═══")
_, b = expect("ME-1", "已登录 → 200", "GET", "/api/v1/users/me/", HTTP["OK"])
case("ME-2", "含 user+workspaces+default_workspace_slug",
     all(k in b["data"] for k in ("user", "workspaces", "default_workspace_slug")))
c2 = req("GET", "/api/v1/users/me/", authed=False)
case("ME-3", "未认证 → 401/403", c2[0] in (HTTP["UNAUTHORIZED"], HTTP["FORBIDDEN"]), f"got {c2[0]}")

print("═══ 6. workspaces 集合 ═══")
_, b = expect("WS-1", "列表 → 200", "GET", "/api/v1/workspaces/", HTTP["OK"])
case("WS-2", "列表含已建 ws 且 role=20", any(x["slug"] == ws and x["role"] == 20 for x in b["data"]))
c3 = req("GET", "/api/v1/workspaces/", authed=False)
case("WS-3", "未认证 → 401/403", c3[0] in (HTTP["UNAUTHORIZED"], HTTP["FORBIDDEN"]), f"got {c3[0]}")
_, b = expect("WS-4", "创建团队 → 201", "POST", "/api/v1/workspaces/", HTTP["CREATED"],
              {"name": f"Full Cov Team {ts}", "description": "e2e"}, {"X-CSRFToken": csrf()})
ws2 = b["data"]["slug"]
case("WS-5", "slug 归一小写", ws2 == ws2.lower() and ws2 == f"full-cov-team-{ts}")
_, b = expect("WS-6", "同名创建 → slug 后缀 -2", "POST", "/api/v1/workspaces/", HTTP["CREATED"],
              {"name": f"Full Cov Team {ts}"}, {"X-CSRFToken": csrf()})
case("WS-7", "slug 冲突消解", b["data"]["slug"] == f"{ws2}-2")

print("═══ 7. workspaces/{slug} 详情 ═══")
_, b = expect("WD-1", "详情 → 200", "GET", f"/api/v1/workspaces/{ws}/", HTTP["OK"])
case("WD-2", "name 字段存在", isinstance(b["data"].get("name"), str))
expect("WD-3", "不存在 → 404", "GET", "/api/v1/workspaces/no-such-ws/", HTTP["NOT_FOUND"])
_, b = expect("WD-4", "PATCH 更新 → 200", "PATCH", f"/api/v1/workspaces/{ws2}/", HTTP["OK"],
              {"name": "Renamed Team"}, {"X-CSRFToken": csrf()})
case("WD-5", "名称已更新", b["data"]["name"] == "Renamed Team")

print("═══ 8. projects 集合 ═══")
pid = f"F{ts % 1000:03d}"[:5] if len(f"F{ts % 1000:03d}") >= 4 else f"F{ts % 100:02d}"
_, b = expect("PR-1", "创建（小写 id 自动大写）", "POST", f"/api/v1/workspaces/{ws}/projects/", HTTP["CREATED"],
              {"name": "Full Cov Proj", "identifier": pid.lower(), "description": "x"}, {"X-CSRFToken": csrf()})
proj = b["data"]["id"]
case("PR-2", "identifier 大写化", b["data"]["identifier"] == pid.upper())
case("PR-3", "total_members == 1", b["data"]["total_members"] == 1)
_, b = expect("PR-4", "重复 identifier → 409", "POST", f"/api/v1/workspaces/{ws}/projects/", HTTP["CONFLICT"],
              {"name": "Dup", "identifier": pid}, {"X-CSRFToken": csrf()})
case("PR-5", "meta.code == PROJECT_IDENTIFIER_EXISTS", (b or {}).get("meta", {}).get("code") == CODES["projectExists"])
case("PR-6", "meta.suggestion 非空", bool((b or {}).get("meta", {}).get("suggestion")))
expect("PR-7", "identifier 1 位 → 400", "POST", f"/api/v1/workspaces/{ws}/projects/", HTTP["BAD"],
       {"name": "Bad", "identifier": "A"}, {"X-CSRFToken": csrf()})
_, b = expect("PR-8", "列表 → 200", "GET", f"/api/v1/workspaces/{ws}/projects/", HTTP["OK"])
case("PR-9", "列表含新项目", any(x["id"] == proj for x in b["data"]))

print("═══ 9. project 详情/PATCH ═══")
_, b = expect("PD-1", "详情 → 200", "GET", f"/api/v1/workspaces/{ws}/projects/{proj}/", HTTP["OK"])
case("PD-2", "含 total_issues", "total_issues" in b["data"])
_, b = expect("PD-3", "PATCH 改名 → 200", "PATCH", f"/api/v1/workspaces/{ws}/projects/{proj}/", HTTP["OK"],
              {"name": "Renamed Proj"}, {"X-CSRFToken": csrf()})
case("PD-4", "名称已更新", b["data"]["name"] == "Renamed Proj")
_, b = expect("PD-5", "PATCH 换 identifier 被忽略", "PATCH", f"/api/v1/workspaces/{ws}/projects/{proj}/", HTTP["OK"],
              {"identifier": "ZZZZZ"}, {"X-CSRFToken": csrf()})
case("PD-6", "identifier 不可变", b["data"]["identifier"] == pid.upper())
expect("PD-7", "不存在项目 → 404", "GET", f"/api/v1/workspaces/{ws}/projects/00000000-0000-0000-0000-000000000000/", HTTP["NOT_FOUND"])

print("═══ 10. states ═══")
_, b = expect("ST-1", "默认列表 → 200", "GET", f"/api/v1/workspaces/{ws}/projects/{proj}/states/", HTTP["OK"])
names = {s["name"] for s in b["data"]}
case("ST-2", "含 待办/进行中/已完成", {"待办", "进行中", "已完成"} <= names, f"got {names}")
case("ST-3", "不含已取消", "已取消" not in names)
started = next(s["id"] for s in b["data"] if s["group"] == "started")
_, b = expect("ST-4", "?include_cancelled=1 → 4 态", "GET",
              f"/api/v1/workspaces/{ws}/projects/{proj}/states/?include_cancelled=1", HTTP["OK"])
case("ST-5", "含已取消（group=cancelled）", any(s["group"] == "cancelled" for s in b["data"]))
case("ST-6", "字段齐备 group/color", all(("group" in s and "color" in s) for s in b["data"]))

print("═══ 11. issues 集合 ═══")
_, b = expect("IS-1", "建任务 → 201", "POST", f"/api/v1/workspaces/{ws}/projects/{proj}/issues/", HTTP["CREATED"],
              {"name": "Issue One", "target_date": "2026-09-15"}, {"X-CSRFToken": csrf()})
i1 = b["data"]["id"]
case("IS-2", "issue_key = {ID}-1", b["data"]["issue_key"] == f"{pid.upper()}-1")
case("IS-3", "sequence_id == 1", b["data"]["sequence_id"] == 1)
case("IS-4", "默认落待办", b["data"]["state_name"] == "待办")
case("IS-5", "sort_order == 65535", b["data"]["sort_order"] == 65535)
case("IS-6", "created_by.name 非空", bool(b["data"].get("created_by") or {}))
_, b = expect("IS-7", "建第二个（指定 started）→ 201", "POST", f"/api/v1/workspaces/{ws}/projects/{proj}/issues/", HTTP["CREATED"],
              {"name": "Issue Two", "state_id": started}, {"X-CSRFToken": csrf()})
i2 = b["data"]["id"]
case("IS-8", "state_group == started", b["data"]["state_group"] == "started")
case("IS-9", "sequence_id == 2", b["data"]["sequence_id"] == 2)
_, b = expect("IS-10", "列表 → 200", "GET", f"/api/v1/workspaces/{ws}/projects/{proj}/issues/?ordering=sort_order", HTTP["OK"])
case("IS-11", "两条", len(b["data"]) == 2)
_, b = expect("IS-12", "?group_by=state_id → 200", "GET",
              f"/api/v1/workspaces/{ws}/projects/{proj}/issues/?group_by=state_id", HTTP["OK"])
case("IS-13", "分组含 1 条的键", any(len(v) == 1 for v in b["data"].values()))

print("═══ 12. issue 详情/PATCH/DELETE ═══")
_, b = expect("ID-1", "详情 → 200", "GET", f"/api/v1/workspaces/{ws}/projects/{proj}/issues/{i1}/", HTTP["OK"])
case("ID-2", "字段齐备", all(k in b["data"] for k in ("issue_key", "state_name", "state_group", "assignee", "created_by")))
_, b = expect("ID-3", "PATCH 改名+改状态 → 200", "PATCH", f"/api/v1/workspaces/{ws}/projects/{proj}/issues/{i1}/", HTTP["OK"],
              {"name": "Renamed Issue", "state_id": started, "sort_order": 131070.0}, {"X-CSRFToken": csrf()})
case("ID-4", "状态已改 started", b["data"]["state_group"] == "started")
_, b = req("GET", f"/api/v1/workspaces/{ws}/projects/{proj}/issues/{i1}/")
case("ID-5", "改名已落库", b["data"]["name"] == "Renamed Issue")
case("ID-6", "sort_order 已落库", b["data"]["sort_order"] == 131070.0)
expect("ID-7", "不存在任务 → 404", "GET", f"/api/v1/workspaces/{ws}/projects/{proj}/issues/00000000-0000-0000-0000-000000000000/", HTTP["NOT_FOUND"])
expect("ID-8", "软删除 → 204", "DELETE", f"/api/v1/workspaces/{ws}/projects/{proj}/issues/{i2}/", HTTP["NO_CONTENT"], None, {"X-CSRFToken": csrf()})
expect("ID-9", "删除后 GET → 404", "GET", f"/api/v1/workspaces/{ws}/projects/{proj}/issues/{i2}/", HTTP["NOT_FOUND"])
_, b = req("GET", f"/api/v1/workspaces/{ws}/projects/{proj}/issues/")
case("ID-10", "删除后列表少一条", len(b["data"]) == 1)

print("═══ 13. auth/sign-out ═══")
expect("SO-1", "登出 → 204", "POST", "/api/v1/auth/sign-out/", HTTP["NO_CONTENT"], None, {"X-CSRFToken": csrf()})
c5 = req("GET", "/api/v1/users/me/")
case("SO-2", "登出后 me → 401/403", c5[0] in (HTTP["UNAUTHORIZED"], HTTP["FORBIDDEN"]), f"got {c5[0]}")

print("═══ 14. 项目删除（复用 ws2）═══")
req("POST", "/api/v1/auth/sign-in/", {"email": email, "password": pw})
_, b = expect("PD-8", "建临时项目", "POST", f"/api/v1/workspaces/{ws2}/projects/", HTTP["CREATED"],
              {"name": "To Delete", "identifier": "DEL"}, {"X-CSRFToken": csrf()})
tmp = b["data"]["id"]
expect("PD-9", "删除项目 → 204", "DELETE", f"/api/v1/workspaces/{ws2}/projects/{tmp}/", HTTP["NO_CONTENT"], None, {"X-CSRFToken": csrf()})
expect("PD-10", "删后 GET → 404", "GET", f"/api/v1/workspaces/{ws2}/projects/{tmp}/", HTTP["NOT_FOUND"])

print(f"\n{'═' * 40}\n接口契约覆盖：{PASS} 通过 / {FAIL} 失败（13 端点 × 方法 × 正/负例）")
if FAILURES:
    print("\n".join("  ✗ " + f for f in FAILURES))
    sys.exit(1)
print("全部通过 ✓")
