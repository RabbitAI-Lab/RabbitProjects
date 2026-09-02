#!/usr/bin/env bash
# Sprint 0 静态检查 runner —— 覆盖 test-cases.md 中 L1/L2 命令型用例（CI 单线程可跑）
# 用法：bash tests/run-ci-checks.sh   （仓库根执行；DB 实库检查需 rp-pg 容器在跑，未跑则自动跳过）
# 退出码：0 全过；1 有失败（失败清单见输出）
set -uo pipefail
PASS=0; FAIL=0; FAILED_IDS=()

check() {  # check <TC-ID> <描述> <命令...>
  local id="$1" desc="$2"; shift 2
  if eval "$@" >/dev/null 2>&1; then
    PASS=$((PASS+1)); echo "✓ $id $desc"
  else
    FAIL=$((FAIL+1)); FAILED_IDS+=("$id"); echo "✗ $id $desc"
  fi
}

# ── INFRA-001 Monorepo 骨架（L1）──
check TC-INF1-001 "workspace 排除 api/proxy" \
  "grep -c '!apps/api' pnpm-workspace.yaml | grep -q '[1-9]'"
check TC-INF1-002 "allowBuilds 含三个构建包" \
  "grep -A4 'allowBuilds' pnpm-workspace.yaml | grep -q esbuild"
check TC-INF1-003 "turbo 任务齐备" \
  "grep -q '\"dev:watch\"' turbo.json && grep -q '\"build-storybook\"' turbo.json"
check TC-INF1-004 "Yjs 同版本守卫" \
  "source ~/.nvm/nvm.sh >/dev/null 2>&1; nvm use 22.14.0 --silent 2>/dev/null; node scripts/check-yjs-version.mjs"
check TC-INF1-013 "compose 服务数=14" \
  "[ \"\$(docker compose --env-file .env -f deploy/compose/docker-compose.yml config --services 2>/dev/null | wc -l | tr -d ' ')\" = '14' ]"

# ── INFRA-002 Compose 编排（L2）──
check TC-INF2-001 "compose config 可解析" \
  "docker compose --env-file .env -f deploy/compose/docker-compose.yml config >/dev/null"
check TC-INF2-003 "依赖序 api→migrator completed + db healthy" \
  "docker compose --env-file .env -f deploy/compose/docker-compose.yml config | grep -A1 'migrator:' | grep -q 'service_completed_successfully'"
check TC-INF2-004 "api entrypoint 不含 migrate" \
  "! grep -q 'manage.py migrate' apps/api/bin/docker-entrypoint-api.sh"
check TC-INF2-005 "migrator entrypoint 含 migrate" \
  "grep -q 'manage.py migrate' apps/api/bin/docker-entrypoint-migrator.sh"
check TC-INF2-006 "一次性服务 restart=no" \
  "[ \"\$(grep -c 'restart: \"no\"' deploy/compose/docker-compose.yml)\" -ge 2 ]"
check TC-INF2-007 "healthcheck ≥9" \
  "[ \"\$(grep -c healthcheck deploy/compose/docker-compose.yml)\" -ge 9 ]"
check TC-INF2-008 "RabbitMQ start_period=40s" \
  "grep -A20 'mq:' deploy/compose/docker-compose.yml | grep -q 'start_period: 40s'"
check TC-INF2-009 "Nginx 5 路由" \
  "[ \"\$(grep -c 'location' apps/proxy/nginx.conf.template)\" -ge 5 ]"
check TC-INF2-010 "WebSocket upgrade 头" \
  "grep -q 'Upgrade \$http_upgrade' apps/proxy/nginx.conf.template"
check TC-INF2-011 "init-extensions 含 pg_trgm" \
  "grep -q pg_trgm deploy/compose/init/init-extensions.sql"
check TC-INF2-014 "api 用 env_file" \
  "grep -q 'env_file' deploy/compose/docker-compose.yml"
check TC-INF2-015 "web Dockerfile 带 VITE args" \
  "grep -q 'VITE_API_BASE_URL' apps/web/Dockerfile"

# ── INFRA-003 数据模型（L1/L2）──
check TC-INF3-001 "User 手工对齐 BaseModel 三项" \
  "grep -q 'id = models.UUIDField' apps/api/plane/db/models/user.py && grep -q 'deleted_at' apps/api/plane/db/models/user.py"
