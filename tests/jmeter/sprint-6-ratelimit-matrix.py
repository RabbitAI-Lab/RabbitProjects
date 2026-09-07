#!/usr/bin/env python3
"""Sprint-6 INFRA-005 §7.2.1 限流矩阵——逐端点触发 429 的可重复验收脚本。

用法：python3 tests/jmeter/sprint-6-ratelimit-matrix.py [http://localhost:8000]
前置：API 进程须以 RATE_LIMIT_ENABLED=1 启动（L2 全局四类 + AuthBurst 受门控；
      收编类 Report/Bulk/ShareUnlock 全环境生效不受门控）。缺省关闭的栈上跑，
      第一行（用户桶）即失败并提示。
口径（api-conventions §7.2 冻结表逐行）：
  用户桶 60/min（61 次→第 61 次 429 信封+头归零）｜auth 10/min·IP（错密码 10
  次 401 后第 11 次 429）｜报表 10/min（第 11 次）｜批量 10/min（第 11 次）｜
  presign 30/min（第 31 次）。
  分享解锁 5 失败/10min 行由 pytest test_file_shares（UT-05/IT-08）承载——
  建 FileAsset 依赖对象存储上传链路，HTTP 矩阵里造数成本不成比例。
数据域：用户 s6rm-*/项目 S6RM-*，结束按域清理（幂等；FK 全序同 sprint-5-flow）。
"""
from __future__ import annotations

import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
PASS = FAIL = 0


def ck(name: str, desc: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    mark = "✓" if ok else "✗"
    print(f"  {mark} {name} {desc}" + ("" if ok else f"  ← {extra}"))
    PASS, FAIL = PASS + (1 if ok else 0), FAIL + (0 if ok else 1)


class Client:
    """带独立 cookie jar 的 HTTP 客户端（tests/jmeter/_contract.py 同范式，本
    脚本自包含不 import —— _contract 无 __main__ 守卫之外的可复用请求层）。"""

    def __init__(self, base: str = BASE):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))

    def req(self, method: str, path: str, data=None, want_headers=False):
        body = json.dumps(data).encode() if data is not None else None
        h = {"Accept": "application/json", "Content-Type": "application/json",
             "Referer": self.base + "/"}
        tok = [c.value for c in self.jar if c.name == "csrftoken"]
        if tok and method in ("POST", "PATCH", "PUT", "DELETE"):
            h["X-CSRFToken"] = tok[0]
        r = urllib.request.Request(
            self.base + urllib.parse.quote(path, safe="/?&=:%+,"),
            data=body, method=method, headers=h)
        try:
            with self.opener.open(r, timeout=15) as resp:
                pair = (resp.status, _json(resp.read()), dict(resp.headers.items()))
        except urllib.error.HTTPError as e:
            pair = (e.code, _json(e.read()), dict(e.headers.items()))
        return pair if want_headers else pair[:2]


def _json(raw: bytes):
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return {"_raw": raw[:200].decode(errors="replace")}


