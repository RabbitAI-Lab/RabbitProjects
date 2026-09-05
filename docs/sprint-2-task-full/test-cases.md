# Sprint 2 测试用例文档

> **本文档定位**：覆盖 Sprint 2 全部 7 份功能规格（TASK-004~010）§5 测试用例章节的用例集合，共 **219 条**（UT 119 + IT 61 + E2E 39），用例 ID、命名与断言**逐条转写自规格原文，零新增、零改名**；本文件只做归集、分工落点标注与回归锚点登记，规格 §5 是唯一事实源。
>
> **与 Sprint 0/1 的关系**：UI 表面清单（附录 C）统一维护在 [`docs/sprint-0-poc/test-cases.md`](../sprint-0-poc/test-cases.md) 附录 C，Sprint 2 新增/变更表面为 **C.37~C.63（27 节）**，本文不复制清单，只引用其编号做 parity 断言的出处（见附录 B 映射表）。
>
> **状态：基线（实现前）**——代码实现尚未开始，本文是开发前的用例基线；实现开始后随 CI gate 增补执行入口（§0.2 中标注「计划」的脚本/文件名届时落地并对账）。

| 元信息项 | 内容 |
| --- | --- |
| 所属迭代 | Sprint 2 — 任务体系完善（第 4 周） |
| 文档数 | 7 份功能规格 → 对应 7 组测试用例 |
| 用例总数 | 219 = UT 119 + IT 61 + E2E 39（各章小计见 §1~§7 章首） |
| 验收依据 | [`sprint-overview.md`](./sprint-overview.md) §6 验收标准 10 条、§10 退出条件 3 项 |
| 关联交付（计划） | `tests/jmeter/sprint-2-flow.py`（CI 端到端）、`tests/e2e/parity-sprint2-*.spec.ts`（浏览器端到端 + parity 扫描，7 个计划文件见附录 B）、`apps/api` pytest 专项（并发 / 事务 / 幂等 / 死信 / EXPLAIN）、性能门禁 bench 脚本（10 万数据集） |
| 视觉基准 | [`docs/design/sprint-2-hifi-prototype.html`](../design/sprint-2-hifi-prototype.html)（**FROZEN 2026-09-05**，ADR-0013 裁决定稿；e2e 视觉断言以此为基准） |
| 上游格式基准 | [`docs/sprint-1-mvp/test-cases.md`](../sprint-1-mvp/test-cases.md)（章节组织 / 表格列 / 术语表 / 回归锚点写法完全沿用） |

---

## 0. 测试基线与执行入口

### 0.1 前置依赖

| 类别 | 项 | 状态 |
| --- | --- | --- |
| 运行时 | PostgreSQL 17 容器 `rp-pg`（见 CLAUDE.md 环境表） | ✅ |
| 运行时 | P0 迁移已建 `parent` / `custom_fields` / `archived_at` / `IssueAssignee` / `IssueActivity` / `IssueLink` 全部列与索引（`idx_issue_parent` / `idx_issue_custom_fields` / `idx_assignee_issue` 等，INFRA-003） | ✅（sprint-overview §3 进入条件） |
| 运行时 | DDL 仅 3 项（migration 0005）：`Issue.estimate_minutes` 加列 + 新表 `WorkLog`（TASK-006，P0「零 DDL」原则受控例外）+ 新表 `CustomFieldDefinition`（TASK-008）；其余 5 份规格零 DDL。TASK-008 IT-01 断言 **issues 表**零 DDL（JSONB+GIN 为 P0 预留）；其索引任务为运行时 `CREATE INDEX CONCURRENTLY`（非迁移） | ✅ 用例守护 |
| 配置 | `settings/features.py` 增 `MAX_ISSUE_DEPTH=5`、`CTE_GUARD_DEPTH=100`（TASK-004 交付物） | ⏳ 实现交付 |
| 运行时 | **Redis**（TASK-008 Schema 缓存主动失效；TASK-010 幂等三层去重的完成标记 / 处理锁 / 死信 hash）——**硬前置**，不可降级 | ⚠️ 未起时 TASK-008 UT-14、TASK-010 UT-10/13/17/18 与 IT-04 标「本地不可验证」 |
| 运行时 | **RabbitMQ + Celery worker/beat**（TASK-010 `activity` 队列与 `activity.dlq` 死信路由为**本迭代交付物**；TASK-008 索引 / 清理异步任务；TASK-006/007 通知） | ⚠️ 未起时异步链路用例标注为未真实验证 |
| 运行时 | Django 5.1 + Python 3.12 + uv 同步；Node 22.14 + pnpm 11 | ✅ |
| 执行 | `uv run --project apps/api pytest`（单元 + 并发 / 事务 / 幂等 / 死信专项） | ⏳ 计划（随实现落地） |
| 执行 | `python3 tests/jmeter/sprint-2-flow.py`（CI 端到端，新建） | ⏳ 计划 |
| 执行 | `E2E_NO_SERVER=1 pnpm exec playwright test`（浏览器端到端 + parity） | ⏳ 计划 |
| 执行 | 性能门禁 bench（10 万 Issue 数据集 + `EXPLAIN ANALYZE`，暂记 `tests/jmeter/sprint-2-bench.py`，落名随 CI gate 定） | ⏳ 计划 |
| 执行 | `python3 tests/jmeter/sprint-0-flow.py` / `sprint-1-flow.py`（**回归必跑**——本迭代改动多个 Sprint 0/1 契约点，见附录 A） | ✅ |

### 0.2 测试分工

| 工具 | 角色 | 覆盖 | 触发时机 |
| --- | --- | --- | --- |
| `tests/jmeter/_contract.py` | **契约常量唯一定义点**（沿用 sprint-1） | HTTP 状态码表 / 错误码 / 信封字段路径 / `Client` / 断言辅助 | 被下列脚本 import；禁止各自硬编码 |
| `apps/api` pytest（`uv run pytest`） | **单元 + 专项集成** | 全部 119 条 UT（服务层校验逻辑）；IT 中**无法经单进程 HTTP 复现**的用例：并发事务（advisory lock / 行锁 / 认领交错）、事务异常注入回滚（IT-05 级联删除 / TASK-009 IT-01 深拷贝）、幂等与死信（TASK-010 UT-10~18 / IT-03~04）、`assertNumQueries`（无 N+1 三处）、保险丝脏数据构造（TASK-005 UT-17/18 / IT-10）、worker 进程控制 | PR 必跑（gate） |
| `tests/jmeter/sprint-2-flow.py`（计划新建） | CI 端到端（单线程 HTTP） | IT 中 HTTP 可达用例：建树 / 建链 / 流转拦截 / 工时填报 / 执行人 PUT / 字段 CRUD / 归档恢复 / 时间线查询 / 权限矩阵；并承担 e2e 造数 | PR 必跑（gate） |
| `tests/e2e/parity-sprint2-*.spec.ts`（计划 7 个，见附录 B）+ interactions 扩展 | 浏览器端到端 | 全部 39 条 E2E 动线 + C.37~C.63 parity 字段级扫描（`expect.soft`，断言由附录 C 清单生成、带 `// C.x` 出处注释） | PR 必跑（gate） |
| 性能门禁 bench | **P95 + EXPLAIN ANALYZE 专项** | §8 六项指标（subtree / 拦截 / 工时列表 / 混合筛选 / 归档视图 / 时间线）；10 万数据集灌库脚本 + `EXPLAIN ANALYZE` 计划断言（GIN bitmap AND 命中 / 索引分工） | 迭代末验收 + 大改索引时重跑（Day 5 压测日） |
| `tests/run-ci-checks.sh` | L1/L2 静态检查（沿用） | 命令级断言（结构 / 约束 / 常量，如 `MAX_ISSUE_DEPTH=5` 常量存在性） | PR 必跑（gate） |

> **parity 断言纪律（ADR-0010 ③，沿用）**：`parity-sprint2-*.spec.ts` 的断言**由附录 C.37~C.63 清单生成**，不由实现反推；每条断言带 `// C.x <清单行原文摘要>` 出处注释。
>
> **断言方式约定（sprint-1 验收教训，UI 用例强制，沿用）**：自动化实现必须**从用户入口出发**（登录 → 点导航/卡片到达页面），断言**该页特有**的内容；受权限保护的页面对应写一条负向用例（无权直进 → 前端 403 + 后端 API 404）。
>
> **加粗约定**：§1~§7 用例表中**加粗的行 = CI gate 必须覆盖的关键异常 / 并发 / 性能用例**（深度校验、环检测并发、CTE 保险丝、复制事务、幂等重试、死信、性能门禁）——对应 sprint-overview §10 退出条件第 2 条「异常路径测试全部通过」的机器判据。

---

## 1. TASK-004 多层级子任务与进度联动

