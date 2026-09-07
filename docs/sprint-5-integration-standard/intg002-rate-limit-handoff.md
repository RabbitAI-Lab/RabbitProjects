# INTG-002 出站限流与基础设施交接清单（→ INFRA-005）

> 日期：2026-09-07　|　来源：Sprint-5（INTG-002 §4.2 限流实现）　|　对接：Sprint-6（INFRA-005 生产部署）

## 一、限流层（sprint-5 已落地）

| 端点 / 用途 | 限流规格 | 实现位置 | 关键语义 |
| --- | --- | --- | --- |
| `ProjectStatsView` | 10 req/min·user | `apps/api/plane/app/views/stats.py` `_report_throttle` | L3 端点级覆盖；429 RATE_LIMIT_EXCEEDED + X-RateLimit-* 头 |
| `WorkspaceActivityStatsView` | 10 req/min·user | 同上文件（共用 `_report_throttle`） | 同上 |
| `WorkspaceLabelListCreateView` 等 5 端点 | 30 req/min·user | 规格列（TEAM-003 §4.2.3）；**实现未限流——sprint-6 接 INFRA-005 全局框架** |  |

限流实现要点：
- 滑窗 Redis 计数器（key 形如 `rpt-throttle:min:<user_id>`）
- 60s 窗口 + 超过即 429
- 与 DRF Throttle 区别：**手工实现保 429 信封经过全局 envelope 处理器**（DRF Throttled 默认抛 Throttled 不经 envelope，与 api-conventions 信封规范冲突）
- L2 全局配额 60 req/min/user（`api-conventions.md` §7.1）；L3 端点级覆盖

## 二、出站 Webhook 限流（sprint-5 已落地）

| 项 | 规格 | 实现 |
| --- | --- | --- |
| 稳态自限 | 30 req/min/安装 | `IntegrationQuotaService.hit(installation_id, steady=True)` |
| 突发回填 | 100 req/min/安装（仅 `backfill_repository` 任务） | 待 sprint-6 接 INFRA-002 `backfill` 队列 |
| GitHub API | 5000 req/h/installation | `hit(steady=False)` 滑窗 |
| 70% 降级开关 | 暂停 PR 挂载细节 | `degraded=True` 旗标 + 调用方分支 |
| 429/403 rate-limit | 暂停队列 | `pause_until(Reset 秒)` + `paused()` 阻塞 |
| 重试退避 | 1s/10s/1m/10m/1h/6h（7 次后 dead） | `RETRY_SCHEDULE` + `deliver_webhook.apply_async(countdown=...)` |
| 死信 | 终态 dead + 连败 +1 | `WebhookDelivery.status=dead` 单表设计 |
| 50 连败停用 | auto_disabled + 通知 | `consecutive_failures ≥ 50`（**无时间窗终态计数器**） |
| 30 天清理 | 终态行分批 5000 | `purge_webhook_deliveries` beat（03:30） |

## 三、Sprint-6 必接项（INFRA-005 落地前）

1. **限流框架统一**
   - 当前 `_report_throttle` 手工实现需要替换为 INFRA-005 全局限流（429 信封需保留）
   - L2 全局 60/min + L3 端点 10/30/min 的三层防护叠加计数（详见规格说明）
   - **对接位**：`plane.base.middleware.RateLimitHeaderMiddleware` 已存在，INFRA-005 注入 X-RateLimit-* 头；sprint-5 端点无需改

2. **Webhook 出站 IP 白名单**
   - INTG-002 §6 风险 #5：与 INFRA-005 限流口径撞车——本迭代不引入新限流框架，所有限流走 `429 RATE_LIMIT_EXCEEDED` + 现有 `X-RateLimit-*` 头
   - **白名单位**：`GITHUB_WEBHOOK_BASE` / `GITHUB_API_BASE` settings 注入；容器编排侧出网白名单（spec §4.5）

3. **30% 配额降级钩子（INFRA-005 触发）**
   - `IntegrationQuotaService.degraded` 旗标对外暴露：`GET /api/v1/integrations/{installation_id}/quota-status`（sprint-6 新增端点）
   - INFRA-005 自动根据此旗位切换速率预算

4. **Webhook 投递失败 → 重启恢复**
   - 退避到期扫描 `retry_due_webhook_deliveries`（beat 每 5 分钟）——自调度丢失兜底
   - 容器重启后 5 分钟内可全量恢复

5. **签名密钥管理**
   - 当前 Fernet 密钥派生：`SHA-256(settings.SECRET_KEY)`（dev/CI 零配置）
   - 生产环境：**必须** 在 settings 注入独立 `INTEGRATION_SECRET_KEY`（sprint-6 部署检查清单）

6. **凭证托管（Webhook 端点 secret）**
   - 入站侧需生产 webhook secret 注入与轮换流程
   - **不接受** dev 模式（"sk3cret"）进入生产——首启必须生成 64 字符随机并落库密文

## 四、Sprint-6 复核项

- [ ] INFRA-005 全局限流框架对接完成（`_report_throttle` 退役）
- [ ] `INTEGRATION_SECRET_KEY` 注入与密钥轮换
- [ ] 容器出网白名单（含 GitHub API/hooks）
- [ ] 限流与 INFRA-002 队列拓扑（`webhooks` 队列 + beat 三任务已就位）
- [ ] 监控告警：50 连败阈值、429 频次、dead 数 30 天滚动
- [ ] 收口签字：sprint-5 INTG-002 §6 风险 #5 "Sprint 6 启动前向 INFRA-005 提交交接清单" ——本文件即该交付物

## 五、风险登记

| 编号 | 风险 | 缓解 |
| --- | --- | --- |
| 1 | 限流实现迁移至全局框架时 429 信封一致性 | 同步 envelope 处理器回归测试（既有 TC-INF4-012/013） |
| 2 | 手工滑窗在 LocMem cache 下多 worker 不共享计数 | 生产必走 Redis（dev 接受漂移） |
| 3 | webhook secret 轮换未实现 | sprint-6 增强：重生成 secret + 重注册 GitHub Webhook |
| 4 | beat 任务在多 worker 部署下并发跑（`purge_webhook_deliveries`/`retry_due_webhook_deliveries`） | INFRA-005 锁机制或迁移到 DatabaseScheduler |
