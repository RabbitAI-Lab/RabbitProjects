"""security_verdict 判定器单测（QA-001 §5.1 安全面；BR-06 判定规则四分支）。

verdict 逻辑为纯 python（ci/security_verdict.py），经 importlib 跨目录加载；
豁免单全局读仓库 security/exemptions.yml——过期/非法日期不生效由 monkeypatch
改写全局 ROOT 实现（不动真文件）。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "security_verdict",
    Path(__file__).resolve().parents[3] / "ci" / "security_verdict.py",
)
sv = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sv)

pytestmark = pytest.mark.django_db


def _write(root: Path, name: str, payload) -> None:
    (root / name).write_text(json.dumps(payload))


PIP_OK = {"dependencies": [{"name": "django", "vulns": []}]}
NPM_HIGH = {"advisories": {
    "1": {"id": 1, "module_name": "semver", "severity": "high",
          "github_advisory_id": "GHSA-aaa-bbb-ccc"}}}
TRIVY_CRIT = {"Results": [{"Vulnerabilities": [
    {"VulnerabilityID": "CVE-2026-0001", "PkgName": "libssl", "Severity": "CRITICAL"}]}]}
GITLEAKS_ONE = [{"RuleID": "aws-access-key", "File": ".env", "StartLine": 3}]


def test_all_clean_passes(tmp_path, capsys):
    _write(tmp_path, "pip-audit.json", PIP_OK)
    assert sv.verdict(tmp_path) == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "PASS"


def test_critical_zero_tolerance(tmp_path, capsys):
    _write(tmp_path, "trivy.json", TRIVY_CRIT)
    assert sv.verdict(tmp_path) == 1
    err = capsys.readouterr().err
    assert "Critical" in err and "零容忍" in err


def test_gitleaks_is_critical(tmp_path):
    """凭据入仓按零容忍（severity=CRITICAL）——gitleaks 任何命中即 FAIL。"""
    _write(tmp_path, "gitleaks.json", GITLEAKS_ONE)
    assert sv.verdict(tmp_path) == 1


def test_uncovered_high_fails(tmp_path, capsys):
    _write(tmp_path, "npm-audit.json", NPM_HIGH)
    assert sv.verdict(tmp_path) == 1
    assert "GHSA-aaa-bbb-ccc" in capsys.readouterr().err


def test_exempted_high_passes(tmp_path, monkeypatch):
    """有效豁免单（未过期）覆盖 High → PASS；过期豁免等同未豁免。"""
    import datetime

    _write(tmp_path, "npm-audit.json", NPM_HIGH)
    fake_root = tmp_path / "repo"
    (fake_root / "security").mkdir(parents=True)
    # 未过期 → 过
    (fake_root / "security" / "exemptions.yml").write_text(
        "- id: GHSA-aaa-bbb-ccc\n  reason: 测试豁免\n  owner: qa@rabbit.dev\n"
        "  fix_version: 1.0.1\n  expires_at: 2999-01-01\n")
    monkeypatch.setattr(sv, "ROOT", fake_root)
    assert sv.verdict(tmp_path) == 0
    # 过期 → 不过（防永久豁免腐化）
    (fake_root / "security" / "exemptions.yml").write_text(
        "- id: GHSA-aaa-bbb-ccc\n  reason: 测试豁免\n  owner: qa@rabbit.dev\n"
        "  fix_version: 1.0.1\n  expires_at: 2020-01-01\n")
    assert sv.verdict(tmp_path) == 1
    # 非法日期 → 不过
    (fake_root / "security" / "exemptions.yml").write_text(
        "- id: GHSA-aaa-bbb-ccc\n  reason: x\n  owner: x\n"
        "  fix_version: x\n  expires_at: 不是日期\n")
    assert sv.verdict(tmp_path) == 1
    del datetime


def test_missing_reports_treated_as_clean(tmp_path):
    """缺件（工具未产出）不判 FAIL——CI 侧 || true 容错口径；sources 标 false。"""
    assert sv.verdict(tmp_path) == 0
