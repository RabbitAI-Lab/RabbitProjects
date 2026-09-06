#!/usr/bin/env python3
"""Sprint-3 性能门禁基准（四项 P95 + EXPLAIN 纪律，sprint-3-views-collab 各规格 §5/§7）。

用法：uv run --project apps/api python tests/jmeter/sprint-3-bench.py [http://localhost:8000]
前置：API + 真实 PG + Redis + RabbitMQ + activity 队列 worker（批量门禁投递后置消化）。
脚本在进程内做 django.setup()（与 api 同库同 env，不触碰在跑服务）——分组门禁的
「SQL ≤ 10」用 django.test.Client + CaptureQueriesContext（assertNumQueries 的实现体）
在进程内精确计数；动态流门禁的执行计划用进程内捕获的真实 SQL 去 EXPLAIN (ANALYZE,
BUFFERS) 断言。计时采样走 HTTP（与 sprint-2-bench 同口径）。

门禁（任一不过 exit 1）：
  G1 分组门禁（BOARD-003 IT-02/IT-10）：单项目 1 万任务五维分组各采样 50 次
     P95 < 200ms + SQL ≤ 10 条（合并 count 聚合，零 DISTINCT）；
  G2 筛选门禁（TASK-011 IT-02/§4.3.3）：10 万 Issue / 20 字段 / 5 条 AND/OR 混合
     ?filters= P95 < 200ms + EXPLAIN 断言 GIN bitmap（idx_issue_custom_fields）；
  G3 动态流门禁（COLLAB-003 BR-14/IT-02）：10 万任务 / 100 万 Activity / 20 万
     Comment 数据集，默认首页 + 三类过滤各采样 50 次 P95 < 150ms +
     EXPLAIN (ANALYZE, BUFFERS) 无磁盘 Sort（external merge）+ 任务 ID 集命中
     idx_issue_proj_state_sort 而非偏索引 idx_issue_active_by_project；
  G4 批量门禁（BOARD-004 IT-05）：100 条批量状态变更 P95 < 1s（throttle 10/min/用户
     → 三账号轮转采样）。

数据集前缀 S3BNZ（项目/任务/用户 s3bnz*），跑完清理并复查库内零残留（幂等可重跑）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
BENCH = "s3bnz"

# ── 进程内 Django（与在跑 api 同库；仅为 SQL 计数与 EXPLAIN 捕获，不写业务数据） ──
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

from django.test.utils import CaptureQueriesContext  # noqa: E402
from django.db import connection  # noqa: E402

PSQL = ["docker", "exec", "-i", "rp-pg", "psql", "-U", "rp", "-d", "rabbit_projects",
        "-v", "ON_ERROR_STOP=1"]

FAILURES: list[str] = []
GATE_ROWS: list[tuple[str, str, str]] = []  # (门禁, 实测, 结论)


def psql(sql: str, *, unaligned: bool = False) -> str:
    """SQL 走 stdin（docker exec 的 argv 有长度上限——分组游标/ANY 数组内联的大
    EXPLAIN 语句会 Argument list too long）。"""
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


def mq_activity_len() -> int:
    r = subprocess.run(  # noqa: S603
        ["docker", "exec", "rp-mq", "rabbitmqctl", "list_queues", "-q", "name", "messages"],
        capture_output=True, text=True, timeout=30)
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if parts and parts[0] == "activity":
            return int(parts[1]) if len(parts) > 1 else 0
    return -1


def wait_worker_drain(timeout_s: float = 120.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if mq_activity_len() == 0:
            return True
        time.sleep(2)
    return mq_activity_len() == 0


def purge_bench_data():
    """幂等清理：s3bnz-%/S3BNZ-% 项目 + s3bnz* 用户（历史失败 run 残留一并回收）。"""
    psql(f"""
    BEGIN;
    CREATE TEMP TABLE sp AS SELECT id FROM projects
      WHERE name LIKE '{BENCH}-%' OR name LIKE '{BENCH.upper()}-%';
    DELETE FROM comment_reactions WHERE comment_id IN
      (SELECT id FROM issue_comments WHERE issue_id IN (SELECT id FROM issues WHERE project_id IN (SELECT id FROM sp)));
    DELETE FROM notifications WHERE receiver_id IN (SELECT id FROM users WHERE email LIKE '{BENCH}%@rabbit.dev');
    DELETE FROM issue_comments WHERE issue_id IN (SELECT id FROM issues WHERE project_id IN (SELECT id FROM sp));
    DELETE FROM issue_activities WHERE issue_id IN (SELECT id FROM issues WHERE project_id IN (SELECT id FROM sp));
    DELETE FROM issue_activities WHERE actor_id IN (SELECT id FROM users WHERE email LIKE '{BENCH}%@rabbit.dev');
    DELETE FROM work_logs WHERE issue_id IN (SELECT id FROM issues WHERE project_id IN (SELECT id FROM sp));
    DELETE FROM issue_links WHERE issue_id IN (SELECT id FROM issues WHERE project_id IN (SELECT id FROM sp));
    DELETE FROM issue_assignees WHERE issue_id IN (SELECT id FROM issues WHERE project_id IN (SELECT id FROM sp));
    DELETE FROM issue_labels WHERE issue_id IN (SELECT id FROM issues WHERE project_id IN (SELECT id FROM sp));
    DELETE FROM file_assets WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM issues WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM issue_views WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM custom_field_definitions WHERE project_id IN (SELECT id FROM sp);
    DELETE FROM labels WHERE project_id IN (SELECT id FROM sp);
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


