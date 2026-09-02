# PostgreSQL 17 容器 + 真实 schema 准备

E2E 与 JMeter 都跑真实 PG（不能跑 SQLite）。本指南负责把容器 + schema 一次性建好。

## 1. 起 PG 17 容器

```bash
docker run -d --name rp-pg --network rp-net \
  -e POSTGRES_USER=rp -e POSTGRES_PASSWORD=rp -e POSTGRES_DB=rabbit_projects \
  -p 5432:5432 postgres:17-alpine
docker exec rp-pg pg_isready -U rp -d rabbit_projects   # 等待就绪
```

## 2. 安装 PG 扩展 + 导入 schema

Django `migrate` 在 PG 上当前卡 `Related model 'db.user' cannot be resolved`（Django 5.1 known issue，临时走 sqlmigrate 落 DDL）：

```bash
# 1) 生成 PG 语法 DDL（4 段）
cd /Users/xujialiang/Desktop/Dev/RabbitAI-Lab/RabbitProjects
DATABASE_URL="postgresql://rp:rp@localhost:5432/rabbit_projects" SECRET_KEY=dev \
  uv run --project apps/api python apps/api/manage.py sqlmigrate auth 0001_initial > /tmp/auth-pg.sql
DATABASE_URL="postgresql://rp:rp@localhost:5432/rabbit_projects" SECRET_KEY=dev \
  uv run --project apps/api python apps/api/manage.py sqlmigrate contenttypes 0001_initial > /tmp/ct-pg.sql
DATABASE_URL="postgresql://rp:rp@localhost:5432/rabbit_projects" SECRET_KEY=dev \
  uv run --project apps/api python apps/api/manage.py sqlmigrate sessions 0001_initial > /tmp/sess-pg.sql
DATABASE_URL="postgresql://rp:rp@localhost:5432/rabbit_projects" SECRET_KEY=dev \
  uv run --project apps/api python apps/api/manage.py sqlmigrate db 0002_initial > /tmp/db-2-pg.sql

# 2) 拼接 + 落库（sqlmigrate 不输出偏条件 unique WHERE；PG 部分 UNIQUE 兼容）
cat /tmp/ct-pg.sql /tmp/auth-pg.sql /tmp/db-2-pg.sql /tmp/sess-pg.sql | grep -v "WHERE" > /tmp/all-clean.sql
docker exec -i rp-pg psql -U rp -d rabbit_projects -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
docker exec -i rp-pg psql -U rp -d rabbit_projects < /tmp/all-clean.sql

# 3) 补扩展 + GIN 索引（migration 0001 拆出 + 手工落）
docker exec -i rp-pg psql -U rp -d rabbit_projects <<'SQL'
CREATE EXTENSION IF NOT EXISTS btree_gin;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS idx_issue_custom_fields ON issues USING GIN (custom_fields);
CREATE INDEX IF NOT EXISTS idx_issue_desc_trgm ON issues USING GIN (description_stripped);
SQL

# 4) 标记 migrations 为 applied（schema 已建好；不重复执行）
DATABASE_URL="postgresql://rp:rp@localhost:5432/rabbit_projects" SECRET_KEY=dev \
  uv run --project apps/api python apps/api/manage.py migrate --fake
```

## 3. 启动 API

```bash
DATABASE_URL="postgresql://rp:rp@localhost:5432/rabbit_projects" \
SECRET_KEY=dev \
uv run --project apps/api python apps/api/manage.py runserver 0.0.0.0:8000
```

健康检查：
```bash
curl -s http://localhost:8000/api/v1/health/ | python3 -m json.tool
# 期望：{"status": "ok", "checks": {"db": "ok"}}
```

## 4. 启动 Web dev server（e2e 才会自动起，可手动起做调试）

```bash
LIVE_PORT=3000 API_INTERNAL_URL=http://localhost:8000 pnpm dev:web
# 或跑全部：pnpm dev:all
```

## 5. 验证

```bash
# 注册跑通
COOKIE=/tmp/c.txt
curl -s -c $COOKIE -X POST http://localhost:8000/api/v1/auth/sign-up/ \
  -H "Content-Type: application/json" \
  -d '{"email":"final@rabbit.dev","password":"Rabbit123","display_name":"终验"}' | python3 -m json.tool

# PG 端确认 issue 已落（advisory lock 真实工作）
docker exec rp-pg psql -U rp -d rabbit_projects \
  -c "SELECT sequence_id, sort_order, name FROM issues ORDER BY sequence_id DESC LIMIT 5;"

# 验 GIN trgm 索引（防描述模糊搜索）
docker exec rp-pg psql -U rp -d rabbit_projects \
  -c "EXPLAIN SELECT id FROM issues WHERE description_stripped ILIKE '%拖拽%';"
```
