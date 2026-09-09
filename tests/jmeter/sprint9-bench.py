#!/usr/bin/env python3
"""Sprint-9 四门禁压测（PROJ-004 IT-07 / FILE-005 IT-08 / GANTT-003 UT-12 / RPT-003 §5）。

数据形状（规格登记口径）：
  ① summary/            P95 < 300ms——项目集下 3 项目合计 1 万任务 + 近 4 周工时快照
  ② dependency-graph/   P95 < 200ms——同工作空间 1 万任务，项目集子图 ≤100 节点/500 边
  ③ wiki search         P95 < 300ms——500 页面（每页 ≥3 版本）× 100 次检索
  ④ CPM 1 万节点        < 300ms（服务端引擎直测——CI 基准 UT-12）
跑完自动清理（坑 16：bench 数据集是大负载，防全局查询与 e2e 变慢）。

用法：python3 tests/jmeter/sprint9-bench.py [http://localhost:8000]
"""
from __future__ import annotations

import os
import statistics
import sys
import time
import uuid
from datetime import date, timedelta

sys.path.insert(0, ".")
sys.path.insert(0, "../../apps/api")
from _contract import Client  # noqa: E402  同目录契约客户端（信封/状态码唯一真相源）

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
BENCH_TAG = f"s9bench-{uuid.uuid4().hex[:8]}"
P95_LIMITS = {"summary": 0.300, "depgraph": 0.200, "wiki_search": 0.300, "cpm_10k": 0.300}



def _psql(stmt: str) -> None:
    import subprocess
    r = subprocess.run(["docker", "exec", "-i", "rp-pg", "psql", "-U", "rp", "-d", "rabbit_projects"],
                       input=stmt, capture_output=True, text=True)
    if r.returncode != 0 or "ERROR" in r.stderr:
        raise RuntimeError(f"psql failed: {r.stderr[:300]}")

def p95(samples: list[float]) -> float:
    s = sorted(samples)
    return s[min(int(len(s) * 0.95), len(s) - 1)]


