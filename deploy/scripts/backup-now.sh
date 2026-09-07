#!/usr/bin/env bash
# backup-now.sh —— 手动触发一次全量备份（INFRA-005 §4.4.1 的人工兜底入口；
# 定时化归 beat 03:07 daily_backup——本脚本与 beat 同一代码路径 run_backup）。
# 用法（仓库根）：deploy/scripts/backup-now.sh [--kind manual|daily]
# env：dev 宿主自动置 BACKUP_PG_CMD="docker exec rp-pg"（宿主无 pg 客户端）；
#      AWS_*/MINIO_* 须在（根 .env 自动 source）。
set -euo pipefail
cd "$(dirname "$0")/../../apps/api"

KIND=manual
[ "${1:-}" = "--kind" ] && KIND="${2:-manual}"

set -a; [ -f ../../.env ] && source ../../.env; set +a
: "${DATABASE_URL:=postgresql://rp:rp@localhost:5432/rabbit_projects}"
export DATABASE_URL
# .env 的 DATABASE_URL 可能指向远端——本地演练显式回写（与 restore-drill 同口径）
case "$DATABASE_URL" in *localhost*) ;; *) export DATABASE_URL="postgresql://rp:rp@localhost:5432/rabbit_projects";; esac
# 运行中 rp-minio 的凭据以容器 env 为准（.env 可能与实际容器漂移——实测教训）
MINIO_ROOT_USER=$(docker exec rp-minio printenv MINIO_ROOT_USER)
MINIO_ROOT_PASSWORD=$(docker exec rp-minio printenv MINIO_ROOT_PASSWORD)
export AWS_ACCESS_KEY_ID="$MINIO_ROOT_USER"
export AWS_SECRET_ACCESS_KEY="$MINIO_ROOT_PASSWORD"
# .env 的 endpoint 多为 compose 主机名（minio:9000）——本地脚本回写宿主可达地址
case "${AWS_S3_ENDPOINT_URL:-}" in *minio:*) export AWS_S3_ENDPOINT_URL="http://localhost:9000";; *) export AWS_S3_ENDPOINT_URL="${AWS_S3_ENDPOINT_URL:-http://localhost:9000}";; esac
command -v pg_dump >/dev/null 2>&1 || export BACKUP_PG_CMD="docker exec rp-pg"

exec uv run python - "$KIND" << 'PYEOF'
import sys
import django, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "plane.settings.dev")
django.setup()
from plane.bgtasks.backup import run_backup
key = run_backup(kind=sys.argv[1])
print(f"BACKUP_OK {key}")
PYEOF
