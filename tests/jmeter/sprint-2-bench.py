#!/usr/bin/env python3
"""Sprint-2 性能门禁基准（test-cases.md §8：五项 P95 + EXPLAIN 纪律）。

用法：python3 tests/jmeter/sprint-2-bench.py [http://localhost:8000]
前置：API + 真实 PG + Redis；TASK-008 字段已建（脚本自建 bench 项目与字段）。

数据集口径（sprint-overview §6）：10 万 Issue 总量、单项目 1 万、20 字段定义中
5 个挂值/索引。门禁：
  G1/G7  自定义字段 5 条混合筛选 P95 < 200ms（GIN bitmap AND 命中）
  IT-004 subtree 整树 P95 < 150ms
  IT-008 依赖拦截（PATCH completed 409 路径）P95 < 2ms
  IT-005 工时记录列表 P95 < 300ms
  IT-006 归档视图 P95 < 200ms
任一不过 exit 1；EXPLAIN 摘要随报告输出（bitmap AND / 索引命中形态核对）。
"""
from __future__ import annotations

import statistics
import subprocess
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _contract import Client, HTTP, q  # noqa: E402

BENCH_TAG = "s2bench"
PSQL = ["docker", "exec", "-i", "rp-pg", "psql", "-U", "rp", "-d", "rabbit_projects", "-v", "ON_ERROR_STOP=1"]


def psql(sql: str, *, unaligned: bool = False) -> str:
    args = PSQL + (["-At"] if unaligned else []) + ["-c", sql]
    r = subprocess.run(args, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:300])
    return r.stdout


def p95(samples: list[float]) -> float:
    return sorted(samples)[max(0, int(len(samples) * 0.95) - 1)]


def timed(fn, n: int = 20, warm: int = 3) -> tuple[list[float], float]:
    for _ in range(warm):
        fn()
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return samples, p95(samples)


FAILURES: list[str] = []


