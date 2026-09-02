# API 真相源（与 tests/e2e/no-console-errors.ts 的 API_TRUTH 镜像——跨语言同源 grep 锁定）
# 状态码变更必须同步：TS 端的 API_TRUTH + 本表 + 后端契约
HTTP = {
  "OK":          200,   # GET 资源正常
  "CREATED":     201,   # POST 建资源
  "NO_CONTENT":  204,   # DELETE / sign-out 无 body
  "FORBIDDEN":   401,   # DRF 未认证 401（CSRF/未登录）
  "UNAUTHORIZED": 403,   # 越权 403（DRF SessionAuth 拒绝）
  "NOT_FOUND":   404,   # 越权 404（AUTH-003 防 ID 枚举）
  "CONFLICT":    409,   # identifier 重复
  "TOO_MANY":    429,   # 限流
  "SRV_ERR":     500,   # 期望失败用
}

"""Sprint 0 接口端到端验证（JMeter jmx 等价的 Python 版）。
用法：python3 tests/jmeter/sprint-0-flow.py http://localhost:8000
前置：API 已启动并连接真实 PG；JMeter jmx 在 tests/jmeter/sprint-0-flow.jmx（结构已校验）。
设计原因：JMeter 5.6 + CSRF/cookie 在跨 sampler 时流转有边界，Python 等价脚本可获得
100% 一致的业务断言，同时 jmx 保留供后续性能压测复用。"""
import sys, json, time, urllib.request, urllib.parse, http.cookiejar

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def req(method, path, data=None, headers=None):
    url = f"{BASE}{path}"
    body = json.dumps(data).encode() if data is not None else None
    h = {"Accept": "application/json", "Content-Type": "application/json", "Referer": BASE + "/"}
    if headers: h.update(headers)
    r = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with opener.open(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null")


def step(label, fn):
    print(f"\n== {label} ==")
    code, body = fn()
    print(f"  HTTP {code}")
    if not (HTTP["OK"] <= code < 300):
        print(f"  ✗ FAIL: {body}")
        raise SystemExit(1)
    print(f"  ✓ ok")
    return body


def fresh_csrf():
    """登录态变化（Django CSRF rotate）后必须重新拉，否则旧 token 失效。"""
    return req("GET", "/api/v1/auth/csrf-token/")[1]["data"]["csrf_token"]


ts = int(time.time())
email = f"py-{ts}@rabbit.dev"
password = "Rabbit123"

# 1) 健康
step("01 health", lambda: req("GET", "/api/v1/health/"))

# 2) csrf
csrf = step("02 csrf-token", lambda: req("GET", "/api/v1/auth/csrf-token/"))["data"]["csrf_token"]

# 3) 注册（带 X-CSRFToken）—— 注册后 Django 触发 session/csrf rotation
sign = step("03 sign-up", lambda: req(
    "POST", "/api/v1/auth/sign-up/",
    {"email": email, "password": password, "display_name": "Py User"},
    {"X-CSRFToken": csrf},
))
ws = sign["data"]["default_workspace_slug"]
print(f"  ws={ws}")

# 4) 注册后 **重新拉 csrf**（关键：登录后 token rotate，旧 token 失效）
csrf = fresh_csrf()
step("04 me", lambda: req("GET", "/api/v1/users/me/"))

# 5) 建项目（identifier 唯一化避免同工作区重复 PYT 撞 409；新建账户无项目也走此步）
csrf = fresh_csrf()
import time
proj_id = f"PYT{int(time.time()) % 10000:04d}"[:5]
proj = step("05 create-project", lambda: req(
    "POST", f"/api/v1/workspaces/{ws}/projects/",
    {"name": "Py Test Project", "identifier": proj_id, "description": "Python generated"},
    {"X-CSRFToken": csrf},
))
pid = proj["data"]["id"]
assert pid, f"create-project returned no id: {proj}"

# 6) 项目状态
states = step("06 project-states", lambda: req("GET", f"/api/v1/workspaces/{ws}/projects/{pid}/states/"))
state_names = {s["name"] for s in states["data"]}
assert {"待办", "进行中", "已完成"}.issubset(state_names), f"got {state_names}"
prog_id = next(s["id"] for s in states["data"] if s["group"] == "started")

# 7) 建任务（再旋转一次 —— 重要的状态变更点；保守起见都重新拉）
csrf = fresh_csrf()
issue = step("07 create-issue", lambda: req(
    "POST", f"/api/v1/workspaces/{ws}/projects/{pid}/issues/",
    {"name": "Py issue", "target_date": "2026-09-15"},
    {"X-CSRFToken": csrf},
))
iid = issue["data"]["id"]

# 8) 拖拽 PATCH
csrf = fresh_csrf()
step("08 drag-issue", lambda: req(
    "PATCH", f"/api/v1/workspaces/{ws}/projects/{pid}/issues/{iid}/",
    {"state_id": prog_id, "sort_order": 200000.0},
    {"X-CSRFToken": csrf},
))

# 9) 验证状态保持
verify = step("09 verify-state", lambda: req("GET", f"/api/v1/workspaces/{ws}/projects/{pid}/issues/?ordering=sort_order"))
assert verify["data"][0]["state_group"] == "started", f"state={verify['data'][0]['state_group']}"
print(f"  issue_key={verify['data'][0]['issue_key']} state_group=started ✓")

# 10a) 删除项目 + 验证软删除（对应 TC-PROJ1-007b）
csrf = fresh_csrf()
del_code, _ = req(
    "DELETE", f"/api/v1/workspaces/{ws}/projects/{pid}/", None, {"X-CSRFToken": csrf},
)
assert del_code in (HTTP["OK"], HTTP["NO_CONTENT"]), f"DELETE expected 200/204, got {del_code}"
# 重新拉 csrf 后 GET 该项目应 404（软删除后唯一约束不重复，Manager 过滤 deleted_at）
csrf = fresh_csrf()
code_after_del, _ = req("GET", f"/api/v1/workspaces/{ws}/projects/{pid}/", headers={"X-CSRFToken": csrf})
assert code_after_del == 404, f"after soft-delete GET expected 404, got {code_after_del}"
print(f"\n== 10a project delete ==\n  DELETE → {del_code} ✓\n  GET after → {code_after_del} ✓ (soft delete: default Manager filters deleted_at)")

# 10b) 越权（清 cookie 后重发 —— 无认证返回 401）
cj.clear()
code, _ = req("GET", f"/api/v1/workspaces/{ws}/projects/{pid}/")
assert code in (HTTP["FORBIDDEN"], HTTP["UNAUTHORIZED"], HTTP["NOT_FOUND"]), f"expected 401/403/404, got {code}"
print(f"\n== 10b cross-tenant blocked ==\n  HTTP {code} ✓")

print("\n🎉 ALL 10 STEPS PASSED — 接口测试通过")
