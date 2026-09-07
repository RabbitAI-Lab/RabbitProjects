# Changelog

本文件格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵守语义化版本；条目关联文档编号与缺陷单号（QA-001 §2.6 规范）。

## 1.0.0 — 2026-09-08

标准版 V1.0（Sprint 0~6 全量；本条目为 Sprint-6 稳定性缓冲迭代的增量）。

### Added
- 三层限流全量落地：L1 Nginx 三区（api/auth/public）+ geo/map 内网白名单 +
  网关 429 体；L2/L3 DRF Throttle 家族十类（INFRA-005 §4.3）；全响应
  X-RateLimit-* 头 + 429 Retry-After 闭环（BR-02/06）。
- 备份体系：每日 03:07 全量（三校验：大小/pg_restore --list/SHA-256）+
  配置快照随行 + 30 天双保险保留 + 连败告警；恢复演练脚本 RTO 实测 2s +
  冒烟 18 项（INFRA-005 §4.4）。
- 生产部署：docker-compose.prod.yml 覆盖层（网络三分段/端口收敛 80/
  api·worker×2 beat×1/只读根+cap_drop/rp-backups 桶+backup-sync）；
  K8s 清单骨架 + kind 对象级冒烟（INFRA-005 §4.5）。
- 发布门禁：ReleaseGate/Event（append-only 事件流）+ 六端点 + checklist
  8 项签署/反签 + 裁决守卫 BLOCKED_BY_GATE（QA-001 §4.1/4.4）。
- 安全门禁 CI：pip-audit/pnpm audit/trivy/gitleaks 四源 + BR-06 判定器
  （Critical 零容忍 / High 凭豁免单且过期即失效）（QA-001 §4.3）。
- admin 运维台：限流监控 / 备份管理 / 发布门禁三区四页（C.134~C.136）。
- GitHub 配额状态端点（degraded 旗标暴露）+ INTEGRATION_SECRET_KEY 注入位
  （INTG-002 交接项 3/5）。
- 越权矩阵 64 格（四主体×四资源×四动作，IT-SEC-01~64）。

### Fixed
- FileAsset.workspace NOT NULL 与头像域写 None 的 schema 漂移（0020；
  avatar presign 必 IntegrityError 的 AUTH-004 期旧缺陷）。
- sprint-5-flow 数据域清理缺失（残留副本打爆 test_rpt002）与 C7-2 INSERT
  缺列（F 表 victim 残留真身）。
- coverage/pytest-cov 未入锁文件（uv sync 即剪掉导致 TC-COVER 误红）。

### Security
- worker 镜像 pg 工具链分层；MinIO 凭据经容器 env 注入（不落编排文件）；
  备份产物 SSE-AES256（MinIO 无 KMS 时降级明文并告警——known-debt）。
- 响应头基线核查脚本（XFO/nosniff/Referrer-Policy；prod 追加 HSTS）。
