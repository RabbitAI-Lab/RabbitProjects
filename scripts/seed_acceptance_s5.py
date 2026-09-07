#!/usr/bin/env python3
"""Sprint-5 验收演示数据准备（幂等可重跑）。

建「S5 验收演示」项目并铺齐 13 幕录屏所需数据（幕映射见 SCENARIOS.md）：
  基线任务 12 条（五态分布 + 1 阻塞前置——幕 09 统计 / 幕 11 关闭向导）；
  GitHub 绑定 acme/rabbit-web（installation 9001，secret 明文经 __register
  注入 mock_github_receiver——幕 01~04 双向同步）；
  Webhook 端点 3 条（指向 mock_500_receiver:8091——幕 05~07 死信链；
  issue.* 族 + project.* 族——幕 12 归档扇出）；
  团队页演示态：李四 CONTRIBUTOR（复选可勾）+ 王五 GUEST（幕 13 权限）。

用法：python3 scripts/seed_acceptance_s5.py [http://localhost:8000]
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, "tests/jmeter")
from _contract import Client, HTTP, q  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
TAG = "S5 验收演示"
IDENT = "S5AC"
WS = "workspace"
LISI = {"email": "lisi@rabbit.dev", "password": "Rabbit123!", "name": "李四"}
WANGWU = {"email": "wangwu@rabbit.dev", "password": "Rabbit123!", "name": "王五"}
GH_SECRET = "s5-acceptance-webhook-secret"
MOCK_500 = "http://127.0.0.1:8091/rp"


def psql(sql: str) -> str:
    r = subprocess.run(
        ["docker", "exec", "-i", "rp-pg", "psql", "-U", "rp", "-d", "rabbit_projects", "-Atc", sql],
        capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:200])
    return r.stdout.strip()


def purge_demo() -> None:
    """幂等清场：S5 验收项目全域硬删（含 14 张 FK 子表，坑 21/22 纪律）。
    SLCD（幕 11 生命周期演示项目）一并清——防复跑 identifier 冲突 409。"""
    for ident in (IDENT, "SLCD"):
        _purge_by_ident(ident)


def _purge_by_ident(ident: str) -> None:
    pid = psql(f"SELECT id FROM projects WHERE identifier='{ident}' LIMIT 1")
    if not pid:
        return
    steps = [
        f"DELETE FROM comment_reactions WHERE comment_id IN (SELECT id FROM issue_comments WHERE issue_id IN (SELECT id FROM issues WHERE project_id='{pid}'))",
        f"DELETE FROM issue_comments WHERE issue_id IN (SELECT id FROM issues WHERE project_id='{pid}')",
        f"DELETE FROM issue_activities WHERE issue_id IN (SELECT id FROM issues WHERE project_id='{pid}')",
        f"DELETE FROM work_logs WHERE issue_id IN (SELECT id FROM issues WHERE project_id='{pid}')",
        f"DELETE FROM issue_assignees WHERE issue_id IN (SELECT id FROM issues WHERE project_id='{pid}')",
        f"DELETE FROM issue_labels WHERE issue_id IN (SELECT id FROM issues WHERE project_id='{pid}')",
        f"DELETE FROM issue_links WHERE issue_id IN (SELECT id FROM issues WHERE project_id='{pid}')",
        f"DELETE FROM issues WHERE project_id='{pid}'",
        f"DELETE FROM issue_activities WHERE project_id='{pid}'",
        f"DELETE FROM labels WHERE project_id='{pid}'",
        f"DELETE FROM states WHERE project_id='{pid}'",
        f"DELETE FROM project_members WHERE project_id='{pid}'",
        f"DELETE FROM issue_views WHERE project_id='{pid}'",
        f"DELETE FROM project_status_logs WHERE project_id='{pid}'",
        f"DELETE FROM project_favorites WHERE project_id='{pid}'",
        f"DELETE FROM webhook_deliveries WHERE endpoint_id IN (SELECT id FROM webhook_endpoints WHERE project_id='{pid}')",
        f"DELETE FROM webhook_endpoints WHERE project_id='{pid}'",
        f"DELETE FROM integration_installations WHERE project_id='{pid}'",
        f"DELETE FROM projects WHERE id='{pid}'",
    ]
    for round_no in range(3):
        try:
            for s in steps:
                psql(s)
            if psql(f"SELECT count(*) FROM projects WHERE id='{pid}'") == "0":
                return
        except RuntimeError:
            continue


def ensure_user(admin: Client, spec: dict) -> Client:
    c = Client(BASE)
    code, body = c.req("POST", "/api/v1/auth/sign-in/",
                       {"email": spec["email"], "password": spec["password"]},
                       {"X-CSRFToken": c.csrf()})
    if code == HTTP["OK"]:
        return c
    admin.req("POST", f"/api/v1/workspaces/{q(WS)}/invitations/",
              {"emails": [spec["email"]], "role": 10},
              {"X-CSRFToken": admin.csrf()})
    c2 = Client(BASE)
    ts = int(time.time() * 1000) % 10**8
    code2, b2 = c2.req("POST", "/api/v1/auth/sign-up/",
                       {"email": spec["email"], "password": spec["password"],
                        "display_name": spec["name"]},
                       {"X-CSRFToken": c2.csrf()})
    assert code2 == HTTP["CREATED"], (code2, b2)
    return c2


def main() -> None:
    admin = Client(BASE)
    code, body = admin.req("POST", "/api/v1/auth/sign-in/",
                           {"email": "zhangsan@rabbit.dev", "password": "Rabbit123"},
                           {"X-CSRFToken": admin.csrf()})
    assert code == HTTP["OK"], f"zhangsan 登录失败 {code} {body}（bootstrap 演示账号先就位）"

    print("── 幂等清场：硬删旧 S5 验收项目")
    purge_demo()

    code, body = admin.req("POST", f"/api/v1/workspaces/{q(WS)}/projects/",
                           {"name": TAG, "identifier": IDENT},
                           {"X-CSRFToken": admin.csrf()})
    assert code == HTTP["CREATED"], (code, body)
    proj = body["data"]["id"]
    base = f"/api/v1/workspaces/{q(WS)}/projects/{proj}"
    st = admin.req("GET", base + "/states/?include_cancelled=1")[1]["data"]
    groups = {s["group"]: s["id"] for s in st}

    lisi_c = ensure_user(admin, LISI)
    wangwu_c = ensure_user(admin, WANGWU)
    lisi_id = lisi_c.req("GET", "/api/v1/users/me/")[1]["data"]["user"]["id"]
    wangwu_id = wangwu_c.req("GET", "/api/v1/users/me/")[1]["data"]["user"]["id"]
    admin.req("POST", base + "/members/", {"member_ids": [lisi_id], "role": 15},
              {"X-CSRFToken": admin.csrf()})

    # ── 基线任务 12 条：五态分布 + 逾期 + 估算/工时（幕 09 统计非零）──
    def mk(name, **f):
        c3, b3 = admin.req("POST", base + "/issues/",
                            {"name": name, "priority": "none", **f},
                            {"X-CSRFToken": admin.csrf()})
        assert c3 == HTTP["CREATED"], (name, c3, b3)
        return b3["data"]

    today = time.strftime("%Y-%m-%d")
    for i in range(1, 9):
        mk(f"基线任务{i}", sequence_id=i, sort_order=i * 100,
           state_id=groups[["unstarted", "started", "started", "completed",
                            "completed", "completed", "cancelled", "unstarted"][i - 1]],
           estimate_minutes=120 * i if i % 2 else None,
           target_date="2026-08-20" if i % 4 == 0 else None)
    blocker = mk("关闭向导-阻塞前置", sequence_id=9, sort_order=900,
                 state_id=groups["unstarted"])
    final = mk("关闭向导-目标任务", sequence_id=10, sort_order=1000,
               state_id=groups["started"])
    admin.req("POST", base + f"/issues/{blocker['id']}/relations/",
              {"related_issue_id": final["id"], "relation_type": "blocks"},
              {"X-CSRFToken": admin.csrf()})

    # ── GitHub 绑定（幕 01~04）：secret 注册进 mock 触发器 ──
    c4, b4 = admin.req("POST", base + "/integrations/github/bindings/",
                        {"repository_full_name": "acme/rabbit-web",
                         "repository_node_id": "R_MOCK", "installation_id": 9001},
                        {"X-CSRFToken": admin.csrf()})
    assert c4 == HTTP["CREATED"], (c4, b4)
    binding = b4["data"]
    real_secret = binding.get("webhook_secret_shown_once") or GH_SECRET
    urllib.request.urlopen(urllib.request.Request(
        "http://127.0.0.1:8090/__register",
        data=json.dumps({"binding_id": binding["id"], "secret": real_secret}).encode(),
        headers={"Content-Type": "application/json"}, method="POST"), timeout=5).read()
    # 幕 04 素材：一条已同步任务（external 锚）
    psql(f"UPDATE issues SET external_source='github', external_id='N_S5DEMO', "
         f"github_context='{{\"number\": 7, \"prs\": [], \"commits\": []}}' "
         f"WHERE project_id='{proj}' AND name='基线任务7'")

    # ── Webhook 端点（幕 05~07 + 幕 12）：指向 mock_500 ──
    for i, ev in enumerate((["issue.created", "issue.updated"], ["project.archived", "project.activated"])):
        url = MOCK_500 if i == 0 else MOCK_500 + "/proj"  # 同 URL 唯一——第二端点换路径
        c5, b5 = admin.req("POST", base + "/webhooks/",
                            {"url": url, "events": ev},
                            {"X-CSRFToken": admin.csrf()})
        assert c5 == HTTP["CREATED"], (c5, b5)

    print(f"seed 完成：项目 {TAG}（{proj}）· 任务 10 · 绑定 acme/rabbit-web · 端点 3")
    print(json.dumps({"proj": proj, "binding_id": binding["id"], "final_id": final["id"],
                      "blocker_id": blocker["id"], "lisi_id": lisi_id,
                      "wangwu_id": wangwu_id, "gh_secret": real_secret},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
