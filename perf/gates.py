"""perf/gates.py —— 门禁表即代码（QA-001 §4.2：文档与脚本防漂移的单源）。

P95 门禁现行表 = s0~s5 已 PASS 文档口径的机器可读源；release.sh 两轮编排与
run-ci-checks 的 TC 断言消费本表——文档表、脚本阈值、报告聚合三处同源。

用法：
  python perf/gates.py dump                 # 打印现行门禁表（JSON）
  python perf/gates.py check <report.json>  # 报告对表判定（超限退出码 1）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

#: 现行 P95 门禁（单位 ms；来源：sprint-2/3/4/5 bench 已 PASS 基线）
GATES: dict[str, float] = {
    # sprint-2（10 万数据集五项）
    "s2_list_p95": 40.0,        # 实测 38.8
    "s2_list_filtered_p95": 15.0,  # 实测 12.5（R1.99）
    "s2_detail_p95": 15.0,      # 实测 12.0（R2）
    # sprint-4（甘特 + 文件库）
    "s4_gantt_first_screen_p95": 200.0,   # 实测 174.1（P1 真优化后）
    "s4_gantt_pan_p95": 35.0,    # 实测 29.5（P2）
    "s4_files_list_p95": 15.0,   # 实测 4.4（W1 量级）
    # sprint-5（报表/工时聚合等——v1-freeze 基线）
    "s5_project_stats_p95": 200.0,
    "s5_member_stats_p95": 200.0,
}

#: 允许的回归容忍（同一脚本两轮波动）——超门禁×(1+tolerance) 才判红
TOLERANCE = 0.10


def check(report: dict) -> tuple[bool, list[str]]:
    """报告 {metric: p95_ms} 对表现判；返回 (全过?, 违规行)。"""
    violations = []
    for metric, limit in GATES.items():
        value = report.get(metric)
        if value is None:
            violations.append(f"{metric}: 报告缺该指标")
            continue
        if float(value) > limit * (1 + TOLERANCE):
            violations.append(f"{metric}: {value}ms > 门禁 {limit}ms（+{TOLERANCE:.0%} 容忍）")
    return (not violations, violations)


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] == "dump":
        print(json.dumps({"gates": GATES, "tolerance": TOLERANCE},
                         ensure_ascii=False, indent=2))
        return 0
    if sys.argv[1] == "check":
        report = json.loads(Path(sys.argv[2]).read_text())
        ok, violations = check(report)
        print(json.dumps({"verdict": "PASS" if ok else "FAIL",
                          "violations": violations}, ensure_ascii=False, indent=2))
        return 0 if ok else 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
