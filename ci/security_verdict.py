#!/usr/bin/env python3
"""security_verdict.py —— BR-06 判定器（QA-001 §4.3）。

输入四份扫描报告（缺件视为空——对应工具在 CI 侧 `|| true` 容错产出）：
  pip-audit.json / npm-audit.json / trivy.json / gitleaks.json
判定：
  ① 任一源存在 CRITICAL → exit 1（零容忍）
  ② HIGH 未持有效豁免单（security/exemptions.yml：CVE/GHSA/漏泄件 ID 对应、
     expires_at 未过期）→ exit 1；豁免过期等同未豁免（防永久豁免腐化）
  ③ 全过 → exit 0，stdout 输出判定摘要 JSON
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ("pip-audit", "npm-audit", "trivy", "gitleaks")


def _load(name: str, root: Path | None = None) -> dict | list | None:
    p = (root or ROOT) / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def collect_pip(data) -> list[dict]:
    """pip-audit --format json：[{id, fix_versions, aliases, vulns?}...]；
    新版为 {dependencies: [...]}。统一吐 [{source, id, severity(缺 HIGH)}]。"""
    if not data:
        return []
    deps = data.get("dependencies", data) if isinstance(data, dict) else data
    out = []
    for dep in deps or []:
        for vuln in dep.get("vulns", []):
            out.append({"source": "pip-audit", "id": vuln.get("id") or vuln.get("alias") or "?",
                        "package": dep.get("name", "?"),
                        # pip-audit 不分级：有 fix 即按 High 处置（无 Critical 语义）
                        "severity": "HIGH"})
    return out


def collect_npm(data) -> list[dict]:
    """pnpm audit --json：{advisories: {id: {id, module_name, severity, ...}}}。"""
    if not data:
        return []
    adv = data.get("advisories", {}) if isinstance(data, dict) else {}
    return [{"source": "npm-audit", "id": str(a.get("github_advisory_id") or a.get("id")),
             "package": a.get("module_name", "?"),
             "severity": str(a.get("severity", "high")).upper()}
            for a in adv.values()]


def collect_trivy(data) -> list[dict]:
    """trivy image/fs --format json：{Results: [{Vulnerabilities: [{VulnerabilityID,
    PkgName, Severity}]}]}。"""
    if not data:
        return []
    out = []
    for res in data.get("Results", []) or []:
        for v in res.get("Vulnerabilities") or []:
            out.append({"source": "trivy", "id": v["VulnerabilityID"],
                        "package": v.get("PkgName", "?"),
                        "severity": str(v.get("Severity", "?")).upper()})
        for s in res.get("Secrets") or []:
            out.append({"source": "trivy", "id": f"secret:{s.get('RuleID', '?')}",
                        "package": s.get("Match", "?")[:80],
                        "severity": "CRITICAL"})   # 镜像内泄漏凭据按零容忍
    return out


def collect_gitleaks(data) -> list[dict]:
    """gitleaks --report-format json：[{RuleID, File, ...}]。"""
    if not data:
        return []
    return [{"source": "gitleaks", "id": f"leak:{f.get('RuleID', '?')}",
             "package": f"{f.get('File', '?')}:{f.get('StartLine', '?')}",
             "severity": "CRITICAL"}              # 凭据入仓按零容忍
            for f in data]


COLLECTORS = {"pip-audit": collect_pip, "npm-audit": collect_npm,
              "trivy": collect_trivy, "gitleaks": collect_gitleaks}


def load_exemptions() -> dict[str, date]:
    """security/exemptions.yml：[{id, reason, owner, fix_version, expires_at}]（id=CVE/GHSA/漏泄件标识）.
    过期豁免直接不载入（等同未豁免）。yaml 手工解析（无 PyYAML 硬依赖，
    CI 与本地均可跑；键值行式 schema 足够）。"""
    p = ROOT / "security" / "exemptions.yml"
    valid: dict[str, date] = {}
    if not p.exists():
        return valid
    entry: dict[str, str] = {}
    for line in p.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s == "-":
            continue
        if s.startswith("- "):
            if entry.get("id"):
                _commit(entry, valid)
            entry = {}
            s = s[2:]
        if ":" in s:
            k, _, v = s.partition(":")
            entry[k.strip()] = v.strip().strip("'\"")
    if entry.get("id"):
        _commit(entry, valid)
    return valid


def _commit(entry: dict[str, str], valid: dict[str, date]) -> None:
    try:
        exp = datetime.strptime(entry.get("expires_at", ""), "%Y-%m-%d").date()
    except ValueError:
        return                                   # 日期非法 → 豁免无效
    if exp >= date.today():
        valid[entry["id"]] = exp


def verdict(root: Path | None = None) -> int:
    root = root or ROOT
    findings: list[dict] = []
    for name in SOURCES:
        findings.extend(COLLECTORS[name](_load(f"{name}.json", root)))
    exemptions = load_exemptions()
    criticals = [f for f in findings if f["severity"] == "CRITICAL"]
    highs = [f for f in findings if f["severity"] == "HIGH"]
    uncovered = [h for h in highs if h["id"] not in exemptions]
    summary = {
        "total": len(findings), "critical": len(criticals),
        "high": len(highs), "high_exempted": len(highs) - len(uncovered),
        "sources": {n: bool(_load(f"{n}.json", root)) for n in SOURCES},
    }
    if criticals:
        print(json.dumps({**summary, "verdict": "FAIL",
                          "criticals": criticals[:20]}, ensure_ascii=False))
        print(f"✗ Critical 漏洞 {len(criticals)} 个，零容忍（BR-06）", file=sys.stderr)
        return 1
    if uncovered:
        print(json.dumps({**summary, "verdict": "FAIL",
                          "uncovered_highs": uncovered[:20]}, ensure_ascii=False))
        print(f"✗ High 漏洞缺豁免评审单: {[h['id'] for h in uncovered[:10]]}",
              file=sys.stderr)
        return 1
    print(json.dumps({**summary, "verdict": "PASS"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(verdict())
