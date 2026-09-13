#!/usr/bin/env python3
"""Sprint-4 性能门禁基准（GANTT-001/002 + FILE-002，T4-13）。

用法：python3 tests/jmeter/sprint-4-bench.py [http://localhost:8000]
前置：API + 真实 PG（rp-pg）+ Redis + MinIO 在跑（文件库轻探针需一次真实直传）。

门禁（任一不过 exit 1；计时采样走 HTTP，与 sprint-2/3-bench 同口径）：
  G1 首屏门禁（GANTT-001 IT-01/BR-13）：单项目 1 万任务 / 5 年跨度，
     rows + relations/bulk 合并 P95 < 1.5s（50 次 ×3 轮，最差轮判定）；
  G2 平移预取门禁（IT-02/BR-13）：连续平移 10 视窗循环采样 P95 < 300ms ×3 轮；
  G3 索引命中（IT-07）：10k 数据集 EXPLAIN (ANALYZE, BUFFERS) 视窗行查询
     命中 idx_issue_gantt_viewport；
  FB 文件库门禁（FILE-002 IT-07：万级目录浏览 P95 < 300ms）：12k 文件 /
     520 目录五层树 → folders 树端点 + 万级单目录列表 + 筛选（name / type），
     三探针各 50 次 ×3 轮，最差轮 P95 < 300ms。
  —— G1/G2/G3 直接并入 T4-06 交付的 sprint-4-bench-gantt.py（importlib 复用
     其数据集构造与幂等清理，避免双源漂移）。

分享/预览轻门禁（FILE-004 §5 / FILE-003 §5 未定数值）：只报数不设门禁——
share meta / content 预览 / download 302 / preview dispatch 各采样 30 次给出
P95 备查，规格定数后收编为门禁（报告注明）。

数据集（s4bench 前缀 / 用户 s4bench*@rabbit.dev）：
  文件库：520 目录五层树（8/32/96/192/192）+ 12,000 文件
    （10,000 集中单目录 —— IT-07「万级单目录」口径；2,000 分布叶层）；
  甘特：s4gz 数据集由 sprint-4-bench-gantt 自带（10,100 任务 / 1,000 连线）。
跑完清理并复查库内零残留（幂等可重跑；bench 后清数据——CLAUDE.md 纪律）。
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
BENCH = "s4bench"

# ── 进程内 Django（sprint-4-bench-gantt import 时即完成 setup；同库同 env）──
os.environ.setdefault("DATABASE_URL", "postgresql://rp:rp@localhost:5432/rabbit_projects")
os.environ.setdefault("SECRET_KEY", "dev")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CELERY_BROKER_URL", "amqp://guest:guest@localhost:5672//")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "plane.settings.dev")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "apps", "api"))
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _contract import ENDPOINTS, HTTP, Client, q  # noqa: E402

PSQL = ["docker", "exec", "-i", "rp-pg", "psql", "-U", "rp", "-d", "rabbit_projects",
        "-v", "ON_ERROR_STOP=1"]

FAILURES: list[str] = []
GATE_ROWS: list[tuple[str, str, str]] = []  # (门禁, 实测, 结论)


def psql(sql: str, *, unaligned: bool = False) -> str:
    args = PSQL + (["-At"] if unaligned else [])
    r = subprocess.run(args, input=sql, capture_output=True, text=True, timeout=900)  # noqa: S603
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:400])
    return r.stdout


def p95(samples: list[float]) -> float:
    return sorted(samples)[max(0, int(len(samples) * 0.95) - 1)]


def timed(fn, n: int = 50, warm: int = 3) -> float:
    for _ in range(warm):
        fn()
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return p95(samples)


def timed_rounds(fn, rounds: int = 3, n: int = 50) -> list[float]:
    """跑 3 轮各 n 次采样 → 逐轮 P95（门禁取最差轮）。"""
    return [timed(fn, n=n, warm=3) for _ in range(rounds)]


def gate(name: str, got_ms: float, limit_ms: float, extra: str = "") -> bool:
    ok = got_ms < limit_ms
    print(f"  {'✓' if ok else '✗'} {name}: P95 {got_ms:.1f}ms（门禁 <{limit_ms}ms）{extra}")
    GATE_ROWS.append((name, f"{got_ms:.1f}ms", "PASS" if ok else "FAIL"))
    if not ok:
        FAILURES.append(f"{name} P95 {got_ms:.1f}ms ≥ {limit_ms}ms")
    return ok


def gate_check(name: str, ok: bool, detail: str) -> bool:
    print(f"  {'✓' if ok else '✗'} {name}: {detail}")
    GATE_ROWS.append((name, detail, "PASS" if ok else "FAIL"))
    if not ok:
        FAILURES.append(f"{name} {detail}")
    return ok


def report_only(name: str, samples_ms: list[float]) -> None:
    """轻门禁：规格未定数值 → 只报 P95 不设判定（任务书口径）。"""
    print(f"  ○ {name}: P95 {p95(samples_ms):.1f}ms / 中位 "
          f"{sorted(samples_ms)[len(samples_ms) // 2]:.1f}ms（未设门禁——规格 §5 无数值）")
    GATE_ROWS.append((name, f"P95 {p95(samples_ms):.1f}ms", "REPORT"))


# ═══ 段一：GANTT 门禁（并入 T4-06 交付的 sprint-4-bench-gantt.py）═══

def run_gantt_gates() -> bool:
    """sprint-4-bench-gantt.py 复用执行：优先进程内 import（venv 解释器下），
    系统 python3 无 django 时降级为其 venv 解释器子进程（输出直通、退出码判定）。"""
    print("═══ 段一 GANTT-001/002 门禁（sprint-4-bench-gantt.py 并入执行）═══")
    mod_path = pathlib.Path(__file__).with_name("sprint-4-bench-gantt.py")
    try:
        spec = importlib.util.spec_from_file_location("s4_bench_gantt", mod_path)
        gantt_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gantt_mod)  # noqa: PN120 —— 模块级 django.setup() 即段一依赖
        rc = gantt_mod.main()
        GATE_ROWS.extend(gantt_mod.GATE_ROWS)
        FAILURES.extend(gantt_mod.FAILURES)
    except ModuleNotFoundError as exc:
        if exc.name != "django":
            raise
        venv_py = (pathlib.Path(__file__).resolve().parents[2] / "apps" / "api" / ".venv"
                   / "bin" / "python")
        if not venv_py.exists():
            print(f"  ✗ 段一前置失败：当前解释器无 django 且未找到 {venv_py}")
            FAILURES.append("段一：无 django 解释器可用")
            return False
        print(f"  · 系统 python3 无 django → 以 {venv_py.name} 子进程执行段一")
        proc = subprocess.run(  # noqa: S603 —— 仓库内固定脚本 + 显式解释器
            [str(venv_py), str(mod_path), BASE], timeout=580)
        rc = proc.returncode
        GATE_ROWS.append(("G1/G2/G3（段一子进程）", "见上方输出", "PASS" if rc == 0 else "FAIL"))
    if rc != 0:
        print(f"  ✗ 段一退出码 {rc}")
        FAILURES.append(f"段一（sprint-4-bench-gantt）退出码 {rc}")
        return False
    print("  ✓ 段一全部通过（G1/G2/G3 + 健全性 + 清理复查）")
    return True


# ═══ 段二：FILE-002 文件库门禁 ═══

#: 五层树形状：L1×8 → L2×4 → L3×3 → L4×2 → L5×1（520 目录）
TREE_SHAPE = ((8, None), (4, 1), (3, 2), (2, 3), (1, 4))
N_BIG_FOLDER = 10_000   # 万级单目录（IT-07 口径）
N_SPREAD = 2_000        # 分布于叶层（目录树计数数据面）


def purge_bench_data():
    psql(f"""
    BEGIN;
    CREATE TEMP TABLE sp AS SELECT id FROM projects WHERE name LIKE '{BENCH.upper()}-%';
    CREATE TEMP TABLE sa AS SELECT id FROM file_assets WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM file_share_accesses WHERE share_id IN
      (SELECT id FROM file_share_links WHERE asset_id IN (SELECT id FROM sa));
    DELETE FROM file_share_links WHERE asset_id IN (SELECT id FROM sa);
    DELETE FROM file_versions WHERE asset_id IN (SELECT id FROM sa);
    DELETE FROM upload_sessions WHERE asset_id IN (SELECT id FROM sa);
    DELETE FROM file_assets WHERE id IN (SELECT id FROM sa);
    DELETE FROM file_folders WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM issues WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM issue_views WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM states WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM project_members WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM project_favorites WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM projects WHERE id IN (SELECT id FROM sp);
    DELETE FROM issue_types WHERE workspace_id IN
      (SELECT id FROM workspaces WHERE created_by_id IN
        (SELECT id FROM users WHERE email LIKE '{BENCH}%@rabbit.dev'));
    DELETE FROM workspace_member_invites WHERE workspace_id IN
      (SELECT id FROM workspaces WHERE created_by_id IN
        (SELECT id FROM users WHERE email LIKE '{BENCH}%@rabbit.dev'));
    DELETE FROM workspace_members WHERE workspace_id IN
      (SELECT id FROM workspaces WHERE created_by_id IN
        (SELECT id FROM users WHERE email LIKE '{BENCH}%@rabbit.dev'));
    DELETE FROM workspaces WHERE created_by_id IN
      (SELECT id FROM users WHERE email LIKE '{BENCH}%@rabbit.dev');
    DELETE FROM users WHERE email LIKE '{BENCH}%@rabbit.dev';
    DROP TABLE sp; DROP TABLE sa;
    COMMIT;
    """)


def _seed_tree_sql(proj: str, actor: str) -> list[str]:
    """五层目录树（8 → 32 → 96 → 192 → 192 = 520 目录）逐层 INSERT；
    id 用确定性十六进制段（6e41~6e45 前缀），父子引用零往返。"""
    stmts: list[str] = []
    stmts.append(f"""
    INSERT INTO file_folders (id, project_id, parent_id, name, visibility, allowed_members,
                              created_by_id, updated_by_id, created_at, updated_at)
    SELECT ('6e410000-0000-4000-8000-' || lpad(to_hex(1000 + n), 12, '0'))::uuid,
           '{proj}'::uuid, NULL, 'S4B-L1-' || n, 'all', '[]'::jsonb,
           '{actor}'::uuid, '{actor}'::uuid, now(), now()
      FROM generate_series(1, 8) n""")
    # L2：8 父 × 4 = 32
    stmts.append(f"""
    INSERT INTO file_folders (id, project_id, parent_id, name, visibility, allowed_members,
                              created_by_id, updated_by_id, created_at, updated_at)
    SELECT ('6e420000-0000-4000-8000-' || lpad(to_hex(2000 + (p - 1) * 4 + c), 12, '0'))::uuid,
           '{proj}'::uuid,
           ('6e410000-0000-4000-8000-' || lpad(to_hex(1000 + p), 12, '0'))::uuid,
           'S4B-L2-' || p || '-' || c, 'all', '[]'::jsonb,
           '{actor}'::uuid, '{actor}'::uuid, now(), now()
      FROM generate_series(1, 8) p, generate_series(1, 4) c""")
    # L3：32 父 × 3 = 96
    stmts.append(f"""
    INSERT INTO file_folders (id, project_id, parent_id, name, visibility, allowed_members,
                              created_by_id, updated_by_id, created_at, updated_at)
    SELECT ('6e430000-0000-4000-8000-' || lpad(to_hex(3000 + (p - 1) * 3 + c), 12, '0'))::uuid,
           '{proj}'::uuid,
           ('6e420000-0000-4000-8000-' || lpad(to_hex(2000 + p), 12, '0'))::uuid,
           'S4B-L3-' || p || '-' || c, 'all', '[]'::jsonb,
           '{actor}'::uuid, '{actor}'::uuid, now(), now()
      FROM generate_series(1, 32) p, generate_series(1, 3) c""")
    # L4：96 父 × 2 = 192
    stmts.append(f"""
    INSERT INTO file_folders (id, project_id, parent_id, name, visibility, allowed_members,
                              created_by_id, updated_by_id, created_at, updated_at)
    SELECT ('6e440000-0000-4000-8000-' || lpad(to_hex(4000 + (p - 1) * 2 + c), 12, '0'))::uuid,
           '{proj}'::uuid,
           ('6e430000-0000-4000-8000-' || lpad(to_hex(3000 + p), 12, '0'))::uuid,
           'S4B-L4-' || p || '-' || c, 'all', '[]'::jsonb,
           '{actor}'::uuid, '{actor}'::uuid, now(), now()
      FROM generate_series(1, 96) p, generate_series(1, 2) c""")
    # L5：192 父 × 1 = 192
    stmts.append(f"""
    INSERT INTO file_folders (id, project_id, parent_id, name, visibility, allowed_members,
                              created_by_id, updated_by_id, created_at, updated_at)
    SELECT ('6e450000-0000-4000-8000-' || lpad(to_hex(5000 + p), 12, '0'))::uuid,
           '{proj}'::uuid,
           ('6e440000-0000-4000-8000-' || lpad(to_hex(4000 + p), 12, '0'))::uuid,
           'S4B-L5-' || p, 'all', '[]'::jsonb,
           '{actor}'::uuid, '{actor}'::uuid, now(), now()
      FROM generate_series(1, 192) p""")
    return stmts


def seed_files(proj: str, actor: str, big_folder: str) -> None:
    """10,000 文件集中单目录 + 2,000 分布 192 叶层（image/document 混合供筛选探针）。"""
    psql(f"""
    INSERT INTO file_assets (id, workspace_id, project_id, entity_type, entity_id, folder_id,
                             attributes, size, storage_path, status, is_uploaded,
                             uploaded_by_id, visibility, allowed_members, dlp_hits, download_count,
                             created_by_id, updated_by_id, created_at, updated_at)
    SELECT gen_random_uuid(),
           (SELECT workspace_id FROM projects WHERE id = '{proj}'), '{proj}'::uuid,
           'project_file', '{big_folder}'::uuid, '{big_folder}'::uuid,
           jsonb_build_object('name', 'S4B-big-' || lpad(g::text, 5, '0') ||
                              CASE WHEN g % 3 = 0 THEN '.png' ELSE '.txt' END,
                              'size', 2048,
                              'mime', CASE WHEN g % 3 = 0 THEN 'image/png' ELSE 'text/plain' END,
                              'ext', CASE WHEN g % 3 = 0 THEN '.png' ELSE '.txt' END),
           2048, 's4bench/big/' || g, 'uploaded', true,
           '{actor}'::uuid, 'all', '[]'::jsonb, '[]'::jsonb, 0,
           '{actor}'::uuid, '{actor}'::uuid, now(), now()
      FROM generate_series(1, {N_BIG_FOLDER}) g""")
    psql(f"""
    INSERT INTO file_assets (id, workspace_id, project_id, entity_type, entity_id, folder_id,
                             attributes, size, storage_path, status, is_uploaded,
                             uploaded_by_id, visibility, allowed_members, dlp_hits, download_count,
                             created_by_id, updated_by_id, created_at, updated_at)
    SELECT gen_random_uuid(),
           (SELECT workspace_id FROM projects WHERE id = '{proj}'), '{proj}'::uuid,
           'project_file',
           ('6e450000-0000-4000-8000-' || lpad(to_hex(5000 + 1 + ((g - 1) % 192)), 12, '0'))::uuid,
           ('6e450000-0000-4000-8000-' || lpad(to_hex(5000 + 1 + ((g - 1) % 192)), 12, '0'))::uuid,
           jsonb_build_object('name', 'S4B-leaf-' || lpad(g::text, 4, '0') || '.png',
                              'size', 1024, 'mime', 'image/png', 'ext', '.png'),
           1024, 's4bench/leaf/' || g, 'uploaded', true,
           '{actor}'::uuid, 'all', '[]'::jsonb, '[]'::jsonb, 0,
           '{actor}'::uuid, '{actor}'::uuid, now(), now()
      FROM generate_series(1, {N_SPREAD}) g""")
    psql("ANALYZE file_assets; ANALYZE file_folders;")


def minio_put(upload_url: str, data: bytes, content_type: str) -> bool:
    req = urllib.request.Request(
        "http://localhost:9000" + upload_url[len("/uploads"):], data=data, method="PUT",
        headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
            return resp.status in (200, 201)
    except Exception:  # noqa: BLE001
        return False


def run_file_library_gates() -> bool:
    print("\n═══ 段二 FILE-002 文件库门禁（12k 文件 / 520 目录五层树，P95<300ms）═══")
    purge_bench_data()  # 幂等：先清后建（清理按 s4bench% 前缀匹配）

    admin = Client(BASE)
    ts = int(time.time() * 1000) % 100000000
    email = f"{BENCH}-{ts}@rabbit.dev"
    code, body = admin.req("POST", "/api/v1/auth/sign-up/",
                           {"email": email, "password": "Rabbit123!", "display_name": "S4 File Bench"},
                           {"X-CSRFToken": admin.csrf()})
    if code != HTTP["CREATED"]:
        print(f"前置失败 sign-up {code} {body}")
        return False
    ws = body["data"]["default_workspace_slug"]
    admin_id = admin.req("GET", "/api/v1/users/me/")[1]["data"]["user"]["id"]
    code, body = admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/",
                           {"name": f"{BENCH.upper()}-FILES", "identifier": "S4B"},
                           {"X-CSRFToken": admin.csrf()})
    if code != HTTP["CREATED"]:
        print(f"前置失败 create project {code} {body}")
        return False
    proj = body["data"]["id"]

    print(f"  灌数据：{N_BIG_FOLDER} 文件集中单目录 + {N_SPREAD} 分布叶层 / 520 目录五层树 …")
    for stmt in _seed_tree_sql(proj, admin_id):
        psql(stmt)
    # 万级单目录挂在 L1-1 下（列表端点主探针对象；L2 深度=2 不触 5 层上限）
    big_folder = psql(
        f"""INSERT INTO file_folders (id, project_id, parent_id, name, visibility,
              allowed_members, created_by_id, updated_by_id, created_at, updated_at)
            VALUES ('6e460000-0000-4000-8000-000000000001', '{proj}'::uuid,
                    ('6e410000-0000-4000-8000-' || lpad(to_hex(1001), 12, '0'))::uuid,
                    'S4B-万级单目录', 'all', '[]'::jsonb, '{admin_id}'::uuid,
                    '{admin_id}'::uuid, now(), now())
            RETURNING id""", unaligned=True).splitlines()[0].strip()
    seed_files(proj, admin_id, big_folder)

    n_folders = psql(f"SELECT count(*) FROM file_folders WHERE project_id='{proj}'",
                     unaligned=True).strip()
    n_files = psql(f"SELECT count(*) FROM file_assets WHERE project_id='{proj}'",
                   unaligned=True).strip()
    gate_check("FB 前置：数据集就绪", n_folders == "521" and n_files == str(N_BIG_FOLDER + N_SPREAD),
               f"folders={n_folders}（520 树 + 1 万级单目录） files={n_files}")

    folders_url = ENDPOINTS["folders"].format(ws=q(ws), proj=proj)
    files_url = ENDPOINTS["folder_files"].format(ws=q(ws), proj=proj, folder=big_folder)

    # 健全性：列表首屏形状（万级单目录翻页口径）
    code, body = admin.req("GET", files_url)
    meta = (body or {}).get("meta") or {}
    gate_check("FB 健全性：万级单目录列表 total_count=10000 + 首页 50 行",
               code == HTTP["OK"] and meta.get("total_count") == N_BIG_FOLDER
               and len((body or {}).get("data") or []) == 50,
               f"total={meta.get('total_count')} page={len((body or {}).get('data') or [])}")

    rounds_tree = timed_rounds(lambda: admin.req("GET", folders_url))
    for i, p in enumerate(rounds_tree, 1):
        print(f"    第 {i} 轮 P95 = {p:.1f}ms")
    gate("FB-1 目录树端点（520 目录 + 12k 文件计数）", max(rounds_tree), 300, "（3 轮最差）")

    rounds_list = timed_rounds(lambda: admin.req("GET", files_url))
    for i, p in enumerate(rounds_list, 1):
        print(f"    第 {i} 轮 P95 = {p:.1f}ms")
    gate("FB-2 万级单目录列表（10k 行首页）", max(rounds_list), 300, "（3 轮最差）")

    rounds_name = timed_rounds(lambda: admin.req("GET", files_url + "?name=S4B-big-0999"))
    for i, p in enumerate(rounds_name, 1):
        print(f"    第 {i} 轮 P95 = {p:.1f}ms")
    gate("FB-3 筛选 ?name=（trgm 命中）", max(rounds_name), 300, "（3 轮最差）")

    rounds_type = timed_rounds(lambda: admin.req("GET", files_url + "?type=image&per_page=100"))
    for i, p in enumerate(rounds_type, 1):
        print(f"    第 {i} 轮 P95 = {p:.1f}ms")
    gate("FB-4 筛选 ?type=image（类别分支）", max(rounds_type), 300, "（3 轮最差）")

    # 翻页第二页（游标路径同门禁口径）
    cur = meta.get("next_cursor")
    rounds_page2 = timed_rounds(lambda: admin.req("GET", files_url + f"?cursor={q(cur)}"))
    gate("FB-5 游标翻页次页（offset 50）", max(rounds_page2), 300, "（3 轮最差）")

    return True


# ═══ 段三：分享 / 预览轻门禁（只报数不设门禁）═══

def run_share_preview_probes() -> None:
    print("\n═══ 段三 FILE-004 分享 / FILE-003 预览轻门禁（规格 §5 未定数值 → 只报数）═══")
    admin = Client(BASE)
    ts = int(time.time() * 1000) % 100000000
    email = f"{BENCH}sp-{ts}@rabbit.dev"
    code, body = admin.req("POST", "/api/v1/auth/sign-up/",
                           {"email": email, "password": "Rabbit123!", "display_name": "S4 SP Probe"},
                           {"X-CSRFToken": admin.csrf()})
    if code != HTTP["CREATED"]:
        print(f"前置失败 sign-up {code} {body}")
        return
    ws = body["data"]["default_workspace_slug"]
    code, body = admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/",
                           {"name": f"{BENCH.upper()}-SHARE", "identifier": "S4P"},
                           {"X-CSRFToken": admin.csrf()})
    if code != HTTP["CREATED"]:
        print(f"前置失败 create project {code} {body}")
        return
    proj = body["data"]["id"]
    folder = admin.req("POST", ENDPOINTS["folders"].format(ws=q(ws), proj=proj),
                       {"name": "S4B-分享"}, {"X-CSRFToken": admin.csrf()})[1]["data"]["id"]

    # 一次真实直传（预览通道需要 current_version 与真实对象）
    presign = ENDPOINTS["folder_presign"].format(ws=q(ws), proj=proj, folder=folder)
    code, body = admin.req("POST", presign, {"file_name": "S4B-share.txt", "file_size": 16,
                                             "content_type": "text/plain"},
                           {"X-CSRFToken": admin.csrf()})
    d = (body or {}).get("data") or {}
    if code != HTTP["CREATED"] or not minio_put(d.get("upload_url") or "", b"bench share body",
                                                "text/plain"):
        print("  ✗ 前置失败：直传不可用——轻门禁探针跳过")
        return
    asset = admin.req("POST", ENDPOINTS["file_complete"].format(ws=q(ws), proj=proj, asset=d["asset_id"]),
                      {}, {"X-CSRFToken": admin.csrf()})[1]["data"]["id"]
    link = admin.req("POST", ENDPOINTS["share_links"].format(ws=q(ws), proj=proj, asset=asset),
                     {"permission": "download"}, {"X-CSRFToken": admin.csrf()})[1]["data"]
    anon = Client(BASE)

    def sample(fn, n: int = 30) -> list[float]:
        fn()
        out = []
        for _ in range(n):
            t0 = time.perf_counter()
            fn()
            out.append((time.perf_counter() - t0) * 1000)
        return out

    report_only("share meta（匿名元信息）",
                sample(lambda: anon.req("GET", ENDPOINTS["public_share"].format(slug=link["slug"]))))
    report_only("share content 预览态（匿名直签）",
                sample(lambda: anon.req("GET", ENDPOINTS["public_content"].format(slug=link["slug"]))))
    report_only("share download 302（预签名换发）",
                sample(lambda: anon.get_no_redirect(
                    ENDPOINTS["public_content"].format(slug=link["slug"]) + "?download=1")))
    report_only("preview dispatch（text 通道）",
                sample(lambda: admin.req("GET", ENDPOINTS["file_preview"].format(
                    ws=q(ws), proj=proj, asset=asset))))
    report_only("download-url（内部下载预签名）",
                sample(lambda: admin.req("GET", ENDPOINTS["file_download"].format(
                    ws=q(ws), proj=proj, asset=asset))))


# ═══ 主流程 ═══

def main() -> int:
    ok_gantt = run_gantt_gates()
    ok_files = run_file_library_gates()
    run_share_preview_probes()

    print("\n═══ 清理与复查 ═══")
    purge_bench_data()
    residue = psql(f"""
        SELECT (SELECT count(*) FROM file_assets WHERE storage_path LIKE 's4bench/%')
              + (SELECT count(*) FROM file_folders WHERE name LIKE 'S4B-%')
              + (SELECT count(*) FROM projects WHERE name LIKE '{BENCH.upper()}-%')
              + (SELECT count(*) FROM users WHERE email LIKE '{BENCH}%@rabbit.dev')
              + (SELECT count(*) FROM issues WHERE name LIKE 's4gz-%')""",
        unaligned=True)
    gate_check("清理复查：库内零 s4bench/s4gz 残留", residue.strip() == "0",
               f"residue={residue.strip()}")

    print("\n════════════════════════════════════")
    print(f"{'门禁':<36}{'实测':<16}结论")
    for name, got, verdict in GATE_ROWS:
        print(f"{name:<38}{got:<18}{verdict}")
    if FAILURES:
        print(f"\n失败 {len(FAILURES)} 项：")
        for f in FAILURES:
            print(f"  ✗ {f}")
        return 1
    if not (ok_gantt and ok_files):
        return 1
    print("性能门禁全部通过 ✓（G1 首屏 / G2 平移 / G3 索引 / FB 文件库 ×5 探针）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
