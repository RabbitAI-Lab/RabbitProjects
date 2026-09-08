#!/usr/bin/env python3
"""Sprint 3 接口端到端验证 —— 与 sprint-0/1/2-flow.py 并列的 CI gate（六段）。

用法：python3 tests/jmeter/sprint-3-flow.py [http://localhost:8000]
前置：API + 真实 PG + Redis + RabbitMQ + activity 队列 worker（通知/Activity 异步）。
推荐 `uv run --project apps/api python tests/jmeter/sprint-3-flow.py`（直改库走
docker exec psql，无需本地 psycopg）。COLLAB-004 段 WS 最小闭环为纯标准库实现
（环境无第三方 ws 客户端依赖）；verify-rooms 内部端点需 INTERNAL_KEY（从运行中
api 进程环境发现，找不到则该断言 SKIP 并注明由 live 侧覆盖）。

六段对齐六规格（docs/sprint-3-views-collab/）：
  1. BOARD-003  视图 CRUD / 内置种子 / 五维分组 / 默认偏好 / 越权
  2. TASK-011  嵌套 DSL / 三源 AND / 边界拒绝 / 占位符 / cf 全类型
  3. COLLAB-002 线程回复 / reactions / 图片评论 / 通知三互斥 / 归档只读
  4. COLLAB-003 动态流合流 / 折叠 / 过滤矩阵 / 组感知游标 / epoch 明细
  5. BOARD-004 批量四端点全矩阵 / throttle / 幂等键 / nowait 409 / 动态聚合
  6. COLLAB-004 实时票据 / 续签 / verify-rooms / WS 最小闭环

契约常量全部来自 tests/jmeter/_contract.py（CLAUDE.md 测试脚本规范 ①）。
throttle 配额注记：批量端点 10 次/min/用户（BR-06）为全局按用户计数，故 BOARD-004
段的请求分散在 4 个账号（admin ≤9 / member ≤10 / 专用 throttle 账号 11 连发 / 专用
nowait 账号 1 次），段间跨分钟边界不敏感。
自建数据：项目名 S3FLOW-* 前缀 / 用户 s3* 邮箱前缀，结束按前缀清理（幂等可重跑）。
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid as uuid_mod

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _contract import CODES, HTTP, Client, detail_of, error_code, q  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
LIVE_BASE = os.environ.get("LIVE_BASE", "http://localhost:3000")
PASS = 0
FAIL = 0
SKIP = 0
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


def skip(cid: str, desc: str):
    global SKIP
    SKIP += 1
    print(f"  - {cid} {desc}（SKIP）")


def section(title: str):
    print(f"\n═══ {title} ═══")


def signup(c: Client, tag: str) -> tuple[str, str]:
    ts = int(time.time() * 1000) % 100000000
    email = f"{tag}{ts}@rabbit.dev"
    code, body = c.req(
        "POST", "/api/v1/auth/sign-up/",
        {"email": email, "password": "Rabbit123!", "display_name": tag.upper().strip("-") + " S3"},
        {"X-CSRFToken": c.csrf()},
    )
    if code != HTTP["CREATED"]:
        print(f"  ✗ 前置失败：sign-up {code} {body}")
        raise SystemExit(1)
    return email, body["data"]["default_workspace_slug"]


def make_project(c: Client, ws: str, name: str, identifier: str) -> str:
    code, body = c.req(
        "POST", f"/api/v1/workspaces/{q(ws)}/projects/",
        {"name": name, "identifier": identifier},
        {"X-CSRFToken": c.csrf()},
    )
    if code != HTTP["CREATED"]:
        print(f"  ✗ 前置失败：create project {name} {code} {body}")
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


def invite_member(admin: Client, ws: str, user: Client, email: str):
    """邀请/直加入 workspace（已注册用户走 added 直加；新邮箱才有 invite_link）。"""
    code, body = admin.req(
        "POST", f"/api/v1/workspaces/{q(ws)}/invitations/",
        {"emails": [email], "role": 10}, {"X-CSRFToken": admin.csrf()})
    rows = (body or {}).get("data") or []
    if rows and rows[0].get("status") == "added":
        return  # 已是成员，无需接受
    links = ((body or {}).get("meta") or {}).get("invite_links") or {}
    token = (links.get(email) or "").rsplit("/", 1)[-1]
    if token:
        user.req("POST", f"/api/v1/invitations/{token}/accept/", {}, {"X-CSRFToken": user.csrf()})


def uid_of(c: Client) -> str:
    return c.req("GET", "/api/v1/users/me/")[1]["data"]["user"]["id"]


def _pg_exec(sql: str, params=()):
    """docker exec psql 直改库（sprint-2-flow 同款；参数化转义）。"""
    quoted = sql
    for v in params:
        quoted = quoted.replace("%s", "'" + str(v).replace("'", "''") + "'", 1)
    r = subprocess.run(  # noqa: S603 —— 常量 SQL + 参数化转义
        ["docker", "exec", "-i", "rp-pg", "psql", "-U", "rp", "-d", "rabbit_projects",
         "-v", "ON_ERROR_STOP=1", "-c", quoted],
        capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300])
    return r.stdout


def _pg_val(sql: str) -> str:
    r = subprocess.run(  # noqa: S603
        ["docker", "exec", "-i", "rp-pg", "psql", "-U", "rp", "-d", "rabbit_projects", "-At", "-c", sql],
        capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300])
    return r.stdout.strip()


def _discover_internal_key() -> str | None:
    """verify-rooms 的 X-Internal-Key：环境变量优先，其次从运行中的 api 进程嗅探。"""
    env = os.environ.get("INTERNAL_KEY")
    if env:
        return env
    try:
        r = subprocess.run(["ps", "-E", "-A"], capture_output=True, text=True, timeout=10)  # noqa: S603
        for line in r.stdout.splitlines():
            if "manage.py runserver" not in line:
                continue
            for tok in line.split():
                if tok.startswith("INTERNAL_KEY=") and len(tok) > len("INTERNAL_KEY="):
                    return tok.split("=", 1)[1]
    except Exception:  # noqa: BLE001
        return None
    return None


def _sql_insert_issues(proj: str, actor_id: str, state_id: str, prefix: str, n: int, seq_start: int):
    sql = (
        "INSERT INTO issues (id, project_id, name, description_json, description_html, "
        " priority, sequence_id, sort_order, custom_fields, state_id, created_by_id, "
        " created_at, updated_at, attachment_count, github_context) "
        "SELECT gen_random_uuid(), %s, %s || g, '{}'::jsonb, '<p></p>', 'none', "
        + str(int(seq_start)) + " + g, g * 100.0, '{}'::jsonb, %s, %s, now(), now(), 0, '{}'::jsonb "
        "FROM generate_series(1, " + str(int(n)) + ") g")
    _pg_exec(sql, (proj, prefix, state_id, actor_id))


def _wait_until(predicate, timeout_s: float = 10.0, interval: float = 0.5) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# ═══ 纯标准库最小 WS 客户端（COLLAB-004 段；无第三方依赖） ═══

class MiniWS:
    """RFC6455 握手 + 单帧收发的最小实现（服务端帧不掩码；客户端发送须掩码）。"""

    def __init__(self, url: str):
        u = urllib.parse.urlparse(url)
        host, port = u.hostname, u.port or (443 if u.scheme == "wss" else 80)
        self.sock = socket.create_connection((host, port), timeout=8)
        self.key = base64.b64encode(os.urandom(16)).decode()
        path = u.path + (f"?{u.query}" if u.query else "")
        self.sock.sendall((
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {self.key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("handshake closed")
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        self.status_line = head.split(b"\r\n")[0].decode()
        self.headers = {}
        for ln in head.split(b"\r\n")[1:]:
            if b":" in ln:
                k, v = ln.split(b":", 1)
                self.headers[k.decode().strip().lower()] = v.decode().strip()
        expect = base64.b64encode(hashlib.sha1(
            (self.key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        self.accept_ok = self.headers.get("sec-websocket-accept") == expect
        self.buf = rest

    def _recv_exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("socket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def read_frame(self, timeout_s: float = 5.0):
        """返回 (fin, opcode, payload)；opcode 1=text 8=close 9=ping 10=pong。"""
        self.sock.settimeout(timeout_s)
        b1, b2 = self._recv_exact(2)
        opcode = b1 & 0x0F
        masked = b2 & 0x80
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else b""
        payload = bytearray(self._recv_exact(length))
        if masked:
            payload = bytearray(c ^ mask[i % 4] for i, c in enumerate(payload))
        return b1 & 0x80, opcode, bytes(payload)

    def send_text(self, text: str):
        payload = text.encode()
        mask = os.urandom(4)
        head = bytearray([0x81])
        n = len(payload)
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126)
            head += struct.pack(">H", n)
        else:
            head.append(0x80 | 127)
            head += struct.pack(">Q", n)
        head += mask
        self.sock.sendall(bytes(head) + bytes(c ^ mask[i % 4] for i, c in enumerate(payload)))

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def _jwt_claims(token: str) -> dict:
    """解 JWT payload（不验签——仅断言 claims 结构用）。"""
    try:
        seg = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))
    except (IndexError, ValueError, KeyError):
        return {}


# ═══ 清理（幂等：S3FLOW-% 项目前缀 + s3* 用户邮箱前缀兜底，单事务） ═══

def cleanup():
    print("\n═══ 清理自建数据（S3FLOW-% 项目 / 本脚本用户前缀）═══")
    # 用户前缀精确匹配（s3flow/s3mem/s3view/s3out/s3thr/s3now）——宽匹配 's3%' 会误伤
    # pytest 套件自建的 s3b-*@rabbit.dev 夹具用户（其项目名不带 S3FLOW- 前缀）
    users_sql = ("SELECT id FROM users WHERE email LIKE ANY (ARRAY["
                 "'s3flow-%','s3mem-%','s3view-%','s3out-%','s3thr-%','s3now-%'])")
    try:
        _pg_exec(f"""
        BEGIN;
        CREATE TEMP TABLE sp AS SELECT id FROM projects WHERE name LIKE 'S3FLOW-%';
        DELETE FROM comment_reactions WHERE comment_id IN
          (SELECT id FROM issue_comments WHERE issue_id IN (SELECT id FROM issues WHERE project_id IN (SELECT id FROM sp)));
        DELETE FROM notifications WHERE receiver_id IN ({users_sql});
        DELETE FROM issue_comments WHERE issue_id IN (SELECT id FROM issues WHERE project_id IN (SELECT id FROM sp));
        DELETE FROM issue_activities WHERE issue_id IN (SELECT id FROM issues WHERE project_id IN (SELECT id FROM sp));
        DELETE FROM issue_activities WHERE actor_id IN ({users_sql});
        DELETE FROM work_logs WHERE issue_id IN (SELECT id FROM issues WHERE project_id IN (SELECT id FROM sp));
        DELETE FROM issue_links WHERE issue_id IN (SELECT id FROM issues WHERE project_id IN (SELECT id FROM sp));
        DELETE FROM issue_assignees WHERE issue_id IN (SELECT id FROM issues WHERE project_id IN (SELECT id FROM sp));
        DELETE FROM issue_labels WHERE issue_id IN (SELECT id FROM issues WHERE project_id IN (SELECT id FROM sp));
        DELETE FROM file_share_accesses WHERE share_id IN
          (SELECT id FROM file_share_links WHERE asset_id IN (SELECT id FROM file_assets WHERE project_id IN (SELECT id FROM sp)));
        DELETE FROM file_share_links WHERE asset_id IN (SELECT id FROM file_assets WHERE project_id IN (SELECT id FROM sp));
        DELETE FROM file_versions WHERE asset_id IN (SELECT id FROM file_assets WHERE project_id IN (SELECT id FROM sp));
        DELETE FROM upload_sessions WHERE project_id IN (SELECT id FROM sp);
        DELETE FROM file_assets WHERE project_id IN (SELECT id FROM sp);
        DELETE FROM file_folders WHERE project_id IN (SELECT id FROM sp);
        DELETE FROM issues WHERE project_id IN (SELECT id FROM sp);
        DELETE FROM issue_views WHERE project_id IN (SELECT id FROM sp);
        DELETE FROM issue_activities WHERE project_id IN (SELECT id FROM sp);
        DELETE FROM custom_field_definitions WHERE project_id IN (SELECT id FROM sp);
        DELETE FROM labels WHERE project_id IN (SELECT id FROM sp);
        DELETE FROM states WHERE project_id IN (SELECT id FROM sp);
        DELETE FROM project_members WHERE project_id IN (SELECT id FROM sp);
        DELETE FROM project_favorites WHERE project_id IN (SELECT id FROM sp);
        DELETE FROM project_status_logs WHERE project_id IN (SELECT id FROM sp);
        DELETE FROM webhook_deliveries WHERE endpoint_id IN
          (SELECT id FROM webhook_endpoints WHERE project_id IN (SELECT id FROM sp));
        DELETE FROM webhook_endpoints WHERE project_id IN (SELECT id FROM sp);
        DELETE FROM integration_sync_conflict_logs WHERE binding_id IN
          (SELECT id FROM integration_installations WHERE project_id IN (SELECT id FROM sp));
        DELETE FROM integration_installations WHERE project_id IN (SELECT id FROM sp);
        DELETE FROM projects WHERE id IN (SELECT id FROM sp);
        DELETE FROM issue_types WHERE workspace_id IN
          (SELECT id FROM workspaces WHERE created_by_id IN ({users_sql}));
        DELETE FROM workspace_member_invites WHERE workspace_id IN
          (SELECT id FROM workspaces WHERE created_by_id IN ({users_sql}));
        DELETE FROM workspace_members WHERE workspace_id IN
          (SELECT id FROM workspaces WHERE created_by_id IN ({users_sql}));
        DELETE FROM custom_field_definitions WHERE project_id IS NULL AND workspace_id IN
          (SELECT id FROM workspaces WHERE created_by_id IN ({users_sql}));
        DELETE FROM workspace_login_daily WHERE workspace_id IN
          (SELECT id FROM workspaces WHERE created_by_id IN ({users_sql}));
        DELETE FROM workspace_labels WHERE workspace_id IN
          (SELECT id FROM workspaces WHERE created_by_id IN ({users_sql}));
        DELETE FROM project_templates WHERE workspace_id IN
          (SELECT id FROM workspaces WHERE created_by_id IN ({users_sql}));
        DELETE FROM workspaces WHERE created_by_id IN ({users_sql});
        DELETE FROM workspace_login_daily WHERE member_id IN ({users_sql});
        DELETE FROM workspace_labels WHERE created_by_id IN ({users_sql});
        DELETE FROM project_templates WHERE created_by_id IN ({users_sql});
        DELETE FROM notifications WHERE receiver_id IN ({users_sql});
        DELETE FROM password_reset_tokens WHERE user_id IN ({users_sql});
        DELETE FROM backup_runs WHERE created_by_id IN ({users_sql});
        DELETE FROM release_gates WHERE created_by_id IN ({users_sql});
        DELETE FROM release_gate_events WHERE actor_id IN ({users_sql});
        DELETE FROM system_admins WHERE user_id IN ({users_sql});
        DELETE FROM users WHERE id IN ({users_sql});
        DROP TABLE sp;
        COMMIT;
        """)
        left_p = _pg_val("SELECT count(*) FROM projects WHERE name LIKE 'S3FLOW-%'")
        left_u = _pg_val("SELECT count(*) FROM users WHERE email LIKE 's3flow-%@rabbit.dev'")
        ck("CLEAN-1", "清理后库内无 S3FLOW-% 项目 / 本脚本用户残留",
           left_p == "0" and left_u == "0", f"projects={left_p} users={left_u}")
    except Exception as e:  # noqa: BLE001
        ck("CLEAN-1", "清理自建数据", False, f"{e}")


# ═══ 主流程 ═══

def main() -> int:
    cleanup()  # 幂等起步：清掉历史失败 run 的残留（sprint-4-flow 同纪律）
    admin = Client(BASE)
    admin_email, ws = signup(admin, "s3flow-")
    member = Client(BASE)
    member_email, _ = signup(member, "s3mem-")
    viewer = Client(BASE)
    viewer_email, _ = signup(viewer, "s3view-")
    outsider = Client(BASE)
    _out_email, out_ws = signup(outsider, "s3out-")

    invite_member(admin, ws, member, member_email)
    invite_member(admin, ws, viewer, viewer_email)
    admin_id, member_id, viewer_id = uid_of(admin), uid_of(member), uid_of(viewer)

    board_segment(admin, member, viewer, outsider, ws, admin_id, member_id, viewer_id)
    filter_segment(admin, member, viewer, ws, admin_id, member_id, viewer_id)
    collab2_segment(admin, member, viewer, outsider, ws, admin_id, member_id, viewer_id)
    collab3_segment(admin, outsider, ws, admin_id)
    board4_segment(admin, member, viewer, outsider, ws, admin_id, member_id, viewer_id)
    collab4_segment(admin, member, viewer, outsider, ws, admin_id, member_id, viewer_id, out_ws)

    print(f"\n{'═' * 40}\nSprint 3 接口流程：{PASS} 通过 / {FAIL} 失败 / {SKIP} 跳过")
    cleanup()
    if FAILURES:
        print("\n".join("  ✗ " + f for f in FAILURES))
        return 1
    print("全部通过 ✓")
    return 0


# ═══ 1. BOARD-003 多看板与视图配置 ═══

def board_segment(admin, member, viewer, outsider, ws, admin_id, member_id, viewer_id):
    section("BOARD-003 多看板与视图配置")
    proj = make_project(admin, ws, "S3FLOW-BOARD", "S3F")
    views = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/views/"
    issues = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/"

    # —— 内置五视图种子（§4.1.2：随项目创建事务种入）——
    code, body = admin.req("GET", views)
    data = (body or {}).get("data") or []
    names = [v["name"] for v in data]
    builtin_names = ["需求池", "缺陷列表", "我的待办", "本周到期", "测试执行"]
    ck("B3-01", "建项目即种子内置五视图（is_system=true）",
       code == HTTP["OK"] and sorted(names) == sorted(builtin_names)
       and all(v.get("is_system") is True for v in data), f"got {code} {names}")
    ck("B3-02", "内置视图 owner=项目创建者 + 按 sort_order 排列",
       all(v.get("owner_id") == admin_id for v in data)
       and [v["sort_order"] for v in data] == sorted(v["sort_order"] for v in data))
    ck("B3-03", "meta count=total_count=5（全量无分页）",
       ((body or {}).get("meta") or {}).get("count") == 5
       and ((body or {}).get("meta") or {}).get("total_count") == 5)
    todo_view = next(v for v in data if v["name"] == "我的待办")

    # —— 创建：嵌套 filters 现已合法（TASK-011 超集接管扁平校验）——
    nested = {"op": "AND", "conditions": [
        {"field": "state.group", "operator": "in", "value": ["unstarted", "started"]},
        {"op": "OR", "conditions": [
            {"field": "priority", "operator": "in", "value": ["urgent"]},
            {"op": "AND", "conditions": [
                {"field": "priority", "operator": "in", "value": ["high", "medium"]}]}]}]}
    code, body = admin.req("POST", views, {
        "name": "S3救火看板", "layout": "kanban", "filters": nested,
        "display_props": {"icon": "🚒", "group_by": "priority", "order_by": "sort_order"},
    }, {"X-CSRFToken": admin.csrf()})
    d = (body or {}).get("data") or {}
    v_id = d.get("id")
    ck("B3-04", "POST 视图（3 层嵌套 filters）→ 201 + 全字段回显",
       code == HTTP["CREATED"] and d.get("layout") == "kanban"
       and d.get("access") == "personal" and d.get("is_system") is False
       and (d.get("filters") or {}).get("op") == "AND", f"got {code} {body}")
    ck("B3-05", "保存即回显 applied（BR-17：view 标识 + 条件/组计数）",
       (((body or {}).get("meta") or {}).get("applied") or {}).get("view", {}).get("id") == v_id
       and ((body or {}).get("meta") or {}).get("applied", {}).get("conditions_count") == 3
       and ((body or {}).get("meta") or {}).get("applied", {}).get("groups_count") == 3,
       f"{(body or {}).get('meta')}")

    code, body = admin.req("POST", views, {"name": "S3救火看板", "layout": "list"},
                           {"X-CSRFToken": admin.csrf()})
    ck("B3-06", "项目内重名允许（靠 id 区分，无唯一约束）→ 201",
       code == HTTP["CREATED"], f"got {code}")

    code, body = admin.req("POST", views, {"name": "S3共享", "access": "shared"},
                           {"X-CSRFToken": admin.csrf()})
    ck("B3-07", "access=shared → 400（P3 前拒绝）",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["validation"]
       and (detail_of(body, "access") or {}).get("code") == "INVALID", f"got {code} {body}")
    code, body = admin.req("POST", views, {"name": "S3坏布局", "layout": "fancy"},
                           {"X-CSRFToken": admin.csrf()})
    ck("B3-08", "layout 非法值 → 400", code == HTTP["BAD_REQUEST"], f"got {code}")

    # —— 造 cf 字段供分组（select groupable / text 非 groupable）——
    props = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issue-properties/"
    code, body = admin.req("POST", props, {
        "name": "等级", "field_key": "cf_grade", "field_type": "select", "is_indexed": True,
        "options": [{"label": "甲", "value": "g1", "color": "#DC2626", "sort_order": 1},
                    {"label": "乙", "value": "g2", "color": "#F59E0B", "sort_order": 2},
                    {"label": "丙", "value": "g3", "color": "#3B82F6", "sort_order": 3}]},
        {"X-CSRFToken": admin.csrf()})
    grade_id = ((body or {}).get("data") or {}).get("id")
    ck("B3-09", "前置：cf_grade select 字段 → 201", code == HTTP["CREATED"], f"got {code}")
    admin.req("POST", props, {"name": "备注", "field_key": "cf_memo", "field_type": "text"},
              {"X-CSRFToken": admin.csrf()})

    code, body = admin.req("POST", views, {
        "name": "S3坏分组", "display_props": {"group_by": "cf_memo"}},
        {"X-CSRFToken": admin.csrf()})
    ck("B3-10", "group_by 非 groupable（text 字段）→ 400",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["validation"]
       and (detail_of(body, "display_props.group_by") or {}).get("code") == "INVALID", f"got {code}")

    deep = nested
    for _ in range(2):
        deep = {"op": "AND", "conditions": [dict(deep)]}
    code, body = admin.req("POST", views, {"name": "S3超深", "filters": deep},
                           {"X-CSRFToken": admin.csrf()})
    ck("B3-11", "filters 4 层嵌套 → 400 INVALID_PARAM(TOO_DEEP)",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["invalidParam"], f"got {code}")
    too_many = {"op": "AND", "conditions": [
        {"field": "priority", "operator": "in", "value": ["low"]} for _ in range(21)]}
    code, body = admin.req("POST", views, {"name": "S3超量", "filters": too_many},
                           {"X-CSRFToken": admin.csrf()})
    ck("B3-12", "filters 21 节点 → 400", code == HTTP["BAD_REQUEST"]
       and error_code(body) == CODES["invalidParam"], f"got {code}")
    code, body = admin.req("POST", views, {"name": "S3坏图标", "display_props": {"icon": "🎈"}},
                           {"X-CSRFToken": admin.csrf()})
    ck("B3-13", "icon 非八枚预设 → 400", code == HTTP["BAD_REQUEST"], f"got {code}")

    # —— 上限 20（含内置）：补满后第 21 个 409 ——
    code, body = admin.req("GET", views)
    cur = len((body or {}).get("data") or [])
    for i in range(20 - cur):
        admin.req("POST", views, {"name": f"S3填充{i:02d}", "layout": "list"},
                  {"X-CSRFToken": admin.csrf()})
    code, body = admin.req("GET", views)
    ck("B3-14", f"补满至 20 个视图（原 {cur}）",
       len((body or {}).get("data") or []) == 20, f"got {len((body or {}).get('data') or [])}")
    code, body = admin.req("POST", views, {"name": "S3第21个"}, {"X-CSRFToken": admin.csrf()})
    ck("B3-15", "第 21 个视图 → 409 RESOURCE_LIMIT_EXCEEDED",
       code == HTTP["CONFLICT"] and error_code(body) == CODES["limitExceeded"]
       and any(x.get("code") == "LIMIT" for x in ((body or {}).get("error") or {}).get("details") or []),
       f"got {code} {body}")
    code, body = admin.req("GET", views)
    for v in (body or {}).get("data") or []:
        if v["name"].startswith("S3填充"):
            admin.req("DELETE", views + f"{v['id']}/", None, {"X-CSRFToken": admin.csrf()})

    # —— PATCH / DELETE ——
    code, body = admin.req("PATCH", views + f"{v_id}/", {
        "name": "S3救火看板V2", "display_props": {"group_by": "priority", "icon": "🔥"}},
        {"X-CSRFToken": admin.csrf()})
    ck("B3-16", "PATCH 改名 + display_props → 200",
       code == HTTP["OK"] and (body or {}).get("data", {}).get("name") == "S3救火看板V2",
       f"got {code}")
    code, body = admin.req("PATCH", views + f"{v_id}/", {"layout": "table"},
                           {"X-CSRFToken": admin.csrf()})
    ck("B3-17", "切布局仅改 layout 单字段，filters 逐字节保留（BR-04/UT-09）",
       code == HTTP["OK"] and (body or {}).get("data", {}).get("layout") == "table"
       and (body or {}).get("data", {}).get("filters") == nested, f"got {code}")
    code, body = admin.req("PATCH", views + f"{todo_view['id']}/", {"filters": nested},
                           {"X-CSRFToken": admin.csrf()})
    ck("B3-18", "PATCH 内置视图 filters → 403 PERM_DENIED（口径锁定）",
       code == HTTP["FORBIDDEN"] and error_code(body) == CODES["permDenied"], f"got {code} {body}")
    code, body = admin.req("PATCH", views + f"{todo_view['id']}/",
                           {"display_props": {"icon": "📅", "group_by": None}},
                           {"X-CSRFToken": admin.csrf()})
    ck("B3-19", "PATCH 内置视图 display_props → 200（BR-03 可改）",
       code == HTTP["OK"]
       and (body or {}).get("data", {}).get("display_props", {}).get("icon") == "📅", f"got {code}")
    code, body = admin.req("PATCH", views + f"{uuid_mod.uuid4()}/", {"name": "X"},
                           {"X-CSRFToken": admin.csrf()})
    ck("B3-20", "PATCH 不存在 view_id → 404", code == HTTP["NOT_FOUND"], f"got {code}")

    # —— 越权（BR-11：无 board.manage 者不可见他人个人视图）——
    admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/members/",
              {"member_ids": [member_id], "role": 15}, {"X-CSRFToken": admin.csrf()})
    admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/members/",
              {"member_ids": [viewer_id], "role": 5}, {"X-CSRFToken": admin.csrf()})
    code, _ = viewer.req("GET", views + f"{v_id}/")
    ck("B3-21", "VIEWER 直 GET 他人个人视图 → 404", code == HTTP["NOT_FOUND"], f"got {code}")
    code, body = viewer.req("GET", views)
    ck("B3-22", "VIEWER 列表只含内置（不含他人个人视图）",
       code == HTTP["OK"] and all(v.get("is_system") or v.get("owner_id") == viewer_id
                                  for v in (body or {}).get("data") or []))
    code, body = member.req("GET", views + f"{v_id}/")
    ck("B3-23", "board.manage（CONTRIBUTOR+，审计）GET 他人视图 → 200", code == HTTP["OK"], f"got {code}")
    code, body = member.req("PATCH", views + f"{v_id}/", {"name": "S3审计改名"},
                            {"X-CSRFToken": member.csrf()})
    ck("B3-24", "board.manage PATCH 他人视图 → 200（UT-16）",
       code == HTTP["OK"] and (body or {}).get("data", {}).get("name") == "S3审计改名", f"got {code}")

    code, body = admin.req("POST", views, {"name": "S3待删"}, {"X-CSRFToken": admin.csrf()})
    del_id = ((body or {}).get("data") or {}).get("id")
    code, _ = admin.req("DELETE", views + f"{del_id}/", None, {"X-CSRFToken": admin.csrf()})
    ck("B3-25", "DELETE 自建视图 → 204（软删）", code == HTTP["NO_CONTENT"], f"got {code}")
    code, _ = admin.req("GET", views + f"{del_id}/")
    ck("B3-26", "删后 GET → 404", code == HTTP["NOT_FOUND"], f"got {code}")
    code, body = admin.req("DELETE", views + f"{todo_view['id']}/", None, {"X-CSRFToken": admin.csrf()})
    ck("B3-27", "DELETE 内置视图 → 403", code == HTTP["FORBIDDEN"]
       and error_code(body) == CODES["permDenied"], f"got {code}")

    # —— 五维分组（§2.3 / BR-16：裸列值键 + 空列恒在 + __none__ 最末）——
    _, b = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/states/?include_cancelled=1")
    states = {x["group"]: x["id"] for x in b["data"]}
    admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/labels/",
              {"name": "S3L1", "color": "#111111"}, {"X-CSRFToken": admin.csrf()})
    admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/labels/",
              {"name": "S3L2", "color": "#222222"}, {"X-CSRFToken": admin.csrf()})
    _, b = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/labels/")
    lbl = {x["name"]: x["id"] for x in b["data"]}
    i_urg = make_issue(admin, ws, proj, "S3-紧急", priority="urgent",
                       assignee_ids=[admin_id], label_ids=[lbl["S3L1"]],
                       custom_fields={"cf_grade": "g1"})
    i_none = make_issue(admin, ws, proj, "S3-无值")
    make_issue(admin, ws, proj, "S3-高", priority="high", assignee_ids=[member_id],
               label_ids=[lbl["S3L2"]], custom_fields={"cf_grade": "g2"})

    def grouped(dimension: str, extra: str = ""):
        return admin.req("GET", f"{issues}?group_by={dimension}{extra}")

    code, body = grouped("state_id")
    g = (body or {}).get("data") or {}
    ck("B3-28", "state_id 分组：键=State 裸 UUID（含已取消 4 列，BOARD-002 兼容）",
       code == HTTP["OK"] and set(g) >= {states["unstarted"], states["started"]}
       and len(g) == 4, f"got {code} keys={list(g)[:5]}")
    ck("B3-29", "组结构 {results,total_results,unfiltered_total_results} 齐备",
       all({"results", "total_results", "unfiltered_total_results"} <= set(v) for v in g.values()))
    ck("B3-30", "meta.grouped_by=state_id + group_cursors 逐组",
       ((body or {}).get("meta") or {}).get("grouped_by") == "state_id"
       and set((((body or {}).get("meta") or {}).get("group_cursors") or {})) == set(g))

    code, body = grouped("state")
    ck("B3-31", "别名 state → 归一为 state_id（UT-19）",
       code == HTTP["OK"] and ((body or {}).get("meta") or {}).get("grouped_by") == "state_id",
       f"got {code}")
    code, body = grouped("states")
    ck("B3-32", "别名表外（states）→ 400", code == HTTP["BAD_REQUEST"]
       and error_code(body) == CODES["invalidParam"], f"got {code}")

    code, body = grouped("priority")
    g = (body or {}).get("data") or {}
    ck("B3-33", "priority 分组：五枚举值键全量（urgent…none）",
       code == HTTP["OK"] and set(g) == {"urgent", "high", "medium", "low", "none"}, f"got {list(g)}")
    ck("B3-34", "空列恒在（low 列 total_results=0）", g.get("low", {}).get("total_results") == 0)
    ck("B3-35", "urgent 列命中 S3-紧急（行含 issue_key/priority）",
       any(r["id"] == i_urg["id"] for r in g.get("urgent", {}).get("results") or [])
       and all("issue_key" in r and "priority" in r for r in g["urgent"]["results"]))

    code, body = grouped("assignee_id")
    g = (body or {}).get("data") or {}
    keys = list(g)
    ck("B3-36", "assignee 分组：成员 UUID 键 + __none__ 且最末",
       code == HTTP["OK"] and admin_id in g and member_id in g and keys[-1] == "__none__",
       f"got {keys}")
    ck("B3-37", "__none__ 未指派池命中 S3-无值",
       any(r["id"] == i_none["id"] for r in g.get("__none__", {}).get("results") or []))

    code, body = grouped("label_id")
    g = (body or {}).get("data") or {}
    ck("B3-38", "label 分组：标签 UUID 键 + __none__",
       code == HTTP["OK"] and lbl["S3L1"] in g and lbl["S3L2"] in g and "__none__" in g,
       f"got {list(g)}")

    code, body = grouped("cf_grade")
    g = (body or {}).get("data") or {}
    ck("B3-39", "cf select 分组：选项值键 g1/g2/g3 + __none__（零 DISTINCT）",
       code == HTTP["OK"] and set(g) == {"g1", "g2", "g3", "__none__"}, f"got {list(g)}")
    ck("B3-40", "cf __none__ 组命中未填值任务",
       any(r["id"] == i_none["id"] for r in g.get("__none__", {}).get("results") or []))

    code, body = grouped("cf_memo")
    ck("B3-41", "非 groupable cf（text）→ 400 INVALID_PARAM",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["invalidParam"]
       and (detail_of(body, "group_by") or {}).get("code") == "INVALID", f"got {code}")

    # 视图层 × 分组：视图 filters priority in [urgent] → 非 urgent 列全部 total=0（双计数）
    vf = {"op": "AND", "conditions": [{"field": "priority", "operator": "in", "value": ["urgent"]}]}
    code, body = admin.req("POST", views, {"name": "S3紧急组", "filters": vf,
                                           "display_props": {"group_by": "priority"}},
                           {"X-CSRFToken": admin.csrf()})
    vgroup_id = ((body or {}).get("data") or {}).get("id")
    code, body = grouped("priority", f"&view_id={vgroup_id}")
    g = (body or {}).get("data") or {}
    meta = (body or {}).get("meta") or {}
    ck("B3-42", "view_id × group_by：meta.view_id 回显 + applied.priority=[urgent]",
       code == HTTP["OK"] and meta.get("view_id") == vgroup_id
       and meta.get("applied", {}).get("priority") == ["urgent"], f"meta={meta}")
    ck("B3-43", "双计数：high 列 total_results=0（筛选后）而 unfiltered≥1（筛选前）",
       g.get("high", {}).get("total_results") == 0
       and g.get("high", {}).get("unfiltered_total_results") >= 1, f"high={g.get('high')}")

    # 降级（BR-08/UT-10）：停用 cf_grade → 视图读取回退 + degraded 提示
    admin.req("PATCH", views + f"{vgroup_id}/", {"display_props": {"group_by": "cf_grade"}},
              {"X-CSRFToken": admin.csrf()})
    admin.req("PATCH", props + f"{grade_id}/", {"is_active": False}, {"X-CSRFToken": admin.csrf()})
    code, body = grouped("state_id", f"&view_id={vgroup_id}")
    deg = ((body or {}).get("meta") or {}).get("degraded") or {}
    ck("B3-44", "停用分组字段 → meta.degraded.group_by 提示（降级不报错）",
       code == HTTP["OK"] and "cf_grade" in str(deg.get("group_by", "")), f"meta={deg}")
    admin.req("PATCH", props + f"{grade_id}/", {"is_active": True}, {"X-CSRFToken": admin.csrf()})

    # —— 默认偏好（BR-10：零新端点，PATCH /users/me/settings/）——
    settings_url = "/api/v1/users/me/settings/"
    code, body = admin.req("PATCH", settings_url,
                           {"board.default_view_id": {proj: vgroup_id}},
                           {"X-CSRFToken": admin.csrf()})
    ck("B3-45", "设默认视图 → 200 偏好回显",
       code == HTTP["OK"]
       and (body or {}).get("data", {}).get("board.default_view_id", {}).get(proj) == vgroup_id,
       f"got {code} {body}")
    code, body = admin.req("GET", settings_url)
    ck("B3-46", "GET settings 持久化读取",
       (body or {}).get("data", {}).get("board.default_view_id", {}).get(proj) == vgroup_id)
    other = admin.req("POST", views, {"name": "S3换默认"}, {"X-CSRFToken": admin.csrf()})[1]["data"]["id"]
    admin.req("PATCH", settings_url, {"board.default_view_id": {proj: other}},
              {"X-CSRFToken": admin.csrf()})
    code, body = admin.req("GET", settings_url)
    ck("B3-47", "换默认视图（项目内至多一条）",
       (body or {}).get("data", {}).get("board.default_view_id", {}).get(proj) == other)
    admin.req("PATCH", settings_url, {"board.default_view_id": {proj: None}},
              {"X-CSRFToken": admin.csrf()})
    code, body = admin.req("GET", settings_url)
    ck("B3-48", "取消默认（null 删条目）",
       proj not in ((body or {}).get("data", {}).get("board.default_view_id") or {}))
    code, body = admin.req("PATCH", settings_url, {"hack.key": 1}, {"X-CSRFToken": admin.csrf()})
    ck("B3-49", "未知偏好键 → 400 INVALID_PARAM",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["invalidParam"], f"got {code}")

    code, _ = outsider.req("GET", views)
    ck("B3-50", "外部用户 GET 视图列表 → 404（AUTH-003）", code == HTTP["NOT_FOUND"], f"got {code}")


# ═══ 2. TASK-011 全字段组合筛选 ═══

def filter_segment(admin, member, viewer, ws, admin_id, member_id, viewer_id):
    section("TASK-011 全字段组合筛选")
    proj = make_project(admin, ws, "S3FLOW-FLT", "S3T")
    views = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/views/"
    issues = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/"
    props = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issue-properties/"
    admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/members/",
              {"member_ids": [member_id], "role": 15}, {"X-CSRFToken": admin.csrf()})
    admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/members/",
              {"member_ids": [viewer_id], "role": 5}, {"X-CSRFToken": admin.csrf()})

    admin.req("POST", props, {"name": "严重", "field_key": "cf_severity", "field_type": "select",
                              "options": [{"label": "致命", "value": "critical", "sort_order": 1},
                                          {"label": "严重", "value": "major", "sort_order": 2},
                                          {"label": "一般", "value": "minor", "sort_order": 3}]},
              {"X-CSRFToken": admin.csrf()})
    admin.req("POST", props, {"name": "版本", "field_key": "cf_versions", "field_type": "multi_select",
                              "options": [{"label": "v1", "value": "v1"},
                                          {"label": "v2", "value": "v2"}]},
              {"X-CSRFToken": admin.csrf()})
    for key, ftype in (("cf_done", "checkbox"), ("cf_day", "date"),
                       ("cf_points", "number"), ("cf_memo", "text")):
        admin.req("POST", props, {"name": key, "field_key": key, "field_type": ftype},
                  {"X-CSRFToken": admin.csrf()})

    today = dt.date.today()
    iA = make_issue(admin, ws, proj, "S3T-A", priority="urgent", assignee_ids=[admin_id],
                    target_date=str(today),
                    custom_fields={"cf_severity": "critical", "cf_done": True, "cf_points": 9,
                                   "cf_day": str(today), "cf_versions": ["v1"]})
    iB = make_issue(admin, ws, proj, "S3T-B", priority="high", assignee_ids=[member_id],
                    target_date=str(today + dt.timedelta(days=7)),
                    custom_fields={"cf_severity": "major", "cf_done": False, "cf_points": 2,
                                   "cf_versions": ["v2"]})
    iC = make_issue(admin, ws, proj, "S3T-C")

    def flt(tree: dict, extra: str = ""):
        return admin.req("GET", f"{issues}?filters={q(json.dumps(tree))}{extra}")

    # —— 保存 3 层树 → view_id 消费 ——
    tree = {"op": "AND", "conditions": [
        {"field": "state.group", "operator": "in", "value": ["unstarted", "started"]},
        {"op": "OR", "conditions": [
            {"field": "cf_severity", "operator": "in", "value": ["critical", "major"]},
            {"op": "AND", "conditions": [
                {"field": "cf_done", "operator": "eq", "value": True},
                {"field": "cf_day", "operator": "between", "value": [str(today), str(today)]}]}]},
        {"field": "assignees", "operator": "in", "value": ["@me"]}]}
    code, body = admin.req("POST", views, {"name": "S3T盯防", "filters": tree, "layout": "list"},
                           {"X-CSRFToken": admin.csrf()})
    vid = ((body or {}).get("data") or {}).get("id")
    ck("T11-01", "保存 3 层布尔树视图 → 201（嵌套合法）", code == HTTP["CREATED"], f"got {code} {body}")
    code, body = admin.req("GET", f"{issues}?view_id={vid}&per_page=50")
    rows = (body or {}).get("data") or []
    ck("T11-02", "?view_id 消费：命中恰 iA（@me=admin 且 critical）",
       code == HTTP["OK"] and {r["id"] for r in rows} == {iA["id"]},
       f"rows={[r['name'] for r in rows]}")
    # 结构化 applied 仅在 ?filters= 同在时附加（实现口径：无该参数响应逐字节不变）——
    # 叠加一条不改变命中集的临时条件后断言 view 标识与合并计数（BR-17）
    keep = {"op": "AND", "conditions": [
        {"field": "state.group", "operator": "in", "value": ["unstarted", "started"]}]}
    code, body = admin.req("GET",
                           f"{issues}?view_id={vid}&filters={q(json.dumps(keep))}&per_page=50")
    applied = ((body or {}).get("meta") or {}).get("applied") or {}
    ck("T11-03", "meta.applied 回显视图标识与合并条件计数（BR-17）",
       {r["id"] for r in (body or {}).get("data") or []} == {iA["id"]}
       and applied.get("view", {}).get("id") == vid
       and applied.get("conditions_count", 0) >= 5
       and applied.get("groups_count", 0) >= 3, f"{applied}")

    # —— 三源恒 AND（view × filters × 平铺参数）——
    adhoc = {"op": "AND", "conditions": [
        {"field": "priority", "operator": "in", "value": ["high"]}]}
    code, body = admin.req("GET",
                           f"{issues}?view_id={vid}&filters={q(json.dumps(adhoc))}&per_page=50")
    rows = (body or {}).get("data") or []
    applied = ((body or {}).get("meta") or {}).get("applied") or {}
    ck("T11-04", "三源 AND：视图(urgent)+临时(high) 交集为空",
       code == HTTP["OK"] and rows == [], f"rows={[r['name'] for r in rows]}")
    ck("T11-05", "applied 双源回显（filters + view + resolved_placeholders）",
       "filters" in applied and "view" in applied and "resolved_placeholders" in applied, f"{applied}")

    code, body = flt({"op": "AND", "conditions": [
        {"field": "priority", "operator": "in", "value": ["urgent", "high"]}]})
    ck("T11-06", "?filters= 单独消费 → 200 + applied.filters", code == HTTP["OK"]
       and "filters" in (((body or {}).get("meta") or {}).get("applied") or {}), f"got {code}")

    # —— 边界拒绝（BR-01~04）——
    deep = {"op": "AND", "conditions": [{"op": "OR", "conditions": [
        {"op": "AND", "conditions": [{"op": "OR", "conditions": [
            {"field": "priority", "operator": "in", "value": ["low"]}]}]}]}]}
    code, body = flt(deep)
    ck("T11-07", "4 层嵌套 → 400 TOO_DEEP", code == HTTP["BAD_REQUEST"]
       and error_code(body) == CODES["invalidParam"], f"got {code}")
    many = {"op": "AND", "conditions": [
        {"field": "cf_memo", "operator": "is_empty"} for _ in range(21)]}
    code, body = flt(many)
    ck("T11-08", "21 条件节点 → 400 TOO_MANY", code == HTTP["BAD_REQUEST"]
       and error_code(body) == CODES["invalidParam"], f"got {code}")
    vals = [{"field": "labels", "operator": "in",
             "value": [str(uuid_mod.uuid4()) for _ in range(51)]}]
    code, body = flt({"op": "AND", "conditions": vals})
    ck("T11-09", "in 51 值 → 400（MAX_IN_VALUES=50）", code == HTTP["BAD_REQUEST"]
       and error_code(body) == CODES["invalidParam"], f"got {code}")
    code, body = flt({"op": "AND", "conditions": [
        {"field": "project__owner", "operator": "eq", "value": "x"}]})
    ck("T11-10", "白名单外字段探测 → 400 + details 指明字段（UT-03）",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["invalidParam"]
       and any("project__owner" in str(x.get("message", "")) for x in
               ((body or {}).get("error") or {}).get("details") or []), f"got {code} {body}")
    code, body = flt({"op": "AND", "conditions": [
        {"field": "cf_done", "operator": "gt", "value": 1}]})
    ck("T11-11", "checkbox × gt 操作符 → 400（操作符×类型）", code == HTTP["BAD_REQUEST"]
       and error_code(body) == CODES["invalidParam"], f"got {code}")
    code, body = flt({"op": "AND", "conditions": [
        {"field": "cf_severity", "operator": "in", "value": ["blocker"]}]})
    ck("T11-12", "select 值域外 → 400 VALIDATION_CUSTOM_FIELD_INVALID",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["cfInvalid"], f"got {code}")
    code, body = flt({"op": "AND", "conditions": [
        {"field": "assignees", "operator": "in", "value": [str(uuid_mod.uuid4())]}]})
    ck("T11-13", "member 域外用户 → 400（UT-06）", code == HTTP["BAD_REQUEST"], f"got {code}")
    code, body = admin.req("GET", f"{issues}?filters=%7Bbroken")
    ck("T11-14", "损坏 JSON → 400 INVALID_PARAM", code == HTTP["BAD_REQUEST"]
       and error_code(body) == CODES["invalidParam"], f"got {code}")

    # —— 占位符（BR-05：视图是活的）——
    me_tree = {"op": "AND", "conditions": [
        {"field": "assignees", "operator": "in", "value": ["@me"]}]}
    code, body = admin.req("GET", f"{issues}?filters={q(json.dumps(me_tree))}&per_page=50")
    got_admin = {r["id"] for r in (body or {}).get("data") or []}
    applied_a = ((body or {}).get("meta") or {}).get("applied") or {}
    code, body = member.req("GET", f"{issues}?filters={q(json.dumps(me_tree))}&per_page=50")
    got_member = {r["id"] for r in (body or {}).get("data") or []}
    ck("T11-15", "@me 两用户各自命中不同（admin→A、member→B）",
       got_admin == {iA["id"]} and got_member == {iB["id"]},
       f"admin={got_admin} member={got_member}")
    ck("T11-16", "resolved_placeholders 回显 @me（BR-17）",
       "@me" in (applied_a.get("resolved_placeholders") or {}), f"{applied_a.get('resolved_placeholders')}")

    wk = {"op": "AND", "conditions": [
        {"field": "target_date", "operator": "between", "value": ["this_week"]}]}
    code, body = flt(wk)
    rows = (body or {}).get("data") or []
    ck("T11-17", "this_week 占位符：命中窗内 A 不命中窗外 B",
       code == HTTP["OK"] and {r["id"] for r in rows} == {iA["id"]},
       f"rows={[r['name'] for r in rows]}")

    # —— cf 全类型操作符 ——
    cases = [
        ("T11-18", {"field": "cf_severity", "operator": "in", "value": ["critical"]}, {iA["id"]}),
        ("T11-19", {"field": "cf_done", "operator": "eq", "value": True}, {iA["id"]}),
        ("T11-20", {"field": "cf_points", "operator": "gt", "value": 3}, {iA["id"]}),
        ("T11-21", {"field": "cf_day", "operator": "between",
                    "value": [str(today), str(today)]}, {iA["id"]}),
        ("T11-22", {"field": "cf_versions", "operator": "contains_any", "value": ["v2"]}, {iB["id"]}),
        ("T11-23", {"field": "cf_memo", "operator": "is_empty"},
         {iA["id"], iB["id"], iC["id"]}),
    ]
    for cid, cond, expect in cases:
        code, body = flt({"op": "AND", "conditions": [cond]})
        got = {r["id"] for r in (body or {}).get("data") or []}
        ck(cid, f"cf 筛选[{cond['field']} {cond['operator']}] → 命中 {len(expect)} 条",
           code == HTTP["OK"] and got == expect, f"got {len(got)} expect {len(expect)}")

    code, body = flt({"op": "AND", "conditions": [
        {"field": "blocked", "operator": "eq", "value": False}]})
    ck("T11-24", "blocked 注解列可筛选（无阻塞 → 全命中）",
       code == HTTP["OK"] and len((body or {}).get("data") or []) == 3, f"got {code}")

    code, body = viewer.req("GET", f"{issues}?view_id={vid}")
    ck("T11-25", "他人个人视图 view_id（无 board.manage 的 VIEWER）→ 404",
       code == HTTP["NOT_FOUND"], f"got {code}")
    code, body = admin.req("GET", f"{issues}?view_id={uuid_mod.uuid4()}")
    ck("T11-26", "不存在 view_id → 404", code == HTTP["NOT_FOUND"], f"got {code}")


# ═══ 3. COLLAB-002 线程回复与表情 ═══

def collab2_segment(admin, member, viewer, outsider, ws, admin_id, member_id, viewer_id):
    section("COLLAB-002 线程回复与表情")
    proj = make_project(admin, ws, "S3FLOW-COLLAB", "S3C")
    admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/members/",
              {"member_ids": [member_id], "role": 15}, {"X-CSRFToken": admin.csrf()})
    admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/members/",
              {"member_ids": [viewer_id], "role": 5}, {"X-CSRFToken": admin.csrf()})
    i1 = make_issue(admin, ws, proj, "S3C-主任务")
    i2 = make_issue(admin, ws, proj, "S3C-跨任务")
    ic = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{i1['id']}/comments/"
    ic2 = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{i2['id']}/comments/"

    def mention(uid, name):
        return f'<span data-mention-id="{uid}">@{name}</span>'

    # —— 两级归并 ——
    code, body = admin.req("POST", ic, {"comment_html": "<p>顶层评论</p>"},
                           {"X-CSRFToken": admin.csrf()})
    c1 = (body or {}).get("data") or {}
    ck("C2-01", "顶层评论 → 201 root_id=null / reply_count=0 / reactions=[]",
       code == HTTP["CREATED"] and c1.get("root_id") is None
       and c1.get("reply_count") == 0 and c1.get("reactions") == [], f"got {code} {c1}")
    code, body = member.req("POST", ic, {
        "parent_id": c1["id"],
        "comment_html": f"<p>{mention(admin_id, '管理员')} 网关超时配置看下？</p>"},
        {"X-CSRFToken": member.csrf()})
    r1 = (body or {}).get("data") or {}
    ck("C2-02", "回复顶层 → 201 parent=root、reply_to_actor=顶层作者、mention_ids",
       code == HTTP["CREATED"] and r1.get("parent_id") == c1["id"]
       and (r1.get("reply_to_actor") or {}).get("id") == admin_id
       and admin_id in (r1.get("mention_ids") or []), f"got {code} {r1}")
    code, body = admin.req("POST", ic, {
        "parent_id": r1["id"], "comment_html": "<p>是 60s，改成 15s 试试</p>"},
        {"X-CSRFToken": admin.csrf()})
    r2 = (body or {}).get("data") or {}
    ck("C2-03", "回复回复 → 归并挂顶层根（BR-03）+ reply_to_actor=中间人",
       code == HTTP["CREATED"] and r2.get("parent_id") == c1["id"]
       and r2.get("root_id") == c1["id"]
       and (r2.get("reply_to_actor") or {}).get("id") == member_id, f"got {code} {r2}")
    code, body = admin.req("POST", ic2, {"parent_id": c1["id"], "comment_html": "<p>跨任务父</p>"},
                           {"X-CSRFToken": admin.csrf()})
    ck("C2-04", "跨任务父 → 400 DOES_NOT_EXIST（BR-02）",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["validation"]
       and (detail_of(body, "parent_id") or {}).get("code") == "DOES_NOT_EXIST", f"got {code} {body}")

    # 父删子留 + 已删父
    c2 = admin.req("POST", ic, {"comment_html": "<p>第二条顶层</p>"},
                   {"X-CSRFToken": admin.csrf()})[1]["data"]
    for txt in ("回复一", "回复二"):
        member.req("POST", ic, {"parent_id": c2["id"], "comment_html": f"<p>{txt}</p>"},
                   {"X-CSRFToken": member.csrf()})
    admin.req("DELETE", ic + f"{c2['id']}/", None, {"X-CSRFToken": admin.csrf()})
    code, body = admin.req("GET", ic)
    rows = (body or {}).get("data") or []
    c2row = next((r for r in rows if r["id"] == c2["id"]), {})
    ck("C2-05", "父删子留：父占位 is_deleted=true 且 replies 保留 2 条（BR-06）",
       c2row.get("is_deleted") is True and len(c2row.get("replies") or []) == 2
       and c2row.get("reply_count") == 2, f"c2={c2row.get('is_deleted')}/{c2row.get('reply_count')}")
    code, body = admin.req("POST", ic, {"parent_id": c2["id"], "comment_html": "<p>回复已删父</p>"},
                           {"X-CSRFToken": admin.csrf()})
    ck("C2-06", "回复已删父 → 400 DOES_NOT_EXIST",
       code == HTTP["BAD_REQUEST"]
       and (detail_of(body, "parent_id") or {}).get("code") == "DOES_NOT_EXIST", f"got {code}")

    # —— 100 回复上限（BR-05：第 100 条 201、第 101 条 409）——
    c3 = admin.req("POST", ic, {"comment_html": "<p>满线程顶层</p>"},
                   {"X-CSRFToken": admin.csrf()})[1]["data"]
    ok_n = 0
    for i in range(100):
        code, _ = admin.req("POST", ic,
                            {"parent_id": c3["id"], "comment_html": f"<p>灌水回复 {i}</p>"},
                            {"X-CSRFToken": admin.csrf()})
        ok_n += code == HTTP["CREATED"]
    ck("C2-07", "第 1~100 条回复全部 201（上界合法）", ok_n == 100, f"ok={ok_n}")
    code, body = admin.req("POST", ic, {"parent_id": c3["id"], "comment_html": "<p>第 101 条</p>"},
                           {"X-CSRFToken": admin.csrf()})
    ck("C2-08", "第 101 条回复 → 409 RESOURCE_LIMIT_EXCEEDED + LIMIT",
       code == HTTP["CONFLICT"] and error_code(body) == CODES["limitExceeded"]
       and (detail_of(body, "parent_id") or {}).get("code") == "LIMIT", f"got {code} {body}")

    # —— 两层列表（30 顶层/页 + replies 内联 + 聚合）——
    for i in range(29):  # 总顶层 32（c1/c2/c3 + 填充）
        admin.req("POST", ic, {"comment_html": f"<p>顶层填充 {i}</p>"},
                  {"X-CSRFToken": admin.csrf()})
    code, body = admin.req("GET", ic)
    rows = (body or {}).get("data") or []
    meta = (body or {}).get("meta") or {}
    c1row = next((r for r in rows if r["id"] == c1["id"]), {})
    ck("C2-09", "列表默认 30 顶层/页 + meta.next_page_results",
       code == HTTP["OK"] and len(rows) == 30 and meta.get("per_page") == 30
       and meta.get("next_page_results") is True, f"len={len(rows)}")
    ck("C2-10", "replies 全量内联且 reply_count == len(replies)（c1 存活回复 2 条）",
       c1row.get("reply_count") == len(c1row.get("replies") or []) == 2, f"c1row={c1row}")
    ck("C2-11", "顶层按 created_at 正序",
       [r["created_at"] for r in rows] == sorted(r["created_at"] for r in rows))

    # —— reactions（BR-09/10 幂等 + 白名单 + 多 emoji 并存）——
    rx = ic + f"{c1['id']}/reactions/"
    code, body = admin.req("POST", rx, {"emoji": "👍"}, {"X-CSRFToken": admin.csrf()})
    d = (body or {}).get("data") or {}
    ck("C2-12", "POST 👍 → 200 changed=true / count=1 / reacted_by_me",
       code == HTTP["OK"] and d.get("changed") is True and d.get("count") == 1
       and d.get("reacted_by_me") is True, f"got {code} {d}")
    code, body = member.req("POST", rx, {"emoji": "👍"}, {"X-CSRFToken": member.csrf()})
    ck("C2-13", "第二人同 emoji → count=2", (body or {}).get("data", {}).get("count") == 2)
    code, body = admin.req("POST", rx, {"emoji": "👍"}, {"X-CSRFToken": admin.csrf()})
    ck("C2-14", "重复 POST 同 emoji → changed=false 幂等（UT-09）",
       code == HTTP["OK"] and (body or {}).get("data", {}).get("changed") is False
       and (body or {}).get("data", {}).get("count") == 2, f"got {code} {body}")
    admin.req("POST", rx, {"emoji": "🎉"}, {"X-CSRFToken": admin.csrf()})
    code, body = admin.req("GET", ic)
    c1row = next((r for r in (body or {}).get("data") or [] if r["id"] == c1["id"]), {})
    agg = {e["emoji"]: e for e in (c1row.get("reactions") or [])}
    ck("C2-15", "一人多 emoji 并存（👍+🎉 两组聚合，UT-12）",
       set(agg) == {"👍", "🎉"} and agg["🎉"].get("count") == 1, f"agg={agg}")
    ck("C2-16", "默认聚合不含 user_ids（名单浮层按需）",
       all("user_ids" not in e for e in (c1row.get("reactions") or [])))
    code, body = admin.req("GET", ic + "?expand=reactions")
    c1row = next((r for r in (body or {}).get("data") or [] if r["id"] == c1["id"]), {})
    ck("C2-17", "?expand=reactions → 追加 user_ids（IT-04）",
       all("user_ids" in e and e["user_ids"] for e in (c1row.get("reactions") or [])),
       f"{c1row.get('reactions')}")
    code, body = admin.req("DELETE", rx, {"emoji": "🎉"}, {"X-CSRFToken": admin.csrf()})
    ck("C2-18", "DELETE 已点 emoji → changed=true / count=0",
       code == HTTP["OK"] and (body or {}).get("data", {}).get("changed") is True
       and (body or {}).get("data", {}).get("count") == 0, f"got {code}")
    code, body = admin.req("DELETE", rx, {"emoji": "🎉"}, {"X-CSRFToken": admin.csrf()})
    ck("C2-19", "重复 DELETE → 200 changed=false（UT-11）",
       code == HTTP["OK"] and (body or {}).get("data", {}).get("changed") is False)
    code, body = admin.req("DELETE", rx, {"emoji": "👎"}, {"X-CSRFToken": admin.csrf()})
    ck("C2-20", "撤销未点过的 emoji → 200 changed=false count=0",
       code == HTTP["OK"] and (body or {}).get("data", {}).get("count") == 0, f"got {code}")
    code, body = admin.req("POST", rx, {"emoji": "💩"}, {"X-CSRFToken": admin.csrf()})
    ck("C2-21", "白名单外 emoji → 400 NOT_A_CHOICE（BR-09）",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["validation"]
       and (detail_of(body, "emoji") or {}).get("code") == "NOT_A_CHOICE", f"got {code} {body}")
    code, body = viewer.req("POST", rx, {"emoji": "👍"}, {"X-CSRFToken": viewer.csrf()})
    ck("C2-22", "VIEWER 点表情 → 403（comment.create 门槛）",
       code == HTTP["FORBIDDEN"] and error_code(body) == CODES["roleInsufficient"], f"got {code}")

    # —— 图片评论（BR-07/08：评论图域 + presign 收紧）——
    presign = (f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{i1['id']}"
               f"/attachments/presign/")
    size = 3 * 1024 * 1024
    code, body = admin.req("POST", presign, {
        "file_name": "shot.png", "file_size": size, "content_type": "image/png",
        "entity_type": "comment_image"}, {"X-CSRFToken": admin.csrf()})
    d = (body or {}).get("data") or {}
    asset_id = d.get("asset_id")
    ck("C2-23", "presign entity_type=comment_image → 201",
       code == HTTP["CREATED"] and bool(asset_id)
       and (d.get("upload_url") or "").startswith("/uploads/"), f"got {code} {d}")
    ck("C2-24", "key 段含 comment_image 域收紧（ws/proj/comment_image/issue）",
       "/comment_image/" in (d.get("upload_url") or ""), d.get("upload_url"))
    code, body = admin.req("POST", presign, {
        "file_name": "big.png", "file_size": 6 * 1024 * 1024, "content_type": "image/png",
        "entity_type": "comment_image"}, {"X-CSRFToken": admin.csrf()})
    ck("C2-25", "评论图 >5MB → 400 FILE_SIZE_EXCEEDED（presign 期收紧）",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["fileSize"], f"got {code} {body}")
    code, body = admin.req("POST", presign, {
        "file_name": "doc.pdf", "file_size": 1024, "content_type": "application/pdf",
        "entity_type": "comment_image"}, {"X-CSRFToken": admin.csrf()})
    ck("C2-26", "评论图非图片格式 → 400 FILE_TYPE_NOT_ALLOWED",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["fileType"], f"got {code} {body}")

    uploaded = False
    try:
        direct = "http://localhost:9000" + d["upload_url"][len("/uploads"):]
        req = urllib.request.Request(direct, data=b"\x89PNG" + b"0" * (size - 4), method="PUT",
                                     headers={"Content-Type": "image/png"})
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            uploaded = resp.status in (200, 201)
    except Exception as e:  # noqa: BLE001
        ck("C2-27", "MinIO 直传（PUT 预签名）", False, f"直传失败：{e}")
    if uploaded:
        ck("C2-27", "MinIO 直传（PUT 预签名 3MB）→ 2xx", True)
        complete = (f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{i1['id']}"
                    f"/attachments/{asset_id}/complete/")
        code, body = admin.req("POST", complete, {}, {"X-CSRFToken": admin.csrf()})
        ck("C2-28", "complete 闭环 → 200（HEAD 尺寸校验过）", code == HTTP["OK"], f"got {code} {body}")
        code, body = admin.req(
            "GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{i1['id']}/attachments/")
        ck("C2-29", "评论图不入附件区列表（不占 20 配额）",
           code == HTTP["OK"] and len((body or {}).get("data") or []) == 0,
           f"got {len((body or {}).get('data') or [])}")
        dl = (f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{i1['id']}"
              f"/attachments/{asset_id}/download/?variant=thumb")
        code, body = admin.req("POST", ic, {
            "comment_html": f'<p>附件就是这张：</p><img src="{dl}" alt="shot.png">'},
            {"X-CSRFToken": admin.csrf()})
        d2 = (body or {}).get("data") or {}
        ck("C2-30", "图片评论 → 201 且 images=[asset_id]（服务端聚合）",
           code == HTTP["CREATED"] and d2.get("images") == [asset_id], f"got {code} {d2}")
        code, body = admin.req("POST", ic2, {
            "comment_html": f'<p>盗链：</p><img src="{dl}" alt="x">'},
            {"X-CSRFToken": admin.csrf()})
        ck("C2-31", "跨任务 asset 引用 → 域校验拒绝（images 空，不 500）",
           code == HTTP["CREATED"] and ((body or {}).get("data") or {}).get("images") == [],
           f"got {code} {body}")
    else:
        skip("C2-27", "MinIO 直传不可用——complete 闭环由 e2e/冒烟覆盖")
        skip("C2-28", "complete 闭环")
        skip("C2-29", "评论图不入附件区")
        skip("C2-30", "图片评论 images 聚合")
        skip("C2-31", "跨任务 asset 域校验")
    code, body = admin.req("POST", ic, {
        "comment_html": '<p>外链：</p><img src="http://evil.example.com/x.png">'},
        {"X-CSRFToken": admin.csrf()})
    html = ((body or {}).get("data") or {}).get("comment_html") or ""
    ck("C2-32", "外链图片剥离为链接文本（无 <img>，BR-15）",
       code == HTTP["CREATED"] and "<img" not in html
       and '<a href="http://evil.example.com/x.png">' in html, f"html={html!r}")

    # —— 通知三互斥（BR-11/12：mentioned > replied > commented）——
    i3 = make_issue(admin, ws, proj, "S3C-通知")
    as_url = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{i3['id']}/assignees/"
    admin.req("PUT", as_url, {"assignee_ids": [member_id]}, {"X-CSRFToken": admin.csrf()})
    admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{i3['id']}/comments/",
              {"comment_html": "<p>顶层：偶发 504</p>"}, {"X-CSRFToken": admin.csrf()})
    top_id = next(r["id"] for r in
                  admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/"
                                   f"{i3['id']}/comments/")[1]["data"] if r["root_id"] is None)
    member.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{i3['id']}/comments/",
               {"parent_id": top_id,
                "comment_html": f"<p>{mention(admin_id, '管理员')} 复现步骤如下</p>"},
               {"X-CSRFToken": member.csrf()})

    def notif_events(c: Client, issue_id: str) -> set[str]:
        code, body = c.req("GET", "/api/v1/users/me/notifications/?per_page=100")
        return {n.get("event") for n in (body or {}).get("data") or []
                if ((n.get("data") or {}).get("issue_id") == issue_id)}

    ok_admin = _wait_until(lambda: "issue.mentioned" in notif_events(admin, i3["id"]),
                           timeout_s=15)
    _wait_until(lambda: "issue.commented" in notif_events(member, i3["id"]), timeout_s=8)
    admin_ev = notif_events(admin, i3["id"])
    member_ev = notif_events(member, i3["id"])
    ck("C2-33", "被 @ 的顶层作者只收 mentioned（不再收 commented/replied）",
       ok_admin and admin_ev == {"issue.mentioned"}, f"admin_ev={admin_ev}")
    ck("C2-34", "指派者收 issue.commented（未被 @）",
       "issue.commented" in member_ev and "issue.mentioned" not in member_ev,
       f"member_ev={member_ev}")
    ck("C2-35", "回复操作者不因自己的回复收 replied/commented（域外剔除）",
       "comment.replied" not in member_ev, f"member_ev={member_ev}")

    # —— 归档项目只读（BR-01/IT-08）——
    _pg_exec("UPDATE projects SET status='archived' WHERE id = %s", (proj,))
    code, body = admin.req("POST", ic, {"comment_html": "<p>归档后评论</p>"},
                           {"X-CSRFToken": admin.csrf()})
    ck("C2-36", "归档项目发评论 → 403 PERM_PROJECT_ARCHIVED",
       code == HTTP["FORBIDDEN"] and error_code(body) == CODES["projectArchived"],
       f"got {code} {body}")
    code, body = admin.req("POST", rx, {"emoji": "🎉"}, {"X-CSRFToken": admin.csrf()})
    ck("C2-37", "归档项目点表情 → 403", code == HTTP["FORBIDDEN"]
       and error_code(body) == CODES["projectArchived"], f"got {code}")
    _pg_exec("UPDATE projects SET status='active' WHERE id = %s", (proj,))
    code, _ = admin.req("POST", ic, {"comment_html": "<p>恢复后可评</p>"},
                        {"X-CSRFToken": admin.csrf()})
    ck("C2-38", "恢复后评论 → 201（可逆）", code == HTTP["CREATED"], f"got {code}")

    code, _ = outsider.req("GET", ic)
    ck("C2-39", "外部用户评论列表 → 404", code == HTTP["NOT_FOUND"], f"got {code}")


# ═══ 4. COLLAB-003 项目动态流 ═══

def collab3_segment(admin, outsider, ws, admin_id):
    section("COLLAB-003 项目动态流")
    proj = make_project(admin, ws, "S3FLOW-ACT", "S3A")
    issues = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/"
    acts = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/activities/"

    a1 = make_issue(admin, ws, proj, "S3A-1")
    a2 = make_issue(admin, ws, proj, "S3A-2")
    a3 = make_issue(admin, ws, proj, "S3A-3")
    _, b = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/states/?include_cancelled=1")
    states = {x["group"]: x["id"] for x in b["data"]}
    done_state = states["completed"]

    admin.req("PATCH", issues + f"{a1['id']}/",
              {"name": "S3A-1 改", "priority": "high", "target_date": "2026-12-31"},
              {"X-CSRFToken": admin.csrf()})
    admin.req("POST", issues + f"{a1['id']}/worklogs/",
              {"minutes": 120, "worked_on": str(dt.date.today()), "note": "联调收尾"},
              {"X-CSRFToken": admin.csrf()})
    admin.req("POST", issues + f"{a2['id']}/comments/", {"comment_html": "<p>评论合流行</p>"},
              {"X-CSRFToken": admin.csrf()})
    admin.req("DELETE", issues + f"{a3['id']}/", None, {"X-CSRFToken": admin.csrf()})
    _sql_insert_issues(proj, admin_id, states["unstarted"], "S3A-B", 5, 9000)
    bulk_ids = [line.split("|")[0] for line in _pg_val(
        "SELECT id || '|' || name FROM issues WHERE project_id = '%s' AND name LIKE 'S3A-B%%' "
        "ORDER BY name" % proj).splitlines()]
    code, body = admin.req("PATCH", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/bulk/",
                           {"issue_ids": bulk_ids, "patch": {"state_id": done_state}},
                           {"X-CSRFToken": admin.csrf()})
    ck("A3-P1", "前置：批量改 5 条 → 200 updated=5", code == HTTP["OK"]
       and (body or {}).get("data", {}).get("updated") == 5, f"got {code} {body}")
    batch_epoch = (body or {}).get("data", {}).get("epoch")

    batch_seen = _wait_until(lambda: any(
        r.get("kind") == "batch" and r.get("batch_count") == 5
        for r in (admin.req("GET", acts)[1] or {}).get("data") or []), timeout_s=15)
    code, body = admin.req("GET", acts)
    rows = (body or {}).get("data") or []
    meta = (body or {}).get("meta") or {}
    kinds = {r.get("kind") for r in rows}
    batch_row = next((r for r in rows if r.get("kind") == "batch"), {})
    ck("A3-01", "三态行合流（activity/comment/batch 全出现）",
       code == HTTP["OK"] and kinds >= {"activity", "comment", "batch"}, f"kinds={kinds}")
    ck("A3-02", "batch 行折叠：batch_count=5 + summary 含「批量更新了 5」+ 无 issue",
       batch_seen and batch_row.get("batch_count") == 5
       and "批量更新了 5" in (batch_row.get("summary") or "")
       and batch_row.get("issue") is None, f"batch={batch_row}")
    ck("A3-03", "change_brief 单一变更直出（状态 待办 → 已完成）",
       "状态" in str(batch_row.get("change_brief")), f"brief={batch_row.get('change_brief')}")
    ck("A3-04", "meta 9 字段 + stream_cursor 仅首页（BR-12）",
       all(k in meta for k in ("next_cursor", "prev_cursor", "next_page_results",
                               "prev_page_results", "count", "total_count", "total_pages",
                               "page", "per_page")) and bool(meta.get("stream_cursor"))
       and meta.get("per_page") == 30, f"meta={meta}")
    ck("A3-05", "软删任务动态保留 + issue.is_deleted 投影（BR-06）",
       any(r.get("issue") and r["issue"].get("id") == a3["id"]
           and r["issue"].get("is_deleted") for r in rows), f"rows={len(rows)}")

    # 软删评论行固定文案（§2.6）
    cid = next(r["id"] for r in
               admin.req("GET", f"{issues}{a2['id']}/comments/")[1]["data"] if r["root_id"] is None)
    admin.req("DELETE", f"{issues}{a2['id']}/comments/{cid}/", None, {"X-CSRFToken": admin.csrf()})
    code, body = admin.req("GET", acts + "?event=comment&per_page=50")
    crows = [r for r in (body or {}).get("data") or [] if r.get("kind") == "comment"]
    ck("A3-06", "软删评论行 text=「删除了一条评论」（不显内容）",
       any(r.get("text") == "删除了一条评论" for r in crows), f"texts={[r.get('text') for r in crows]}")

    # —— 过滤矩阵（actor × event 恒 AND）——
    # 注：批量状态行的单条 Activity 已折叠为 batch 行（SQL 层先过滤后折叠）——
    # event=state 的命中以 batch 行（change_brief 含状态）+ 无 comment 行为口径
    code, body = admin.req("GET", acts + "?event=state&per_page=50")
    rows = (body or {}).get("data") or []
    ck("A3-07", "event=state：仅 state 域行（batch 折叠 + 无 comment），余字段全排除",
       code == HTTP["OK"] and rows
       and all(r.get("kind") != "comment" for r in rows)
       and any(r.get("kind") == "batch" and "状态" in str(r.get("change_brief")) for r in rows)
       and all(r.get("field") in (None, "state") for r in rows if r.get("kind") == "activity"),
       f"rows={[(r.get('kind'), r.get('field')) for r in rows]}")
    code, body = admin.req("GET", acts + "?event=comment&per_page=50")
    crows = (body or {}).get("data") or []
    ck("A3-08", "event=comment：仅评论行（activity 排除）",
       code == HTTP["OK"] and crows and all(r.get("kind") == "comment" for r in crows))
    code, body = admin.req("GET", acts + f"?actor_id={admin_id}&per_page=50")
    arows = (body or {}).get("data") or []
    ck("A3-09", "actor_id 过滤：全部行 actor.id == admin（comment 行同滤）",
       code == HTTP["OK"] and arows
       and all((r.get("actor") or {}).get("id") == admin_id for r in arows))
    code, body = admin.req("GET", acts + f"?actor_id={admin_id}&event=state&per_page=50")
    arows = (body or {}).get("data") or []
    ck("A3-10", "actor×event 组合恒 AND（IT-06）",
       code == HTTP["OK"] and arows
       and all((r.get("actor") or {}).get("id") == admin_id and r.get("field") == "state"
               for r in arows if r.get("kind") == "activity")
       and not any(r.get("kind") == "comment" for r in arows))
    code, body = admin.req("GET", acts + "?event=foo")
    ck("A3-11", "非法 event → 400 INVALID_PARAM + 可用值提示（BR-08）",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["invalidParam"]
       and (detail_of(body, "event") or {}).get("code") == "NOT_A_CHOICE", f"got {code}")
    code, body = admin.req("GET", acts + f"?actor_id={uuid_mod.uuid4()}")
    ck("A3-12", "域外 actor_id → 200 空集（过滤是缩小不是寻址）",
       code == HTTP["OK"] and (body or {}).get("data") == [], f"got {code}")

    # —— 组感知游标（BR-10/UT-16：组边界不割裂）——
    code, body = admin.req("GET", acts + "?per_page=1")
    p1 = (body or {}).get("data") or []
    m1 = (body or {}).get("meta") or {}
    ck("A3-13", "per_page=1 → 首页恰 1 组且为最新 batch 组（batch_count=5 非截断）",
       len(p1) == 1 and p1[0].get("kind") == "batch" and p1[0].get("batch_count") == 5
       and m1.get("stream_cursor"), f"p1={[r.get('kind') for r in p1]}")
    cur = m1.get("next_cursor")
    code, body = admin.req("GET", acts + f"?per_page=1&cursor={q(cur)}")
    p2 = (body or {}).get("data") or []
    ck("A3-14", "次页不含同 epoch 残余（组不跨页割裂，IT-10）",
       code == HTTP["OK"] and p2 and not any(
           r.get("kind") == "batch" and r.get("epoch") == p1[0].get("epoch") for r in p2),
       f"p2={[r.get('kind') for r in p2]}")
    ck("A3-15", "次页无 stream_cursor（仅首页）",
       "stream_cursor" not in ((body or {}).get("meta") or {}))
    code, body = admin.req("GET", acts + f"?per_page=3&cursor={q(cur)}")
    p2 = (body or {}).get("data") or []
    ck("A3-16", "keyset 严格更旧（次页全部行不晚于首页末组）",
       all(r.get("created_at", "") <= p1[0].get("created_at", "z") for r in p2),
       f"p1={p1[0].get('created_at')} p2={[r.get('created_at') for r in p2]}")
    code, body = admin.req("GET", acts + "?cursor=!!!invalid")
    ck("A3-17", "非法游标 → 400 VALIDATION_INVALID_CURSOR",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["invalidCursor"], f"got {code}")

    # —— ?epoch= 明细（BR-05/BR-11：100 截断 + meta 豁免）——
    code, body = admin.req("GET", acts + f"?epoch={batch_epoch}")
    d = (body or {}).get("data") or []
    meta = (body or {}).get("meta") or {}
    ck("A3-18", "?epoch= 明细：轻量行（issue_key/field/old/new）",
       code == HTTP["OK"] and len(d) == 5
       and all({"issue_key", "field", "old_value", "new_value"} <= set(r) for r in d),
       f"got {code} {d[:1]}")
    ck("A3-19", "明细 meta 豁免（仅 count/total_count/truncated/limit 四字段）",
       set(meta) == {"count", "total_count", "truncated", "limit"} and meta.get("limit") == 100
       and meta.get("truncated") is False, f"meta={meta}")
    _pg_exec(
        "INSERT INTO issue_activities (id, issue_id, actor_id, verb, field, old_value, new_value, "
        " comment, epoch, created_by_id, created_at, updated_at) "
        "SELECT gen_random_uuid(), (SELECT issue_id FROM issue_activities WHERE epoch = %s LIMIT 1), "
        " %s, 'updated', 'priority', 'none', 'low', 'batch: 补量', %s, %s, now(), now() "
        "FROM generate_series(1, 110) g", (batch_epoch, admin_id, batch_epoch, admin_id))
    code, body = admin.req("GET", acts + f"?epoch={batch_epoch}")
    meta = (body or {}).get("meta") or {}
    ck("A3-20", ">100 行 → count=100 + truncated=true（UT-12）",
       len((body or {}).get("data") or []) == 100 and meta.get("truncated") is True
       and meta.get("total_count") == 115, f"meta={meta}")
    code, body = admin.req("GET", acts + "?epoch=abc")
    ck("A3-21", "?epoch=abc 非数值 → 400（寻址型参数）",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["invalidParam"], f"got {code}")
    code, body = admin.req("GET", acts + "?per_page=999")
    ck("A3-22", "per_page 超上限 50 → 静默截断 + meta.degraded 提示",
       code == HTTP["OK"] and ((body or {}).get("meta") or {}).get("per_page") == 50
       and "per_page" in str((((body or {}).get("meta") or {}).get("degraded") or {})),
       f"meta={(body or {}).get('meta')}")
    code, _ = outsider.req("GET", acts)
    ck("A3-23", "非成员 → 404（存在性隐藏）", code == HTTP["NOT_FOUND"], f"got {code}")


# ═══ 5. BOARD-004 批量操作 ═══

def board4_segment(admin, member, viewer, outsider, ws, admin_id, member_id, viewer_id):
    section("BOARD-004 批量操作")
    proj = make_project(admin, ws, "S3FLOW-BULK", "S3B")
    issues = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/"
    bulk = issues + "bulk/"
    members_api = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/members/"
    admin.req("POST", members_api, {"member_ids": [member_id], "role": 15},
              {"X-CSRFToken": admin.csrf()})
    admin.req("POST", members_api, {"member_ids": [viewer_id], "role": 5},
              {"X-CSRFToken": admin.csrf()})
    # throttle / nowait 专用账号（配额独立，见文件头注记）
    c_thr, c_thr_email = Client(BASE), None
    c_thr_email, _ = signup(c_thr, "s3thr-")
    c_now, c_now_email = Client(BASE), None
    c_now_email, _ = signup(c_now, "s3now-")
    invite_member(admin, ws, c_thr, c_thr_email)
    invite_member(admin, ws, c_now, c_now_email)
    admin.req("POST", members_api, {"member_ids": [uid_of(c_thr), uid_of(c_now)], "role": 15},
              {"X-CSRFToken": admin.csrf()})
    # 复用清理前缀：s3thr/s3now 也以 s3 开头，随 s3% 前缀一并清理

    _, b = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/states/?include_cancelled=1")
    states = {x["group"]: x["id"] for x in b["data"]}
    done_state = states["completed"]

    ids12 = [make_issue(admin, ws, proj, f"S3B-{i:02d}")["id"] for i in range(12)]
    bx = make_issue(admin, ws, proj, "S3B-阻塞源")
    by = make_issue(admin, ws, proj, "S3B-被阻塞")
    admin.req("POST", issues + f"{bx['id']}/relations/",
              {"related_issue_id": by["id"], "relation_type": "blocks"},
              {"X-CSRFToken": admin.csrf()})

    # —— 动作 1：批量状态（admin #1）——
    code, body = admin.req("PATCH", bulk, {
        "issue_ids": ids12, "patch": {"state_id": done_state}, "comment": "迭代收尾批量关闭"},
        {"X-CSRFToken": admin.csrf()})
    d = (body or {}).get("data") or {}
    ck("B4-01", "批量改状态 12 条 → 200 {updated:12, epoch, action:state}",
       code == HTTP["OK"] and d.get("updated") == 12 and d.get("action") == "state"
       and isinstance(d.get("epoch"), (int, float))
       and ((body or {}).get("meta") or {}).get("batch_size") == 12, f"got {code} {body}")
    code, body = admin.req("GET", issues + f"{ids12[0]}/")
    ck("B4-02", "completed_at 派生齐写 + 状态落库",
       code == HTTP["OK"] and (body or {}).get("data", {}).get("state_group") == "completed"
       and (body or {}).get("data", {}).get("completed_at"), f"got {code}")

    # —— 守卫失败：整批回滚 + 项级索引（member #1）——
    before = [admin.req("GET", issues + f"{i}/")[1]["data"]["updated_at"]
              for i in (ids12[0], ids12[1])]
    code, body = member.req("PATCH", bulk, {
        "issue_ids": [ids12[0], by["id"], ids12[1]], "patch": {"state_id": done_state}},
        {"X-CSRFToken": member.csrf()})
    det = ((body or {}).get("error") or {}).get("details") or []
    ck("B4-03", "第 2 条被 BLOCKED_BY → 400 + field=issue_ids[1]（0 基）+ 含阻塞源编号",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["validation"]
       and det and det[0].get("field") == "issue_ids[1]" and det[0].get("code") == "BLOCKED_BY"
       and "S3B-" in str(det[0].get("message", "")), f"got {code} {det}")
    after = [admin.req("GET", issues + f"{i}/")[1]["data"]["updated_at"]
             for i in (ids12[0], ids12[1])]
    ck("B4-04", "整批回滚零残留（其余 2 条 updated_at 不变）", before == after,
       f"{before} != {after}")

    # —— 优先级（admin #2；B4-06~08 载荷级 400 走 c_now 账号——throttle 在 400 前计数）——
    code, body = admin.req("PATCH", bulk, {"issue_ids": ids12[:2], "patch": {"priority": "urgent"}},
                           {"X-CSRFToken": admin.csrf()})
    ck("B4-05", "批量优先级 → 200 updated=2", code == HTTP["OK"]
       and (body or {}).get("data", {}).get("updated") == 2
       and (body or {}).get("data", {}).get("action") == "priority", f"got {code}")
    code, body = c_now.req("PATCH", bulk, {"issue_ids": ids12[:2], "patch": {"priority": "fancy"}},
                           {"X-CSRFToken": c_now.csrf()})
    ck("B4-06", "非法优先级 → 400 NOT_A_CHOICE（载荷级）",
       code == HTTP["BAD_REQUEST"]
       and (detail_of(body, "patch.priority") or {}).get("code") == "NOT_A_CHOICE", f"got {code}")
    code, body = c_now.req("PATCH", bulk, {"issue_ids": ids12[:2]}, {"X-CSRFToken": c_now.csrf()})
    ck("B4-07", "缺动作字段 → 400 INVALID", code == HTTP["BAD_REQUEST"], f"got {code}")
    code, body = c_now.req("PATCH", bulk, {"issue_ids": ids12[:2], "comment": "长" * 501,
                                          "patch": {"priority": "low"}},
                           {"X-CSRFToken": c_now.csrf()})
    ck("B4-08", "批量备注 501 字 → 400 TOO_LONG", code == HTTP["BAD_REQUEST"]
       and (detail_of(body, "comment") or {}).get("code") == "TOO_LONG", f"got {code}")

    # —— 集合三模式（member #2..#5：指派）——
    def assign(c: Client, mode: str, who: list[str]):
        return c.req("PATCH", bulk, {
            "issue_ids": ids12[2:4], "assignees": {"mode": mode, "assignee_ids": who}},
            {"X-CSRFToken": c.csrf()})

    assign(member, "replace", [admin_id])
    got = admin.req("GET", issues + f"{ids12[2]}/")[1]["data"]["assignee_ids"]
    ck("B4-09", "assignees replace → [admin]", got == [admin_id], f"got {got}")
    assign(member, "add", [member_id])
    got = admin.req("GET", issues + f"{ids12[2]}/")[1]["data"]["assignee_ids"]
    ck("B4-10", "assignees add 并集 → {admin, member}（UT-06）",
       set(got) == {admin_id, member_id}, f"got {got}")
    assign(member, "remove", [admin_id])
    got = admin.req("GET", issues + f"{ids12[2]}/")[1]["data"]["assignee_ids"]
    ck("B4-11", "assignees remove 差集 → [member]", got == [member_id], f"got {got}")
    code, body = member.req("PATCH", bulk, {
        "issue_ids": ids12[2:3],
        "assignees": {"mode": "replace", "assignee_ids": [str(uuid_mod.uuid4())]}},
        {"X-CSRFToken": member.csrf()})
    det = ((body or {}).get("error") or {}).get("details") or []
    ck("B4-12", "指派非成员 → 项级失败（issue_ids[0] + 子码）",
       code == HTTP["BAD_REQUEST"] and det and det[0].get("field") == "issue_ids[0]",
       f"got {code} {det}")

    # —— 标签三模式（member #6/#7）——
    admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/labels/",
              {"name": "S3BL1", "color": "#111111"}, {"X-CSRFToken": admin.csrf()})
    admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/labels/",
              {"name": "S3BL2", "color": "#222222"}, {"X-CSRFToken": admin.csrf()})
    _, b = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/labels/")
    L = {x["name"]: x["id"] for x in b["data"]}
    code, body = member.req("PATCH", bulk, {
        "issue_ids": ids12[4:6],
        "labels": {"mode": "add", "label_ids": [L["S3BL1"], L["S3BL2"]]}},
        {"X-CSRFToken": member.csrf()})
    got = set(admin.req("GET", issues + f"{ids12[4]}/")[1]["data"]["label_ids"])
    ck("B4-13", "labels add → 两标签", code == HTTP["OK"] and got == {L["S3BL1"], L["S3BL2"]},
       f"got {got}")
    member.req("PATCH", bulk, {"issue_ids": ids12[4:6],
                               "labels": {"mode": "remove", "label_ids": [L["S3BL1"]]}},
               {"X-CSRFToken": member.csrf()})
    got = admin.req("GET", issues + f"{ids12[4]}/")[1]["data"]["label_ids"]
    ck("B4-14", "labels remove → 剩 [S3BL2]", got == [L["S3BL2"]], f"got {got}")
    code, body = c_now.req("PATCH", bulk, {
        "issue_ids": ids12[4:5],
        "labels": {"mode": "add", "label_ids": [str(uuid_mod.uuid4())]}},
        {"X-CSRFToken": c_now.csrf()})
    ck("B4-15", "他项目标签 → 400 labels.label_ids DOES_NOT_EXIST（载荷级）",
       code == HTTP["BAD_REQUEST"]
       and (detail_of(body, "labels.label_ids") or {}).get("code") == "DOES_NOT_EXIST", f"got {code}")

    # —— 上限 / 项级不可见（member #8..#10）——
    code, body = member.req("PATCH", bulk, {
        "issue_ids": [str(uuid_mod.uuid4()) for _ in range(101)], "patch": {"priority": "low"}},
        {"X-CSRFToken": member.csrf()})
    ck("B4-16", "101 条 → 400 VALIDATION_BULK_LIMIT_EXCEEDED + LIMIT",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["bulkLimit"]
       and (detail_of(body, "issue_ids") or {}).get("code") == "LIMIT", f"got {code} {body}")
    code, body = member.req("PATCH", bulk, {
        "issue_ids": [ids12[6], str(uuid_mod.uuid4())], "patch": {"priority": "low"}},
        {"X-CSRFToken": member.csrf()})
    det = ((body or {}).get("error") or {}).get("details") or []
    ck("B4-17", "混入不可见 id → 项级 issue_ids[1] DOES_NOT_EXIST（不 404 整批）",
       code == HTTP["BAD_REQUEST"] and det and det[0].get("field") == "issue_ids[1]"
       and det[0].get("code") == "DOES_NOT_EXIST", f"got {code} {det}")

    # —— 批量归档（admin #4/#5：级联幂等 + archived_count 口径）——
    p_root = make_issue(admin, ws, proj, "S3B-归档根")
    admin.req("POST", issues + f"{p_root['id']}/sub-issues/", {"name": "S3B-归档子"},
              {"X-CSRFToken": admin.csrf()})
    code, body = admin.req("POST", bulk + "archive/",
                           {"issue_ids": [p_root["id"], ids12[7]], "comment": "季度收尾"},
                           {"X-CSRFToken": admin.csrf()})
    d = (body or {}).get("data") or {}
    ck("B4-18", "批量归档（1 父带 1 子 + 1 单）→ archived_count=3 / affected_total=3",
       code == HTTP["OK"] and d.get("archived_count") == 3 and d.get("affected_total") == 3
       and ((body or {}).get("meta") or {}).get("cascade") == 1, f"got {code} {body}")
    code, body = admin.req("POST", bulk + "archive/", {"issue_ids": [p_root["id"], ids12[7]]},
                           {"X-CSRFToken": admin.csrf()})
    ck("B4-19", "重复归档幂等 → archived_count=0（仅计新归档行）",
       code == HTTP["OK"] and (body or {}).get("data", {}).get("archived_count") == 0, f"got {code}")

    # —— 预检（admin #6/#7：BE-7/BE-8）——
    p2 = make_issue(admin, ws, proj, "S3B-预检根")
    admin.req("POST", issues + f"{p2['id']}/sub-issues/", {"name": "S3B-预检子"},
              {"X-CSRFToken": admin.csrf()})
    updated_before = admin.req("GET", issues + f"{p2['id']}/")[1]["data"]["updated_at"]
    code, body = admin.req("POST", bulk + "preview/", {
        "issue_ids": [p2["id"], str(uuid_mod.uuid4())], "action": "delete"},
        {"X-CSRFToken": admin.csrf()})
    d = (body or {}).get("data") or {}
    ck("B4-20", "preview 统计：selected=2 / cascade=1 / affected=2 + denied[0].index=1",
       code == HTTP["OK"] and d.get("selected") == 2 and d.get("cascade_total") == 1
       and d.get("affected_total") == 2 and d.get("denied")
       and d["denied"][0].get("index") == 1, f"got {code} {d}")
    ck("B4-21", "preview 只读零写入（updated_at 不变）",
       admin.req("GET", issues + f"{p2['id']}/")[1]["data"]["updated_at"] == updated_before)
    code, body = admin.req("POST", bulk + "preview/", {"issue_ids": ids12[:1], "action": "fancy"},
                           {"X-CSRFToken": admin.csrf()})
    ck("B4-22", "preview 非法 action → 400", code == HTTP["BAD_REQUEST"], f"got {code}")

    # —— 批量删除（admin #8/#9/#10：BE-9/BE-11/BE-12）——
    del_ids = [ids12[8], ids12[9], p2["id"]]  # 预检根带 1 子 → affected 4
    code, body = admin.req("DELETE", bulk, {"issue_ids": del_ids, "confirm_count": 2},
                           {"X-CSRFToken": admin.csrf()})
    ck("B4-23", "confirm_count 错配 → 400", code == HTTP["BAD_REQUEST"]
       and bool(detail_of(body, "confirm_count")), f"got {code} {body}")
    idem_key = str(uuid_mod.uuid4())
    code, body, hdrs = admin.req("DELETE", bulk, {"issue_ids": del_ids, "confirm_count": 3},
                                 {"X-CSRFToken": admin.csrf(), "Idempotency-Key": idem_key},
                                 want_headers=True)
    d = (body or {}).get("data") or {}
    ck("B4-24", "批量删除 3 选（1 父 1 子）→ deleted=3 / affected_total=4",
       code == HTTP["OK"] and d.get("deleted") == 3 and d.get("affected_total") == 4
       and ((body or {}).get("meta") or {}).get("cascade") == 1, f"got {code} {body}")
    code2, body2, hdrs2 = admin.req("DELETE", bulk,
                                    {"issue_ids": del_ids, "confirm_count": 3},
                                    {"X-CSRFToken": admin.csrf(), "Idempotency-Key": idem_key},
                                    want_headers=True)
    ck("B4-25", "幂等键重放 → 首次响应回放 + Idempotency-Replayed: true",
       code2 == HTTP["OK"] and hdrs2.get("Idempotency-Replayed") == "true"
       and (body2 or {}).get("data", {}).get("deleted") == d.get("deleted"),
       f"got {code2} replayed={hdrs2.get('Idempotency-Replayed')}")
    code, _ = admin.req("GET", issues + f"{ids12[8]}/")
    ck("B4-26", "删除后 GET → 404", code == HTTP["NOT_FOUND"], f"got {code}")

    # —— VIEWER 拒写（403 在 throttle 之前，不耗配额）——
    code, body = viewer.req("PATCH", bulk, {"issue_ids": ids12[:1], "patch": {"priority": "low"}},
                            {"X-CSRFToken": viewer.csrf()})
    ck("B4-27", "VIEWER 批量写 → 403 PERM_ROLE_INSUFFICIENT",
       code == HTTP["FORBIDDEN"] and error_code(body) == CODES["roleInsufficient"], f"got {code}")

    # —— throttle（c_thr 专用账号 11 连发：BE-14）——
    codes = []
    retry_after = None
    for _ in range(11):
        code, body, hdrs = c_thr.req("POST", bulk + "preview/", {
            "issue_ids": ids12[:1], "action": "delete"},
            {"X-CSRFToken": c_thr.csrf()}, want_headers=True)
        codes.append(code)
        if code == HTTP["TOO_MANY"] and retry_after is None:
            retry_after = hdrs.get("Retry-After")
    ck("B4-28", "throttle：60s 内第 11 次 → 429 + Retry-After 头",
       codes[0] == HTTP["OK"] and codes[-1] == HTTP["TOO_MANY"]
       and codes.count(HTTP["TOO_MANY"]) >= 1 and retry_after is not None,
       f"codes={codes} retry_after={retry_after}")
    code, body = c_thr.req("POST", bulk + "preview/", {"issue_ids": ids12[:1], "action": "delete"},
                           {"X-CSRFToken": c_thr.csrf()})
    ck("B4-29", "429 错误码 = RATE_LIMIT_EXCEEDED", error_code(body) == CODES["rateLimited"],
       f"{error_code(body)}")

    # —— nowait 并发 409（c_now 专用账号 #1：第二连接锁行）——
    lock_ids = "','".join(ids12[10:12])
    try:
        holder = subprocess.Popen(  # noqa: S603 —— 持锁 6s 的并发事务
            ["docker", "exec", "-i", "rp-pg", "psql", "-U", "rp", "-d", "rabbit_projects", "-c",
             f"BEGIN; SELECT id FROM issues WHERE id IN ('{lock_ids}') FOR UPDATE; "
             f"SELECT pg_sleep(6);"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.6)
        code, body = c_now.req("PATCH", bulk,
                               {"issue_ids": ids12[10:12], "patch": {"priority": "low"}},
                               {"X-CSRFToken": c_now.csrf()})
        holder.wait(timeout=15)
        ck("B4-30", "行被并发事务持有 → 409 RESOURCE_CONFLICT（nowait 快速失败）",
           code == HTTP["CONFLICT"] and error_code(body) == CODES["conflict"], f"got {code} {body}")
    except Exception as e:  # noqa: BLE001
        ck("B4-30", "nowait 并发 409", False, f"构造失败：{e}")

    # —— 动态流聚合（IT-02：批量更新了 12 个，worker 消费后 5s 内）——
    acts = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/activities/"
    found = _wait_until(lambda: any(
        r.get("kind") == "batch" and r.get("batch_count") == 12
        for r in (admin.req("GET", acts + "?per_page=50")[1] or {}).get("data") or []),
        timeout_s=15)
    code, body = admin.req("GET", acts + "?per_page=50")
    rows = [r for r in (body or {}).get("data") or []
            if r.get("kind") == "batch" and r.get("batch_count") == 12]
    ck("B4-31", "动态流聚合「批量更新了 12 个任务」（轮询 ≤5s 粒度命中）",
       found and len(rows) >= 1 and "批量更新了 12" in (rows[0].get("summary") or ""),
       f"rows={rows[:1]}")
    code, _ = outsider.req("PATCH", bulk, {"issue_ids": ids12[:1], "patch": {"priority": "low"}},
                           {"X-CSRFToken": outsider.csrf()})
    ck("B4-32", "外部用户 bulk → 404（项目隔离）", code == HTTP["NOT_FOUND"], f"got {code}")


# ═══ 6. COLLAB-004 实时票据与 WS ═══

def collab4_segment(admin, member, viewer, outsider, ws, admin_id, member_id, viewer_id, out_ws):
    section("COLLAB-004 实时票据与 WS")
    proj = make_project(admin, ws, "S3FLOW-WS", "S3W")
    members_api = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/members/"
    admin.req("POST", members_api, {"member_ids": [member_id], "role": 15},
              {"X-CSRFToken": admin.csrf()})
    admin.req("POST", members_api, {"member_ids": [viewer_id], "role": 5},
              {"X-CSRFToken": admin.csrf()})
    i1 = make_issue(admin, ws, proj, "S3W-任务")
    rt = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/realtime-token/"
    tab = str(uuid_mod.uuid4())

    code, body = admin.req("POST", rt, {"client_tab_id": tab, "issue_rooms": [i1["id"]]},
                           {"X-CSRFToken": admin.csrf()})
    d = (body or {}).get("data") or {}
    token = d.get("token") or ""
    ck("C4-01", "换票 → 200 token/rooms/expires_at/renew_after=90",
       code == HTTP["OK"] and token.count(".") == 2
       and d.get("renew_after") == 90 and bool(d.get("expires_at")), f"got {code} {d}")
    ck("C4-02", "rooms 服务端装配：project + issue + user 恒附（裁决非声明）",
       set(d.get("rooms") or []) == {f"project:{proj}", f"issue:{i1['id']}", f"user:{admin_id}"},
       f"rooms={d.get('rooms')}")

    out_proj = make_project(outsider, out_ws, "S3FLOW-OUT", "S3O")
    _, b = outsider.req("POST", f"/api/v1/workspaces/{q(out_ws)}/projects/{out_proj}/issues/",
                        {"name": "S3W-外人任务"}, {"X-CSRFToken": outsider.csrf()})
    out_issue_id = b["data"]["id"]
    code, body = admin.req("POST", rt, {"client_tab_id": tab, "issue_rooms": [out_issue_id]},
                           {"X-CSRFToken": admin.csrf()})
    ck("C4-03", "issue 不可见 → 403 PERM_DENIED 拒整票（存在性隐藏不适用）",
       code == HTTP["FORBIDDEN"] and error_code(body) == CODES["permDenied"], f"got {code} {body}")
    code, body = viewer.req("POST", rt, {"client_tab_id": str(uuid_mod.uuid4())},
                            {"X-CSRFToken": viewer.csrf()})
    ck("C4-04", "VIEWER 可换票（project.read 全员）",
       code == HTTP["OK"] and f"project:{proj}" in ((body or {}).get("data") or {}).get("rooms", []),
       f"got {code}")
    code, body = admin.req("POST", rt, {
        "client_tab_id": tab, "issue_rooms": [str(uuid_mod.uuid4()) for _ in range(9)]},
        {"X-CSRFToken": admin.csrf()})
    ck("C4-05", "issue_rooms 9 个（超 rooms≤10 上限）→ 400 INVALID_PARAM",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["invalidParam"], f"got {code}")
    code, body = admin.req("POST", rt, {"client_tab_id": ""}, {"X-CSRFToken": admin.csrf()})
    ck("C4-06", "client_tab_id 空 → 400", code == HTTP["BAD_REQUEST"]
       and error_code(body) == CODES["invalidParam"], f"got {code}")

    # —— 续签（BR-02：jti 轮换；验签需 LIVE_JWT_PUBLIC_KEY——环境未配置时走 503 分支）——
    code, body = admin.req("POST", "/api/v1/users/me/realtime-token/renew/",
                           {"token": token, "client_tab_id": tab}, {"X-CSRFToken": admin.csrf()})
    d2 = (body or {}).get("data") or {}
    if code == HTTP["OK"] and d2.get("token"):
        old_c = _jwt_claims(token) if token else {}
        new_c = _jwt_claims(d2["token"])
        ck("C4-07", "续签 → 200 新票据 + rooms 沿用",
           (d2.get("token") or "").count(".") == 2
           and set(d2.get("rooms") or []) == set(d.get("rooms") or []), f"got {code}")
        ck("C4-08", "jti 轮换 + exp 递增（UT-12）",
           old_c.get("jti") != new_c.get("jti") and new_c.get("exp", 0) >= old_c.get("exp", 0),
           f"jti {old_c.get('jti')} → {new_c.get('jti')}")
        rtok = d2["token"]
    elif code == HTTP["SRV_UNAVAILABLE"] and error_code(body) == CODES["liveUnavailable"]:
        # 本环境 api 仅注入 LIVE_JWT_PRIVATE_KEY（签发面），未注入公钥（验签面）——
        # 密钥分离下续签 503 为注册契约分支；jti 轮换/他票 403/伪造 401 由
        # apps/api/tests/test_realtime_ticket.py（test_renew_rotates_jti_and_keeps_rooms 等）
        # 覆盖，此处不重启 api 补环境（门禁纪律：不动在跑服务）。
        ck("C4-07", "续签公钥未配置环境 → 503 SERVER_LIVE_SERVICE_UNAVAILABLE（注册契约分支）",
           True, "")
        skip("C4-08", "jti 轮换（环境未配 LIVE_JWT_PUBLIC_KEY，pytest test_realtime_ticket 覆盖）")
        rtok = token
    else:
        ck("C4-07", "续签 → 200/503 之外的意外响应", False, f"got {code} {body}")
        rtok = token
    if rtok != token or code == HTTP["OK"]:
        code, body = member.req("POST", "/api/v1/users/me/realtime-token/renew/",
                                {"token": rtok, "client_tab_id": tab},
                                {"X-CSRFToken": member.csrf()})
        ck("C4-09", "续签他人票据 → 403 PERM_DENIED",
           code == HTTP["FORBIDDEN"] and error_code(body) == CODES["permDenied"], f"got {code}")
        code, body = admin.req("POST", "/api/v1/users/me/realtime-token/renew/",
                               {"token": "x.y.z", "client_tab_id": tab},
                               {"X-CSRFToken": admin.csrf()})
        ck("C4-10", "伪造票据续签 → 401 AUTH_TOKEN_EXPIRED",
           code == HTTP["UNAUTHORIZED"] and error_code(body) == CODES["tokenExpired"],
           f"got {code} {body}")
    else:
        skip("C4-09", "他票续签 403（pytest test_renew_other_users_token_403 覆盖）")
        skip("C4-10", "伪造票据 401（pytest test_renew_expired_old_token_401 覆盖）")

    # —— verify-rooms（内部端点：X-Internal-Key 三态）——
    verify = "/api/v1/internal/realtime/verify-rooms/"
    bogus_room = f"project:{uuid_mod.uuid4()}"
    tickets = [{"sub": admin_id, "rooms": [f"project:{proj}", f"issue:{i1['id']}"]},
               {"sub": admin_id, "rooms": [bogus_room]}]
    code, body = admin.req("POST", verify, {"tickets": tickets})
    ck("C4-11", "verify-rooms 缺 X-Internal-Key → 403 PERM_DENIED",
       code == HTTP["FORBIDDEN"] and error_code(body) == CODES["permDenied"], f"got {code}")
    code, body = admin.req("POST", verify, {"tickets": tickets}, {"X-Internal-Key": "wrong"})
    ck("C4-12", "错误密钥 → 403", code == HTTP["FORBIDDEN"], f"got {code}")
    internal_key = _discover_internal_key()
    if internal_key:
        code, body = admin.req("POST", verify, {"tickets": tickets},
                               {"X-Internal-Key": internal_key})
        inv = ((body or {}).get("data") or {}).get("invalid") or []
        ck("C4-13", "正确密钥 → 200 invalid 列表（失效房间精确到项）",
           code == HTTP["OK"] and len(inv) == 1 and inv[0].get("rooms") == [bogus_room]
           and inv[0].get("sub") == admin_id, f"got {code} {inv}")
    else:
        skip("C4-13", "INTERNAL_KEY 未发现（非本地 compose 环境）——正确密钥分支由 live 侧 e2e 覆盖")

    # —— WS 最小闭环（纯标准库；无第三方 ws 客户端依赖）——
    try:
        m = MiniWS(f"{LIVE_BASE}/live/connect?token={token}")
        ck("C4-14", "WS 握手 101 + Sec-WebSocket-Accept 校验通过",
           m.accept_ok and "101" in m.status_line, m.status_line)
        _, op, payload = m.read_frame()
        frame = json.loads(payload) if op == 1 else {}
        ck("C4-15", "connected 帧：rooms/ws/heartbeat=25（§2.1）",
           frame.get("event") == "connected"
           and f"project:{proj}" in (frame.get("payload") or {}).get("rooms", [])
           and (frame.get("payload") or {}).get("heartbeat") == 25, f"frame={frame}")
        m.send_text("ping")
        got_pong = False
        for _ in range(3):
            _, op, payload = m.read_frame(timeout_s=6)
            if op == 1 and payload == b"pong":
                got_pong = True
                break
        ck("C4-16", "应用层 ping → pong（心跳容忍）", got_pong, "未收到 pong")
        m.close()
        m2 = MiniWS(f"{LIVE_BASE}/live/connect?token=invalid.jwt.here")
        _, op, payload = m2.read_frame()
        close_code = struct.unpack(">H", payload[:2])[0] if op == 8 and len(payload) >= 2 else None
        ck("C4-17", "无效票据 → close(4001 TOKEN_INVALID)（BR-01）", close_code == 4001,
           f"op={op} code={close_code}")
        m2.close()
    except Exception as e:  # noqa: BLE001
        ck("C4-14", "WS 最小闭环（live:3000）", False, f"{e}——live 未运行？")


if __name__ == "__main__":
    sys.exit(main())