> 来源：[`TASK-004-subtask-hierarchy.md`](./TASK-004-subtask-hierarchy.md) §5。本章小计：UT 16 + IT 8 + E2E 7 = **31 条**。

### 1.1 目标

验证三层防线（写入层深度校验 `MAX_ISSUE_DEPTH=5` → 防环 CTE → 查询侧 `CTE_GUARD_DEPTH=100` 保险丝）互不替代、计数单源（直接子级口径）、级联软删事务性与 `subtree/` 性能门禁。

### 1.2 前置

- `settings/features.py` 含 `MAX_ISSUE_DEPTH=5` / `CTE_GUARD_DEPTH=100`；`idx_issue_parent` 索引可用
- TASK-002 既有 `sub-issues/` 端点与 `MAX_SUB_ISSUES_PER_PARENT=100` 校验在位（本迭代放开深度、不放开宽度）
- TASK-009 未交付前，UT-07/UT-09 中归档相关用例可先以直改 `archived_at` 落库构造

### 1.3 用例清单

#### 单元测试（16）

| 用例 ID | 测试目标 | 关键输入 | 预期 | 落点 |
| --- | --- | --- | --- | --- |
| UT-01 | **深度上限拦截** | 已 5 层时下挂第 6 层 | `409 RESOURCE_LIMIT_EXCEEDED`，details 含当前层数 | pytest |
| UT-02 | **直接环** | A.parent = A 的子节点 C | `409` + 环路径含 A 与 C | pytest |
| UT-03 | **间接环（环长 4）** | D.parent = A（A→B→C→D） | 拒绝 + 路径按序渲染 4 节点 | pytest |
| UT-04 | **自引用** | A.parent = A | 拒绝（`_is_descendant` 先判等短路） | pytest |
| UT-05 | 移动不改编号 | 移动 3 层子树到新父 | 子树内 `sequence_id`、`sort_order` 全不变 | pytest |
| UT-06 | 计数口径 | 3 子级：1 完成 1 取消 1 进行 | `sub_issues_count=2`、`completed=1` | pytest |
| UT-07 | 归档树不可见 | 归档父任务 | `subtree/` 404；列表不含整树 | pytest |
| UT-08 | 摘出 | parent_id=null | 变顶层，`depth=1` | pytest |
| UT-09 | 跨项目 parent | parent 属项目 Y | `400 DOES_NOT_EXIST` | pytest |
| UT-10 | **CTE 保险丝** | 人为构造 101 层脏数据（上行祖先链） | 抛 `SubtreeDepthGuardError`（→500 SERVER_ERROR）+ ERROR 告警日志（§4.3.1 SELECT 后判定 `len(chain) ≥ CTE_GUARD_DEPTH`） | pytest |
| UT-11 | 子树截断 | 600 节点 | 500 条 + `truncated=true` + 无 stats | pytest |
| UT-12 | 级联软删计数 | 1 根 + 2 层共 6 后代 | `deleted_count=7`；整树 `deleted_at` 非空 | pytest |
| UT-13 | 权限矩阵 | VIEWER/COMMENTER 挂子任务 | `403 PERM_ROLE_INSUFFICIENT` | pytest |
| UT-14 | 归档项目写保护 | 归档项目挂子任务 | `403 PERM_PROJECT_ARCHIVED` | pytest |
| UT-15 | 归档任务写保护 | 对已归档任务挂子任务 / 挂到已归档父下 | 均 `409 RESOURCE_STATE_INVALID`（TASK-009 BR-08 同口径，BR-13） | pytest |
| UT-16 | **移动越限（子树高度漏算）** | 把高 3 层子树挂到第 4 层父下（仅校验 `depth(parent)+1` 会放行、静默产生第 6/7 层） | `409 RESOURCE_LIMIT_EXCEEDED`（details: DEPTH）；子树零变更（§4.3.2 `_subtree_height` 整体校验） | pytest |

#### 集成测试（8）

| 用例 ID | 场景 | 前置 / 步骤 | 预期 | 落点 |
| --- | --- | --- | --- | --- |
| IT-01 | 四层建树 | 项目与默认状态；建 Epic→需求→任务→子任务 | 全 201；各 `depth` 1/2/3/4 正确 | jmeter |
| IT-02 | **并发互换父子防环** | A、B 互为独立树；并发 PATCH A.parent=B 与 B.parent=A | 恰一个成功一个 409（行锁串行化） | pytest |
| IT-03 | 列表无 N+1 | 100 父任务 × 10 子；`assertNumQueries` 拉列表 | 查询数常数级（annotate 一次聚合） | pytest |
| IT-04 | **性能门禁** | 单项目 1 万 Issue、最深 5 层；`subtree/` 连续 50 次 | P95 < 150ms（`idx_issue_parent` 点查） | bench |
| IT-05 | **级联删除事务性** | 3 层子树；DELETE 根 + 中途注入异常 | 整树无部分删除（全回滚） | pytest |
| IT-06 | Activity 完整性 | 移动子树；查 `IssueActivity` | 1 条 `field=parent`，old/new_identifier 齐全 | jmeter（需 worker） |
| IT-07 | 序列号无空洞 | 删 3 个子任务后再建 | 新任务编号 = 全局 MAX+1，无复用 | jmeter |
| IT-08 | 展开懒加载 | 某父 120 子级；`?parent_id=` 两页 | 50+50，游标稳定 | jmeter |

#### E2E 测试（7）

| 用例 ID | 用户场景 / 操作路径 | 验收标准 | 落点 |
| --- | --- | --- | --- |
| E2E-01 | 需求多层拆解：需求下建两层共 5 个子任务（3 个直接子级 + 其中 1 个再拆 2 个孙级）；依次完成 2 个孙级、3 个直接子级，最后完成需求自身 | 父徽标 `3/3`（BR-04 **直接子级**口径——孙级不进父徽标）；中间父行徽标 `2/2`；全屏树 `stats.total=6 / completed=6 / max_depth=2`（**含根口径**：total/completed 均计入需求自身，§4.2.2 契约要点 2——操作序列共完成 2 孙级 + 3 直接子级 + 根 = 6）；刷新后层级与比例均保持 | e2e（tree） |
| E2E-02 | 拖拽移动子树：把「分页游标改造」子树拖到「前端导出按钮」下 | 确认后树重排；原父计数 -1、新父 +1；编号不变 | e2e（tree） |
| E2E-03 | **非法移动拦截**：把父任务拖到自己子级下，确认 | Toast 显示完整环路径；树上环节点红显 2s；无数据变更 | e2e（tree） |
| E2E-04 | 深度入口预判：在第 5 层任务行悬浮 | 无「＋」子任务入口；直连 API 409 | e2e（tree） |
| E2E-05 | 级联删除确认：删除有 6 后代的父任务 | 确认弹层列数量；成功后整树从列表消失 | e2e（tree） |
| E2E-06 | 折叠记忆：展开某分支后刷新 | 折叠状态还原（localStorage） | e2e（tree） |
| E2E-07 | **移动深度拦截**：把 3 层子树拖到第 4 层任务下并确认 | `409 RESOURCE_LIMIT_EXCEEDED` + Toast「移动后子树最深将达第 7 层，超出 5 层上限」；树无数据变更（§4.3.2 子树高度校验：被移根新深度 = 4+1 = 5，最深节点 = 5+(3−1) = 7，与 UT-16 括注同口径） | e2e（tree） |

---

## 2. TASK-005 任务前置 / 后置依赖关系

> 来源：[`TASK-005-task-dependency.md`](./TASK-005-task-dependency.md) §5。本章小计：UT 18 + IT 10 + E2E 6 = **34 条**。

### 2.1 目标

验证成对存储（镜像行）不变量、递归 CTE 依赖防环与项目级 advisory lock 串行化（READ COMMITTED 并发窗口）、流转拦截（`RESOURCE_TRANSITION_BLOCKED`）与 2ms 拦截门禁；`CTE_GUARD_DEPTH=100` 在依赖侧是**脏数据告警线**（闭合命中 → 500），不是业务限制。

### 2.2 前置

- `IssueLink` 表与 `uniq_issue_relation` 约束在位（P0）；`link_service` 成对写入入口可用
- 项目级 advisory lock（`acquire_project_lock`，与序列号生成同款）已封装
- 环检测 CTE 与 `CTE_GUARD_DEPTH=100` 保险丝实现就位（UT-17/18/IT-10 直接锚定其边界行为）

### 2.3 用例清单

#### 单元测试（18）

