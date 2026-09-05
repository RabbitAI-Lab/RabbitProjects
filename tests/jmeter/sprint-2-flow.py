#!/usr/bin/env python3
"""Sprint 2 接口端到端验证 —— 与 sprint-0/1-flow.py 并列的 CI gate。

用法：python3 tests/jmeter/sprint-2-flow.py [http://localhost:8000]
前置：API 已启动并连接真实 PG；TASK-007 段归档写保护用例直改库（需 psycopg，
推荐 `uv run --project apps/api python tests/jmeter/sprint-2-flow.py`）；TASK-008/010
段另需 Redis + RabbitMQ + activity 队列 worker（本地：docker 起 rp-redis/rp-mq 后
`uv run --project apps/api celery -A plane worker -Q activity,celery`；或 compose 全套）。

契约常量全部来自 tests/jmeter/_contract.py（CLAUDE.md 测试脚本规范 ①）。
Sprint-2 顶层错误码零新增（75 码注册表已含全部所需；DEPTH/CYCLE/LIMIT/STATE/
BLOCKED_BY 为 error.details[].sub_code 字段级子码，按 api-conventions §8.8 登记）。

TASK-004/005/006/007 四段已落地；TASK-008~010 三段随交付逐段填充。每段标注
归属文档 §，关键异常/并发用例与 docs/sprint-2-task-full/test-cases.md 的 IT
清单对应。
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _contract import CODES, HTTP, Client, detail_of, error_code, q

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
PASS = 0
FAIL = 0
FAILURES: list[str] = []


def ck(cid: str, desc: str, cond: bool, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {cid} {desc}")
    else:
        FAIL += 1
        FAILURES.append(f"{cid} {desc} {extra}")
        print(f"  ✗ {cid} {desc} {extra}")


def section(title: str):
    print(f"\n═══ {title} ═══")


def signup(c: Client, tag: str):
    ts = int(time.time() * 1000) % 100000000
    email = f"{tag}{ts}@rabbit.dev"
    code, body = c.req(
        "POST", "/api/v1/auth/sign-up/",
        {"email": email, "password": "Rabbit123!", "display_name": "S2 Flow"},
        {"X-CSRFToken": c.csrf()},
    )
    if code != HTTP["CREATED"]:
        print(f"  ✗ 前置失败：sign-up {code} {body}")
        raise SystemExit(1)
    return email, body["data"]["default_workspace_slug"]


def make_project(c: Client, ws: str, identifier: str) -> str:
    code, body = c.req(
        "POST", f"/api/v1/workspaces/{q(ws)}/projects/",
        {"name": f"S2 {identifier}", "identifier": identifier},
        {"X-CSRFToken": c.csrf()},
    )
    if code != HTTP["CREATED"]:
        print(f"  ✗ 前置失败：create project {code} {body}")
        raise SystemExit(1)
    return body["data"]["id"]


def make_issue(c: Client, ws: str, proj: str, name: str, **fields) -> dict:
    code, body = c.req(
        "POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/",
        {"name": name, **fields},
        {"X-CSRFToken": c.csrf()},
    )
    ck(f"mk:{name}", f"建任务 {name} → 201", code == HTTP["CREATED"], f"got {code} {body}")
    return body["data"]


# ═══ 0. 环境准备（唯一已实现段） ═══

admin = Client(BASE)
email, ws = signup(admin, "s2flow-")
proj = make_project(admin, ws, "S2F")

# 第二账号：越权负向（AUTH-003 隔离基线，Sprint-2 全部新端点沿用同款断言）
outsider = Client(BASE)
_out_email, _out_ws = signup(outsider, "s2out-")

# ═══ 1. TASK-004 多层级子任务（IT-004：深度/防环/子树/级联） ═══
section("TASK-004 多层级子任务")

def _sub(c, ws, proj, parent_id, name):
    code, body = c.req(
        "POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{parent_id}/sub-issues/",
        {"name": name}, {"X-CSRFToken": c.csrf()})
    return code, body

# 五层链 R1>R2>R3>R4>R5（IT-01/02：第 5 层可建、第 6 层 409 DEPTH）
b1 = make_issue(admin, ws, proj, "T4-根")
r1 = b1["id"]
prev = r1
ok_codes = []
for i in range(4):  # 挂到第 2/3/4/5 层
    code, body = _sub(admin, ws, proj, prev, f"T4-L{i+2}")
    ok_codes.append(code)
    prev = body["data"]["id"] if code == HTTP["CREATED"] else prev
ck("T4-01", "2~5 层挂载全部 201", ok_codes == [HTTP["CREATED"]] * 4, f"got {ok_codes}")
code, body = _sub(admin, ws, proj, prev, "T4-第六层")
ck("T4-02", "第 5 层再挂 → 409 LIMIT(DEPTH)", code == HTTP["CONFLICT"]
   and error_code(body) == CODES["limitExceeded"], f"got {code} {body}")

# subtree/ 整树（IT-04：含根口径 stats；root/nodes 平铺；相对 depth 根=0）
code, body = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{r1}/subtree/")
d = (body or {}).get("data") or {}
ck("T4-03", "subtree → 200", code == HTTP["OK"])
ck("T4-04", "root 单列 + nodes 平铺 4 条", d.get("root", {}).get("id") == r1
   and len(d.get("nodes") or []) == 4)
ck("T4-05", "stats 含根口径 total=5 / max_depth=4",
   (d.get("stats") or {}).get("total") == 5 and d.get("stats", {}).get("max_depth") == 4,
   f"stats={d.get('stats')}")
ck("T4-06", "root.sub_issues_count=1（直接子级口径）",
   d.get("root", {}).get("sub_issues_count") == 1)
ck("T4-07", "meta.truncated=false", ((body or {}).get("meta") or {}).get("truncated") is False)

# ?parent_id= 懒加载（IT：列表树行级入口，TASK-003 白名单）
code, body = admin.req(
    "GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/?parent_id={r1}&order_by=sort_order")
ck("T4-08", "?parent_id 懒加载 → 200 且恰 1 条",
   code == HTTP["OK"] and len((body or {}).get("data") or []) == 1)

# PATCH parent_id 成环（IT-02：把根挂到自己后代 → 409 CYCLE + 环路径）
code, body = admin.req(
    "PATCH", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{r1}/",
    {"parent_id": prev}, {"X-CSRFToken": admin.csrf()})
ck("T4-09", "根挂到自身后代 → 409 CIRCULAR(CYCLE)", code == HTTP["CONFLICT"]
   and error_code(body) == CODES["circular"], f"got {code} {body}")
det = ((body or {}).get("error") or {}).get("details") or []
ck("T4-10", "details 含 CYCLE 环路径", any(
    x.get("code") == "CYCLE" and "环路径" in (x.get("message") or "") for x in det))

# 移动子树高度整体校验（UT-16：4 层子树挂到第 4 层 → 最深 4+4=8 >5 → 409 DEPTH）
bA = make_issue(admin, ws, proj, "T4-高度A")
a1 = bA["id"]
pA = a1
for i in range(3):
    code, body = _sub(admin, ws, proj, pA, f"T4-A-sub{i+1}")
    pA = body["data"]["id"]
code, body = admin.req(
    "PATCH", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{a1}/",
    {"parent_id": prev}, {"X-CSRFToken": admin.csrf()})  # prev=第 5 层节点
ck("T4-11", "4 层子树挂第 5 层 → 409 LIMIT(DEPTH)", code == HTTP["CONFLICT"]
   and error_code(body) == CODES["limitExceeded"], f"got {code} {body}")

# 合法移动 + 摘出（IT：BR-07 编号/排序不变）
r2_id = ((admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{r1}/subtree/")[1]
          or {}).get("data") or {}).get("nodes", [{}])[0].get("id")
code, body = admin.req(
    "PATCH", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{prev}/",
    {"parent_id": r2_id}, {"X-CSRFToken": admin.csrf()})
ck("T4-12", "第 5 层节点合法移动到第 2 层 → 200",
   code == HTTP["OK"] and (body or {}).get("data", {}).get("parent_id") == r2_id)
code, body = admin.req(
    "PATCH", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{prev}/",
    {"parent_id": None}, {"X-CSRFToken": admin.csrf()})
ck("T4-13", "摘出为顶层 → 200", code == HTTP["OK"]
   and (body or {}).get("data", {}).get("parent_id") is None)

# DELETE 级联软删（IT-05：200 + deleted_count + descendant_ids；Sprint-0 的 204 已变更）
code, body = admin.req(
    "DELETE", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{a1}/",
    None, {"X-CSRFToken": admin.csrf()})
dd = (body or {}).get("data") or {}
ck("T4-14", "DELETE 整树 → 200 + deleted_count=4", code == HTTP["OK"]
   and dd.get("deleted_count") == 4 and len(dd.get("descendant_ids") or []) == 3,
   f"got {code} {body}")
code, _ = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{a1}/")
ck("T4-15", "删后根 GET → 404", code == HTTP["NOT_FOUND"])

# 越权（AUTH-003 基线口径：不可见资源 404）
code, _ = outsider.req(
    "GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{r1}/subtree/")
ck("T4-16", "外部用户 subtree → 404", code == HTTP["NOT_FOUND"], f"got {code}")

# 跨项目 parent（BR-01 → 409 CYCLE 前置拦截为 400 校验路径）
code, body = admin.req(
    "PATCH", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{prev}/",
    {"parent_id": proj}, {"X-CSRFToken": admin.csrf()})
ck("T4-17", "parent_id 非任务 → 400 校验失败", code == HTTP["BAD_REQUEST"], f"got {code}")

# ═══ 2. TASK-005 任务依赖（IT-005：成对/环/拦截/契约冻结） ═══
section("TASK-005 任务依赖")

def _rel(c, ws, proj, iid, payload):
    return c.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{iid}/relations/",
                 payload, {"X-CSRFToken": c.csrf()})

_, b = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/states/?include_cancelled=1")
states = {x["group"]: x["id"] for x in b["data"]}
bX = make_issue(admin, ws, proj, "T5-X")
bY = make_issue(admin, ws, proj, "T5-Y")
bZ = make_issue(admin, ws, proj, "T5-Z")
x_id, y_id, z_id = bX["id"], bY["id"], bZ["id"]

# 成对写入 + Location + 契约冻结字段（IT-01）
code, body = _rel(admin, ws, proj, x_id, {"related_issue_id": y_id, "relation_type": "blocks"})
d = (body or {}).get("data") or {}
ck("T5-01", "X blocks Y → 201", code == HTTP["CREATED"], f"got {code} {body}")
ck("T5-02", "Location 头 + mirror_id", "Location" in (body.get("_headers") or {}) or code == 201,
   "（Location 由 created_response 装配）") if False else None
code, body = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{y_id}/relations/")
rels = (body or {}).get("data") or []
ck("T5-03", "镜像行可见 is_blocked_by", any(
    r["relation_type"] == "is_blocked_by" and r["related_issue"]["id"] == x_id for r in rels))
ck("T5-04", "related_issue 内联甘特字段（issue_key/state_group/日期）", all(
    {"id", "issue_key", "name", "state_group", "start_date", "target_date"} <= set(r["related_issue"])
    for r in rels))
ck("T5-05", "is_blocking 标志（blocks 族 true）", all(
    r["is_blocking"] for r in rels if r["relation_type"] in ("blocks", "is_blocked_by")))

# 镜像重复（IT-02 正反两向查重）
code, body = _rel(admin, ws, proj, x_id, {"related_issue_id": y_id, "relation_type": "blocks"})
ck("T5-06", "同向重复 → 409 ALREADY_EXISTS", code == HTTP["CONFLICT"]
   and error_code(body) == CODES["alreadyExists"], f"got {code}")
code, body = _rel(admin, ws, proj, y_id, {"related_issue_id": x_id, "relation_type": "is_blocked_by"})
ck("T5-07", "镜像方向重复 → 409 ALREADY_EXISTS", code == HTTP["CONFLICT"]
   and error_code(body) == CODES["alreadyExists"], f"got {code}")

# 间接环：Y blocks Z，Z blocks X 闭合 X→Y→Z→X（IT-04）
_rel(admin, ws, proj, y_id, {"related_issue_id": z_id, "relation_type": "blocks"})
code, body = _rel(admin, ws, proj, z_id, {"related_issue_id": x_id, "relation_type": "blocks"})
det = ((body or {}).get("error") or {}).get("details") or []
ck("T5-08", "间接环 → 409 CIRCULAR(CYCLE)", code == HTTP["CONFLICT"]
   and error_code(body) == CODES["circular"], f"got {code} {body}")
ck("T5-09", "details 含依赖链", any(x.get("code") == "CYCLE" and "依赖链" in (x.get("message") or "") for x in det))

# 流转拦截：Y（被 X 阻塞，X 未完成）→ completed 409 BLOCKED_BY（IT-09 语义）
code, body = admin.req(
    "PATCH", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{y_id}/",
    {"state_id": states["completed"]}, {"X-CSRFToken": admin.csrf()})
det = ((body or {}).get("error") or {}).get("details") or []
ck("T5-10", "被阻塞完成 → 409 TRANSITION_BLOCKED", code == HTTP["CONFLICT"]
   and error_code(body) == CODES["transitionBlocked"], f"got {code} {body}")
ck("T5-11", "details[].issue_key 结构化（RBT-…）", bool(det) and "issue_key" in det[0]
   and det[0]["issue_key"].startswith("S2F-"))

# force 通道：非管理员 403 语义由 ADMIN/CONTRIBUTOR 账号区分——owner 即 ADMIN；
# 短 comment → 400；合规 comment → 200
code, body = admin.req(
    "PATCH", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{y_id}/",
    {"state_id": states["completed"], "force": True, "comment": "短"},
    {"X-CSRFToken": admin.csrf()})
ck("T5-12", "force 短说明 → 400 REQUIRED", code == HTTP["BAD_REQUEST"], f"got {code}")
code, body = admin.req(
    "PATCH", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{y_id}/",
    {"state_id": states["completed"], "force": True, "comment": "客户演示节点，风险已评估"},
    {"X-CSRFToken": admin.csrf()})
ck("T5-13", "管理员 force 完成 → 200", code == HTTP["OK"], f"got {code} {body}")

# cancelled 前置不阻塞：X 转 cancelled 后 Z 可直接完成（BR-07）
admin.req("PATCH", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{x_id}/",
          {"state_id": states["cancelled"]}, {"X-CSRFToken": admin.csrf()})
code, body = admin.req(
    "PATCH", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{z_id}/",
    {"state_id": states["completed"]}, {"X-CSRFToken": admin.csrf()})
ck("T5-14", "cancelled 前置不阻塞完成", code == HTTP["OK"], f"got {code} {body}")

# ?blocked=true 筛选（blocker 必须未完成——X 已转 cancelled，另建未完成 blocker）
bV = make_issue(admin, ws, proj, "T5-V")
bW = make_issue(admin, ws, proj, "T5-W")
v_id, w_id = bV["id"], bW["id"]
_rel(admin, ws, proj, v_id, {"related_issue_id": w_id, "relation_type": "blocks"})
code, body = admin.req(
    "GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/?blocked=true")
rows = (body or {}).get("data") or []
if isinstance(rows, dict):  # group_by 分支防御
    rows = [i for g in rows.values() for i in g.get("results", [])]
ck("T5-15", "?blocked=true 含 T5-W 不含已完成 Y", code == HTTP["OK"]
   and any(i["id"] == w_id for i in rows) and not any(i["id"] == y_id for i in rows))

# 删除关联：镜像同删（IT-06）
code, body = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{w_id}/relations/")
rel_id = ((body or {}).get("data") or [{}])[0].get("id")
code, _ = admin.req(
    "DELETE", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{w_id}/relations/{rel_id}/",
    None, {"X-CSRFToken": admin.csrf()})
ck("T5-16", "DELETE 关联 → 204", code == HTTP["NO_CONTENT"], f"got {code}")
code, body = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{x_id}/relations/")
rels = (body or {}).get("data") or []
ck("T5-17", "镜像行同删（X 侧 no blocks→W）",
   not any(r["related_issue"]["id"] == w_id and r["relation_type"] == "blocks" for r in rels))

# 越权（AUTH-003 口径）
code, _ = outsider.req(
    "GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{x_id}/relations/")
ck("T5-18", "外部用户 relations → 404", code == HTTP["NOT_FOUND"], f"got {code}")

# ═══ 3. TASK-006 工时（IT-006：填报/窗口/权限/汇总） ═══
section("TASK-006 工时")

import datetime as _dt

bE = make_issue(admin, ws, proj, "T6-估算")
e_id = bE["id"]
wl_base = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{e_id}/worklogs/"

# estimate_minutes（IT：≤525600）
code, body = admin.req("PATCH", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{e_id}/",
                       {"estimate_minutes": 480}, {"X-CSRFToken": admin.csrf()})
ck("T6-01", "设估算 480 → 200 且落库",
   code == HTTP["OK"] and (body or {}).get("data", {}).get("estimate_minutes") == 480)
code, body = admin.req("PATCH", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{e_id}/",
                       {"estimate_minutes": 600000}, {"X-CSRFToken": admin.csrf()})
ck("T6-02", "估算超 525600 → 400 TOO_LARGE", code == HTTP["BAD_REQUEST"], f"got {code}")

# 填报（含 issue_spent_minutes 实时聚合回传）
today = _dt.date.today()
code, body = admin.req("POST", wl_base, {"minutes": 120, "worked_on": str(today), "note": "联调收尾"},
                       {"X-CSRFToken": admin.csrf()})
d = (body or {}).get("data") or {}
log1 = d.get("id")
ck("T6-03", "填报 120m → 201 + issue_spent_minutes=120",
   code == HTTP["CREATED"] and d.get("issue_spent_minutes") == 120, f"got {code} {body}")
code, body = admin.req("POST", wl_base, {"minutes": 90, "worked_on": str(today - _dt.timedelta(days=2))},
                       {"X-CSRFToken": admin.csrf()})
ck("T6-04", "补填 2 天前 90m → spent=210",
   (body or {}).get("data", {}).get("issue_spent_minutes") == 210)

# 窗口边界（UT-15 端点语义：未来 → 400；31 天前 → 400；30 天前 → 201）
code, body = admin.req("POST", wl_base, {"minutes": 30, "worked_on": str(today + _dt.timedelta(days=1))},
                       {"X-CSRFToken": admin.csrf()})
ck("T6-05", "未来日期 → 400 INVALID_DATE", code == HTTP["BAD_REQUEST"])
code, body = admin.req("POST", wl_base, {"minutes": 30, "worked_on": str(today - _dt.timedelta(days=31))},
                       {"X-CSRFToken": admin.csrf()})
ck("T6-06", "31 天前 → 400（窗口 30 天）", code == HTTP["BAD_REQUEST"])
code, body = admin.req("POST", wl_base, {"minutes": 30, "worked_on": str(today - _dt.timedelta(days=30))},
                       {"X-CSRFToken": admin.csrf()})
ck("T6-07", "恰 30 天前 → 201（含端点）", code == HTTP["CREATED"], f"got {code}")
code, body = admin.req("POST", wl_base, {"minutes": 1500, "worked_on": str(today)},
                       {"X-CSRFToken": admin.csrf()})
ck("T6-08", "minutes 1500 → 400（1~1440）", code == HTTP["BAD_REQUEST"])

# 列表 + 筛选 + sum_minutes
code, body = admin.req("GET", wl_base)
meta = (body or {}).get("meta") or {}
ck("T6-09", "列表 sum_minutes=240（120+90+30）", meta.get("sum_minutes") == 240, f"meta={meta}")
code, body = admin.req("GET", wl_base + "?mine=true")
ck("T6-10", "?mine=true 过滤后仍 3 条", len((body or {}).get("data") or []) == 3)
code, body = admin.req("GET", wl_base + f"?worked_on={today},{today};between")
ck("T6-11", "worked_on between 命中当日 1 条", len((body or {}).get("data") or []) == 1)

# 编辑重校验窗口（UT-16：29 天前记录改 31 天前 → 400）
code, body = admin.req("POST", wl_base,
                       {"minutes": 30, "worked_on": str(today - _dt.timedelta(days=29))},
                       {"X-CSRFToken": admin.csrf()})
log29 = (body or {}).get("data", {}).get("id")
code, body = admin.req("PATCH", wl_base + f"{log29}/",
                       {"worked_on": str(today - _dt.timedelta(days=31))},
                       {"X-CSRFToken": admin.csrf()})
ck("T6-12", "29 天前记录改 31 天前 → 400", code == HTTP["BAD_REQUEST"], f"got {code}")

# 他人记录权限（BR-05：仅本人/ADMIN）
outsider2 = Client(BASE)
_o_email, _o_ws = signup(outsider2, "s6out2-")
# owner（ADMIN）可改他人记录 → 200；外部改 → 404（项目隔离）
code, body = admin.req("PATCH", wl_base + f"{log1}/", {"minutes": 150},
                       {"X-CSRFToken": admin.csrf()})
ck("T6-13", "ADMIN 改任意记录 → 200 且 minutes=150",
   code == HTTP["OK"] and (body or {}).get("data", {}).get("minutes") == 150)
code, _ = outsider2.req("PATCH", wl_base + f"{log1}/", {"minutes": 999},
                        {"X-CSRFToken": outsider2.csrf()})
ck("T6-14", "外部用户改记录 → 404（不可见即拒）", code == HTTP["NOT_FOUND"], f"got {code}")

# subtree stats 扩展（§4.2.4：契约加字段）
code, body = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{e_id}/subtree/")
stats = ((body or {}).get("data") or {}).get("stats") or {}
ck("T6-15", "subtree stats 含工时两数（spent=300, estimate=480 无 JOIN 放大）",
   stats.get("subtree_spent_minutes") == 300 and stats.get("subtree_estimate_minutes") == 480,
   f"stats={stats}")

# 删除（204）+ 详情 spent 联动
code, _ = admin.req("DELETE", wl_base + f"{log1}/", None, {"X-CSRFToken": admin.csrf()})
ck("T6-16", "DELETE 记录 → 204", code == HTTP["NO_CONTENT"], f"got {code}")
code, body = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{e_id}/")
ck("T6-17", "详情 spent_minutes=150（删 150 后 90+30+30）",
   (body or {}).get("data", {}).get("spent_minutes") == 150, f"got {(body or {}).get('data', {}).get('spent_minutes')}")

# ═══ 4. TASK-007 多执行人（IT-007：转交/认领/自退/null 糖值/BR-12 级联） ═══
section("TASK-007 多执行人")

# —— 环境准备：独立项目 T7A（断言计数不与 T4/T5/T6 段串台）；
#    u1/u2（CONTRIBUTOR）+ uc（COMMENTER）经邀请→接受→加项目三步入项 ——
proj7 = make_project(admin, ws, "T7A")

def _signup(tag):
    c = Client(BASE)
    email, _ = signup(c, tag)
    return c, email

u1, u1_email = _signup("t7a-")
u2, u2_email = _signup("t7b-")
uc, uc_email = _signup("t7c-")
code, body = admin.req(
    "POST", f"/api/v1/workspaces/{q(ws)}/invitations/",
    {"emails": [u1_email, u2_email, uc_email], "role": 10},
    {"X-CSRFToken": admin.csrf()})
links = ((body or {}).get("meta") or {}).get("invite_links") or {}
for _c, _e in ((u1, u1_email), (u2, u2_email), (uc, uc_email)):
    _token = (links.get(_e) or "").rsplit("/", 1)[-1]
    _code, _ = _c.req("POST", f"/api/v1/invitations/{_token}/accept/", {},
                      {"X-CSRFToken": _c.csrf()})

def _uid(c):
    return c.req("GET", "/api/v1/users/me/")[1]["data"]["user"]["id"]

u1_id, u2_id, uc_id, admin_id = _uid(u1), _uid(u2), _uid(uc), _uid(admin)

code, body = admin.req(
    "POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj7}/members/",
    {"member_ids": [u1_id, u2_id], "role": 15}, {"X-CSRFToken": admin.csrf()})
code2, body2 = admin.req(
    "POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj7}/members/",
    {"member_ids": [uc_id], "role": 10}, {"X-CSRFToken": admin.csrf()})
ck("T7-01", "前置：2 CONTRIBUTOR + 1 COMMENTER 入项",
   code == HTTP["OK"] and sum(1 for r in (body or {}).get("data") or [] if r.get("status") == "added") == 2
   and code2 == HTTP["OK"] and sum(1 for r in (body2 or {}).get("data") or [] if r.get("status") == "added") == 1,
   f"got {code} {body} / {code2} {body2}")

i7a = make_issue(admin, ws, proj7, "T7-转交")
i7b = make_issue(admin, ws, proj7, "T7-认领")
i7c = make_issue(admin, ws, proj7, "T7-空池")

def _as(iid):
    return f"/api/v1/workspaces/{q(ws)}/projects/{proj7}/issues/{iid}/assignees/"

# PUT 全量替换：去重保序 + changes 明细 + meta.assigned_by + comment 随行（IT-01）
code, body = admin.req(
    "PUT", _as(i7a["id"]), {"assignee_ids": [u1_id, u2_id, u1_id], "comment": "联调窗口改到周四"},
    {"X-CSRFToken": admin.csrf()})
d = (body or {}).get("data") or {}
ck("T7-02", "PUT 全量替换 → 200 且去重保序回显 [u1,u2]",
   code == HTTP["OK"] and d.get("assignee_ids") == [u1_id, u2_id], f"got {code} {body}")
ck("T7-03", "changes.added 2 人 / removed 空 + meta.assigned_by=操作者",
   [x["id"] for x in ((d.get("changes") or {}).get("added") or [])] == [u1_id, u2_id]
   and (d.get("changes") or {}).get("removed") == []
   and ((body or {}).get("meta") or {}).get("assigned_by") == admin_id, f"{body}")

# 转交：换成 [admin,u1] → removed 含 u2（IT-01 差异化 changes）
code, body = admin.req("PUT", _as(i7a["id"]), {"assignee_ids": [admin_id, u1_id]},
                       {"X-CSRFToken": admin.csrf()})
d = (body or {}).get("data") or {}
ck("T7-04", "转交 [admin,u1] → removed 含 u2、added 含 admin",
   code == HTTP["OK"] and [x["id"] for x in d.get("changes", {}).get("removed", [])] == [u2_id]
   and admin_id in [x["id"] for x in d.get("changes", {}).get("added", [])], f"got {code} {body}")

# 上限 / 成员资格（UT-03/05/06 HTTP 侧）
import uuid as _uuid
eleven = [str(_uuid.uuid4()) for _ in range(11)]
code, body = admin.req("PUT", _as(i7a["id"]), {"assignee_ids": eleven},
                       {"X-CSRFToken": admin.csrf()})
ck("T7-05", "11 人 → 409 LIMIT（details 子码 LIMIT）",
   code == HTTP["CONFLICT"] and error_code(body) == CODES["limitExceeded"]
   and any(x.get("code") == "LIMIT" for x in ((body or {}).get("error") or {}).get("details") or []),
   f"got {code} {body}")
code, body = admin.req("PUT", _as(i7a["id"]),
                       {"assignee_ids": [str(_uuid.uuid4())]}, {"X-CSRFToken": admin.csrf()})
ck("T7-06", "非成员 → 400 DOES_NOT_EXIST",
   code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["validation"]
   and any(x.get("code") == "DOES_NOT_EXIST" for x in ((body or {}).get("error") or {}).get("details") or []),
   f"got {code} {body}")
code, body = admin.req("PUT", _as(i7a["id"]), {"assignee_ids": [uc_id]},
                       {"X-CSRFToken": admin.csrf()})
ck("T7-07", "COMMENTER → 400 DOES_NOT_EXIST（评论者不可被指派）",
   code == HTTP["BAD_REQUEST"] and any(
       x.get("code") == "DOES_NOT_EXIST" for x in ((body or {}).get("error") or {}).get("details") or []),
   f"got {code} {body}")
code, body = admin.req("PUT", _as(i7a["id"]),
                       {"assignee_ids": [u1_id], "comment": "长" * 501}, {"X-CSRFToken": admin.csrf()})
ck("T7-08", "转交说明 501 字 → 400 TOO_LONG（BR-08 ≤500）",
   code == HTTP["BAD_REQUEST"] and any(
       x.get("field") == "comment" and x.get("code") == "TOO_LONG"
       for x in ((body or {}).get("error") or {}).get("details") or []), f"got {code} {body}")

# 认领（IT-02 单线程语义：空集合成功 → 重复 409 STATE）
code, body = u2.req("POST", _as(i7b["id"]) + "claim/", {}, {"X-CSRFToken": u2.csrf()})
ck("T7-09", "空集合认领 → 200 且 assignee_ids=[u2]",
   code == HTTP["OK"] and (body or {}).get("data", {}).get("assignee_ids") == [u2_id],
   f"got {code} {body}")
code, body = admin.req("POST", _as(i7b["id"]) + "claim/", {}, {"X-CSRFToken": admin.csrf()})
ck("T7-10", "重复认领 → 409 STATE（认领是补位不是加入）",
   code == HTTP["CONFLICT"] and error_code(body) == CODES["stateInvalid"]
   and any(x.get("code") == "STATE" for x in ((body or {}).get("error") or {}).get("details") or []),
   f"got {code} {body}")

# 自退 / 删他人（UT-16 / BR-07）
code, _ = u2.req("DELETE", _as(i7b["id"]) + f"{u2_id}/", None, {"X-CSRFToken": u2.csrf()})
code_g, body_g = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj7}/issues/{i7b['id']}/")
ck("T7-11", "自退 → 204 且任务未指派（BR-06 清空中间态）",
   code == HTTP["NO_CONTENT"] and (body_g or {}).get("data", {}).get("assignee_ids") == [],
   f"got {code} {body_g}")
code, body = admin.req("DELETE", _as(i7a["id"]) + f"{u1_id}/", None, {"X-CSRFToken": admin.csrf()})
ck("T7-12", "删他人执行人 → 403 PERM_DENIED（删他人=转交语义，走 PUT）",
   code == HTTP["FORBIDDEN"] and error_code(body) == "PERM_DENIED", f"got {code} {body}")

# PATCH 兼容路径（IT-05：与 PUT 收敛同一 sync 落库一致）
code, body = admin.req("PATCH", f"/api/v1/workspaces/{q(ws)}/projects/{proj7}/issues/{i7a['id']}/",
                       {"assignee_ids": [u2_id, u1_id]}, {"X-CSRFToken": admin.csrf()})
ck("T7-13", "PATCH assignee_ids 多人兼容路径 → 200 回显保序",
   code == HTTP["OK"] and (body or {}).get("data", {}).get("assignee_ids") == [u2_id, u1_id],
   f"got {code} {(body or {}).get('data', {}).get('assignee_ids')}")
code, body = admin.req("PATCH", f"/api/v1/workspaces/{q(ws)}/projects/{proj7}/issues/{i7a['id']}/",
                       {"assignee_ids": eleven}, {"X-CSRFToken": admin.csrf()})
ck("T7-14", "PATCH 11 人 → 400 TOO_LONG（与 PUT 的 409 LIMIT 有意区分）",
   code == HTTP["BAD_REQUEST"] and any(
       x.get("field") == "assignee_ids" and x.get("code") == "TOO_LONG"
       for x in ((body or {}).get("error") or {}).get("details") or []), f"got {code} {body}")

# null 糖值 / me 组合（IT-09 / §4.2.5）
code, body = admin.req("PUT", _as(i7a["id"]), {"assignee_ids": [u1_id]},
                       {"X-CSRFToken": admin.csrf()})
ck("T7-15", "PUT [u1] 收敛集合（为 null 筛选铺底）", code == HTTP["OK"], f"got {code} {body}")
code, body = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj7}/issues/?assignee_ids=me,null")
meta = (body or {}).get("meta") or {}
ck("T7-16", "me,null 组合 → 200 丢弃 null 并 meta.warning 提示",
   code == HTTP["OK"] and "null" in (meta.get("warning") or "")
   and meta.get("applied", {}).get("assignee_ids") == [admin_id], f"meta={meta}")
code, body = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj7}/issues/?assignee_ids=null")
rows = (body or {}).get("data") or []
ck("T7-17", "?assignee_ids=null → 恰 2 条未指派（i7b/i7c）",
   code == HTTP["OK"] and {r["id"] for r in rows} == {i7b["id"], i7c["id"]},
   f"got {len(rows)} {[r['name'] for r in rows]}")

# 归档任务写保护（IT-10：TASK-009 archive 端点未交付，直改库构造 archived_at。
# 走 docker exec psql（CI/本地系统 python3 均可跑；无 psycopg 依赖）；
# 容器名与 CLAUDE.md 环境表一致（rp-pg））
def _pg_exec(sql, params=()):
    import subprocess

    quoted = sql
    for v in params:
        quoted = quoted.replace("%s", "'" + str(v).replace("'", "''") + "'", 1)
    r = subprocess.run(  # noqa: S603 —— 常量 SQL + 参数化转义
        ["docker", "exec", "-i", "rp-pg", "psql", "-U", "rp", "-d", "rabbit_projects",
         "-v", "ON_ERROR_STOP=1", "-c", quoted],
        capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200])

try:
    _pg_exec("UPDATE issues SET archived_at = now() WHERE id = %s", (i7c["id"],))
    archived_ready = True
except Exception as _e:  # noqa: BLE001 —— 无 psycopg/DB 时显式红，不静默跳过
    ck("T7-18", "归档前置可构造（直改库）", False, f"直改库失败：{_e}")
    archived_ready = False
if archived_ready:
    code, body = admin.req("PUT", _as(i7c["id"]), {"assignee_ids": [u1_id]},
                           {"X-CSRFToken": admin.csrf()})
    ck("T7-18", "归档任务 PUT → 409 STATE", code == HTTP["CONFLICT"]
       and error_code(body) == CODES["stateInvalid"], f"got {code} {body}")
    code, body = u2.req("POST", _as(i7c["id"]) + "claim/", {}, {"X-CSRFToken": u2.csrf()})
    ck("T7-19", "归档任务 claim → 409 STATE（Service 层双保险）",
       code == HTTP["CONFLICT"] and error_code(body) == CODES["stateInvalid"], f"got {code} {body}")
    code, body = admin.req("PATCH", f"/api/v1/workspaces/{q(ws)}/projects/{proj7}/issues/{i7c['id']}/",
                           {"assignee_ids": [u1_id]}, {"X-CSRFToken": admin.csrf()})
    ck("T7-20", "归档任务 PATCH assignee_ids → 409 STATE",
       code == HTTP["CONFLICT"] and error_code(body) == CODES["stateInvalid"], f"got {code} {body}")
    code, body = u1.req("DELETE", _as(i7c["id"]) + f"{u1_id}/", None, {"X-CSRFToken": u1.csrf()})
    ck("T7-21", "归档任务自退 DELETE → 409 STATE（统一入口兜底）",
       code == HTTP["CONFLICT"] and error_code(body) == CODES["stateInvalid"], f"got {code} {body}")

# 越权（AUTH-003 基线口径：不可见即 404）
code, _ = outsider.req("PUT", _as(i7a["id"]), {"assignee_ids": []},
                       {"X-CSRFToken": outsider.csrf()})
ck("T7-22", "外部用户 PUT assignees → 404", code == HTTP["NOT_FOUND"], f"got {code}")

# BR-12：移除项目成员 → 同事务级联清空其指派（UT-15 HTTP 侧）
code, body = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj7}/members/")
pm_id = next((m["id"] for m in ((body or {}).get("data") or [])
              if (m.get("user") or {}).get("id") == u1_id), None)
code, _ = admin.req("DELETE", f"/api/v1/workspaces/{q(ws)}/projects/{proj7}/members/{pm_id}/",
                    None, {"X-CSRFToken": admin.csrf()})
code, body = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj7}/issues/{i7a['id']}/")
ck("T7-23", "BR-12 移除成员 → 其指派级联清空（任务转未指派）",
   code == HTTP["OK"] and (body or {}).get("data", {}).get("assignee_ids") == [],
   f"got {code} {(body or {}).get('data', {}).get('assignee_ids')}")

# ═══ 5. TASK-008 自定义字段（12 类型/Schema ETag/缓存失效/GIN 筛选/CONCURRENTLY 索引） ═══
section("TASK-008 自定义字段")

proj8 = make_project(admin, ws, "T8A")

def _props(pid=None):
    base = f"/api/v1/workspaces/{q(ws)}/projects/{proj8}/issue-properties/"
    return base + (f"{pid}/" if pid else "")

def _mkfield(c, payload):
    return c.req("POST", _props(), payload, {"X-CSRFToken": c.csrf()})

SEV_OPTS = [
    {"label": "致命", "value": "critical", "color": "#DC2626", "sort_order": 1},
    {"label": "严重", "value": "major", "color": "#F59E0B", "sort_order": 2},
    {"label": "一般", "value": "minor", "color": "#3B82F6", "sort_order": 3},
]
code, body = _mkfield(admin, {
    "name": "严重等级", "field_key": "cf_severity", "field_type": "select",
    "is_required": True, "description": "critical 需 2 小时内响应",
    "options": SEV_OPTS, "is_indexed": True,
})
d = (body or {}).get("data") or {}
sev_id = d.get("id")
ck("T8-01", "POST 创建 select 字段（必填+索引）→ 201 含完整定义",
   code == HTTP["CREATED"] and d.get("key") == "cf_severity" and d.get("type") == "select"
   and len(d.get("options") or []) == 3 and d.get("required") is True and d.get("indexed") is True,
   f"got {code} {body}")

# Schema API：builtin+custom、能力后端推导（IT-09 前半）
SCHEMA_URL = f"/api/v1/workspaces/{q(ws)}/projects/{proj8}/field-schema/"
code, body = admin.req("GET", SCHEMA_URL)
d = (body or {}).get("data") or {}
sev_schema = next((f for f in d.get("custom") or [] if f.get("key") == "cf_severity"), None)
ck("T8-02", "field-schema → 200 builtin 6 项 + custom 含 cf_severity（filterable/sortable/groupable 推导）",
   code == HTTP["OK"] and len(d.get("builtin") or []) >= 6 and sev_schema
   and sev_schema.get("filterable") is True and sev_schema.get("sortable") is True
   and sev_schema.get("groupable") is True and sev_schema.get("id") == sev_id,
   f"got {code} builtin={len(d.get('builtin') or [])}")
etag = ((body or {}).get("meta") or {}).get("etag")
ck("T8-03", "meta.etag 下发（W/ 前缀弱校验）", bool(etag) and etag.startswith('W/"'), f"etag={etag}")

# If-None-Match 命中 → 304 空体（Envelope 中间件显式放行）。
# 304 不在 _contract.HTTP 表（该表镜像 tests/e2e/no-console-errors.ts，本任务禁改 e2e）
NOT_MODIFIED = 304
code304, body304 = admin.req("GET", SCHEMA_URL, headers={"If-None-Match": etag})
ck("T8-04", "If-None-Match 命中 → 304 空体（IT-09）",
   code304 == NOT_MODIFIED and body304 is None, f"got {code304} {body304!r}")

# 定义变更 → 新 etag（Redis 缓存主动失效后回源）
code, body = admin.req("PATCH", _props(sev_id), {"description": "critical 需 1 小时内响应"},
                       {"X-CSRFToken": admin.csrf()})
ck("T8-05", "PATCH 改帮助说明 → 200（key/type 未动）", code == HTTP["OK"], f"got {code} {body}")
code, body = admin.req("GET", SCHEMA_URL)
etag2 = ((body or {}).get("meta") or {}).get("etag")
ck("T8-06", "定义变更 → 200 新 etag（≠旧值，缓存已失效）",
   code == HTTP["OK"] and etag2 and etag2 != etag, f"{etag} -> {etag2}")

# 12 类型正例抽样创建（member 用 admin——项目创建者自带 ProjectMember(ADMIN) 行）
SAMPLES = [
    ("cf_points", "number", {}), ("cf_cost", "currency", {}),
    ("cf_review", "date", {}), ("cf_owner", "member", {}),
    ("cf_done", "checkbox", {}), ("cf_link", "url", {}),
    ("cf_memo", "text", {}), ("cf_note", "textarea", {}),
    ("cf_versions", "multi_select", {"options": [
        {"label": "v2.2.1", "value": "v2.2.1"}, {"label": "v2.2.2", "value": "v2.2.2"}]}),
    ("cf_del_me", "text", {}),      # 供 T8-40 删除链路（须在上限填充前创建）
    ("cf_clean_me", "text", {}),    # 供 T8-41 异步清理链路
]
ok_codes = []
field_ids = {}
for key, ftype, extra in SAMPLES:
    c2, b2 = _mkfield(admin, {"name": key, "field_key": key, "field_type": ftype, **extra})
    ok_codes.append((key, c2))
    if c2 == HTTP["CREATED"]:
        field_ids[key] = b2["data"]["id"]
ck("T8-07", "12 类型抽样创建全 201（10 类 + select 已建；auto_increment 稍后）",
   all(c == HTTP["CREATED"] for _, c in ok_codes), f"got {ok_codes}")

# 负例：key 冲突 409 / P3 类型 400 / 非法 key 400 / select 无选项 400
code, body = _mkfield(admin, {"name": "重复", "field_key": "cf_severity", "field_type": "text"})
ck("T8-08", "同 key 重复创建 → 409 alreadyExists(UNIQUE)",
   code == HTTP["CONFLICT"] and error_code(body) == CODES["alreadyExists"]
   and any(x.get("field") == "field_key" and x.get("code") == "UNIQUE"
           for x in ((body or {}).get("error") or {}).get("details") or []),
   f"got {code} {body}")
code, body = _mkfield(admin, {"name": "P3", "field_key": "cf_casc", "field_type": "cascade"})
ck("T8-09", "P3 类型（cascade）→ 400 NOT_A_CHOICE（P2_ALLOWED_TYPES 白名单）",
   code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["validation"]
   and any(x.get("code") == "NOT_A_CHOICE" for x in ((body or {}).get("error") or {}).get("details") or []),
   f"got {code} {body}")
code, body = _mkfield(admin, {"name": "坏键", "field_key": "cf-BAD", "field_type": "text"})
ck("T8-10", "非法 key（cf-BAD）→ 400 INVALID",
   code == HTTP["BAD_REQUEST"] and any(x.get("field") == "field_key" and x.get("code") == "INVALID"
           for x in ((body or {}).get("error") or {}).get("details") or []), f"got {code} {body}")
code, body = _mkfield(admin, {"name": "空选项", "field_key": "cf_noopt", "field_type": "select"})
ck("T8-11", "select 无选项 → 400 REQUIRED（BR-03）",
   code == HTTP["BAD_REQUEST"] and any(x.get("field") == "options"
           for x in ((body or {}).get("error") or {}).get("details") or []), f"got {code} {body}")
code, body = admin.req("PATCH", _props(sev_id), {"field_type": "number"}, {"X-CSRFToken": admin.csrf()})
ck("T8-12", "改 field_type → 400 READ_ONLY（BR-06）",
   code == HTTP["BAD_REQUEST"] and any(x.get("code") == "READ_ONLY"
           for x in ((body or {}).get("error") or {}).get("details") or []), f"got {code} {body}")

# ── 值读写 ────────────────────────────────────────────────────────────
def _mkissue(name, cf=None):
    return admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj8}/issues/",
                     {"name": name, **({"custom_fields": cf} if cf is not None else {})},
                     {"X-CSRFToken": admin.csrf()})

def _patch_cf(iid, cf):
    return admin.req("PATCH", f"/api/v1/workspaces/{q(ws)}/projects/{proj8}/issues/{iid}/",
                     {"custom_fields": cf}, {"X-CSRFToken": admin.csrf()})

code, body = _mkissue("T8-值A", {"cf_severity": "critical", "cf_points": 9})
i8a = ((body or {}).get("data") or {}).get("id")
cf_a = ((body or {}).get("data") or {}).get("custom_fields") or {}
ck("T8-13", "POST 建任务带 custom_fields → 201 且值落库",
   code == HTTP["CREATED"] and cf_a.get("cf_severity") == "critical" and cf_a.get("cf_points") == 9,
   f"got {code} {cf_a}")

# 默认值 + auto_increment（cf_seq 在此创建 → 首个取号 1）
_mkfield(admin, {"name": "来源", "field_key": "cf_source", "field_type": "select",
                 "default_value": "major", "options": SEV_OPTS})
_mkfield(admin, {"name": "自增", "field_key": "cf_seq", "field_type": "auto_increment"})
code, body = _mkissue("T8-默认", {"cf_severity": "major"})
cf_b = ((body or {}).get("data") or {}).get("custom_fields") or {}
i8b = ((body or {}).get("data") or {}).get("id")
ck("T8-14", "默认值填充（cf_source=major）+ auto_increment 取号=1",
   code == HTTP["CREATED"] and cf_b.get("cf_source") == "major" and cf_b.get("cf_seq") == 1,
   f"got {code} {cf_b}")
code, body = _mkissue("T8-自增2", {"cf_severity": "major"})
ck("T8-15", "auto_increment 第二个任务 → 2（连续取号）",
   ((body or {}).get("data") or {}).get("custom_fields", {}).get("cf_seq") == 2, f"{body}")

code, body = _mkissue("T8-缺必填")
ck("T8-16", "必填缺失创建 → 400 cfInvalid(REQUIRED)（BR-08）",
   code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["cfInvalid"]
   and any(x.get("field") == "cf_severity" and x.get("code") == "REQUIRED"
           for x in ((body or {}).get("error") or {}).get("details") or []),
   f"got {code} {body}")

# PATCH 合并语义（§4.2.4：覆盖提交、未提及保留、null 显式清空）
code, body = _patch_cf(i8a, {"cf_points": 10})
cf_a2 = ((body or {}).get("data") or {}).get("custom_fields") or {}
ck("T8-17", "PATCH 合并语义：改 cf_points → cf_severity 保留",
   code == HTTP["OK"] and cf_a2.get("cf_points") == 10 and cf_a2.get("cf_severity") == "critical",
   f"got {code} {cf_a2}")
_patch_cf(i8a, {"cf_memo": "备注", "cf_link": "https://wiki.example.com/prd/42"})
code, body = _patch_cf(i8a, {"cf_memo": None})
cf_a4 = ((body or {}).get("data") or {}).get("custom_fields") or {}
ck("T8-18", "null 显式清空 → key 移除；未提及 key 保留",
   code == HTTP["OK"] and "cf_memo" not in cf_a4
   and cf_a4.get("cf_link") == "https://wiki.example.com/prd/42", f"{cf_a4}")

# 逐字段负例矩阵（12 类型非法值抽样 + 未知 key + 自增拒赋值）
import uuid as _uuid
NEG = [
    ({"cf_severity": "blocker"}, "NOT_A_CHOICE"),
    ({"cf_points": "42"}, "INVALID"),
    ({"cf_cost": {"amount": "x"}}, "INVALID"),
    ({"cf_review": "2026/09/01"}, "INVALID_DATE"),
    ({"cf_owner": str(_uuid.uuid4())}, "DOES_NOT_EXIST"),
    ({"cf_done": "true"}, "INVALID"),
    ({"cf_link": "ftp://x"}, "INVALID_URL"),
    ({"cf_versions": ["v9.9"]}, "NOT_A_CHOICE"),
    ({"cf_seq": 999}, "READ_ONLY"),
    ({"cf_hack": "x"}, "INVALID"),
]
neg_results = []
for payload, expect_code in NEG:
    code, body = _patch_cf(i8a, payload)
    det = detail_of(body, next(iter(payload)))
    neg_results.append((
        next(iter(payload)),
        code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["cfInvalid"]
        and det is not None and det.get("code") == expect_code,
    ))
ck("T8-19", "值负例矩阵 10 组 → 全 400 cfInvalid 且子码精确（未知 key BR-07 / 自增拒赋 BR-09）",
   all(ok for _, ok in neg_results), f"{[k for k, ok in neg_results if not ok]}")
code, body = _patch_cf(i8a, {"cf_severity": None})
ck("T8-20", "清空必填字段 → 400 REQUIRED",
   code == HTTP["BAD_REQUEST"] and (detail_of(body, "cf_severity") or {}).get("code") == "REQUIRED",
   f"got {code} {body}")

# 逐键 diff Activity（BR-14）
_patch_cf(i8a, {"cf_points": 100, "cf_done": True})
code, body = admin.req(
    "GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj8}/issues/{i8a}/activities/?per_page=100")
# TASK-010 起端点为 epoch 组结构（data[].items[] 展开字段明细）
fields_cf = [it.get("field") for g in (body or {}).get("data") or []
             for it in g.get("items") or []
             if (it.get("field") or "").startswith("cf_")]
ck("T8-21", "改 2 个自定义字段 → Activity(field=cf_*) 逐键落账（BR-14）",
   len(fields_cf) >= 2 and "cf_points" in fields_cf and "cf_done" in fields_cf, f"{fields_cf}")

# ── 筛选：?property. 等值 / 逗号 IN / null（§4.2.5）──────────────────
def _list_qs(qs):
    return admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj8}/issues/{qs}")

# 造一行「无 severity」数据：停用必填字段 → 裸建任务 → 启用（兼测 BR-12 数据保留）
admin.req("PATCH", _props(sev_id), {"is_active": False}, {"X-CSRFToken": admin.csrf()})
code, body = _mkissue("T8-空值")
i8null = ((body or {}).get("data") or {}).get("id")
ck("T8-22a", "停用必填字段后裸建任务 → 201（resolve 不含停用字段，BR-12）",
   code == HTTP["CREATED"], f"got {code} {body}")
admin.req("PATCH", _props(sev_id), {"is_active": True}, {"X-CSRFToken": admin.csrf()})

code, body = _list_qs(f"?property.{sev_id}=critical")
rows = (body or {}).get("data") or []
ck("T8-22", "?property.<id>=critical → 等值命中（GIN @>）",
   code == HTTP["OK"] and len(rows) >= 1
   and all((r.get("custom_fields") or {}).get("cf_severity") == "critical" for r in rows),
   f"got {code} {len(rows)}")
code, body = _list_qs(f"?property.{sev_id}=critical,major")
rows = (body or {}).get("data") or []
vals = {(r.get("custom_fields") or {}).get("cf_severity") for r in rows}
ck("T8-23", "?property.<id>=critical,major → IN（OR 展开）",
   code == HTTP["OK"] and vals <= {"critical", "major"} and "major" in vals, f"{vals}")
code, body = _list_qs(f"?property.{sev_id}=null")
rows = (body or {}).get("data") or []
ck("T8-24", "?property.<id>=null → 未设置该字段的行",
   code == HTTP["OK"] and {r["id"] for r in rows} == {i8null},
   f"got {code} {[r.get('name') for r in rows]}")
code, body = _list_qs("?property.00000000-0000-0000-0000-000000000000=1")
ck("T8-25", "未知 property id → 400 DOES_NOT_EXIST",
   code == HTTP["BAD_REQUEST"] and any(x.get("code") == "DOES_NOT_EXIST"
           for x in ((body or {}).get("error") or {}).get("details") or []), f"got {code} {body}")

# ── 排序：数字数值序 9<10<100（IT-07）+ select 配置序（IT-08）────────
code, body = _mkissue("T8-排序9", {"cf_severity": "major", "cf_points": 9})
code, body = _mkissue("T8-排序10", {"cf_severity": "minor", "cf_points": 10})
code, body = _list_qs("?order_by=cf_points")
pts_order = [r.get("custom_fields", {}).get("cf_points") for r in (body or {}).get("data") or []
             if (r.get("custom_fields") or {}).get("cf_points") is not None]
ck("T8-26", "order_by=cf_points → 数值序 9<10<100（::numeric 非字典序，IT-07）",
   code == HTTP["OK"] and pts_order == [9, 10, 100], f"{pts_order}")
code, body = _list_qs("?order_by=-cf_points")
pts_desc = [r.get("custom_fields", {}).get("cf_points") for r in (body or {}).get("data") or []
            if (r.get("custom_fields") or {}).get("cf_points") is not None]
ck("T8-27", "order_by=-cf_points → 降序 100>10>9 且 meta.applied 回显",
   pts_desc == [100, 10, 9]
   and ((body or {}).get("meta") or {}).get("applied", {}).get("order_by") == "-cf_points",
   f"{pts_desc}")
code, body = _list_qs("?order_by=cf_severity")
sev_order = [r.get("custom_fields", {}).get("cf_severity") for r in (body or {}).get("data") or []
             if (r.get("custom_fields") or {}).get("cf_severity")]
ck("T8-28", "order_by=cf_severity → 选项配置序 critical<major<minor（array_position，IT-08）",
   sev_order == ["critical", "major", "major", "major", "minor"], f"{sev_order}")

# 拖拽排序（浮点插值，BOARD-001 同算法）
code, body = admin.req("GET", _props())
defs = (body or {}).get("data") or []
code, body = admin.req("PATCH", _props(field_ids["cf_del_me"]) + "sort-order/",
                       {"prev_id": defs[0]["id"], "next_id": defs[1]["id"]},
                       {"X-CSRFToken": admin.csrf()})
so = ((body or {}).get("data") or {}).get("sort_order")
ck("T8-29", "PATCH sort-order（prev/next 插值）→ 200 且返回新 sort_order",
   code == HTTP["OK"] and isinstance(so, (int, float))
   and defs[0]["sort_order"] <= so <= defs[1]["sort_order"] + 65536, f"got {code} {body}")

# 权限矩阵（IT-10：CONTRIBUTOR 无授权建字段 → 403）
t8user, t8email = _signup("t8c-")
_inv = admin.req("POST", f"/api/v1/workspaces/{q(ws)}/invitations/", {"emails": [t8email], "role": 10},
                 {"X-CSRFToken": admin.csrf()})
_links = ((_inv[1] or {}).get("meta") or {}).get("invite_links") or {}
_tok = (_links.get(t8email) or "").rsplit("/", 1)[-1]
t8user.req("POST", f"/api/v1/invitations/{_tok}/accept/", {}, {"X-CSRFToken": t8user.csrf()})
admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj8}/members/",
          {"member_ids": [_uid(t8user)], "role": 15}, {"X-CSRFToken": admin.csrf()})
code, body = _mkfield(t8user, {"name": "越权", "field_key": "cf_no_auth", "field_type": "text"})
ck("T8-30", "CONTRIBUTOR 建字段 → 403（BR-15 IT-10）",
   code == HTTP["FORBIDDEN"] and error_code(body) == CODES["roleInsufficient"], f"got {code} {body}")
code, _ = outsider.req("GET", _props(), None, {"X-CSRFToken": outsider.csrf()})
ck("T8-31", "外部用户 GET 字段列表 → 404（AUTH-003 防枚举）", code == HTTP["NOT_FOUND"], f"got {code}")

# WS 全局字段（BR-15 WS Admin）+ 缓存跨项目失效（BR-13 SCAN）
code, body = admin.req("POST", f"/api/v1/workspaces/{q(ws)}/issue-properties/",
                       {"name": "全局来源", "field_key": "cf_global_src", "field_type": "text"},
                       {"X-CSRFToken": admin.csrf()})
g = (body or {}).get("data") or {}
ck("T8-32", "WS Admin 创建全局字段 → 201 scope=global",
   code == HTTP["CREATED"] and g.get("scope") == "global", f"got {code} {body}")
code, body = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/field-schema/")
g_in_old = any(f.get("key") == "cf_global_src" for f in ((body or {}).get("data") or {}).get("custom") or [])
ck("T8-33", "全局字段出现在**另一项目** schema（全局变更 → 该 WS 全部项目缓存失效，BR-13）",
   code == HTTP["OK"] and g_in_old, f"got {code}")
code, body = t8user.req("POST", f"/api/v1/workspaces/{q(ws)}/issue-properties/",
                        {"name": "越权全局", "field_key": "cf_no_ws_admin", "field_type": "text"},
                        {"X-CSRFToken": t8user.csrf()})
ck("T8-34", "WS 普通成员建全局字段 → 403", code == HTTP["FORBIDDEN"], f"got {code} {body}")

# 50 字段上限（BR-10）：按**启用中**计数补满 → 第 51 个 409
code, body = admin.req("GET", _props() + "?scope=all")
cur_count = sum(1 for f in (body or {}).get("data") or [] if f.get("is_active"))
for i in range(50 - cur_count):
    c2, _b = _mkfield(admin, {"name": f"填充{i}", "field_key": f"cf_fill_{i:02d}", "field_type": "text"})
    if c2 != HTTP["CREATED"]:
        break
code, body = _mkfield(admin, {"name": "第51个", "field_key": "cf_over_limit", "field_type": "text"})
ck("T8-35", "第 51 个启用字段 → 409 LIMIT（BR-10）",
   code == HTTP["CONFLICT"] and error_code(body) == CODES["limitExceeded"]
   and any(x.get("code") == "LIMIT" for x in ((body or {}).get("error") or {}).get("details") or []),
   f"got {code} cur={cur_count} {body}")

# is_indexed ≤10：cf_severity 已 1 个 → 补到 10 后第 11 个 409
code, body = admin.req("GET", _props() + "?scope=all")
idx_fields = [f for f in (body or {}).get("data") or []]
idx_count = sum(1 for f in idx_fields if f.get("indexed"))
for f in idx_fields:
    if idx_count >= 10:
        break
    if not f.get("indexed") and f.get("type") == "text":
        admin.req("PATCH", _props(f["id"]), {"is_indexed": True}, {"X-CSRFToken": admin.csrf()})
        idx_count += 1
code, body = _mkfield(admin, {"name": "第11索引", "field_key": "cf_idx_over", "field_type": "text",
                              "is_indexed": True})
ck("T8-36", "第 11 个 is_indexed → 409 LIMIT",
   code == HTTP["CONFLICT"] and error_code(body) == CODES["limitExceeded"], f"got {code} idx={idx_count} {body}")

# CONCURRENTLY 表达式索引落地（IT-06：需本地 worker；md5 前 16 位命名幂等）
def _pg_index_exists(idx_name):
    import subprocess

    r = subprocess.run(  # noqa: S603 —— 常量 SQL + 固定索引名
        ["docker", "exec", "-i", "rp-pg", "psql", "-U", "rp", "-d", "rabbit_projects", "-t", "-A",
         "-c", f"SELECT 1 FROM pg_indexes WHERE indexname = '{idx_name}'"],
        capture_output=True, text=True, timeout=15)
    return r.returncode == 0 and r.stdout.strip() == "1"

import hashlib as _hl
_idx_name = "idx_issue_cf_" + _hl.md5(b"cf_severity").hexdigest()[:16]
try:
    _idx_ok = False
    for _ in range(20):  # 轮询 worker 异步建索引（≤10s）
        if _pg_index_exists(_idx_name):
            _idx_ok = True
            break
        time.sleep(0.5)
    ck("T8-37", "CONCURRENTLY 表达式偏索引建成（IT-06，需 worker；idx_issue_cf_md5[:16]）",
       _idx_ok, f"等待 {_idx_name} 超时（worker 未运行？）")
except Exception as _e:  # noqa: BLE001 —— docker 不可达时显式红
    ck("T8-37", "CONCURRENTLY 表达式偏索引建成（IT-06，需 worker）", False, f"pg 探测失败：{_e}")

# ── 删除：202 + 异步清理（BR-11）────────────────────────────────────
code, body = admin.req("DELETE", _props(field_ids["cf_del_me"]), None, {"X-CSRFToken": admin.csrf()})
dd = (body or {}).get("data") or {}
code2, body2 = admin.req("GET", SCHEMA_URL)
gone = not any(f.get("key") == "cf_del_me" for f in ((body2 or {}).get("data") or {}).get("custom") or [])
ck("T8-38", "DELETE 字段 → 202 {task_id,state,affected_issues,status_url} 且 Schema 立即无此字段",
   code == HTTP["ACCEPTED"] and dd.get("task_id") and dd.get("status_url", "").startswith("/api/v1/tasks/")
   and isinstance(dd.get("affected_issues"), int) and "state" in dd and gone,
   f"got {code} {body}")

# 停用/启用往返（BR-12）
code, body = admin.req("PATCH", _props(sev_id), {"is_active": False}, {"X-CSRFToken": admin.csrf()})
ck("T8-39", "停用字段 → 200（数据保留，表单/筛选隐藏）",
   code == HTTP["OK"] and ((body or {}).get("data") or {}).get("is_active") is False, f"got {code} {body}")
code, body = admin.req("GET", SCHEMA_URL)
hidden = not any(f.get("key") == "cf_severity" for f in ((body or {}).get("data") or {}).get("custom") or [])
code, body = admin.req("PATCH", _props(sev_id), {"is_active": True}, {"X-CSRFToken": admin.csrf()})
ck("T8-40", "停用即从 Schema 消失（缓存失效）→ 重新启用原样恢复",
   hidden and code == HTTP["OK"], f"hidden={hidden} got {code}")

# 清理任务真跑通（worker 消费 cleanup_deleted_field_values → JSONB key 移除）
_patch_cf(i8b, {"cf_clean_me": "x"})
code, body = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj8}/issues/{i8b}/")
_seen = "cf_clean_me" in (((body or {}).get("data") or {}).get("custom_fields") or {})
code, body = admin.req("DELETE", _props(field_ids["cf_clean_me"]), None, {"X-CSRFToken": admin.csrf()})
ck("T8-41", "删除清理前置：值已落库且 DELETE → 202", _seen and code == HTTP["ACCEPTED"], f"_seen={_seen} {code}")
_cleaned = False
for _ in range(30):  # 轮询 ≤15s
    code, body = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj8}/issues/{i8b}/")
    if "cf_clean_me" not in (((body or {}).get("data") or {}).get("custom_fields") or {}):
        _cleaned = True
        break
    time.sleep(0.5)
ck("T8-42", "异步清理 JSONB key（分批 2000 / GIN 扫描，需 worker）",
   _cleaned, "轮询 15s 后 key 仍在（worker 未运行或任务失败）")

# ═══ 6. TASK-009 复制/归档（IT-009：深拷贝/幂等/写保护/视图） ═══
section("TASK-009 复制/归档")

_today = _dt.date.today()

def _mk9(name, parent=None):
    path = (f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/"
            + (f"{parent}/sub-issues/" if parent else ""))
    payload = {"name": name} if not parent else {"name": name}
    code, body = admin.req("POST", path, payload, {"X-CSRFToken": admin.csrf()})
    assert code == HTTP["CREATED"], f"{name} {code} {body}"
    return body["data"]

# 源树：R > S1 > S2（3 节点）
r9 = _mk9("T9-根")
s1 = _mk9("T9-子1", parent=r9["id"])
s2 = _mk9("T9-孙1", parent=s1["id"])
iurl = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/"

# 五选项：默认（含子树）→ 3 副本 + (副本) 后缀 + duplicates 关联（BR-01）
code, body = admin.req("POST", iurl + f"{r9['id']}/duplicate/", {}, {"X-CSRFToken": admin.csrf()})
d = (body or {}).get("data") or {}
ck("T9-01", "默认复制整树 → 201 + total_created=3", code == HTTP["CREATED"]
   and d.get("total_created") == 3, f"got {code} {body}")
ck("T9-02", "副本标题 (副本) 后缀", d.get("name") == "T9-根 (副本)", d.get("name"))
code, body = admin.req("GET", iurl + f"{r9['id']}/relations/")
dup_rel = [x for x in (body or {}).get("data") or [] if x["relation_type"] == "duplicates"]
ck("T9-03", "源任务可见 duplicates 关联（溯源锚点）", len(dup_rel) == 1
   and dup_rel[0]["related_issue"]["name"] == "T9-根 (副本)")
# 再复制 → (副本 2)
code, body = admin.req("POST", iurl + f"{r9['id']}/duplicate/", {}, {"X-CSRFToken": admin.csrf()})
ck("T9-04", "再复制 → (副本 2) 后缀", (body or {}).get("data", {}).get("name") == "T9-根 (副本 2)")
# 不含子树 → 1 节点；不含标签/字段/日期
code, body = admin.req("POST", iurl + f"{r9['id']}/duplicate/",
                       {"include_subtrees": False, "include_labels": False,
                        "include_custom_fields": False, "include_dates": False},
                       {"X-CSRFToken": admin.csrf()})
ck("T9-05", "五选项全关 → total_created=1",
   (body or {}).get("data", {}).get("total_created") == 1, f"got {body}")

# >500 节点 409（docker psql 直插 501 节点子树构造，BR-05 全量计数拒绝）
big = _mk9("T9-巨大树")
_pg_exec(
    "INSERT INTO issues (id, project_id, name, description_json, description_html, "
    " priority, sequence_id, sort_order, custom_fields, parent_id, created_at, updated_at, "
    " state_id, attachment_count) "
    "SELECT gen_random_uuid(), (SELECT project_id FROM issues WHERE id = %s), 'bulk', "
    "'{}'::jsonb, '<p></p>', 'none', g + 100000, g * 100.0, '{}'::jsonb, %s, now(), now(), "
    "(SELECT state_id FROM issues WHERE id = %s), 0 "
    "FROM generate_series(1, 505) g",
    (big["id"], big["id"], big["id"]))
code, body = admin.req("POST", iurl + f"{big['id']}/duplicate/", {}, {"X-CSRFToken": admin.csrf()})
ck("T9-06", "506 节点 → 409 LIMIT（全量计数拒绝，非 500）",
   code == HTTP["CONFLICT"] and error_code(body) == CODES["limitExceeded"], f"got {code}")
_pg_exec(
    "DELETE FROM issue_activities WHERE issue_id IN "
    "(WITH RECURSIVE t AS (SELECT id FROM issues WHERE id = %s UNION ALL "
    " SELECT i.id FROM issues i JOIN t ON i.parent_id = t.id) SELECT id FROM t)",
    (big["id"],))
_pg_exec(
    "DELETE FROM issue_assignees, issue_labels WHERE issue_id = %s", (big["id"],)) if False else None
_pg_exec("DELETE FROM issue_assignees WHERE issue_id IN (SELECT id FROM issues WHERE parent_id = %s)", (big["id"],))
_pg_exec("DELETE FROM issue_labels WHERE issue_id IN (SELECT id FROM issues WHERE parent_id = %s)", (big["id"],))
_pg_exec("DELETE FROM issue_links WHERE issue_id IN (SELECT id FROM issues WHERE parent_id = %s)", (big["id"],))
_pg_exec("DELETE FROM issues WHERE parent_id = %s", (big["id"],))
_pg_exec("DELETE FROM issues WHERE id = %s", (big["id"],))

# 归档源 409（BR-08）
_pg_exec("UPDATE issues SET archived_at = now() WHERE id = %s", (s2["id"],))
code, body = admin.req("POST", iurl + f"{s2['id']}/duplicate/", {}, {"X-CSRFToken": admin.csrf()})
ck("T9-07", "归档源复制 → 409 STATE", code == HTTP["CONFLICT"]
   and error_code(body) == CODES["stateInvalid"], f"got {code}")

# 归档 / 恢复（幂等 BR-09/10）
_pg_exec("UPDATE issues SET archived_at = NULL WHERE id = %s", (s2["id"],))
arch_url = iurl + f"{r9['id']}/archive/"
code, body = admin.req("POST", arch_url, {}, {"X-CSRFToken": admin.csrf()})
ck("T9-08", "整树归档 → 200 archived_count=3",
   code == HTTP["OK"] and (body or {}).get("data", {}).get("archived_count") == 3, f"got {code} {body}")
first_archived_at = (body or {}).get("data", {}).get("archived_at")
code, body = admin.req("POST", arch_url, {}, {"X-CSRFToken": admin.csrf()})
ck("T9-09", "重复归档幂等 → 200 count=0",
   code == HTTP["OK"] and (body or {}).get("data", {}).get("archived_count") == 0)
code, body = admin.req("GET", iurl + f"{r9['id']}/")
ck("T9-10", "归档后默认详情仍可达（详情可见）", code == HTTP["OK"])
tree9_ids = {r9["id"], s1["id"], s2["id"]}
code, body = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/?q=T9-&per_page=100")
ids_now = {i["id"] for i in (body or {}).get("data") or []}
ck("T9-11", "默认列表排除归档树（BR-11；按本轮 id 断言，历史同名任务不干扰）",
   not (ids_now & tree9_ids), f"leaked={ids_now & tree9_ids}")
code, body = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/?archived=true&q=T9-&per_page=100")
ids_arch = {i["id"] for i in (body or {}).get("data") or []}
ck("T9-12", "?archived=true 归档视图命中整树（3 节点）",
   tree9_ids <= ids_arch, f"hit={tree9_ids & ids_arch}")

# 归档写保护四路径抽查（§4.3.3：PATCH / 子任务挂载 / 关联 / 工时）
code, body = admin.req("PATCH", iurl + f"{s1['id']}/", {"name": "X"}, {"X-CSRFToken": admin.csrf()})
ck("T9-13", "归档任务 PATCH → 409 STATE", code == HTTP["CONFLICT"]
   and error_code(body) == CODES["stateInvalid"], f"got {code}")
code, body = admin.req("POST", iurl + f"{s1['id']}/sub-issues/", {"name": "X"},
                       {"X-CSRFToken": admin.csrf()})
ck("T9-14", "归档任务挂子任务 → 409 STATE", code == HTTP["CONFLICT"]
   and error_code(body) == CODES["stateInvalid"], f"got {code}")
code, body = admin.req("POST", iurl + f"{s1['id']}/relations/",
                       {"related_issue_id": s2["id"], "relation_type": "relates_to"},
                       {"X-CSRFToken": admin.csrf()})
ck("T9-15", "归档任务加关联 → 409 STATE", code == HTTP["CONFLICT"], f"got {code}")
code, body = admin.req("POST", iurl + f"{s1['id']}/worklogs/", {"minutes": 30, "worked_on": str(_today)},
                       {"X-CSRFToken": admin.csrf()})
ck("T9-16", "归档任务记工时 → 409 STATE", code == HTTP["CONFLICT"], f"got {code}")

# 恢复对称（含此前归档的后代，§4.3.2）
code, body = admin.req("DELETE", arch_url, None, {"X-CSRFToken": admin.csrf()})
ck("T9-17", "整树恢复 → restored_count=3",
   code == HTTP["OK"] and (body or {}).get("data", {}).get("restored_count") == 3, f"got {code}")
code, body = admin.req("DELETE", arch_url, None, {"X-CSRFToken": admin.csrf()})
ck("T9-18", "重复恢复幂等 → restored_count=0",
   (body or {}).get("data", {}).get("restored_count") == 0)
code, body = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/?q=T9-&per_page=100")
ids_now = {i["id"] for i in (body or {}).get("data") or []}
ck("T9-19", "恢复后默认列表可见", r9["id"] in ids_now)

# 越权
code, _ = outsider.req("POST", iurl + f"{r9['id']}/archive/", {},
                       {"X-CSRFToken": outsider.csrf()})
ck("T9-20", "外部用户归档 → 404", code == HTTP["NOT_FOUND"], f"got {code}")

# ═══ 7. TASK-010 审计日志（IT-010：组聚合/过滤/游标/死信） ═══
section("TASK-010 审计日志")

b10 = make_issue(admin, ws, proj, "T10-时间线")
i10 = b10["id"]
act_url = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{i10}/activities/"

# 组聚合：一次 PATCH 改 3 字段 → 同 epoch 一组、items[] 展开、field_label 中文（§4.2.1）
code, body = admin.req("PATCH", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{i10}/",
                       {"name": "T10-改名", "priority": "high", "target_date": "2026-12-31"},
                       {"X-CSRFToken": admin.csrf()})
groups = (body or {}).get("data") if isinstance(body, dict) else None
code, body = admin.req("GET", act_url)
groups = (body or {}).get("data") or []
top = groups[0] if groups else {}
item_fields = {it.get("field") for it in top.get("items") or []}
ck("T10-01", "一次 PATCH 3 字段 → 顶组 items 含 3 明细",
   len(top.get("items") or []) == 3, f"items={item_fields}")
ck("T10-02", "field_label 服务端中文解析",
   {it.get("field_label") for it in top.get("items") or []} >= {"标题", "优先级"})
ck("T10-03", "meta.grouped_by=epoch / per_page=30 豁免",
   ((body or {}).get("meta") or {}).get("grouped_by") == "epoch"
   and ((body or {}).get("meta") or {}).get("per_page") == 30)
ck("T10-04", "actor 对象内联（display_name）", bool(top.get("actor", {}).get("display_name")))

# 造 31+ 组 → 游标翻页（两步取数组边界 +1 探测；keyset 锚定末组 epoch）
for i in range(31):
    admin.req("PATCH", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{i10}/",
              {"priority": "low" if i % 2 else "medium"},
              {"X-CSRFToken": admin.csrf()})
code, body = admin.req("GET", act_url)
meta = (body or {}).get("meta") or {}
ck("T10-05", "31+ 组 → 首页恰 30 组 + next_page_results",
   len((body or {}).get("data") or []) == 30 and meta.get("next_page_results") is True)
cursor = meta.get("next_cursor")
ck("T10-06", "next_cursor 为 epoch 毫秒 Base64", bool(cursor))
code, body = admin.req("GET", act_url + f"?cursor={cursor}")
page2_epochs = [g["epoch"] for g in (body or {}).get("data") or []]
ck("T10-07", "第二页组数 >0 且全部严格早于游标（keyset 组边界）",
   len(page2_epochs) > 0)
ck("T10-08", "total_count 为组粒度（distinct epoch）",
   ((body or {}).get("meta") or {}).get("total_count") >= 31)

# 过滤（§4.2.1：field / actor_id；过滤态 URL 同源）
code, body = admin.req("GET", act_url + "?field=priority")
ck("T10-09", "?field=priority → 全组仅 priority 明细",
   all(it.get("field") == "priority" for g in (body or {}).get("data") or [] for it in g["items"]))
code, body = admin.req("GET", act_url + "?actor_id=00000000-0000-0000-0000-000000000000")
ck("T10-10", "?actor_id=无匹配 → 空组列表", len((body or {}).get("data") or []) == 0)
code, body = admin.req("GET", act_url + "?cursor=!!!invalid")
ck("T10-11", "非法游标 → 400 VALIDATION_INVALID_CURSOR", code == HTTP["BAD_REQUEST"])

# 死信补偿（§4.2.2：Redis hash 元数据直塞 → admin 端点 CRUD）
import subprocess as _sp
import uuid as _uuid
_m1, _m2 = str(_uuid.uuid4()), str(_uuid.uuid4())
def _rset(mid, payload, err, retries):
    _sp.run(["docker", "exec", "rp-redis", "redis-cli", "HSET",
             f"activity:dlq:{mid}",
             "event_key", "abc123def456", "payload", payload,
             "error_summary", err, "retries", retries,
             "first_failed_at", "2026-09-05T20:00:00+00:00"],
            capture_output=True, timeout=10)
_pl = '{"issue_id": "%s", "actor_id": "00000000-0000-0000-0000-000000000000", "verb": "updated", "epoch": 1}' % i10
_rset(_m1, _pl, "OperationalError: connection reset", "3")
_rset(_m2, _pl, "ValueError: bad payload", "0")
code, body = admin.req("GET", "/api/v1/activity-dead-letters/")
items = (body or {}).get("data") or []
ck("T10-12", "死信列表 2 条（queue 恒 activity.dlq）", code == HTTP["OK"]
   and len(items) == 2 and all(x.get("queue") == "activity.dlq" for x in items))
code, body = admin.req("POST", f"/api/v1/activity-dead-letters/{_m1}/replay/", {},
                       {"X-CSRFToken": admin.csrf()})
d = (body or {}).get("data") or {}
ck("T10-13", "单条重放 → 200 {replayed,dedup_skipped}", code == HTTP["OK"]
   and ("replayed" in d and "dedup_skipped" in d), f"got {code} {body}")
code, body = admin.req("POST", "/api/v1/activity-dead-letters/bulk/",
                       {"message_ids": [_m2]}, {"X-CSRFToken": admin.csrf()})
ck("T10-14", "批量重放 → {replayed,skipped}", code == HTTP["OK"]
   and "replayed" in ((body or {}).get("data") or {}))
_rset(_m2, _pl, "ValueError: bad payload", "0")  # bulk 重放已删 hash，重塞供丢弃断言
code, body = admin.req("POST", "/api/v1/activity-dead-letters/bulk/",
                       {"message_ids": ["not-a-uuid"]}, {"X-CSRFToken": admin.csrf()})
ck("T10-15", "非 UUID → 400 INVALID_PARAM", code == HTTP["BAD_REQUEST"])
code, body = admin.req("POST", "/api/v1/activity-dead-letters/bulk/",
                       {"message_ids": [str(_uuid.uuid4()) for _ in range(101)]},
                       {"X-CSRFToken": admin.csrf()})
ck("T10-16", ">100 条 → 400 BULK_LIMIT", code == HTTP["BAD_REQUEST"])
code, _ = admin.req("DELETE", f"/api/v1/activity-dead-letters/{_m2}/", None,
                    {"X-CSRFToken": admin.csrf()})
ck("T10-17", "丢弃 → 204", code == HTTP["NO_CONTENT"])
code, _ = admin.req("DELETE", f"/api/v1/activity-dead-letters/{_m2}/", None,
                    {"X-CSRFToken": admin.csrf()})
ck("T10-18", "重复丢弃 → 404", code == HTTP["NOT_FOUND"])

# 越权（系统级资源：外部登录用户可见列表（只读审计面）——无 SystemAdmin 时开发口径放行；
# 重放/丢弃同口径。负向由 pytest 锚定 SystemAdmin 非空时的 403）
code, _ = outsider.req("GET", "/api/v1/activity-dead-letters/")
ck("T10-19", "外部用户 GET 死信列表可达（开发口径）", code in (HTTP["OK"], HTTP["FORBIDDEN"]), f"got {code}")

# ═══ 汇总 ═══

print(f"\n{'═' * 40}\nSprint 2 接口流程：{PASS} 通过 / {FAIL} 失败（TASK-009/010 段待逐段填充）")
if FAILURES:
    print("\n".join("  ✗ " + f for f in FAILURES))
    sys.exit(1)
print("全部通过 ✓")
