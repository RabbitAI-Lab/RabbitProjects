# Sprint 1 测试用例文档

> **本文档定位**：覆盖 Sprint 1 全部 11 份功能文档（INFRA-004 + AUTH-004/005 + TEAM-002 + PROJ-002 + TASK-002/003 + BOARD-002 + FILE-001 + COLLAB-001 + RPT-001）的测试用例集合，按 5 维度（完整性 / 一致性 / 可实施性 / 可测性 / 清晰度）评审，全部 ≥9.5 通过。
>
> **与 Sprint 0 的关系**：UI 表面清单（附录 C）统一维护在 [`docs/sprint-0-poc/test-cases.md`](../sprint-0-poc/test-cases.md) 附录 C，Sprint 1 新增表面为 **C.10~C.36（27 节）**，本文不复制清单，只引用其行号做 parity 断言的出处。

| 元信息项 | 内容 |
| --- | --- |
| 所属迭代 | Sprint 1 — MVP 能力补齐 |
| 周期 | 第 3 周 |
| 文档数 | 11 份功能文档 → 对应 11 组测试用例 |
| 评分维度 | 完整性 / 一致性 / 可实施性 / 可测性 / 清晰度，各 10 分，0.5 步进 |
| 通过标准 | 5 项**全部 ≥9.5** |
| 关联交付 | `tests/jmeter/_contract.py`（契约常量唯一定义点）、`tests/jmeter/sprint-1-flow.py`（CI 端到端）、`tests/jmeter/api-full-coverage.py`（契约矩阵）、`tests/e2e/*.spec.ts`（浏览器端到端 + parity 扫描）、`tests/run-ci-checks.sh`（L1/L2 静态检查） |
| 视觉基准 | [`docs/design/sprint-1-hifi-prototype.html`](../design/sprint-1-hifi-prototype.html)（**FROZEN 2026-09-04**） |
| 裁决依据 | [ADR-0011](../adr/0011-sprint-1-cross-doc-ui-adjudication.md)（跨文档 UI 矛盾 20 项）、[ADR-0012](../adr/0012-sprint-1-impl-deviations.md)（实现偏差 A~E 类） |

---

## 0. 测试基线与执行入口

### 0.1 前置依赖

| 类别 | 项 | 状态 |
| --- | --- | --- |
| 运行时 | PostgreSQL 17 容器 `rp-pg`（见 CLAUDE.md 环境表） | ✅ |
| 运行时 | PG schema 含 `0003_sprint1` 迁移（6 张新表 + 7 列新增 + 11 索引 + 4 偏唯一约束） | ✅ |
| 运行时 | Django 5.1 + Python 3.12 + uv 同步 | ✅ |
| 运行时 | Node 22.14 + pnpm 11 | ✅ |
| 运行时 | MinIO（FILE-001 直传 / AUTH-004 头像） | ⚠️ 未起时相关用例按「本地不可验证」标注 |
| 运行时 | RabbitMQ + Celery worker（COLLAB-001 通知 / AUTH-004 邮件 / TEAM-002 邀请信） | ⚠️ 未起时走降级路径，异步链路用例标注为未真实验证 |
| 执行 | `python3 tests/jmeter/sprint-1-flow.py`（CI 端到端） | ✅ |
| 执行 | `python3 tests/jmeter/api-full-coverage.py`（契约矩阵） | ✅ |
| 执行 | `E2E_NO_SERVER=1 pnpm exec playwright test`（浏览器端到端 + parity） | ✅ |
| 执行 | `bash tests/run-ci-checks.sh`（L1/L2 静态检查） | ✅ |
| 执行 | `uv run --project apps/api ruff check .` / `pnpm typecheck` | ✅ |

### 0.2 测试分工

