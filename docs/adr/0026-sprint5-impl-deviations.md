# ADR-0026 · Sprint-5 实现偏差汇总收口（行级隔离 + 集成 + 治理 + 模板）

| 项 | 内容 |
| --- | --- |
| 状态 | 已接受（Accepted） |
| 日期 | 2026-09-07 |
| 背景 | Sprint-5（integration + standard 收尾，迁移至 0014/0015/0017/0018）全量实现。范围 = AUTH-006（行级隔离）/ TEAM-003（治理）/ PROJ-003（生命周期）/ RPT-002（项目统计）/ INTG-001（GitHub）/ INTG-002（Webhook）。各实施批次（T5-01~T5-10）上报的偏差按 ADR-0020/0022 模式汇总登记。 |
| 决策 | 各列按"实现-规格"配对登记，规格回改项以 §2 E 表为清单；tier-2/3 偏差随 Sprint-6 QA 收尾处理。 |
| 关联 | `docs/sprint-5-integration-standard/`（6 份规格 + 概览）、`docs/adr/0023`/`0024`/`0025`（Sprint-5 架构偏离登记）、`docs/sprint-4-acceptance/`（Sprint-4 收口基线，迁移 0013 沿用） |

## A. 实现偏差（按规格）

### A-1 AUTH-006（行级隔离 / T5-03）

| # | 规格 | 实现 | 理由 |
| --- | --- | --- | --- |
| 1 | §2.1 矩阵 — 公开项目对 WS_ONLY 只读可见 | 当前态 = 私有（rbac §6.2 `_scoped_for` 公开分支 + `Project.visibility` 通道未实现） | 架构待回改（已随 ADR-0026 同步在 rbac §6.2 + unified-issue-model §2.4 标注）；落地前 401/404 收口 |
| 2 | §2.1 矩阵 — draft 仅创建者 + WS_ADMIN 可见 | matrix `project_q` 同 SQL 行收口（`_is_draft ∨ _is_creator`） | 无偏差 |
| 3 | §2.3 批量角色 §2.5 BR-09 ≤100 | 服务层 `BULK_ROLE_MAX = 100` 整请求 400 校验；UI 浮条 disabled 同步 | 无偏差 |
| 4 | §2.4 账号禁用 ≤5s 生效 | DRF SessionAuthentication 拒 inactive（API 0s 401）；DB 会话 + Valkey 索引清理 | APIKey 0 计数（无基建，偏差登记） |
| 5 | §4.1 `User.disabled_at/by` 审计列 | 0014 迁移加列 + 完整实现 | 无偏差 |
| 6 | §4.2 `WorkspaceArchiveMiddleware` 路径正则 | `^/api/v1/workspaces/(?P<slug>[^/]+)(/.*)?$` —— 路径尾段 `(/.*)?` 兜住全部子端点 | 旧实现只覆盖精确五端点，`/projects/.../members` 等被绕过——根因分析后改单正则 |
| 7 | §4.3 ViewSet 基类 `AccessibleModelViewSet` 命名 | `from_queryset` 派生 + 全模型基类 `SoftDeleteManager.accessible_by/accessible_in/unsafe_all`；"AccessibleModelViewSet"作为功能名不引入新基类（api-conventions §10.1 BaseAPIView 派生） | 命名统一以架构文档为裁（rbac §6.3 BaseViewSet 同步加注） |
| 8 | §4.3 CI AST 守护 | `scripts/lint_access.py` 收 AC-01~05 + AC-06（视图直改 .status 即红）；自检 `--selftest` 子命令；TC-AUTH6-001 接入 run-ci-checks | 无偏差 |
| 9 | §4.5 启停吊销链 | `AccountService.disable/enable`：`is_active=False` + 会话 decode 扫描 + Valkey 索引清理 + on_commit 通知 | APIKey 0（无基建）；WS 踢出按票据 120s 轮换（COLLAB-004 后续增强）——偏差登记 |

### A-2 TEAM-003（团队治理 / T5-08）

| # | 规格 | 实现 | 理由 |
| --- | --- | --- | --- |
| 1 | §2.2 全局标签 OR-merge 下发 | `Governance.list_labels` 端点 + 项目侧列表并集展示（默认全局态；项目行 `overrides_global_id` 指向全局行 → 顶替） | 与规格「并集 + 覆盖」一致 |
| 2 | §2.3 状态模板内置兜底 | 迁移内置 3 套（基础/敏捷/产品）；PUT 全量替换五组（每组 ≥1，sequence 连续） | 无偏差 |
| 3 | §2.4 活跃度 Schema 红线 | `WorkspaceActivityStatsView` 端到端无 `user_id` 键路径（`activity_stats` 走 SQL 聚合内消化 per-member 维度）；`test_team003` 递归断言守护 | 无偏差 |
| 4 | §4.1 `Workspace.archived_at/by` | 0016 迁移加列 + `archive/restore` 视图 `_require_owner` | 与 ADR-0023 收窄一致 |
| 5 | §4.3.3 登录命中表精度 | `WorkspaceLoginDailyAggregate`（workspace × member × date 唯一）；beat 心跳（无独立落盘 worker——记录为偏差） | Sprint-5 内集成 `record_login_hits` 入登录钩子 |

