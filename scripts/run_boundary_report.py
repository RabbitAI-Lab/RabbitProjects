"""P4 R1 演示兜底：同步执行最新一条排队中的边界报告任务。

演示环境无独立 reports 队列消费者时的收口工具（worker 在跑则自然消费，
本脚本幂等——只处理 state=queued 的最新一条）。
用法：PATH=apps/api/.venv/bin:$PATH python3 scripts/run_boundary_report.py [tenant_id]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import django

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "plane.settings.dev")
# 演示兜底默认凭据（dev MinIO 容器 rp-minio；settings base 缺省为空串会被
# boto3 当 InvalidAccessKeyId 拒——显式给默认值，真实环境 env 覆盖）
os.environ.setdefault("AWS_ACCESS_KEY_ID", "rpminio")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "rpminio123")
django.setup()  # noqa: E402

from plane.db.models import BoundaryReport  # noqa: E402
from plane.governance.tasks import generate_boundary_report  # noqa: E402


def main() -> None:
    qs = BoundaryReport.objects.filter(state="queued").order_by("-created_at")
    if len(sys.argv) > 1:
        qs = qs.filter(tenant_id=sys.argv[1])
    report = qs.first()
    if report is None:
        print("no queued report")
        return
    result = generate_boundary_report.run(str(report.id))
    print(f"report={report.id} -> {result}")


if __name__ == "__main__":
    main()
