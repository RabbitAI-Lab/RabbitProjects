#!/usr/bin/env python3
"""Sprint-4 验收演示数据准备（幂等可重跑）。

建「S4 验收演示」项目并铺齐 13 幕录屏所需数据：甘特行树（父/子/完成/取消/
进行中/逾期×5（张三 2 李四 2 王五 1）/开放端×2/未排期×4/连线四型（blocks 无
冲突 + 冲突红点 + relates_to + duplicates）/拖改与键盘演示位）、文件树（设计稿/
2026Q3 等目录）、五通道样本文件（真实 PNG / 手工合法 PDF / md / webm 视频 /
docx 排队态）、版本×3 文件、仅预览分享链接、三态权限样本（seed 全员态——幕 11
由 UI 现场改）、回收站样本；第二用户（李四，CONTRIBUTOR，密码固定，与 s3 同账
号）与第三用户（王五，逾期分布素材）。数据准备走 API（不属于被验收场面），录屏
场景全部从登录 UI 出发操作。

万级数据集（幕 ① 专用，bench 同款 SQL 构造，录完即清——不进常驻演示库）：
  python3 scripts/seed_acceptance_s4.py --gantt10k         # 建 10,100 任务/5 年/1000 连线
  python3 scripts/seed_acceptance_s4.py --gantt10k-clean   # 清理并复查零残留

用法：python3 scripts/seed_acceptance_s4.py [http://localhost:8000]
"""
from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, "tests/jmeter")
from _contract import Client, HTTP, q  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "http://localhost:8000"
TAG = "S4 验收演示"
IDENT = "S4AC"
WS = "workspace"
MINIO = "http://localhost:9000"
LISI = {"email": "lisi@rabbit.dev", "password": "Rabbit123!", "name": "李四"}
WANGWU = {"email": "wangwu@rabbit.dev", "password": "Rabbit123!", "name": "王五"}

# 视频样本：webm 取 sprint-3 验收产物（真实可播放流式视频；.webm 属官方流式白名单）
VIDEO_SRC = "docs/sprint-3-acceptance/videos/scene-02-四布局切换.webm"

# 真实 PNG：与 seed_acceptance_s3.py 同一份字节（Pillow 预生成——缩略图派生可成功）；
# 直接 import 复用，避免双源 base64 漂移
sys.path.insert(0, "scripts")
from seed_acceptance_s3 import PNG_B64  # noqa: E402

PSQL = ["docker", "exec", "-i", "rp-pg", "psql", "-U", "rp", "-d", "rabbit_projects",
        "-v", "ON_ERROR_STOP=1"]


def psql(sql: str, *, unaligned: bool = False) -> str:
    args = PSQL + (["-At"] if unaligned else [])
    r = subprocess.run(args, input=sql, capture_output=True, text=True, timeout=900)  # noqa: S603
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:400])
    return r.stdout


def d(offset_days: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time() + offset_days * 86400))


def put_minio(upload_path: str, content_type: str, body: bytes) -> str:
    """presign 返回 /uploads/<bucket>/<key>?sig → 直连 MinIO :9000 PUT（签名按 9000 端口
    计算）。返回 ETag（分片登记用；完整对象直传场景忽略返回值）。"""
    assert upload_path.startswith("/uploads/")
    url = MINIO + upload_path[len("/uploads"):]
    r = urllib.request.Request(url, data=body, method="PUT",
                               headers={"Content-Type": content_type})
    with urllib.request.urlopen(r, timeout=60) as resp:
        assert resp.status == 200, (resp.status, url)
        return resp.headers.get("ETag", "")


def ensure_user(admin: Client, who: dict) -> Client:
    """幂等：已有则登录；没有则邀请（admin）→ 注册（自动接受）→ 返回其会话。"""
    c = Client(BASE)
    code, _ = c.req("POST", "/api/v1/auth/sign-in/",
                    {"email": who["email"], "password": who["password"]},
                    {"X-CSRFToken": c.csrf()})
    if code == HTTP["OK"]:
        return c
    admin.req("POST", f"/api/v1/workspaces/{q(WS)}/invitations/",
              {"emails": [who["email"]], "role": 10},
              {"X-CSRFToken": admin.csrf()})
    code, body = c.req("POST", "/api/v1/auth/sign-up/",
                       {"email": who["email"], "password": who["password"],
                        "display_name": who["name"]},
                       {"X-CSRFToken": c.csrf()})
    assert code == HTTP["CREATED"], (who["email"], code, body)
    return c