def _pg(sql: str) -> str:
    import subprocess
    r = subprocess.run(
        ["docker", "exec", "-i", "rp-pg", "psql", "-U", "rp", "-d",
         "rabbit_projects", "-tAc", sql],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    return r.stdout.strip()


def _signup(c: Client, prefix: str) -> tuple[str, str]:
    c.req("GET", "/api/v1/auth/csrf-token/")
    import uuid
    email = f"{prefix}-{uuid.uuid4().hex[:6]}@rabbit.dev"
    code, body = c.req("POST", "/api/v1/auth/sign-up/",
                       {"email": email, "password": "Rabbit123!",
                        "display_name": prefix})
    assert code == 201, (code, body)
    ws = body["data"]["default_workspace_slug"]
    return email, ws


def _fresh_project(c: Client, ws: str, tag: str) -> str:
    code, body = c.req("POST", f"/api/v1/workspaces/{ws}/projects/",
                       {"name": f"S6RM {tag}", "identifier": f"S6R{tag[-1]}"})
    if code != 201:  # 标识符唯一性兜底（同分钟重跑）
        code, body = c.req("POST", f"/api/v1/workspaces/{ws}/projects/",
                           {"name": f"S6RM {tag}", "identifier": f"S6R{tag[-1]}{int(time.time()) % 97}"})
    assert code == 201, (code, body)
    return body["data"]["id"]


_CLEAN = (
    "DELETE FROM comment_reactions WHERE "
        "comment_id IN ("
        "SELECT id FROM issue_comments WHERE issue_id IN ("
            "SELECT id FROM issues WHERE project_id IN (SELECT id FROM projects WHERE identifier LIKE 'S6R%')))",
    "DELETE FROM issue_comments WHERE "
        "issue_id IN ("
        "SELECT id FROM issues WHERE project_id IN (SELECT id FROM projects WHERE identifier LIKE 'S6R%'))",
    "DELETE FROM issue_activities WHERE "
        "issue_id IN ("
        "SELECT id FROM issues WHERE project_id IN (SELECT id FROM projects WHERE identifier LIKE 'S6R%'))",
    "DELETE FROM work_logs WHERE "
        "issue_id IN ("
        "SELECT id FROM issues WHERE project_id IN (SELECT id FROM projects WHERE identifier LIKE 'S6R%'))",
    "DELETE FROM issue_assignees WHERE "
        "issue_id IN ("
        "SELECT id FROM issues WHERE project_id IN (SELECT id FROM projects WHERE identifier LIKE 'S6R%'))",
    "DELETE FROM issue_labels WHERE "
        "issue_id IN ("
        "SELECT id FROM issues WHERE project_id IN (SELECT id FROM projects WHERE identifier LIKE 'S6R%'))",
    "DELETE FROM issue_links WHERE "
        "issue_id IN ("
        "SELECT id FROM issues WHERE project_id IN (SELECT id FROM projects WHERE identifier LIKE 'S6R%'))",
    "DELETE FROM issues WHERE "
        "project_id IN ("
        "SELECT id FROM projects WHERE identifier LIKE 'S6R%')",
    "DELETE FROM issue_activities WHERE "
        "project_id IN ("
        "SELECT id FROM projects WHERE identifier LIKE 'S6R%')",
    "DELETE FROM labels WHERE "
        "project_id IN ("
        "SELECT id FROM projects WHERE identifier LIKE 'S6R%')",
    "DELETE FROM states WHERE "
        "project_id IN ("
        "SELECT id FROM projects WHERE identifier LIKE 'S6R%')",
    "DELETE FROM project_members WHERE "
        "project_id IN ("
        "SELECT id FROM projects WHERE identifier LIKE 'S6R%')",
    "DELETE FROM issue_views WHERE "
        "project_id IN ("
        "SELECT id FROM projects WHERE identifier LIKE 'S6R%')",
    "DELETE FROM project_status_logs WHERE "
        "project_id IN ("
        "SELECT id FROM projects WHERE identifier LIKE 'S6R%')",
    "DELETE FROM project_favorites WHERE "
        "project_id IN ("
        "SELECT id FROM projects WHERE identifier LIKE 'S6R%')",
    "DELETE FROM file_folders WHERE "
        "project_id IN ("
        "SELECT id FROM projects WHERE identifier LIKE 'S6R%')",
    "DELETE FROM upload_sessions WHERE "
        "project_id IN ("
        "SELECT id FROM projects WHERE identifier LIKE 'S6R%')",
    "DELETE FROM file_assets WHERE "
        "project_id IN ("
        "SELECT id FROM projects WHERE identifier LIKE 'S6R%')",
    "DELETE FROM custom_field_definitions WHERE "
        "project_id IN ("
        "SELECT id FROM projects WHERE identifier LIKE 'S6R%')",
    "DELETE FROM projects WHERE identifier LIKE 'S6R%'",
)


def _wait_window() -> None:
    """等翻入下一分钟固定窗口（HTTP 模式清不到 API 进程 LocMem，同 R4 纪律）。"""
    time.sleep(61 - (time.time() % 60) + 1)


def main() -> int:
    print(f"═ INFRA-005 §7.2.1 限流矩阵 · {BASE} ═")
    for sql in _CLEAN:  # 开局清扫
        try:
            _pg(sql)
        except RuntimeError:
            pass

    _wait_window()  # 上一次运行（或并行栈）的 auth 桶计数可能在当前窗口内
    # ── 行 0：批量建号（auth 桶同 IP 共享——全部 signup 集中在前 4 计数内，
    # 行 2 爆破垫后独占窗口，互不挤占）──
    c3 = Client()
    _, ws3 = _signup(c3, "s6rm-r1")
    c4 = Client()
    _, ws4 = _signup(c4, "s6rm-b1")
    c5 = Client()
    _, _ws5 = _signup(c5, "s6rm-p1")

    # ── 行 1：用户桶 60/min（61 次 → 第 61 次 429 信封 + 头归零）──
    c1 = Client()
    email1, ws1 = _signup(c1, "s6rm-u1")
    _wait_window()
    codes = [c1.req("GET", "/api/v1/users/me/")[0] for _ in range(61)]
    code, body, hdrs = c1.req("GET", "/api/v1/users/me/", want_headers=True)
    ck("S6-M1-01", "用户桶前 60 次 200 / 第 61 次 429",
       codes[:60] == [200] * 60 and codes[60] == 429, f"codes={codes[:5]}…{codes[-3:]}")
    ck("S6-M1-02", "429 信封 RATE_LIMIT_EXCEEDED + details.retry_after",
       body.get("error", {}).get("code") == "RATE_LIMIT_EXCEEDED"
       and body["error"]["details"][0]["field"] == "retry_after", json.dumps(body)[:160])
    ck("S6-M1-03", "X-RateLimit 三头（60/0/Unix）+ Retry-After ≥1",
       hdrs.get("X-RateLimit-Limit") == "60"
       and hdrs.get("X-RateLimit-Remaining") == "0"
       and int(hdrs.get("X-RateLimit-Reset", "0")) > time.time() - 1
       and int(hdrs.get("Retry-After", "0")) >= 1,
       f"hdrs={ {k: hdrs.get(k) for k in ('X-RateLimit-Limit', 'X-RateLimit-Remaining', 'Retry-After')} }")

    # ── 行 3：报表聚合 10/min（收编类不受门控；第 11 次 429）──
    # c3/ws3 已在行 0 批量建立（auth 桶同 IP 共享——signup 集中、爆破垫后）
    pid3 = _fresh_project(c3, ws3, "P3")
    _wait_window()
    codes = [c3.req("GET", f"/api/v1/workspaces/{ws3}/projects/{pid3}/stats/?days=30")[0]
             for _ in range(11)]
    ck("S6-M3-01", "报表前 10 次 200 / 第 11 次 429（10/min·user）",
       codes[:10] == [200] * 10 and codes[10] == 429, f"codes={codes}")

    # ── 行 4：批量端点 10/min（收编类；第 11 次 429）──
    pid4 = _fresh_project(c4, ws4, "P4")
    c4.req("POST", f"/api/v1/workspaces/{ws4}/projects/{pid4}/issues/",
           {"name": "矩阵任务", "priority": "none"})
    _wait_window()
    codes = []
    for _ in range(11):
        codes.append(c4.req(
            "POST", f"/api/v1/workspaces/{ws4}/projects/{pid4}/issues/bulk/preview/",
            {"issue_ids": [], "action": "move"})[0])
    # preview 空 issue_ids 的合法形态断言只看限流维度：前 10 非限流码、第 11 次 429
    ck("S6-M4-01", "批量前 10 次非 429 / 第 11 次 429（10/min·user）",
       all(c != 429 for c in codes[:10]) and codes[10] == 429, f"codes={codes}")

    # ── 行 5：presign 30/min（第 31 次 429；挂会话发起面 avatar/presign）──
    _wait_window()
    codes = []
    for _ in range(31):
        codes.append(c5.req("POST", "/api/v1/users/me/avatar/presign/",
                            {"filename": "a.png", "size": 100,
                             "content_type": "image/png"})[0])
    ck("S6-M5-01", "presign 前 30 次非 429 / 第 31 次 429（30/min·user）",
       all(c != 429 for c in codes[:30]) and codes[30] == 429, f"codes={codes[-3:]}")

    # ── 行 2：auth 突发 10/min·IP（垫后独占窗口——signup 已耗 5 计数在历史窗口）──
    c2 = Client()
    c2.req("GET", "/api/v1/auth/csrf-token/")
    _wait_window()
    auth_codes = []
    for _ in range(11):
        auth_codes.append(c2.req("POST", "/api/v1/auth/sign-in/",
                                 {"email": "nobody@rabbit.dev",
                                  "password": "wrong"})[0])
    code, body, hdrs = c2.req("POST", "/api/v1/auth/sign-in/",
                              {"email": "nobody@rabbit.dev",
                               "password": "wrong"}, want_headers=True)
    ck("S6-M2-01", "auth 区前 10 次 401 / 第 11 次 429（按 IP，与用户桶独立）",
       auth_codes[:10] == [401] * 10 and auth_codes[10] == 429,
       f"codes={auth_codes}")
    ck("S6-M2-02", "429 带 Retry-After ≥1（L2 信封口径）",
       code == 429 and int(hdrs.get("Retry-After", "0")) >= 1,
       f"code={code} RA={hdrs.get('Retry-After')}")


    for sql in _CLEAN:  # 收尾清理
        try:
            _pg(sql)
        except RuntimeError:
            pass
    left = _pg("SELECT count(*) FROM projects WHERE identifier LIKE 'S6R%';")
    print(f"\n{'═' * 40}\n限流矩阵：{PASS} 通过 / {FAIL} 失败（S6R 域残留 {left}）")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
