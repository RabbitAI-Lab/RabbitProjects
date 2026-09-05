#!/usr/bin/env bash
# COLLAB-004 §4.1 / INFRA-004 配置登记：生成 live 实时票据 RS256 密钥对。
#
# 用法：
#   scripts/gen_live_keys.sh [输出目录]        # 默认 ./deploy/keys（已 gitignore 语义：自行保管）
#
# 产物：
#   live_jwt_private_key.pem   私钥（仅注入 api 容器：LIVE_JWT_PRIVATE_KEY）
#   live_jwt_public_key.pem    公钥（注入 live 容器：LIVE_JWT_PUBLIC_KEY）
#   stdout                     .env 片段（单行 \n 转义形态，可直接粘贴到 .env）
#
# 密钥分离论证（api-conventions.md §9.5）：私钥仅 api 持有，live 只持公钥——
# live 被攻破也无法伪造票据。
set -euo pipefail

OUT_DIR="${1:-deploy/keys}"
mkdir -p "$OUT_DIR"
PRIV="$OUT_DIR/live_jwt_private_key.pem"
PUB="$OUT_DIR/live_jwt_public_key.pem"

umask 077
openssl genrsa -out "$PRIV" 2048 2>/dev/null
openssl rsa -in "$PRIV" -pubout -out "$PUB" 2>/dev/null
umask 022

# INTERNAL_KEY：服务间共享密钥（§9.7），openssl 随机 32 hex
INTERNAL_KEY_HEX="$(openssl rand -hex 24)"

# 单行 \n 转义（PEM 多行在 .env 中也可用引号包裹的真实换行，两种形态等价；
# api/live 侧读取时统一做 \n → 换行归一）
PRIV_ESCAPED="$(awk '{printf "%s\\n", $0}' "$PRIV")"
PUB_ESCAPED="$(awk '{printf "%s\\n", $0}' "$PUB")"

cat <<EOF
# ── COLLAB-004 live 实时票据密钥（由 scripts/gen_live_keys.sh 生成于 $(date '+%Y-%m-%d %H:%M:%S')）──
# 私钥仅注入 api（compose env_file 已透传）；公钥注入 live 容器。
LIVE_JWT_PRIVATE_KEY="$PRIV_ESCAPED"
LIVE_JWT_PUBLIC_KEY="$PUB_ESCAPED"
INTERNAL_KEY=$INTERNAL_KEY_HEX
LIVE_TICKET_TTL=120
LIVE_HEARTBEAT=25
EOF

printf "\n" >&2
echo "keys generated: $PRIV / $PUB (private chmod 600; do NOT commit)" >&2