def build_pdf(title: str) -> bytes:
    """手工构造最小合法 PDF（含 xref——Chromium 内建 pdfium 可直接渲染）。"""
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
    ]
    stream = f"BT /F1 20 Tf 72 720 Td ({title}) Tj ET\nBT /F1 12 Tf 72 690 Td (Sprint-4 acceptance sample) Tj ET\n".encode()
    objs.append(b"<</Length " + str(len(stream)).encode() + b">>stream\n" + stream + b"endstream")
    objs.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj".encode() + body + b"endobj\n"
    xref_at = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (b"trailer<</Size " + str(len(objs) + 1).encode() + b"/Root 1 0 R>>\n"
            b"startxref\n" + str(xref_at).encode() + b"\n%%EOF\n")
    return bytes(out)


def purge_demo() -> None:
    """幂等清场：按 identifier 硬删旧 S4 验收项目（含文件/分享/版本/会话全域）。"""
    psql(f"""
    BEGIN;
    CREATE TEMP TABLE sp AS SELECT id FROM projects WHERE identifier = '{IDENT}';
    DELETE FROM file_share_accesses WHERE share_id IN
      (SELECT id FROM file_share_links WHERE asset_id IN
        (SELECT id FROM file_assets WHERE project_id IN (SELECT id FROM sp)));
    DELETE FROM file_share_links WHERE asset_id IN
      (SELECT id FROM file_assets WHERE project_id IN (SELECT id FROM sp));
    DELETE FROM file_versions WHERE asset_id IN
      (SELECT id FROM file_assets WHERE project_id IN (SELECT id FROM sp));
    DELETE FROM upload_sessions WHERE asset_id IN
      (SELECT id FROM file_assets WHERE project_id IN (SELECT id FROM sp));
    DELETE FROM file_assets WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM file_folders WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM issue_activities WHERE issue_id IN
      (SELECT id FROM issues WHERE project_id IN (SELECT id FROM sp));
    DELETE FROM issue_comments WHERE issue_id IN
      (SELECT id FROM issues WHERE project_id IN (SELECT id FROM sp));
    DELETE FROM issue_links WHERE issue_id IN
      (SELECT id FROM issues WHERE project_id IN (SELECT id FROM sp));
    DELETE FROM work_logs WHERE issue_id IN
      (SELECT id FROM issues WHERE project_id IN (SELECT id FROM sp));
    DELETE FROM issue_assignees WHERE issue_id IN
      (SELECT id FROM issues WHERE project_id IN (SELECT id FROM sp));
    DELETE FROM issue_labels WHERE issue_id IN
      (SELECT id FROM issues WHERE project_id IN (SELECT id FROM sp));
    DELETE FROM issues WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM issue_views WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM states WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM labels WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM custom_field_definitions WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM project_members WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM project_favorites WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM projects WHERE id IN (SELECT id FROM sp);
    DROP TABLE sp;
    COMMIT;
    """)


