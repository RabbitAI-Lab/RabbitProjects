"""psql 调用参数环境自适应（唯一定义点——s4/s5 种子清场 SQL 共用）。

dev 走 rp-pg 容器；CI（GitHub runner 的 PG 服务容器无该名字）与异库彩排
回退本机 psql（PGPASSWORD/PGHOST 走标准环境变量）。库名自 DATABASE_URL
推导——20260923 每夜：硬编码库名把授权/清场打错库，403 且无报错。
"""
from __future__ import annotations

import os
import subprocess


def db_name() -> str:
    return (os.environ.get("DATABASE_URL") or "").rsplit("/", 1)[-1].split("?")[0] or "rabbit_projects"


def psql_args(extra: list[str]) -> list[str]:
    if subprocess.run(["docker", "inspect", "rp-pg"], capture_output=True).returncode == 0:
        return ["docker", "exec", "-i", "rp-pg", "psql", "-U", "rp", "-d", db_name(), *extra]
    return ["psql", "-h", os.environ.get("PGHOST", "127.0.0.1"), "-U", "rp", "-d", db_name(), *extra]