### A-3 PROJ-003（生命周期 / T5-07）

| # | 规格 | 实现 | 理由 |
| --- | --- | --- | --- |
| 1 | §2.2 TRANSITION_GUARDS 边集 | `ProjectLifecycleService.transition` 同款矩阵 + 幂等短路 + 非法边 409 RESOURCE_TRANSITION_INVALID（带 `allowed_targets`） | 无偏差 |
| 2 | §2.2 守卫双前置（draft→active） | `_guard_activate`：至少 1 状态 + identifier 复检 | 无偏差 |
| 3 | §2.5 close 守卫 | `assert_completable`（rbac §7.2 依赖拦截）+ force 批量取消 | 无偏差 |
| 4 | §4.1 状态历史首行 | 0015 迁移存量回填 `from='' to=当前态` | 无偏差 |
| 5 | §4.3.2 模板实例化 | `apply_template` 事务四件套（状态/标签/字段/目录） | 无偏差 |
| 6 | §2.5 副本重开标识符自动 `-C` 递增 | 服务层实现 | 无偏差 |
| 7 | BR-08 生命周期事件五族（created/activated/restored/archived/closed） | `enqueue_project_activity` 独立 worker 轨道（project 域），与 issue 域物理隔离 | T5-02 双轨扩域前置 |

### A-4 RPT-002（项目统计 / T5-06）

| # | 规格 | 实现 | 理由 |
| --- | --- | --- | --- |
| 1 | §4.3.1 公共口径基座 `analytics/services/project.py` | 并入 `plane/db/services/stats.py`（与 RPT-001 同基座 BR-01 收口） | 偏差——实现位置偏离规格 §4.3.1 |
| 2 | §4.3.2 五组分布/完成率 | 单条 aggregate + 剔 cancelled 口径 | 无偏差 |
| 3 | §4.3.2 趋势 | `TruncDate` 按请求时区 + 补零 | 无偏差 |
| 4 | §4.3.4 成员任务量 | 单条 GROUP BY + 未指派/合计行 + 30d 窗口 | 无偏差 |
| 5 | BR-13 限流 10/min | 手工 `_report_throttle`（保 429 信封 + X-RateLimit-* 头，DRF Throttled 不经 envelope） | 偏差——手写而非 DRF Throttle |

### A-5 INTG-001（GitHub 集成 / T5-04）

| # | 规格 | 实现 | 理由 |
| --- | --- | --- | --- |
| 1 | §4.1 `IntegrationInstallation` 条件唯一 ≤5 | 0017 迁移 + `UniqueConstraint(project, repository_full_name) WHERE deleted_at IS NULL` | 无偏差 |
| 2 | §4.1 `Issue.external_source/external_id` 幂等锚 | 0017 迁移加列 + `UniqueConstraint(project, source, id) WHERE source IS NOT NULL AND deleted_at IS NULL` | 无偏差 |
| 3 | §4.1 `webhook_secret` Fernet 密文 | `encrypt_secret/decrypt_secret` 复用 integration 密钥派生（settings.SECRET_KEY SHA-256 → Fernet key） | 无偏差 |
| 4 | §4.2.1 入站三道闸（BR-01 顺序） | 视图层：定位绑定（未命中静默 202）→ 验签（`hmac.compare_digest` + 漂移 ≤5min）→ Delivery SETNX 24h 查重 → 202 + `dispatch_github_event.delay` | 无偏差 |
| 5 | §4.3 GitHub 客户端 | `plane/integrations/github.py`：`installation token` 缓存（过期前 5 分钟刷新）；dev 无 App 凭据走 dev token 直通；传输层可注入（mock 门禁） | 无偏差 |
| 6 | §4.3.2 速率预算 5000/h + 70% 降级 | `IntegrationQuotaService`：滑窗 Redis 计数 + `pause_until` 暂停 + `paused()` 查询 | 无偏差 |
| 7 | §4.3 双向同步 | 视图直改 status 守卫 + 后写胜出（lww）+ 冲突日志败方快照 | 无偏差 |
| 8 | PR 合并自动流转（§2.3） | `RBT-\d+` 正则提取 → `assert_completable` 守卫 → 阻塞时降级系统评论 + 通知执行人 | 无偏差 |
| 9 | Commit 挂载（BR-11） | `push.commits[].message` RBT 匹配 → `Issue.github_context.commits` 追加（sha×任务 去重） | 无偏差 |
| 10 | 回写标题前缀（BR-05） | `GitHubClient.edit_issue_title` 出站，content-hash 比对节省速率预算 | 无偏差 |