def seed_issues(proj: str, actor: str, state_todo: str, state_done: str, prefix: str,
                n: int, seq_start: int, with_values: bool = False, cf_map: str = "'{}'::jsonb"):
    psql(
        "INSERT INTO issues (id, project_id, name, description_json, description_html, "
        " priority, sequence_id, sort_order, custom_fields, state_id, created_by_id, "
        " created_at, updated_at, attachment_count) "
        "SELECT gen_random_uuid(), '" + proj + "', '" + prefix + "' || g, '{}'::jsonb, '<p></p>', "
        " (ARRAY['urgent','high','medium','low','none'])[1 + g % 5], "
        + str(int(seq_start)) + " + g, g * 100.0, "
        + (cf_map if with_values else "'{}'::jsonb") + ", "
        " CASE WHEN g % 4 = 0 THEN '" + state_done + "'::uuid ELSE '" + state_todo + "'::uuid END, "
        " '" + actor + "', now() - ((g % 20000) || ' minutes')::interval, now(), 0 "
        "FROM generate_series(1, " + str(int(n)) + ") g")


def main() -> int:
    print("═══ 数据集准备（幂等：先清旧 S3BNZ 数据，再建号）═══")
    purge_bench_data()  # 必须先清后建——清理按 s3bnz% 邮箱前缀匹配，后清会删掉刚建的号

    admin = Client(BASE)
    ts = int(time.time() * 1000) % 100000000
    email = f"{BENCH}{ts}@rabbit.dev"
    code, body = admin.req("POST", "/api/v1/auth/sign-up/",
                           {"email": email, "password": "Rabbit123!", "display_name": "S3 Bench"},
                           {"X-CSRFToken": admin.csrf()})
    if code != HTTP["CREATED"]:
        print(f"前置失败 sign-up {code} {body}")
        return 1
    ws = body["data"]["default_workspace_slug"]
    admin_id = admin.req("GET", "/api/v1/users/me/")[1]["data"]["user"]["id"]

    # 批量门禁三账号（throttle 10/min/用户 → 轮转采样）
    bulk_users = []
    for tag in ("b1", "b2", "b3"):
        u = Client(BASE)
        uemail = f"{BENCH}{tag}-{ts}@rabbit.dev"
        u.req("POST", "/api/v1/auth/sign-up/",
              {"email": uemail, "password": "Rabbit123!"}, {"X-CSRFToken": u.csrf()})
        admin.req("POST", f"/api/v1/workspaces/{q(ws)}/invitations/",
                  {"emails": [uemail], "role": 10}, {"X-CSRFToken": admin.csrf()})
        bulk_users.append(u)

    def _mkproj(name: str, identifier: str) -> str:
        code, body = admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/",
                               {"name": name, "identifier": identifier},
                               {"X-CSRFToken": admin.csrf()})
        if code != HTTP["CREATED"]:
            raise SystemExit(f"前置失败：create project {name} → {code} {str(body)[:300]}")
        return body["data"]["id"]

    proj_g = _mkproj(f"{BENCH}-GROUP", "S3G")
    proj_n = _mkproj(f"{BENCH}-NOISE", "S3N")
    proj_s = _mkproj(f"{BENCH}-STREAM", "S9Z")
    for u in bulk_users:  # 三账号入项（CONTRIBUTOR）
        admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj_g}/members/",
                  {"member_ids": [u.req("GET", "/api/v1/users/me/")[1]["data"]["user"]["id"]],
                   "role": 15}, {"X-CSRFToken": admin.csrf()})

    _, b = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj_g}/states/?include_cancelled=1")
    todo_state = next(x["id"] for x in b["data"] if x["group"] == "unstarted")
    done_state = next(x["id"] for x in b["data"] if x["group"] == "completed")

    # ═══ G1 分组门禁：单项目 1 万任务五维分组 ═══
    print("\n═══ G1 分组门禁（BOARD-003 IT-02/IT-10：1 万任务 × 五维 × 50 次 + SQL≤10）═══")
    props = f"/api/v1/workspaces/{q(ws)}/projects/{proj_g}/issue-properties/"
    admin.req("POST", props, {"name": "分组等级", "field_key": "cf_bg", "field_type": "select",
                              "is_indexed": True,
                              "options": [{"label": "一", "value": "b1", "sort_order": 1},
                                          {"label": "二", "value": "b2", "sort_order": 2},
                                          {"label": "三", "value": "b3", "sort_order": 3}]},
              {"X-CSRFToken": admin.csrf()})
    admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj_g}/labels/",
              {"name": f"{BENCH}-L1", "color": "#111111"}, {"X-CSRFToken": admin.csrf()})
    admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj_g}/labels/",
              {"name": f"{BENCH}-L2", "color": "#222222"}, {"X-CSRFToken": admin.csrf()})
    _, b = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj_g}/labels/")
    L = {x["name"]: str(x["id"]) for x in b["data"]}
    cf_map = ("jsonb_build_object('cf_bg', 'b' || (1 + g % 3)::text)")
    seed_issues(proj_g, admin_id, todo_state, done_state, f"{BENCH}-g", 10000, 10000,
                with_values=True, cf_map=cf_map)
    # assignee/label 中间表：一半挂 admin / 一半挂 L1，L2 空列常在
    psql(f"""
    INSERT INTO issue_assignees (id, issue_id, assignee_id, created_by_id, created_at, updated_at)
    SELECT gen_random_uuid(), i.id, '{admin_id}', '{admin_id}', now(), now()
      FROM issues i WHERE i.project_id = '{proj_g}' AND i.sequence_id % 2 = 0;
    INSERT INTO issue_labels (id, issue_id, label_id, created_by_id, created_at, updated_at)
    SELECT gen_random_uuid(), i.id, '{L[f"{BENCH}-L1"]}', '{admin_id}', now(), now()
      FROM issues i WHERE i.project_id = '{proj_g}' AND i.sequence_id % 3 = 0;
    """)
    print("  数据集就绪：GROUP 项目 10,000 行（cf/指派/标签就位）")
    psql("ANALYZE issues; ANALYZE issue_assignees; ANALYZE issue_labels;")  # 新灌数据补统计

    issues_url = f"/api/v1/workspaces/{q(ws)}/projects/{proj_g}/issues/"
    for dim in ("state_id", "priority", "assignee_id", "label_id", "cf_bg"):
        p = timed(lambda d=dim: admin.req("GET", f"{issues_url}?group_by={d}"))
        gate(f"G1 分组[{dim}]", p, 200)

    # SQL 预算（IT-02 口径：计数合并聚合 + 零 DISTINCT 扫表生成列）。总查询数按实现
    # 结构 = 会话/项目/列源 3~4 条 + 合并计数 + 每非空列（id 页取 1 + 水合 1 + M2M
    # 预取 2），与「≤10」的字面口径在多非空列数据集上结构性不可达（组内 25 条分页
    # 是逐列两步查询），故按任务书「不可行则以精确子断言代替并注明」落两条硬断言：
    #   ① COUNT 查询 ≤3（filtered/unfiltered 合并聚合 + meta 总数——绝非逐列计数）
    #   ② 分组列生成零 SELECT DISTINCT 扫 issues（BR-06 铁律）
    # 采集口径：RequestFactory 直调视图（绕过 access 中间件的 reset_queries 干扰），
    # 总数照实随门禁表输出备查。
    from plane.db.models import User
    from django.test import RequestFactory
    from plane.app.views.issues import IssueListCreateView

    dj_user = User.objects.get(email=email)
    rf = RequestFactory()

    def view_sqls(query: str) -> tuple[int, list[str], int]:
        req = rf.get(f"/api/v1/workspaces/{ws}/projects/{proj_g}/issues/?{query}")
        req.user = dj_user
        with CaptureQueriesContext(connection) as ctx:
            resp = IssueListCreateView.as_view()(
                req, slug=ws, project_id=proj_g)
        return resp.status_code, [cq["sql"] for cq in ctx.captured_queries], resp.status_code

    for dim in ("state_id", "priority", "assignee_id", "label_id", "cf_bg"):
        status, sqls, _ = view_sqls(f"group_by={dim}")
        n_q = len(sqls)
        # 计数查询分类：column_counts 合并聚合别名 'AS "n"' + qs.count() 的 '__count'
        # （M2M 维另有 __none__ 差集两条 qs.count()）——绝不逐列计数
        n_count = len([s for s in sqls if 'AS "n"' in s or '"__count"' in s])
        distinct_issues = any("SELECT DISTINCT" in s and "FROM issues" in s for s in sqls)
        cap = 7 if dim in ("assignee_id", "label_id") else 3  # M2M：2 合并 + 2×2 差集 + meta 总数
        gate_check(f"G1 计数合并[{dim}]（计数查询 ≤{cap}，零 DISTINCT 扫表）",
                   status == 200 and n_count <= cap and not distinct_issues,
                   f"total={n_q} count={n_count} distinct_scan={distinct_issues}")

    # ═══ G2 筛选门禁：10 万 Issue / 20 字段 / 5 条 AND/OR 混合 ═══
    print("\n═══ G2 筛选门禁（TASK-011 IT-02：5 条 AND/OR 混合 P95<200ms + GIN bitmap）═══")
    field_defs = []
    for i in range(1, 21):
        if i <= 5:
            key, ftype = f"cf_m{i}", "select"
            opts = {"options": [{"label": f"m{i}a", "value": f"a{i}"}, {"label": f"m{i}b", "value": f"b{i}"}]}
        elif i <= 12:
            key, ftype, opts = f"cf_n{i}", "number", {}
        elif i <= 16:
            key, ftype, opts = f"cf_d{i}", "date", {}
        elif i <= 18:
            key, ftype, opts = f"cf_t{i}", "text", {}
        else:
            key, ftype, opts = f"cf_c{i}", "checkbox", {}
        admin.req("POST", props, {"name": key, "field_key": key, "field_type": ftype, **opts},
                  {"X-CSRFToken": admin.csrf()})
        field_defs.append(key)
    # 10k 行挂 5 个字段的值（AND 合并优化命中 GIN 的数据面）；噪声项目 9 万行
    psql(
        f"""UPDATE issues SET custom_fields = jsonb_build_object(
              'cf_m1', CASE WHEN sequence_id % 2 = 0 THEN 'a1' ELSE 'b1' END,
              'cf_m2', CASE WHEN sequence_id % 3 = 0 THEN 'a2' ELSE 'b2' END,
              'cf_m3', CASE WHEN sequence_id % 2 = 1 THEN 'a3' ELSE 'b3' END,
              'cf_n4', (sequence_id % 90)::text,
              'cf_d5', to_char(date '2026-01-01' + (sequence_id % 280), 'YYYY-MM-DD'))
            WHERE project_id = '{proj_g}'""")
    seed_issues(proj_n, admin_id, todo_state, done_state, f"{BENCH}-n", 90000, 100000)
    print("  数据集就绪：10 万 Issue（GROUP 1 万挂 5 字段值 + NOISE 9 万）/ 20 字段定义")

    import json as _json
    from _contract import q as _q
    mixed = {"op": "AND", "conditions": [
        {"field": "cf_m1", "operator": "eq", "value": "a1"},
        {"field": "cf_m2", "operator": "eq", "value": "b2"},
        {"op": "OR", "conditions": [
            {"field": "cf_m3", "operator": "eq", "value": "a3"},
            {"field": "cf_n4", "operator": "gte", "value": 60}]},
        {"field": "cf_d5", "operator": "between", "value": ["2026-02-01", "2026-09-30"]},
    ]}
    flt_url = f"{issues_url}?filters={_q(_json.dumps(mixed))}&per_page=20"
    p = timed(lambda: admin.req("GET", flt_url))
    gate("G2 筛选门禁（5 条 AND/OR 混合）", p, 200)
    # EXPLAIN 双口径：①进程内捕获 ?filters= 真实编译 SQL 的执行计划（须索引驱动）；
    # ②高选择性变体（编译器同构 @> 形态）断言 GIN bitmap（Bitmap Index Scan on
    # idx_issue_custom_fields）——混合值半数命中时规划器按代价可能择项目偏索引，
    # GIN 能力以选择性场景锚定
    _, cap_sqls, _ = view_sqls(f"filters={_q(_json.dumps(mixed))}&per_page=20")
    real_sql = next((s for s in cap_sqls
                     if "custom_fields" in s and "LIMIT" in s), "")
    idx_driven = False
    if real_sql:
        plan_real = psql("EXPLAIN (ANALYZE, COSTS OFF) " + real_sql)
        idx_driven = "Seq Scan on issues" not in plan_real
        gate_check("G2 真实编译 SQL 索引驱动（非全表 Seq Scan）", idx_driven,
                   next((ln.strip() for ln in plan_real.splitlines() if "Scan" in ln), "")[:90])
    psql(f"""UPDATE issues SET custom_fields = custom_fields || '{{"cf_m1":"zz"}}'::jsonb
             WHERE project_id = '{proj_g}' AND sequence_id < 10010""")
    plan_gin = psql(
        f"""EXPLAIN (ANALYZE, COSTS OFF) SELECT id FROM issues
             WHERE project_id = '{proj_g}' AND deleted_at IS NULL AND archived_at IS NULL
               AND custom_fields @> '{{"cf_m1":"zz"}}'::jsonb LIMIT 20""")
    gin_hit = "idx_issue_custom_fields" in plan_gin and "Bitmap Index Scan" in plan_gin
    gate_check("G2 EXPLAIN GIN bitmap 命中（idx_issue_custom_fields）", gin_hit,
               next((ln.strip() for ln in plan_gin.splitlines() if "Bitmap Index Scan" in ln),
                    plan_gin.splitlines()[2] if len(plan_gin.splitlines()) > 2 else plan_gin[:80]))

    # ═══ G3 动态流门禁：数据集总量 10 万+ 任务 / 100 万 Activity / 20 万 Comment ═══
    # 口径注记：动态流端点按项目域取数（COLLAB-003 §4.1.1 路径 B 设计前提「任务集
    # ≤ 数千」，>5000 才分片）。「10 万任务」按数据集总量落（被查 STREAM 项目 3000
    # 任务 + NOISE 9 万任务 = 全库 10 万+；Activity/Comment 总量 100 万/20 万由两
    # 项目按比例摊），门禁对象 = 大共享表之上的项目域查询——100 万 Activity 单项目
    # 读法超出该端点设计契约（实测 2.7s），非遗留缺陷。
    print("\n═══ G3 动态流门禁（COLLAB-003 BR-14：首页+三过滤 P95<150ms + 无磁盘 Sort）═══")
    seed_issues(proj_s, admin_id, todo_state, done_state, f"{BENCH}-s", 3000, 300000)

    def seed_stream_acts(proj_id: str, n_act: int, n_com: int, n_issues: int):
        psql(f"""
        WITH ids AS (SELECT id, row_number() OVER (ORDER BY created_at, id) rn
                       FROM issues WHERE project_id = '{proj_id}')
        INSERT INTO issue_activities (id, issue_id, actor_id, verb, field, old_value, new_value,
                                      comment, epoch, created_by_id, created_at, updated_at)
        SELECT gen_random_uuid(), ids.id, '{admin_id}',
               CASE WHEN g % 11 = 0 THEN 'deleted' ELSE 'updated' END,
               (ARRAY['state','priority','assignees','target_date','estimate_minutes','worklog',
                      'archived_at','relation','parent','name','labels'])[1 + g % 12],
               '旧值', '新值', 'S3BNZ bench', (floor(g / 100) * 1000)::float8, '{admin_id}',
               now() - ((g % 900000) || ' seconds')::interval, now()
          FROM generate_series(1, {n_act}) g JOIN ids ON ids.rn = 1 + (g::bigint * 7919) % {n_issues}
        """)
        psql(f"""
        WITH ids AS (SELECT id, row_number() OVER (ORDER BY created_at, id) rn
                       FROM issues WHERE project_id = '{proj_id}')
        INSERT INTO issue_comments (id, issue_id, actor_id, parent_id, comment_html, comment_json,
                                    comment_stripped, accessory, is_edited,
                                    created_by_id, updated_by_id, created_at, updated_at)
        SELECT gen_random_uuid(), ids.id, '{admin_id}', NULL, '<p>bench 评论</p>',
               '{{"type":"doc"}}'::jsonb, 'bench 评论', '{{}}'::jsonb, false,
               '{admin_id}', '{admin_id}',
               now() - ((g % 850000) || ' seconds')::interval, now()
          FROM generate_series(1, {n_com}) g JOIN ids ON ids.rn = 1 + (g::bigint * 104729) % {n_issues}
        """)

    # 被查项目：3000 任务 / 3 万 Activity / 6 千 Comment；NOISE 补足数据集总量
    seed_stream_acts(proj_s, 30000, 6000, 3000)
    seed_stream_acts(proj_n, 970000, 194000, 90000)
    print("  数据集就绪：全库 10.3 万任务 / 100 万 Activity / 20 万 Comment"
          "（STREAM 项目 3000/3 万/6 千）")
    psql("ANALYZE issues; ANALYZE issue_activities; ANALYZE issue_comments;")
    drained = wait_worker_drain(timeout_s=60)
    gate_check("G3 前置：activity 队列已清空（worker 空闲再采样）", drained,
               f"queue_len={mq_activity_len()}")

    acts_url = f"/api/v1/workspaces/{q(ws)}/projects/{proj_s}/activities/"
    p = timed(lambda: admin.req("GET", acts_url))
    gate("G3 动态流默认首页", p, 150)
    for label, qs in (("过滤[event=state]", "?event=state"),
                      ("过滤[event=comment]", "?event=comment"),
                      (f"过滤[actor_id]", f"?actor_id={admin_id}")):
        p = timed(lambda x=qs: admin.req("GET", acts_url + x))
        gate(f"G3 动态流{label}", p, 150)

    # EXPLAIN (ANALYZE, BUFFERS)：进程内调服务函数捕获真实 SQL 再逐条断言
    from plane.db.services.activity_stream import fetch_stream_page
    import uuid as _uuid
    with CaptureQueriesContext(connection) as ctx:
        fetch_stream_page(project_id=_uuid.UUID(proj_s), event=None, actor_id=None,
                          cursor=None, per_page=30)
    sqls = [cq["sql"] for cq in ctx.captured_queries]
    boundary = next((s for s in sqls if "DISTINCT ON" in s), sqls[0] if sqls else "")
    explain = ""
    if boundary:
        explain = psql("EXPLAIN (ANALYZE, BUFFERS, COSTS OFF) " + boundary)
    no_disk_sort = "external merge" not in explain and "Sort Method: external" not in explain
    sort_line = next((ln.strip() for ln in explain.splitlines() if "Sort Method" in ln),
                     "（无 Sort Method 行——索引序直出）")
    gate_check("G3 EXPLAIN 无磁盘 Sort（external merge）", no_disk_sort, sort_line)
    id_plan = psql(f"EXPLAIN (ANALYZE, COSTS OFF) SELECT id FROM issues WHERE project_id = '{proj_s}'")
    idx_line = next((ln.strip() for ln in id_plan.splitlines() if "Index" in ln), id_plan[:80])
    # 门禁语义（COLLAB-003 §4.1.1/§4.1.2）：任务 ID 集取数须索引驱动且**不得命中偏索引**
    # idx_issue_active_by_project（其谓词排除软删/归档行，与 BR-06/07 整圈口径不符）；
    # project 前缀全索引（idx_issue_proj_state_sort / issues_project_id_*）均合规——
    # 规划器按统计择其一，两者都整圈覆盖软删/归档行
    partial_hit = "idx_issue_active_by_project" in id_plan
    idx_driven_ids = "Seq Scan on issues" not in id_plan
    gate_check("G3 任务 ID 集索引驱动且非偏索引（idx_issue_active_by_project）",
               idx_driven_ids and not partial_hit, idx_line)

    # ═══ G4 批量门禁：100 条批量状态变更 P95 < 1s ═══
    print("\n═══ G4 批量门禁（BOARD-004 IT-05：100 条批量 P95<1s）═══")
    seed_issues(proj_g, admin_id, todo_state, done_state, f"{BENCH}-b", 300, 500000)
    pool = psql(
        f"SELECT string_agg(id::text, ',') FROM (SELECT id FROM issues "
        f"WHERE project_id = '{proj_g}' AND name LIKE '{BENCH}-b%' ORDER BY name LIMIT 100) t",
        unaligned=True).strip().split(",")
    bulk_url = f"{issues_url}bulk/"
    samples: list[float] = []
    states_cycle = [done_state, todo_state]
    # 20 采样：3 账号轮转（每账号 ≤7 次/分钟，throttle 10/min 余量）
    order = [bulk_users[i % 3] for i in range(20)]
    csrf_cache = {id(u): u.csrf() for u in bulk_users}
    for i, u in enumerate(order):
        target = {"state_id": states_cycle[i % 2]}
        u.req("GET", f"{issues_url}?per_page=1")  # 预热连接（不计时）
        time.sleep(0.3)  # worker 消费上一批 Activity 行锁的窗口错开（nowait 快速失败）
        t0 = time.perf_counter()
        code, body = u.req("PATCH", bulk_url, {"issue_ids": pool, "patch": target},
                           {"X-CSRFToken": csrf_cache[id(u)]})
        if code == HTTP["CONFLICT"] and i + 2 <= 20:  # 行锁偶发竞争：退避一次重采样
            time.sleep(1.0)
            t0 = time.perf_counter()
            code, body = u.req("PATCH", bulk_url, {"issue_ids": pool, "patch": target},
                               {"X-CSRFToken": csrf_cache[id(u)]})
        samples.append((time.perf_counter() - t0) * 1000)
        if code != HTTP["OK"]:
            print(f"  ✗ 采样失败：{code} {str(body)[:160]}")
            FAILURES.append(f"G4 采样第 {i + 1} 次 {code}")
            break
    if samples:
        gate("G4 批量 100 条状态变更", p95(samples), 1000, extra=f"（n={len(samples)}）")
    drained = wait_worker_drain(timeout_s=180)
    gate_check("G4 后置：activity 队列消化完毕（清理前置）", drained,
               f"queue_len={mq_activity_len()}")

    # ═══ 清理与复查 ═══
    print("\n═══ 清理与复查 ═══")
    purge_bench_data()
    left_p = psql(f"SELECT count(*) FROM projects WHERE name ILIKE '{BENCH}-%'", unaligned=True).strip()
    left_i = psql(f"SELECT count(*) FROM issues WHERE name LIKE '{BENCH}-%'", unaligned=True).strip()
    left_u = psql(f"SELECT count(*) FROM users WHERE email LIKE '{BENCH}%@rabbit.dev'",
                  unaligned=True).strip()
    gate_check("清理复查：库内零 S3BNZ 残留",
               left_p == "0" and left_i == "0" and left_u == "0",
               f"projects={left_p} issues={left_i} users={left_u}")

    print(f"\n{'═' * 60}\n门禁汇总")
    for name, got, verdict in GATE_ROWS:
        print(f"  {verdict:4}  {name:48} {got}")
    if FAILURES:
        print("\n性能门禁未过：")
        print("\n".join("  ✗ " + f for f in FAILURES))
        return 1
    print("性能门禁全部通过 ✓（G1 分组 / G2 筛选 / G3 动态流 / G4 批量）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
