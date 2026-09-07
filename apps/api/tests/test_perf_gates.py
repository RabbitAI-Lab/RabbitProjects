"""perf/gates.py 单测（QA-001 §5.1 UT-07——门禁表与文档表不一致即红）。

GATES 的单源在 perf/gates.py；本测硬拷 QA-001 §4.2 现行表值做双源比对
（文档↔代码漂移防线），阈值改动必须两边同 PR 走评审（BR-01 冻结口径）。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.django_db

_ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("perf_gates", _ROOT / "perf" / "gates.py")
gates = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gates)

#: QA-001 §4.2 现行 P95 门禁表（文档侧硬拷——双源比对锚点）
DOC_TABLE = {
    "s2_list_p95": 40.0,
    "s2_list_filtered_p95": 15.0,
    "s2_detail_p95": 15.0,
    "s4_gantt_first_screen_p95": 200.0,
    "s4_gantt_pan_p95": 35.0,
    "s4_files_list_p95": 15.0,
    "s5_project_stats_p95": 200.0,
    "s5_member_stats_p95": 200.0,
}


def test_ut07_gates_match_documentation():
    """UT-07：gates.py 与 QA-001 §4.2 文档表逐键一致（防双源漂移）。"""
    assert gates.GATES == DOC_TABLE, (
        f"门禁表漂移：代码={ {k: v for k, v in gates.GATES.items() if DOC_TABLE.get(k) != v} } "
        f"文档={ {k: v for k, v in DOC_TABLE.items() if gates.GATES.get(k) != v} }——"
        "阈值改动须 QA-001 §4.2 与 perf/gates.py 同 PR")


def test_check_pass_fail_and_missing():
    ok, v = gates.check({k: lim * 0.5 for k, lim in gates.GATES.items()})
    assert ok and v == []
    # 超限（>门禁×1.10）红；容忍带内（×1.05）绿
    bad = {k: lim * 1.5 for k, lim in gates.GATES.items()}
    ok2, v2 = gates.check(bad)
    assert not ok2 and len(v2) == len(gates.GATES)
    ok3, _ = gates.check({k: lim * 1.05 for k, lim in gates.GATES.items()})
    assert ok3
    # 缺指标=违规（全矩阵纪律）
    ok4, v4 = gates.check({"s2_list_p95": 10.0})
    assert not ok4 and any("缺该指标" in x for x in v4)


def test_cli_exit_codes(tmp_path):
    dump = subprocess.run([sys.executable, str(_ROOT / "perf" / "gates.py"), "dump"],
                          capture_output=True, text=True)
    assert dump.returncode == 0 and "s2_list_p95" in json.loads(dump.stdout)["gates"]
    bad = tmp_path / "r.json"
    bad.write_text(json.dumps({"s2_list_p95": 999.0}))
    r = subprocess.run([sys.executable, str(_ROOT / "perf" / "gates.py"),
                        "check", str(bad)], capture_output=True, text=True)
    assert r.returncode == 1 and "FAIL" in r.stdout