| 工具 | 角色 | 覆盖 | 触发时机 |
| --- | --- | --- | --- |
| `tests/jmeter/_contract.py` | **契约常量唯一定义点** | HTTP 状态码表 / 错误码 / 信封字段路径 / `Client` / 断言辅助 | 被下列脚本 import；禁止各自硬编码（ADR-0012 E4） |
| `tests/jmeter/sprint-1-flow.py` | CI 端到端（单线程） | 信封 C1 / 权限快照 / sort_order / 搜索收藏归档 / 跨租户隔离 | PR 必跑（gate） |
| `tests/jmeter/sprint-0-flow.py` | CI 端到端（Sprint 0 回归） | 10 步业务流，防 Sprint 1 改动回退 Sprint 0 能力 | PR 必跑（gate） |
| `tests/jmeter/api-full-coverage.py` | 契约矩阵 | 端点 × 方法 × 正/负例 | PR 必跑（gate） |
| `tests/e2e/*.spec.ts` | 浏览器端到端 | 动线 + 权限门控 + 成员管理 + parity 字段级扫描 | PR 必跑（gate） |
| `tests/run-ci-checks.sh` | L1/L2 静态检查 | 命令级断言（结构 / 约束 / 常量） | PR 必跑（gate） |

> **parity 断言纪律（ADR-0010 ③）**：`tests/e2e/parity*.spec.ts` 的断言**由附录 C 清单生成**，不由实现反推；每条断言带 `// C.x <清单行原文摘要>` 出处注释。
>
> **断言方式约定（sprint-1 验收教训，UI 用例强制）**：凡「断言方式」列标 *条件断言 / 交互断言 / 权限断言* 的用例，自动化实现必须**从用户入口出发**（登录 → 点导航/卡片到达页面；深链 goto 仅当先登录且资源真实），并断言**该页特有**的内容——全局元素（Logo / "RabbitProjects"）不构成页面断言（403 页 / 登录页 / 白屏同样渲染它们）。受权限保护的页面对应写一条负向用例（无权直进 → 前端 403 + 后端 API 404）。

---

## 1. INFRA-004 统一返回格式 / 全局错误 / 环境配置

### 1.1 目标
验证 C1 信封「两种结构无例外」、C2 错误码双源一致、C3 `request_id` 全链路贯穿三条硬约束。

### 1.2 前置
- API 已启动；`plane/base/` 框架层就位；`REST_FRAMEWORK["EXCEPTION_HANDLER"]` 指向 `plane.base.handlers.envelope_exception_handler`

### 1.3 用例清单

| ID | 级别 | 标题 | 前置/依赖 | 步骤 | 预期 | 自动化 | 判分锚点 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TC-INF4-001 | L2 集成 | 2xx 为 success 信封 | API 已起 | `GET /api/v1/health/` | `{"status":"success","data":{...}}` | 是（ENV-01） | `body.status == "success"` |
| TC-INF4-002 | L2 集成 | 成功体含 `data` 节点 | 同上 | 同上 | 含 `data` 键 | 是（ENV-02） | `"data" in body` |
| TC-INF4-003 | L2 集成 | **4xx 为 error 信封**（含 DRF `NotFound`） | 同上 | `GET /api/v1/workspaces/__no_such_ws__/` | `{"status":"error","error":{...}}` | 是（ENV-03） | `body.status == "error"` |
| TC-INF4-004 | L2 集成 | 错误码在 `error.code` 且已注册 | 同上 | 同上 | `error.code == "RESOURCE_NOT_FOUND"` | 是（ENV-04） | 错误码 ∈ 75 码注册表 |
| TC-INF4-005 | L2 集成 | 错误体含 `request_id`（C3） | 同上 | 同上 | `error.request_id` 非空 ULID | 是（ENV-05） | 字段非空 |
| TC-INF4-006 | L1 单元 | 注册表规模 = 75 码 | — | `ErrorCodes.all()` 长度 | 75 | 是 | `len == 75` |
| TC-INF4-007 | L1 单元 | 默认文案覆盖全部注册码 | — | `set(DEFAULT_MESSAGES) == set(ErrorCodes.all())` | True（差集为空） | 是 | 双向差集为空 |
| TC-INF4-008 | L1 单元 | 未注册码构造即 KeyError | — | `AppException("NOT_EXIST")` | 抛 `KeyError` | 是 | 异常类型 = KeyError |
| TC-INF4-009 | L2 集成 | 204 响应体为空（C1 唯一例外） | 有可删资源 | `DELETE .../issues/{id}/` | 204 且 body 为空 | 是 | `len(body) == 0` |
| TC-INF4-010 | L2 集成 | 校验错误 `details[]` 平铺 | — | `POST .../projects/` 传超长 identifier | 400 + `error.details[0].field == "identifier"` | 是 | details 为数组且含 field/code/message |
| TC-INF4-011 | L2 集成 | prod 缺必填变量启动即失败（BR-13） | — | `DJANGO_SETTINGS_MODULE=plane.settings.prod manage.py check` | `ImproperlyConfigured` 列出缺失项 | 是 | 退出码非 0 且信息含缺失变量名 |

