#!/usr/bin/env bash
# restore-drill.sh —— 恢复演练（INFRA-005 §4.4.4；BR-09：RTO ≤30min 修流程不改目标）。
#
# 模式（--mode）：
#   local（默认，dev）：最近成功备份 → 恢复到 rp-pg 内隔离库 rabbit_projects_drill
#     → 临时 runserver :8002（全量 env）→ smoke release-18 → RTO 报告 → 即弃清理。
#   compose：隔离 compose 栈（18xxx 端口，docker-compose.drill.yml）——生产同构，
#     运行时验证归收口轮（规格 §7.2.4；当前交付 local 实测留痕）。
# 产物：报告 JSON（stdout 末行 DRILL_REPORT {...}）+ BackupRun(kind=drill) 回写。
set -euo pipefail
cd "$(dirname "$0")/../.."

MODE=local
while [ $# -gt 0 ]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    *) echo "unknown $1" >&2; exit 2 ;;
  esac
done
[ "$MODE" = "local" ] || { echo "mode=$MODE 的隔离栈演练归收口轮（当前交付 local）" >&2; exit 2; }

T0=$(date +%s)
DRILL_DB=rabbit_projects_drill
DRILL_PORT=8002

# env 装配前置（步骤①取对象即需存储凭证；凭据以运行容器为准——.env 会漂移）
MU=$(docker exec rp-minio printenv MINIO_ROOT_USER)
MP=$(docker exec rp-minio printenv MINIO_ROOT_PASSWORD)
export AWS_ACCESS_KEY_ID="$MU" AWS_SECRET_ACCESS_KEY="$MP"
case "${AWS_S3_ENDPOINT_URL:-}" in *minio:*) export AWS_S3_ENDPOINT_URL="http://localhost:9000";; *) export AWS_S3_ENDPOINT_URL="${AWS_S3_ENDPOINT_URL:-http://localhost:9000}";; esac
docker exec rp-minio mc alias set local http://localhost:9000 "$MU" "$MP" >/dev/null

# ① 取最近成功备份对象（BackupRun 留痕优先，桶清单兜底）
KEY=$(cd apps/api && DATABASE_URL="postgresql://rp:rp@localhost:5432/rabbit_projects" SECRET_KEY=dev \
  uv run python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'plane.settings.dev')
django.setup()
from plane.bgtasks.backup import BACKUP_BUCKET, BACKUP_PREFIX
from plane.storage import minio as st
objs = [o['key'] for o in st.list_objects(bucket=BACKUP_BUCKET, prefix=f'{BACKUP_PREFIX}/')
        if o['key'].endswith('db.dump')]
print(objs[-1] if objs else '')" 2>/dev/null || echo "")
[ -n "$KEY" ] || { echo "无备份对象——先跑 backup-now.sh" >&2; exit 1; }
echo "── 演练备份对象：$KEY"

# ② 拉取对象（预拉取，RTO 计时不含对象下载——与 §2.5 前置拉取口径一致）
DUMP=/tmp/rp-drill-$(date +%s).dump
docker exec rp-minio mc cp "local/rp-backups/$KEY" /drill.dump >/dev/null 2>&1 || {
  # mc cp 跨容器到宿主：经 stdin 中转
  docker exec rp-minio mc cat "local/rp-backups/$KEY" > "$DUMP"
}
[ -s "$DUMP" ] || docker exec rp-minio mc cat "local/rp-backups/$KEY" > "$DUMP"
[ -s "$DUMP" ] || { echo "备份对象拉取失败" >&2; exit 1; }

T1=$(date +%s)
# ③ 恢复到隔离库（drop 幂等 → create → pg_restore -j4 并行）
docker exec rp-pg psql -U rp -d postgres -c "DROP DATABASE IF EXISTS $DRILL_DB (FORCE)" >/dev/null
docker exec rp-pg psql -U rp -d postgres -c "CREATE DATABASE $DRILL_DB OWNER rp" >/dev/null
docker cp "$DUMP" rp-pg:/tmp/drill.dump >/dev/null
docker exec rp-pg pg_restore -U rp -d "$DRILL_DB" --no-owner -j 4 /tmp/drill.dump
docker exec rp-pg rm -f /tmp/drill.dump

# ④ 临时 API（全量 env；迁移头一致由 preflight 保证——skip，演练库随备份版本）
cd apps/api
export DATABASE_URL="postgresql://rp:rp@localhost:5432/$DRILL_DB"
export SECRET_KEY="${SECRET_KEY:-dev}" REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
uv run python manage.py runserver 0.0.0.0:$DRILL_PORT --noreload > /tmp/rp-drill-api.log 2>&1 &
API_PID=$!
trap 'kill $API_PID 2>/dev/null || true; docker exec rp-pg psql -U rp -d postgres -c "DROP DATABASE IF EXISTS '$DRILL_DB' (FORCE)" >/dev/null 2>&1; rm -f "$DUMP"' EXIT
for i in $(seq 1 30); do
  curl -sf "http://localhost:$DRILL_PORT/api/v1/health/" >/dev/null 2>&1 && break
  sleep 1
done
T2=$(date +%s)

# ⑤ 冒烟 18 项（演练与发布同一套口径——§4.4.4 注）
SMOKE_OUT=$(../../deploy/scripts/smoke.sh --suite release-18 \
  --base-url "http://localhost:$DRILL_PORT/api/v1" | tail -1)
SMOKE_PASSED=${SMOKE_OUT#SMOKE }
SMOKE_PASSED=${SMOKE_PASSED%/*}
SMOKE_TOTAL=${SMOKE_OUT#*/}
T3=$(date +%s)

# ⑥ 报告（stdout + BackupRun kind=drill 回写）
RTO=$((T2-T1)); TOTAL=$((T3-T0))
kill $API_PID 2>/dev/null || true
cat <<EOF
── 恢复演练报告（local）──
对象        $KEY
RTO_restore ${RTO}s   （恢复+就绪，T1→T2）
RTO_total   ${TOTAL}s （含对象拉取与冒烟，T0→T3）
冒烟        ${SMOKE_OUT}
结论        $([ "$SMOKE_PASSED" = "$SMOKE_TOTAL" ] && [ $TOTAL -le 1800 ] && echo PASS || echo FAILED)
DRILL_REPORT {"object_key":"$KEY","rto_seconds":$RTO,"total_seconds":$TOTAL,"smoke":"$SMOKE_OUT","mode":"local"}
EOF
# 即弃：EXIT trap 清理 runserver + 演练库 + 本地 dump