check TC-INF3-002 "AUTH_USER_MODEL=db.User" \
  "grep -q 'AUTH_USER_MODEL = \"db.User\"' apps/api/plane/settings/common.py"
check TC-INF3-003 "User swappable" \
  "grep -q 'swappable' apps/api/plane/db/models/user.py"
check TC-INF3-004 "BaseModel 软删除 Manager" \
  "grep -q 'SoftDeleteManager' apps/api/plane/db/models/base.py && grep -q 'all_objects' apps/api/plane/db/models/base.py"
check TC-INF3-005 "扩展前置迁移独立" \
  "grep -q 'TrigramExtension' apps/api/plane/db/migrations/0001_extensions.py && ! grep -q 'CreateModel' apps/api/plane/db/migrations/0001_extensions.py"
check TC-INF3-006 "14 领域模型注册" \
  "[ \"\$(grep -c '^from \.' apps/api/plane/db/models/__init__.py)\" -ge 9 ]"
check TC-INF3-007 "Issue 含 custom_fields" \
  "grep -q 'custom_fields' apps/api/plane/db/models/issue.py"
check TC-INF3-008 "GIN custom_fields 索引在模型" \
  "grep -q 'idx_issue_custom_fields' apps/api/plane/db/models/issue.py"
check TC-INF3-009 "GIN trgm 索引在模型" \
  "grep -q 'idx_issue_desc_trgm' apps/api/plane/db/models/issue.py"
check TC-INF3-013 "unique_together/UniqueConstraint 并存" \
  "grep -q 'unique_together' apps/api/plane/db/models/workspace.py && grep -q 'UniqueConstraint' apps/api/plane/db/models/issue.py"
check TC-INF3-014 "State 5 group 枚举" \
  "[ \"\$(grep -c 'BACKLOG\|UNSTARTED\|STARTED\|COMPLETED\|CANCELLED' apps/api/plane/db/models/state.py)\" -ge 5 ]"
check TC-INF3-015 "IssueType is_system" \
  "grep -q 'is_system' apps/api/plane/db/models/issue_type.py"
if docker exec rp-pg pg_isready -U rp -d rabbit_projects >/dev/null 2>&1; then
  check TC-INF3-008-db "PG 实库 GIN custom_fields" \
    "docker exec rp-pg psql -U rp -d rabbit_projects -tAc \"SELECT indexname FROM pg_indexes WHERE tablename='issues' AND indexname='idx_issue_custom_fields'\" | grep -q idx"
  check TC-INF3-009-db "PG 实库 GIN trgm" \
    "docker exec rp-pg psql -U rp -d rabbit_projects -tAc \"SELECT indexname FROM pg_indexes WHERE tablename='issues' AND indexname='idx_issue_desc_trgm'\" | grep -q idx"
else
  echo "⊘ PG 容器未运行，跳过 2 条 DB 实库检查（TC-INF3-008-db/009-db）"
fi

# ── AUTH-003 权限模型（L2）──
check TC-AUTH3-004 "整数角色等级" \
  "grep -q 'OWNER = 20' apps/api/plane/db/models/roles.py && grep -q 'GUEST = 5' apps/api/plane/db/models/roles.py"
check TC-AUTH3-005 "越权 404 而非 403" \
  "grep -q 'RESOURCE_NOT_FOUND' apps/api/plane/app/views/projects.py"
check TC-AUTH3-006 "actor FK SET_NULL" \
  "grep -A2 'actor = models.ForeignKey' apps/api/plane/db/models/issue.py | grep -q 'SET_NULL'"

# ── TASK/BOARD 服务层（L2）──
check TC-TASK1-008 "sequence 唯一约束" \
  "grep -q 'uniq_issue_sequence_per_project' apps/api/plane/db/models/issue.py"
check TC-BOARD1-007 "DEFAULT_GAP=65535" \
  "grep -q 'DEFAULT_GAP = 65535' apps/api/plane/db/services/sort_order.py"

echo ""
echo "════════════════════════════════════"
echo "静态检查：$PASS 通过 / $FAIL 失败"
if [ ${#FAILED_IDS[@]} -gt 0 ]; then
  printf '失败用例：%s\n' "${FAILED_IDS[*]}"
  exit 1
fi
echo "全部通过 ✓"