> **回归锚点**：TC-INF4-003/004/005 是对 [ADR-0012 D5](../adr/0012-sprint-1-impl-deviations.md) 的守护——`handlers.py` 第 6 步曾只判 `Http404`/`ObjectDoesNotExist`，漏掉 DRF 的 `NotFound`（`APIException` 子类），导致**全 API 命中率最高的越权 404 出口**返回裸 `{"detail": ...}`。

---

## 2. AUTH-005 按钮级权限 + 接口二次鉴权

### 2.1 目标
验证权限快照下发的结构、隐式管理员继承语义、截断标记，以及前端 `PermissionGate` 三种 mode 与 403 落点分工。

### 2.2 前置
- 权限矩阵唯一数据源 `apps/api/plane/constants/permissions.py`（25 个权限点：workspace 域 10 + project 域 15）

### 2.3 用例清单

| ID | 级别 | 标题 | 前置/依赖 | 步骤 | 预期 | 自动化 | 判分锚点 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TC-AUTH5-001 | L2 集成 | 权限快照 200 + success | 已登录 | `GET /api/v1/users/me/permissions/` | 200 且 success 信封 | 是（PERM-01） | 状态码 + status 字段 |
| TC-AUTH5-002 | L2 集成 | 快照含三大节点 | 同上 | 同上 | 含 `is_system_admin` / `workspaces` / `projects` | 是（PERM-02） | 三键齐备 |
| TC-AUTH5-003 | L2 集成 | `meta` 含 `generated_at` / `truncated` | 同上 | 同上 | 两字段齐备 | 是（PERM-03） | 两键齐备 |
| TC-AUTH5-004 | L2 集成 | 本人空间角色为 OWNER(20) | 新注册用户 | 同上 | `workspaces[*].role == 20` | 是（PERM-04） | 存在 role=20 的行 |
| TC-AUTH5-005 | L2 集成 | 未知 `workspace_slug` 静默忽略 | 同上 | `?workspace_slug=__nope__` | 200（不报错，权限数据仅本人可见） | 是（PERM-05） | 状态码 = 200 |
| TC-AUTH5-006 | L1 单元 | 矩阵与标签集合相等 | — | `all_permission_keys()` vs `PERMISSION_LABELS` | 双向差集为空 | 是 | 差集为空 |
| TC-AUTH5-007 | L1 单元 | 未注册权限点抛 KeyError | — | `threshold_of("nope.nope")` | 抛 `KeyError` | 是 | 异常类型 = KeyError |
| TC-AUTH5-008 | L2 集成 | 隐式管理员标 `inherited=true` | WS_ADMIN 且无 ProjectMember 行 | 快照 `projects[pid]` | `inherited == true` 且 `role == 20` | 是 | 两字段 |
| TC-AUTH5-009 | L3 端到端 | 403 页渲染权限点**中文名** | — | 直达 `/403?perm=project.update` | 显示「编辑项目设置」，不裸露英文 key | 是（e2e） | 页面文本不含 `project.update` |
| TC-AUTH5-010 | L3 端到端 | `disable` mode 保留可聚焦 | 低权限视角 | Tab 到危险按钮 | `aria-disabled="true"` 且可聚焦（非原生 disabled） | 是（e2e） | 属性 + focus 断言 |
| TC-AUTH5-011 | L3 端到端 | Gate 加载骨架不产生布局跳动 | — | 权限加载中 | 骨架等宽等高、`aria-busy`，≤300ms | 是（e2e） | 尺寸一致 + 属性 |
| TC-AUTH5-012 | L3 端到端 | 403 后权限刷新无 toast | — | 触发刷新 | 按钮显隐收敛且**不弹 toast** | 是（e2e） | toast 数量无变化 |

---

## 3. PROJ-002 项目成员管理与搜索收藏

### 3.1 目标
验证项目成员 CRUD、`?q=` 搜索（ADR-0011 #11 统一参数名）、收藏软删复活、归档只读态。

