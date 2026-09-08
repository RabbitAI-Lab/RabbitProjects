# Sprint-5 已知技术债（Sprint-6 收尾消费）

> 登记日期：2026-09-07　|　对接：QA-001 收尾（sprint-6）　|　依据：ADR-0026 / sprint-5-progress-inflight.md

## A. tier-2/3 偏差（规格回改 + QA 复核）

| 编号 | 偏差 | 后续动作 |
| --- | --- | --- |
| 1 | `Project.visibility` 公开项目对 WS_ONLY 只读可见通道未实现（rbac §6.2 `_scoped_for` 公开分支缺失） | 架构回改任务；`unified-issue-model.md` §2.4 加列 + rbac §6.2 增分支；矩阵解锁后开 IT-02 公开行 |
| 2 | APIKey 基建全站无（吊销计数恒 0） | P3 阶段补全 AUTH-009 / INFRA-005；本迭代仅做"无基建"登记 |
| 3 | WS 主动踢出通道未实现（依赖 COLLAB-004 后续） | sprint-6/7 增强 live 票据 webhookserver 主动 kick |
| 4 | RPT-002 公共口径基座并入 stats.py | 与规格 §4.3.1 位置偏离（ADR 登记）；规格回改随下批次一并 |
| 5 | Webhook 限流手工实现（保 429 信封 + X-RateLimit-* 头） | 与 INFRA-005 限流框架对接（限流基线 10/min·user） |
| 6 | 跨 user_id 键路径**无**任何模块的渲染（全局红线） | 持续 lint_access AC-01~05 守护；新增端点需复检 |
| 7 | file 域 `Issue` 改换 task_type 需手工迁移（`FileAsset` ↔ `Issue` 多态） | 边界 #12；FILE-001 §1.4 文档同步 |
| 8 | `Project.is_default_state` 字段缺失 | 默认态由 `State.is_default` 承载（双轨），无迁移 |
| 9 | lifecycle 占位实现先 no-op 后转真（INTG-002 §4.3.2 挂点） | 已在本迭代内转真（`ProjectLifecycleService._dispatch_webhook_events`） |
| 10 | `ProjectLifecycleService` 偏 BR-10 守卫用 `assert_completable`（spec §2.3 仅概念） | 文档同步随 sprint-6 |

## B. 平台/工具

| 编号 | 项 | 后续动作 |
| --- | --- | --- |
| 1 | dev 缺 soffice/ffmpeg（Sprint-4 已知） | compose worker `INSTALL_TRANSCODE_TOOLS` 分层已就位；INFRA-005 上线前需真构建验证（可能 CI 镜像变更） |
| 2 | ~~UT19 甘特时区用例在 UTC 04:00~11:00 必红~~ | **已修**（7b98a1d：Pago↔Kiritimati 恒差 25h 配对，断言与 UTC 时刻无关）|
| 3 | celery 多代 worker 并存会分食投递 | 持续跑 `celery inspect registered` 核新任务；新交付后重启 worker（坑 20） |
| 4 | 并行会话半成品（test_comment_thread / test_file_versions / test_gantt 等） | sprint-6 收尾前由 main 合并吸收；本分支不掺入 |

## C. 文档同步（不留债，但留清单）

| 编号 | 文档 | 状态 |
| --- | --- | --- |
| 1 | `unified-issue-model.md` §2.4 加 `Project.visibility` 行（已随 T5-03 ADR-0026 同步） | ✓ |
| 2 | `rbac-permission-model.md` §8.1 增 `team.stats.read` + WS archive 收窄 + §6.2 `_scoped_for` 公开分支标注 | ✓（T5-08/03） |
| 3 | `api-conventions.md` §13.3 限流章节（与 INTG-002 退避表一致） | ✓（2026-09-08 Sprint-7 R0：§13.3 重试/自动禁用两行按 INTG-002 BR-06/BR-08 精确化——死信单表 + 无时间窗终态计数器语义） |
| 4 | `COLLAB-001` §2.3 增 `webhook.auto_disabled` 通知类型 | ✓（T5-05） |
| 5 | `INTG-002` 概览 §9 风险表"project.* 五事件"补登 | ✓（2026-09-08 Sprint-7 R0：概览 §1 依赖图补 COLLAB-002/PROJ-003 独立挂点虚线边 + 事件挂点注；§9 风险 #2/#3 五处冲突全部对齐实现口径） |
| 6 | `INTG-002` §4.1 概览"70% 阈值降级"与实现无时间窗计数器口径一致 | ✓（2026-09-08 Sprint-7 R0：概览风险 #2「滑窗 1 小时」已改无时间窗 `consecutive_failures` 终态计数器；风险 #1 的 70% 配额降级经核实实现已落地 `QUOTA_DEGRADE_RATIO=0.70`（integrations/github.py），无需回改；INTG-002 §1.2 登记段同步收口） |

