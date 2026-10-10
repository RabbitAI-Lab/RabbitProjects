#!/usr/bin/env python3
"""admin-ops e2e 造数清理（坑 #22 纪律：spec 造数必须 afterAll 自动收尾）。

admin-ops.spec.ts 的 beforeAll 授予 ops-parity SystemAdmin、C.136 经 UI 创建
release_gates 实例（全局表，非项目域）——不清理则下一轮「创建卡」分支变形，
waitForResponse(/release-gates/create/) 永不命中（20261010 每夜本地四连挂定位；
CI 全新库永不复现，dev 长期栈必踩）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from _psql_env import psql_args  # noqa: E402

OPS_EMAIL = "ops-parity@rabbit.dev"


def run(sql: str) -> str:
    r = subprocess.run(
        psql_args(["-Atc", sql]),
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200])
    return r.stdout.strip()


def main() -> int:
    run(
        "DELETE FROM release_gate_events WHERE gate_id IN ("
        "SELECT id FROM release_gates WHERE created_by_id IN ("
        "SELECT id FROM users WHERE email = '{email}'))".format(email=OPS_EMAIL)
    )
    left = run(
        "SELECT count(*) FROM release_gates WHERE created_by_id IN ("
        "SELECT id FROM users WHERE email = '{email}')".format(email=OPS_EMAIL)
    )
    if left != "0":
        run(
            "DELETE FROM release_gates WHERE created_by_id IN ("
            "SELECT id FROM users WHERE email = '{email}')".format(email=OPS_EMAIL)
        )
        left = run(
            "SELECT count(*) FROM release_gates WHERE created_by_id IN ("
            "SELECT id FROM users WHERE email = '{email}')".format(email=OPS_EMAIL)
        )
    print(f"admin-ops 域零残留 ✓ (release_gates left={left})" if left == "0"
          else f"admin-ops 残留 {left} 行（应为 0）", file=sys.stderr)
    return 0 if left == "0" else 1


if __name__ == "__main__":
    raise SystemExit(main())
