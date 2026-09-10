#!/usr/bin/env bash
# api-db-bootstrap —— 全新 PG 上一键引导 RabbitProjects schema（api-ci 用）。
#
# 背景：Django 5.1 + swappable User 在 PG 上 `manage.py migrate` 必炸
# （ValueError: Related model 'db.user' cannot be resolved，CLAUDE.md 已知坑 ①）。
# dev 环境按 tests/e2e/PG_README.md 的 sqlmigrate 配方手工建库；本脚本把该配方
# 泛化到任意迁移数：按 `migrate --plan` 拓扑序全量 sqlmigrate → 拼接落库 →
# 补扩展 + GIN 索引 → `migrate --fake` 标记全量已应用。
#
# 用法（psql 走标准连接变量；DATABASE_URL 供 Django settings 读取）：
#   DATABASE_URL=postgresql://rp:rp@127.0.0.1:5432/rabbit_projects \
#   PGHOST=127.0.0.1 PGUSER=rp PGPASSWORD=rp PGDATABASE=rabbit_projects \
#   bash ci/api_db_bootstrap.sh
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL 必填（Django settings 数据源）}"
: "${PGDATABASE:?PGDATABASE/PGUSER/PGPASSWORD 等 psql 标准连接变量必填}"
export SECRET_KEY="${SECRET_KEY:-dev}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/apps/api"
django() { uv run --project . python manage.py "$@"; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# ① 拓扑序生成全部迁移 DDL。sqlmigrate 在 PG 连接下产出 PG 方言 DDL，
#    偏条件唯一索引（WHERE deleted_at IS NULL 等）是合法 PG 语法，必须保留——
#    sprint-1 配方曾 `grep -v WHERE` 整行剥掉，致唯一约束/偏条件索引全缺
#    （2026-09-10 scratch 库 8 条约束类测试翻车复盘；dev 库 52 个偏条件索引）。
django migrate --plan | grep -E '^[a-z_]+\.[0-9]{4}_[a-zA-Z_0-9]+$' > "$WORK/plan.txt"
[ -s "$WORK/plan.txt" ] || { echo "✗ migrate --plan 为空，无法引导" >&2; exit 1; }

: > "$WORK/all.sql"
while IFS= read -r mig; do
  # sqlmigrate 失败的迁移跳过并留痕——dev 库同口径（admin_* 等 swappable User
  # 相关联迁移本就导不出，dev 库亦无 django_admin_log 表，见 PG_README 配方）
  if ! django sqlmigrate "${mig%%.*}" "${mig#*.}" >> "$WORK/all.sql" 2>> "$WORK/sqlmigrate.err"; then
    echo "⊘ sqlmigrate 失败，跳过：${mig}" >&2
    echo "-- SKIPPED ${mig} (sqlmigrate failed; harmless comment)" >> "$WORK/all.sql"
  fi
done < "$WORK/plan.txt"

# ② 落库 + 扩展 + GIN 索引（扩展必须先于 GIN，见已知坑 ①）+ 手工 DDL 附加
psql -v ON_ERROR_STOP=1 -q < "$WORK/all.sql"
psql -v ON_ERROR_STOP=1 -q < "$ROOT/ci/manual_ddl_addendum.sql"
psql -v ON_ERROR_STOP=1 -q <<'SQL'
CREATE EXTENSION IF NOT EXISTS btree_gin;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS idx_issue_custom_fields ON issues USING GIN (custom_fields);
CREATE INDEX IF NOT EXISTS idx_issue_desc_trgm ON issues USING GIN (description_stripped);
SQL

# ③ 标记全部迁移已应用（schema 已就位，不重复执行）
django migrate --fake >/dev/null
echo "✓ api-db-bootstrap：$PGDATABASE schema 就绪（$(wc -l < "$WORK/plan.txt" | tr -d ' ') 个迁移）"