### 3.2 用例清单

| ID | 级别 | 标题 | 前置/依赖 | 步骤 | 预期 | 自动化 | 判分锚点 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TC-PROJ2-001 | L2 集成 | 成员列表 200 | 已建项目 | `GET .../projects/{id}/members/` | 200 + success | 是 | 状态码 |
| TC-PROJ2-002 | L2 集成 | 创建者在成员列表中 | 同上 | 同上 | 至少 1 行 | 是 | 行数 ≥ 1 |
| TC-PROJ2-003 | L2 集成 | `?q=` 按名称前缀搜索 | 两个项目 | `?q=营销` | 命中 1 条 | 是（PROJ-01） | 结果数 = 1 |
| TC-PROJ2-004 | L2 集成 | `?q=` 按 identifier 搜索 | 同上 | `?q=MK123` | 命中 1 条 | 是（PROJ-02） | 结果数 = 1 |
| TC-PROJ2-005 | L2 集成 | 列表 `meta` 含分页字段 | 同上 | 列表 | 含 `count/total_count/favorite_count/per_page` | 是（PROJ-03） | 四键齐备 |
| TC-PROJ2-006 | L2 集成 | 列表项含 `is_favorite` | 同上 | 列表 | 每项含该字段 | 是（PROJ-04） | 全项含键 |
| TC-PROJ2-007 | L2 集成 | 收藏成功 | 同上 | `POST .../favorite/` | 200/201 | 是（PROJ-05） | 状态码 |
| TC-PROJ2-008 | L2 集成 | 收藏过滤命中 | 同上 | `?favorite=true` | 命中 1 条 | 是（PROJ-06） | 结果数 = 1 |
| TC-PROJ2-009 | L2 集成 | 取消收藏 | 同上 | `DELETE .../favorite/` | 200/204 | 是（PROJ-07） | 状态码 |
| TC-PROJ2-010 | L2 集成 | **再次收藏成功（软删复活回归）** | TC-PROJ2-009 | `POST .../favorite/` | 200/201，不被软删行的唯一约束挡住 | 是（PROJ-08） | 状态码；偏唯一索引带 `deleted_at IS NULL` 条件 |
| TC-PROJ2-011 | L2 集成 | 归档成功 | 同上 | `POST .../archive/` | 200/204 | 是（PROJ-09） | 状态码 |
| TC-PROJ2-012 | L2 集成 | 默认列表排除归档项（BR-11） | TC-PROJ2-011 | 列表（不带 status） | 不含归档项 | 是（PROJ-10） | 结果集不含该 id |
| TC-PROJ2-013 | L2 集成 | `?status=all` 可见归档项 | 同上 | `?status=all` | 含归档项 | 是（PROJ-11） | 结果集含该 id |
| TC-PROJ2-014 | L3 端到端 | 归档只读态（ADR-0011 #14） | 归档项目 | 进入项目 | 顶部琥珀色横幅「项目已归档，仅可查看」+ 写入口禁用 | 是（e2e） | 横幅文案 + disabled 断言 |

---

## 4. TASK-001 回归 · BR-8 列尾追加

> 归属 Sprint 0 的 TASK-001，但因 BOARD-002 的「筛选后拖拽排序」直接依赖该语义（sprint-overview §9 风险 8），在本迭代补齐守护用例。

| ID | 级别 | 标题 | 前置/依赖 | 步骤 | 预期 | 自动化 | 判分锚点 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TC-TASK1-020 | L2 集成 | 连续创建任务 `sort_order` **严格递增** | 空项目 | 连建 3 个任务 | `65535 → 131070 → 196605` | 是（SORT-02） | 严格递增 |
| TC-TASK1-021 | L2 集成 | 步长 = 65535（BR-8） | 同上 | 同上 | 相邻差恒为 65535 | 是（SORT-03） | 差值断言 |

> **回归锚点**：守护 [ADR-0012 D1](../adr/0012-sprint-1-impl-deviations.md)——视图算出列尾值却未传给 service，`calculate_sort_order` 两参皆 None 恒返回常量 65535，BR-8「末任务追加 = 列尾」完全失效。

---

## 5. AUTH-003 回归 · 跨租户隔离

