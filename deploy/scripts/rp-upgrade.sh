#!/usr/bin/env bash
# rp-upgrade：版本检查 → 备份快照 → 镜像更新 → 迁移 → 冒烟 → 失败回滚（BR-08）
set -euo pipefail
NEW_TAG="${1:?用法: rp-upgrade <新镜像 tag>}"
echo "== ① 备份快照（强制，BR-08）=="
POD0=$(kubectl get pods -l app=rp-api -o jsonpath='{.items[0].metadata.name}')
kubectl exec "$POD0" -- python manage.py dumpdata --exclude=plane_ai > "backup-pre-$NEW_TAG.json"
echo "== ② 镜像更新（maxUnavailable=0）=="
kubectl set image deployment/rp-api api=rabbitprojects/api:"$NEW_TAG"
kubectl set image deployment/rp-worker worker=rabbitprojects/api:"$NEW_TAG"
echo "== ③ 迁移执行（只前滚）=="
POD=$(kubectl get pods -l app=rp-api -o jsonpath='{.items[0].metadata.name}')
kubectl exec "$POD" -- python manage.py migrate --noinput
echo "== ④ 冒烟 =="
sleep 10
if ! kubectl rollout status deployment/rp-api --timeout=180s; then
  echo "✗ 冒烟失败——回滚应用层（DB 前滚保留，需人工评估）"
  kubectl rollout undo deployment/rp-api
  kubectl rollout undo deployment/rp-worker
  exit 1
fi
echo "✓ 升级完成 $NEW_TAG"
