#!/usr/bin/env bash
# smoke.sh —— 发布/演练冒烟用例集（INFRA-005 §4.4.4 / QA-001 §4.5 同一口径）。
# 用法：smoke.sh --suite release-18 --base-url http://localhost:8000/api/v1
# 输出：末行 "SMOKE <passed>/<total>"；退出码 0 = 全过。
# 18 项（release-18）：健康/会话/项目/任务/评论/动态/状态/标签/统计/预签名/登出。
set -uo pipefail

BASE="http://localhost:8000/api/v1"
while [ $# -gt 0 ]; do
  case "$1" in
    --suite) SUITE="$2"; shift 2 ;;
    --base-url) BASE="${2%/}"; shift 2 ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done
: "${SUITE:?--suite required}"
[ "$SUITE" = "release-18" ] || { echo "suite $SUITE 未实现（当前仅 release-18）" >&2; exit 2; }

PASS=0; TOTAL=0
JAR=$(mktemp); BODY=$(mktemp); HDR=$(mktemp)
trap 'rm -f "$JAR" "$BODY" "$HDR"' EXIT

req() { # method path [json] -> writes $BODY/$HDR, echoes status
  local m="$1" p="$2" data="${3:-}"
  local args=(-s -o "$BODY" -D "$HDR" -w '%{http_code}' -X "$m" \
    --cookie "$JAR" --cookie-jar "$JAR" -H "Accept: application/json" \
    -H "Referer: ${BASE%/api/v1}/" --max-time 15 "$BASE$p")
  if [ "$m" != "GET" ]; then
    local t; t=$(awk '$6=="csrftoken"{print $7}' "$JAR" | tail -1)
    args+=(-H "X-CSRFToken: $t")
  fi
  if [ -n "$data" ]; then args+=(-H 'Content-Type: application/json' -d "$data"); fi
  curl "${args[@]}"
}

csrf() { req GET /auth/csrf-token/ >/dev/null; }
tok() { awk '$6=="csrftoken"{print $7}' "$JAR" | tail -1; }
post() { local p="$1" d="$2"; req POST "$p" "$d"; }
code_of() { grep -m1 '^HTTP' "$HDR" | awk '{print $2}'; }
jqget() { python3 -c "import json,sys; d=json.load(open('$BODY')); print(eval(sys.argv[1]))" "$1" 2>/dev/null || echo ""; }

t() { # name expect_status [extra_check_expr]
  local name="$1" want="$2" got="$3"; shift 3
  TOTAL=$((TOTAL+1))
  if [ "$got" = "$want" ] && { [ $# -eq 0 ] || eval "$1"; }; then
    PASS=$((PASS+1)); echo "  ✓ $name"
  else
    echo "  ✗ ${name}(want=${want} got=${got} ${1:+extra=FAIL}) body=$(head -c 420 "$BODY" | tr '\n' ' ')"
  fi
}

STAMP=$RANDOM
# ── 会话与实例健康 ──
t "S01 health 探针" 200 "$(req GET /health/)"
csrf
t "S02 csrf-token 签发" 200 "$(req GET /auth/csrf-token/)"
EMAIL="smoke-$STAMP@rabbit.dev"
# bash 3.2 嵌套 $() 内的转义引号会被解析器吃掉（VALIDATION_INVALID_JSON 教训）——JSON 一律先建变量
SU_JSON=$(printf '{"email":"%s","password":"Rabbit123!","display_name":"冒烟"}' "$EMAIL")
t "S03 注册即登录" 201 "$(post /auth/sign-up/ "$SU_JSON")"
WS=$(jqget "d['data']['default_workspace_slug']")
[ -n "$WS" ] || WS="w-smoke-$STAMP"
csrf
t "S04 users/me 回读" 200 "$(req GET /users/me/)"
t "S05 登出（204 C1 例外）" 204 "$(req POST /auth/sign-out/ '')"
csrf; SI_JSON=$(printf '{"email":"%s","password":"Rabbit123!"}' "$EMAIL")
req POST /auth/sign-in/ "$SI_JSON" >/dev/null
csrf
# ── 项目与任务主链 ──
PROJ_JSON=$(printf '{"name":"冒烟 S%s","identifier":"SMK%s"}' "$STAMP" "$((STAMP % 97))")
PID=$(post "/workspaces/$WS/projects/" "$PROJ_JSON" >/dev/null; jqget "d['data']['id']")
t "S06 建项目" 200 "$(req GET "/workspaces/$WS/projects/$PID/")"
csrf
ISSUE_JSON='{"name":"冒烟任务","priority":"none"}'
IID=$(post "/workspaces/$WS/projects/$PID/issues/" "$ISSUE_JSON" >/dev/null; jqget "d['data']['id']")
t "S07 建任务" 200 "$(req GET "/workspaces/$WS/projects/$PID/issues/$IID/")"
csrf
PATCH_JSON='{"description":"冒烟更新"}'
t "S08 改任务" 200 "$(req PATCH "/workspaces/$WS/projects/$PID/issues/$IID/" "$PATCH_JSON")"
csrf
COMMENT_JSON='{"comment_html":"冒烟评论"}'
t "S09 任务评论" 201 "$(post "/workspaces/$WS/projects/$PID/issues/$IID/comments/" "$COMMENT_JSON")"
t "S10 评论列表" 200 "$(req GET "/workspaces/$WS/projects/$PID/issues/$IID/comments/")"
t "S11 项目动态流" 200 "$(req GET "/workspaces/$WS/projects/$PID/activities/")"
t "S12 状态列表" 200 "$(req GET "/workspaces/$WS/projects/$PID/states/")"
csrf
LABEL_JSON=$(printf '{"name":"冒烟标%s","color":"#3B82F6"}' "$STAMP")
t "S13 建标签" 201 "$(post "/workspaces/$WS/projects/$PID/labels/" "$LABEL_JSON")"
t "S14 标签列表" 200 "$(req GET "/workspaces/$WS/projects/$PID/labels/")"
t "S15 任务列表" 200 "$(req GET "/workspaces/$WS/projects/$PID/issues/")"
# ── 聚合与凭证面 ──
t "S16 项目统计" 200 "$(req GET "/workspaces/$WS/projects/$PID/stats/?days=7")"
csrf
PRESIGN_JSON='{"file_name":"a.png","file_size":100,"content_type":"image/png"}'
t "S17 头像预签名" 201 "$(post /users/me/avatar/presign/ "$PRESIGN_JSON")"
t "S18 登出收口（204）" 204 "$(req POST /auth/sign-out/ '')"

echo "SMOKE $PASS/$TOTAL"
[ "$PASS" = "$TOTAL" ]