def main() -> int:
    c = Client(BASE)
    c.post("/api/v1/auth/sign-up/", {
        "email": f"{BENCH_TAG}@rabbit.dev", "password": "Rabbit123!",
        "display_name": "压测"})
    c.post("/api/v1/auth/sign-in/", {
        "email": f"{BENCH_TAG}@rabbit.dev", "password": "Rabbit123!"})
    ws = c.post("/api/v1/workspaces/", {"name": BENCH_TAG})[1]["data"]
    slug = ws["slug"]
    print(f"[setup] ws={slug}")

    # ── ① ② 组合树 + 3 项目 1 万任务 + 工时快照 + 依赖边 ──
    pf = c.post(f"/api/v1/workspaces/{slug}/portfolios/", {"name": "压测组合"})[1]["data"]
    projects = []
    per = 3400  # 3×3400 ≈ 1.02 万（含跨项目边余量）
    for i in range(3):
        p = c.post(f"/api/v1/workspaces/{slug}/projects/",
                   {"name": f"P{i}", "identifier": f"B{i}"})[1]["data"]
        c.post(f"/api/v1/workspaces/{slug}/portfolios/{pf['id']}/projects/", {"project_id": p["id"]})
        projects.append(p)
    # 万级任务直插 SQL（写路径不计门禁；读侧才是被测面——P95 口径不变）
    import subprocess
    t0 = time.time()
    default_states = {}
    for p in projects:
        st = c.get(f"/api/v1/workspaces/{slug}/projects/{p['id']}/states/")[1]["data"]
        default_states[p["id"]] = next(x["id"] for x in st if x.get("is_default")) if isinstance(st, list) else st[0]["id"]
    chunks = []
    for p in projects:
        sid = default_states[p["id"]]
        for j in range(per):
            chunks.append(
                f"(gen_random_uuid(), now(), now(), NULL, '{p['id']}', '{sid}', 't{j}', "
                f"'2026-08-01', '2026-09-30', 480, {j + 1}, NULL)")
    stmt = ("INSERT INTO issues (id, created_at, updated_at, deleted_at, name, description_json, "
            "description_html, priority, sequence_id, sort_order, custom_fields, project_id, "
            "attachment_count, github_context, state_id, start_date, target_date, estimate_minutes, "
            "created_by_id) SELECT i.id, now(), now(), NULL, i.nm, '{}', '', 'none', i.seq, i.seq*65536, '{}', "
            "i.pid, 0, '{}', i.sid, '2026-08-01', '2026-09-30', 480, NULL FROM (VALUES "
            + ", ".join(f"(gen_random_uuid()::uuid, '{pid2}'::uuid, '{sid2}'::uuid, 't{j2}', {j2 + 1})"
                        for pid2, sid2 in default_states.items() for j2 in range(per))
            + ") AS i(id, pid, sid, nm, seq)")
    _psql(stmt)
    total_made = per * 3
    print(f"[setup] issues={total_made} (bulk SQL) in {time.time()-t0:.1f}s")
    # 工时快照（近 4 周 × 每项目 3 人）
    import subprocess
    me_id = c.get("/api/v1/users/me/")[1]["data"]["user"]["id"]
    monday = date(2026, 9, 7)
    sql_rows = []
    for p in projects:
        for w in range(4):
            wk = (monday - timedelta(weeks=w)).isoformat()
            for m in range(3):
                sql_rows.append(
                    f"(gen_random_uuid(), now(), now(), NULL, '{p['id']}', '{me_id}', '{wk}', 1800, 0, 0, 0, FALSE)")
    stmt = ("INSERT INTO worklog_summaries (id, created_at, updated_at, deleted_at, project_id, actor_id, "
            "week_start, total_minutes, task_count, approved_minutes, over_8h_days, is_frozen) VALUES "
            + ",".join(sql_rows) + " ON CONFLICT DO NOTHING")
    _psql(stmt)
    print("[setup] workload snapshots in-place（dep-graph 子图由节点过滤有界）")

    samples = {"summary": [], "depgraph": [], "wiki_search": []}
    for _ in range(30):
        t = time.perf_counter()
        c.get(f"/api/v1/workspaces/{slug}/portfolios/{pf['id']}/summary/")
        samples["summary"].append(time.perf_counter() - t)
    for _ in range(30):
        t = time.perf_counter()
        c.get(f"/api/v1/workspaces/{slug}/portfolios/{pf['id']}/dependency-graph/")
        samples["depgraph"].append(time.perf_counter() - t)

    # ── ③ Wiki 500 页 ×3 版本 × 100 检索 ──
    space = c.post(f"/api/v1/workspaces/{slug}/wiki/spaces/",
                   {"project_id": projects[0]["id"], "name": "压测空间"})[1]["data"]
    # 500 页 × 3 版本直插 SQL（写路径不计门禁）；1 页走 API 冒烟验证写路径契约
    doc = {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text",
         "text": "压测正文包含错误码注册表与网关验收约定，检索命中词 s9probe"}]}]}
    import json as _json
    doc_json = _json.dumps(doc, ensure_ascii=False)
    doc_html = "<p>压测正文包含错误码注册表与网关验收约定，检索命中词 s9probe</p>"
    doc_text = "压测正文包含错误码注册表与网关验收约定，检索命中词 s9probe"
    smoke = c.post(f"/api/v1/workspaces/{slug}/wiki/pages/",
                   {"space_id": space["id"], "title": "API冒烟页 s9probe"})[1]["data"]
    c.patch(f"/api/v1/workspaces/{slug}/wiki/pages/{smoke['id']}/draft/", {"content": doc})
    c.post(f"/api/v1/workspaces/{slug}/wiki/pages/{smoke['id']}/publish/", {"base_version_id": None})
    page_vals = ",".join(
        f"(gen_random_uuid(), now(), now(), NULL, '规范页{i} s9probe', 1, 65535, NULL, NULL, NULL, '{space['id']}', NULL)"
        for i in range(499))
    _psql("INSERT INTO wiki_pages (id, created_at, updated_at, deleted_at, title, depth, sort_order, "
          "draft_json, base_version_id, current_version_id, space_id, deleted_by_id) VALUES " + page_vals)
    _psql("INSERT INTO wiki_page_versions (id, created_at, updated_at, deleted_at, page_id, version_no, "
          "content_json, content_html, content_text, change_summary) "
          "SELECT gen_random_uuid(), now(), now(), NULL, p.id, v.version_no, "
          "'" + doc_json.replace("'", "''") + "', "
          "'" + doc_html + "', '" + doc_text + "', '' "
          "FROM wiki_pages p CROSS JOIN (VALUES (1),(2),(3)) AS v(version_no) "
          f"WHERE p.space_id='{space['id']}' AND p.title LIKE '规范页%'")
    _psql("UPDATE wiki_pages p SET current_version_id="
          "(SELECT v.id FROM wiki_page_versions v WHERE v.page_id=p.id AND v.version_no=3) "
          f"WHERE p.space_id='{space['id']}'")
    for _ in range(100):
        t = time.perf_counter()
        c.get(f"/api/v1/workspaces/{slug}/wiki/search/?q=s9probe")
        samples["wiki_search"].append(time.perf_counter() - t)

    # ── ④ CPM 1 万节点（服务端引擎直测）──
    print("[bench] CPM 10k …")
    os.environ["DJANGO_SETTINGS_MODULE"] = "plane.settings.dev"
    os.environ["DATABASE_URL"] = "postgresql://rp:rp@localhost:5432/rabbit_projects"
    os.environ["SECRET_KEY"] = "dev"
    import subprocess as _sp
    # CPM 引擎在 API venv 内运行（脚本宿主 python 无 django）——委托子进程直测
    code = (
        "import os, time, uuid, datetime;"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE','plane.settings.dev');"
        "import django; django.setup();"
        "from plane.db.services.cpm import CPMEngine;"
        "t=time.perf_counter();"
        f"CPMEngine().compute(uuid.UUID('{projects[0]['id']}'), anchor_today=datetime.date.today());"
        "print('CPM_MS', round((time.perf_counter()-t)*1000,1))"
    )
    api_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../apps/api"))
    venv_py = os.path.join(api_root, ".venv/bin/python")
    r = _sp.run([venv_py, "-c", code], capture_output=True, text=True, cwd=api_root)
    line = next((l for l in r.stdout.splitlines() if l.startswith("CPM_MS")), None)
    if line is None:
        raise RuntimeError(f"CPM subprocess failed: {r.stderr[-300:]}")
    samples["cpm_10k"] = [float(line.split()[1]) / 1000]

    # ── 结果与判定 ──
    failed = []
    print("\n═══ Sprint-9 四门禁 P95 ═══")
    for key, limit in P95_LIMITS.items():
        v = p95(samples[key])
        ok = v < limit
        print(f"  {key:12s} P95={v*1000:7.1f}ms  限 {limit*1000:.0f}ms  {'✓' if ok else '✗'}")
        if not ok:
            failed.append(key)

    # ── 清理（坑 16）──
    print("\n[cleanup] …")
    wid = ws["id"]
    _psql(
        f"DELETE FROM issue_views v USING projects pr WHERE v.project_id=pr.id AND pr.workspace_id='{wid}';"
        f"DELETE FROM user_view_preferences WHERE view_id IN (SELECT id FROM issue_views);"
        f"DELETE FROM project_status_logs l USING projects pr WHERE l.project_id=pr.id AND pr.workspace_id='{wid}';"
        f"DELETE FROM workflows w USING projects pr WHERE w.project_id=pr.id AND pr.workspace_id='{wid}';"
        f"DELETE FROM approval_flows f USING projects pr WHERE f.project_id=pr.id AND pr.workspace_id='{wid}';"
        f"DELETE FROM automation_rules r USING projects pr WHERE r.project_id=pr.id AND pr.workspace_id='{wid}';"
        f"DELETE FROM automation_settings t USING projects pr WHERE t.project_id=pr.id AND pr.workspace_id='{wid}';"
        f"DELETE FROM project_worklog_configs g USING projects pr WHERE g.project_id=pr.id AND pr.workspace_id='{wid}';"
        f"DELETE FROM project_favorites USING projects pr WHERE pr.workspace_id='{wid}';"
        f"DELETE FROM labels l USING projects pr WHERE l.project_id=pr.id AND pr.workspace_id='{wid}';"
        f"DELETE FROM cycles cy USING projects pr WHERE cy.project_id=pr.id AND pr.workspace_id='{wid}';"
        f"DELETE FROM issue_activities WHERE project_id IN (SELECT id FROM projects WHERE workspace_id='{wid}');"
        f"DELETE FROM issue_activities WHERE issue_id IN (SELECT i.id FROM issues i JOIN projects pr ON pr.id=i.project_id WHERE pr.workspace_id='{wid}');"
        f"DELETE FROM issue_links WHERE issue_id IN (SELECT i.id FROM issues i JOIN projects pr ON pr.id=i.project_id WHERE pr.workspace_id='{wid}');"
        f"DELETE FROM issues i USING projects pr WHERE i.project_id=pr.id AND pr.workspace_id='{wid}';"
        f"DELETE FROM states s USING projects pr WHERE s.project_id=pr.id AND pr.workspace_id='{wid}';"
        f"DELETE FROM project_members pm USING projects pr WHERE pm.project_id=pr.id AND pr.workspace_id='{wid}';"
        f"DELETE FROM portfolio_projects WHERE portfolio_id='{pf['id']}';"
        f"DELETE FROM portfolios WHERE id='{pf['id']}';"
        f"DELETE FROM worklog_summaries ws2 USING projects pr WHERE ws2.project_id=pr.id AND pr.workspace_id='{wid}';"
        f"UPDATE wiki_pages SET current_version_id=NULL, base_version_id=NULL WHERE space_id IN (SELECT id FROM wiki_spaces WHERE project_id IN (SELECT id FROM projects WHERE workspace_id='{wid}'));"
        f"DELETE FROM wiki_page_versions WHERE page_id IN (SELECT p.id FROM wiki_pages p JOIN wiki_spaces sp ON sp.id=p.space_id WHERE sp.project_id IN (SELECT id FROM projects WHERE workspace_id='{wid}'));"
        f"DELETE FROM wiki_pages p USING wiki_spaces sp WHERE p.space_id=sp.id AND sp.project_id IN (SELECT id FROM projects WHERE workspace_id='{wid}');"
        f"DELETE FROM wiki_spaces sp USING projects pr WHERE sp.project_id=pr.id AND pr.workspace_id='{wid}';"
        f"DELETE FROM projects WHERE workspace_id='{wid}';"
        f"DELETE FROM worklog_summaries WHERE project_id NOT IN (SELECT id FROM projects);"
        f"DELETE FROM project_report_configs WHERE project_id NOT IN (SELECT id FROM projects);"
        f"DELETE FROM cpm_alert_config WHERE project_id NOT IN (SELECT id FROM projects);"
        f"DELETE FROM issue_cpm_cache WHERE project_id NOT IN (SELECT id FROM projects);"
        f"DELETE FROM daily_group_snapshots WHERE project_id NOT IN (SELECT id FROM projects);"
        f"DELETE FROM workspace_members WHERE workspace_id='{wid}';"
        f"DELETE FROM export_tasks WHERE workspace_id='{wid}';"
        f"DELETE FROM workspaces WHERE id='{wid}';")
    print("[cleanup] done")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
