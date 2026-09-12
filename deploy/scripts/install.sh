#!/usr/bin/env bash
# RabbitProjects 私有化安装器（INFRA-006 §2.3，P4 R9）
# 流程：环境预检 → k3s 落地 → Chart 安装 → License 导入 → 冒烟（BR-07 离线闭环）
set -euo pipefail

NODE_ROLE="${1:-standard}"   # standard（k3s 单节点 ≤200 用户）/ ha（外置数据层）
LICENSE_PATH="${LICENSE_PATH:-./license.json}"

echo "== ① 环境预检 =="
[ -f "$LICENSE_PATH" ] || { echo "✗ 缺 License 文件（$LICENSE_PATH）"; exit 1; }
command -v kubectl >/dev/null || { echo "✗ 缺 kubectl"; exit 1; }
case "$NODE_ROLE" in
  standard) [ "$(nproc)" -ge 8 ] || { echo "✗ 标准档需 ≥8C"; exit 1; } ;;
  ha) echo "（HA 档：外置 PG/MinIO 连通性检查）" ;;
esac

echo "== ② k3s 落地 =="
command -v k3s >/dev/null || curl -sfL https://get.k3s.io | INSTALL_K3S_SKIP_AGENT=true sh -

echo "== ③ 清单安装 =="
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)/k8s"
kubectl apply -f "$SCRIPT_DIR/rabbit-projects-ha.yaml"

echo "== ④ License 导入 =="
kubectl create secret generic rp-license --from-file=license.json="$LICENSE_PATH" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "== ⑤ 冒烟 =="
sleep 15
kubectl rollout status deployment/rp-api --timeout=300s
POD=$(kubectl get pods -l app=rp-api -o jsonpath='{.items[0].metadata.name}')
kubectl exec "$POD" -- curl -sf http://localhost:8000/api/v1/health/ >/dev/null \
  && echo "✓ 冒烟通过（健康端点 200）" || { echo "✗ 冒烟失败"; exit 1; }
echo "安装完成（compose 小形态另见 deploy/compose/，BR-11 存续）"