| 用例 ID | 测试目标 | 关键输入 | 预期 | 落点 |
| --- | --- | --- | --- | --- |
| UT-01 | 成对写入 | 建 A blocks B | 两行落库，类型互为镜像 | pytest |
| UT-02 | 归一化 | 传 `is_blocked_by` | 落库仍是正向 blocks + 镜像 | pytest |
| UT-03 | **直接环** | 建 B blocks A（已有 A blocks B） | 409 + 链路径 | pytest |
| UT-04 | **间接环（链长 4）** | A→B→C→D 后建 D blocks A | 409 + 4 节点链 | pytest |
| UT-05 | 镜像边不误报 | `relates_to` 双向建 | 不触发环检测，成功 | pytest |
| UT-06 | 自引用 | A→A | 400（Service 拦截先于 DB） | pytest |
| UT-07 | 跨项目 | 目标属项目 Y | 400 | pytest |
| UT-08 | 重复业务事实 | 建 (B,A,blocks)（已有 (A,B)） | 409 已存在 | pytest |
| UT-09 | 拦截判定 | 2 前置：1 完成 1 进行 | 409，blockers 仅列进行中项 | pytest |
| UT-10 | 取消解除阻塞 | 前置全部取消 | 允许完成 | pytest |
| UT-11 | relates_to 不拦截 | 相关任务未完成 | 允许完成 | pytest |
| UT-12 | 强制完成权限 | CONTRIBUTOR force=true | 403；PROJ_ADMIN + comment 通过 | pytest |
| UT-13 | 强制缺 comment | PROJ_ADMIN force 无 comment | 400 REQUIRED | pytest |
| UT-14 | 删镜像一致 | DELETE 正向行 | 镜像行同事务软删 | pytest |
| UT-15 | 关联数上限 | 第 51 条 | 409 LIMIT | pytest |
| UT-16 | 级联删除 | 删有 3 关联的任务 | 关联（含镜像）全部软删 | pytest |
| UT-17 | **保险丝触发（脏数据告警）** | 构造 102 任务 / 101 条边链 T0→…→T101，再建 T101 blocks T0（闭合） | 500 `SERVER_ERROR` + ERROR 告警（非 409——闭合时 target 命中 depth=100 触发保险丝；若仅 101 任务/100 边，hit_depth=99 只走 409 正常环分支，§4.3.2）；关系零写入 | pytest |
| UT-18 | **合法深链不拦截** | 构造 120 层链后新建不闭合边（新任务 X blocks 链首） | 201；扫描超保险丝截断、未命中 target 按不可达放行，不触发 500 | pytest |

#### 集成测试（10）

| 用例 ID | 场景 | 前置 / 步骤 | 预期 | 落点 |
| --- | --- | --- | --- | --- |
| IT-01 | 建链与查询 | 3 任务；依序建 A→B、B→C，查 A relations | 结构符合 §4.2.1 契约（含内联 related_issue） | jmeter |
| IT-02 | **并发镜像双写** | 空关系；并发建 (A,B) 与 (B,A) | 恰一个 201，一个 409（约束兜底） | pytest |
| IT-03 | **并发环构造** | A→B 已建；并发建 B→C 与 C→A | 两事务被项目 advisory lock 串行化，后提交者检测到已提交边 → 409 `RESOURCE_CIRCULAR_DEPENDENCY`，无环落库 | pytest |
| IT-04 | 看板拦截路径 | 前置未完成；PATCH state → completed | 409 `RESOURCE_TRANSITION_BLOCKED`；状态未变 | jmeter |
| IT-05 | 解除后放行 | 前置未完成 → 完成前置；再 PATCH completed | 200，`completed_at` 写入 | jmeter |
| IT-06 | 删除任务级联 | C 有出入关联各 1；DELETE C | 关联 4 行（含镜像）软删；对端关联区刷新后消失 | jmeter |
| IT-07 | 详情查询无 N+1 | 50 关联；`assertNumQueries` GET relations | 常数级（select_related 内联） | pytest |
| IT-08 | **性能门禁** | 1 万任务、密度 5 关联/任务；拦截检查 1000 次 | P95 < 2ms（索引点查） | bench |
| IT-09 | relations 三端点角色矩阵 | 项目内有 `PROJ_VIEWER` / `PROJ_COMMENTER` / `PROJ_CONTRIBUTOR` 三名成员，存在 1 条既有关联；三角色分别调 GET / POST / DELETE relations | VIEWER、COMMENTER：GET 200，POST / DELETE 403 `PERM_ROLE_INSUFFICIENT`；CONTRIBUTOR：POST 201 + `Location`，DELETE 204（rbac §8 `issue.read` / `issue.relation.manage`） | jmeter |
| IT-10 | **保险丝触发端到端（真库链路）** | 1 项目、102 个任务（脚本批量建模）；脚本依序构造 101 条边 blocks 链 T0→…→T101，再建闭合边 T101 blocks T0（闭合时 hit_depth=100；100 边链闭合仅 hit_depth=99 → 409，不足以触发） | 500 `SERVER_ERROR`（响应含 request_id）；服务端 ERROR 告警记录环两端 ID；关系零写入（UT-17 的集成版，UT-18 的深链放行在同一脚本中断言） | pytest |

#### E2E 测试（6）

| 用例 ID | 用户场景 / 操作路径 | 验收标准 | 落点 |
| --- | --- | --- | --- |
| E2E-01 | 建立阻塞闭环：详情添加「被…阻塞」→ 选目标任务 | 两端详情页关联区同时出现（正确的分组）；列表行出现 link 图标 | e2e（dependency） |
| E2E-02 | 完成被拦：拖被阻塞卡片入「已完成」列 | 卡片弹回 + 对话框列阻塞项；点阻塞项可跳转 | e2e（dependency） |
| E2E-03 | 解除后可完成：完成全部前置后重拖 | 卡片就位；角标消失；`RPT-001` 完成计数联动 | e2e（dependency） |
| E2E-04 | 管理员强制：对话框「强制完成」+ comment | 成功；Activity 动态显示强制标记与意见 | e2e（dependency） |
| E2E-05 | **环拦截可视化**：构造 A→B 后建 B→A | 弹层红条展示「A → B」依赖链；无数据写入 | e2e（dependency） |
| E2E-06 | 删除关联：关联区 ⓧ 删除 | 二次确认后两端同时消失 | e2e（dependency） |

---

## 3. TASK-006 工时估算与工时填报

> 来源：[`TASK-006-worklog.md`](./TASK-006-worklog.md) §5。本章小计：UT 17 + IT 8 + E2E 5 = **30 条**。
>
> 转写说明：规格 §5.2 表存在列错位（IT-02/03/04 的「操作步骤 / 预期结果」串列），本表按 §2 业务规则归位补全，行尾标「※补全」。

### 3.1 目标

验证分钟整数制校验（1~1440）、30 天补填窗口（编辑时对新值重校验）、`spent_minutes` 单一事实来源（annotate 无笛卡尔放大）、子树上卷口径与列表 300ms 门禁。

### 3.2 前置

- `WorkLog` 表与 `Issue.estimate_minutes` 字段在位；`?actor_id=&worked_on=` 筛选与 `meta.sum_minutes` 回传按 §4.2.2 实现
- TASK-004 `subtree/` 已交付（IT-04 消费其 stats 扩展）

### 3.3 用例清单

#### 单元测试（17）

| 用例 ID | 测试目标 | 关键输入 | 预期 | 落点 |
| --- | --- | --- | --- | --- |
| UT-01 | 分钟整数校验 | minutes=90.5 | 400 INVALID | pytest |
| UT-02 | 区间下界 | minutes=0 | 400 TOO_SMALL | pytest |
| UT-03 | 区间上界 | minutes=1441 | 400 TOO_LARGE | pytest |
| UT-04 | 边界合法值 | minutes=1 与 1440 | 均 201 | pytest |
| UT-05 | 补填窗口 | worked_on=今天−31 | 400 INVALID_DATE | pytest |
| UT-06 | 未来日期 | worked_on=明天 | 400 | pytest |
| UT-07 | actor 注入 | 请求体带 actor_id | 忽略，落库为当前用户 | pytest |
| UT-08 | 本人编辑 | 修改自己的记录 | 200 | pytest |
| UT-09 | **汇总无放大** | 任务 3 指派 × 各 5 记录 | `spent_minutes` = 精确总和（无笛卡尔重复） | pytest |
| UT-10 | 子树上卷 | 3 层树各含记录 | `subtree_spent` = 全树和；未估算节点不入 estimate 和 | pytest |
| UT-11 | 权限 | 改他人记录（非 ADMIN） | 403 | pytest |
| UT-12 | 估算上限 | 525601 | 400 TOO_LARGE | pytest |
| UT-13 | 完成后补填 | 已完成任务 + worked_on=昨天 | 201（BR-12） | pytest |
| UT-14 | 软删联动 | 删除任务 | WorkLog 保留但不可见；恢复重现 | pytest |
| UT-15 | 窗口边界值 | worked_on=今天−30（恰含端点）与 今天−31 | 前者 201；后者 400 INVALID_DATE | pytest |
| UT-16 | 编辑重校验窗口 | 29 天前记录改 worked_on=31 天前 | 400（校验作用于新值，§4.3.5） | pytest |
| UT-17 | 权限矩阵 | VIEWER/COMMENTER 填报 vs CONTRIBUTOR/ADMIN | 前者 403 PERM_ROLE_INSUFFICIENT；后者 201 | pytest |

