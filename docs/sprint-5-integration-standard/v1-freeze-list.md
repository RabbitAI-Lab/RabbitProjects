# Sprint-5 V1.0 冻结清单

> 签名日期：2026-09-07　|　迭代：sprint-5（integration + standard 收尾）　|　冻结基线：`2142cf3`（CI 门禁接入提交）
>
> 本清单为标准版功能冻结点（sprint-overview §10.2）；Sprint-6（INFRA-005 生产部署 / QA-001 收尾）消费入口。

## 一、范围（已交付）

### 治理收口（AUTH-006 · T5-03）
- 矩阵单源（`plane/access/matrix.py`）——五族（workspace/project/issue/comment/file_assets）可见性 Q 构造 + `matriby_family_for` 未注册显式报错
- 迁移 0014：`User.disabled_at/by` + `Project.visibility`（架构回改后解锁 public 通道）
- 全模型基类 `accessible_by/accessible_in/unsafe_all`（`from_queryset` 派生）
- 8 端点（WS 批量/启停/启停/项目批量/启停/禁用/启用）
- `scripts/lint_access.py` AC-01~06 行级守护（AC-06 视图直改 .status 红屏）
- 3 份架构 ADR（0002/0003/0004 → 0023/0024/0025 顺延登记） + rbac §8.1 / unified-issue-model §2.4 标注

### 团队治理（TEAM-003 · T5-08）
- `WorkspaceArchiveMiddleware` 全站写保护（archived/closed）
- 5 端点（归档/恢复/标签 CRUD/状态模板/活跃度）
- 登录命中表（`record_login_hits` 入登录钩子）
- 隐私红线：响应键路径无 `user_id`（UT Schema 递归断言）

### 生命周期（PROJ-003 · T5-07）
- 迁移 0015：`ProjectStatusLog` + `ProjectTemplate`（3 套内置 + 同步回填）
- `ProjectLifecycleService` 唯一入口：四态机 + `TRANSITION_GUARDS` + 幂等
- 4 端点（状态转换/状态历史/副本重开/模板 CRUD）
- AC-06 入 lint_access（视图直改 .status 红屏）
- draft 静默（BR-03）+ closed 单向门（BR-05）

### 模板与项目流（T5-02/06/07）
- 迁移 0013：`issue_activities.project_id` 双轨（XOR CHECK + 条件偏索引）
- `_STREAM_VIEW` 第三 UNION ALL 分支（kind=project）+ lifecycle 第四语义组
- file 域 10 动作 emit_file_activity（首传只留「上传」防双行 / v2+/回滚留「版本」/DLQ 双轨）

### 集成（INTG-001/002 · T5-04/05）
- 迁移 0017/0018：IntegrationInstallation + SyncConflictLog + WebhookEndpoint + WebhookDelivery
- 8 端点（GitHub 安装入口/回调/仓库代理/绑定 CRUD/解绑/同步冲突日志 + Webhook 列表/新建/更新/启停/ping/投递/重放）
- 入站三道闸（定位/验签/查重）+ 投递引擎（退避 1s/10s/1m/10m/1h/6h）+ 死信重放 `replay_of` 审计链
- 退避表与速率预算的本地口径已与 INTG-002 文档同步消解（see ADR-0026 E 表）

### 项目统计（RPT-002 · T5-06）
- 公共口径基座并入 `stats.py`（RPT-001/002 BR-01 收口）
- 2 端点（项目进度/成员任务量）+ 手工限流（保 429 信封 + X-RateLimit-* 头）
- 9 测全绿（含口径一致性——合计=逐行之和+未指派=手工逐条筛选）

### 前端（T5-09）
- 4 新页（project-stats / project-webhooks / project-integrations / workspace-governance）
- 团队页扩批量/启停/灰标；项目设置扩生命周期区块 + 关闭向导 + 副本重开；新建项目模板选择
- 侧栏接入统计/集成/Webhook 三入口
- E-4 分享角标：后端 `file_row.share_count` 单条 GROUP BY 批量注水 + 前端 `🔗N` 渲染
- 附录 C.126~C.133 入库（ADR-0010 五步纪律先于实现）