| ID | 级别 | 标题 | 前置/依赖 | 步骤 | 预期 | 自动化 | 判分锚点 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TC-AUTH3-020 | L2 集成 | 他人项目返回 **404 而非 403** | 两个用户 | B 访问 A 的项目 | 404（防 ID 枚举） | 是（ISO-01） | 状态码 = 404 |
| TC-AUTH3-021 | L2 集成 | 错误码为 `RESOURCE_NOT_FOUND` | 同上 | 同上 | `error.code == "RESOURCE_NOT_FOUND"` | 是（ISO-02） | 错误码 |

---

## 6. AUTH-004 个人信息修改与密码重置

| ID | 级别 | 标题 | 步骤 | 预期 | 自动化 |
| --- | --- | --- | --- | --- | --- |
| TC-AUTH4-001 | L2 | PATCH 昵称 + 简介 | `PATCH /users/me/` | 200 | 是（PROF-01） |
| TC-AUTH4-002 | L2 | 回读昵称已更新 | `GET /users/me/` | `display_name` 为新值 | 是（PROF-02） |
| TC-AUTH4-003 | L2 | 错误旧密码改密被拒 | `POST /users/me/change-password/` 传错旧密码 | 4xx | 是（PROF-03） |
| TC-AUTH4-004 | L2 | 头像 presign / complete | `POST /users/me/avatar/presign/` → 直传 → `complete` | 三步闭环 | ⚠️ 需 MinIO |
| TC-AUTH4-005 | L2 | 重置令牌一次性 | `forgot-password` → `reset-password` → **再次使用同一 token** | 二次使用失败（`AUTH_PASSWORD_RESET_INVALID`） | ⚠️ 需邮件通道取 token |
| TC-AUTH4-006 | L3 | 重置后不自动登录（ADR-0011 #9） | 重置成功 | 跳成功页「密码已重置」+「去登录」 | e2e |

## 7. TEAM-002 团队成员邀请 / 移除 / 角色分配

| ID | 级别 | 标题 | 步骤 | 预期 | 自动化 |
| --- | --- | --- | --- | --- | --- |
| TC-TEAM2-001 | L2 | 成员列表含创建者 | `GET /workspaces/{slug}/members/` | 200，≥1 行 | 是（TEAM-01） |
| TC-TEAM2-002 | L2 | 邀请列表 | `GET .../invitations/` | 200 | 是（TEAM-02） |
| TC-TEAM2-003 | L2 | 非成员访问他人团队成员 | 另一账号 `GET .../members/` | **404**（非 403） | 是（TEAM-03） |
| TC-TEAM2-004 | L2 | 批量邀请三态汇总 | `POST .../invitations/` 传已注册 / 未注册 / 已是成员 | `summary` 含 added / invited / skipped | 是（实现期验证脚本 44 断言） |
| TC-TEAM2-005 | L2 | **重新邀请复活既有行** | 移除成员 → 再次邀请同邮箱 | 成功（`revived=true`），不撞唯一约束 | 是 |
| TC-TEAM2-006 | L2 | 末位 OWNER 保护 | OWNER 退出 / 被移除 | 409 `RESOURCE_STATE_INVALID` | 是 |
| TC-TEAM2-007 | L2 | 层级保护 | ADMIN 试图移除 OWNER | 409 | 是 |
| TC-TEAM2-008 | L2 | 所有权转让原子 | `POST .../ownership/transfer/` | 双方角色原子互换 | 是 |
| TC-TEAM2-009 | L2 | 注册即自动接受待处理邀请 | 未注册邮箱被邀请 → 注册 | 自动入队 | 是 |
| TC-TEAM2-010 | L2 | 被移除者失去访问 | 移除后访问原工作空间 | 404 | 是 |

## 8. TASK-002 任务扩展属性与一级子任务