def main() -> None:
    admin = Client(BASE)
    code, body = admin.req("POST", "/api/v1/auth/sign-in/",
                           {"email": "zhangsan@rabbit.dev", "password": "Rabbit123"},
                           {"X-CSRFToken": admin.csrf()})
    assert code == HTTP["OK"], (code, body)
    zhangsan_id = admin.req("GET", "/api/v1/users/me/")[1]["data"]["user"]["id"]

    lisi_c = ensure_user(admin, LISI)
    wangwu_c = ensure_user(admin, WANGWU)
    lisi_id = lisi_c.req("GET", "/api/v1/users/me/")[1]["data"]["user"]["id"]
    wangwu_id = wangwu_c.req("GET", "/api/v1/users/me/")[1]["data"]["user"]["id"]

    print("── 幂等清场：硬删旧 S4 验收项目（SQL，避开软删残留坑 #21）")
    purge_demo()

    code, body = admin.req("POST", f"/api/v1/workspaces/{q(WS)}/projects/",
                           {"name": TAG, "identifier": IDENT},
                           {"X-CSRFToken": admin.csrf()})
    assert code == HTTP["CREATED"], (code, body)
    proj = body["data"]["id"]
    base = f"/api/v1/workspaces/{q(WS)}/projects/{proj}"

    st = admin.req("GET", base + "/states/?include_cancelled=1")[1]["data"]
    groups = {s["group"]: s["id"] for s in st}

    # ── 成员：李四 CONTRIBUTOR（幕 11/13 双视口）；王五 CONTRIBUTOR（幕 4 逾期分布）──
    for uid in (lisi_id, wangwu_id):
        code, b = admin.req("POST", base + "/members/", {"member_ids": [uid], "role": 15},
                            {"X-CSRFToken": admin.csrf()})
        assert code in (HTTP["OK"], HTTP["CREATED"]), (code, b)

    # ═══ 甘特域种子（幕 1~6、13）══════════════════════════════════
    def mk(name, parent=None, **f):
        path = base + (f"/issues/{parent}/sub-issues/" if parent else "/issues/")
        c3, b3 = admin.req("POST", path, {"name": name, **f}, {"X-CSRFToken": admin.csrf()})
        assert c3 == HTTP["CREATED"], (name, c3, b3)
        return b3["data"]

    def set_dates(issue_id, start=None, target=None):
        payload = {}
        if start is not None:
            payload["start_date"] = start
        if target is not None:
            payload["target_date"] = target
        c4, b4 = admin.req("PATCH", f"{base}/issues/{issue_id}/", payload,
                           {"X-CSRFToken": admin.csrf()})
        assert c4 == HTTP["OK"], (issue_id, c4, b4)

    def assign(issue_id, ids):
        c5, b5 = admin.req("PUT", f"{base}/issues/{issue_id}/assignees/",
                           {"assignee_ids": ids}, {"X-CSRFToken": admin.csrf()})
        assert c5 == HTTP["OK"], (issue_id, c5, b5)

    def rel(from_id, to_id, relation_type):
        c6, b6 = admin.req("POST", f"{base}/issues/{from_id}/relations/",
                           {"related_issue_id": to_id, "relation_type": relation_type},
                           {"X-CSRFToken": admin.csrf()})
        assert c6 == HTTP["CREATED"], (relation_type, c6, b6)

    # 行树：父（聚合条）+ 子（完成划线）；七态条素材
    parent = mk("导出 PDF（里程碑）")
    child = mk("后端导出 API", parent=parent["id"], state_id=groups["completed"],
               target_date=d(-1))
    set_dates(child["id"], start=d(-5))
    cancelled = mk("遗留方案", state_id=groups["cancelled"], target_date=d(2))
    set_dates(cancelled["id"], start=d(-1))
    started = mk("登录会话延长", state_id=groups["started"], target_date=d(6))
    set_dates(started["id"], start=d(-2))

    # 逾期 ×5：张三 2 / 李四 2 / 王五 1（幕 4 三数字 + 按人分布；最长 5 天）
    overdue_z1 = mk("修复 504 超时", state_id=groups["started"], target_date=d(-3))
    set_dates(overdue_z1["id"], start=d(-8))
    assign(overdue_z1["id"], [zhangsan_id])
    overdue_z2 = mk("环境变量文档", target_date=d(-1))  # 开放左端条逾期（同源口径）
    assign(overdue_z2["id"], [zhangsan_id])
    overdue_l1 = mk("移动端适配回归", target_date=d(-5))  # 最长逾期 5 天
    set_dates(overdue_l1["id"], start=d(-9))
    assign(overdue_l1["id"], [lisi_id])
    overdue_l2 = mk("文案走查", target_date=d(-2))
    set_dates(overdue_l2["id"], start=d(-6))
    assign(overdue_l2["id"], [lisi_id])
    overdue_w1 = mk("依赖升级评估", target_date=d(-1))
    set_dates(overdue_w1["id"], start=d(-3))
    assign(overdue_w1["id"], [wangwu_id])

    # 开放端 ×2
    open_end = mk("水印设计")
    set_dates(open_end["id"], start=d(1), target=None)
    open_start = mk("旧归档清理", target_date=d(12))  # start 空 → 左端开放

    # 连线四型：blocks 无冲突 / blocks 冲突（红点）/ relates_to / duplicates
    blocker = mk("连读 A", target_date=d(9))
    set_dates(blocker["id"], start=d(5))
    blocked = mk("连读 B", target_date=d(14))
    set_dates(blocked["id"], start=d(11))
    rel(blocker["id"], blocked["id"], "blocks")
    conflict_a = mk("冲读 A", target_date=d(4))
    set_dates(conflict_a["id"], start=d(-2))
    conflict_b = mk("冲读 B", target_date=d(9))
    set_dates(conflict_b["id"], start=d(-4))  # B 起 -4d < A 终 +4d → violation 红点
    rel(conflict_a["id"], conflict_b["id"], "blocks")
    relates = mk("相关读", target_date=d(6))
    set_dates(relates["id"], start=d(2))
    rel(started["id"], relates["id"], "relates_to")
    dup = mk("重复读", target_date=d(8))
    set_dates(dup["id"], start=d(3))
    rel(dup["id"], overdue_z1["id"], "duplicates")

    # 幕 2/3/5 演示位（前缀「排期演示-」，与展示行树区分）
    drag_target = mk("排期演示-平移目标", target_date=d(9))
    set_dates(drag_target["id"], start=d(4))
    pre = mk("排期演示-前置任务", target_date=d(6))
    set_dates(pre["id"], start=d(2))
    blocked2 = mk("排期演示-被阻塞任务", target_date=d(12))
    set_dates(blocked2["id"], start=d(8))
    rel(pre["id"], blocked2["id"], "blocks")
    kbd_target = mk("排期演示-键盘改期", target_date=d(7))
    set_dates(kbd_target["id"], start=d(3))
    mk("已排期锚点", target_date=d(5))  # 空轴无行可渲染（BR-03）→ 入轨幕的行锚
    mk("待入轨任务")
    mk("留下未排期")
    mk("图标规范整理")
    mk("移动端适配清单")

    # ═══ 文件域种子（幕 7~13）════════════════════════════════════
    def folder(name, parent_id=None):
        c7, b7 = admin.req("POST", base + "/folders/",
                           {"name": name, **({"parent_id": parent_id} if parent_id else {})},
                           {"X-CSRFToken": admin.csrf()})
        assert c7 == HTTP["CREATED"], (name, c7, b7)
        return b7["data"]["id"]

    def upload(folder_id, name, mime, blob):
        c8, b8 = admin.req("POST", f"{base}/folders/{folder_id}/files/presign/",
                           {"file_name": name, "file_size": len(blob), "content_type": mime},
                           {"X-CSRFToken": admin.csrf()})
        assert c8 == HTTP["CREATED"], (name, c8, b8)
        pres = b8["data"]
        put_minio(pres["upload_url"], mime, blob)
        c9, b9 = admin.req("POST", f"{base}/files/{pres['asset_id']}/complete/",
                           {}, {"X-CSRFToken": admin.csrf()})
        assert c9 == HTTP["OK"], (name, c9, b9)
        return pres["asset_id"]

    f_des = folder("设计稿")
    f_q3 = folder("2026Q3", f_des)
    folder("需求文档")
    f_perm = folder("权限样本")
    f_trash = folder("回收站样本")
    folder("大文件")
    f_out = folder("外发")
    f_collab = folder("协作")
    f_ver = folder("版本演示")

    def upload_chunked(folder_id, name, mime, blob):
        """分片会话通道（视频等扩展名仅此白名单可达——直传 presign 零回改，已知口径）：
        init → 逐片换发/PUT/登记（ETag+MD5）→ complete。"""
        c10, b10 = admin.req("POST", base + "/upload-sessions/",
                             {"file_name": name, "file_size": len(blob),
                              "folder_id": folder_id, "content_type": mime},
                             {"X-CSRFToken": admin.csrf()})
        assert c10 == HTTP["CREATED"], (name, c10, b10)
        sid = b10["data"]["session_id"]
        size = b10["data"]["chunk_size"]
        parts = [blob[i:i + size] for i in range(0, len(blob), size)] or [b""]
        for n, part in enumerate(parts, start=1):
            c11, b11 = admin.req("POST", f"{base}/upload-sessions/{sid}/chunks/{n}/",
                                 {}, {"X-CSRFToken": admin.csrf()})
            assert c11 == HTTP["OK"], (name, n, c11, b11)
            etag = put_minio(b11["data"]["upload_url"], "application/octet-stream", part)
            payload = {"etag": etag}
            if n < len(parts):
                payload["md5"] = hashlib.md5(part).hexdigest()
            c12, b12 = admin.req("PATCH", f"{base}/upload-sessions/{sid}/chunks/{n}/",
                                 payload, {"X-CSRFToken": admin.csrf()})
            assert c12 == HTTP["OK"], (name, n, c12, b12)
        c13, b13 = admin.req("POST", f"{base}/upload-sessions/{sid}/complete/",
                             {}, {"X-CSRFToken": admin.csrf()})
        assert c13 == HTTP["CREATED"], (name, c13, b13)
        return (b13["data"].get("file") or {}).get("id")

    # 五通道（幕 8）：图片/PDF/文本/视频 四通道实操 + Office 排队态（无 soffice）
    upload(f_q3, "需求说明.md", "text/markdown",
           "# Sprint-4 验收演示\n\n- 文本通道：Monaco 只读正文\n- 五通道之一\n\n正文第一行。".encode())
    upload(f_q3, "合同扫描.pdf", "application/pdf", build_pdf("RabbitProjects PDF Preview"))
    png_asset = upload(f_q3, "演示截图.png", "image/png", base64.b64decode(PNG_B64))
    try:
        with open(VIDEO_SRC, "rb") as fh:
            video_blob = fh.read()
    except OSError as exc:
        raise SystemExit(f"✗ 视频样本缺失：{VIDEO_SRC}（sprint-3 验收产物）——{exc}") from exc
    upload_chunked(f_q3, "产品演示录屏.webm", "video/webm", video_blob)
    upload(f_q3, "转码排队.docx", "application/msword",
           "PK\x03\x04office-placeholder（本机无 soffice → 202 排队态如实展示）".encode())

    # 版本 ×3（幕 9 回滚：v1 内容独特可断言还原）
    upload(f_ver, "方案.md", "text/markdown",
           "首页改版 Q3\n导航采用侧边栏方案\n视觉 token 延续 v2".encode())
    upload(f_ver, "方案.md", "text/markdown",
           "首页改版 Q3\n导航改为顶部 Tab 方案\n新增深色模式适配".encode())
    upload(f_ver, "方案.md", "text/markdown",
           "首页改版 Q3（v3 终稿）\n顶部 Tab + 面包屑\n埋点补齐曝光事件".encode())

    # 分享宿主（幕 10）+ 实时版本宿主（幕 13）
    out_asset = upload(f_out, "对外方案.md", "text/markdown",
                       "对外方案正文（分享链可达内容）\n第一段落：验收演示素材。".encode())
    upload(f_collab, "联调笔记.md", "text/markdown", "第一版内容".encode())

    # 三态权限样本（幕 11：seed 全员态，张三 UI 现场改 members/admins）
    upload(f_perm, "全员可见.md", "text/markdown", "项目全部成员可见".encode())
    perm_members = upload(f_perm, "指定成员-仅张三.md", "text/markdown",
                          "仅勾选成员与管理员可见".encode())
    perm_admins = upload(f_perm, "仅管理员-机密.md", "text/markdown",
                         "仅项目 ADMIN 可见".encode())

    # 回收站样本（幕 12）
    upload(f_trash, "待删除演示.md", "text/markdown", "回收站往返素材".encode())
    upload(f_trash, "邻居.txt", "text/plain", "stays".encode())

    # 仅预览分享链接（幕 10B：无密码永久 → 匿名直通 + 无下载按钮）
    c10, b10 = admin.req("POST", f"{base}/files/{out_asset}/share-links/",
                         {"permission": "view"},
                         {"X-CSRFToken": admin.csrf()})
    assert c10 == HTTP["CREATED"], (c10, b10)
    view_slug = b10["data"]["slug"]

    # ── 等真实 PNG 缩略就绪（worker PIL 派生；幕 8 图片通道即时可看）──
    deadline = time.time() + 40
    ready = False
    while time.time() < deadline and not ready:
        c11, b11 = admin.req("GET", f"{base}/files/{png_asset}/preview/")
        ready = c11 == HTTP["OK"] and (b11.get("data") or {}).get("ready") is True
        if not ready:
            time.sleep(1.0)
    if not ready:
        print("⚠ 图片缩略派生未就绪（worker 滞后）——幕 8 可能先见排队态")

    print(f"✓ 数据就绪：WS={WS} 项目={TAG}（{IDENT}，{proj}）")
    print(f"  李四 {LISI['email']}/{LISI['password']}（CONTRIBUTOR）｜王五 {WANGWU['email']}/{WANGWU['password']}")
    print(f"  幕 11 素材：{perm_members[:8]}…（现场改 members）/ {perm_admins[:8]}…（现场改 admins）")
    print(f"  幕 10 仅预览链接 slug：{view_slug}")
    print(f"  逾期 5（张三2 李四2 王五1）/ 最长 5 天；未排期 4；连线 5 组（blocks×3 含冲突 + relates + duplicates）")


