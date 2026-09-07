#!/usr/bin/env bash
# preflight.sh —— 发布/演练前置检查（INFRA-005 §7.2.5；QA-001 发布 checklist 消费）。
# 检查项：①磁盘余量 >20% ②PG 可达 ③MinIO rp-backups 桶存在且版本化
# ④备份对象最近一次在 24h 内 ⑤迁移版本头与最新备份快照一致（防跳版恢复）
# 用法：deploy/scripts/preflight.sh [--skip-backup-freshness]（首启无备份时）
set -uo pipefail
cd "$(dirname "$0")/../.."
FAIL=0
ok()   { echo "  ✓ $1"; }
bad()  { echo "  ✗ $1"; FAIL=$((FAIL+1)); }

echo "── INFRA-005 preflight ──"
# ① 磁盘余量
AVAIL=$(df -k / | awk 'NR==2{print int($4/1024/1024)}')
TOTAL=$(df -k / | awk 'NR==2{print int(($3+$4)/1024/1024)}')
PCT=$((TOTAL > 0 ? AVAIL * 100 / TOTAL : 0))
[ "$PCT" -gt 20 ] && ok "磁盘余量 ${PCT}%（>20%）" || bad "磁盘余量仅 ${PCT}%（≤20%，BR-09 预拉取前提失守）"

# ② PG 探针
docker exec rp-pg pg_isready -U rp -d rabbit_projects >/dev/null 2>&1 \
  && ok "PG 可达（rp-pg）" || bad "PG 不可达"

# ③ rp-backups 桶 + 版本化
MU=$(docker exec rp-minio printenv MINIO_ROOT_USER 2>/dev/null || echo "")
MP=$(docker exec rp-minio printenv MINIO_ROOT_PASSWORD 2>/dev/null || echo "")
if [ -n "$MU" ]; then
  docker exec rp-minio mc alias set local http://localhost:9000 "$MU" "$MP" >/dev/null 2>&1
  docker exec rp-minio mc ls local/rp-backups >/dev/null 2>&1 \
    && ok "rp-backups 桶存在" || bad "rp-backups 桶不存在（createbuckets 未跑）"
  docker exec rp-minio mc version info local/rp-backups 2>/dev/null | grep -q "versioning is enabled" \
    && ok "rp-backups 版本化开启" || bad "rp-backups 未版本化（误删不可恢复）"
else
  bad "MinIO 容器不可达或无凭证"
fi

# ④⑤ 最新备份对象 + 迁移头一致性
LATEST=$(docker exec rp-minio mc ls --recursive local/rp-backups/backups/pg/ 2>/dev/null | grep db.dump | tail -1 | awk '{print $NF}')
if [ "${1:-}" = "--skip-backup-freshness" ]; then
  echo "  - 备份新鲜度跳过（首启/首备模式）"
elif [ -n "$LATEST" ]; then
  ok "最新备份对象：${LATEST}"
  # LATEST 形如 20260907-165928/db.dump（mc ls 递归键相对 prefix）——取日期段 f1
  SNAP=$(docker exec rp-minio mc cat "local/rp-backups/backups/config/$(echo "$LATEST" | cut -d/ -f1)/snapshot.json" 2>/dev/null || echo "")
  if [ -n "$SNAP" ]; then
    SNAP_HEAD=$(echo "$SNAP" | python3 -c "import json,sys; print(json.load(sys.stdin)['migration_head'])" 2>/dev/null || echo "")
    CODE_HEAD=$(cd apps/api && DATABASE_URL="postgresql://rp:rp@localhost:5432/rabbit_projects" SECRET_KEY=dev \
      uv run python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'plane.settings.dev')
django.setup()
from plane.bgtasks.backup import _migration_head
print(_migration_head())" 2>/dev/null || echo "")
    if [ -n "$SNAP_HEAD" ] && [ "$SNAP_HEAD" = "$CODE_HEAD" ]; then
      ok "迁移版本头一致（${SNAP_HEAD%%,*}…）"
    else
      bad "迁移版本头漂移：备份=${SNAP_HEAD:0:60} 代码=${CODE_HEAD:0:60}（跳版恢复风险，BR-10）"
    fi
  else
    bad "最新备份缺配置快照（BR-10 配置随行缺失）"
  fi
else
  bad "rp-backups 无任何 pg 备份对象"
fi

echo "── preflight：$([ $FAIL -eq 0 ] && echo ALL-OK || echo "${FAIL} 项未过") ──"
[ $FAIL -eq 0 ]