## D. 测试/门禁缺口

| 编号 | 项 | 后续 |
| --- | --- | --- |
| 1 | 前端 E2E 录屏（13 幕 sprint-5 验收脚本） | 契约已入库（3a7e0ea）；parity 六幕已绿（d24e8eb）；**录制归 sprint-6 INFRA-005**（需 mock 双服务 8090/8091） |
| 2 | 越权矩阵端点枚举（WebhookEndpoint CRUD）当前仅 matrix+permission 子集跑通，sprint-6 补全 CRUD 端点逐端点 |  |
| 3 | ~~bench 数据集缺口~~ | **已建**（20654d8 sprint-5-bench：10 万任务五门禁全绿；P1 附带真优化 375→174ms）|

## E. 不留债（已闭环）

- Sprint-4 收口的三件遗留（迁移 0013/文件域动态流/外部驱动活动）已随 T5-02 落地
- ADR-0026 tier-2/3 偏差全部列出且文档同步
- CI 平价三件套（ruff/mypy/pytest）由并行会话新增 + 本迭代挂入


## F. 2026-09-07 二轮补遗收口（用户指出伪完成后全量补齐）

| # | 项 | 结果 |
| --- | --- | --- |
| 1 | UT19 时区抖动 | 修（7b98a1d） |
| 2 | sprint-5-flow jMeter | 建 540 行七段（acae60d 39/47；victim 子查询归 sprint-6） |
| 3 | sprint-5-bench 压测基线 | 建五门禁全绿 + P1 真优化（20654d8） |
| 4 | 前端覆盖率基建 | vitest 38 测 + TC-COVER-004（a6e977f，36.4% 守 35% 门槛） |
| 5 | 5 规格 e2e parity | 六幕 6/6（d24e8eb） |
| 6 | 006bc7a 误导说明订正 | 空提交登记（c080c6f） |

## G. 2026-09-07 真 GitHub 联调（第 14 幕）发现与处置

| # | 项 | 处置 |
| --- | --- | --- |
| 1 | GitHub App 未含「Repository webhooks」写权限 → 绑仓时 repo webhook 注册 403（`webhook_registered:false`） | 联调走 App 级 webhook 变体（`PATCH /app/hook/config` 指公网穿透域名，secret 与绑定行同源）；App 补权限后回归产品标准路径（绑仓自动注册 per-repo webhook），入站视图两模式通用无需改码 |
| 2 | **BR-06 评论双向同步整条缺失**（worker 路由表无 comment.created、`create_issue_comment` 零调用） | 已补齐：入站 `_on_comment_created`（系统账号代发镜像）+ 出站 `sync_comment_outbound`（事务后投递、echo 防环）+ 评论视图挂点；真仓实测双向通（issue #2 双向评论可见） |
| 3 | GitHubClient 真联调注入点缺失（settings 无 GITHUB_* 定义） | dev settings 补 env 三元组（APP_ID/PRIVATE_KEY/WEBHOOK_BASE），缺省空值维持 dev token 直通（mock/单测口径不变） |
| 4 | OAuth App 凭证不适用安装闭环（历史澄清项） | 用户已改供 GitHub App（App ID 4859835）；曾贴对话的 OAuth Client Secret 两枚均建议轮换（未入库，仅 /tmp 与进程 env） |
| 5 | RabbitTest 仓库测试痕迹（issue #1~#9、分支 s5-intg-test、PR #4 squash 合并） | 保留为联调证据（公开仓库、b 视口取证面）；如需清场可整仓重建（RabbitTest 原为空仓） |