#### 集成测试（8）

| 用例 ID | 场景 | 前置 / 步骤 | 预期 | 落点 |
| --- | --- | --- | --- | --- |
| IT-01 | 多人填报同一任务 | 2 成员；各填 2 笔 | `spent` = 4 笔总和；列表按日期倒序 | jmeter |
| IT-02 | 同日多笔 | 同一成员同日填报 2 笔 ※补全 | 均 201（BR-06） | jmeter |
| IT-03 | 估算-实际独立 | 设估算 480；再填报 600 ※补全 | estimate 仍 480；偏差展示 125% | jmeter |
| IT-04 | subtree 契约 | 有子树与填报；GET `subtree/` ※补全 | stats 含两个新字段，契约符合 §4.2.4 | jmeter |
| IT-05 | **列表 annotate 性能** | 1 万任务 × 均 5 记录；列表查询 | 常数查询数；P95 < 300ms（沿用 TASK-003 门禁） | bench |
| IT-06 | 归档项目填报 | 项目归档；POST worklog | 403 PERM_PROJECT_ARCHIVED | jmeter |
| IT-07 | Activity 留痕 | 填报→修改→删除；查 IssueActivity | 3 条 field=worklog，epoch 各异 | jmeter（需 worker） |
| IT-08 | 筛选与聚合回传 | 2 人各填数笔；`?actor_id=&worked_on=…;between` | 结果仅含该人区间记录；`meta.sum_minutes` 等于手加（§4.2.2） | jmeter |

#### E2E 测试（5）

| 用例 ID | 用户场景 / 操作路径 | 验收标准 | 落点 |
| --- | --- | --- | --- |
| E2E-01 | 快速填报：侧栏 ⏱ → 2h → 保存 | 已耗 +2h；进度条与百分比即时更新；记录列表出现新行 | e2e（worklog） |
| E2E-02 | 估算与偏差：设估算 4h → 填 5h | 已耗红显，进度条红 125% | e2e（worklog） |
| E2E-03 | 补填历史：弹层选 3 天前日期 | 成功；日期器禁选 31 天前与未来 | e2e（worklog） |
| E2E-04 | 编辑自己的记录：⋯ → 改 2h 为 3h | 已耗 +1h；他人记录行无 ⋯ | e2e（worklog） |
| E2E-05 | 子树口径切换：父任务 ⊕含子任务 开关 | 数字在本任务 5.5h 与子树 7h 间切换 | e2e（worklog） |

---

## 4. TASK-007 多执行人 / 任务转交 / 认领

> 来源：[`TASK-007-multi-assignee.md`](./TASK-007-multi-assignee.md) §5。本章小计：UT 17 + IT 10 + E2E 5 = **32 条**。
>
> 转写说明：规格 §5.2 IT-05 行列错位（前置/步骤/预期串列），按 §1.2 双路径收敛语义归位，行尾标「※补全」。

### 4.1 目标

验证集合替换语义（`PUT …/assignees/` 全量替换与 PATCH `assignee_ids` 收敛到同一 `sync_assignees`）、去重保序、上限 10、active 成员且 ≥CONTRIBUTOR 候选约束、认领/自退并发交错与「重加同人全新行」的软删复活语义。

### 4.2 前置

- `IssueAssignee` 表（`assigned_by` / `uniq_issue_assignee` / `idx_assignee_issue`）P0 已建，本迭代零 DDL 只放开限制
- PROJ-002 active 成员候选集与 AUTH-005 权限码（`issue.update` 等）在位
- 通知通道（COLLAB-001）可用；`issue.unassigned` 事件键为本迭代新增

### 4.3 用例清单

#### 单元测试（17）

| 用例 ID | 测试目标 | 关键输入 | 预期 | 落点 |
| --- | --- | --- | --- | --- |
| UT-01 | 多人替换 | 3 人集合 | 3 行存活，changes 正确 | pytest |
| UT-02 | 去重保序 | [B,A,B] | 落库 [B,A] | pytest |
| UT-03 | 上限 | 11 人 | 409 LIMIT | pytest |
| UT-04 | 边界值 | 恰 10 人 | 200 | pytest |
| UT-05 | 非成员指派 | 他项目用户 | 400 DOES_NOT_EXIST | pytest |
| UT-06 | COMMENTER/VIEWER 指派 | 项目 COMMENTER 与项目 VIEWER 各一 | 400 DOES_NOT_EXIST | pytest |
| UT-07 | 认领空任务 | 集合空 | 200，assigned_by=自己 | pytest |
| UT-08 | 认领非空 | 集合 1 人 | 409 STATE | pytest |
| UT-09 | **重加同人（软删复活语义）** | 删后重加 | 旧行已物理删除，全新行 INSERT 不撞唯一约束；assigned_by/created_at 刷新为本次操作 | pytest |
| UT-10 | 清空合法 | [] | 200，任务入未指派 | pytest |
| UT-11 | 自退权限 | DELETE 他人 user_id | 403 | pytest |
| UT-12 | assigned_by 记录 | A 操作 | 新行 assigned_by=A | pytest |
| UT-13 | comment 上限 | 501 字 | 400 TOO_LONG | pytest |
| UT-14 | 通知抑制 | 认领自己 | 无通知产生 | pytest |
| UT-15 | 级联清理 | 成员移出项目 | 其该项目全部指派行物理删除 | pytest |
| UT-16 | 自退收敛 | 3 人中 1 人自退 | 剩余 2 人集合不变（remove_self 经 sync）；最后一人自退 = 清空 | pytest |
| UT-17 | 候选校验单查询 | 10 候选全合法；10 候选含 1 个非法 | 成功路径 `assertNumQueries(1)`（IN 批量，§4.3.4）；失败路径允许 +1（错误文案经 `_name` 查 `User.display_name`，非法候选首查未命中缓存） | pytest |

#### 集成测试（10）

| 用例 ID | 场景 | 前置 / 步骤 | 预期 | 落点 |
| --- | --- | --- | --- | --- |
| IT-01 | 转交通知差异化 | 原 2 人；PUT 换成 3 人（1 老 2 新） | 老人收移出、新人收指派；操作者无通知 | jmeter（需 worker） |
| IT-02 | **并发认领** | 空任务；两用户同时 claim | 一 200 一 409 | pytest |
| IT-03 | Activity 逐人留痕 | 加 2 删 1；查 IssueActivity | 3 条 field=assignees 同 epoch | jmeter（需 worker） |
| IT-04 | 我的待办联动 | 指派 2 人；两人各自 /users/me/issues | 各自列表出现该任务（BR-14） | jmeter |
| IT-05 | PUT/PATCH 收敛 | 同一集合分别经 PUT 与 PATCH 两路径提交 ※补全 | 落库一致 | jmeter |
| IT-06 | 任务软删级联 | 有 3 执行人；删除任务 | 指派行物理删除（表内不复存在）；恢复后任务呈未指派态，历史经 Activity 可溯（BR-11） | jmeter |
| IT-07 | 归档项目 | 项目归档；PUT assignees | 403 PERM_PROJECT_ARCHIVED | jmeter |
| IT-08 | **认领 vs 转交交错** | 空任务；并发 claim 与 PUT 不同集合 | 行锁串行：其一成功；最终集合恰为后提交者意图（§2.2.1） | pytest |
| IT-09 | 未指派筛选 | 清空 2 个任务集合；`?assignee_ids=null` | 恰返回 2 条；被移除者行已物理删除、天然不参与判定（§4.2.5） | jmeter |
| IT-10 | 已归档任务写保护 | 任务已归档（`archived_at` 非空）、集合非空；分别执行 PUT 全量替换 / `claim` / 自退 `DELETE` 三路径 | 全部 `409 RESOURCE_STATE_INVALID`（`STATE`）——PUT/claim 由 `ProjectEntityPermission` 拦截、自退经 `sync_assignees` 入口 `archived_at` 判定兜底（§2.4 末行、§4.3.1；范式对齐 `TASK-009` UT-13 归档写保护） | jmeter |

#### E2E 测试（5）

| 用例 ID | 用户场景 / 操作路径 | 验收标准 | 落点 |
| --- | --- | --- | --- |
| E2E-01 | 多人指派：编辑选 3 人 + 转交说明保存 | 堆叠 3 头像；新人心铃响；动态出现逐人记录 | e2e（assignee） |
| E2E-02 | 转交：移除自己改指派他人 | 自己待办消失该任务、对方出现；双方通知文案正确 | e2e（assignee） |
| E2E-03 | 认领：未指派任务点 🖐 | 头像即时变自己；另一人后点收到「已被认领」 | e2e（assignee） |
| E2E-04 | 自退：最后一人退出 | 二次确认后任务显示未指派；「未指派」筛选可见 | e2e（assignee） |
| E2E-05 | 上限交互：选第 11 人 | 未选项禁用 + 计数红；直连 API 409 | e2e（assignee） |

