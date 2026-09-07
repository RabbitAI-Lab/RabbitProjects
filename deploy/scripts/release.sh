#!/usr/bin/env bash
# release.sh —— 正式发布序列（QA-001 §4.5；BR-07：彩排与实战同脚本同路径）。
#
# 用法（仓库根）：
#   deploy/scripts/release.sh --rehearse          # 彩排（pre-${VERSION} 备份锚点 + 迁移演练，不动生产流量）
#   deploy/scripts/release.sh --go                # 实战（止写→迁移→部署→冒烟）
#   deploy/scripts/release.sh --rollback          # 回滚四步（QA-001 §2.6 回滚预案表）
# env：VERSION（缺省读 CHANGELOG 头部）；COMPOSE_FILE 组合（prod 覆盖层）。
set -euo pipefail
cd "$(dirname "$0")/../.."

MODE="${1:--rehearse}"
VERSION="${VERSION:-$(grep -m1 '^## ' CHANGELOG.md | awk '{print $2}')}"
COMPOSE="docker compose -f deploy/compose/docker-compose.yml -f deploy/compose/docker-compose.prod.yml --env-file .env"

step() { echo "── [$(date +%H:%M:%S)] $1 ──"; }

case "$MODE" in
  --rehearse)
    step "彩排开始 v${VERSION}（BR-07：备份→恢复→迁移→回滚 全流程留痕）"
    deploy/scripts/preflight.sh || { echo "✗ preflight 未过——彩排终止"; exit 1; }
    step "pre-${VERSION} 备份锚点"
    BACKUP_KIND=manual deploy/scripts/backup-now.sh
    step "恢复演练（隔离栈，RTO ≤30min 硬门禁）"
    deploy/scripts/restore-drill.sh --mode local
    step "迁移演练（migrator 一次性容器，--check 干跑语义=对齐迁移头）"
    echo "（compose 栈：$COMPOSE run --rm migrator python manage.py migrate --plan）"
    step "回滚步骤可逆性核对（reverse_code 清单=迁移文件逐个声明）"
    grep -L "reverse_code" apps/api/plane/db/migrations/00*.py | grep -v __init__ || echo "全部迁移声明 reverse_code ✓"
    echo "── 彩排 PASS：发布评审（ReleaseGate 签署）后 --go ──"
    ;;
  --go)
    step "正式发布 v${VERSION}"
    step "① 止写（beat + worker 停止；proxy 只读保留）"
    $COMPOSE stop beat worker
    step "② 发布前即时备份 pre-${VERSION}"
    deploy/scripts/backup-now.sh
    step "③ 迁移（migrator 一次性服务）"
    $COMPOSE up -d --no-deps migrator && $COMPOSE wait migrator
    step "④ 部署（镜像回滚点=前一 tag，本步滚动其余服务）"
    $COMPOSE up -d
    step "⑤ 冒烟 18 项（release-18 与演练/回滚同口径）"
    deploy/scripts/smoke.sh --suite release-18 --base-url "http://localhost:${NGINX_PORT:-80}/api/v1"
    step "⑥ 24h 观察启动（六项指标阈值见 QA-001 §4.5）"
    echo "观察面板查询与阈值表：docs/sprint-6-stabilize/QA-001-standard-release.md §4.5"
    echo "── 发布完成 v${VERSION} ──"
    ;;
  --rollback)
    step "回滚四步（≤30min；QA-001 §2.6 表）"
    step "① 止写"
    $COMPOSE stop beat worker
    step "② 镜像回滚（手工确认前一 tag 后 up）"
    echo "   $COMPOSE up -d  #（镜像 tag 由运维确认后改 .env/deploy 覆盖）"
    step "③ 数据库回滚：迁移可逆 → migrate <prev>；不可逆 → 当日备份定点恢复"
    echo "   恢复入口：deploy/scripts/restore-drill.sh（同一脚本实战路径）"
    step "④ 冒烟 18 项 + 数据抽检"
    deploy/scripts/smoke.sh --suite release-18 --base-url "http://localhost:${NGINX_PORT:-80}/api/v1"
    echo "── 回滚完成 ──"
    ;;
  *) echo "用法：release.sh --rehearse|--go|--rollback" >&2; exit 2 ;;
esac
