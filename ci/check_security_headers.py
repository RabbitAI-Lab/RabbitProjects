#!/usr/bin/env python3
"""check_security_headers.py —— 响应头核查（QA-001 §2.4 安全加固清单）。

对给定 base-url 断言应用层安全头基线（api-conventions §13.4 / INFRA-004）：
  X-Frame-Options: DENY｜X-Content-Type-Options: nosniff｜
  Referrer-Policy 存在｜（prod 经 SECURE_SSL_REDIRECT 时）Strict-Transport-Security
用法：python ci/check_security_headers.py http://localhost:8000 [--prod]
退出码 0 = 基线齐；缺项逐条列出。
"""
from __future__ import annotations

import json
import sys
import urllib.request

BASELINE = [
    ("X-Frame-Options", lambda v: v.upper() in ("DENY", "SAMEORIGIN")),
    ("X-Content-Type-Options", lambda v: v.lower() == "nosniff"),
    ("Referrer-Policy", lambda v: bool(v)),
]
PROD_ONLY = [
    ("Strict-Transport-Security", lambda v: "max-age" in v.lower()),
]


def main() -> int:
    url = sys.argv[1].rstrip("/") + "/api/v1/health/"
    prod = "--prod" in sys.argv
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=10) as resp:
        headers = {k.lower(): v for k, v in resp.headers.items()}
    checks = BASELINE + (PROD_ONLY if prod else [])
    missing, wrong = [], []
    for name, predicate in checks:
        v = headers.get(name.lower())
        if v is None:
            missing.append(name)
        elif not predicate(v):
            wrong.append(f"{name}={v}")
    if missing or wrong:
        print(json.dumps({"verdict": "FAIL", "missing": missing,
                          "invalid": wrong}, ensure_ascii=False))
        return 1
    print(json.dumps({"verdict": "PASS", "checked": [n for n, _ in checks]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