---

## 5. TASK-008 基础自定义字段动态增删

> 来源：[`TASK-008-custom-fields-basic.md`](./TASK-008-custom-fields-basic.md) §5。本章小计：UT 16 + IT 10 + E2E 6 = **32 条**。

### 5.1 目标

验证元数据驱动 12 类型值校验、key/类型不可变、JSONB 存储纪律（空值不落 key）、作用域私有覆盖全局、Redis 缓存主动失效（零发版生效）、零 DDL（G1）与 GIN 混合筛选 200ms 门禁（G7）。

### 5.2 前置

- `issues.custom_fields` JSONB + `idx_issue_custom_fields` GIN 索引 P0 已建（INFRA-003）；`CustomFieldDefinition` 元数据表就位
- Redis 可用（Schema 缓存）；Celery worker 可用（索引任务 / 删除清理批任务）
- 10 万 Issue / 20 字段基准数据集灌库脚本就绪（IT-03 / §8 门禁共用）

### 5.3 用例清单

#### 单元测试（16）

| 用例 ID | 测试目标 | 关键输入 | 预期 | 落点 |
| --- | --- | --- | --- | --- |
| UT-01 | key 格式全分支 | 无前缀/大写/短横线/超 64 | 全部 400 | pytest |
| UT-02 | key 创建后不可改 | PATCH 改 key | 拒绝（BR-01） | pytest |
| UT-03 | 类型不可改 | PATCH 改 field_type | 拒绝（BR-06） | pytest |
| UT-04 | 选项 value 唯一 | 两个 critical | 400 | pytest |
| UT-05 | 12 类型值校验逐类 | 每类型 合法/非法/空 三组 | 36 断言全过 | pytest |
| UT-06 | 未知 key 拒绝 | payload 含 cf_hack | 400（BR-07） | pytest |
| UT-07 | 类型作用域 | 字段限 bug，需求任务提交 | 400 | pytest |
| UT-08 | 默认值填充 | 新建未传、字段有默认 | 落库含默认值 | pytest |
| UT-09 | 空值不落 key | 传 null | JSONB 无该 key | pytest |
| UT-10 | 必填存量不追溯 | 改必填后旧任务保存他字段 | 通过（仅新保存拦缺失） | pytest |
| UT-11 | 私有覆盖全局 | 同 key 双定义 | resolve 取私有 | pytest |
| UT-12 | auto_increment 拒赋值 | 客户端传 999 | 400（BR-09） | pytest |
| UT-13 | **auto_increment 并发** | 并发创建 10 任务 | 编号 1~10 无重 | pytest |
| UT-14 | 缓存失效 | 全局字段改名 | 该 WS 全部项目 schema 缓存被清（BR-13） | pytest（需 Redis） |
| UT-15 | 数量上限 | 第 51 个字段 / 第 11 个索引 | 409 | pytest |
| UT-16 | 逐键 diff | 改 2 个自定义字段 | 2 条 Activity(field=cf_*) | pytest |

#### 集成测试（10）

| 用例 ID | 场景 | 前置 / 步骤 | 预期 | 落点 |
| --- | --- | --- | --- | --- |
| IT-01 | **零 DDL 全程（G1 门禁）** | 干净库；增→改→停→删字段 | `django migrate` 无新迁移文件（G1 断言） | pytest |
| IT-02 | 零发版生效 | 已打开列表页；另一会话建字段，原页面刷新 | 新列/新筛选控件出现（G2/G4） | e2e（cfields） |
| IT-03 | **GIN 等值筛选（G7 门禁）** | 10 万 Issue、20 字段、5 条混合条件；`EXPLAIN ANALYZE` + 计时 | 走 `idx_issue_custom_fields`；P95 < 200ms（G7 门禁） | bench（EXPLAIN） |
| IT-04 | 为空筛选安全 | 仅 `property.x=null` 无其他条件；直连 | 400（强制组合其他条件，§4.3.6） | jmeter |
| IT-05 | 删除清理 | 5 万条含该字段值；DELETE + 轮询任务 | JSONB key 全清；每批 2000；无长事务 | pytest（需 worker） |
| IT-06 | 索引任务 | 标记 is_indexed；Celery 执行 | `CONCURRENTLY` 建成；重复执行幂等 | pytest（需 worker） |
| IT-07 | 排序正确性 | 数字字段值 9/10/100；order_by | 9<10<100（::numeric，非字典序） | jmeter |
| IT-08 | 选项配置序排序 | select 自定义顺序 B,A,C；order_by | 按配置序非字典序 | jmeter |
| IT-09 | Schema ETag | 二次请求带 If-None-Match；定义未变 / 变更 | 304 / 200 新 etag | jmeter |
| IT-10 | 权限矩阵 | CONTRIBUTOR 无授权建字段；POST | 403（BR-15） | jmeter |

#### E2E 测试（6）

| 用例 ID | 用户场景 / 操作路径 | 验收标准 | 落点 |
| --- | --- | --- | --- |
| E2E-01 | 五分钟上线一个字段：管理页创建「严重等级」（select，3 选项，必填，索引） | 新建弹窗/详情/列表列/筛选器四处即现；必填拦截生效 | e2e（cfields） |
| E2E-02 | 改名不迁数据：改 label「致命→致命(P0)」 | 存量值显示新 label，value 不变 | e2e（cfields） |
| E2E-03 | 类型隔离：字段限缺陷类型 | 需求表单无该字段；直连提交 400 | e2e（cfields） |
| E2E-04 | 停用与恢复：停用→查任务→启用 | 停用期 UI 隐藏但数据保留；启用即恢复显示 | e2e（cfields） |
| E2E-05 | 删除全链路：删除有 1284 值的字段（输入确认） | 202→任务进度→完成后旧值清除、Schema 无此字段 | e2e（cfields） |
| E2E-06 | 筛选联动：`?property.<id>=critical,major` + 排序 | 结果精确；刷新还原（URL 同源） | e2e（cfields） |

---

## 6. TASK-009 任务复制 / 归档 / 恢复

> 来源：[`TASK-009-task-copy-archive.md`](./TASK-009-task-copy-archive.md) §5。本章小计：UT 17 + IT 7 + E2E 5 = **29 条**。
>
> 转写说明：规格 §5.2 IT-02/IT-03 行列错位（前置/步骤/预期串列），按 §2.1/§2.2 语义归位，行尾标「※补全」。

### 6.1 目标

验证深拷贝事务一致性（一次锁 + 连续号段 + 映射重建 + `COPY_TARGET_SQL` 全量计数预检）、副本后缀按 `duplicates` 关联计数、归档级联与幂等、恢复对称、归档写保护与统计退出（`archived_at IS NULL` 排除方向）。

### 6.2 前置

- TASK-004 子树服务（`fetch_subtree` / 级联语义）与 TASK-005 `link_service`（副本 `duplicates` 关联）已交付
- `idx_issue_active_by_project` 偏索引在位；归档视图走项目复合索引的反方向扫描（索引分工见其 §4.1）

### 6.3 用例清单

#### 单元测试（17）

| 用例 ID | 测试目标 | 关键输入 | 预期 | 落点 |
| --- | --- | --- | --- | --- |
| UT-01 | 副本标题后缀 | 首次/二次复制 | `(副本)` / `(副本 2)`（由现存 `duplicates` 关联计数驱动，BR-01） | pytest |
| UT-02 | 标题超长截断 | 510 字标题 | 截断保留后缀 | pytest |
| UT-03 | 状态落默认 | 源为已完成 | 副本=默认待办，completed_at NULL | pytest |
| UT-04 | 号段连续 | 复制 4 节点 | 4 个连续 sequence_id | pytest |
| UT-05 | 结构重建 | 3 层子树 | 副本同构（parent 映射正确） | pytest |
| UT-06 | 历史不复制 | 源有评论/工时/关联 | 副本全无 | pytest |
| UT-07 | 选项边界 | 全部选项关 | 纯净副本（无标签/执行人/字段/日期） | pytest |
| UT-08 | **子树超限** | 612 节点（目标集全量计数） | 409 LIMIT（拒绝而非截断复制，BR-05） | pytest |
| UT-09 | 归档级联 | 3 层树归档根 | 整树 archived_at 置位 | pytest |
| UT-10 | 首次时间保留 | 先归档子、再归档父 | 子保持更早时间戳 | pytest |
| UT-11 | 归档幂等 | 连续两次 POST archive | 均 200，第二次 count=0 | pytest |
| UT-12 | 恢复对称 | 归档→恢复 | 整树回归，状态字段不变 | pytest |
| UT-13 | 归档写保护 | 归档后 PATCH/评论/挂子任务/复制 | 全部 409 | pytest |
| UT-14 | 统计退出 | 归档后查 RPT-001 | 不计数（BR-11） | pytest |
| UT-15 | 权限矩阵（参照 TASK-005 IT-09 三角色范式） | VIEWER / COMMENTER / 非成员分别调 复制+归档+恢复 | VIEWER、COMMENTER：全部 403 `PERM_ROLE_INSUFFICIENT`（BR-14）；非成员：404 `RESOURCE_NOT_FOUND`（存在性隐藏） | pytest |
| UT-16 | **并发复制同源** | 两请求同时 | 各自成组互不干扰 | pytest |
| UT-17 | 已归档后代复制 | 源树含已归档子任务 | 一并复制为活跃副本（BR-15），副本树结构同构 | pytest |