# ═══ 幕 ① 万级数据集（bench 同款 SQL 构造；建/清分离，录完即清）════════════

G10K_NAME = "万级甘特演示"
G10K_IDENT = "S4ACG"
G10K_BASE_DATE = time.strftime("%Y-%m-%d", time.localtime(time.time() - 365 * 2.5 * 86400))
SPAN_DAYS = 1826


def purge_g10k() -> None:
    psql(f"""
    BEGIN;
    CREATE TEMP TABLE sp AS SELECT id FROM projects WHERE identifier = '{G10K_IDENT}';
    DELETE FROM issue_links WHERE issue_id IN (SELECT id FROM issues WHERE project_id IN (SELECT id FROM sp));
    DELETE FROM work_logs WHERE issue_id IN (SELECT id FROM issues WHERE project_id IN (SELECT id FROM sp));
    DELETE FROM issue_assignees WHERE issue_id IN (SELECT id FROM issues WHERE project_id IN (SELECT id FROM sp));
    DELETE FROM issue_labels WHERE issue_id IN (SELECT id FROM issues WHERE project_id IN (SELECT id FROM sp));
    DELETE FROM issues WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM issue_views WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM states WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM project_members WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM project_favorites WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM projects WHERE id IN (SELECT id FROM sp);
    DROP TABLE sp;
    COMMIT;
    """)


