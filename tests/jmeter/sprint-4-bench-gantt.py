#!/usr/bin/env python3
"""Sprint-4 GANTT-001 性能门禁基准（IT-01/IT-02/IT-07，GANTT-001 §5.2 / BR-13）。

用法：python3 tests/jmeter/sprint-4-bench-gantt.py [http://localhost:8000]
前置：API + 真实 PG（rp-pg 容器）在跑。计时采样走 HTTP（与 sprint-2/3-bench
同口径）；EXPLAIN 断言用进程内 django.setup 捕获真实 SQL 后经 psql 执行
（argv 长度上限——SQL 走 stdin）。

门禁（任一不过 exit 1；每门禁 3 轮各 50 次采样，逐轮报 P95，最差轮判定）：
  G1 首屏门禁（IT-01/BR-13）：单项目 1 万任务 / 5 年跨度，首屏 =
     GET gantt/ 视窗行（60 行/页）+ POST gantt/relations/bulk/ 合并 P95 < 1.5s；
  G2 平移预取门禁（IT-02/BR-13）：同数据集连续平移 10 视窗循环采样，
     单次预取 P95 < 300ms（绝不全量拉取：视窗外行不进响应）；
  G3 索引命中（IT-07）：10k 数据集上 EXPLAIN (ANALYZE, BUFFERS) 视窗行查询，
     断言 idx_issue_gantt_viewport 出现在计划中（Index Scan 直扫或经 BitmapOr
     合并的 Bitmap Index Scan 均算——按索引名断言，不锁扫描形态）；
  附带健全性：unscheduled_count == 灌入的纯未排期数（双 NULL 且子树无日期），
     聚合条父（双 NULL 子树有日期）计入 rows 不计入未排期。

数据集（前缀 S4GZ / 用户 s4gz*@rabbit.dev）：
  10,100 任务 = 9,800 条 5 年跨度排期任务（跨 1826 天均匀分布，工期 1~15 天）
              + 200 条双 NULL（其中 50 条为聚合条父，各带 2 条排期子任务）
  1,000 行 issue_links（450 blocks 对 + 50 relates_to 对，成对正向+镜像）。
跑完清理并复查库内零残留（幂等可重跑）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
BENCH = "s4gz"

# ── 进程内 Django（与在跑 api 同库；仅为 EXPLAIN 的 SQL 捕获，不写业务数据） ──
os.environ.setdefault("DATABASE_URL", "postgresql://rp:rp@localhost:5432/rabbit_projects")
os.environ.setdefault("SECRET_KEY", "dev")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CELERY_BROKER_URL", "amqp://guest:guest@localhost:5672//")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "plane.settings.dev")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "apps", "api"))
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _contract import Client, HTTP, q  # noqa: E402

import django  # noqa: E402

django.setup()

from django.db import connection  # noqa: E402
from django.test.utils import CaptureQueriesContext  # noqa: E402

PSQL = ["docker", "exec", "-i", "rp-pg", "psql", "-U", "rp", "-d", "rabbit_projects",
        "-v", "ON_ERROR_STOP=1"]

FAILURES: list[str] = []
GATE_ROWS: list[tuple[str, str, str]] = []  # (门禁, 实测, 结论)

#: 数据集日期基线：今天 - 2.5 年（5 年跨度 ≈ 1826 天覆盖）
BASE_DATE = time.strftime("%Y-%m-%d", time.localtime(time.time() - 365 * 2.5 * 86400))
SPAN_DAYS = 1826
N_SCHEDULED = 9800
N_UNSCHED = 150     # 纯未排期（双 NULL 且子树无日期）
N_AGG_PARENTS = 50  # 聚合条父（双 NULL，子树有日期）——不计入未排期
N_TOTAL = N_SCHEDULED + N_UNSCHED + N_AGG_PARENTS + N_AGG_PARENTS * 2  # 10100


def psql(sql: str, *, unaligned: bool = False) -> str:
    """SQL 走 stdin（docker exec argv 长度上限，同 sprint-3-bench）。"""
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


def purge_bench_data():
    """幂等清理：s4gz-%/S4GZ-% 项目 + s4gz* 用户（历史失败 run 残留一并回收）。"""
    psql(f"""
    BEGIN;
    CREATE TEMP TABLE sp AS SELECT id FROM projects
      WHERE name LIKE '{BENCH}-%' OR name LIKE '{BENCH.upper()}-%';
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
    DELETE FROM issue_types WHERE workspace_id IN
      (SELECT id FROM workspaces WHERE created_by_id IN (SELECT id FROM users WHERE email LIKE '{BENCH}%@rabbit.dev'));
    DELETE FROM workspace_member_invites WHERE workspace_id IN
      (SELECT id FROM workspaces WHERE created_by_id IN (SELECT id FROM users WHERE email LIKE '{BENCH}%@rabbit.dev'));
    DELETE FROM workspace_members WHERE workspace_id IN
      (SELECT id FROM workspaces WHERE created_by_id IN (SELECT id FROM users WHERE email LIKE '{BENCH}%@rabbit.dev'));
    DELETE FROM workspaces WHERE created_by_id IN (SELECT id FROM users WHERE email LIKE '{BENCH}%@rabbit.dev');
    DELETE FROM users WHERE email LIKE '{BENCH}%@rabbit.dev';
    DROP TABLE sp;
    COMMIT;
    """)


def issue_uuid(g: str) -> str:
    """确定性 UUID（lpad 12 hex）——参数为 SQL 表达式文本（'g' / 'a + 1' …），
    links/父子引用无需 RETURNING 往返。"""
    return f"('5e400000-0000-4000-8000-' || lpad(to_hex(100000 + ({g})), 12, '0'))::uuid"


def seed_dataset(proj: str, actor: str, state_todo: str, state_done: str) -> None:
    # 主批次：10,000 行（9,800 排期 + 150 纯未排期 + 50 聚合父）
    psql(
        "INSERT INTO issues (id, project_id, name, description_json, description_html, "
        " priority, sequence_id, sort_order, start_date, target_date, estimate_minutes, "
        " custom_fields, state_id, created_by_id, created_at, updated_at, attachment_count, github_context) "
        "SELECT " + issue_uuid("g") + ", '" + proj + "', '" + BENCH + "-g' || g, '{}'::jsonb, '<p></p>', "
        " (ARRAY['urgent','high','medium','low','none'])[1 + g % 5], "
        " g, (g * 100.0), "
        " CASE WHEN g % 50 = 0 THEN NULL "                      # 200 行双 NULL（含 50 聚合父）
        "      ELSE DATE '" + BASE_DATE + "' + (g % " + str(SPAN_DAYS) + ") END, "
        " CASE WHEN g % 50 = 0 THEN NULL "
        "      ELSE DATE '" + BASE_DATE + "' + (g % " + str(SPAN_DAYS) + ") + 1 + (g % 14) END, "
        " CASE WHEN g % 3 = 0 THEN 60 * (1 + g % 16) ELSE NULL END, "
        " '{}'::jsonb, "
        " CASE WHEN g % 4 = 0 THEN '" + state_done + "'::uuid ELSE '" + state_todo + "'::uuid END, "
        " '" + actor + "', now() - ((g % 20000) || ' minutes')::interval, now(), 0, '{}'::jsonb "
        "FROM generate_series(1, 10000) g")
    # 聚合父的子任务：50 父 × 2 子（g=200k，子 g=10000+c / 10050+c）
    psql(
        "INSERT INTO issues (id, project_id, name, description_json, description_html, "
        " priority, sequence_id, sort_order, parent_id, start_date, target_date, "
        " custom_fields, state_id, created_by_id, created_at, updated_at, attachment_count, github_context) "
        "SELECT " + issue_uuid("10000 + c") + ", '" + proj + "', '" + BENCH + "-agg-c' || c, '{}'::jsonb, '<p></p>', "
        " 'medium', 10000 + c, (10000 + c) * 100.0, "
        " " + issue_uuid("(CASE WHEN c <= 50 THEN c ELSE c - 50 END) * 200") + ", "
        " DATE '" + BASE_DATE + "' + (((CASE WHEN c <= 50 THEN c ELSE c - 50 END) * 200) % " + str(SPAN_DAYS) + ")"
        "   + CASE WHEN c <= 50 THEN 0 ELSE 5 END, "
        " DATE '" + BASE_DATE + "' + (((CASE WHEN c <= 50 THEN c ELSE c - 50 END) * 200) % " + str(SPAN_DAYS) + ")"
        "   + CASE WHEN c <= 50 THEN 0 ELSE 5 END + 10, "
        " '{}'::jsonb, '" + state_todo + "'::uuid, '" + actor + "', now(), now(), 0, '{}'::jsonb "
        "FROM generate_series(1, 100) c")
    # 连线：450 blocks 对 + 50 relates_to 对（成对正向 + 镜像行，TASK-005 存储形态）
    psql(
        "INSERT INTO issue_links (id, issue_id, related_issue_id, relation_type, created_by_id, created_at, updated_at) "
        "SELECT gen_random_uuid(), " + issue_uuid("a") + ", " + issue_uuid("a + 1") + ", "
        " CASE WHEN k % 9 = 0 THEN 'relates_to' ELSE 'blocks' END, "
        " '" + actor + "'::uuid, now(), now() "
        "FROM generate_series(1, 500) k, LATERAL (SELECT (k * 19 + 25) AS a) x "
        "UNION ALL "
        "SELECT gen_random_uuid(), " + issue_uuid("a + 1") + ", " + issue_uuid("a") + ", "
        " CASE WHEN k % 9 = 0 THEN 'relates_to' ELSE 'is_blocked_by' END, "
        " '" + actor + "'::uuid, now(), now() "
        "FROM generate_series(1, 500) k, LATERAL (SELECT (k * 19 + 25) AS a) x")
    psql("ANALYZE issues; ANALYZE issue_links;")  # 新灌数据补统计


def main() -> int:
    print("═══ 数据集准备（幂等：先清旧 S4GZ 数据，再建号）═══")
    purge_bench_data()  # 必须先清后建——清理按 s4gz% 邮箱前缀匹配

    admin = Client(BASE)
    ts = int(time.time() * 1000) % 100000000
    email = f"{BENCH}{ts}@rabbit.dev"
    code, body = admin.req("POST", "/api/v1/auth/sign-up/",
                           {"email": email, "password": "Rabbit123!", "display_name": "S4 Gantt Bench"},
                           {"X-CSRFToken": admin.csrf()})
    if code != HTTP["CREATED"]:
        print(f"前置失败 sign-up {code} {body}")
        return 1
    ws = body["data"]["default_workspace_slug"]
    admin_id = admin.req("GET", "/api/v1/users/me/")[1]["data"]["user"]["id"]

    code, body = admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/",
                           {"name": f"{BENCH}-GANTT", "identifier": "S4G"},
                           {"X-CSRFToken": admin.csrf()})
    if code != HTTP["CREATED"]:
        print(f"前置失败 create project {code} {body}")
        return 1
    proj = body["data"]["id"]
    _, b = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/states/?include_cancelled=1")
    todo_state = next(x["id"] for x in b["data"] if x["group"] == "unstarted")
    done_state = next(x["id"] for x in b["data"] if x["group"] == "completed")

    print(f"  灌数据：{N_TOTAL} 任务 / 5 年跨度（{BASE_DATE} 起 {SPAN_DAYS} 天）/ 1000 连线行 …")
    seed_dataset(proj, admin_id, todo_state, done_state)

    gantt = f"/api/v1/workspaces/{q(ws)}/projects/{proj}/gantt/"
    vp_start = time.strftime("%Y-%m-%d")
    vp_end = time.strftime("%Y-%m-%d", time.localtime(time.time() + 30 * 86400))

    # ── 健全性（数据集就绪 + 视窗取数正确性锚点）──
    code, body = admin.req("GET", f"{gantt}?viewport_start={vp_start}&viewport_end={vp_end}")
    assert code == HTTP["OK"], (code, body)
    rows = body["data"]["rows"]
    unscheduled = body["data"]["unscheduled_count"]
    agg = [r for r in rows if r["is_aggregated"]]
    gate_check("健全性：首屏 rows 非空 + 视窗裁剪生效（total < 全量）",
               0 < body["meta"]["total_count"] < N_TOTAL,
               f"viewport rows={len(rows)} total={body['meta']['total_count']} 全量={N_TOTAL}")
    gate_check("健全性：unscheduled_count = 150（双 NULL 且子树无日期）",
               unscheduled == N_UNSCHED, f"got {unscheduled}")
    code, u_body = admin.req("GET", f"{gantt}unscheduled/")
    gate_check("健全性：unscheduled/ 列表与计数一致（IT-06 口径）",
               u_body["meta"]["total_count"] == unscheduled,
               f"list={u_body['meta']['total_count']} count={unscheduled}")

    # ═══ G1 首屏门禁：rows + relations bulk 合并 P95 < 1.5s ×3 轮 ═══
    print("\n═══ G1 首屏门禁（IT-01：1 万任务/5 年，首屏 rows+edges 50 次 ×3 轮 P95<1.5s）═══")

    def first_screen() -> None:
        c, r = admin.req("GET", f"{gantt}?viewport_start={vp_start}&viewport_end={vp_end}")
        assert c == HTTP["OK"], (c, r)
        ids = [row["id"] for row in r["data"]["rows"]]
        c2, e = admin.req("POST", f"{gantt}relations/bulk/", {"issue_ids": ids},
                          {"X-CSRFToken": admin.csrf()})
        assert c2 == HTTP["OK"], (c2, e)

    rounds_g1 = [timed(first_screen) for _ in range(3)]
    for i, p in enumerate(rounds_g1, 1):
        print(f"    第 {i} 轮 P95 = {p:.1f}ms")
    gate("G1 首屏（rows+edges）", max(rounds_g1), 1500, "（3 轮最差）")
    _, e_body = admin.req("POST", f"{gantt}relations/bulk/",
                          {"issue_ids": [row["id"] for row in rows]},
                          {"X-CSRFToken": admin.csrf()})
    gate_check("健全性：首屏页内连线非零（连线仅视窗内求值）",
               0 < e_body["meta"]["edges"] <= 60, f"edges={e_body['meta']['edges']}")

    # ═══ G2 平移预取门禁：连续平移 10 视窗 P95 < 300ms ×3 轮 ═══
    print("\n═══ G2 平移预取门禁（IT-02：连续平移 10 视窗循环，单次 P95<300ms ×3 轮）═══")
    pans = []
    for k in range(10):
        s = time.strftime("%Y-%m-%d", time.localtime(time.time() + (k + 1) * 61 * 86400))
        e = time.strftime("%Y-%m-%d", time.localtime(time.time() + (k + 1) * 61 * 86400 + 30 * 86400))
        pans.append((s, e))
    pan_rounds: list[float] = []
    for _round in range(3):
        samples: list[float] = []
        for i in range(50):
            s, e = pans[i % 10]
            t0 = time.perf_counter()
            c, r = admin.req("GET", f"{gantt}?viewport_start={s}&viewport_end={e}")
            dt = (time.perf_counter() - t0) * 1000
            assert c == HTTP["OK"], (c, r)
            samples.append(dt)
        pan_rounds.append(p95(samples))
    for i, p in enumerate(pan_rounds, 1):
        print(f"    第 {i} 轮 P95 = {p:.1f}ms")
    gate("G2 平移预取", max(pan_rounds), 300, "（3 轮最差）")

    # ═══ G3 索引命中：10k 数据集 EXPLAIN (ANALYZE, BUFFERS) ═══
    print("\n═══ G3 索引命中（IT-07：EXPLAIN 断言 idx_issue_gantt_viewport，不锁扫描形态）═══")
    from django.test import RequestFactory

    from plane.app.views.gantt import GanttRowsView
    from plane.db.models import User

    dj_user = User.objects.get(email=email)
    rf = RequestFactory()
    req = rf.get(f"/api/v1/workspaces/{ws}/projects/{proj}/gantt/"
                 f"?viewport_start={vp_start}&viewport_end={vp_end}")
    req.user = dj_user
    with CaptureQueriesContext(connection) as ctx:
        resp = GanttRowsView.as_view()(req, slug=ws, project_id=proj)
        assert resp.status_code == 200
    rows_sql = next(
        sq["sql"] for sq in ctx.captured_queries
        if sq["sql"].startswith("SELECT")
        and 'ORDER BY "issues"."sort_order"' in sq["sql"]  # 行窗口主查询（BR-11 行序）
    )
    plan = psql("EXPLAIN (ANALYZE, BUFFERS) " + rows_sql)
    gate_check("G3 视窗行查询命中 idx_issue_gantt_viewport",
               "idx_issue_gantt_viewport" in plan,
               f"计划含索引名={('idx_issue_gantt_viewport' in plan)}")
    print("    计划摘要：" + " | ".join(
        ln.strip() for ln in plan.splitlines() if "gantt" in ln or "Execution Time" in ln)[:300])

    # ═══ 清理与复查 ═══
    print("\n═══ 清理与复查 ═══")
    purge_bench_data()
    residue = psql(
        f"""SELECT (SELECT count(*) FROM issues WHERE name LIKE '{BENCH}-%')
                  + (SELECT count(*) FROM projects WHERE name LIKE '{BENCH}-%')
                  + (SELECT count(*) FROM users WHERE email LIKE '{BENCH}%@rabbit.dev')""",
        unaligned=True)
    gate_check("清理复查：库内零 S4GZ 残留", residue.strip() == "0", f"residue={residue.strip()}")

    print("\n════════════════════════════════════")
    print(f"{'门禁':<28}{'实测':<14}结论")
    for name, got, verdict in GATE_ROWS:
        print(f"{name:<30}{got:<16}{verdict}")
    if FAILURES:
        print(f"\n失败 {len(FAILURES)} 项：")
        for f in FAILURES:
            print(f"  ✗ {f}")
        return 1
    print("全部通过 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