#### 集成测试（7）

| 用例 ID | 场景 | 前置 / 步骤 | 预期 | 落点 |
| --- | --- | --- | --- | --- |
| IT-01 | **深拷贝事务性** | 3 层子树；注入第 2 节点构造失败 | 整体回滚，零残留（BR-06） | pytest |
| IT-02 | 复制后独立性 | 完成一次复制；改副本标签/字段 ※补全 | 源零变化 | jmeter |
| IT-03 | 归档→看板联动 | 根任务在看板/列表可见；归档根任务 ※补全 | 看板/列表即时消失；归档视图可见 | jmeter |
| IT-04 | 撤销恢复 | Toast 撤销；DELETE archive | 整树回归原位 | jmeter |
| IT-05 | Activity 留痕 | 复制+归档+恢复；查日志 | 3 类 Activity 齐全（BR-12） | jmeter（需 worker） |
| IT-06 | **归档视图查询（性能门禁）** | 1 万任务半数归档；`?archived=true` | 项目复合索引扫描 + 归档过滤（不命中 active 偏索引，§4.1 索引分工），P95 < 200ms | bench |
| IT-07 | 归档项目前置 | 项目归档；duplicate/archive | 403 PERM_PROJECT_ARCHIVED | jmeter |

#### E2E 测试（5）

| 用例 ID | 用户场景 / 操作路径 | 验收标准 | 落点 |
| --- | --- | --- | --- |
| E2E-01 | 整树复制：详情复制（默认选项） | 新树 4 任务同构、待办态、连续编号；Toast 可跳转 | e2e（copy-archive） |
| E2E-02 | 无子任务复制：关闭子任务选项 | 仅根副本 1 个 | e2e（copy-archive） |
| E2E-03 | 归档与撤销：归档含子树任务 → 点撤销 | 移除后 10s 内一键回归 | e2e（copy-archive） |
| E2E-04 | 归档视图浏览：切「显示已归档」 | 半透明行、只恢复/删除菜单；恢复后可再编辑 | e2e（copy-archive） |
| E2E-05 | 只读保护：归档任务尝试拖看板/评论 | 409 提示与恢复入口 | e2e（copy-archive） |

---

## 7. TASK-010 全操作留痕审计日志

> 来源：[`TASK-010-full-audit-log.md`](./TASK-010-full-audit-log.md) §5。本章小计：UT 18 + IT 8 + E2E 5 = **31 条**。

### 7.1 目标

验证可靠投递三原则的机器判据：on_commit 后投递（回滚不留痕 / 主请求零阻塞）、`event_key` 三层去重幂等（完成标记 → DB 同键 → Redis 锁；占位绝不先于落库、锁占用失败不丢弃）、死信兜底（3 次重试 → `activity.dlq` + 告警 + 管理端重放）与 epoch 分组时间线契约。

### 7.2 前置

- **RabbitMQ + Celery worker 运行中**；`activity` 队列与 `activity.dlq` 死信路由（DLX）为本迭代交付配置，先于用例部署
- **Redis 运行中**（完成标记 / 处理锁 / 死信 hash `activity:dlq:{message_id}`，TTL 7 天）
- `issue_activities` 表与三索引 P0 已建（INFRA-003）；§1.2 事件覆盖矩阵为 IT-01 的对照清单

### 7.3 用例清单

#### 单元测试（18）

| 用例 ID | 测试目标 | 关键输入 | 预期 | 落点 |
| --- | --- | --- | --- | --- |
| UT-01 | 标量 diff | 改 priority | 1 条，old/new 文本 | pytest |
| UT-02 | 多字段一次 PATCH | 改 3 字段 | 3 条同 epoch | pytest |
| UT-03 | FK diff | 状态流转 | old/new 名+ID 四字段齐 | pytest |
| UT-04 | M2M 拆分 | 加 2 删 1 执行人 | 3 条逐项 | pytest |
| UT-05 | custom_fields 逐键 | 改 2 键 | 2 条 field=cf_* | pytest |
| UT-06 | 描述标记化 | 改描述 | `__modified__` 不落全文 | pytest |
| UT-07 | 敏感脱敏（值侧 / 字段名侧） | ① 值含 token 字样；② 字段名 `cf_webhook_url` 而值不含敏感词（如 `https://hooks.example/x`） | 两者值均为 `***`（字段名 OR 值任一命中即整体脱敏，§4.3.1 `_clip`） | pytest |
| UT-08 | 值截断 | 600 字新值 | 500+`…` | pytest |
| UT-09 | **回滚不留痕** | 事务内抛异常 | 0 条 Activity | pytest |
| UT-10 | **幂等（Redis）** | 同 payload 投递两次 | 1 组落库 | pytest（需 Redis+MQ） |
| UT-11 | **幂等（DB 兜底）** | Redis 清空后重放 | 不重复 | pytest（需 Redis+MQ） |
| UT-12 | epoch 贯穿 | 深拷贝 4 节点 | 4 条 created 同 epoch | pytest |
| UT-13 | **死信路由** | Worker 持续失败 | 3 次重试后入 dlq + 告警 | pytest（需 MQ） |
| UT-14 | 时间线过滤 | field=state | 仅状态记录 | pytest |
| UT-15 | 越权 | 非成员查时间线 | 404 | pytest |
| UT-16 | 软删保留 | 删除任务 | 其 Activity 管理端可查 | pytest |
| UT-17 | **失败不占位** | 首次落库失败（模拟 DB 瞬断）后重试 | 锁已释放、完成标记未写；重试成功落库——重试窗口不丢日志 | pytest（需 Redis+MQ） |
| UT-18 | **锁崩溃窗口不丢失** | 消费者获锁后进程被杀（锁残留 300s），消息被 acks_late 重投 | 新消费者**不 return 被 ack 丢弃**：`retry(countdown=310)` 等锁过期后重试成功落库——硬崩溃窗口消息不静默丢失 | pytest（需 Redis+MQ） |

#### 集成测试（8）

| 用例 ID | 场景 | 前置 / 步骤 | 预期 | 落点 |
| --- | --- | --- | --- | --- |
| IT-01 | 全矩阵覆盖 | —；逐一执行 §1.2 全部操作 | 每操作 ≥1 条对应 Activity | jmeter（需 worker） |
| IT-02 | 主请求零阻塞 | 慢 Worker；计时 PATCH | 业务响应不含日志耗时 | pytest（慢 Worker 桩） |
| IT-03 | **Worker 宕机恢复** | 停 Worker 执行写；重启 | 积压按序消费，时间线完整 | pytest（worker 进程控制） |
| IT-04 | **死信重放** | 制造死信 → §4.2.2 admin 端点重放；查时间线 | 补齐且无重复（信封/权限码/`Idempotency-Key` 符合契约） | pytest（需 Redis+MQ） |
| IT-05 | 预聚合正确 | 同 epoch 3 条；GET activities | 1 组 items=3 | jmeter |
| IT-06 | 评论合并 | 2 评论 + 1 更新；前端时间线 | 三条按时间归并（BR-05） | e2e（activity） |
| IT-07 | **性能门禁** | 单任务 5000 条动态；GET 首页 | P95 < 100ms（索引排序） | bench |
| IT-08 | 分页不割裂 | 同 epoch 跨页边界；翻页 | 组完整（游标 epoch 锚定） | jmeter |

#### E2E 测试（5）

| 用例 ID | 用户场景 / 操作路径 | 验收标准 | 落点 |
| --- | --- | --- | --- |
| E2E-01 | 时间线完整性：改 3 字段 + 流转 + 指派 | 分组渲染正确、值对比清晰、刷新保持 | e2e（activity） |
| E2E-02 | 工时与复制事件：填工时、复制任务 | 「⏱ 填报…」「由 RBT-12 复制…」正确呈现 | e2e（activity） |
| E2E-03 | 过滤：只看状态变更 | 仅状态组；URL 分享还原 | e2e（activity） |
| E2E-04 | 异步收敛：Worker 延迟 3s | 200 立即返回；时间线稍后出现（无报错） | e2e（activity） |
| E2E-05 | 权限：移出成员后访问旧链接 | 404 | e2e（activity） |

