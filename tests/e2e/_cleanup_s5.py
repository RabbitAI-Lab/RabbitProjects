#!/usr/bin/env python3
"""S5E2E 域幂等清理（parity-sprint5.spec.ts afterAll 调用；坑 22 纪律）。

projects 的 14 张 FK 子表全序覆盖（comment_reactions → … → projects），
循环至零残留；S5[IGWL]* 标识符域与 S5E2E-% 名域一并清。
"""
from __future__ import annotations

import subprocess
import sys

STEPS = [
    "DELETE FROM comment_reactions WHERE comment_id IN (SELECT id FROM issue_comments WHERE issue_id IN (SELECT id FROM issues WHERE project_id IN (SELECT id FROM projects WHERE name LIKE 'S5E2E%')))",
    "DELETE FROM issue_comments WHERE issue_id IN (SELECT id FROM issues WHERE project_id IN (SELECT id FROM projects WHERE name LIKE 'S5E2E%'))",
    "DELETE FROM issue_activities WHERE issue_id IN (SELECT id FROM issues WHERE project_id IN (SELECT id FROM projects WHERE name LIKE 'S5E2E%'))",
    "DELETE FROM work_logs WHERE issue_id IN (SELECT id FROM issues WHERE project_id IN (SELECT id FROM projects WHERE name LIKE 'S5E2E%'))",
    "DELETE FROM issue_assignees WHERE issue_id IN (SELECT id FROM issues WHERE project_id IN (SELECT id FROM projects WHERE name LIKE 'S5E2E%'))",
    "DELETE FROM issue_labels WHERE issue_id IN (SELECT id FROM issues WHERE project_id IN (SELECT id FROM projects WHERE name LIKE 'S5E2E%'))",
    "DELETE FROM issue_links WHERE issue_id IN (SELECT id FROM issues WHERE project_id IN (SELECT id FROM projects WHERE name LIKE 'S5E2E%'))",
    "DELETE FROM issues WHERE project_id IN (SELECT id FROM projects WHERE name LIKE 'S5E2E%')",
    "DELETE FROM issue_activities WHERE project_id IN (SELECT id FROM projects WHERE name LIKE 'S5E2E%')",
    "DELETE FROM labels WHERE project_id IN (SELECT id FROM projects WHERE name LIKE 'S5E2E%')",
    "DELETE FROM states WHERE project_id IN (SELECT id FROM projects WHERE name LIKE 'S5E2E%')",
    "DELETE FROM project_members WHERE project_id IN (SELECT id FROM projects WHERE name LIKE 'S5E2E%')",
    "DELETE FROM issue_views WHERE project_id IN (SELECT id FROM projects WHERE name LIKE 'S5E2E%')",
    "DELETE FROM project_status_logs WHERE project_id IN (SELECT id FROM projects WHERE name LIKE 'S5E2E%')",
    "DELETE FROM project_favorites WHERE project_id IN (SELECT id FROM projects WHERE name LIKE 'S5E2E%')",
    "DELETE FROM file_folders WHERE project_id IN (SELECT id FROM projects WHERE name LIKE 'S5E2E%')",
    "DELETE FROM upload_sessions WHERE project_id IN (SELECT id FROM projects WHERE name LIKE 'S5E2E%')",
    "DELETE FROM file_assets WHERE project_id IN (SELECT id FROM projects WHERE name LIKE 'S5E2E%')",
    "DELETE FROM custom_field_definitions WHERE project_id IN (SELECT id FROM projects WHERE name LIKE 'S5E2E%')",
    "DELETE FROM webhook_deliveries WHERE endpoint_id IN (SELECT id FROM webhook_endpoints WHERE project_id IN (SELECT id FROM projects WHERE name LIKE 'S5E2E%'))",
    "DELETE FROM webhook_endpoints WHERE project_id IN (SELECT id FROM projects WHERE name LIKE 'S5E2E%')",
    "DELETE FROM integration_installations WHERE project_id IN (SELECT id FROM projects WHERE name LIKE 'S5E2E%')",
    "DELETE FROM projects WHERE name LIKE 'S5E2E%'",
]


def run(sql: str) -> str:
    r = subprocess.run(
        ["docker", "exec", "-i", "rp-pg", "psql", "-U", "rp", "-d", "rabbit_projects", "-Atc", sql],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200])
    return r.stdout.strip()


def main() -> int:
    for round_no in range(3):
        try:
            for step in STEPS:
                run(step)
        except RuntimeError as e:
            print(f"round {round_no} partial: {e}", file=sys.stderr)
            continue
        left = run("SELECT count(*) FROM projects WHERE name LIKE 'S5E2E%';")
        if left == "0":
            print("S5E2E 域零残留 ✓")
            return 0
    left = run("SELECT count(*) FROM projects WHERE name LIKE 'S5E2E%';")
    print(f"S5E2E 残留 {left} 行（应为 0）", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
