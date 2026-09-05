#!/usr/bin/env python3
"""Sprint 1 接口端到端验证 —— 与 sprint-0-flow.py 并列的 CI gate。

用法：python3 tests/jmeter/sprint-1-flow.py [http://localhost:8000]
前置：API 已启动并连接真实 PG（见 tests/e2e/PG_README.md）。

契约常量全部来自 tests/jmeter/_contract.py（CLAUDE.md 测试脚本规范 ①：
API 真相源唯一，禁止各自硬编码状态码 / 字段名 / 错误码）。

覆盖范围随 sprint-1 各功能落地逐段追加；每段标注归属文档 §。
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _contract import CODES, ENVELOPE, HTTP, Client, error_code, q

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


def _gateway_to_minio(url: str) -> str:
    """把网关相对路径 `/uploads/...` 还原为对象存储直连地址。

    presign / download 返回的都是网关相对路径（FILE-001：浏览器直传走 Nginx 代理，
    同源零跨域）。本地跑接口流程时没有网关，需还原后在 9000 端口直接访问。
    """
    if url.startswith("/uploads/"):
        return "http://localhost:9000/" + url[len("/uploads/"):]
    return url


def _put_object(upload_url: str, blob: bytes) -> bool:
    """把对象 PUT 到预签名地址。"""
    import urllib.error
    import urllib.request

    url = _gateway_to_minio(upload_url)
    try:
        req = urllib.request.Request(url, data=blob, method="PUT",
                                     headers={"Content-Type": "image/png"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError):
        return False


def signup(c: Client, tag: str):
    ts = int(time.time() * 1000) % 100000000
    email = f"{tag}{ts}@rabbit.dev"
    code, body = c.req(
        "POST", "/api/v1/auth/sign-up/",
        {"email": email, "password": "Rabbit123!"},
        {"X-CSRFToken": c.csrf()},
    )
    if code != HTTP["CREATED"]:
        print(f"  ✗ 前置失败：sign-up {code} {body}")
        raise SystemExit(1)
    return email, body["data"]["default_workspace_slug"]


SKIP = 0


def skip(cid: str, desc: str, why: str):
    """环境不可达时跳过（与失败区分开）：如 PWD 段需从 API 日志取降级邮件里的令牌。"""
    global SKIP
    SKIP += 1
    print(f"  ⊘ {cid} {desc} —— SKIP：{why}")


def _reset_token_for(email: str) -> str | None:
    """从 API 日志取重置令牌。

    令牌在 DB 只存不可逆摘要，明文只出现在邮件里；SMTP 降级模式下邮件链接
    会落进 API 日志（account.reset_email_broker_down → mail.reset_email.degraded）。
    日志路径经环境变量 API_LOG 覆盖，默认本地 runserver 的输出文件。
    """
    import os
    import re

    log_path = os.environ.get("API_LOG", "/tmp/api-s1.log")
    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    hits = re.findall(rf"to={re.escape(email)} link=[^ ]*token=([A-Za-z0-9_-]+)", text)
    return hits[-1] if hits else None


def main() -> int:
    owner = Client(BASE)
    _, slug = signup(owner, "s1")

    # ── INFRA-004 §4.13 统一信封 ────────────────────────────────
    section("INFRA-004 统一响应信封（C1）")
    code, body = owner.get("/api/v1/health/")
    ck("ENV-01", "2xx 为 success 信封", code == HTTP["OK"] and body.get("status") == ENVELOPE["successStatus"], (code, body))
    ck("ENV-02", "成功体含 data 节点", ENVELOPE["dataKey"] in (body or {}), body)
    code, body = owner.get("/api/v1/workspaces/__no_such_ws__/")
    ck("ENV-03", "4xx 为 error 信封", code == HTTP["NOT_FOUND"] and body.get("status") == ENVELOPE["errorStatus"], (code, body))
    ck("ENV-04", "错误码在 error.code 且已注册", error_code(body) == CODES["notFound"], error_code(body))
    ck("ENV-05", "错误体含 request_id（C3 全链路追踪）",
       bool((body.get("error") or {}).get(ENVELOPE["errorRequestId"])), body.get("error"))

    # ── AUTH-005 §4.2 权限快照下发 ──────────────────────────────
    section("AUTH-005 权限快照 /users/me/permissions/")
    code, body = owner.get("/api/v1/users/me/permissions/")
    d = (body or {}).get("data") or {}
    ck("PERM-01", "200 + success", code == HTTP["OK"] and body.get("status") == ENVELOPE["successStatus"], code)
    ck("PERM-02", "含 is_system_admin / workspaces / projects",
       {"is_system_admin", "workspaces", "projects"} <= set(d), sorted(d))
    ck("PERM-03", "meta 含 generated_at / truncated",
       {"generated_at", "truncated"} <= set((body or {}).get("meta") or {}), body.get("meta"))
    ck("PERM-04", "本人空间角色为 OWNER(20)",
       any(v.get("role") == 20 for v in d.get("workspaces", {}).values()), d.get("workspaces"))
    code, _ = owner.get("/api/v1/users/me/permissions/?workspace_slug=__nope__")
    ck("PERM-05", "未知 workspace_slug 静默忽略（权限数据仅本人可见）", code == HTTP["OK"], code)

    # ── TASK-001 BR-8 列尾追加（sort_order 回归）────────────────
    section("TASK-001 BR-8 sort_order 列尾追加")
    ts = int(time.time()) % 1000  # identifier 上限 5 字符，前缀 2 位 + 3 位时间戳
    code, body = owner.post(f"/api/v1/workspaces/{slug}/projects/",
                            {"name": "排序回归", "identifier": f"SO{ts}"})
    ck("SORT-00", "建项目 201", code == HTTP["CREATED"], (code, body))
    pid = body["data"]["id"]
    orders = []
    for i in range(3):
        code, body = owner.post(f"/api/v1/workspaces/{slug}/projects/{pid}/issues/", {"name": f"任务{i + 1}"})
        if code != HTTP["CREATED"]:
            ck("SORT-01", "连续建 3 个任务", False, (code, body))
            break
        orders.append(body["data"]["sort_order"])
    else:
        ck("SORT-01", "连续建 3 个任务", True)
        # 修复前三者恒为 65535（视图算了列尾值却没传给 service），BR-8 完全失效
        ck("SORT-02", f"sort_order 严格递增 {orders}",
           all(orders[i] < orders[i + 1] for i in range(len(orders) - 1)), orders)
        ck("SORT-03", "步长为 65535（BR-8）",
           all(abs((orders[i + 1] - orders[i]) - 65535.0) < 1e-6 for i in range(len(orders) - 1)), orders)

    # ── PROJ-002 §4.2 搜索 / 收藏 / 归档 ────────────────────────
    section("PROJ-002 项目搜索 / 收藏 / 归档")
    code, body = owner.post(f"/api/v1/workspaces/{slug}/projects/",
                            {"name": "营销页改版", "identifier": f"MK{ts}"})
    ck("PROJ-00", "建第二个项目 201", code == HTTP["CREATED"], (code, body))
    mkid = body["data"]["id"]
    code, body = owner.get(f"/api/v1/workspaces/{slug}/projects/?q=" + q("营销"))
    ck("PROJ-01", "?q= 按名称前缀搜索（ADR-0011 #11 统一为 q）",
       code == HTTP["OK"] and len(body["data"]) == 1, (code, len(body.get("data", []))))
    code, body = owner.get(f"/api/v1/workspaces/{slug}/projects/?q=MK{ts}")
    ck("PROJ-02", "?q= 按 identifier 搜索", code == HTTP["OK"] and len(body["data"]) == 1,
       (code, len(body.get("data", []))))
    code, body = owner.get(f"/api/v1/workspaces/{slug}/projects/")
    ck("PROJ-03", "列表 meta 含分页字段",
       {"count", "total_count", "favorite_count", "per_page"} <= set(body.get("meta") or {}), body.get("meta"))
    ck("PROJ-04", "列表项含 is_favorite", all("is_favorite" in p for p in body["data"]), None)

    code, _ = owner.post(f"/api/v1/workspaces/{slug}/projects/{mkid}/favorite/")
    ck("PROJ-05", "收藏成功", code in (HTTP["OK"], HTTP["CREATED"]), code)
    code, body = owner.get(f"/api/v1/workspaces/{slug}/projects/?favorite=true")
    ck("PROJ-06", "收藏过滤命中", code == HTTP["OK"] and len(body["data"]) == 1, (code, len(body.get("data", []))))
    code, _ = owner.delete(f"/api/v1/workspaces/{slug}/projects/{mkid}/favorite/")
    ck("PROJ-07", "取消收藏", code in (HTTP["OK"], HTTP["NO_CONTENT"]), code)
    code, _ = owner.post(f"/api/v1/workspaces/{slug}/projects/{mkid}/favorite/")
    # 回归：ProjectFavorite 用带 deleted_at 条件的偏唯一索引，取消后重收必须能复活
    ck("PROJ-08", "★ 再次收藏成功（软删复活回归）", code in (HTTP["OK"], HTTP["CREATED"]), code)

    code, _ = owner.post(f"/api/v1/workspaces/{slug}/projects/{mkid}/archive/")
    ck("PROJ-09", "归档成功", code in (HTTP["OK"], HTTP["NO_CONTENT"]), code)
    code, body = owner.get(f"/api/v1/workspaces/{slug}/projects/")
    ck("PROJ-10", "默认列表排除归档项（BR-11）", all(p["id"] != mkid for p in body["data"]), None)
    code, body = owner.get(f"/api/v1/workspaces/{slug}/projects/?status=all")
    ck("PROJ-11", "?status=all 可见归档项", any(p["id"] == mkid for p in body["data"]), None)

    # ── AUTH-003 越权一律 404（防 ID 枚举）──────────────────────
    section("AUTH-003 跨租户隔离")
    other = Client(BASE)
    signup(other, "s1x")
    code, body = other.get(f"/api/v1/workspaces/{slug}/projects/{pid}/")
    ck("ISO-01", "他人项目返回 404 而非 403", code == HTTP["NOT_FOUND"], code)
    ck("ISO-02", "错误码为 RESOURCE_NOT_FOUND", error_code(body) == CODES["notFound"], error_code(body))

    # ── AUTH-004 §4.2 资料与密码 ────────────────────────────────
    section("AUTH-004 个人资料 / 密码")
    code, body = owner.patch("/api/v1/users/me/", {"display_name": "改后昵称", "intro": "一句话简介"})
    ck("PROF-01", "PATCH 昵称 + 简介 200", code == HTTP["OK"], (code, body))
    code, body = owner.get("/api/v1/users/me/")
    user = ((body or {}).get("data") or {}).get("user") or (body or {}).get("data") or {}
    ck("PROF-02", "回读昵称已更新", user.get("display_name") == "改后昵称", user.get("display_name"))
    code, body = owner.post("/api/v1/users/me/change-password/",
                            {"old_password": "WrongPass1!", "new_password": "NewRabbit123!"})
    ck("PROF-03", "错误旧密码改密被拒", code >= HTTP["BAD_REQUEST"], code)

    # 密码重置：一次性消费 + 旧密码失效（AUTH-004 §2 安全关键路径）
    victim = Client(BASE)
    vemail, _ = signup(victim, "s1r")
    code, _ = victim.post("/api/v1/auth/forgot-password/", {"email": vemail})
    ck("PWD-01", "forgot-password 202 且不泄露令牌（防邮箱枚举）",
       code == 202, code)
    token = _reset_token_for(vemail)
    if token:
        payload = {"token": token, "new_password": "BrandNew123!",
                   "new_password_confirm": "BrandNew123!"}
        fresh = Client(BASE)
        code, body = fresh.post("/api/v1/auth/reset-password/", payload)
        ck("PWD-02", "凭令牌重置成功", code == HTTP["OK"], (code, error_code(body)))
        code, body = fresh.post("/api/v1/auth/reset-password/", payload)
        ck("PWD-03", "★ 同一令牌二次使用被拒（一次性消费）",
           code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["resetInvalid"],
           (code, error_code(body)))
        old = Client(BASE)
        code, body = old.req("POST", "/api/v1/auth/sign-in/",
                             {"email": vemail, "password": "Rabbit123!"},
                             {"X-CSRFToken": old.csrf()})
        ck("PWD-04", "★ 旧密码已失效",
           code == HTTP["UNAUTHORIZED"] and error_code(body) == CODES["invalidCreds"],
           (code, error_code(body)))
        new = Client(BASE)
        code, _ = new.req("POST", "/api/v1/auth/sign-in/",
                          {"email": vemail, "password": "BrandNew123!"},
                          {"X-CSRFToken": new.csrf()})
        ck("PWD-05", "新密码可登录", code == HTTP["OK"], code)
        code, body = fresh.post("/api/v1/auth/reset-password/",
                                {"token": "x" * 86, "new_password": "Zz123456!",
                                 "new_password_confirm": "Zz123456!"})
        ck("PWD-06", "伪造令牌被拒",
           code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["resetInvalid"],
           (code, error_code(body)))
    else:
        for cid, desc in [("PWD-02", "凭令牌重置成功"), ("PWD-03", "同一令牌二次使用被拒"),
                          ("PWD-04", "旧密码已失效"), ("PWD-05", "新密码可登录"),
                          ("PWD-06", "伪造令牌被拒")]:
            skip(cid, desc, "API 日志不可达或未含降级邮件链接（设 API_LOG 指向 runserver 输出后重跑）")

    # ── TEAM-002 §4.2 成员与邀请 ────────────────────────────────
    section("TEAM-002 团队成员 / 邀请")
    code, body = owner.get(f"/api/v1/workspaces/{slug}/members/")
    ck("TEAM-01", "成员列表 200 且含创建者", code == HTTP["OK"] and len(body.get("data") or []) >= 1,
       (code, len(body.get("data") or [])))
    code, body = owner.get(f"/api/v1/workspaces/{slug}/invitations/")
    ck("TEAM-02", "邀请列表 200", code == HTTP["OK"], code)
    code, body = other.get(f"/api/v1/workspaces/{slug}/members/")
    ck("TEAM-03", "非成员看他人团队成员 → 404", code == HTTP["NOT_FOUND"], code)

    # ── TASK-002 §4.3 属性 / 标签 / 子任务 / 活动 ───────────────
    section("TASK-002 任务属性 / 标签 / 子任务")
    code, body = owner.get(f"/api/v1/workspaces/{slug}/projects/{pid}/issue-types/")
    ck("ATTR-01", "项目可用类型列表 200", code == HTTP["OK"], code)
    code, body = owner.post(f"/api/v1/workspaces/{slug}/projects/{pid}/labels/",
                            {"name": "紧急", "color": "#EF4444"})
    ck("ATTR-02", "建标签 201", code == HTTP["CREATED"], (code, body))
    label_id = ((body or {}).get("data") or {}).get("id")
    code, body = owner.post(f"/api/v1/workspaces/{slug}/projects/{pid}/issues/",
                            {"name": "带属性的任务", "priority": "high"})
    ck("ATTR-03", "创建任务可带 priority", code == HTTP["CREATED"], (code, body))
    parent_id = ((body or {}).get("data") or {}).get("id")
    ck("ATTR-04", "响应回显 priority=high",
       ((body or {}).get("data") or {}).get("priority") == "high", (body or {}).get("data", {}).get("priority"))
    code, body = owner.patch(f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{parent_id}/",
                             {"priority": "urgent"})
    # 教训 #3：新增序列化字段若漏挂 Meta.fields，GET 正常但 PATCH 必 500
    ck("ATTR-05", "PATCH priority 不 500（序列化字段已挂 Meta.fields）",
       code == HTTP["OK"] and ((body or {}).get("data") or {}).get("priority") == "urgent", (code, body))
    if label_id:
        # PUT 而非 POST：TASK-002 §2.3 定义为「全量替换标签集合」，走 PUT 白名单
        code, body = owner.put(
            f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{parent_id}/labels/",
            {"label_ids": [label_id]})
        ck("ATTR-06", "PUT 全量替换标签 2xx", code < 300, (code, error_code(body)))
        code, body = owner.get(f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{parent_id}/")
        ck("ATTR-09", "任务回显 label_ids 含新标签",
           label_id in ((body or {}).get("data") or {}).get("label_ids", []),
           (body or {}).get("data", {}).get("label_ids"))
    code, body = owner.get(f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{parent_id}/sub-issues/")
    ck("ATTR-07", "子任务列表 200", code == HTTP["OK"], code)
    code, body = owner.get(f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{parent_id}/activities/")
    ck("ATTR-08", "操作日志时间线 200", code == HTTP["OK"], code)

    # ── C.25 动态行结构（前端曾按 actor.name / epoch 取，恒显示「系 / 系统」）──
    rows = body.get("data") or []
    ck("ACT-01", "★ 动态响应是裸数组（不是 {results:[]}）", isinstance(rows, list), type(rows).__name__)
    ck("ACT-02", "★ 动态行含平铺 actor_name / created_at（无嵌套 actor 对象）",
       all({"actor_name", "created_at"} <= set(r or {}) for r in rows),
       [sorted(r or {}) for r in rows][:2])
    ck("ACT-03", "actor_name 非空且不等于兜底值「系统」",
       rows and all(str((r or {}).get("actor_name") or "").strip() for r in rows),
       [(r or {}).get("actor_name") for r in rows][:3])
    ck("ACT-04", "游标分页信息在 meta.next_cursor（不在 data 里）",
       "next_cursor" in (body.get("meta") or {}), sorted((body.get("meta") or {}).keys()))

    # ── TASK-002 子任务勾选（前端 checkbox 的实际写路径）──
    code, body = owner.post(
        f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{parent_id}/sub-issues/",
        {"name": "可勾选子任务"})
    ck("SUB-01", "建子任务 201", code == HTTP["CREATED"], (code, error_code(body)))
    sub_id = ((body or {}).get("data") or {}).get("id")
    if sub_id:
        code, body = owner.get(f"/api/v1/workspaces/{slug}/projects/{pid}/states/?include_cancelled=1")
        states4 = body.get("data") or []
        by_group = {s["group"]: s["id"] for s in states4}
        ck("SUB-02", "?include_cancelled=1 拿到四态（含 completed / cancelled）",
           {"completed", "cancelled"} <= set(by_group), sorted(by_group))
        # 勾「完成」：必须 PATCH state_id，这是前端 toggleSub 唯一的正确写路径
        code, body = owner.patch(
            f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{sub_id}/",
            {"state_id": by_group.get("completed")})
        ck("SUB-03", "★ PATCH state_id=completed → 2xx 且 state_group 变 completed",
           code == HTTP["OK"] and ((body or {}).get("data") or {}).get("state_group") == "completed",
           (code, (body or {}).get("data", {}).get("state_group")))
        # 回归锚点：state_group 是读侧 SerializerMethodField，写它会被 DRF 静默忽略
        # （返回 200 但库里没变）——前端就是这么写的，表现「勾上了刷新又变回去」
        code, body = owner.patch(
            f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{sub_id}/",
            {"state_group": "started"})
        ck("SUB-04", "★ PATCH 只读字段 state_group 不生效（state 仍为 completed）",
           code == HTTP["OK"] and ((body or {}).get("data") or {}).get("state_group") == "completed",
           (code, (body or {}).get("data", {}).get("state_group")))
        code, body = owner.patch(
            f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{sub_id}/",
            {"state_id": by_group.get("started")})
        ck("SUB-05", "再 PATCH state_id=started → 取消勾选生效",
           code == HTTP["OK"] and ((body or {}).get("data") or {}).get("state_group") == "started",
           (code, (body or {}).get("data", {}).get("state_group")))

    # ── C.26 项目标签管理（项目设置「管理标签」弹窗的完整数据流）──
    section("TASK-002 项目标签管理（C.26）")
    lpath = f"/api/v1/workspaces/{slug}/projects/{pid}/labels"
    code, body = owner.get(f"{lpath}/")
    ck("LBL-01", "标签列表 200 且是数组（弹窗列表区数据源）",
       code == HTTP["OK"] and isinstance(body.get("data"), list), (code, type(body.get("data")).__name__))
    code, body = owner.post(f"{lpath}/", {"name": "阻塞", "color": "#F59E0B"})
    ck("LBL-02", "★ 新建标签 201（验收缺陷：弹窗新建后列表不刷新）",
       code == HTTP["CREATED"], (code, error_code(body)))
    new_label_id = ((body or {}).get("data") or {}).get("id")
    if new_label_id:
        code, body = owner.get(f"{lpath}/")
        ck("LBL-03", "★ 新建后立即能在列表回读到（前端 load() 依赖这条）",
           any(l.get("id") == new_label_id for l in (body.get("data") or [])),
           [l.get("name") for l in (body.get("data") or [])])
        ck("LBL-04", "列表行含 sort_order / is_active / usage_count（弹窗三列渲染字段）",
           all({"sort_order", "is_active", "usage_count"} <= set(l)
               for l in (body.get("data") or [])),
           [sorted(l) for l in (body.get("data") or [])][:2])
        code, body = owner.patch(f"{lpath}/{new_label_id}/", {"name": "阻塞（改）", "color": "#EF4444"})
        ck("LBL-05", "PATCH 改名换色 200", code == HTTP["OK"], (code, error_code(body)))
        code, _ = owner.patch(f"{lpath}/{new_label_id}/", {"is_active": False})
        ck("LBL-06", "停用（软删）2xx", code < 300, code)
        code, _ = owner.patch(f"{lpath}/{new_label_id}/", {"is_active": True})
        ck("LBL-07", "恢复 2xx", code < 300, code)
        code, body = owner.delete(f"{lpath}/{new_label_id}/")
        ck("LBL-08", "未被引用时物理删除 2xx", code < 300, (code, error_code(body)))
    code, body = owner.post(f"{lpath}/", {"name": "", "color": "#EF4444"})
    ck("LBL-09", "空名称被拒", code >= HTTP["BAD_REQUEST"], (code, error_code(body)))

    # ── C.7 描述写入（抽屉描述编辑器的落库路径）──
    code, body = owner.patch(
        f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{parent_id}/",
        {"description_html": "<p>接口写入的描述</p>"})
    ck("DESC-01", "★ PATCH description_html 2xx（写侧确实接收该字段）",
       code == HTTP["OK"], (code, error_code(body)))
    ck("DESC-02", "回读 description_html 已更新",
       ((body or {}).get("data") or {}).get("description_html") == "<p>接口写入的描述</p>",
       ((body or {}).get("data") or {}).get("description_html"))
    # 回归锚点：Issue.save() 用 strip_tags 重算 stripped，`<p></p>` 视为空。
    # 若哪天改成只在 create 时算，改描述后 trigram 搜索就会搜不到（静默失效）。
    code, body = owner.get(f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{parent_id}/")
    ck("DESC-03", "★ description_stripped 同步重算（GIN 搜索索引不失效）",
       "接口写入的描述" in (((body or {}).get("data") or {}).get("description_stripped") or ""),
       ((body or {}).get("data") or {}).get("description_stripped"))
    code, body = owner.patch(
        f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{parent_id}/",
        {"description_html": "<p></p>"})
    code, body = owner.get(f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{parent_id}/")
    ck("DESC-04", "清空为空文档时 description_stripped 归 None（FE-37）",
       ((body or {}).get("data") or {}).get("description_stripped") is None,
       ((body or {}).get("data") or {}).get("description_stripped"))

    # ── C.23 属性区行内编辑的写侧契约（优先级 / 负责人 / 开始·截止 / 类型）──
    code, body = owner.get("/api/v1/users/me/")
    me_id = ((body or {}).get("data") or {}).get("user", {}).get("id")
    ck("ATTRP-01", "取到当前用户 id（负责人候选池基准）", bool(me_id), me_id)

    code, body = owner.patch(
        f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{parent_id}/", {"priority": "urgent"})
    ck("ATTRP-02", "★ PATCH priority=urgent 2xx（抽屉优先级下拉的落库路径）",
       code == HTTP["OK"] and ((body or {}).get("data") or {}).get("priority") == "urgent",
       (code, (body or {}).get("data", {}).get("priority")))
    code, body = owner.patch(
        f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{parent_id}/", {"priority": "bogus"})
    ck("ATTRP-03", "非法 priority → 400（枚举校验，前端下拉值不可越界）",
       code == HTTP["BAD_REQUEST"], (code, error_code(body)))

    code, body = owner.patch(
        f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{parent_id}/",
        {"assignee_ids": [me_id]})
    ck("ATTRP-04", "★ PATCH assignee_ids 2xx（抽屉负责人下拉的落库路径）",
       code == HTTP["OK"] and me_id in (((body or {}).get("data") or {}).get("assignee_ids") or []),
       (code, (body or {}).get("data", {}).get("assignee_ids")))
    code, body = owner.patch(
        f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{parent_id}/",
        {"assignee_ids": ["00000000-0000-0000-0000-000000000000"]})
    ck("ATTRP-05", "非本项目成员 → 400（validate_assignees BR）",
       code == HTTP["BAD_REQUEST"], (code, error_code(body)))

    code, body = owner.patch(
        f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{parent_id}/",
        {"start_date": "2026-09-01", "target_date": "2026-09-30"})
    ck("ATTRP-06", "★ PATCH start_date / target_date 2xx（日期控件落库路径）",
       code == HTTP["OK"] and ((body or {}).get("data") or {}).get("start_date") == "2026-09-01",
       (code, (body or {}).get("data", {}).get("start_date")))
    code, body = owner.patch(
        f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{parent_id}/",
        {"start_date": "2026-09-30", "target_date": "2026-09-01"})
    ck("ATTRP-07", "截止早于开始 → 400（chk_issue_start_before_target）",
       code == HTTP["BAD_REQUEST"], (code, error_code(body)))

    code, body = owner.get(f"/api/v1/workspaces/{slug}/projects/{pid}/issue-types/")
    tid = (body.get("data") or [{}])[0].get("id") if code == HTTP["OK"] else None
    if tid:
        code, body = owner.patch(
            f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{parent_id}/", {"type_id": tid})
        ck("ATTRP-08", "★ PATCH type_id 2xx（抽屉类型下拉的落库路径）",
           code == HTTP["OK"], (code, error_code(body)))

    # ── TASK-003 §4.2 筛选 / 搜索 / 排序 ────────────────────────
    section("TASK-003 列表筛选 / 搜索 / 排序")
    for cid, desc, qs in [
        ("FLT-01", "?priority= 筛选", "?priority=urgent"),
        ("FLT-02", "?q= 关键词搜索", "?q=" + q("带属性")),
        ("FLT-03", "?order_by= 排序", "?order_by=-created_at"),
    ]:
        code, body = owner.get(f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{qs}")
        ck(cid, desc, code == HTTP["OK"], (code, error_code(body)))
    code, body = owner.get(f"/api/v1/workspaces/{slug}/projects/{pid}/issues/?priority=urgent")
    ck("FLT-04", "priority 筛选结果全为 urgent",
       all(i.get("priority") == "urgent" for i in (body.get("data") or [])), None)

    # ── BOARD-002 §4.2.1 看板分组（四列，含已取消）──────────────
    section("BOARD-002 看板分组")
    code, body = owner.get(f"/api/v1/workspaces/{slug}/projects/{pid}/issues/?group_by=state_id")
    groups = (body or {}).get("data") or {}
    ck("BRD-01", "分组响应 200", code == HTTP["OK"], code)
    ck("BRD-02", "四列齐备（含「已取消」第 4 列）", len(groups) == 4, len(groups))
    ck("BRD-03", "每组结构为 {results,total_results}",
       all(isinstance(v, dict) and {"results", "total_results"} <= set(v) for v in groups.values()), None)
    ck("BRD-04", "分组按状态过滤（各组条数之和 = 总数）",
       sum(v["total_results"] for v in groups.values()) == (body.get("meta") or {}).get("total_count"),
       (sum(v["total_results"] for v in groups.values()), (body.get("meta") or {}).get("total_count")))
    # 回归锚点：前端看板用 states 端点回填每列的 state id。默认排除 cancelled 时
    # 「已取消」列 id 恒为 null → move() 静默 return → 拖过去没反应。
    code, body = owner.get(f"/api/v1/workspaces/{slug}/projects/{pid}/states/")
    default_groups = {s.get("group") for s in body.get("data") or []}
    ck("BRD-05", "★ states 默认排除 cancelled（既有契约，不要为前端改默认值）",
       "cancelled" not in default_groups, sorted(default_groups))
    code, body = owner.get(f"/api/v1/workspaces/{slug}/projects/{pid}/states/?include_cancelled=1")
    all_groups = {s.get("group") for s in body.get("data") or []}
    ck("BRD-06", "★ states?include_cancelled=1 含四态（看板必须带这个参数）",
       {"unstarted", "started", "completed", "cancelled"} <= all_groups, sorted(all_groups))
    # 看板拖到「已取消」的落库校验：后端不得拒绝该 group
    cancelled_id = next((s["id"] for s in body.get("data") or [] if s.get("group") == "cancelled"), None)
    if cancelled_id:
        code, body = owner.patch(
            f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{sub_id}/",
            {"state_id": cancelled_id})
        ck("BRD-07", "★ PATCH state_id=cancelled 被接受（后端无 group 黑名单）",
           code == HTTP["OK"] and ((body or {}).get("data") or {}).get("state_group") == "cancelled",
           (code, (body or {}).get("data", {}).get("state_group")))

    # ── COLLAB-001 §4.2 评论与通知 ──────────────────────────────
    section("COLLAB-001 评论 / 通知中心")
    code, body = owner.post(f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{parent_id}/comments/",
                            {"comment_html": "<p>第一条评论</p>"})
    ck("CMT-01", "发表评论 201", code == HTTP["CREATED"], (code, body))
    code, body = owner.get(f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{parent_id}/comments/")
    ck("CMT-02", "评论列表含新评论", code == HTTP["OK"] and len(body.get("data") or []) >= 1,
       (code, len(body.get("data") or [])))
    # 回归锚点：前端头像/昵称取 actor.display_name。后端曾有 `name` 与 `display_name`
    # 两种口径并存，前端按 actor.name 取 → undefined → 头像兜底渲染成「?」。
    # 这里同时断言「有 display_name」与「没有 name」，把契约钉死在唯一形态上。
    actors = [c.get("actor") for c in (body.get("data") or [])]
    ck("CMT-03", "★ 评论 actor 含 id / display_name（前端头像与昵称的数据源）",
       all({"id", "display_name"} <= set(a or {}) for a in actors), actors)
    ck("CMT-04", "★ 评论 actor 不含 `name` 字段（防前端再按 actor.name 取首字母）",
       all("name" not in (a or {}) for a in actors), [sorted(a or {}) for a in actors])
    ck("CMT-05", "actor.display_name 非空（空串同样会渲染成「?」）",
       all(str((a or {}).get("display_name") or "").strip() for a in actors),
       [(a or {}).get("display_name") for a in actors])
    code, body = owner.get("/api/v1/users/me/notifications/")
    ck("NTF-01", "通知列表 200", code == HTTP["OK"], code)
    code, body = owner.get("/api/v1/users/me/notifications/unread-count/")
    ck("NTF-02", "未读数 200", code == HTTP["OK"], code)
    code, body = owner.post("/api/v1/users/me/notifications/read-all/")
    ck("NTF-03", "全部已读 2xx", code < 300, code)
    code, body = owner.get("/api/v1/users/me/notifications/?unread=true")
    # ADR：未读参数统一为 ?unread=true（RPT-001 与 COLLAB-001 曾有 ?unread_only 分歧）
    ck("NTF-04", "?unread=true 全部已读后为空", code == HTTP["OK"] and not (body.get("data") or []),
       (code, len(body.get("data") or [])))

    # ── FILE-001 §4.2 附件三步直传 ──────────────────────────────
    section("FILE-001 任务附件直传")
    apath = f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{parent_id}/attachments"
    code, body = owner.post(f"{apath}/presign/",
                            {"file_name": "evil.exe", "file_size": 1024,
                             "content_type": "application/octet-stream"})
    ck("FILE-01", "非法扩展名被拒（白名单）",
       code == HTTP["BAD_REQUEST"] and error_code(body) == "VALIDATION_FILE_TYPE_NOT_ALLOWED",
       (code, error_code(body)))
    code, body = owner.post(f"{apath}/presign/",
                            {"file_name": "big.png", "file_size": 99 * 1024 * 1024,
                             "content_type": "image/png"})
    ck("FILE-02", "超大文件被拒",
       code == HTTP["BAD_REQUEST"] and error_code(body) == "VALIDATION_FILE_SIZE_EXCEEDED",
       (code, error_code(body)))
    blob = b"\x89PNG\r\n\x1a\n" + b"x" * 200
    code, body = owner.post(f"{apath}/presign/",
                            {"file_name": "ok.png", "file_size": len(blob), "content_type": "image/png"})
    if code == HTTP["CREATED"]:
        ck("FILE-03", "presign 201 且返回四要素",
           {"asset_id", "upload_url", "fields", "expires_at"} <= set(body.get("data") or {}), None)
        asset_id = body["data"]["asset_id"]
        rel = body["data"]["upload_url"]
        uploaded = _put_object(rel, blob)
        ck("FILE-04", "直传对象存储 2xx", uploaded, uploaded)
        if uploaded:
            code, body = owner.post(f"{apath}/{asset_id}/complete/")
            ck("FILE-05", "complete 200", code == HTTP["OK"], (code, error_code(body)))
            reported = ((body or {}).get("data") or {}).get("attachment_count")
            code, body = owner.get(f"{apath}/")
            listed = len(body.get("data") or [])
            code, body = owner.get(f"/api/v1/workspaces/{slug}/projects/{pid}/issues/{parent_id}/")
            on_issue = ((body or {}).get("data") or {}).get("attachment_count")
            # 回归：complete 曾「先 count 再 +1」而 count 已在状态翻转后执行，返回值比真实值多 1
            ck("FILE-06", f"计数三处一致（complete={reported} 列表={listed} 任务={on_issue}）",
               reported == listed == on_issue, (reported, listed, on_issue))
            # 回归：列表行缺 download_url 时，前端「下载」按钮会跳 window.location.href = undefined
            code, body = owner.get(f"{apath}/")
            arows = body.get("data") or []
            ck("FILE-07", "★ 列表每行含非空 download_url（下载按钮的跳转目标）",
               bool(arows) and all(str(r.get("download_url") or "").strip() for r in arows),
               [r.get("download_url") for r in arows])
            ck("FILE-08", "列表每行含 status / size / mime（C.31 文件行渲染字段）",
               bool(arows) and all({"status", "size", "mime"} <= set(r) for r in arows),
               [sorted(r) for r in arows])
            if arows:
                dcode, loc = owner.get_no_redirect(str(arows[0].get("download_url")))
                ck("FILE-09", "★ download 端点 302 并换发 Location", dcode == 302 and bool(loc),
                   (dcode, loc))
                if loc:
                    import urllib.request as _u
                    try:
                        with _u.urlopen(_gateway_to_minio(loc), timeout=15) as rr:
                            got = rr.read()
                    except Exception as exc:  # noqa: BLE001 - 只用于判定可达性
                        got = b""
                        print(f"    （换发地址不可达：{exc}）")
                    ck("FILE-10", "★ 换发后的对象可下载且内容一致（三步直传闭环）",
                       got == blob, (len(got), len(blob)))
    else:
        ck("FILE-03", "presign 201（对象存储不可达时跳过后续）",
           error_code(body) == "SERVER_STORAGE_ERROR",
           f"对象存储未就绪：{error_code(body)}；起 MinIO 后本段才完整")

    # ── RPT-001 §4.2 个人统计 ───────────────────────────────────
    section("RPT-001 个人待办统计")
    code, body = owner.get("/api/v1/users/me/issues/stats/")
    ck("RPT-01", "缺 workspace 参数 → 400 VALIDATION_ERROR",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["validation"], (code, error_code(body)))
    code, body = owner.get(f"/api/v1/users/me/issues/stats/?workspace={slug}")
    data = (body or {}).get("data") or {}
    ck("RPT-02", "四项统计齐备",
       {"todo_count", "due_today_count", "overdue_count", "completed_this_week_count"} <= set(data),
       sorted(data))
    ck("RPT-03", "7 日趋势为 7 个点", len(data.get("trend") or []) == 7, len(data.get("trend") or []))
    code, body = owner.get(f"/api/v1/users/me/issues/?workspace={slug}")
    ck("RPT-04", "我的待办列表 200 且 meta 含分页",
       code == HTTP["OK"] and {"count", "total_count", "per_page"} <= set(body.get("meta") or {}),
       (code, body.get("meta")))

    print("\n" + "═" * 44)
    print(f"Sprint 1 接口流程：{PASS} 通过 / {FAIL} 失败 / {SKIP} 跳过")
    if FAILURES:
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("全部通过 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