### 门禁（T5-10）
- TC-AUTH6-001：lint_access 入 run-ci-checks
- TC-AUTH6-002：越权矩阵参数化（四主体 × 四资源层笛卡尔积）

## 二、质量

| 类别 | 数据 | 备注 |
| --- | --- | --- |
| 后端 pytest | 573 / 573 通过 | 唯一失败为已登记时区抖动（test_gantt::UT19 · 预存项） |
| 静态检查 | ruff + mypy 全绿 | TC-AUTH6-001 接入门禁 |
| 行级守护 | AC-01~05 + AC-06 绿 | selftest 红屏冒烟过 |
| 越权矩阵 | 四主体 × 四资源层笛卡尔积 | dev PG 在跑时启用；缺失则跳过 |
| 前端构建 | `pnpm build` + tsc 全绿 | 4 新页 + 7 扩展项 |
| ADR 偏差 | 7 条 tier-2/3（已列 E 表为回改参考） | ADR-0026 收口 |
| 前端 E2E | 待 T5-11 收口补录屏 | sprint-3 同模式 |

## 三、规格状态

| 规格 | 实现 | 文档状态 | 备注 |
| --- | --- | --- | --- |
| AUTH-006 | 完成 | 已实现（2026-09-07） | 矩阵单源 + CI 守护入库 |
| TEAM-003 | 完成 | 已实现（2026-09-07） | 5 端点 + 命中表 |
| PROJ-003 | 完成 | 已实现（2026-09-07） | 模板 3 套种子 + 0015 迁移 |
| RPT-002 | 完成 | 已实现（2026-09-07） | 公共基座并入 stats.py（偏差 ADR-0026 A-4#1） |
| INTG-001 | 完成 | 已实现（2026-09-07） | mock 门禁（IT-05 自按 mock 设计） |
| INTG-002 | 完成 | 已实现（2026-09-07） | 5 事件 + 退避/重放/停用 |
| sprint-overview | 完成 | 已实现（2026-09-07） | 概览 §9 五处口径已同步（见 ADR-0022 与 T5-01 提交） |

## 四、已登记偏差（ADR-0026 摘录）

| 编号 | 偏差 | 处置 |
| --- | --- | --- |
| A-1#1 | 公开项目 WS_ONLY 通道未实现 | 架构回改（rbac §6.2 + uim §2.4）；落地前 404 收口 |
| A-1#9 | APIKey 吊销计数恒 0 | 全站无 APIKey 基建（ADR 登记） |
| A-1#9' | WS 踢出按票据 120s | 主动踢出通道归 COLLAB-004 |
| A-4#1 | RPT-002 基座并入 stats.py | 实现位置偏离规格 §4.3.1 |
| A-4#5 | 限流手工实现 | DRF Throttled 不经 envelope |
| A-6#6 | 终态连败计数器无时间窗 | 概览口径同步消解（T5-01） |
| A-6#4 | project.* 五事件扇出比规格晚一节 | 已在 lifecycle 挂点转真 |

## 五、依赖与前置

- sprint-1/2/3/4 全部能力（迁移 0001~0012）
- COLLAB-003 动态流（迁移 0013 双轨扩域前置）
- COLLAB-004 实时票据（T5-04/05 踢出通道）

## 六、Sprint-6 衔接

- INFRA-005 生产部署（接受本清单基线 + 三交接文档）
- QA-001 收尾（消费 ADR-0026 偏差复核 + tier-2/3 回改）
- 已知技术债：见 `sprint-5-progress-inflight.md`（断档续接）
- 限流交接：见 `intg002-rate-limit-handoff.md`

**冻结签字位（sprint-overview §10.4 纪律）**：

- 质量 + 安全 + 架构三签字缺一不收口（QA-001 / 架构组 / 工程项目）
- 本文件由 Sprint-6 INFRA-005 触发复审与最终签收