def gantt10k() -> None:
    purge_g10k()
    admin = Client(BASE)
    code, body = admin.req("POST", "/api/v1/auth/sign-in/",
                           {"email": "zhangsan@rabbit.dev", "password": "Rabbit123"},
                           {"X-CSRFToken": admin.csrf()})
    assert code == HTTP["OK"], (code, body)
    zhangsan_id = admin.req("GET", "/api/v1/users/me/")[1]["data"]["user"]["id"]
    code, body = admin.req("POST", f"/api/v1/workspaces/{q(WS)}/projects/",
                           {"name": G10K_NAME, "identifier": G10K_IDENT},
                           {"X-CSRFToken": admin.csrf()})
    assert code == HTTP["CREATED"], (code, body)
    proj = body["data"]["id"]
    _, b = admin.req("GET", f"/api/v1/workspaces/{q(WS)}/projects/{proj}/states/?include_cancelled=1")
    todo = next(x["id"] for x in b["data"] if x["group"] == "unstarted")
    done = next(x["id"] for x in b["data"] if x["group"] == "completed")
    actor = str(zhangsan_id)

    def uuid(expr: str) -> str:
        return f"('5e400000-0000-4000-8000-' || lpad(to_hex(100000 + ({expr})), 12, '0'))::uuid"

    # 主批次 10,000 行（9,800 排期 5 年跨度 + 150 纯未排期 + 50 聚合父）+ 100 聚合子 + 1000 连线行
    psql(
        "INSERT INTO issues (id, project_id, name, description_json, description_html, priority, "
        "sequence_id, sort_order, start_date, target_date, estimate_minutes, custom_fields, state_id, "
        "created_by_id, created_at, updated_at, attachment_count) "
        f"SELECT {uuid('g')}, '{proj}', 'S4ACG-g' || g, '{{}}'::jsonb, '<p></p>', "
        "(ARRAY['urgent','high','medium','low','none'])[1 + g % 5], g, (g * 100.0), "
        f"CASE WHEN g % 50 = 0 THEN NULL ELSE DATE '{G10K_BASE_DATE}' + (g % {SPAN_DAYS}) END, "
        f"CASE WHEN g % 50 = 0 THEN NULL ELSE DATE '{G10K_BASE_DATE}' + (g % {SPAN_DAYS}) + 1 + (g % 14) END, "
        "CASE WHEN g % 3 = 0 THEN 60 * (1 + g % 16) ELSE NULL END, '{}'::jsonb, "
        f"CASE WHEN g % 4 = 0 THEN '{done}'::uuid ELSE '{todo}'::uuid END, "
        f"'{actor}', now() - ((g % 20000) || ' minutes')::interval, now(), 0 "
        "FROM generate_series(1, 10000) g")
    psql(
        "INSERT INTO issues (id, project_id, name, description_json, description_html, priority, "
        "sequence_id, sort_order, parent_id, start_date, target_date, custom_fields, state_id, "
        "created_by_id, created_at, updated_at, attachment_count) "
        f"SELECT {uuid('10000 + c')}, '{proj}', 'S4ACG-agg-c' || c, '{{}}'::jsonb, '<p></p>', "
        "'medium', 10000 + c, (10000 + c) * 100.0, "
        f"{uuid('(CASE WHEN c <= 50 THEN c ELSE c - 50 END) * 200')}, "
        f"DATE '{G10K_BASE_DATE}' + (((CASE WHEN c <= 50 THEN c ELSE c - 50 END) * 200) % {SPAN_DAYS}) "
        " + CASE WHEN c <= 50 THEN 0 ELSE 5 END, "
        f"DATE '{G10K_BASE_DATE}' + (((CASE WHEN c <= 50 THEN c ELSE c - 50 END) * 200) % {SPAN_DAYS}) "
        " + CASE WHEN c <= 50 THEN 0 ELSE 5 END + 10, "
        f"'{{}}'::jsonb, '{todo}'::uuid, '{actor}', now(), now(), 0 "
        "FROM generate_series(1, 100) c")
    psql(
        "INSERT INTO issue_links (id, issue_id, related_issue_id, relation_type, created_by_id, created_at, updated_at) "
        f"SELECT gen_random_uuid(), {uuid('a')}, {uuid('a + 1')}, "
        "CASE WHEN k % 9 = 0 THEN 'relates_to' ELSE 'blocks' END, "
        f"'{actor}'::uuid, now(), now() "
        "FROM generate_series(1, 500) k, LATERAL (SELECT (k * 19 + 25) AS a) x "
        "UNION ALL "
        f"SELECT gen_random_uuid(), {uuid('a + 1')}, {uuid('a')}, "
        "CASE WHEN k % 9 = 0 THEN 'relates_to' ELSE 'is_blocked_by' END, "
        f"'{actor}'::uuid, now(), now() "
        "FROM generate_series(1, 500) k, LATERAL (SELECT (k * 19 + 25) AS a) x")
    psql("ANALYZE issues; ANALYZE issue_links;")
    n = psql(f"SELECT count(*) FROM issues WHERE project_id = '{proj}'", unaligned=True).strip()
    assert n == "10100", n
    print(json.dumps({"ok": True, "slug": WS, "pid": proj, "name": G10K_NAME, "issues": int(n)}))


def gantt10k_clean() -> None:
    purge_g10k()
    residue = psql(f"SELECT count(*) FROM projects WHERE identifier = '{G10K_IDENT}'", unaligned=True)
    assert residue.strip() == "0", residue
    print(json.dumps({"ok": True, "cleaned": G10K_IDENT}))


if __name__ == "__main__":
    if "--gantt10k" in sys.argv:
        gantt10k()
    elif "--gantt10k-clean" in sys.argv:
        gantt10k_clean()
    else:
        main()
