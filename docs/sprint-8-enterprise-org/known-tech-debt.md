# Sprint-8 已知技术债（Sprint-9+ 消费）

> 登记日期：2026-09-09　|　状态：企业组织权限治理交付收口　|　依据：
> r1-inflight.md 七轮实录（R0 基线 / R1 部门 / R2 角色 / R3 SSO / R4 审计 /
> R5 视图治理 / R6 前端六表面 + Keycloak 联调）

## A. 顺延项（非阻塞收口）

| # | 项 | 顺延理由与归属 |
| --- | --- | --- |
| 1 | 审计导出 202 异步两段式（task_id/status_url + MinIO 预签名） | 当前同步 CSV 流式已承载（密码双授权 + 自审计 + 断链冻结齐备）；异步范式随 S9 RPT 大容量导出统一接入（与 S7 known-debt A#1 同批） |
| 2 | SAML 真容器断言消费联调（Keycloak SAML IdP 侧） | OIDC 已全链过（JIT/绑定/会话）；SAML SP 元数据/配置构建/无效断言拒绝已测，真 SAML 断言流随 S9 验收轮补（浏览器协议流录制成本高） |
| 3 | SSO 配置向导式三步表单（当前单页四段） | §3.1 向导分步 UI 属打磨面；功能面（协议/元数据/干跑/启用/强制/绑定清单）已全 |
| 4 | 二维泳道拖拽写路径（列维度流转 + 行维度字段同改） | 当前矩阵为只读聚合 + 一维看板承载写路径（§2.3 懒加载语义）；拖拽双字段变更随 S9 打磨 |
| 5 | users/me/permissions 快照契约扩展（码集并集下发 + usePermission 并集分支） | AUTH-005 待回改项；前端当前按权限快照角色级渲染，码级显隐随 S9 前端轮统一 |
| 6 | 认领页 302 后 next 参数透传细节（sso_txn 闭环已测，UI 联动随验收视频轮校准） | 功能可用；文案与跳转微调随验收 |
| 7 | org-structure 拖拽移动部门（键盘「移动到」等价路径） | API move/ 全通；树拖拽交互属打磨 |
| 8 | e2e：SSO 真链（Keycloak 浏览器流）与 SAML 面 | 真链已人工联调通过（JIT 落库自证）；进 e2e 需 Keycloak 容器 fixture 化（S9 CI 基建） |

## B. 本迭代收口清单（已闭环，留档）

- AUTH-007 部门树/归属/按部门授权快照展开（11 端点 + 批量授权弹窗 C.150）
- AUTH-008 自定义角色（42 码冻结目录 + require_permission 并集提升分支 +
  GUEST 天花板 + WS 降级级联；矩阵编辑器 C.151）
- AUTH-009 SSO（OIDC/SAML 双协议 + JIT + 认领 + 强制门 + 逃生名单；
  **Keycloak 26 真容器 OIDC 全链过**：JIT 建号/入空间/绑定/会话；配置页
  C.155 + 登录邮箱路由 + 认领页）
- AUTH-010 全站审计（月分区 + hash 链 + 三层去重 + audit.dlq + 检索/导出/
  实例级；22 处埋点空间归属修复；审计页 C.152）
- BOARD-005 视图治理（共享/锁定/项目默认/订阅/副本 + 二维泳道矩阵 C.153/154）
- S7 known-debt A#2 收口（审批超时 24h 加报 WS_ADMIN/OWNER）
- 测试终态：pytest 896 全绿；e2e 171 过（含 C.150~C.155 六表面 parity）；
  ruff/mypy/tsc/oxlint 零错

## C. 门禁终态（2026-09-09）

| 类别 | 数据 |
| --- | --- |
| pytest | 896 全绿（部门 35 + 角色 28 + SSO 23 + 审计 19 + 视图治理 19 + 既有回归） |
| 静态 | ruff + mypy（244 文件）+ tsc + oxlint 全绿 |
| e2e | 171 过 0 挂（parity-sprint8 5/5：C.150~C.155） |
| 联调 | Keycloak 26 容器（realm rabbit / client rabbit-projects）：干跑验签 ✓ |
|      | + OIDC 浏览器全链 ✓（JIT 建号 sso-demo + WorkspaceMember + SSOAccount） |
| 迁移 | 0025~0030 六批（手工灌 + fake），audit_log 月分区 + DEFAULT 兜底 |

## D. Sprint-9 入口

- 本表 A 组逐项排期；与 S7 known-debt A 组（#1/#3/#4 未消费项）合并
- 交接：`docs/sprint-8-enterprise-org/r1-inflight.md`（七轮全实录含坑）
- dev 栈注意：celery 需 `-Q activity,celery,workflow,notifications,audit`；
  SSO_FERNET_KEY 在 /tmp/rp-sso-key.env（runserver/worker 均需）；
  Keycloak 容器 rp-keycloak（8180，admin/admin，realm rabbit）