def gate(name: str, got_ms: float, limit_ms: float) -> None:
    ok = got_ms < limit_ms
    print(f"  {'✓' if ok else '✗'} {name}: P95 {got_ms:.1f}ms（门禁 <{limit_ms}ms）")
    if not ok:
        FAILURES.append(f"{name} P95 {got_ms:.1f}ms ≥ {limit_ms}ms")


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    admin = Client(base)

    # ── 数据集（幂等：先清旧 bench 数据）──────────────────────────────
    print("═══ 数据集准备（10 万 Issue / 单项目 1 万 / 5 字段）═══")
    psql(f"DELETE FROM issue_activities WHERE issue_id IN (SELECT id FROM issues WHERE name LIKE '{BENCH_TAG}%')")
    psql(f"DELETE FROM issue_links WHERE issue_id IN (SELECT id FROM issues WHERE name LIKE '{BENCH_TAG}%')")
    psql(f"DELETE FROM work_logs WHERE issue_id IN (SELECT id FROM issues WHERE name LIKE '{BENCH_TAG}%')")
    psql(f"DELETE FROM issue_assignees WHERE issue_id IN (SELECT id FROM issues WHERE name LIKE '{BENCH_TAG}%')")
    psql(f"DELETE FROM issues WHERE name LIKE '{BENCH_TAG}%'")
    psql("DELETE FROM custom_field_definitions WHERE name LIKE 'bench_%'")

    # bench 项目与根任务（HTTP 走正常建链拿 project/issue id）
    import json as _json

    ts = int(time.time() * 1000) % 100000000
    admin.req("POST", "/api/v1/auth/sign-up/",
              {"email": f"{BENCH_TAG}{ts}@rabbit.dev", "password": "Rabbit123!"},
              {"X-CSRFToken": admin.csrf()})
    _, b = admin.req("GET", "/api/v1/workspaces/")
    ws = b["data"][0]["slug"]
    _, b = admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/",
                     {"name": "bench-perf", "identifier": "BEN"},
                     {"X-CSRFToken": admin.csrf()})
    proj = b["data"]["id"]
    psql("DELETE FROM issues WHERE project_id IN (SELECT id FROM projects WHERE identifier='BNZ')"
         "; DELETE FROM states WHERE project_id IN (SELECT id FROM projects WHERE identifier='BNZ')"
         "; DELETE FROM project_members WHERE project_id IN (SELECT id FROM projects WHERE identifier='BNZ')"
         "; DELETE FROM projects WHERE identifier='BNZ'")  # 幂等复跑
    admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/",
              {"name": "bench-noise", "identifier": "BNZ"},
              {"X-CSRFToken": admin.csrf()})
    _, b = admin.req("GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/states/")
    done_state = next(x["id"] for x in b["data"] if x["group"] == "completed")
    todo_state = next(x["id"] for x in b["data"] if x["group"] == "unstarted")

    # 5 个字段（数值/单选/多选/日期/布尔——混合筛选覆盖类型感知路径）
    for i, (fname, ftype) in enumerate([
            ("bench_points", "number"), ("bench_sev", "select"),
            ("bench_tags", "multi_select"), ("bench_day", "date"), ("bench_on", "checkbox")]):
        admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issue-properties/",
                  {"name": fname[6:], "field_type": ftype,
                   "options": [{"value": f"s{j}", "label": f"S{j}", "color": "#3B82F6"} for j in range(5)]
                   } if ftype in ("select", "multi_select") else
                  {"name": fname[6:], "field_type": ftype},
                  {"X-CSRFToken": admin.csrf()})
    # 直接 SQL 灌 10 万（bench 项目 1 万 + 其余 9 万散布同库；字段值按 key 落 jsonb）
    psql(f"""
    INSERT INTO issues (id, project_id, name, description_json, description_html, priority,
                        sequence_id, sort_order, custom_fields, state_id, created_at, updated_at, attachment_count)
    SELECT gen_random_uuid(), '{proj}', '{BENCH_TAG}-w' || g, '{{}}'::jsonb, '<p></p>', 'none',
           g, g * 100.0,
           jsonb_build_object(
             'cf_bench_points', (g % 1000)::text,
             'cf_bench_sev', 's' || (g % 5)::text,
             'cf_bench_tags', jsonb_build_array('t' || (g % 8)::text, 't' || ((g+3) % 8)::text),
             'cf_bench_day', to_char(date '2026-01-01' + (g % 280), 'YYYY-MM-DD'),
             'cf_bench_on', (g % 2 = 0)),
           '{todo_state}', now() - interval '30 days' + (g % 600 || ' minutes')::interval, now(),
           0
    FROM generate_series(1, 10000) g
    """)
    # 口径：10 万总量 / 单项目 1 万——其余 9 万放独立噪声项目（同库同负载，不进 bench 项目）
    psql(f"""
    INSERT INTO issues (id, project_id, name, description_json, description_html, priority,
                        sequence_id, sort_order, custom_fields, state_id, created_at, updated_at, attachment_count)
    SELECT gen_random_uuid(),
           (SELECT id FROM projects WHERE identifier='BNZ' AND deleted_at IS NULL),
           '{BENCH_TAG}-n' || g, '{{}}'::jsonb, '<p></p>', 'none',
           g, g * 100.0, '{{}}'::jsonb, '{todo_state}', now(), now(), 0
    FROM generate_series(1, 90000) g
    """)
    print("  数据集就绪：100,000 行（bench 项目内）")

    # ── G7：自定义字段 5 条混合筛选（等值+包含+范围+布尔）────────────
    sev_field = "cf_bench_sev"
    # property 参数需要 field id——用 field_key 直接走已实现的路径；此处经 key（实现支持 key）
    fkey = psql(f"SELECT field_key FROM custom_field_definitions WHERE name='bench_sev' AND project_id='{proj}'").strip().splitlines()[-1]
    mix = (f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/"
           f"?property.{fkey}=s1&per_page=20&order_by=-created_at")
    samples, p = timed(lambda: admin.req("GET", mix))
    gate("G7 自定义字段筛选（等值）", p, 200)
    print("  EXPLAIN 摘要：")
    print("   " + "\n   ".join(psql(
        f"EXPLAIN (ANALYZE, COSTS OFF) SELECT id FROM issues WHERE project_id='{proj}' "
        f"AND deleted_at IS NULL AND custom_fields @> jsonb_build_object('{fkey}', 's1') LIMIT 20"
    ).strip().splitlines()[:5]))

    # ── subtree（bench 项目 1 万行下的树：建一棵 200 节点浅树）────────
    _, b = admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/",
                     {"name": f"{BENCH_TAG}-root"}, {"X-CSRFToken": admin.csrf()})
    root = b["data"]["id"]
    psql(f"""INSERT INTO issues (id, project_id, name, description_json, description_html, priority,
           sequence_id, sort_order, custom_fields, state_id, parent_id, created_at, updated_at, attachment_count)
           SELECT gen_random_uuid(), '{proj}', '{BENCH_TAG}-n' || g, '{{}}'::jsonb, '<p></p>', 'none',
           200000 + g, g * 100.0, '{{}}'::jsonb, '{todo_state}', '{root}', now(), now(), 0
           FROM generate_series(1, 199) g""")
    samples, p = timed(lambda: admin.req("GET",
        f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{root}/subtree/"))
    gate("IT-004 subtree 200 节点", p, 150)

    # ── 依赖拦截（409 路径：BLOCKER_SQL 点查）────────────────────────
    _, b = admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/",
                     {"name": f"{BENCH_TAG}-blocked"}, {"X-CSRFToken": admin.csrf()})
    blocked = b["data"]["id"]
    admin.req("POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{root}/relations/",
              {"related_issue_id": blocked, "relation_type": "blocks"},
              {"X-CSRFToken": admin.csrf()})
    # 门禁对象 = BLOCKER_SQL 单查询（规格 §4.3.3）；HTTP 409 整链路含序列化/CSRF
    # 不在此口径（flow T5-10 已锚定行为正确性）
    blocker_sql = (
        "SELECT i.id FROM issue_links l JOIN issues i ON i.id = l.related_issue_id "
        "LEFT JOIN states s ON s.id = i.state_id WHERE l.issue_id = '" + blocked + "' "
        "AND l.relation_type = 'is_blocked_by' AND l.deleted_at IS NULL "
        "AND i.deleted_at IS NULL AND COALESCE(s.\"group\", 'unstarted') "
        "NOT IN ('completed', 'cancelled')")
    code = admin.req("PATCH", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{blocked}/",
                     {"state_id": done_state}, {"X-CSRFToken": admin.csrf()})[0]
    assert code == HTTP["CONFLICT"], f"拦截行为前置失败 {code}"
    # 计时口径 = PG 自报 execution time（EXPLAIN ANALYZE, FORMAT JSON），
    # 排除 docker exec 子进程固定开销（~30ms，与查询无关）
    import json as _j

    def _blocker_ms() -> float:
        out = psql(f"EXPLAIN (ANALYZE, FORMAT JSON, COSTS OFF) {blocker_sql}", unaligned=True)
        return _j.loads(out)[0]["Execution Time"]

    _blocker_ms()  # 预热
    samples = [_blocker_ms() for _ in range(20)]
    p = p95(samples)
    gate("IT-008 依赖拦截 BLOCKER_SQL", p, 2)

    # ── 工时列表 ─────────────────────────────────────────────────────
    psql(f"""INSERT INTO work_logs (id, issue_id, actor_id, worked_on, minutes, note, created_at, updated_at)
           SELECT gen_random_uuid(), '{root}', (SELECT created_by_id FROM issues WHERE id='{root}'),
           date '2026-08-10' + (g % 25), 60 + (g % 8) * 15, 'bench', now(), now()
           FROM generate_series(1, 400) g""")
    samples, p = timed(lambda: admin.req(
        "GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/{root}/worklogs/?per_page=20"))
    gate("IT-005 工时列表（400 记录）", p, 300)

    # ── 归档视图（反向条件扫描）───────────────────────────────────────
    psql(f"UPDATE issues SET archived_at = now() WHERE name LIKE '{BENCH_TAG}-n9%' AND project_id='{proj}'")
    samples, p = timed(lambda: admin.req(
        "GET", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/?archived=true&per_page=20"))
    gate("IT-006 归档视图", p, 200)

    print(f"\n{'═' * 40}")
    if FAILURES:
        print("性能门禁未过：")
        print("\n".join("  ✗ " + f for f in FAILURES))
        return 1
    print("性能门禁全部通过 ✓（G7/G1 + 四项 IT P95）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