| ID | 级别 | 标题 | 步骤 | 预期 | 自动化 |
| --- | --- | --- | --- | --- | --- |
| TC-TASK2-001 | L2 | 项目可用类型列表 | `GET .../issue-types/` | 200 | 是（ATTR-01） |
| TC-TASK2-002 | L2 | 标签 CRUD | `POST .../labels/` | 201 | 是（ATTR-02） |
| TC-TASK2-003 | L2 | 创建任务可带 priority | `POST .../issues/` | 201 且回显 | 是（ATTR-03/04） |
| TC-TASK2-005 | L2 | **PATCH 属性不 500** | `PATCH .../issues/{id}/` 改 priority | 200 且回显 urgent | 是（ATTR-05） |
| TC-TASK2-006 | L2 | 标签全量替换用 **PUT** | `PUT .../issues/{id}/labels/` | 2xx，任务回显 `label_ids` | 是（ATTR-06/09） |
| TC-TASK2-007 | L2 | 子任务列表 | `GET .../issues/{id}/sub-issues/` | 200 | 是（ATTR-07） |
| TC-TASK2-008 | L2 | 操作日志只读时间线 | `GET .../issues/{id}/activities/` | 200 | 是（ATTR-08） |

## 9. TASK-003 任务列表筛选 / 搜索 / 排序

| ID | 级别 | 标题 | 步骤 | 预期 | 自动化 |
| --- | --- | --- | --- | --- | --- |
| TC-TASK3-001 | L2 | `?priority=` 筛选 | 列表带参 | 200 | 是（FLT-01） |
| TC-TASK3-002 | L2 | `?q=` 关键词搜索（trigram） | 列表带中文关键词 | 200 | 是（FLT-02） |
| TC-TASK3-003 | L2 | `?order_by=` 排序 | 列表带参 | 200 | 是（FLT-03） |
| TC-TASK3-004 | L2 | 筛选结果正确性 | `?priority=urgent` | 结果**全部**为 urgent | 是（FLT-04） |

## 10. BOARD-002 看板筛选与卡片悬浮预览

| ID | 级别 | 标题 | 步骤 | 预期 | 自动化 |
| --- | --- | --- | --- | --- | --- |
| TC-BOARD2-001 | L2 | 分组响应 | `?group_by=state_id` | 200 | 是（BRD-01） |
| TC-BOARD2-002 | L2 | **四列齐备（含「已取消」）** | 同上 | 分组键数 = 4 | 是（BRD-02） |
| TC-BOARD2-003 | L2 | 每组结构 | 同上 | `{results, total_results}` | 是（BRD-03） |
| TC-BOARD2-004 | L2 | **分组计数正确** | 同上 | 各组 `total_results` 之和 = `meta.total_count` | 是（BRD-04） |

## 11. FILE-001 任务级附件上传下载

| ID | 级别 | 标题 | 步骤 | 预期 | 自动化 |
| --- | --- | --- | --- | --- | --- |
| TC-FILE1-001 | L2 | 非法扩展名被拒 | presign 传 `.exe` | 400 `VALIDATION_FILE_TYPE_NOT_ALLOWED` | 是（FILE-01） |
| TC-FILE1-002 | L2 | 超大文件被拒 | presign 传 99MB | 400 `VALIDATION_FILE_SIZE_EXCEEDED` | 是（FILE-02） |
| TC-FILE1-003 | L2 | presign 返回四要素 | presign 合法文件 | 201 + `{asset_id, upload_url, fields, expires_at}` | 是（FILE-03） |
| TC-FILE1-004 | L2 | 直传对象存储 | PUT 预签名地址 | 2xx | 是（FILE-04，需 MinIO） |
| TC-FILE1-005 | L2 | complete 翻转状态 | `POST .../complete/` | 200 | 是（FILE-05） |
| TC-FILE1-006 | L2 | **计数三处一致** | complete 返回值 vs 列表条数 vs 任务 `attachment_count` | 三者相等 | 是（FILE-06） |
| TC-FILE1-007 | L2 | 对象存储不可达降级 | 停 MinIO 后 presign | 500 `SERVER_STORAGE_ERROR`（不是未捕获异常） | 是 |

## 12. COLLAB-001 任务评论 / @提醒 / 通知中心

