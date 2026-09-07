#!/usr/bin/env bash
# g1-webhook-regress.sh —— G1 回归探针（Sprint-6 known-tech-debt #11 收口）。
#
# 前置（用户侧，一次性）：
#   ① App 设置 → Permissions → Webhooks → Repository webhooks = Read & write → Save
#   ② 组织 installation 重批新权限：
#      github.com/organizations/RabbitAI-Lab/settings/installations →
#      rabbit-projects → Review request → Approve（或卸载重装，效果等同）
# 用法：bash scripts/g1-webhook-regress.sh
# 判定：hook 存在且 config.url 指向本系统入站端点 → G1 PASS。
set -euo pipefail
cd "$(dirname "$0")/../apps/api"
set -a; [ -f ../../.env ] && source ../../.env; set +a
export DATABASE_URL="${DATABASE_URL:-postgresql://rp:rp@localhost:5432/rabbit_projects}"
export SECRET_KEY="${SECRET_KEY:-dev}"
export GITHUB_APP_ID="${GITHUB_APP_ID:-4859835}"
KEY="${GITHUB_APP_PRIVATE_KEY:-}"
[ -z "$KEY" ] && [ -f ~/Downloads/rabbit-projects.2026-09-07.private-key.pem ] \
  && export GITHUB_APP_PRIVATE_KEY="$(cat ~/Downloads/rabbit-projects.2026-09-07.private-key.pem)"

uv run python - << 'PYEOF'
import os, sys, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "plane.settings.dev")
django.setup()
from plane.integrations.github import GitHubClient

REPO = "rabbitai-lab/rabbittest"
CALLBACK = "http://projects-server.passnat.iapp.link/api/v1/integrations/github/webhook/"
c = GitHubClient(159744359)

hooks = []
try:
    resp = c._call("GET", f"/repos/{REPO}/hooks")
    hooks = (resp.get("json") or [])
except Exception as e:
    print(f"LIST-ERR: {str(e)[:200]}")

ours = [h for h in hooks if CALLBACK in (h.get("config") or {}).get("url", "")]
if ours:
    h = ours[-1]
    print(f"EXISTING hook id={h['id']} url={h['config']['url']} active={h.get('active')}")
    print("G1: PASS（per-repo webhook 已在仓上，产品标准路径通）")
    sys.exit(0)

try:
    r = c.register_webhook(REPO, callback_url=CALLBACK, secret="g1-regression-probe")
    print(f"REGISTERED hook id={r['id']} status={r['status']}")
    print("G1: PASS（本次绑仓路径注册成功——权限已生效）")
except Exception as e:
    msg = str(e)
    print(f"REGISTER-ERR: {msg[:240]}")
    if "Resource not accessible by integration" in msg:
        print("G1: FAIL —— installation token 仍无 repository webhooks 写权限。")
        print("      → 组织 owner 到 github.com/organizations/RabbitAI-Lab/settings/installations")
        print("        重批 rabbit-projects 的新权限（Review request → Approve，或卸载重装）后重跑本脚本。")
    sys.exit(1)
PYEOF