---

## 8. 性能门禁（P95 + EXPLAIN ANALYZE）

> 对应 sprint-overview §6 验收第 8 条与各规格 §5 的性能用例；统一数据集口径，避免每章各建一套基准。六项门禁全部走 §0.2 的 bench 落点（10 万数据集灌库 + `EXPLAIN ANALYZE` 计划断言 + P95 计时），迭代 Day 5 压测日跑全量，触达索引 / 查询路径的改动重跑对应行。

### 8.1 P95 指标表

| # | 指标 | 门禁 | 数据集 / 采样 | 用例出处 | 计划断言（EXPLAIN / 计时） |
| --- | --- | --- | --- | --- | --- |
| 1 | `subtree/` 整树查询 | **P95 < 150ms** | 单项目 1 万 Issue、最深 5 层；连续 50 次 | TASK-004 IT-04 | `idx_issue_parent` 点查（防环上行 JOIN 每步索引命中，与树规模无关） |
| 2 | 依赖流转拦截检查 | **P95 < 2ms** | 1 万任务、密度 5 关联/任务；拦截检查 1000 次 | TASK-005 IT-08 | 索引点查（成对存储两行直取） |
| 3 | 工时列表 annotate | **P95 < 300ms** | 1 万任务 × 均 5 记录 | TASK-006 IT-05 | 沿用 TASK-003 门禁；`assertNumQueries` 常数级 |
| 4 | 自定义字段 5 条混合筛选 | **P95 < 200ms** | **10 万 Issue / 20 字段**；等值 + 范围 + 排序混合 | TASK-008 IT-03（G7） | `EXPLAIN ANALYZE` 走 `idx_issue_custom_fields` **bitmap AND** 命中；数字字段按数值序（9 < 10 < 100） |
| 5 | 归档视图 `?archived=true` | **P95 < 200ms** | 1 万任务、半数归档 | TASK-009 IT-06 | 反方向条件**不命中** `idx_issue_active_by_project` 偏索引，走项目复合索引扫描 + 过滤（索引分工为其 §4.1） |
| 6 | 时间线 GET 首页 | **P95 < 100ms** | 单任务 5000 条动态 | TASK-010 IT-07 | 索引排序（`issue_activities` 三索引，INFRA-003） |

### 8.2 数据集口径

| 项 | 口径 | 出处 |
| --- | --- | --- |
| 全库规模 | **10 万 Issue**（sprint-overview §6 第 8 条基准） | overview §6 |
| 单项目规模 | **1 万 Issue**（指标 1/2/3/5 的项目内数据集） | 各规格 §5.2 |
| 字段规模 | **20 个自定义字段**（指标 4；含数字 / select / date 等类型混合） | TASK-008 §5.2 IT-03 |
| 树形态 | 最深 5 层（合法上限；保险丝 100 层脏数据只在 pytest 专项构造，不进基准集） | TASK-004 §5.2 |
| 归档占比 | 半数归档（指标 5） | TASK-009 §5.2 |

> **EXPLAIN 纪律**：① 指标 4 必须 `EXPLAIN ANALYZE` 证明 GIN bitmap AND 命中（防等值 + 范围 + 排序叠加退化全表扫描——overview §9 风险 5）；② 指标 5 必须证明**不命中** active 偏索引（反方向条件走复合索引是设计预期，命中偏索引反而说明过滤方向实现错了）；③ 数字字段按数值序存储与排序（`::numeric`，非字典序，TASK-008 IT-07）；④ 筛选 DSL 单一实现，禁止各视图自建查询逻辑（dependency-graph §6）。

---

## 术语表（本文出现的关键术语）

| 术语 | 含义 |
| --- | --- |
| 三层防线 | TASK-004 §1.3：写入层深度校验（`MAX_ISSUE_DEPTH=5`，409）→ 防环 CTE（409 + 环路径）→ 查询侧保险丝（`CTE_GUARD_DEPTH=100`，命中判脏数据 500 + 告警）；两层机制不得互相替代 |
| CTE 保险丝（脏数据告警线） | `CTE_GUARD_DEPTH=100`：查询侧上行扫描链长触达即判脏数据、快速失败并 ERROR 告警；**不是业务深度限制**（overview §5 / §9 风险 1） |
| 成对存储（镜像行） | TASK-005 §1.3：一条依赖落两行，类型经 `INVERSE_MAP` 互为镜像（blocks ↔ is_blocked_by）；删除/级联均成对 |
| 项目级 advisory lock | `pg_advisory_xact_lock(项目键)`：把「查重 → 环检测 → 写入」临界区按项目串行化，关闭 READ COMMITTED 下 CTE 看不到并发未提交边的窗口（TASK-005 §4.3.2） |
| 直接子级口径 | `sub_issues_count` / `completed_sub_issues_count` 只统计直接子级且排除软删与归档（TASK-004 BR-04）；整树统计走 `subtree/` 的 `stats`（**含根口径**） |
| `event_key` 三层去重 | TASK-010 BR-07：`sha256(verb + issue_id + actor_id + epoch)`；消费端按「24h 完成标记快路径 → DB 同键查询 → Redis 处理锁」判定；占位绝不先于落库、锁占用失败不丢弃 |
| 死信（`activity.dlq`） | 重试 3 次（1s/4s/16s 退避）仍失败的任务经 DLX 路由入死信队列，元数据存 Redis hash（TTL 7 天），触发 `SERVER_QUEUE_ERROR` 告警，管理端可重放（重放经 ①② 幂等判定） |
| epoch 分组 | 同一业务动作产生的多条 Activity 共享同一 `epoch`，时间线按 epoch 聚合为一组；游标分页以 epoch 锚定不割裂 |
| 偏索引 / 索引分工 | 默认视图「排除归档」（`archived_at IS NULL`）命中 `idx_issue_active_by_project` 偏索引；归档视图 `?archived=true` 为反方向条件、不命中，走项目复合索引（TASK-009 §4.1） |
| 软删复活（全新行） | TASK-007 UT-09：执行人行删除即物理删除，重加同人是全新 INSERT，assigned_by/created_at 刷新——区别于带 `deleted_at` 条件的偏唯一索引复活（sprint-1 TC-PROJ2-010） |
| null 糖值 | `?assignee_ids=null` 编译为 `assignees__id__isnull=true` 的 URL 语法糖（TASK-007 §4.2）；`__isnull` 直写不在白名单、按 `ignored_params` 丢弃并出 `meta.warning` |
| 零 DDL / 零发版（G1/G2） | 字段增删改停全程无 `ALTER TABLE`（`django migrate` 无新迁移）；后台保存 → 刷新即生效（Redis 缓存主动失效）；可选 `CREATE INDEX CONCURRENTLY` 不违反 G1 |
| 副本后缀 | `(副本)` / `(副本 2)` 由现存 `IssueLink(duplicates)` 关联计数驱动（TASK-009 BR-01），非标题模糊匹配 |
| parity 断言 | 由附录 C 表面清单逐行生成的 e2e 字段级断言，带出处注释；不由实现反推（ADR-0010 ③） |

---

## 附录 A：本迭代新增的回归锚点

> 下列是 **Sprint 2 会改变 Sprint 0/1 既有行为**的契约点。实现合入时，除跑 `sprint-2-flow.py` 外必须**同步回跑** `sprint-0-flow.py` / `sprint-1-flow.py` 并按本表修订受影响断言——不改旧断言直接跑，会把「契约升级」误判为回归失败（或更糟：旧断言已失效却仍绿灯）。

