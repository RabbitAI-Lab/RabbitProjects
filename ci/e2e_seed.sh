#!/usr/bin/env bash
# e2e 演示数据种子 —— 全新库（CI / 本地彩排）一键铺底。
#
# 背景：e2e 套件不是全自建数据——C.35 工作台非空断言、auth/parity 的演示账号
# 「一键进入」、my-tasks 的 /workspace/ 深链都依赖 dev 库累积的演示数据基线。
# 本脚本把该基线固化成可重放配方（20260923 每夜在 scratch 库彩排验证）。
#
# 前置：API 8000 已起（zhangsan 注册走 API）、schema 已 bootstrap
# （DATABASE_URL/PG* 就绪，见 ci/api_db_bootstrap.sh）。
# 用法：DATABASE_URL=… SECRET_KEY=dev bash ci/e2e_seed.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
: "${DATABASE_URL:?DATABASE_URL 必填}"
export SECRET_KEY="${SECRET_KEY:-dev}"

BASE="${E2E_API_BASE:-http://localhost:8000}"

# ① 演示账号 zhangsan：必须是全库首用户——纯中文 display_name 触发 slug 兜底
#    「workspace」，与 dev 库 artifact 一致（my-tasks.spec 硬编码 /workspace/ 依赖它）。
CSRF=$(curl -fsS "$BASE/api/v1/auth/csrf-token/" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['csrf_token'])")
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/v1/auth/sign-up/" \
  -H "X-CSRFToken: $CSRF" -H "Content-Type: application/json" \
  -d '{"email":"zhangsan@rabbit.dev","password":"Rabbit123","display_name":"张三"}')
# 400/409=已存在（幂等重跑口径）
[ "$CODE" = "201" ] || [ "$CODE" = "400" ] || [ "$CODE" = "409" ] || { echo "✗ zhangsan 注册失败 http $CODE" >&2; exit 1; }
echo "✓ zhangsan@rabbit.dev 就绪（http ${CODE}，slug=workspace）"

# ② 各迭代验收种子（全部幂等可重跑；s8/p4r2 走 ORM 直写库，收 BASE 参数的脚本多传无害）
for s in seed_acceptance seed_acceptance_s3 seed_acceptance_s4 seed_acceptance_s5 \
         seed_acceptance_s7 seed_acceptance_s8 seed_acceptance_s9 seed_acceptance_p4r2; do
  echo "── $s"
  uv run --project apps/api python "scripts/$s.py" "$BASE" > /tmp/e2e-seed-$s.log 2>&1 \
    || { echo "✗ $s 失败：" >&2; tail -n 40 /tmp/e2e-seed-$s.log >&2; echo "── API 访问日志尾部：" >&2; grep -E '40[0-9]' /tmp/api.log | tail -n 10 >&2; exit 1; }
done

# ③ 近 7 日完成事件（C.35 统计卡非零 + 趋势非平线）
uv run --project apps/api python scripts/seed_demo_history.py
echo "✓ e2e 演示数据种子完成"
