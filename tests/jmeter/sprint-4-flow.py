#!/usr/bin/env python3
"""Sprint 4 接口端到端验证 —— 与 sprint-0/1/2/3-flow.py 并列的 CI gate（七段）。

用法：python3 tests/jmeter/sprint-4-flow.py [http://localhost:8000]
前置：API + 真实 PG + Redis + MinIO + RabbitMQ + worker（-Q activity,celery；
FILE-003 预览派生 / FILE-002 下载计数依赖消费——CLAUDE.md 测试节常驻 worker 说明）。
推荐 `uv run --project apps/api python tests/jmeter/sprint-4-flow.py`（直改库走
docker exec psql，无需本地 psycopg）。

七段对齐五规格（docs/sprint-4-gantt-file/）：
  1. FILE-002  目录 CRUD / 直传三步 / 列表筛选游标 / 重命名移动双挂 / 三态可见性
              / 软删回收站 / 还原(恢复) / purge 引用计数 / 配额四字段
  2. FILE-003  分片会话五端点 / 断点续传 / 片级核对 / 版本列表回滚 / 内容 302
              / 预览调度（image 排队 / pdf 透传 / office 无工具 / 文本 2MB / 视频 400）
  3. FILE-004  分享创建（密码/有效期/权限）/ 公开元信息脱敏 / unlock 限流锁定
              / 读时四查统一 410 / 延期吊销 / 管理列表 / 访问计数
  4. GANTT-001 视窗取数相交判定 / tz 解析 / 进度派生 is_overdue 口径（开放端）
              / 未排期 / relations bulk（violation+镜像去重）/ view_id 联动（ADR-0021）
  5. GANTT-002 overdue-summary 三数字完整集聚合 / 前 20 截断 / 429 第 11 次
  6. 实时段   换票 file_rooms（不可见资产 403 拒整票）——事件扇出链路由 T4-08 e2e 覆盖，
              此处只断票据与房间装配
  7. 越权隔离 外部用户 404 成对 / VIEWER 写路径 403 / 未认证 401 / 405 方法集

契约常量与端点模板全部来自 tests/jmeter/_contract.py（CLAUDE.md 测试脚本规范 ①）。
throttle 配额注记：GANTT-002 聚合端点 10 次/min/用户（§4.2.1 契约要点 4）为全局
按用户计数——admin 段内调用 ≤6 次，429 连发走专用 s4agg 账号；FILE-004 unlock
5 次/10min/(IP,slug)——锁定链与成功链分 slug 互不干扰。
自建数据：项目名 S4FLOW-* 前缀 / 用户 s4* 邮箱前缀（s4flow/s4mem/s4view/s4out/
s4agg/s4adm2，与 pytest 夹具 f2-*/gantt-* 无交集），结束按前缀清理（幂等可重跑）。
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import subprocess
import sys
import time
import urllib.request
import uuid as uuid_mod

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _contract import CODES, ENDPOINTS, HTTP, Client, detail_of, error_code, q  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
PASS = 0
FAIL = 0
SKIP = 0
FAILURES: list[str] = []

MINIO_BASE = "http://localhost:9000"

#: 1×1 真 PNG（预览派生 worker 用 PIL 解码，假字节会 giveup）
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


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
        {"email": email, "password": "Rabbit123!", "display_name": tag.upper().strip("-") + " S4"},
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
    code, body = admin.req(
        "POST", f"/api/v1/workspaces/{q(ws)}/invitations/",
        {"emails": [email], "role": 10}, {"X-CSRFToken": admin.csrf()})
    rows = (body or {}).get("data") or []
    if rows and rows[0].get("status") == "added":
        return
    links = ((body or {}).get("meta") or {}).get("invite_links") or {}
    token = (links.get(email) or "").rsplit("/", 1)[-1]
    if token:
        user.req("POST", f"/api/v1/invitations/{token}/accept/", {}, {"X-CSRFToken": user.csrf()})


def uid_of(c: Client) -> str:
    return c.req("GET", "/api/v1/users/me/")[1]["data"]["user"]["id"]


def join_project(admin: Client, ws: str, proj: str, member_id: str, role: int):
    code, _ = admin.req(
        "POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/members/",
        {"member_ids": [member_id], "role": role}, {"X-CSRFToken": admin.csrf()})
    return code


def _pg_exec(sql: str, params=()):
    """docker exec psql 直改库（sprint-3-flow 同款；%s 按序占位转义）。"""
    quoted = sql
    for v in params:
        quoted = quoted.replace("%s", "'" + str(v).replace("'", "''") + "'", 1)
    r = subprocess.run(  # noqa: S603 —— 常量 SQL + 参数化转义
        ["docker", "exec", "-i", "rp-pg", "psql", "-U", "rp", "-d", "rabbit_projects",
         "-v", "ON_ERROR_STOP=1", "-c", quoted],
        capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300])
    return r.stdout


def _pg_val(sql: str) -> str:
    r = subprocess.run(  # noqa: S603
        ["docker", "exec", "-i", "rp-pg", "psql", "-U", "rp", "-d", "rabbit_projects", "-At", "-c", sql],
        capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300])
    return r.stdout.strip()


def _wait_until(predicate, timeout_s: float = 25.0, interval: float = 0.5) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def dstr(offset_days: int) -> str:
    return (dt.date.today() + dt.timedelta(days=offset_days)).isoformat()


# ── MinIO 直传 / 分片直传辅助（FILE-002 §4.3.1；坑 #13：/uploads 同源反代语义）──

def minio_put(upload_url: str, data: bytes, content_type: str = "") -> tuple[bool, str]:
    """PUT 预签名 URL → (ok, etag)。presign 返回 /uploads/<bucket>/<key>?X-Amz-…，
    直连 API 无网关——换 MinIO 源站地址（sprint-3-flow C2-27 同款）。"""
    direct = MINIO_BASE + upload_url[len("/uploads"):]
    req = urllib.request.Request(
        direct, data=data, method="PUT",
        headers={"Content-Type": content_type or "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
            return resp.status in (200, 201), (resp.headers.get("ETag") or "").strip('"')
    except Exception:  # noqa: BLE001
        return False, ""


def upload_direct(c: Client, ws: str, proj: str, folder_id: str, name: str,
                  data: bytes, mime: str) -> tuple[str, dict]:
    """presign → 真实 PUT → complete 全链路 → (asset_id, file_row)；失败 asset_id 为空。"""
    presign = ENDPOINTS["folder_presign"].format(ws=q(ws), proj=proj, folder=folder_id)
    code, body = c.req("POST", presign,
                       {"file_name": name, "file_size": len(data), "content_type": mime},
                       {"X-CSRFToken": c.csrf()})
    if code != HTTP["CREATED"]:
        return "", {"_err": f"presign {code} {body}"}
    d = body["data"]
    ok, _etag = minio_put(d["upload_url"], data, mime)
    if not ok:
        return "", {"_err": "minio put failed"}
    code, body = c.req("POST", ENDPOINTS["file_complete"].format(ws=q(ws), proj=proj, asset=d["asset_id"]),
                       {}, {"X-CSRFToken": c.csrf()})
    if code != HTTP["OK"]:
        return "", {"_err": f"complete {code} {body}"}
    # complete 响应才是最终行：同名并入版本链时 presign 的暂存行被硬删、
    # 行 id 换为既有资产（FILE-003 §4.3.4）——不得返回暂存 asset_id
    return body["data"].get("id") or d["asset_id"], body["data"]


def gantt_rows_all(c: Client, ws: str, proj: str, vs: str, ve: str, extra: str = ""):
    """跨页收集视窗行 → (status, error_body, rows, meta)。"""
    base = ENDPOINTS["gantt_rows"].format(ws=q(ws), proj=proj)
    rows: list[dict] = []
    cursor = None
    meta: dict = {}
    for _ in range(12):
        url = f"{base}?viewport_start={vs}&viewport_end={ve}&per_page=100"
        if cursor:
            url += f"&cursor={q(cursor)}"
        code, body = c.req("GET", url + extra)
        if code != HTTP["OK"]:
            return code, body, [], meta
        rows.extend((body or {}).get("data", {}).get("rows") or [])
        meta = (body or {}).get("meta") or {}
        cursor = meta.get("next_cursor")
        if not cursor:
            break
    return HTTP["OK"], None, rows, meta


# ═══ 清理（幂等：S4FLOW-% 项目 + 本脚本用户前缀，单事务）═══

USER_PREFIXES = ("s4flow-%", "s4mem-%", "s4view-%", "s4out-%", "s4agg-%", "s4adm2-%")


def _cleanup_sql():
    users_sql = ("SELECT id FROM users WHERE email LIKE ANY (ARRAY["
                 + ",".join(f"'{p}'" for p in USER_PREFIXES) + "])")
    _pg_exec(f"""
    BEGIN;
    CREATE TEMP TABLE sp AS SELECT id FROM projects WHERE name LIKE 'S4FLOW-%';
    CREATE TEMP TABLE sa AS SELECT id FROM file_assets WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM file_share_accesses WHERE share_id IN
      (SELECT id FROM file_share_links WHERE asset_id IN (SELECT id FROM sa));
    DELETE FROM file_share_links WHERE asset_id IN (SELECT id FROM sa);
    DELETE FROM file_versions WHERE asset_id IN (SELECT id FROM sa);
    DELETE FROM upload_sessions WHERE asset_id IN (SELECT id FROM sa);
    DELETE FROM file_assets WHERE id IN (SELECT id FROM sa);
    DELETE FROM file_folders WHERE project_id IN (SELECT id FROM sp);
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
    DELETE FROM issues WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM issue_views WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM custom_field_definitions WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM labels WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM states WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM project_members WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM project_favorites WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM projects WHERE id IN (SELECT id FROM sp);
    DELETE FROM issue_types WHERE workspace_id IN
      (SELECT id FROM workspaces WHERE created_by_id IN ({users_sql}));
    DELETE FROM workspace_member_invites WHERE workspace_id IN
      (SELECT id FROM workspaces WHERE created_by_id IN ({users_sql}));
    DELETE FROM workspace_members WHERE workspace_id IN
      (SELECT id FROM workspaces WHERE created_by_id IN ({users_sql}));
    DELETE FROM workspaces WHERE created_by_id IN ({users_sql});
    DELETE FROM system_admins WHERE user_id IN ({users_sql});
    DELETE FROM users WHERE id IN ({users_sql});
    DROP TABLE sp; DROP TABLE sa;
    COMMIT;
    """)


def cleanup():
    print("\n═══ 清理自建数据（S4FLOW-% 项目 / 本脚本用户前缀）═══")
    try:
        _cleanup_sql()
        left_p = _pg_val("SELECT count(*) FROM projects WHERE name LIKE 'S4FLOW-%'")
        left_a = _pg_val("SELECT count(*) FROM file_assets WHERE attributes->>'name' LIKE 'S4FL-%'")
        left_all_u = _pg_val(
            "SELECT count(*) FROM users WHERE email LIKE ANY (ARRAY["
            + ",".join(f"'{p}@rabbit.dev'" for p in USER_PREFIXES) + "])")
        ck("CLEAN-1", "清理后库内无 S4FLOW-% 项目 / S4FL 文件 / 本脚本用户残留",
           left_p == "0" and left_a == "0" and left_all_u == "0",
           f"projects={left_p} assets={left_a} users={left_all_u}")
    except Exception as e:  # noqa: BLE001
        ck("CLEAN-1", "清理自建数据", False, f"{e}")


# ═══ 主流程 ═══

def main() -> int:
    _cleanup_sql()  # 幂等起步：清掉历史失败 run 的残留（sprint-2/3-bench 同纪律）
    admin = Client(BASE)
    admin_email, ws = signup(admin, "s4flow-")
    member = Client(BASE)
    member_email, _ = signup(member, "s4mem-")
    viewer = Client(BASE)
    viewer_email, _ = signup(viewer, "s4view-")
    outsider = Client(BASE)
    _out_email, out_ws = signup(outsider, "s4out-")
    c_agg = Client(BASE)      # GANTT-002 429 专用（throttle 配额独立）
    c_agg_email, _ = signup(c_agg, "s4agg-")
    admin2 = Client(BASE)     # 第二项目管理员（FILE-004 分享归属「ADMIN 直通」分支）
    admin2_email, _ = signup(admin2, "s4adm2-")

    for c, e in ((member, member_email), (viewer, viewer_email),
                 (c_agg, c_agg_email), (admin2, admin2_email)):
        invite_member(admin, ws, c, e)
    admin_id, member_id, viewer_id = uid_of(admin), uid_of(member), uid_of(viewer)

    file2_segment(admin, member, viewer, ws, admin_id, member_id, viewer_id)
    chunk_segment(admin, member, viewer, ws, admin_id, member_id, viewer_id)
    share_segment(admin, admin2, member, viewer, ws, admin_id, member_id, viewer_id)
    gantt1_segment(admin, member, viewer, ws, admin_id, member_id, viewer_id)
    gantt2_segment(admin, member, c_agg, ws, admin_id, member_id)
    realtime_segment(admin, member, viewer, ws, admin_id, member_id, viewer_id)
    isolation_segment(admin, viewer, outsider, out_ws, ws, admin_id)

    print(f"\n{'═' * 40}\nSprint 4 接口流程：{PASS} 通过 / {FAIL} 失败 / {SKIP} 跳过")
    cleanup()
    if FAILURES:
        print("\n".join("  ✗ " + f for f in FAILURES))
        return 1
    print("全部通过 ✓")
    return 0


# ═══ 1. FILE-002 项目文件库 ═══

def file2_segment(admin, member, viewer, ws, admin_id, member_id, viewer_id):
    section("FILE-002 项目文件库")
    proj = make_project(admin, ws, "S4FLOW-FILE", "S4F")
    folders = ENDPOINTS["folders"].format(ws=q(ws), proj=proj)
    join_project(admin, ws, proj, member_id, 15)
    join_project(admin, ws, proj, viewer_id, 5)
    # 配额基线先于本段任何上传读取（F2-59 的增量断言口径）
    base_used = admin.req("GET", ENDPOINTS["storage"].format(ws=q(ws), proj=proj))[1]["data"]["used_bytes"]

    # —— 目录 CRUD（BR-01/BR-04 / UT-01~04）——
    code, body = admin.req("POST", folders, {"name": "S4FL-设计稿"},
                           {"X-CSRFToken": admin.csrf()})
    d = (body or {}).get("data") or {}
    root1 = d.get("id")
    ck("F2-01", "新建根目录 → 201 + visibility=all + allowed_members=[]",
       code == HTTP["CREATED"] and d.get("visibility") == "all"
       and d.get("allowed_members") == [] and d.get("parent_id") is None, f"got {code} {d}")
    code, body = admin.req("POST", folders, {"name": "S4FL-设计稿"},
                           {"X-CSRFToken": admin.csrf()})
    ck("F2-02", "同层同名 → 409 RESOURCE_ALREADY_EXISTS + details UNIQUE",
       code == HTTP["CONFLICT"] and error_code(body) == CODES["alreadyExists"]
       and (detail_of(body, "name") or {}).get("code") == "UNIQUE", f"got {code} {body}")
    code, body = admin.req("POST", folders, {"name": "S4FL-设计稿", "parent_id": root1},
                           {"X-CSRFToken": admin.csrf()})
    ck("F2-03", "异层同名合法（唯一性按层）→ 201", code == HTTP["CREATED"], f"got {code}")
    sub1 = ((body or {}).get("data") or {}).get("id")
    code, body = admin.req("POST", folders, {"name": "S4FL-视频", "parent_id": sub1},
                           {"X-CSRFToken": admin.csrf()})
    sub2 = ((body or {}).get("data") or {}).get("id")

    cur, ok_chain = sub2, True
    for i in range(2):  # L4、L5
        code, body = admin.req("POST", folders, {"name": f"S4FL-L{i + 4}", "parent_id": cur},
                               {"X-CSRFToken": admin.csrf()})
        ok_chain = ok_chain and code == HTTP["CREATED"]
        cur = ((body or {}).get("data") or {}).get("id")
    ck("F2-04", "建满 5 层目录链（L4/L5 均 201）", ok_chain, "")
    code, body = admin.req("POST", folders, {"name": "S4FL-L6", "parent_id": cur},
                           {"X-CSRFToken": admin.csrf()})
    ck("F2-05", "第 6 层 → 409 RESOURCE_LIMIT_EXCEEDED + LIMIT（最多 5 层）",
       code == HTTP["CONFLICT"] and error_code(body) == CODES["limitExceeded"]
       and (detail_of(body, "parent_id") or {}).get("code") == "LIMIT", f"got {code} {body}")
    code, body = admin.req("PATCH", folders + f"{root1}/", {"parent_id": sub2},
                           {"X-CSRFToken": admin.csrf()})
    ck("F2-06", "祖先移入后代 → 409 RESOURCE_CIRCULAR_DEPENDENCY + CYCLE",
       code == HTTP["CONFLICT"] and error_code(body) == CODES["circular"]
       and (detail_of(body, "parent_id") or {}).get("code") == "CYCLE", f"got {code} {body}")
    code, body = admin.req("POST", folders, {"name": "S4FL-根二"},
                           {"X-CSRFToken": admin.csrf()})
    root2 = ((body or {}).get("data") or {}).get("id")
    admin.req("POST", folders, {"name": "S4FL-占位", "parent_id": root2},
              {"X-CSRFToken": admin.csrf()})
    code, body = admin.req("PATCH", folders + f"{sub2}/", {"parent_id": root2},
                           {"X-CSRFToken": admin.csrf()})
    ck("F2-07", "移动目录（sub2 → 根二下）→ 200 parent_id 更新（纯元数据）",
       code == HTTP["OK"] and (body or {}).get("data", {}).get("parent_id") == root2,
       f"got {code}")
    code, body = admin.req("PATCH", folders + f"{sub2}/", {"name": "S4FL-视频改名"},
                           {"X-CSRFToken": admin.csrf()})
    ck("F2-08", "PATCH 目录改名 → 200",
       code == HTTP["OK"] and (body or {}).get("data", {}).get("name") == "S4FL-视频改名",
       f"got {code}")
    code, body = admin.req("PATCH", folders + f"{sub2}/", {"name": "S4FL-占位"},
                           {"X-CSRFToken": admin.csrf()})
    ck("F2-09", "改名撞同层既有名 → 409 UNIQUE",
       code == HTTP["CONFLICT"] and error_code(body) == CODES["alreadyExists"], f"got {code}")

    # —— presign → PUT → complete（IT-01/IT-02 / UT-15）——
    presign = ENDPOINTS["folder_presign"].format(ws=q(ws), proj=proj, folder=root1)
    code, body = admin.req("POST", presign, {"file_name": "S4FL-big.png",
                                             "file_size": 51 * 1024 * 1024,
                                             "content_type": "image/png"},
                           {"X-CSRFToken": admin.csrf()})
    ck("F2-10", "presign >50MB → 400 FILE_SIZE_EXCEEDED（TOO_LARGE）",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["fileSize"]
       and (detail_of(body, "file_size") or {}).get("code") == "TOO_LARGE", f"got {code} {body}")
    code, body = admin.req("POST", presign, {"file_name": "tool.exe", "file_size": 1024,
                                             "content_type": "application/octet-stream"},
                           {"X-CSRFToken": admin.csrf()})
    ck("F2-11", "presign 白名单外扩展名 → 400 FILE_TYPE_NOT_ALLOWED",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["fileType"], f"got {code} {body}")
    png_size = len(PNG_1PX)
    code, body = admin.req("POST", presign, {"file_name": "S4FL-banner.png", "file_size": png_size,
                                             "content_type": "image/png"},
                           {"X-CSRFToken": admin.csrf()})
    d = (body or {}).get("data") or {}
    png_asset = d.get("asset_id")
    ck("F2-12", "presign → 201 {asset_id, upload_url(/uploads/), fields, expires_in}",
       code == HTTP["CREATED"] and bool(png_asset)
       and (d.get("upload_url") or "").startswith("/uploads/")
       and "/project_file/" in (d.get("upload_url") or "")
       and isinstance(d.get("expires_in"), int), f"got {code} {d}")

    code, body = admin.req("POST", ENDPOINTS["file_complete"].format(ws=q(ws), proj=proj, asset=png_asset),
                           {}, {"X-CSRFToken": admin.csrf()})
    ck("F2-13", "对象未上传先 complete → 400 VALIDATION_FILE_UPLOAD_MISMATCH（DOES_NOT_EXIST）",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["uploadMismatch"]
       and (detail_of(body, "asset") or {}).get("code") == "DOES_NOT_EXIST", f"got {code} {body}")
    code2, body2 = admin.req("POST", presign, {"file_name": "S4FL-half.png", "file_size": png_size,
                                               "content_type": "image/png"},
                             {"X-CSRFToken": admin.csrf()})
    half_asset = ((body2 or {}).get("data") or {}).get("asset_id")
    minio_put(body2["data"]["upload_url"], PNG_1PX[: png_size // 2], "image/png")  # 半量
    code, body = admin.req("POST", ENDPOINTS["file_complete"].format(ws=q(ws), proj=proj, asset=half_asset),
                           {}, {"X-CSRFToken": admin.csrf()})
    ck("F2-14", "PUT 半量后 complete → 400（HEAD 大小不匹配，IT-02）",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["uploadMismatch"], f"got {code}")

    ok, _ = minio_put(d["upload_url"], PNG_1PX, "image/png")
    ck("F2-15", "MinIO 直传（PUT 预签名小文件）→ 2xx", ok, "直传失败")
    code, body = admin.req("POST", ENDPOINTS["file_complete"].format(ws=q(ws), proj=proj, asset=png_asset),
                           {}, {"X-CSRFToken": admin.csrf()})
    d = (body or {}).get("data") or {}
    ck("F2-16", "complete → 200 status=uploaded + type_category=image",
       code == HTTP["OK"] and d.get("status") == "uploaded"
       and d.get("type_category") == "image" and d.get("folder_id") == root1, f"got {code} {d}")

    # —— 列表 / 筛选 / 游标（§4.2.1）——
    txt_id, _ = upload_direct(admin, ws, proj, root1, "S4FL-notes.txt", b"hello sprint4", "text/plain")
    zip_id, _ = upload_direct(admin, ws, proj, root1, "S4FL-pack.zip", b"PK\x03\x04fake", "application/zip")
    files = ENDPOINTS["folder_files"].format(ws=q(ws), proj=proj, folder=root1)
    code, body = admin.req("GET", files)
    rows = (body or {}).get("data") or []
    meta = (body or {}).get("meta") or {}
    names = [r["name"] for r in rows]
    ck("F2-17", "目录列表含三类文件 + meta 十字段（含 total_size_bytes）",
       code == HTTP["OK"] and set(names) >= {"S4FL-banner.png", "S4FL-notes.txt", "S4FL-pack.zip"}
       and "total_size_bytes" in meta and {"next_cursor", "total_count", "per_page"} <= set(meta),
       f"got {code} names={names}")
    ck("F2-18", "total_size_bytes ≥ 可见文件体积和",
       meta.get("total_size_bytes", 0) >= png_size + 13, f"meta={meta.get('total_size_bytes')}")
    code, body = admin.req("GET", files + "?type=image")
    ck("F2-19", "?type=image 筛选 → 仅 png",
       {r["name"] for r in (body or {}).get("data") or []} == {"S4FL-banner.png"},
       f"{[r['name'] for r in (body or {}).get('data') or []]}")
    code, body = admin.req("GET", files + "?type=document")
    ck("F2-20", "?type=document → 仅 txt",
       {r["name"] for r in (body or {}).get("data") or []} == {"S4FL-notes.txt"}, "")
    code, body = admin.req("GET", files + "?name=NOTES")
    ck("F2-21", "?name= 大小写不敏感 icontains → txt",
       {r["name"] for r in (body or {}).get("data") or []} == {"S4FL-notes.txt"}, "")
    code, body = admin.req("GET", files + f"?uploaded_by={admin_id}&type=archive")
    ck("F2-22", "?uploaded_by= × type= 组合恒 AND → zip",
       {r["name"] for r in (body or {}).get("data") or []} == {"S4FL-pack.zip"}, "")
    code, body = admin.req("GET", files + "?per_page=1")
    meta = (body or {}).get("meta") or {}
    cur1 = meta.get("next_cursor")
    ck("F2-23", "游标分页 per_page=1 → 首页 1 行 + next_cursor",
       len((body or {}).get("data") or []) == 1 and bool(cur1)
       and meta.get("next_page_results") is True, f"meta={meta}")
    code, body = admin.req("GET", files + f"?per_page=1&cursor={q(cur1)}")
    ck("F2-24", "次页不重复首页行（prev/next 链）",
       code == HTTP["OK"] and len((body or {}).get("data") or []) == 1
       and (body or {}).get("data")[0]["id"] != png_asset
       and ((body or {}).get("meta") or {}).get("prev_cursor"), f"got {code}")
    code, body = admin.req("GET", files + "?cursor=!!!broken")
    ck("F2-25", "损坏游标 → 400 VALIDATION_INVALID_CURSOR",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["invalidCursor"], f"got {code}")
    code, body = admin.req("GET", files + "?expand=uploaded_by")
    ck("F2-26", "?expand=uploaded_by → 追加 uploaded_by_detail 对象形态",
       all("uploaded_by_detail" in r and r["uploaded_by_detail"]["id"] == admin_id
           for r in (body or {}).get("data") or []), "")

    # —— 重命名 / 移动 / 双挂 / R1（BR-02 / UT-19 / UT-11）——
    fdetail = ENDPOINTS["file_detail"].format(ws=q(ws), proj=proj, asset=txt_id)
    code, body = admin.req("PATCH", fdetail, {"name": "S4FL-notes-v2.txt"},
                           {"X-CSRFToken": admin.csrf()})
    ck("F2-27", "文件重命名 → 200 回显新名",
       code == HTTP["OK"] and (body or {}).get("data", {}).get("name") == "S4FL-notes-v2.txt",
       f"got {code}")
    code, body = admin.req("PATCH", fdetail, {"folder_id": root2},
                           {"X-CSRFToken": admin.csrf()})
    ck("F2-28", "移动文件到根二 → 200 folder_id 更新",
       code == HTTP["OK"] and (body or {}).get("data", {}).get("folder_id") == root2, f"got {code}")
    issue = make_issue(admin, ws, proj, "S4F-双挂任务")
    code, body = admin.req("PATCH", fdetail, {"issue_id": issue["id"]},
                           {"X-CSRFToken": admin.csrf()})
    ck("F2-29", "双挂：PATCH issue_id → 200 issue_id 落库（第二视图入口）",
       code == HTTP["OK"] and (body or {}).get("data", {}).get("issue_id") == issue["id"],
       f"got {code}")
    code, body = admin.req("PATCH", fdetail, {"issue_id": None},
                           {"X-CSRFToken": admin.csrf()})
    ck("F2-30", "解除双挂（null）→ 200 且对象与文件库行无损（UT-11）",
       code == HTTP["OK"] and (body or {}).get("data", {}).get("issue_id") is None, f"got {code}")

    mem_asset, mem_row = upload_direct(member, ws, proj, root1, "S4FL-mem.txt",
                                       b"member own file", "text/plain")
    ck("F2-31", "前置：CONTRIBUTOR 上传本人文件（file.upload）",
       bool(mem_asset) and mem_row.get("status") == "uploaded", str(mem_row)[:160])
    code, body = member.req("PATCH", fdetail, {"name": "S4FL-越权改名.txt"},
                            {"X-CSRFToken": member.csrf()})
    ck("F2-32", "R1：CONTRIBUTOR PATCH 他人上传文件 → 403 PERM_DENIED",
       code == HTTP["FORBIDDEN"] and error_code(body) == CODES["permDenied"], f"got {code} {body}")
    code, body = member.req("PATCH", ENDPOINTS["file_detail"].format(ws=q(ws), proj=proj, asset=mem_asset),
                            {"name": "S4FL-mem-v2.txt"}, {"X-CSRFToken": member.csrf()})
    ck("F2-33", "R1：CONTRIBUTOR PATCH 本人上传 → 200",
       code == HTTP["OK"] and (body or {}).get("data", {}).get("name") == "S4FL-mem-v2.txt",
       f"got {code}")
    code, body = member.req("PATCH", fdetail,
                            {"visibility": "members", "allowed_members": [member_id]},
                            {"X-CSRFToken": member.csrf()})
    ck("F2-34", "可见性配置仅 ADMIN（file.permission.manage）→ 403 PERM_DENIED",
       code == HTTP["FORBIDDEN"] and error_code(body) == CODES["permDenied"], f"got {code}")

    # —— 三态可见性（UT-06~09 / IT-05：列表 / 目录树 / download-url 三路一致）——
    v_all = admin.req("POST", folders, {"name": "S4FL-全可见"},
                      {"X-CSRFToken": admin.csrf()})[1]["data"]["id"]
    v_adm = admin.req("POST", folders, {"name": "S4FL-仅管理员"},
                      {"X-CSRFToken": admin.csrf()})[1]["data"]["id"]
    v_mem = admin.req("POST", folders, {"name": "S4FL-指定成员"},
                      {"X-CSRFToken": admin.csrf()})[1]["data"]["id"]
    admin.req("PATCH", folders + f"{v_adm}/", {"visibility": "admins"},
              {"X-CSRFToken": admin.csrf()})
    admin.req("PATCH", folders + f"{v_mem}/",
              {"visibility": "members", "allowed_members": [member_id]},
              {"X-CSRFToken": admin.csrf()})
    fv_all, _ = upload_direct(admin, ws, proj, v_all, "S4FL-open.txt", b"open", "text/plain")
    fv_adm, _ = upload_direct(admin, ws, proj, v_adm, "S4FL-secret.txt", b"secret", "text/plain")
    fv_mem, _ = upload_direct(admin, ws, proj, v_mem, "S4FL-member.txt", b"member", "text/plain")
    ck("F2-35", "前置：三目录三文件就位", all([fv_all, fv_adm, fv_mem]), "")

    code, body = viewer.req("GET", folders)
    vnames = {r["name"] for r in (body or {}).get("data") or []}
    ck("F2-36", "VIEWER 目录树：admins/members 态目录整支不呈现",
       code == HTTP["OK"] and "S4FL-全可见" in vnames
       and "S4FL-仅管理员" not in vnames and "S4FL-指定成员" not in vnames, f"{sorted(vnames)}")
    code, _ = viewer.req("GET", ENDPOINTS["folder_files"].format(ws=q(ws), proj=proj, folder=v_adm))
    ck("F2-37", "VIEWER 直连 admins 态目录文件列表 → 404（存在性隐藏）",
       code == HTTP["NOT_FOUND"], f"got {code}")
    code, _ = viewer.req("GET", ENDPOINTS["file_download"].format(ws=q(ws), proj=proj, asset=fv_adm))
    ck("F2-38", "VIEWER 直连 admins 态文件 download-url → 404（三路一致）",
       code == HTTP["NOT_FOUND"], f"got {code}")
    code, body = member.req("GET", folders)
    vnames = {r["name"] for r in (body or {}).get("data") or []}
    ck("F2-39", "CONTRIBUTOR（指定成员）树：members 态可见、admins 态不可见",
       "S4FL-指定成员" in vnames and "S4FL-仅管理员" not in vnames, f"{sorted(vnames)}")
    code, body = member.req("GET", ENDPOINTS["folder_files"].format(ws=q(ws), proj=proj, folder=v_mem))
    ck("F2-40", "members 态目录内文件对指定成员可见",
       code == HTTP["OK"] and any(r["id"] == fv_mem for r in (body or {}).get("data") or []),
       f"got {code}")
    code, body = admin.req("GET", folders)
    tree = {r["name"]: r for r in (body or {}).get("data") or []}
    ck("F2-41", "ADMIN 树：三态目录全可见 + file_count 计数",
       "S4FL-全可见" in tree and "S4FL-仅管理员" in tree and "S4FL-指定成员" in tree
       and tree.get("S4FL-全可见", {}).get("file_count") == 1, f"{sorted(tree)}")
    code, body = viewer.req("GET", ENDPOINTS["file_download"].format(ws=q(ws), proj=proj, asset=fv_all))
    d = (body or {}).get("data") or {}
    ck("F2-42", "VIEWER download-url 全可见文件 → 200 {download_url, expires_in=300}",
       code == HTTP["OK"] and (d.get("download_url") or "").startswith("/uploads/")
       and d.get("expires_in") == 300, f"got {code} {d}")

    # —— 软删 / 回收站（R1 同键）/ 还原（(恢复)）/ purge（引用计数）——
    code, _ = admin.req("DELETE", ENDPOINTS["file_detail"].format(ws=q(ws), proj=proj, asset=zip_id),
                        None, {"X-CSRFToken": admin.csrf()})
    ck("F2-43", "软删文件 → 204（对象不动）", code == HTTP["NO_CONTENT"], f"got {code}")
    code, _ = member.req("DELETE", ENDPOINTS["file_detail"].format(ws=q(ws), proj=proj, asset=mem_asset),
                         None, {"X-CSRFToken": member.csrf()})
    ck("F2-44", "R1：CONTRIBUTOR 软删本人文件 → 204", code == HTTP["NO_CONTENT"], f"got {code}")
    code, body = member.req("GET", ENDPOINTS["trash"].format(ws=q(ws), proj=proj))
    mrows = (body or {}).get("data") or []
    ck("F2-45", "回收站 R1 过滤：CONTRIBUTOR 仅见本人删除项（含 deleted_at 字段）",
       code == HTTP["OK"] and {r["id"] for r in mrows} == {mem_asset}
       and all(r.get("deleted_at") for r in mrows), f"{[r.get('name') for r in mrows]}")
    code, body = admin.req("GET", ENDPOINTS["trash"].format(ws=q(ws), proj=proj))
    arows = (body or {}).get("data") or []
    ck("F2-46", "回收站 ADMIN 全量（zip + member 的行均可见）",
       {r["id"] for r in arows} >= {zip_id, mem_asset}, f"{len(arows)} 行")
    code, body = member.req("POST", ENDPOINTS["file_restore"].format(ws=q(ws), proj=proj, asset=mem_asset),
                            {}, {"X-CSRFToken": member.csrf()})
    ck("F2-47", "还原本人删除项 → 200 回原目录（R1 同码同键）",
       code == HTTP["OK"] and (body or {}).get("data", {}).get("folder_id") == root1, f"got {code}")
    code, body = member.req("POST", ENDPOINTS["file_restore"].format(ws=q(ws), proj=proj, asset=zip_id),
                            {}, {"X-CSRFToken": member.csrf()})
    ck("F2-48", "R1：CONTRIBUTOR 还原他人删除项 → 403 PERM_DENIED",
       code == HTTP["FORBIDDEN"] and error_code(body) == CODES["permDenied"], f"got {code}")
    code, body = admin.req("POST", ENDPOINTS["file_restore"].format(ws=q(ws), proj=proj, asset=zip_id),
                           {}, {"X-CSRFToken": admin.csrf()})
    ck("F2-49", "ADMIN 还原他人删除项 → 200（R1 全量口径）",
       code == HTTP["OK"] and (body or {}).get("data", {}).get("folder_id") == root1, f"got {code}")
    code, body = admin.req("POST", ENDPOINTS["file_restore"].format(ws=q(ws), proj=proj, asset=zip_id),
                           {}, {"X-CSRFToken": admin.csrf()})
    ck("F2-50", "重复还原（不在回收站）→ 409 RESOURCE_STATE_INVALID",
       code == HTTP["CONFLICT"] and error_code(body) == CODES["stateInvalid"], f"got {code} {body}")

    # 还原冲突落根 + (恢复) 后缀（BR-07/UT-13：目录整树恢复语义）
    cf = admin.req("POST", folders, {"name": "S4FL-冲突目录"},
                   {"X-CSRFToken": admin.csrf()})[1]["data"]["id"]
    cf_file, _ = upload_direct(admin, ws, proj, cf, "S4FL-in-conflict.txt", b"x", "text/plain")
    admin.req("DELETE", folders + f"{cf}/", None, {"X-CSRFToken": admin.csrf()})
    code, body = admin.req("POST", folders, {"name": "S4FL-冲突目录"},
                           {"X-CSRFToken": admin.csrf()})
    ck("F2-51", "前置：删目录树后原位重建同名目录 → 201", code == HTTP["CREATED"], f"got {code}")
    code, body = admin.req("POST", ENDPOINTS["file_restore"].format(ws=q(ws), proj=proj, asset=cf_file),
                           {}, {"X-CSRFToken": admin.csrf()})
    ck("F2-52", "还原触发父目录整树恢复：原位同名冲突 → 落根 + (恢复) 后缀",
       code == HTTP["OK"], f"got {code} {body}")
    code, body = admin.req("GET", folders)
    fnames = {r["name"] for r in (body or {}).get("data") or []}
    ck("F2-53", "树中出现「S4FL-冲突目录(恢复)」根目录（新旧行并存）",
       "S4FL-冲突目录(恢复)" in fnames and "S4FL-冲突目录" in fnames, f"{sorted(fnames)}")

    # purge：引用计数（BR-06/UT-17）——同键双行，删一行对象保留
    code, body = member.req("DELETE", ENDPOINTS["file_purge"].format(ws=q(ws), proj=proj, asset=mem_asset),
                            None, {"X-CSRFToken": member.csrf()})
    ck("F2-54", "purge 仅 PROJ_ADMIN → CONTRIBUTOR 403 PERM_DENIED",
       code == HTTP["FORBIDDEN"] and error_code(body) == CODES["permDenied"], f"got {code} {body}")
    ref_a, _ = upload_direct(admin, ws, proj, v_all, "S4FL-ref-a.txt", b"shared-key-body", "text/plain")
    key_path = _pg_val(f"SELECT storage_path FROM file_assets WHERE id = '{ref_a}'")
    dup_id = str(uuid_mod.uuid4())
    _pg_exec(
        "INSERT INTO file_assets (id, workspace_id, project_id, entity_type, entity_id, "
        " folder_id, attributes, size, storage_path, status, is_uploaded, uploaded_by_id, "
        " visibility, allowed_members, download_count, created_by_id, updated_by_id, "
        " created_at, updated_at) "
        "VALUES (%s, (SELECT workspace_id FROM projects WHERE id=%s), %s, 'project_file', "
        " %s, %s, %s::jsonb, 15, %s, 'uploaded', true, %s, 'all', '[]'::jsonb, 0, "
        " %s, %s, now(), now())",
        (dup_id, proj, proj, v_all, v_all,
         json.dumps({"name": "S4FL-ref-b.txt", "size": 15, "mime": "text/plain", "ext": ".txt"}),
         key_path, admin_id, admin_id, admin_id))
    admin.req("DELETE", ENDPOINTS["file_detail"].format(ws=q(ws), proj=proj, asset=dup_id),
              None, {"X-CSRFToken": admin.csrf()})
    code, body = admin.req("DELETE", ENDPOINTS["file_purge"].format(ws=q(ws), proj=proj, asset=dup_id),
                           None, {"X-CSRFToken": admin.csrf()})
    ck("F2-55", "purge 同键第二行 → 200 {purged:true}（行硬删）",
       code == HTTP["OK"] and (body or {}).get("data", {}).get("purged") is True, f"got {code} {body}")
    code, body = admin.req("GET", ENDPOINTS["file_download"].format(ws=q(ws), proj=proj, asset=ref_a))
    ck("F2-56", "BR-06 引用计数：仍被引用的对象保留（ref_a 可签发下载）",
       code == HTTP["OK"] and ((body or {}).get("data") or {}).get("download_url"), f"got {code}")

    # —— 配额（§4.2.3 四字段；BR-03 409 分支以 SQL 造临界行——环境级配额不可配小值）——
    code, body = admin.req("GET", ENDPOINTS["storage"].format(ws=q(ws), proj=proj))
    d = (body or {}).get("data") or {}
    ck("F2-57", "storage 端点四字段 {quota_bytes, used_bytes, pending_bytes, usage_ratio}",
       code == HTTP["OK"] and {"quota_bytes", "used_bytes", "pending_bytes", "usage_ratio"} == set(d)
       and isinstance(d.get("quota_bytes"), int) and d.get("quota_bytes") >= 10 * 1024 ** 3,
       f"got {code} {d}")
    ws_id = _pg_val(f"SELECT workspace_id FROM projects WHERE id='{proj}'")
    hog = str(uuid_mod.uuid4())
    hog_size = int(d.get("quota_bytes") or 10 * 1024 ** 3) - 1024
    try:
        _pg_exec(
            "INSERT INTO file_assets (id, workspace_id, project_id, entity_type, entity_id, "
            " attributes, size, storage_path, status, is_uploaded, uploaded_by_id, visibility, "
            " allowed_members, download_count, created_by_id, updated_by_id, created_at, updated_at) "
            "VALUES (%s, %s, %s, 'project_file', %s, %s::jsonb, %s, %s, 'uploaded', true, "
            " %s, 'all', '[]'::jsonb, 0, %s, %s, now(), now())",
            (hog, ws_id, proj, v_all,
             json.dumps({"name": "S4FL-quota-hog.bin", "size": hog_size, "mime": "", "ext": ".bin"}),
             str(hog_size), f"s4flow-quota-hog/{hog}", admin_id, admin_id, admin_id))
        code, body = admin.req("POST", presign,
                               {"file_name": "S4FL-over-quota.png", "file_size": 4096,
                                "content_type": "image/png"},
                               {"X-CSRFToken": admin.csrf()})
        ck("F2-58", "配额临界 → 409 QUOTA_STORAGE_EXCEEDED + details 子码 QUOTA",
           code == HTTP["CONFLICT"] and error_code(body) == CODES["quotaStorage"]
           and (detail_of(body, "file_size") or {}).get("code") == "QUOTA", f"got {code} {body}")
    finally:
        _pg_exec(f"DELETE FROM file_assets WHERE id = '{hog}'")
    code, body = admin.req("GET", ENDPOINTS["storage"].format(ws=q(ws), proj=proj))
    d2 = (body or {}).get("data") or {}
    ck("F2-59", "used_bytes 随真实上传增长（hog 行已清，增量来自本段文件）",
       d2.get("used_bytes", 0) > (base_used or 0), f"{base_used} → {d2.get('used_bytes')}")
    code, _ = member.req("GET", ENDPOINTS["storage"].format(ws=q(ws), proj=proj))
    ck("F2-60", "storage 端点 file.read（VIEWER+）可读", code == HTTP["OK"], f"got {code}")

    # 目录级联软删回传文件数（UT-14）
    cascade = admin.req("POST", folders, {"name": "S4FL-级联"},
                        {"X-CSRFToken": admin.csrf()})[1]["data"]["id"]
    for i in range(3):
        upload_direct(admin, ws, proj, cascade, f"S4FL-casc-{i}.txt", b"c", "text/plain")
    code, body = admin.req("DELETE", folders + f"{cascade}/", None, {"X-CSRFToken": admin.csrf()})
    d = (body or {}).get("data") or {}
    ck("F2-61", "DELETE 目录整树软删 → 200 {folders_deleted:1, files_deleted:3}（UT-14）",
       code == HTTP["OK"] and d.get("folders_deleted") == 1 and d.get("files_deleted") == 3,
       f"got {code} {d}")
    code, _ = admin.req("GET", folders + f"{cascade}/files/")
    ck("F2-62", "软删目录的文件列表 → 404", code == HTTP["NOT_FOUND"], f"got {code}")


# ═══ 2. FILE-003 分片会话与版本 ═══

def chunk_segment(admin, member, viewer, ws, admin_id, member_id, viewer_id):
    section("FILE-003 分片会话与版本")
    proj = make_project(admin, ws, "S4FLOW-CHUNK", "S4C")
    join_project(admin, ws, proj, member_id, 15)
    join_project(admin, ws, proj, viewer_id, 5)
    folders = ENDPOINTS["folders"].format(ws=q(ws), proj=proj)
    sessions = ENDPOINTS["upload_sessions"].format(ws=q(ws), proj=proj)
    session_detail = ENDPOINTS["upload_session_detail"].format(ws=q(ws), proj=proj, session="{}")
    session_chunk = ENDPOINTS["upload_session_chunk"].format(ws=q(ws), proj=proj, session="{}", n="{}")
    session_complete = ENDPOINTS["upload_session_complete"].format(ws=q(ws), proj=proj, session="{}")
    folder = admin.req("POST", folders, {"name": "S4FL-大文件"},
                       {"X-CSRFToken": admin.csrf()})[1]["data"]["id"]

    # —— init（§2.6 边界 / BR-15）——
    code, body = admin.req("POST", sessions, {"file_name": "S4FL-tool.exe", "file_size": 1024,
                                              "folder_id": folder},
                           {"X-CSRFToken": admin.csrf()})
    ck("F3-01", "init 白名单外（.exe）→ 400 FILE_TYPE_NOT_ALLOWED",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["fileType"], f"got {code} {body}")
    code, body = admin.req("POST", sessions, {"file_name": "S4FL-huge.mp4",
                                              "file_size": 5 * 1024 ** 3 + 1, "folder_id": folder},
                           {"X-CSRFToken": admin.csrf()})
    ck("F3-02", "init >5GB → 400 FILE_SIZE_EXCEEDED",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["fileSize"], f"got {code}")
    size_2c = 9 * 1024 * 1024  # 9MB → 2 片
    code, body = admin.req("POST", sessions, {"file_name": "S4FL-multipart.zip",
                                              "file_size": size_2c, "folder_id": folder,
                                              "content_type": "application/zip",
                                              "content_md5": "0" * 32},
                           {"X-CSRFToken": admin.csrf()})
    d = (body or {}).get("data") or {}
    sid = d.get("session_id")
    ck("F3-03", "init 9MB → 201 {total_chunks:2, chunk_size:8MB, uploaded_chunks:[]}",
       code == HTTP["CREATED"] and d.get("total_chunks") == 2
       and d.get("chunk_size") == 8 * 1024 * 1024 and d.get("uploaded_chunks") == []
       and d.get("status") == "uploading" and d.get("asset_id"), f"got {code} {d}")

    # 活动会话上限 3（§2.6）
    extra_sids = []
    for i in range(2):
        code, body = admin.req("POST", sessions, {"file_name": f"S4FL-fill-{i}.zip",
                                                  "file_size": 1024, "folder_id": folder},
                               {"X-CSRFToken": admin.csrf()})
        extra_sids.append(((body or {}).get("data") or {}).get("session_id"))
    code, body = admin.req("POST", sessions, {"file_name": "S4FL-fill-3.zip", "file_size": 1024,
                                              "folder_id": folder},
                           {"X-CSRFToken": admin.csrf()})
    ck("F3-04", "活动会话第 4 个 → 409 RESOURCE_LIMIT_EXCEEDED + LIMIT（3/用户）",
       code == HTTP["CONFLICT"] and error_code(body) == CODES["limitExceeded"]
       and (detail_of(body, "session") or {}).get("code") == "LIMIT", f"got {code} {body}")
    for s in extra_sids:
        admin.req("DELETE", session_detail.format(s), None, {"X-CSRFToken": admin.csrf()})

    # 断点续传：同名在途二次 init → 409（BR-15）
    code, body = admin.req("POST", sessions, {"file_name": "S4FL-multipart.zip",
                                              "file_size": size_2c, "folder_id": folder},
                           {"X-CSRFToken": admin.csrf()})
    ck("F3-05", "同名在途会话二次 init → 409 UNIQUE（复用断点续传）",
       code == HTTP["CONFLICT"] and error_code(body) == CODES["alreadyExists"]
       and (detail_of(body, "file_name") or {}).get("code") == "UNIQUE", f"got {code} {body}")

    # —— chunk 换发 + 登记（BR-02/BR-03）——
    code, body = admin.req("POST", session_chunk.format(sid, 0), {}, {"X-CSRFToken": admin.csrf()})
    ck("F3-06", "片号越界（0）→ 400 VALIDATION_ERROR（chunk INVALID）",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["validation"]
       and (detail_of(body, "chunk") or {}).get("code") == "INVALID", f"got {code} {body}")
    code, body = admin.req("POST", session_chunk.format(sid, 1), {}, {"X-CSRFToken": admin.csrf()})
    d = (body or {}).get("data") or {}
    ck("F3-07", "换发片预签名 → 200 {part_number:1, upload_url, expires_in:1800}",
       code == HTTP["OK"] and d.get("part_number") == 1
       and (d.get("upload_url") or "").startswith("/uploads/") and d.get("expires_in") == 1800,
       f"got {code} {d}")
    chunk1 = b"\x11" * (8 * 1024 * 1024)
    ok1, etag1 = minio_put(d["upload_url"], chunk1, "application/octet-stream")
    ck("F3-08", "MinIO UploadPart 直传片 1（8MB）→ 2xx + ETag", ok1 and bool(etag1), "直传失败")
    code, body = admin.req("PATCH", session_chunk.format(sid, 1), {"etag": etag1, "md5": "f" * 32},
                           {"X-CSRFToken": admin.csrf()})
    ck("F3-09", "MD5 与 ETag 不符 → 400（篡改片服务端拒记，BR-03）",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["validation"], f"got {code} {body}")
    md5_1 = hashlib.md5(chunk1).hexdigest()
    code, body = admin.req("PATCH", session_chunk.format(sid, 1), {"etag": etag1, "md5": md5_1},
                           {"X-CSRFToken": admin.csrf()})
    ck("F3-10", "登记片 1（ETag+MD5 符）→ 200 uploaded_chunks=[1]",
       code == HTTP["OK"] and (body or {}).get("data", {}).get("uploaded_chunks") == [1],
       f"got {code} {body}")
    code, body = admin.req("GET", session_detail.format(sid))
    ck("F3-11", "会话状态断点片表 uploaded_chunks=[1]（续传基线）",
       code == HTTP["OK"] and (body or {}).get("data", {}).get("uploaded_chunks") == [1],
       f"got {code}")
    code, _ = member.req("GET", session_detail.format(sid))
    ck("F3-12", "他人窥探会话片表 → 403 PERM_DENIED（BR-16 #2 对象级属主）",
       code == HTTP["FORBIDDEN"], f"got {code}")

    # complete 缺片（BR-04 片级核对）
    code, body = admin.req("POST", session_complete.format(sid), {}, {"X-CSRFToken": admin.csrf()})
    det = ((body or {}).get("error") or {}).get("details") or []
    ck("F3-13", "缺片 complete → 400 VALIDATION_FILE_UPLOAD_MISMATCH + chunks MISSING [2]",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["uploadMismatch"]
       and any(x.get("field") == "chunks" and x.get("code") == "MISSING" and "2" in str(x.get("message"))
               for x in det), f"got {code} {det}")

    chunk2 = b"\x22" * (1 * 1024 * 1024)
    code, body = admin.req("POST", session_chunk.format(sid, 2), {}, {"X-CSRFToken": admin.csrf()})
    ok2, etag2 = minio_put(body["data"]["upload_url"], chunk2, "application/octet-stream")
    code, body = admin.req("PATCH", session_chunk.format(sid, 2), {"etag": etag2},
                           {"X-CSRFToken": admin.csrf()})
    ck("F3-14", "补传片 2（末片 1MB）+ 登记 → uploaded_chunks=[1,2]",
       ok2 and (body or {}).get("data", {}).get("uploaded_chunks") == [1, 2],
       f"ok2={ok2} {body}")
    code, body = admin.req("POST", session_complete.format(sid), {}, {"X-CSRFToken": admin.csrf()})
    d = (body or {}).get("data") or {}
    mp_asset = (d.get("file") or {}).get("id")
    ck("F3-15", "complete 合并落库 → 201 {file.status=uploaded, version{number:1,current}}",
       code == HTTP["CREATED"] and (d.get("file") or {}).get("status") == "uploaded"
       and (d.get("version") or {}).get("version_number") == 1
       and (d.get("version") or {}).get("is_current") is True, f"got {code} {str(d)[:200]}")
    code, body = admin.req("GET", session_detail.format(sid))
    code2, _ = admin.req("POST", session_complete.format(sid), {}, {"X-CSRFToken": admin.csrf()})
    ck("F3-16", "会话态收口 completed；重复 complete → 409 RESOURCE_STATE_INVALID",
       (body or {}).get("data", {}).get("status") == "completed"
       and code2 == HTTP["CONFLICT"], f"status={((body or {}).get('data') or {}).get('status')}")

    # —— 同名直传并入版本链（§4.3.4）+ 回滚 + 内容 302 ——
    same_id, same_row = upload_direct(admin, ws, proj, folder, "S4FL-multipart.zip",
                                      b"direct v2 body", "application/zip")
    ck("F3-17", "同名直传 complete 并入既有行（asset_id 不变，v2）",
       bool(same_id) and same_id == mp_asset and same_row.get("name") == "S4FL-multipart.zip",
       f"{same_id} vs {mp_asset} name={same_row.get('name')!r}")
    versions = ENDPOINTS["file_versions"].format(ws=q(ws), proj=proj, asset=mp_asset)
    code, body = admin.req("GET", versions)
    vrows = (body or {}).get("data") or []
    ck("F3-18", "版本列表新→旧 [v2(current), v1] + source 指针空",
       code == HTTP["OK"] and [v["version_number"] for v in vrows] == [2, 1]
       and vrows[0]["is_current"] is True and vrows[0]["source_version_id"] is None,
       f"{[v['version_number'] for v in vrows]}")
    v1 = next(v for v in vrows if v["version_number"] == 1)
    code, body = admin.req("POST", ENDPOINTS["file_version_rollback"].format(
        ws=q(ws), proj=proj, asset=mp_asset, version=v1["version_id"]), {},
        {"X-CSRFToken": admin.csrf()})
    d = (body or {}).get("data") or {}
    ck("F3-19", "回滚 → 201 新版本 v3 指向 v1 对象（零拷贝，source_version_number=1）",
       code == HTTP["CREATED"] and d.get("version_number") == 3
       and d.get("source_version_number") == 1, f"got {code} {d}")
    v3_id = admin.req("GET", versions)[1]["data"][0]["version_id"]
    st3, loc_v3 = admin.get_no_redirect(
        ENDPOINTS["file_version_content"].format(ws=q(ws), proj=proj, asset=mp_asset, version=v3_id))
    st1, loc_v1 = admin.get_no_redirect(
        ENDPOINTS["file_version_content"].format(ws=q(ws), proj=proj, asset=mp_asset,
                                                 version=v1["version_id"]))
    ck("F3-20", "版本内容换发 302（/uploads/ 五分钟预签名）",
       st3 == 302 and st1 == 302 and (loc_v3 or "").startswith("/uploads/"), f"{st3}/{st1}")
    ck("F3-21", "回滚版本与目标版本同对象（302 Location 路径段一致）",
       (loc_v3 or "").split("?")[0] == (loc_v1 or "").split("?")[0],
       f"{(loc_v3 or '')[:80]} vs {(loc_v1 or '')[:80]}")
    code, _ = viewer.req("POST", ENDPOINTS["file_version_rollback"].format(
        ws=q(ws), proj=proj, asset=mp_asset, version=v1["version_id"]), {},
        {"X-CSRFToken": viewer.csrf()})
    ck("F3-22", "VIEWER 回滚 → 403 PERM_ROLE_INSUFFICIENT（file.version.manage）",
       code == HTTP["FORBIDDEN"], f"got {code}")
    code, _ = admin.req("GET", ENDPOINTS["file_version_content"].format(
        ws=q(ws), proj=proj, asset=mp_asset, version=str(uuid_mod.uuid4())))
    ck("F3-23", "不存在版本 content → 404", code == HTTP["NOT_FOUND"], f"got {code}")

    # —— 预览调度（§2.3 决策链）——
    preview = ENDPOINTS["file_preview"].format(ws=q(ws), proj=proj, asset="{}")
    img_id, _ = upload_direct(admin, ws, proj, folder, "S4FL-photo.png", PNG_1PX, "image/png")
    code, body = admin.req("GET", preview.format(img_id))
    d = (body or {}).get("data") or {}
    ck("F3-24", "image 预览 → 202 排队（worker 竞速窗口内可能已就绪 200，两者皆合法）",
       code in (HTTP["ACCEPTED"], HTTP["OK"]) and d.get("kind") == "image"
       and (code == HTTP["OK"] or (d.get("state") == "transcoding"
                                   and d.get("eta_seconds") == 30)), f"got {code} {d}")
    ready = _wait_until(lambda: admin.req("GET", preview.format(img_id))[0] == HTTP["OK"])
    code, body = admin.req("GET", preview.format(img_id))
    d = (body or {}).get("data") or {}
    ck("F3-25", "worker 派生后 image 预览 → 200 ready + preview_url 指向衍生物端点",
       ready and code == HTTP["OK"] and d.get("ready") is True
       and "/derivatives/thumbnail/" in (d.get("preview_url") or ""), f"got {code} {d}")
    code, loc = admin.get_no_redirect(ENDPOINTS["file_derivative"].format(
        ws=q(ws), proj=proj, asset=img_id, kind="thumbnail"))
    ck("F3-26", "derivatives/thumbnail 换发 → 302 /uploads/",
       code == 302 and (loc or "").startswith("/uploads/"), f"got {code}")

    pdf_id, _ = upload_direct(admin, ws, proj, folder, "S4FL-manual.pdf", b"%PDF-1.4 fake",
                              "application/pdf")
    code, body = admin.req("GET", preview.format(pdf_id))
    d = (body or {}).get("data") or {}
    ck("F3-27", "pdf 预览透传 → 200 ready（preview_url = 版本 content 路径）",
       code == HTTP["OK"] and d.get("kind") == "pdf" and d.get("ready") is True
       and "/content/" in (d.get("preview_url") or ""), f"got {code} {d}")
    doc_id, _ = upload_direct(admin, ws, proj, folder, "S4FL-spec.docx", b"PK docx body", "")
    code, body = admin.req("GET", preview.format(doc_id))
    d = (body or {}).get("data") or {}
    ck("F3-28", "office 无转码工具 → 202 排队/失败兜底（不 200 假成功；kind 按 .docx 判定）",
       code == HTTP["ACCEPTED"] and d.get("state") == "transcoding" and d.get("kind") == "pdf",
       f"got {code} {d}")
    txt_id, _ = upload_direct(admin, ws, proj, folder, "S4FL-readme.txt", b"line1\nline2\n",
                              "text/plain")
    code, body = admin.req("GET", preview.format(txt_id))
    d = (body or {}).get("data") or {}
    ck("F3-29", "文本 ≤2MB → 200 ready kind=text（inline 预览）",
       code == HTTP["OK"] and d.get("kind") == "text" and d.get("ready") is True
       and "/content/" in (d.get("preview_url") or ""), f"got {code} {d}")
    big_id, _ = upload_direct(admin, ws, proj, folder, "S4FL-big.log", b"L" * (3 * 1024 * 1024),
                              "text/plain")
    code, body = admin.req("GET", preview.format(big_id))
    d = (body or {}).get("data") or {}
    ck("F3-30", "文本 >2MB → 200 state=too_large + fallback_download（BR-12）",
       code == HTTP["OK"] and d.get("kind") == "text" and d.get("ready") is False
       and d.get("state") == "too_large" and d.get("fallback_download") is True, f"got {code} {d}")
    # 非流式视频（.mov 不在直传白名单——FILE-002 白名单零回改的既定差异；走分片通道）
    mov_sid = admin.req("POST", sessions, {"file_name": "S4FL-clip.mov", "file_size": 1024 * 1024,
                                           "folder_id": folder, "content_type": "video/quicktime"},
                        {"X-CSRFToken": admin.csrf()})[1]["data"]["session_id"]
    mov_body = b"\x00\x00\x00 ftypmov"
    code, body = admin.req("POST", session_chunk.format(mov_sid, 1), {}, {"X-CSRFToken": admin.csrf()})
    ok_m, etag_m = minio_put(body["data"]["upload_url"], mov_body, "video/quicktime")
    admin.req("PATCH", session_chunk.format(mov_sid, 1), {"etag": etag_m},
              {"X-CSRFToken": admin.csrf()})
    code, body = admin.req("POST", session_complete.format(mov_sid), {}, {"X-CSRFToken": admin.csrf()})
    mov_id = ((body or {}).get("data") or {}).get("file", {}).get("id")
    code, body = admin.req("GET", preview.format(mov_id))
    ck("F3-31", "非流式视频（.mov）→ 400 VALIDATION_INVALID_PARAM（仅 mp4/webm）",
       ok_m and code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["invalidParam"],
       f"put={ok_m} got {code} {body}")
    code, _ = admin.req("GET", ENDPOINTS["file_derivative"].format(
        ws=q(ws), proj=proj, asset=txt_id, kind="bogus"))
    ck("F3-32", "derivatives 未知 kind → 404（kind 域 preview/thumbnail/poster）",
       code == HTTP["NOT_FOUND"], f"got {code}")

    # —— abort（§4.2 #6）——
    abort_sid = admin.req("POST", sessions, {"file_name": "S4FL-abort.zip", "file_size": 1024,
                                             "folder_id": folder},
                          {"X-CSRFToken": admin.csrf()})[1]["data"]["session_id"]
    code, _ = admin.req("DELETE", session_detail.format(abort_sid), None,
                        {"X-CSRFToken": admin.csrf()})
    ck("F3-33", "abort 会话 → 204", code == HTTP["NO_CONTENT"], f"got {code}")
    code, body = admin.req("GET", session_detail.format(abort_sid))
    ck("F3-34", "aborted 会话状态可见（status=aborted）",
       (body or {}).get("data", {}).get("status") == "aborted", f"{body}")
    code, _ = admin.req("DELETE", session_detail.format(abort_sid), None,
                        {"X-CSRFToken": admin.csrf()})
    ck("F3-35", "重复 abort → 409 RESOURCE_STATE_INVALID", code == HTTP["CONFLICT"], f"got {code}")
    code, _ = viewer.req("POST", sessions, {"file_name": "S4FL-v.zip", "file_size": 1024,
                                            "folder_id": folder},
                         {"X-CSRFToken": viewer.csrf()})
    ck("F3-36", "VIEWER init 会话 → 403 PERM_ROLE_INSUFFICIENT（file.upload）",
       code == HTTP["FORBIDDEN"], f"got {code}")


# ═══ 3. FILE-004 文件分享 ═══

def share_segment(admin, admin2, member, viewer, ws, admin_id, member_id, viewer_id):
    section("FILE-004 文件分享")
    proj = make_project(admin, ws, "S4FLOW-SHARE", "S4S")
    join_project(admin, ws, proj, member_id, 15)
    join_project(admin, ws, proj, viewer_id, 5)
    admin2_id = uid_of(admin2)
    join_project(admin, ws, proj, admin2_id, 20)  # 第二项目管理员
    folders = ENDPOINTS["folders"].format(ws=q(ws), proj=proj)
    fshare = admin.req("POST", folders, {"name": "S4FL-分享区"},
                       {"X-CSRFToken": admin.csrf()})[1]["data"]["id"]
    shares = ENDPOINTS["share_links"].format(ws=q(ws), proj=proj, asset="{}")
    links = ENDPOINTS["share_link_detail"].format(ws=q(ws), proj=proj, link="{}")
    pub = ENDPOINTS["public_share"].format(slug="{}")

    f_txt, _ = upload_direct(admin, ws, proj, fshare, "S4FL-share.txt", b"share me", "text/plain")
    f_adm, _ = upload_direct(admin, ws, proj, fshare, "S4FL-share-secret.txt", b"secret",
                             "text/plain")
    admin.req("PATCH", ENDPOINTS["file_detail"].format(ws=q(ws), proj=proj, asset=f_adm),
              {"visibility": "admins"}, {"X-CSRFToken": admin.csrf()})
    anon = Client(BASE)  # 匿名访客（公开分组端点）

    # —— 创建（§4.2.1 / BR-11）——
    code, body = admin.req("POST", shares.format(f_txt),
                           {"permission": "view", "expires_in_days": 7},
                           {"X-CSRFToken": admin.csrf()})
    d = (body or {}).get("data") or {}
    view_link, view_slug = d.get("id"), d.get("slug")
    ck("F4-01", "创建 view 权限分享 → 201 {slug(22), share_url(/s/), status=active}",
       code == HTTP["CREATED"] and len(d.get("slug") or "") == 22
       and (d.get("share_url") or "").endswith(f"/s/{d.get('slug')}")
       and d.get("status") == "active" and d.get("has_password") is False
       and d.get("permission") == "view", f"got {code} {d}")
    code, body = anon.req("GET", pub.format(view_slug))
    d = (body or {}).get("data") or {}
    ck("F4-02", "匿名 meta（无密码链）→ requires_password=false + file 元信息最小集",
       code == HTTP["OK"] and d.get("requires_password") is False
       and (d.get("file") or {}).get("name") == "S4FL-share.txt"
       and d.get("permission") == "view", f"got {code} {d}")
    code, body = anon.req("GET", pub.format("A" * 22))
    ck("F4-03", "无效 slug → 410 RESOURCE_GONE（同码同文案防枚举）",
       code == HTTP["GONE"] and error_code(body) == CODES["gone"], f"got {code} {body}")
    code, _ = viewer.req("POST", shares.format(f_adm), {}, {"X-CSRFToken": viewer.csrf()})
    ck("F4-04", "不可见文件（admins 态）建分享 → 404（先于 403 的存在性隐藏，BR-01）",
       code == HTTP["NOT_FOUND"], f"got {code}")

    code, body = admin.req("POST", shares.format(f_txt),
                           {"password": "pass1234", "expires_in_days": 7},
                           {"X-CSRFToken": admin.csrf()})
    pw_slug = ((body or {}).get("data") or {}).get("slug")
    ck("F4-05", "创建密码+有效期分享 → 201 has_password=true + expires_at 非空",
       code == HTTP["CREATED"] and ((body or {}).get("data") or {}).get("has_password") is True
       and ((body or {}).get("data") or {}).get("expires_at"), f"got {code}")
    code, body = anon.req("GET", pub.format(pw_slug))
    d = (body or {}).get("data") or {}
    ck("F4-06", "密码门 meta 仅 {requires_password:true}——零文件/项目信息泄露（BR-10）",
       code == HTTP["OK"] and d == {"requires_password": True}, f"got {code} {d}")
    code, body = admin.req("POST", shares.format(f_txt), {"expires_in_days": 0},
                           {"X-CSRFToken": admin.csrf()})
    ck("F4-07", "有效期 0 天 → 400（TOO_SMALL 子码）",
       code == HTTP["BAD_REQUEST"]
       and (detail_of(body, "expires_in_days") or {}).get("code") == "TOO_SMALL", f"got {code}")

    # —— unlock：错密码 / 剩余次数 / 五错锁定（BR-07/BR-08）——
    unlock = ENDPOINTS["public_unlock"].format(slug="{}")
    content = ENDPOINTS["public_content"].format(slug="{}")
    code, body = anon.req("GET", content.format(pw_slug))
    ck("F4-08", "未解锁读 content → 401 AUTH_REQUIRED",
       code == HTTP["UNAUTHORIZED"] and error_code(body) == CODES["authRequired"], f"got {code} {body}")
    code, body = anon.req("POST", unlock.format(pw_slug), {"password": "wrong-pw"},
                          {"X-CSRFToken": anon.csrf()})
    det = detail_of(body, "password") or {}
    ck("F4-09", "错密码 → 401 AUTH_INVALID_CREDENTIALS + details 剩余 4 次",
       code == HTTP["UNAUTHORIZED"] and error_code(body) == CODES["invalidCreds"]
       and det.get("code") == "INVALID" and "4 次" in str(det.get("message", "")), f"got {code} {body}")
    fifth = None
    for _i in range(4):
        fifth = anon.req("POST", unlock.format(pw_slug), {"password": "wrong-pw"},
                         {"X-CSRFToken": anon.csrf()})
    det = detail_of(fifth[1], "password") or {}
    ck("F4-10", "累计五错 → 第 5 次响应剩余 0 次",
       "剩余 0 次" in str(det.get("message", "")), f"{det}")
    code, body, hdrs = anon.req("POST", unlock.format(pw_slug), {"password": "wrong-pw"},
                                {"X-CSRFToken": anon.csrf()}, want_headers=True)
    ck("F4-11", "第 6 次 → 429 RATE_LIMIT_EXCEEDED + Retry-After（5 次/10min/(IP,slug)）",
       code == HTTP["TOO_MANY"] and error_code(body) == CODES["rateLimited"]
       and hdrs.get("Retry-After") is not None, f"got {code} retry={hdrs.get('Retry-After')}")

    # 成功解锁走独立 slug（(IP,slug) 二维键互不干扰）
    ok_slug = admin.req("POST", shares.format(f_txt), {"password": "pass5678"},
                        {"X-CSRFToken": admin.csrf()})[1]["data"]["slug"]
    code, body, hdrs = anon.req("POST", unlock.format(ok_slug), {"password": "pass5678"},
                                {"X-CSRFToken": anon.csrf()}, want_headers=True)
    ck("F4-12", "正确密码解锁 → 200 {unlocked, expires_in:7200} + share_token cookie",
       code == HTTP["OK"] and (body or {}).get("data", {}).get("unlocked") is True
       and (body or {}).get("data", {}).get("expires_in") == 7200
       and "share_token" in (hdrs.get("Set-Cookie") or ""), f"got {code} {hdrs.get('Set-Cookie')}")
    code, body = anon.req("GET", pub.format(ok_slug))
    d = (body or {}).get("data") or {}
    ck("F4-13", "解锁后 meta → 文件元信息 + permission + expires_at",
       d.get("requires_password") is False and (d.get("file") or {}).get("name") == "S4FL-share.txt",
       f"{d}")

    # —— 文件态 + 下载（BR-05 / §4.3.3）——
    code, body = anon.req("GET", content.format(ok_slug))
    d = (body or {}).get("data") or {}
    ck("F4-14", "匿名 content 预览态 → 200（text ready，匿名直签变体）",
       code == HTTP["OK"] and d.get("kind") == "text"
       and (d.get("preview_url") or "").startswith("/uploads/"), f"got {code} {d}")
    code, body = anon.req("GET", content.format(view_slug) + "?download=1")
    ck("F4-15", "仅预览链 ?download=1 → 403 PERM_DENIED（BR-05）",
       code == HTTP["FORBIDDEN"] and error_code(body) == CODES["permDenied"], f"got {code} {body}")
    dl = admin.req("POST", shares.format(f_txt), {"permission": "download"},
                   {"X-CSRFToken": admin.csrf()})[1]["data"]
    dl_slug, dl_link = dl["slug"], dl["id"]
    st, loc = anon.get_no_redirect(content.format(dl_slug) + "?download=1")
    ck("F4-16", "下载链 ?download=1 → 302 跳 /uploads/ 五分钟预签名",
       st == 302 and (loc or "").startswith("/uploads/"), f"got {st} {loc}")

    # —— 读时四查统一 410（§4.3.1：吊销/过期/源软删/项目归档 同码同文案）——
    gone_msg = "链接不存在或已失效"
    code, _ = admin.req("DELETE", links.format(view_link), None, {"X-CSRFToken": admin.csrf()})
    ck("F4-17", "吊销 → 204 终态", code == HTTP["NO_CONTENT"], f"got {code}")
    code, body = anon.req("GET", pub.format(view_slug))
    ck("F4-18", "四查①吊销 → 410 RESOURCE_GONE 同文案",
       code == HTTP["GONE"] and error_code(body) == CODES["gone"]
       and ((body or {}).get("error") or {}).get("message") == gone_msg, f"got {code} {body}")

    exp_slug = admin.req("POST", shares.format(f_txt), {"expires_in_days": 1},
                         {"X-CSRFToken": admin.csrf()})[1]["data"]["slug"]
    _pg_exec(f"UPDATE file_share_links SET expires_at = now() - interval '1 hour' "
             f"WHERE slug = '{exp_slug}'")
    code, body = anon.req("GET", pub.format(exp_slug))
    ck("F4-19", "四查②过期 → 410 同码同文案（惰性标记 expired）",
       code == HTTP["GONE"] and error_code(body) == CODES["gone"]
       and ((body or {}).get("error") or {}).get("message") == gone_msg, f"got {code}")

    del_slug = admin.req("POST", shares.format(f_adm), {"permission": "download"},
                         {"X-CSRFToken": admin.csrf()})[1]["data"]["slug"]
    admin.req("DELETE", ENDPOINTS["file_detail"].format(ws=q(ws), proj=proj, asset=f_adm),
              None, {"X-CSRFToken": admin.csrf()})
    code, body = anon.req("GET", pub.format(del_slug))
    ck("F4-20", "四查③源软删 → 410 同码同文案（恢复不复活）",
       code == HTTP["GONE"] and error_code(body) == CODES["gone"], f"got {code}")

    arch_slug = admin.req("POST", shares.format(f_txt), {"permission": "download"},
                          {"X-CSRFToken": admin.csrf()})[1]["data"]["slug"]
    _pg_exec(f"UPDATE projects SET status='archived' WHERE id='{proj}'")
    code, body = anon.req("GET", pub.format(arch_slug))
    ck("F4-21", "四查④项目归档 → 410 同码同文案",
       code == HTTP["GONE"] and error_code(body) == CODES["gone"], f"got {code}")
    _pg_exec(f"UPDATE projects SET status='active' WHERE id='{proj}'")

    # —— 延期 / 管理列表 / 归属（§4.2.5 / BR-15）——
    ext = admin.req("POST", shares.format(f_txt), {"expires_in_days": 1},
                    {"X-CSRFToken": admin.csrf()})[1]["data"]
    before = ext.get("expires_at")
    code, body = admin.req("POST", ENDPOINTS["share_link_extend"].format(
        ws=q(ws), proj=proj, link=ext["id"]), {"extend_days": 7}, {"X-CSRFToken": admin.csrf()})
    d = (body or {}).get("data") or {}
    ck("F4-22", "延期 7 天 → 200 expires_at 后移 + status=active（非幂等叠加）",
       code == HTTP["OK"] and d.get("expires_at") and d.get("expires_at") > (before or ""),
       f"got {code} {before} → {d.get('expires_at')}")
    perm_link = admin.req("POST", shares.format(f_txt), {"password": "perm-9999"},
                          {"X-CSRFToken": admin.csrf()})[1]["data"]["id"]
    code, body = admin.req("POST", ENDPOINTS["share_link_extend"].format(
        ws=q(ws), proj=proj, link=perm_link), {"extend_days": 7}, {"X-CSRFToken": admin.csrf()})
    ck("F4-23", "永久链接延期 → 400 VALIDATION_ERROR（field=expires_at INVALID）",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["validation"]
       and (detail_of(body, "expires_at") or {}).get("code") == "INVALID", f"got {code} {body}")
    code, body = admin.req("POST", ENDPOINTS["share_link_extend"].format(
        ws=q(ws), proj=proj, link=ext["id"]), {"extend_days": 366}, {"X-CSRFToken": admin.csrf()})
    ck("F4-24", "延期步长 366 → 400 TOO_LARGE（≤365）",
       code == HTTP["BAD_REQUEST"]
       and (detail_of(body, "extend_days") or {}).get("code") == "TOO_LARGE", f"got {code}")
    code, _ = member.req("POST", ENDPOINTS["share_link_extend"].format(
        ws=q(ws), proj=proj, link=ext["id"]), {"extend_days": 1}, {"X-CSRFToken": member.csrf()})
    ck("F4-25", "CONTRIBUTOR 延期（file.share=ADMIN 门槛）→ 403 PERM_ROLE_INSUFFICIENT",
       code == HTTP["FORBIDDEN"], f"got {code}")
    code, _ = admin2.req("POST", ENDPOINTS["share_link_extend"].format(
        ws=q(ws), proj=proj, link=ext["id"]), {"extend_days": 1}, {"X-CSRFToken": admin2.csrf()})
    ck("F4-26", "第二 ADMIN（非创建者）延期 → 200（ADMIN 直通归属）",
       code == HTTP["OK"], f"got {code}")

    # 管理列表（含失效态）+ 访问计数（BR-09）
    for _i in range(2):
        anon.req("GET", content.format(dl_slug))
    code, body = admin.req("GET", shares.format(f_txt))
    srows = (body or {}).get("data") or []
    smeta = (body or {}).get("meta") or {}
    statuses = {r["id"]: r["status"] for r in srows}
    dl_row = next(r for r in srows if r["id"] == dl_link)
    ck("F4-27", "管理列表含失效态（revoked/expired 行均在）+ meta 分页字段",
       code == HTTP["OK"] and statuses.get(view_link) == "revoked"
       and {"next_cursor", "total_count"} <= set(smeta), f"{statuses}")
    ck("F4-28", "访问计数 access_count≥2（仅 view/download 成功计数，BR-09）",
       dl_row.get("access_count", 0) >= 2, f"{dl_row}")
    code, _ = viewer.req("POST", shares.format(f_txt), {}, {"X-CSRFToken": viewer.csrf()})
    ck("F4-29", "VIEWER 创建分享 → 403 PERM_ROLE_INSUFFICIENT（file.share=ADMIN）",
       code == HTTP["FORBIDDEN"], f"got {code}")
    code, _ = admin.req("POST", shares.format(str(uuid_mod.uuid4())), {},
                        {"X-CSRFToken": admin.csrf()})
    ck("F4-30", "不存在资产建分享 → 404", code == HTTP["NOT_FOUND"], f"got {code}")


# ═══ 4. GANTT-001 甘特取数地基 ═══

def gantt1_segment(admin, member, viewer, ws, admin_id, member_id, viewer_id):
    section("GANTT-001 甘特取数地基")
    proj = make_project(admin, ws, "S4FLOW-GANTT", "S4G")
    join_project(admin, ws, proj, member_id, 15)
    join_project(admin, ws, proj, viewer_id, 5)
    gantt = ENDPOINTS["gantt_rows"].format(ws=q(ws), proj=proj)
    _, b = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/states/?include_cancelled=1")
    states = {x["group"]: x["id"] for x in b["data"]}

    vs, ve = dstr(-10), dstr(10)
    iA = make_issue(admin, ws, proj, "S4G-逾期", start_date=dstr(-5), target_date=dstr(-1),
                    assignee_ids=[admin_id])
    make_issue(admin, ws, proj, "S4G-窗外", start_date=dstr(30), target_date=dstr(40))
    iC = make_issue(admin, ws, proj, "S4G-跨窗", start_date=dstr(-2), target_date=dstr(3))
    iG = make_issue(admin, ws, proj, "S4G-开放端逾期", target_date=dstr(-2))  # start NULL
    iH = make_issue(admin, ws, proj, "S4G-未排期")  # 双 NULL 且子树无日期
    iF = make_issue(admin, ws, proj, "S4G-已完成豁免", target_date=dstr(-3),
                    state_id=states["completed"])
    iS = make_issue(admin, ws, proj, "S4G-进行中", start_date=dstr(-1), target_date=dstr(2),
                    state_id=states["started"])
    iParent = make_issue(admin, ws, proj, "S4G-聚合父")  # 双 NULL 带日期子树
    make_issue(admin, ws, proj, "S4G-子一", parent_id=iParent["id"],
               start_date=dstr(1), target_date=dstr(6), state_id=states["completed"])
    make_issue(admin, ws, proj, "S4G-子二", parent_id=iParent["id"],
               start_date=dstr(1), target_date=dstr(6))

    # —— 视窗取数（BR-02/BR-03 相交判定）——
    code, body, rows, meta = gantt_rows_all(admin, ws, proj, vs, ve)
    row_of = {r["id"]: r for r in rows}
    ids_in = set(row_of)
    ck("G1-01", "相交判定：窗内 iA/iC/iG/聚合父在窗、窗外任务不进响应",
       code == HTTP["OK"] and {iA["id"], iC["id"], iG["id"], iParent["id"]} <= ids_in
       and not any("窗外" in r["name"] for r in rows), f"{len(rows)} 行")
    agg = row_of.get(iParent["id"]) or {}
    ck("G1-02", "聚合条父（双 NULL 子树有日期）→ is_aggregated + 子树区间 [T+1, T+6]",
       agg.get("is_aggregated") is True and agg.get("start_date") == dstr(1)
       and agg.get("target_date") == dstr(6), f"{agg.get('start_date')}~{agg.get('target_date')}")
    ck("G1-03", "进度派生：聚合父 1/2 完成 → progress=50 source=subtasks",
       agg.get("progress") == 50 and agg.get("progress_source") == "subtasks"
       and agg.get("has_children") is True, f"{agg.get('progress')}/{agg.get('progress_source')}")
    ck("G1-04", "进度派生：无子任务按语义组（started→50 / unstarted→0）",
       (row_of.get(iS["id"]) or {}).get("progress") == 50
       and (row_of.get(iS["id"]) or {}).get("progress_source") == "state"
       and (row_of.get(iC["id"]) or {}).get("progress") == 0, "")
    ck("G1-05", "is_overdue 口径：iA 逾期 true / 已完成豁免 false / 开放端（start NULL）true",
       (row_of.get(iA["id"]) or {}).get("is_overdue") is True
       and (row_of.get(iF["id"]) or {}).get("is_overdue") is False
       and (row_of.get(iG["id"]) or {}).get("is_overdue") is True, "")
    ck("G1-06", "meta 视窗回显 {viewport, today, granularity}",
       ((meta.get("viewport") or {}).get("start") == vs)
       and bool(meta.get("today")) and meta.get("granularity") == "day", f"{meta}")
    ck("G1-07", "行契约字段（issue_key/depth/relation_count/spent_minutes）齐备",
       all({"id", "issue_key", "depth", "relation_count", "spent_minutes", "assignee_ids",
            "state_group"} <= set(r) for r in rows), "")

    # —— 参数边界（§2.4 异常表）——
    code, body = admin.req("GET", gantt + f"?viewport_end={ve}")
    ck("G1-08", "缺 viewport_start → 400 INVALID_PARAM（REQUIRED）",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["invalidParam"]
       and (detail_of(body, "viewport_start") or {}).get("code") == "REQUIRED", f"got {code}")
    code, body = admin.req("GET", gantt + f"?viewport_start={dstr(5)}&viewport_end={dstr(1)}")
    ck("G1-09", "视窗起始晚于结束 → 400（INVALID_DATE_RANGE）",
       code == HTTP["BAD_REQUEST"]
       and (detail_of(body, "viewport_start") or {}).get("code") == "INVALID_DATE_RANGE",
       f"got {code}")
    code, body = admin.req("GET", gantt + f"?viewport_start={vs}&viewport_end={ve}&tz=Not/Real")
    ck("G1-10", "非法 tz → 400（不静默回退）", code == HTTP["BAD_REQUEST"], f"got {code}")
    code, body = admin.req("GET",
                           gantt + f"?viewport_start={vs}&viewport_end={ve}&tz=Asia/Shanghai")
    ck("G1-11", "合法 tz → 200（?tz=Asia/Shanghai）", code == HTTP["OK"], f"got {code}")
    code, body = admin.req("GET",
                           gantt + f"?viewport_start={vs}&viewport_end={ve}&granularity=year")
    ck("G1-12", "granularity 非法 → 400 NOT_A_CHOICE",
       code == HTTP["BAD_REQUEST"]
       and (detail_of(body, "granularity") or {}).get("code") == "NOT_A_CHOICE", f"got {code}")
    code, body = admin.req("GET", gantt + f"?viewport_start={vs}&viewport_end={ve}&per_page=101")
    ck("G1-13", "per_page 超上限 → 静默截断 100 + meta.degraded",
       code == HTTP["OK"] and (body or {}).get("meta", {}).get("per_page") == 100
       and "per_page" in str((((body or {}).get("meta") or {}).get("degraded") or {})), f"got {code}")
    code, body = admin.req("GET", gantt + f"?viewport_start={vs}&viewport_end={ve}&cursor=@@@bad")
    ck("G1-14", "损坏游标 → 400 VALIDATION_INVALID_CURSOR",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["invalidCursor"], f"got {code}")

    # 跨视窗平移（BR-02：带日期行按 (start,target) 与窗相交）
    code, body, rows2, _m = gantt_rows_all(admin, ws, proj, dstr(35), dstr(45))
    names2 = {r["name"] for r in rows2}
    ck("G1-15", "平移到 T+35..T+45：窗外任务入窗、原窗行（iA/iC/iG/聚合父子树）出窗",
       "S4G-窗外" in names2 and "S4G-逾期" not in names2
       and not any(r["id"] in {iC["id"], iG["id"], iParent["id"]} for r in rows2),
       f"{sorted(names2)}")

    # —— 未排期（BR-03 口径）——
    code, body = admin.req("GET", ENDPOINTS["gantt_unscheduled"].format(ws=q(ws), proj=proj))
    urows = (body or {}).get("data") or []
    umeta = (body or {}).get("meta") or {}
    ck("G1-16", "unscheduled 列表恰含双 NULL 且子树全无日期行（聚合父排除）",
       code == HTTP["OK"] and {r["id"] for r in urows} == {iH["id"]}
       and umeta.get("total_count") == 1, f"{[r['name'] for r in urows]}")
    ck("G1-17", "未排期行 fields 裁剪（id/issue_key/name/state_group/assignee_ids）",
       urows and set(urows[0]) == {"id", "issue_key", "name", "state_group", "state_color",
                                   "assignee_ids"}, f"{set(urows[0]) if urows else None}")
    code, body = admin.req("GET", gantt + f"?viewport_start={vs}&viewport_end={ve}")
    ck("G1-18", "rows.unscheduled_count 与 unscheduled/ 同口径（=1，聚合父不计）",
       (body or {}).get("data", {}).get("unscheduled_count") == 1,
       f"{(body or {}).get('data', {}).get('unscheduled_count')}")

    # —— relations bulk（§4.3.3 violation 派生 + 镜像去重）——
    r1 = make_issue(admin, ws, proj, "S4G-阻塞源", start_date=dstr(-8), target_date=dstr(-4))
    r2 = make_issue(admin, ws, proj, "S4G-被阻塞", start_date=dstr(-6), target_date=dstr(1))
    admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{r1['id']}/relations/",
              {"related_issue_id": r2["id"], "relation_type": "blocks"},
              {"X-CSRFToken": admin.csrf()})
    r3 = make_issue(admin, ws, proj, "S4G-关联一", start_date=dstr(-1), target_date=dstr(2))
    r4 = make_issue(admin, ws, proj, "S4G-关联二", start_date=dstr(-1), target_date=dstr(2))
    admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{r3['id']}/relations/",
              {"related_issue_id": r4["id"], "relation_type": "relates_to"},
              {"X-CSRFToken": admin.csrf()})
    bulk = ENDPOINTS["gantt_relations"].format(ws=q(ws), proj=proj)
    code, body = admin.req("POST", bulk, {"issue_ids": [r1["id"], r2["id"], r3["id"], r4["id"]]},
                           {"X-CSRFToken": admin.csrf()})
    d = (body or {}).get("data") or {}
    meta = (body or {}).get("meta") or {}
    blocks_edges = [e for e in d.get("edges") or [] if e["relation_type"] == "blocks"]
    rel_edges = [e for e in d.get("edges") or [] if e["relation_type"] == "relates_to"]
    ck("G1-19", "blocks 边恰 1 条（成对存储镜像去重）且 from/to 带编号",
       code == HTTP["OK"] and len(blocks_edges) == 1
       and blocks_edges[0]["from"]["issue_key"].startswith("S4G-")
       and blocks_edges[0]["to"]["issue_key"].startswith("S4G-"), f"{len(blocks_edges)}")
    ck("G1-20", "violation 派生：dst.start(T-6) < src.target(T-4) → true",
       bool(blocks_edges) and blocks_edges[0]["to"].get("violation") is True, f"{blocks_edges[:1]}")
    ck("G1-21", "relates_to 对称类型按 id 序去重恰 1 条 + violation 恒 False（非派生信号）",
       len(rel_edges) == 1 and rel_edges[0]["to"].get("violation") is False, f"{rel_edges}")
    ck("G1-22", "meta {requested:4, edges:2}（行集连线批量计数）",
       meta.get("requested") == 4 and meta.get("edges") == 2, f"{meta}")
    many = [str(r["id"]) for r in rows] + [str(uuid_mod.uuid4()) for _ in range(55)]
    code, body = admin.req("POST", bulk, {"issue_ids": many[:61]},
                           {"X-CSRFToken": admin.csrf()})
    ck("G1-23", "61 个 id → 200 截断至 60（meta.requested=61，§2.5 前端分批）",
       code == HTTP["OK"] and (body or {}).get("meta", {}).get("requested") == 61, f"got {code}")
    code, body = admin.req("POST", bulk, {"issue_ids": "not-a-list"},
                           {"X-CSRFToken": admin.csrf()})
    ck("G1-24", "issue_ids 非数组 → 400", code == HTTP["BAD_REQUEST"], f"got {code}")
    code, body = admin.req("POST", bulk, {"issue_ids": ["not-a-uuid"]},
                           {"X-CSRFToken": admin.csrf()})
    ck("G1-25", "issue_ids 元素非 UUID → 400", code == HTTP["BAD_REQUEST"], f"got {code}")

    # —— view_id 联动（ADR-0021：甘特 view_id 仅内置或本人，不沿用 board.manage 审计面）——
    views = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/views/"
    urgent_vid = admin.req("POST", views, {
        "name": "S4G-紧急视图", "layout": "list",
        "filters": {"op": "AND", "conditions": [
            {"field": "priority", "operator": "in", "value": ["urgent"]}]}},
        {"X-CSRFToken": admin.csrf()})[1]["data"]["id"]
    make_issue(admin, ws, proj, "S4G-紧急行", priority="urgent",
               start_date=dstr(-1), target_date=dstr(4))
    code, body, rows_v, meta_v = gantt_rows_all(admin, ws, proj, vs, ve, f"&view_id={urgent_vid}")
    ck("G1-26", "view_id 联动：视图 filters（urgent）作用于甘特行集（仅紧急行入选）",
       code == HTTP["OK"] and {r["name"] for r in rows_v} == {"S4G-紧急行"},
       f"rows={[r['name'] for r in rows_v]}")
    mem_vid = member.req("POST", views, {"name": "S4G-成员私有"},
                         {"X-CSRFToken": member.csrf()})[1]["data"]["id"]
    code, _ = admin.req("GET",
                        gantt + f"?viewport_start={vs}&viewport_end={ve}&view_id={mem_vid}")
    ck("G1-27", "他人个人视图 view_id → 404（ADR-0021：不走 board.manage 审计口径）",
       code == HTTP["NOT_FOUND"], f"got {code}")
    code, _ = admin.req("GET",
                        gantt + f"?viewport_start={vs}&viewport_end={ve}&view_id={uuid_mod.uuid4()}")
    ck("G1-28", "不存在 view_id → 404", code == HTTP["NOT_FOUND"], f"got {code}")
    code, _ = viewer.req("GET", gantt + f"?viewport_start={vs}&viewport_end={ve}")
    ck("G1-29", "VIEWER 甘特行取数 → 200（project.read）", code == HTTP["OK"], f"got {code}")


# ═══ 5. GANTT-002 延期概览 ═══

def gantt2_segment(admin, member, c_agg, ws, admin_id, member_id):
    section("GANTT-002 延期概览")
    proj = make_project(admin, ws, "S4FLOW-OVERDUE", "S4O")
    join_project(admin, ws, proj, member_id, 15)
    join_project(admin, ws, proj, uid_of(c_agg), 5)
    summary = ENDPOINTS["gantt_overdue"].format(ws=q(ws), proj=proj)
    _, b = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/states/?include_cancelled=1")
    state_todo = next(x["id"] for x in b["data"] if x["group"] == "unstarted")
    state_started = next(x["id"] for x in b["data"] if x["group"] == "started")
    state_done = next(x["id"] for x in b["data"] if x["group"] == "completed")

    # 2 条指派逾期（by_assignee 数据面）+ 25 条 SQL 逾期（前 20 截断数据面）。
    # 日期基线取 API 自报的 meta.today（tz 折算口径）——PG current_date 是 UTC 日界，
    # 与 Asia/Shanghai 的「今天」在晚间窗口相差一天，直接用会算错 overdue_days。
    _c, _b, _r, gm = gantt_rows_all(admin, ws, proj, dstr(-1), dstr(1))
    today_api = gm.get("today") or dstr(0)
    make_issue(admin, ws, proj, "S4O-指派A", target_date=dstr(-2), assignee_ids=[admin_id])
    make_issue(admin, ws, proj, "S4O-指派B", target_date=dstr(-3),
               assignee_ids=[member_id], state_id=state_started)
    make_issue(admin, ws, proj, "S4O-未逾期", target_date=dstr(2))
    make_issue(admin, ws, proj, "S4O-已完成豁免", target_date=dstr(-9), state_id=state_done)
    _pg_exec(
        "INSERT INTO issues (id, project_id, name, description_json, description_html, "
        " priority, sequence_id, sort_order, target_date, custom_fields, state_id, "
        " created_by_id, created_at, updated_at, attachment_count) "
        "SELECT gen_random_uuid(), %s, 'S4O-bulk-' || g, '{}'::jsonb, '<p></p>', 'medium', "
        " 100 + g, g * 100.0, date %s - (5 + g), '{}'::jsonb, %s, %s, now(), now(), 0 "
        "FROM generate_series(1, 25) g", (proj, today_api, state_todo, admin_id))
    total_overdue = 27  # 25 SQL + 2 指派
    max_days = 30       # today_api - (5+25)

    code, body = admin.req("GET", summary)
    d = (body or {}).get("data") or {}
    meta = (body or {}).get("meta") or {}
    ck("G2-01", "overdue-summary 形状：三数字 + by_assignee + items + items_truncated + meta.today",
       code == HTTP["OK"] and {"overdue_count", "max_overdue_days", "by_assignee", "items",
                               "items_truncated"} == set(d) and bool(meta.get("today")),
       f"got {code} {str(d)[:160]}")
    ck("G2-02", "overdue_count=27（完整逾期集聚合，非 items 截断集）",
       d.get("overdue_count") == total_overdue, f"got {d.get('overdue_count')}")
    ck("G2-03", "max_overdue_days=30（完整集最早 target 推导）",
       d.get("max_overdue_days") == max_days, f"got {d.get('max_overdue_days')}")
    sql_n = _pg_val(
        f"SELECT count(*) FROM issues i LEFT JOIN states s ON s.id = i.state_id "
        f"WHERE i.project_id='{proj}' AND i.deleted_at IS NULL AND i.archived_at IS NULL "
        f"AND i.target_date IS NOT NULL AND i.target_date < date '{today_api}' "
        f"AND COALESCE(s.group, 'unstarted') NOT IN ('completed', 'cancelled')")
    ck("G2-04", "与行级 is_overdue 真同源：SQL 集合投影（overdue_q 等价式）计数一致",
       sql_n == str(total_overdue), f"sql={sql_n}")
    items = d.get("items") or []
    ck("G2-05", "items 前 20 截断（27 条逾期集）+ items_truncated=true + 逾期天数降序",
       len(items) == 20 and d.get("items_truncated") is True
       and [it["overdue_days"] for it in items]
       == sorted((it["overdue_days"] for it in items), reverse=True),
       f"len={len(items)}")
    ck("G2-06", "明细行 {issue_key, target_date, overdue_days, assignee_ids} 逐行派生",
       bool(items) and items[0]["overdue_days"] == max_days
       and items[0]["issue_key"].startswith("S4O-")
       and {"id", "issue_key", "name", "target_date", "overdue_days", "assignee_ids"} <= set(items[0]),
       f"{items[0] if items else None}")
    ba = {r["assignee_id"]: r["count"] for r in d.get("by_assignee") or []}
    ck("G2-07", "by_assignee 按执行人分布（admin=1/member=1；未指派不入分布）+ display_name",
       ba.get(admin_id) == 1 and ba.get(member_id) == 1
       and all("display_name" in r for r in d.get("by_assignee") or []), f"{d.get('by_assignee')}")

    # 行级同源复核：完整视窗下 is_overdue 计数 == overdue_count（同一筛选管道）
    code, body, rows, _m = gantt_rows_all(admin, ws, proj, dstr(-60), dstr(0))
    ck("G2-08", "gantt/ 行级 is_overdue=true 行数 == overdue-summary.overdue_count（同源双端点）",
       code == HTTP["OK"] and sum(1 for r in rows if r["is_overdue"]) == total_overdue,
       f"rows_overdue={sum(1 for r in rows if r['is_overdue'])}")

    # —— 429：10 次/min/用户（§4.2.1 契约要点 4；专用账号连发）——
    codes = []
    retry_after = None
    for _i in range(11):
        code, body, hdrs = c_agg.req("GET", summary, want_headers=True)
        codes.append(code)
        if code == HTTP["TOO_MANY"] and retry_after is None:
            retry_after = hdrs.get("Retry-After")
    ck("G2-09", "限流：60s 内第 11 次 → 429 + Retry-After（前 10 次全 200）",
       codes[:10] == [HTTP["OK"]] * 10 and codes[10] == HTTP["TOO_MANY"]
       and retry_after is not None, f"codes={codes}")
    code, body = c_agg.req("GET", summary)
    ck("G2-10", "429 错误码 = RATE_LIMIT_EXCEEDED", error_code(body) == CODES["rateLimited"],
       f"{error_code(body)}")
    code, _ = admin.req("GET", summary + "?viewport_start=" + dstr(-10))
    ck("G2-11", "聚合端点不接受视窗参数（viewport_* 忽略，无分页单包返回）",
       code == HTTP["OK"], f"got {code}")


# ═══ 6. 实时票据 file_rooms（事件扇出由 T4-08 e2e 覆盖，此处断票据与房间）═══

def realtime_segment(admin, member, viewer, ws, admin_id, member_id, viewer_id):
    section("COLLAB-004×FILE-003 file_rooms 换票")
    proj = _pg_val("SELECT id FROM projects WHERE name = 'S4FLOW-FILE' "
                   "ORDER BY created_at DESC LIMIT 1")
    v_all_file = _pg_val(
        "SELECT id FROM file_assets WHERE project_id='%s' AND attributes->>'name'='S4FL-open.txt' "
        "ORDER BY created_at DESC LIMIT 1" % proj)
    v_adm_file = _pg_val(
        "SELECT id FROM file_assets WHERE project_id='%s' AND attributes->>'name'='S4FL-secret.txt' "
        "ORDER BY created_at DESC LIMIT 1" % proj)
    rt = ENDPOINTS["realtime_token"].format(ws=q(ws), proj=proj)
    tab = str(uuid_mod.uuid4())

    code, body = admin.req("POST", rt, {"client_tab_id": tab, "file_rooms": [v_all_file]},
                           {"X-CSRFToken": admin.csrf()})
    rooms = ((body or {}).get("data") or {}).get("rooms") or []
    ck("RT-01", "换票携带 file_rooms → 200 rooms 服务端装配 file:{asset} + project/user 恒附",
       code == HTTP["OK"] and f"file:{v_all_file}" in rooms
       and f"project:{proj}" in rooms and any(r.startswith("user:") for r in rooms),
       f"got {code} {rooms}")
    code, body = member.req("POST", rt, {"client_tab_id": tab, "file_rooms": [v_adm_file]},
                            {"X-CSRFToken": member.csrf()})
    ck("RT-02", "不可见资产（admins 态）file_rooms → 403 PERM_DENIED 拒整票",
       code == HTTP["FORBIDDEN"] and error_code(body) == CODES["permDenied"]
       and (detail_of(body, "file_rooms") or {}).get("code") == "PERM_DENIED", f"got {code} {body}")
    foreign = _pg_val("SELECT a.id FROM file_assets a JOIN projects p ON p.id = a.project_id "
                      "WHERE p.name = 'S4FLOW-CHUNK' LIMIT 1")
    code, body = admin.req("POST", rt, {"client_tab_id": tab, "file_rooms": [foreign]},
                           {"X-CSRFToken": admin.csrf()})
    ck("RT-03", "他项目资产 file_rooms → 403 拒整票（项目域过滤）",
       code == HTTP["FORBIDDEN"] and error_code(body) == CODES["permDenied"], f"got {code}")
    code, body = viewer.req("POST", rt, {"client_tab_id": tab, "file_rooms": [v_all_file]},
                            {"X-CSRFToken": viewer.csrf()})
    ck("RT-04", "VIEWER 携带 all 态资产 → 200（file.read=VIEWER+）",
       code == HTTP["OK"] and f"file:{v_all_file}" in (
           ((body or {}).get("data") or {}).get("rooms") or []), f"got {code}")
    code, body = admin.req("POST", rt, {
        "client_tab_id": tab,
        "issue_rooms": [str(uuid_mod.uuid4()) for _ in range(5)],
        "file_rooms": [str(uuid_mod.uuid4()) for _ in range(5)]},
        {"X-CSRFToken": admin.csrf()})
    ck("RT-05", "issue_rooms + file_rooms 合计超上限（>8）→ 400 INVALID_PARAM",
       code == HTTP["BAD_REQUEST"] and error_code(body) == CODES["invalidParam"], f"got {code}")


# ═══ 7. 越权 / 隔离收尾 ═══

def isolation_segment(admin, viewer, outsider, out_ws, ws, admin_id):
    section("越权与项目隔离（新端点族成对）")
    proj = _pg_val("SELECT id FROM projects WHERE name = 'S4FLOW-FILE' "
                   "ORDER BY created_at DESC LIMIT 1")
    folder = _pg_val("SELECT id FROM file_folders WHERE project_id='%s' "
                     "ORDER BY created_at DESC LIMIT 1" % proj)
    asset = _pg_val(
        "SELECT id FROM file_assets WHERE project_id='%s' AND attributes->>'name'='S4FL-open.txt' "
        "ORDER BY created_at DESC LIMIT 1" % proj)
    out_proj = make_project(outsider, out_ws, "S4FLOW-OUTSIDE", "S4X")  # 对照：隔离非禁用

    pairs = [
        ("X-01", "GET", ENDPOINTS["folders"].format(ws=q(ws), proj=proj), HTTP["NOT_FOUND"]),
        ("X-02", "GET", ENDPOINTS["trash"].format(ws=q(ws), proj=proj), HTTP["NOT_FOUND"]),
        ("X-03", "GET", ENDPOINTS["storage"].format(ws=q(ws), proj=proj), HTTP["NOT_FOUND"]),
        ("X-04", "POST", ENDPOINTS["upload_sessions"].format(ws=q(ws), proj=proj),
         HTTP["NOT_FOUND"]),
        ("X-05", "GET", ENDPOINTS["gantt_rows"].format(ws=q(ws), proj=proj)
         + f"?viewport_start={dstr(-5)}&viewport_end={dstr(5)}", HTTP["NOT_FOUND"]),
        ("X-06", "GET", ENDPOINTS["gantt_unscheduled"].format(ws=q(ws), proj=proj),
         HTTP["NOT_FOUND"]),
        ("X-07", "GET", ENDPOINTS["gantt_overdue"].format(ws=q(ws), proj=proj),
         HTTP["NOT_FOUND"]),
        ("X-08", "GET", ENDPOINTS["file_versions"].format(ws=q(ws), proj=proj, asset=asset),
         HTTP["NOT_FOUND"]),
        ("X-09", "GET", ENDPOINTS["file_download"].format(ws=q(ws), proj=proj, asset=asset),
         HTTP["NOT_FOUND"]),
        ("X-10", "POST", ENDPOINTS["share_links"].format(ws=q(ws), proj=proj, asset=asset),
         HTTP["NOT_FOUND"]),
    ]
    for cid, method, url, expect_code in pairs:
        code, _b = outsider.req(method, url, {} if method == "POST" else None,
                                {"X-CSRFToken": outsider.csrf()} if method == "POST" else None)
        ck(cid, f"外部用户 {method} {url.split('/projects/')[0].split('/')[-1]} 族 → {expect_code}"
           f"（AUTH-003 隔离）", code == expect_code, f"got {code}")

    # VIEWER 写路径 403（folder.manage / file.upload / file.delete / purge）
    code, body = viewer.req("POST", ENDPOINTS["folders"].format(ws=q(ws), proj=proj),
                            {"name": "S4FL-越权目录"}, {"X-CSRFToken": viewer.csrf()})
    ck("X-11", "VIEWER 建目录 → 403 PERM_DENIED（folder.manage）",
       code == HTTP["FORBIDDEN"] and error_code(body) == CODES["permDenied"], f"got {code} {body}")
    code, body = viewer.req("POST", ENDPOINTS["folder_presign"].format(
        ws=q(ws), proj=proj, folder=folder),
        {"file_name": "v.png", "file_size": 10, "content_type": "image/png"},
        {"X-CSRFToken": viewer.csrf()})
    ck("X-12", "VIEWER presign → 403 PERM_DENIED（file.upload）",
       code == HTTP["FORBIDDEN"] and error_code(body) == CODES["permDenied"], f"got {code}")
    code, body = viewer.req("DELETE", ENDPOINTS["file_detail"].format(ws=q(ws), proj=proj, asset=asset),
                            None, {"X-CSRFToken": viewer.csrf()})
    ck("X-13", "VIEWER 软删文件 → 403 PERM_DENIED（file.delete）",
       code == HTTP["FORBIDDEN"] and error_code(body) == CODES["permDenied"], f"got {code}")
    code, body = viewer.req("DELETE", ENDPOINTS["file_purge"].format(ws=q(ws), proj=proj, asset=asset),
                            None, {"X-CSRFToken": viewer.csrf()})
    ck("X-14", "VIEWER purge → 403（仅 PROJ_ADMIN）",
       code == HTTP["FORBIDDEN"] and error_code(body) == CODES["permDenied"], f"got {code}")
    code, body = viewer.req("GET", ENDPOINTS["trash"].format(ws=q(ws), proj=proj))
    ck("X-15", "VIEWER 回收站 → 403 PERM_DENIED（file.delete 门槛）",
       code == HTTP["FORBIDDEN"] and error_code(body) == CODES["permDenied"], f"got {code}")

    # 未认证 401（公开分享端点除外——匿名是设计面）
    saved = list(admin.jar)
    admin.jar.clear()
    code, _b = admin.req("GET", ENDPOINTS["folders"].format(ws=q(ws), proj=proj))
    ck("X-16", "未认证 GET folders → 401", code == HTTP["UNAUTHORIZED"], f"got {code}")
    code, _b = admin.req("GET", ENDPOINTS["gantt_overdue"].format(ws=q(ws), proj=proj))
    ck("X-17", "未认证 GET overdue-summary → 401", code == HTTP["UNAUTHORIZED"], f"got {code}")
    admin.jar.clear()
    for c_ in saved:
        admin.jar.set_cookie(c_)

    # 方法集（405）
    code, _b = admin.req("PUT", ENDPOINTS["folders"].format(ws=q(ws), proj=proj),
                         {"name": "x"}, {"X-CSRFToken": admin.csrf()})
    ck("X-18", "PUT folders/ → 405 MethodNotAllowed", code == 405, f"got {code}")
    anon_probe = Client(BASE)
    code, _b = anon_probe.req("POST", ENDPOINTS["public_content"].format(slug="A" * 22), {},
                              {"X-CSRFToken": anon_probe.csrf()})
    ck("X-19", "公开 content 不接受 POST → 405（匿名只读基线）", code == 405, f"got {code}")
    code, _b = outsider.req("GET", ENDPOINTS["folders"].format(ws=q(out_ws), proj=out_proj))
    ck("X-20", "外部用户在自有项目 folders → 200（X-01 的 404 确系项目隔离）",
       code == HTTP["OK"], f"got {code}")


if __name__ == "__main__":
    sys.exit(main())
