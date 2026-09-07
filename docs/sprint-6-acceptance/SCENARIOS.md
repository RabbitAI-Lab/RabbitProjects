# Sprint-6 验收视频契约（六幕）

> 录制器：`scripts/acceptance_video_s6.mjs`（`node scripts/acceptance_video_s6.mjs`，
> 单幕 `ONLY=<关键字>`）。视频永不在库（*.webm 全局 gitignore，沿用 sprint-3/5 口径）。
> 前置：API 8000（全量 env）+ admin 3002 + PG/Redis/MinIO/rp-pg 容器 + mock 不需要。

| 幕 | 内容 | 验收点（INFRA-005/QA-001 §7.2） |
| --- | --- | --- |
| 01 | 限流矩阵逐端点 | 用户桶 61→429 信封+头归零；auth/报表/批量/presign 触发边界；8/8 |
| 02 | L1 边缘三区 | 严格变体连发 100 → 64×200+36×429；网关 429 JSON + Retry-After；health 豁免 |
| 03 | 备份全链 + preflight | BACKUP_OK（三校验+快照随行）；preflight 六项 ALL-OK；桶内对象 |
| 04 | 恢复演练 | restore-drill：RTO_restore 2s / 冒烟 18/18 / 结论 PASS / 即弃零残留 |
| 05 | admin 运维台实操作 | 限流快照页；备份页立即备份；发布门禁创建→签署→反签→裁决守卫→时间线 |
| 06 | 安全门禁 + 越权 64 格 | verdict PASS（Critical 零容忍/豁免三态）；IT-SEC 64 passed |

终端幕（01~04/06）画面为「真实命令执行的完整输出」渲染（命令在本录制器内
真实执行，输出逐行回放展示；无任何手写脚本数据）。浏览器幕（05）为真实
页面操作（ops-parity 系统管理员会话）。