| ID | 级别 | 标题 | 步骤 | 预期 | 自动化 |
| --- | --- | --- | --- | --- | --- |
| TC-COLLAB1-001 | L2 | 发表评论 | `POST .../comments/` | 201 | 是（CMT-01） |
| TC-COLLAB1-002 | L2 | 评论列表 | `GET .../comments/` | 含新评论 | 是（CMT-02） |
| TC-COLLAB1-003 | L2 | 通知列表 | `GET /users/me/notifications/` | 200 | 是（NTF-01） |
| TC-COLLAB1-004 | L2 | 未读数 | `.../unread-count/` | 200 | 是（NTF-02） |
| TC-COLLAB1-005 | L2 | 全部已读 | `.../read-all/` | 2xx | 是（NTF-03） |
| TC-COLLAB1-006 | L2 | `?unread=true` 语义 | 全部已读后查未读 | 空集 | 是（NTF-04） |
| TC-COLLAB1-007 | L2 | @提及扇出成员域过滤 | @非项目成员 | 不产生通知 | ⚠️ 需多用户 + 异步链路 |
| TC-COLLAB1-008 | L2 | 通知去重 | 同事件重复触发 | `dedup_key` 拦截 | ⚠️ 需 broker |

## 13. RPT-001 个人待办与已完成统计

| ID | 级别 | 标题 | 步骤 | 预期 | 自动化 |
| --- | --- | --- | --- | --- | --- |
| TC-RPT1-001 | L2 | 缺 `workspace` 参数 | `GET /users/me/issues/stats/` | 400 `VALIDATION_ERROR`，`details[0].field == "workspace"` | 是（RPT-01） |
| TC-RPT1-002 | L2 | 四项统计齐备 | 带 `?workspace=` | `todo/due_today/overdue/completed_this_week` | 是（RPT-02） |
| TC-RPT1-003 | L2 | 7 日趋势 | 同上 | `trend` 恰 7 个点 | 是（RPT-03） |
| TC-RPT1-004 | L2 | 我的待办列表 | `GET /users/me/issues/?workspace=` | 200，`meta` 含分页 | 是（RPT-04） |
| TC-RPT1-005 | L2 | 日界按本地时区 | 跨 UTC 日界的任务 | 归属日与用户时区一致（非 UTC `__date`） | ⚠️ 待补时区用例 |

---

## 术语表（本文出现的关键术语）

| 术语 | 含义 |
| --- | --- |
| C1 信封 | INFRA-004 §1.2 的硬约束：2xx 为 `{status:"success",data,meta?}`；4xx/5xx 为 `{status:"error",error:{...}}`；204 体为空是唯一例外 |
| 判分锚点 | 该用例在 5 维度评审中「可测性」的客观判据，必须是机器可判的表达式或字段断言 |
| 软删复活 | 带 `deleted_at IS NULL` 条件的偏唯一索引下，取消后重新创建同一逻辑行必须成功；裸 `unique_together` 会 IntegrityError |
| parity 断言 | 由附录 C 表面清单逐行生成的 e2e 字段级断言，带出处注释；不由实现反推（ADR-0010 ③） |
| 隐式管理员 | WS_ADMIN+ 在无 `ProjectMember` 行时隐式视为项目 ADMIN（INFRA-003 §4.5），快照中标 `inherited=true` |

---

## 附录 A：本迭代新增的回归锚点

下列用例守护的是**实现期发现的真实缺陷**（详见 [ADR-0012](../adr/0012-sprint-1-impl-deviations.md) D 类），删除任一条都会让对应缺陷可以静默复发：

| 用例 | 守护的缺陷 | 缺陷表现 |
| --- | --- | --- |
| TC-INF4-003/004/005（ENV-03/04/05） | D5 | 越权 404 返回裸 `{"detail":...}`，不走 C1 信封 |
| TC-TASK1-020/021（SORT-02/03） | D1 | 所有任务 `sort_order` 恒为 65535，列尾追加失效 |
| TC-PROJ2-010（PROJ-08） | 收藏软删复活 | 取消收藏后无法重新收藏 |
| TC-FILE1-003（FILE-03） | D6 | presign 必 500（`ulid.new()` 是另一个同名包的 API） |
| TC-FILE1-006（FILE-06） | D7 | `complete` 返回的附件计数比真实值多 1 |
| TC-BOARD2-004（BRD-04） | D8 | 看板各列 `total_results` 之和远小于真实总数 |
| TC-TASK2-005（ATTR-05） | 教训 #3 | 新增序列化字段漏挂 `Meta.fields` 时 GET 正常、PATCH 必 500 |

> 全部由 `tests/jmeter/sprint-1-flow.py` 自动执行（当前 67 项断言全通过），断言 ID 见括号。