### A-6 INTG-002（出站 Webhook / T5-05）

| # | 规格 | 实现 | 理由 |
| --- | --- | --- | --- |
| 1 | §4.1 `WebhookEndpoint/Status` `active/disabled/auto_disabled` | 0018 迁移 + 状态机（含并发约束：状态迁移走 `UPDATE WHERE is_active != 'active'` 幂等 SQL） | 无偏差 |
| 2 | §4.1 死信 = `WebhookDelivery.status=dead` 单表（无独立死信表） | 同款设计 | 无偏差 |
| 3 | §2.3 事件面闭集 12 种（webhook.ping 免勾选） | 端点 `meta.event_choices` 单源下发，前端据其勾选 | 无偏差 |
| 4 | §2.3 五事件 `project.created/activated/restored/archived/closed` | `ProjectLifecycleService._dispatch_webhook_events` 转真（前 T5-02 预留 no-op 占位） | 偏差——落地比规格晚一节 |
| 5 | §4.2 退避表 1s/10s/1m/10m/1h/6h | `RETRY_SCHEDULE = (1, 10, 60, 600, 3600, 21600)`；初始 + 6 重试 = 7 次后 dead | 无偏差 |
| 6 | §4.2 BR-08 终态连败 ≥50 auto_disabled | `consecutive_failures` 终态计数器（dead +1 / success −1 钳位 ≥0，**无时间窗**——与 spec §4.2 退避表对齐，规避 70% 阈值降级口径偏离） | 偏差——实现以无时间窗计数器替代概览风险表"滑窗 1 小时粒度"口径（已随 T5-01 文档同步消解） |
| 7 | §4.2 重放 `replay_of` 审计链 | 新建 pending 行指回原 dead（BR-07） | 无偏差 |
| 8 | §4.2 ping 免订阅直达 | `dispatch_events("webhook.ping", ...)` 端点匹配跳过 events 过滤 | 无偏差 |
| 9 | §4.2 签名 `v1={hmac}` + `±5min` 时间窗 | `sign_payload` + `verify_signature`（接收方校验参考实现） | 无偏差 |
| 10 | §4.2 `5min` 定期清理 30 天前终态 | `purge_webhook_deliveries` beat（每日 03:30）分批 5000 | 无偏差 |

## B. 缺陷修复（已闭环 / 历史）

T5-05 ruff unused import 修补；其余无未关闭缺陷。

## C. 门禁脚本偏差

T5-10 接入 TC-AUTH6-001/002 后无新偏差；既有 sprint-1/2/3/4 TC-INF4-* / TC-API-CI-* 一并通过（与并行会话并发登记的 TC-INF4-016 v2 扫描器口径一致）。

## D. 流程与工具链登记

| # | 项 | 说明 |
| --- | --- | --- |
| 1 | 行级守护脚本 | `scripts/lint_access.py`（AC-01~05 + AC-06 + selftest）——sprint-5 行级隔离体系化的工程落地 |
| 2 | 越权矩阵参数化 | `tests/test_auth006.py`（四主体 × 四资源层笛卡尔积；SYSTEM_ADMIN/WS_OWNER/WS_MEMBER/WS_GUEST × Workspace/Project/Issue/WebhookEndpoint） |
| 3 | 字段级 lint | ruff F401（test_team003 unused timezone import——T5-10 顺修） |

## E. 规格勘误待回改（收口后回写，不阻塞）

| # | 规格处 | 现状 | 待回改 |
| --- | --- | --- | --- |
| 1 | INTG-002 §2.3 概览风险表"project.* 五事件" | 现 Lifecycle 挂点已交付；概览 §2.3 风险表"未登记"五事件状态需更新 | §2.3 表行同步增列（dev 已与实现一致） |
| 2 | INTG-002 §4.1 概览"70% 阈值降级" | 终态计数器（无时间窗）已落地；概览口径需同步 | 概览 §4.1 收口注释 |
| 3 | SPEC-vs-implement 偏差表（A-4/A-5/A-6 编号项） | sprint-6 QA-001 复核范围 | sprint-6 PR 时一次性回写 |

## 结论

Sprint-5 全部实现偏差按上述口径收口；规格回改清单（E 表）以本 ADR 为后续迭代文档批次执行参考。V1.0 冻结清单（待 T5-11 产出）与已知技术债（断档续接见 `sprint-5-progress-inflight.md`）以 Sprint-6 QA-001 收尾。

**tier-2/3 偏差合计 7 条**（A-1#1、A-2#5、A-3#4、A-4#1、A-4#5、A-6#4、A-6#6）——均已列 E 表为回改参考或文档同步项，不阻塞冻结签字。