| # | 变更点 | 规格出处 | 影响的既有断言 / 用例 | 回归处置 |
| --- | --- | --- | --- | --- |
| A1 | **`DELETE …/issues/{id}/` 成功码 204 → `200 + {deleted_count, descendant_ids}`**（级联删除必须回传受影响数；上游 TASK-001 §4.2.6 BE-69 与 TASK-002 §4.3 两处 `204` 需同步回改，否则跨文档成功码漂移） | TASK-004 §2.4 / §4.2（上游待回改登记） | sprint-1 **TC-INF4-009**（204 响应体为空是 C1 唯一例外——该用例改写为「DELETE 任务返回 200 + `deleted_count` ≥ 1」）；`sprint-0-flow.py` / `sprint-1-flow.py` 中对任务 DELETE 的状态码断言；附录 C.36 之外的 API 契约表 | 旧断言改写后回跑；`api-full-coverage.py` 契约矩阵同步该行 |
| A2 | **TASK-002 计数过滤器追加 `archived_at IS NULL`**：`sub_issues_count` / `completed_sub_issues_count` 由「排除软删 + cancelled」追加排除归档（P1 无归档能力，P2 起口径漂移） | TASK-004 BR-04 / §4.3.4（回改登记：TASK-002 §1.3 决策 2 与 §4.4.2） | sprint-1 **TC-TASK2-007**（子任务列表）、看板/列表消费计数的 C.24 子任务分区徽标与 C.28 列表行；归档子任务后徽标数字应变化 | 造数含归档子级后重跑徽标断言 |
| A3 | **一级子任务限制放开**：TASK-002 P1「父无父」单层校验改写为深度 + 防环双校验；`sub-issues/` 端点多层放开（`per_page` 上限 100 不变）；`?parent_id=` 进 TASK-003 `IssueFilterSet` 白名单（列表懒加载新入口） | TASK-004 §1.5 / §4.2 行 3 / §4.2 裁决注 | sprint-1 **TC-TASK2-007**（响应仍 200，但直接子级行可再含子级计数字段）；TASK-003 白名单相关 `meta.warning` / `ignored_params` 通道行为 | 白名单新增参数不产生 warning；旧 ignored 断言排除新参数 |
| A4 | **「负责人」单值 → 多执行人**：`IssueAssignee` 放开（≤10、去重保序、候选须 active 且 ≥CONTRIBUTOR）；`PATCH assignee_ids` 数组 + `PUT …/assignees/` 集合替换双入口；`assignee_ids=null` 未指派糖值；新增 `issue.unassigned` 事件（COLLAB-001「移除不通知」口径升级，上游待补登）；api-conventions §5.3 `__isnull` 直写示例待回改为 `null` 糖值 | TASK-007 §1.2 / §4.2 / §4.3.1 | sprint-1 看板卡片 **C.29 负责人位**与列表 **C.27**（变更为头像堆叠 / 未指派徽标，见 C.51）；`RPT-001` 我的待办（`idx_assignee_issue` 口径不变但入列条件变为集合成员）；通知去重断言（新增事件键） | C.27/C.29 相关 parity 断言按 C.51 更新；我的待办 flow 断言多人各含该任务 |
| A5 | **TASK-003 筛选白名单扩容**：新增 `parent_id`（TASK-004）、`blocked`（TASK-005）、`assignee_ids` 多值 + `null` 糖值（TASK-007）、`archived`（TASK-009） | 各规格 §1.5 上游依赖栏 + TASK-009 §4.2 行 4 | sprint-1 **TC-TASK3-001~004**（筛选语义不变，但 `meta.warning` / `ignored_params` 白名单集合变化）；URL → 结果集纯函数断言 | 白名单快照断言更新为新集合 |
| A6 | **看板卡片字段变化**：负责人位 → 头像堆叠（C.51，归 TASK-007）；归档任务退出列与列计数即时扣减（默认板查询排除 `archived_at IS NOT NULL`；「已归档」折叠入口 UI 不在 TASK-009 交付物，BOARD-002/003 待登记归属） | TASK-007 §3.3；TASK-009 §2.2 / §3.2（BR-11） | sprint-1 **TC-BOARD2-002/004**（四列齐备 / 分组计数——归档任务不再计入 `total_results` 与 `meta.total_count`）；`RPT-001` 统计四卡（UT-14 统计退出同口径） | 造数含归档任务后重跑分组计数 = 总数断言 |

---

## 附录 B：UI 表面 C.37~C.63 ↔ e2e spec 映射表

> 27 个 Sprint-2 新增/变更表面（清单正文见 [`docs/sprint-0-poc/test-cases.md`](../sprint-0-poc/test-cases.md) 附录 C 续），按归属任务分组成 7 个计划 parity spec 文件；每条断言带 `// C.x <清单行原文摘要>` 出处注释（ADR-0010 ③），条件态 / 禁用态 / 空态 / 加载态 / toast 文案逐类过。「变更 · 基线=C.xx」的表面同时保留基线行的既有断言（防升级丢字段）。

| 表面 | 名称（简） | 归属任务 | 计划 spec 文件 |
| --- | --- | --- | --- |
| C.37 | 任务列表·树形展示【变更 · 基线=C.28】 | TASK-004 §3.1 | `tests/e2e/parity-sprint2-tree.spec.ts` |
| C.38 | 行内快速加子任务 | TASK-004 §3.2 | `tests/e2e/parity-sprint2-tree.spec.ts` |
| C.39 | 拖拽移动子树 + 确认弹层 | TASK-004 §3.5 | `tests/e2e/parity-sprint2-tree.spec.ts` |
| C.40 | 全屏树抽屉 | TASK-004 §3.3 | `tests/e2e/parity-sprint2-tree.spec.ts` |
| C.41 | 详情抽屉·子任务分区升级【变更 · 基线=C.24】 | TASK-004 §3.4 | `tests/e2e/parity-sprint2-tree.spec.ts` |
| C.63 | 移动端「移动到…」弹窗 | TASK-004 §3.7 | `tests/e2e/parity-sprint2-tree.spec.ts` |
| C.42 | 详情抽屉·关联分区（新增区块） | TASK-005 §3.1 | `tests/e2e/parity-sprint2-dependency.spec.ts` |
| C.43 | 添加关联弹层 | TASK-005 §3.2 | `tests/e2e/parity-sprint2-dependency.spec.ts` |
| C.44 | 完成被拦截对话框（看板/详情双入口） | TASK-005 §3.3 | `tests/e2e/parity-sprint2-dependency.spec.ts` |
| C.45 | 列表/看板依赖可见性 | TASK-005 §3.4 | `tests/e2e/parity-sprint2-dependency.spec.ts` |
| C.46 | 详情抽屉·工时分区（新增区块） | TASK-006 §3.1 | `tests/e2e/parity-sprint2-worklog.spec.ts` |
| C.47 | 工时填报弹层 WorkLogDialog | TASK-006 §3.2 | `tests/e2e/parity-sprint2-worklog.spec.ts` |
| C.48 | 列表工时列 + 全屏树工时 tooltip | TASK-006 §3.3 | `tests/e2e/parity-sprint2-worklog.spec.ts` |
| C.49 | 详情抽屉·执行人区升级【变更 · 基线=C.23 负责人行】 | TASK-007 §3.1 | `tests/e2e/parity-sprint2-assignee.spec.ts` |
| C.50 | 转交弹层 AssigneePicker | TASK-007 §3.2 | `tests/e2e/parity-sprint2-assignee.spec.ts` |
| C.51 | 列表/看板执行人呈现 + 快速指派/认领【变更 · 基线=C.27/C.29】 | TASK-007 §3.3 | `tests/e2e/parity-sprint2-assignee.spec.ts` |
| C.52 | 字段管理页（项目设置 → 字段） | TASK-008 §3.1 | `tests/e2e/parity-sprint2-cfields.spec.ts` |
| C.53 | 新建/编辑字段弹层 | TASK-008 §3.2 | `tests/e2e/parity-sprint2-cfields.spec.ts` |
| C.54 | 删除字段三段式确认 | TASK-008 §3.3 | `tests/e2e/parity-sprint2-cfields.spec.ts` |
| C.55 | 动态表单渲染器 DynamicFieldForm | TASK-008 §3.4 | `tests/e2e/parity-sprint2-cfields.spec.ts` |
| C.56 | 动态列表列 + 列选择器 | TASK-008 §3.5 | `tests/e2e/parity-sprint2-cfields.spec.ts` |
| C.57 | 复制选项弹层 DuplicateDialog | TASK-009 §3.1 | `tests/e2e/parity-sprint2-copy-archive.spec.ts` |
| C.58 | 归档确认 + 撤销 Toast | TASK-009 §3.2 | `tests/e2e/parity-sprint2-copy-archive.spec.ts` |
| C.59 | 归档视图（列表模式） | TASK-009 §3.2 | `tests/e2e/parity-sprint2-copy-archive.spec.ts` |
| C.60 | 归档详情只读横幅（与 TASK-004 §3.4 交叉） | TASK-009 §3.2 + TASK-004 §3.4 | `tests/e2e/parity-sprint2-copy-archive.spec.ts` |
| C.61 | 详情抽屉·动态 Tab 时间线升级【变更 · 基线=C.25】 | TASK-010 §3.1（R1/R4 裁决） | `tests/e2e/parity-sprint2-activity.spec.ts` |
| C.62 | 管理端死信补偿页（admin） | TASK-010 §3.2（布局 O2） | `tests/e2e/parity-sprint2-activity.spec.ts` |

> 交叉归属说明：C.40 节点行工时 tooltip 与 C.48 联动（断言归 worklog 文件，tree 文件仅断言 tooltip 挂点存在）；C.60 子任务只读行的口径与 C.41 归档行一致（两文件各断言各自侧，勿重复造数）。`interactions.spec.ts`（sprint-1 既有）扩展承接拖拽移动 / 认领交错等重交互动线，与 parity 文件分工沿用 sprint-1 模式。
