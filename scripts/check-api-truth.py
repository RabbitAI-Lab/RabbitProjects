#!/usr/bin/env python3
"""跨语言契约一致性检查（CLAUDE.md 测试脚本规范 ①）。

比对两侧「API 真相源」的 HTTP 状态码表是否逐键一致：
  · Python 侧：tests/jmeter/_contract.py 的 HTTP
  · TS 侧：    tests/e2e/no-console-errors.ts 的 HTTP

规范只写了「必须双源同步」，但此前没有任何机制**验证**它——同步与否全靠人眼，
而 sprint-0 实际就漂过（Python 侧 FORBIDDEN=401 / UNAUTHORIZED=403，与 TS 侧互换）。
本脚本把该约定变成可执行断言。

退出码：0 一致；1 不一致；2 解析失败。
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY_SRC = ROOT / "tests" / "jmeter" / "_contract.py"
TS_SRC = ROOT / "tests" / "e2e" / "no-console-errors.ts"


def parse_python_http() -> dict[str, int]:
    text = PY_SRC.read_text(encoding="utf-8")
    block = re.search(r"^HTTP\s*=\s*\{(.*?)^\}", text, re.DOTALL | re.MULTILINE)
    if not block:
        raise ValueError(f"未在 {PY_SRC} 找到 HTTP 字典")
    return {k: int(v) for k, v in re.findall(r'"(\w+)"\s*:\s*(\d+)', block.group(1))}


def parse_ts_http() -> dict[str, int]:
    text = TS_SRC.read_text(encoding="utf-8")
    block = re.search(r"export const HTTP\s*=\s*\{(.*?)\}\s*as const", text, re.DOTALL)
    if not block:
        raise ValueError(f"未在 {TS_SRC} 找到 HTTP 常量")
    return {k: int(v) for k, v in re.findall(r"(\w+)\s*:\s*(\d+)", block.group(1))}


def main() -> int:
    try:
        py, ts = parse_python_http(), parse_ts_http()
    except (OSError, ValueError) as exc:
        print(f"check-api-truth: 解析失败 {exc}", file=sys.stderr)
        return 2

    # TS 侧允许是 Python 侧的子集（e2e 未必用到全部码），但**共有键的值必须相等**，
    # 且 TS 不得出现 Python 里没有的键（那意味着 TS 自造了契约）。
    problems: list[str] = []
    for key in sorted(set(ts) - set(py)):
        problems.append(f"TS 独有键 {key}={ts[key]}（Python 侧无此码，属自造契约）")
    for key in sorted(set(py) & set(ts)):
        if py[key] != ts[key]:
            problems.append(f"键 {key} 取值不一致：Python={py[key]} TS={ts[key]}")

    if problems:
        print("check-api-truth: 两侧契约漂移", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    print(f"check-api-truth: OK（Python {len(py)} 码 / TS {len(ts)} 码，共有键全部一致）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
