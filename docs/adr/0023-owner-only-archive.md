# ADR-0023 · 归档权限收窄至 WS_OWNER

| 项 | 内容 |
| --- | --- |
| 状态 | 已接受（Accepted） |
| 日期 | 2026-09-07 |
| 背景 | TEAM-003 BR-03：空间归档是「空间级生死」语义，与删除/转让同级。 |
| 决策 | archive/restore 仅 WS_OWNER（rbac §8.1 原文 WS_OWNER/WS_ADMIN 双 ✅ 收窄为 Owner-only；WS_ADMIN 列改 ⚠️）。 |
| 关联 | `docs/sprint-5-integration-standard/TEAM-003-team-archive-config.md` §6.4（占位编号 ADR-0002，按仓库序列顺延落位） |

## 后续动作

- rbac §8.1 两处回改：①「Workspace archive」行 WS_ADMIN 列 ✅→⚠️；② 新增「Workspace restore」行（WS_OWNER ✅ / 其余 ❌）——已随本 ADR 同步回改。
- `WorkspaceAdminPermission` 的 archive/restore 动作叠加 `role >= WS_OWNER` 校验（实现于 workspace_governance 视图 `_require_owner`）。
