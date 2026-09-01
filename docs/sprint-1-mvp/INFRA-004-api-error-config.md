# 统一返回格式 / 全局错误 / 环境配置

| 元信息项 | 内容 |
| --- | --- |
| 文档编号 | INFRA-004 |
| 所属迭代 | Sprint 1：MVP 能力补齐（第 3 周） |
| 优先级 | P1（MVP 必备级 · **本迭代全部功能文档的公共工程底座**） |
| 所属模块 | M9-INFRA 基础设施与部署运维 |
| 文档状态 | 待评审（Draft） |
| 最后更新日期 | 2026-09-01 |
| 上游依据 | `docs/需求文档.md` §8.2 部署运维 P1 列（统一接口返回格式、全局错误捕获、环境变量配置、基础运行日志）、§五核心技术约束（统一返回格式 + 全局异常捕获） |
| 前置依赖 | `INFRA-001`（工程骨架与 `packages/types` 生成链路）、`INFRA-002`（Docker Compose 全套服务与 Nginx `apps/proxy`）、`INFRA-003`（Django App 划分与 settings 骨架） |
| 下游依赖 | Sprint 1 全部 10 份功能文档；Sprint 2-9 所有新增端点默认继承本文档的异常处理、日志与配置框架；`INFRA-005`（P2 限流复用 `RateLimitHeaderMiddleware` 与 `error_code` 日志维度） |
| 架构基线 | [`api-conventions.md`](../architecture/api-conventions.md) §4（响应格式）、§8（错误码体系全表）、§8.8（字段级子码）、§8.9（前端消费范式）、§10.4（全局异常处理与中间件顺序）、§13.4（CORS 与安全响应头）、§13.5（日志与可观测性）；[`monorepo-structure.md`](../architecture/monorepo-structure.md) §9（环境变量层级与命名前缀）、§8.2（`gen-api-types` 链路） |
| 竞品参考 | Plane（`apps/api/plane/settings/common.py` 环境变量直读 + DRF `EXCEPTION_HANDLER` + `base/exception.py` 的 `AppException` 基类）、Ones（企业级配置中心、分级日志采集与可审计运维） |
| 工作量估算 | 后端 2.5 人日 / 前端 1.5 人日 / 联调与测试 1 人日，合计 **5 人日** |

> **范围声明**：本文档交付「后端响应 / 异常 / 日志 / 配置」四件套的统一实现。接口限流（`INFRA-005`，P2）、Sentry 接入与错误聚合（P2）、全站安全审计日志（`AUTH-010`，P3）、多租户配置中心（P4）不在本文档范围。P0 阶段各文档已按信封格式**逐端点手工遵守规范**，本文档将其**收口为框架级默认行为**——此后业务代码只抛 `AppException`，不写任何响应包装与日志样板。

---

## 1. 概述

### 1.1 功能定位

Sprint 0 的端点是「逐个手工遵守规范」，存在四个工程隐患：

| # | 隐患 | P0 现状的具体表现 | 后果（若不收口） |
| --- | --- | --- | --- |
| 1 | DRF 默认格式漏出 | DRF 自动抛出的 404 / 403 / 405 / 429 仍是 `{"detail": "..."}` 原生结构 | 前端对同一资源要写两套解析逻辑；错误处理出现「漏网之鱼」 |
| 2 | 错误码散落 | 错误码字符串硬编码在各 ViewSet / Permission 内，无注册表 | 前端无法穷举分支；文案改版即静默破坏前端逻辑 |
| 3 | 日志不可追踪 | 默认 Django 文本日志，无请求 ID | 多人协作排障时无法把一次用户报障定位到具体请求与堆栈 |
| 4 | 配置无校验 | settings 直读 `os.environ`，漏配在首次使用时才爆 | 生产「带病启动」，故障点漂移到随机位置 |

INFRA-004 把这四件事一次收口为**框架级默认行为**：

| 交付项 | 说明 |
| --- | --- |
| 全局异常处理器 | `EXCEPTION_HANDLER` 统一改写全部 4xx/5xx（含 DRF 默认、`PermissionDenied`、`Http404`、`IntegrityError`、未捕获 `Exception`）为 `{status, error}` 信封，处理顺序对齐 [`api-conventions.md`](../architecture/api-conventions.md) §10.4 的十步收敛 |
| 错误码注册表 | `ErrorCodes` 注册表与架构文档 §8 全表**一一对应**；业务代码只能抛 `AppException(code=...)`，未注册码在测试期即 `KeyError` 暴露 |
| 六件套中间件 | `RequestIDMiddleware → StructuredLoggingMiddleware → RateLimitHeaderMiddleware → AuditContextMiddleware → ResponseEnvelopeMiddleware → MaintenanceModeMiddleware`，顺序与职责对齐 §10.4 配套中间件表 |
| 结构化运行日志 | structlog JSON 行格式 + ULID 请求追踪 ID + 按模块分 channel + 慢请求 / 查询数 / 脱敏三条可观测规则（§13.5） |
| 环境配置体系 | `settings/{__init__,base,dev,prod}.py` 四文件分层 + `.env.example` 单一事实来源 + prod 9 项必填项启动即校验（快速失败） |
| 前端消费闭环 | axios 错误拦截器按 `error.code` 分派（§8.9 范围的 P1 子集）、`ErrorToast`、`useApiFieldErrors`、404/403/500 空态页 |

### 1.2 三大关键约定（本文档最重要的技术决策）

> ⚠️ **这三条是硬约束，CI 守护，违反即构建失败。**

| # | 约定 | 含义 | 守护机制 |
| --- | --- | --- | --- |
| **C1** | **信封两种结构，无例外** | 所有 `2xx`（除 `204` 空体）为 `{status:"success", data, meta}`；所有 `4xx/5xx` 为 `{status:"error", error:{code, message, details?, request_id, doc_url?}}`。**不存在任何第三种结构**，包括 Nginx 网关层产生的 413/502/503/504 | IT-01 全路由快照测试逐路由断言；`ResponseEnvelopeMiddleware` 兜底（开发态直接抛错，见 §4.6） |
| **C2** | **错误码双源一致** | 错误码唯一事实来源是 [`api-conventions.md`](../architecture/api-conventions.md) §8；后端 `ErrorCodes` 注册表、前端手写 `ErrorCode` 常量、OpenAPI `ErrorEnvelope` 影子枚举由脚本校验与文档四方集合一致（详见 §4.11.1 / §4.11.3） | UT-01 解析 Markdown 错误码表 vs 注册表集合断言相等；`scripts/check-error-codes.mjs` 在 CI 对四组集合两两 diff（§4.11.3） |
| **C3** | **`request_id` 全链路贯穿** | ULID 生成于最外层中间件，同时出现在：响应头 `X-Request-Id`、错误体 `error.request_id`、access 日志、error 日志（含堆栈）。用户报障提供此 ID 即可检索到该请求的全部日志 | UT-05 单元断言 + IT-03 检索演练 |

**与 `204` 的关系澄清**（对齐 §4.3 状态码表）：`204 No Content` 的响应体**必须为空**，不包装 `{status:"success"}`——这是 C1 的显式例外而非漏洞。`ResponseEnvelopeMiddleware` 对 204 放行不包装。

### 1.3 交付物与代码落位总览

```
apps/api/plane/
├── base/                          # ★ 本文档新增的框架层
│   ├── __init__.py
│   ├── error_codes.py             # 错误码注册表（§8 全表 75 码）
│   ├── exception.py               # AppException 基类 + 默认文案
│   ├── handlers.py                # envelope_exception_handler（十步收敛）
│   ├── response.py                # success_response / 分页 meta 装配
│   └── middleware.py              # 六件套中间件
├── settings/
│   ├── __init__.py                # 按 DJANGO_SETTINGS_MODULE 分发
│   ├── base.py                    # 公共配置（全量）
│   ├── dev.py                     # DEBUG=True + 本地容器默认值 + CORS 宽松
│   └── prod.py                    # DEBUG=False + 安全头 + 9 项必填启动校验
└── logging.py                     # structlog 配置（进程入口调用）

apps/proxy/conf.d/api.conf         # 413/502/503/504 网关层统一 JSON
apps/web/src/lib/api/interceptors/error.ts        # axios 错误拦截器
packages/ui/src/components/error-toast.tsx        # ErrorToast
packages/form/src/hooks/use-api-field-errors.ts   # details[] → 表单字段错误
scripts/check-error-codes.mjs      # 前后端错误码一致性校验（CI）
```

> **落位说明（与 INFRA-003 / monorepo-structure.md 的命名差异）**：
> 1. **框架层包名**：[`api-conventions.md`](../architecture/api-conventions.md) §10.4 以 `plane/utils/` / `plane/middleware/` 指称相关组件；[`monorepo-structure.md`](../architecture/monorepo-structure.md) §2 已将 `utils/` 与 `middleware/` 用于「分页器、字段选择 mixin」与「请求 ID、审计上下文」等独立模块；`INFRA-003` 已交付 `plane/settings/{common,local,production,test}.py`。**为避免与既有 `plane/utils/` / `plane/middleware/` 目录命名冲突**，本文档新增的「异常处理 / 错误码注册表 / 信封装配 / 六件套中间件 / contextvar」统一落位到 `plane/base/`，与既有 `plane/app/` / `plane/api/` / `plane/space/` / `plane/utils/` / `plane/middleware/` 五包平行的**第六个框架层**。`plane/middleware/` 保留 `monorepo-structure` §2 约定的「请求 ID、审计上下文」等内容（与 `plane/base/middleware.py` 的六件套是子集关系，本文档的六件套作为 `MIDDLEWARE` 列表的入口）。
> 2. **settings 文件名**：[`INFRA-003`](./INFRA-003-django-models-init.md) §2.2 已交付 `plane/settings/{common,local,production,test}.py`；本文 §4.8 引用的 `plane/settings/{__init__,base,dev,prod}.py` 为**正式命名收口**——`common` → `base`、`local` → `dev`、`production` → `prod`、`test.py` 不在 P1 范围（测试态通过 `DJANGO_SETTINGS_MODULE` 切到 `base` + 环境变量覆盖实现）。**该命名收口在 INFRA-003 验收通过后由本文档统一发起 ADR，不在 INFRA-003 PR 中回改**，以避免 PR 范围污染。
> 3. **架构文档待回改**：`api-conventions.md` §10.4 的 `plane/utils/exception_handler.py` 与 `monorepo-structure.md` §2 的 `utils/` / `middleware/` 命名待回改为 `plane/base/`，本文与 INFRA-003 实现为准。

### 1.4 范围边界

| 能力 | 本文档（P1） | 归属 |
| --- | --- | --- |
| 成功 / 错误信封收口 | ✅ | — |
| 错误码注册表 + 双源校验 | ✅ | — |
| ULID 请求追踪 + 结构化日志 | ✅ | — |
| 环境变量分层 + prod 启动校验 | ✅ | — |
| Nginx 网关层统一错误 JSON | ✅（413 / 502 / 503 / 504） | — |
| 前端错误分派 / Toast / 字段错误 / 空态页 | ✅ | — |
| OpenAPI `ErrorEnvelope` 组件 + 类型生成 | ✅ | — |
| 接口限流（配额表 / 三层限流 / `Retry-After` 策略） | ❌ 仅预留 `RateLimitHeaderMiddleware` 空实现与 `RATE_LIMIT_EXCEEDED` 码 | `INFRA-005`（P2） |
| Sentry / 错误聚合上报 | ❌ 仅在 error 日志埋 `request_id` 锚点 | P2 |
| 审计日志（独立表 / 合规留存） | ❌ 仅 `AuditContextMiddleware` 写 contextvar | `AUTH-010`（P3） |
| 多租户配置中心 / 灰度开关 | ❌ | P4 |
| 维护模式页面 | ❌ 仅中间件骨架 | P2（`SERVER_MAINTENANCE` 码已注册） |

### 1.5 前置依赖

| 依赖文档 | 消费的具体决策 | 缺失后果 |
| --- | --- | --- |
| `INFRA-003` | Django App 划分、settings 骨架、`BaseModel` | 异常处理器与中间件无处挂载 |
| `INFRA-002` | `apps/proxy`（Nginx）与全部服务容器；`docker-compose.prod.yml` 的 `${VAR:?}` 强制变量清单（§2.5） | 网关层 413/502/503/504 统一 JSON 无法落地；9 项必填校验清单无依据 |
| `INFRA-001` | `scripts/gen-api-types.mjs` 生成链路、`packages/types` 位置 | 前端 `ErrorCode` 无法同源生成 |
| [`api-conventions.md`](../architecture/api-conventions.md) | §4 响应格式、§8 错误码总表、§10.4 处理顺序与中间件、§13.4 CORS、§13.5 日志——本文档是其**代码实现**，不新增任何规范 | — |

### 1.6 竞品参考

| 竞品 | 参考点 | 处置 |
| --- | --- | --- |
| Plane | `plane/settings/common.py` 按 Docker 环境变量直读约 40 项配置；`EXCEPTION_HANDLER` 有挂载但未全局收口；`base/exception.py` 的 `AppException` 基类 | 环境变量心智**沿用**；异常收口与注册表**收紧**（§6.1） |
| Ones | 企业版配置中心（租户级配置下发、灰度开关）、分级日志采集、审计级留痕 | 对齐其「配置可审计、故障可定位」的**结果体验**，实现方式用 2 人团队可维护的轻量方案（§6.2） |

---

## 2. 业务逻辑

### 2.1 一次请求的完整中间件管道

对齐 [`api-conventions.md`](../architecture/api-conventions.md) §10.4 配套中间件表（顺序敏感，自外向内）：

```mermaid
flowchart TD
    A["客户端请求"] --> M1["① RequestIDMiddleware<br/>X-Request-Id 透传或生成 ULID<br/>写入 contextvar + 响应头"]
    M1 --> M2["② StructuredLoggingMiddleware<br/>开始计时；绑定 user_id / method / path"]
    M2 --> M3["③ RateLimitHeaderMiddleware<br/>注入 X-RateLimit-* 三件套<br/>（P1 空实现，INFRA-005 填充）"]
    M3 --> M4["④ AuditContextMiddleware<br/>user / IP / UA 写入 contextvar<br/>供 save() 审计字段与 Activity 使用"]
    M4 --> M5["⑤ ResponseEnvelopeMiddleware<br/>兜底包装 2xx 信封（204 放行）<br/>开发态发现漏包装直接抛错"]
    M5 --> M6["⑥ MaintenanceModeMiddleware<br/>维护模式白名单外统一 503"]
    M6 --> V{"DRF View / Serializer / Service"}
    V -- "正常返回" --> E["success_response 已包装<br/>（Envelope 仅兜底，不重复包装）"]
    V -- "任何异常" --> X["EXCEPTION_HANDLER<br/>十步收敛（§2.2）"]
    E --> M6R["⑥ 出站：维护模式放行"]
    X --> M6R
    M6R --> M5R["⑤ 出站：断言信封完整性"]
    M5R --> M4R["④ 出站：无动作"]
    M4R --> M3R["③ 出站：无动作"]
    M3R --> M2R["② 出站：记录 access 日志一行<br/>status / error_code / duration_ms / db_query_count"]
    M2R --> M1R["① 出站：X-Request-Id 已注入"]
    M1R --> B["返回客户端"]
```

**顺序为何敏感（三条不可调换的规则）**：

| 规则 | 说明 |
| --- | --- |
| RequestID 必须最外 | 后续所有中间件与 View 的日志都要携带 `request_id`；若在内层，外层异常的日志将失去追踪 ID |
| Envelope 必须在 Maintenance 之外 | 维护模式的 503 响应也要被兜底包装成信封；反之则 503 裸奔 |
| Logging 必须在 RateLimit 之外 | 被限流拒绝的请求也要留 access 日志（此时 `error_code=RATE_LIMIT_EXCEEDED` 是重要的监控维度，§13.5） |

### 2.2 错误发生时的处理顺序（十步收敛）

对齐 [`api-conventions.md`](../architecture/api-conventions.md) §10.4 的处理顺序：

```mermaid
sequenceDiagram
    autonumber
    participant V as View / Serializer
    participant EX as 抛出异常
    participant H as envelope_exception_handler
    participant L as logger(plane.api.errors)
    participant R as Response

    V->>EX: raise（任何类型）
    EX->>H: DRF 先过 drf_exception_handler
    alt 第 1 步：AppException（BusinessError）
        H->>R: code/message/details/doc_url 直接映射 + 注册表 HTTP 码
    else 第 2 步：DRF ValidationError
        H->>R: 400 VALIDATION_ERROR + details[] 平铺（嵌套字段点号路径）
    else 第 3-4 步：NotAuthenticated / AuthenticationFailed
        H->>R: 401 AUTH_*（认证层指定子码）
    else 第 5 步：PermissionDenied
        H->>R: 403 PERM_DENIED（或 Permission 类附带码）
    else 第 6 步：Http404 / ObjectDoesNotExist
        H->>R: 404 RESOURCE_NOT_FOUND
    else 第 7 步：MethodNotAllowed / UnsupportedMediaType
        H->>R: 405 / 415 对应 VALIDATION_* 码
    else 第 8 步：Throttled
        H->>R: 429 RATE_LIMIT_EXCEEDED + Retry-After
    else 第 9 步：IntegrityError
        H->>R: 解析约束名 → 409 RESOURCE_ALREADY_EXISTS / 400 VALIDATION_ERROR
    else 第 10 步：其余未捕获异常
        H->>L: logger.exception（完整堆栈，含 request_id）
        L-->>H: 堆栈仅进日志与（P2）Sentry
        H->>R: 500 SERVER_ERROR，message 固定通用文案
    end
    H->>R: 注入 error.request_id + X-Request-Id 头
    R-->>V: {status:"error", error:{...}}
```

**第 10 步的脱敏红线**：500 响应体**永不**包含堆栈、SQL、文件路径、内部主机名——`message` 固定为「服务器开小差了，请稍后重试」，完整信息只在服务端日志（UT-04 断言）。

### 2.3 异常收敛决策表

| # | 异常源（Python 类型） | HTTP | `error.code` | `details` 形态 | 触发场景举例 |
| --- | --- | --- | --- | --- | --- |
| 1 | `AppException`（业务代码显式抛出） | 注册表映射 | 注册表映射 | `AppException(details=[...])` 透传 | `TASK-002` 标签重名、`FILE-001` 附件超限 |
| 2 | `rest_framework.exceptions.ValidationError` | 400 | `VALIDATION_ERROR` | `detail` 字典逐字段平铺为 `[{field, code, message}]`，嵌套用点号路径 `properties.0.value` | Serializer `is_valid(raise_exception=True)` |
| 3 | 请求体 JSON 解析失败（`json.JSONDecodeError`，DRF 包装为 ParseError） | 400 | `VALIDATION_INVALID_JSON` | 无 details | 请求体不是合法 JSON |
| 4 | `rest_framework.exceptions.NotAuthenticated` | 401 | `AUTH_REQUIRED` | 无 | 未携带 Session 访问受保护端点 |
| 5 | `rest_framework.exceptions.AuthenticationFailed` | 401 | `AUTH_INVALID_CREDENTIALS` / `AUTH_ACCOUNT_DISABLED`（认证层在异常上挂 `error_code` 属性指定） | 无 | 登录密码错误 / 账号禁用 |
| 6 | `rest_framework.exceptions.PermissionDenied` | 403 | `PERM_DENIED`（或 Permission 类附带的 `PERM_ROLE_INSUFFICIENT` 等具体码） | 无 | 角色等级不足 |
| 7 | `django.core.exceptions.PermissionDenied`（Django 原生，CSRF 场景） | 403 | `AUTH_CSRF_FAILED` | 无 | CSRF token 缺失或不匹配 |
| 8 | `django.http.Http404` / `get_object_or_404` / `ObjectDoesNotExist` | 404 | `RESOURCE_NOT_FOUND` | 无 | 资源不存在，**或因权限不可见而隐藏存在性**（§4.3 策略） |
| 9 | `rest_framework.exceptions.MethodNotAllowed` | 405 | `VALIDATION_ERROR`（子码 `METHOD_NOT_ALLOWED`） | 无 | 对端点使用 PUT |
| 10 | `rest_framework.exceptions.UnsupportedMediaType` | 415 | `VALIDATION_UNSUPPORTED_MEDIA_TYPE` | 无 | Content-Type 非 application/json |
| 11 | `rest_framework.exceptions.Throttled` | 429 | `RATE_LIMIT_EXCEEDED` | `[{field:"retry_after", code:"RETRY_AFTER", message:"23"}]` + `Retry-After` 头 | P1 仅认证端点启用；全量见 `INFRA-005` |
| 12 | `django.db.utils.IntegrityError`（唯一约束） | 409 | `RESOURCE_ALREADY_EXISTS` | 从约束名反查冲突字段：`[{field:"slug", code:"UNIQUE", message:"..."}]` | `uniq_workspace_slug_alive` 冲突 |
| 13 | `django.db.utils.IntegrityError`（其余约束） | 400 | `VALIDATION_ERROR` | 约束名 → 字段映射，无法映射时归 `__all__` | `chk_issue_start_before_target` 违反 |
| 14 | `django.db.utils.OperationalError` / `DatabaseError` | 500 | `SERVER_DATABASE_ERROR` | 无 | 连接池耗尽、死锁重试耗尽 |
| 15 | 未捕获 `Exception`（含 `RuntimeError` / `TypeError`…） | 500 | `SERVER_ERROR` | 无；`logger.exception` 记完整堆栈 | 任何编程错误 |

> **行 8 与行 6 的边界**（对齐 §4.3「404 vs 403 一致性策略」）：用户**无权知晓其存在**的资源 → 404（防枚举探测）；用户**能看见但不能操作**的资源 → 403。判定规则收口在 Permission 基类（`AUTH-003`），不由各 ViewSet 自行决定。IT-02 用两账号时间差 < 20ms 断言同构。

### 2.4 业务规则表

| 编号 | 规则 | 判定位置 | 违反后果 |
| --- | --- | --- | --- |
| BR-01 | 成功响应（除 204）必须为 `{status:"success", data, meta}`；`data` 为对象 / 数组 / null 三态，列表端点 `meta` 必填 | `response.py` + Envelope 兜底 | 开发态抛错；CI 快照失败 |
| BR-02 | `204` 响应体必须为空，禁止包装信封 | Envelope 显式放行 | — |
| BR-03 | 所有 `4xx/5xx` 必须为 `{status:"error", error:{...}}`，且 `error.code` 必须在注册表内 | handlers.py | UT-02 KeyError 暴露未注册码 |
| BR-04 | 业务代码只允许抛 `AppException`；禁止手写 `JsonResponse` 错误体、禁止 `raise Response` | Code Review + CI AST 扫描 | 评审拒绝 |
| BR-05 | 错误码一经发布**永不修改语义、永不复用**；新增码先登记 [`api-conventions.md`](../architecture/api-conventions.md) §8 再进注册表 | UT-01 + `check-error-codes.mjs` | CI 失败 |
| BR-06 | `error.request_id` 必填且与 `X-Request-Id` 头同值；成功响应也带该头 | RequestIDMiddleware | UT-05 |
| BR-07 | `AppException` 的 `message` ≤ 200 字符（面向用户直接展示）；超长视为编码错误 | 构造时断言 | AssertionError（测试期） |
| BR-08 | 500 响应体永不包含堆栈 / SQL / 路径 / 内部主机名；`message` 固定通用文案 | handlers.py 第 10 步 | UT-04 |
| BR-09 | 校验错误必须逐字段给出 `details[]`，字段级子码遵循 §8.8（`REQUIRED` / `INVALID` / `UNIQUE` / `DOES_NOT_EXIST`…） | handlers.py 第 2 步 | 评审拒绝 |
| BR-10 | 日志单条 ≤ 8 KB：超长字段（请求体摘要等）截断为前 512 字符 + `…` | structlog processor | UT-08 |
| BR-11 | 日志中的 `password` / `token` / `secret` / `X-API-Key` 一律脱敏为 `***` | structlog processor | UT-11 |
| BR-12 | 单请求 DB 查询 > 30 记 WARN；请求耗时 > 1000ms 记 WARN、> 3000ms 记 ERROR | LoggingMiddleware 出站 | — |
| BR-13 | prod 环境 9 项必填变量（§4.8）缺失时 `ImproperlyConfigured` 快速失败，**禁止带缺省启动** | prod.py 模块级校验 | 进程退出 |
| BR-14 | `SMTP_HOST` 为空 = 邮件功能**降级**为日志投递，主流程不报错（P1 无邮件依赖方，`AUTH-004` 忘记密码依赖此契约） | `plane/app/mail.py` | IT-05 |
| BR-15 | `.env.example` 是唯一环境变量模板；代码读取的变量集合 ⊆ 模板键集合 | UT-09 扫描 `os.environ.get` | CI 失败 |
| BR-16 | 新增错误码的完整链路（文档 §8 → 后端注册表 → 前端常量 → OpenAPI 组件）必须在**同一 PR** 完成 | PR 模板检查项 | 评审拒绝 |

### 2.5 异常场景表（前端表现视角）

| 异常场景 | HTTP | `error.code` | 前端表现 | 后端处理 |
| --- | --- | --- | --- | --- |
| 表单校验失败 | 400 | `VALIDATION_ERROR` | `details[]` 映射到对应输入框红字（§3.3），无全局 Toast | 逐字段展开子码 |
| 越权访问他人资源 | 404 | `RESOURCE_NOT_FOUND` | 404 空态页（§3.4） | 权限不可见统一 404（防枚举） |
| 已认证无操作权限 | 403 | `PERM_DENIED` | 局部空态 / 灰置按钮（`AUTH-005`），**不弹全局 Toast** | Permission 类抛出 |
| 会话过期 | 401 | `AUTH_SESSION_EXPIRED` | 清理本地态，静默跳登录（带 `next`） | 拦截器统一分派 |
| CSRF 失败 | 403 | `AUTH_CSRF_FAILED` | 自动重取 CSRF token 重试一次 | 拦截器分派 |
| 唯一性冲突 | 409 | `RESOURCE_ALREADY_EXISTS` | 对应字段提示「已存在」 | IntegrityError 转换 |
| 乐观锁冲突 | 409 | `RESOURCE_CONFLICT` | 冲突对话框（刷新 / 覆盖） | `If-Match` 不匹配 |
| 触发限流 | 429 | `RATE_LIMIT_EXCEEDED` | Toast 展示 `Retry-After` 秒数（§3.5） | Throttle 类抛出 |
| 服务端异常 | 500 | `SERVER_ERROR` | 通用错误 + 展示 request_id 后 8 位（§3.6） | `logger.exception` |
| 数据库异常 | 500 | `SERVER_DATABASE_ERROR` | 「数据库暂时不可用，请稍后重试」 | 同 500 路径 |
| 网关：上游不可用 | 502 | `SERVER_EXTERNAL_SERVICE_ERROR` | 「服务暂不可用」空态 | Nginx 返回统一 JSON（§4.10） |
| 网关：维护模式 | 503 | `SERVER_MAINTENANCE` | 维护页 | 同上 |
| 网关：上游超时 | 504 | `SERVER_TIMEOUT` | 「请求超时，请重试」 | 同上 |
| 网关：请求体过大 | 413 | `VALIDATION_PAYLOAD_TOO_LARGE` | 「文件过大，请使用附件直传通道」 | 同上 |

### 2.6 边界条件表

| 边界场景 | 限制值 | 超出处理方式 |
| --- | --- | --- |
| 单个 `details[]` 条目数 | ≤ 20 | 截断并在 `meta` 侧标记（防御性约定，P1 不触发） |
| `AppException.message` | 200 字符 | 构造断言失败（编码错误） |
| 日志单条体积 | 8 KB | 超长字段截断前 512 字符 + `…` |
| 请求体大小 | 2 MB（api 层；附件直传走 MinIO 预签名不经 api） | Nginx `client_max_body_size` 拦截 → 413 统一 JSON |
| `request_id` 透传 | 仅接受合法 ULID 格式的外部 `X-Request-Id` | 非法则重新生成（防日志注入） |
| ULID 长度 | 26 字符 | — |
| 环境变量缺失（9 项必填） | — | 启动即 `ImproperlyConfigured`，退出码非 0 |
| 维护模式白名单 | `/api/v1/health/` 一条 | 白名单外全部 503 |

---

## 3. UI/UX 设计

> **本章说明：本文档为基础设施文档，无直接业务界面。**
>
> 但其行为直接决定前端四类**全局错误呈现组件**。本章以「错误呈现规范」替代常规 UI/UX 内容，组件实现在 `packages/ui` / `packages/form` / `apps/web`，被全部业务页面消费。

### 3.1 组件消费关系

| UI 元素 | 依赖本文档的行为 | 实现位置 | 被谁使用 |
| --- | --- | --- | --- |
| `ErrorToast` | `error.message` 可直接展示；`code` 决定图标与时长 | `packages/ui/src/components/error-toast.tsx` | 全局（拦截器调用） |
| 表单字段错误 | `details[].field → code → 文案` 映射 | `packages/form/src/hooks/use-api-field-errors.ts` | 全部表单 |
| 404 / 403 / 500 空态页 | `error.code` 分支 | `apps/web/src/routes/_error.tsx` | 路由级与请求级共用 |
| 429 退避提示 | `Retry-After` 头 / `details` 内秒数 | 拦截器 + `ErrorToast` 变体 | 全局 |

### 3.2 `ErrorToast` 组件规格

```
┌───────────────────────────────────────────────────┐
│  ⚠ 请求过于频繁，请在 23 秒后重试                    │  ← variant: warning
└───────────────────────────────────────────────────┘
┌───────────────────────────────────────────────────┐
│  ✕ 服务器开小差了，请稍后重试                        │
│    追踪号 01JBX3K9  📋 复制              [反馈]    │  ← variant: error + request_id
└───────────────────────────────────────────────────┘
```

| 属性 | 规格 |
| --- | --- |
| `variant` | `error`（红，`alert-circle`）/ `warning`（橙，`alert-triangle`）/ `info`（蓝） |
| 展示时长 | 普通 5s；含 request_id 的错误 10s（保证用户能抄下追踪号） |
| 位置 | 视口右上角，`fixed top-4 right-4 z-50`；多条纵向堆叠，最多 3 条，超出挤掉最早的 |
| 追踪号 | request_id 前 8 位 `font-mono`；点 📋 复制完整 ULID 到剪贴板并 toast「已复制」 |
| 反馈按钮 | 仅 500 类错误显示；点击打开反馈 Modal 并预填追踪号（P2 接 Sentry 后附 issue 链接） |
| 关闭 | 点 ✕ 立即关闭；hover 暂停自动消失计时 |

### 3.3 表单字段错误映射（`useApiFieldErrors`）

```
┌────────────────────────────────────────────────┐
│  项目名称                                       │
│  ┌──────────────────────────────────────────┐  │
│  │                                          │  │  ← 输入框边框变红 border-red-500
│  └──────────────────────────────────────────┘  │
│  ⚠ 该名称已被使用                               │  ← 字段错误文案 text-red-600 text-xs
├────────────────────────────────────────────────┤
│  项目缩写                                       │
│  ┌──────────────────────────────────────────┐  │
│  │ RBT                                      │  │
│  └──────────────────────────────────────────┘  │
└────────────────────────────────────────────────┘
```

```ts
// packages/form/src/hooks/use-api-field-errors.ts（核心逻辑）
const FIELD_CODE_MESSAGES: Record<string, string> = {
  REQUIRED: "该项为必填项",
  UNIQUE: "该值已被使用",
  DOES_NOT_EXIST: "所选值无效",
  INVALID: "格式不正确",
  TOO_LONG: "超出长度限制",
  // …与 api-conventions §8.8 字段级子码一一对应
};

export function useApiFieldErrors(form: UseFormReturn) {
  return useCallback((error: ApiFieldError[] | undefined) => {
    for (const { field, code, message } of error ?? []) {
      form.setError(field, {            // field 已是点号路径，直接映射 react-hook-form 路径
        type: code,
        message: message ?? FIELD_CODE_MESSAGES[code] ?? "校验未通过",
      });
    }
  }, [form]);
}
```

**规则**：`VALIDATION_ERROR` 的 `details[]` **只落字段**、不弹全局 Toast（避免一次提交弹 N 个提示）；无对应字段的条目（`field="__all__"`）降级为 `ErrorToast`。

### 3.4 错误空态页（404 / 403 / 500 共用骨架）

```
┌──────────────────────────────────────────────────────────┐
│                                                          │
│                    ┌────────────┐                        │
│                    │   🧭 96px   │                        │  ← lucide 图标
│                    └────────────┘                        │
│                  页面走丢了                              │  ← 主标题 text-xl
│        你访问的内容不存在、已删除，或你没有访问权限          │  ← 副文案（按 code 微调）
│                                                          │
│           ┌──────────┐   ┌──────────────┐               │
│           │  ← 返回   │   │ 返回工作台    │               │  ← 双按钮
│           └──────────┘   └──────────────┘               │
│                                                          │
│    追踪号 01JBX3K9 · 复制                     （500 时显示）│
└──────────────────────────────────────────────────────────┘
```

| `error.code` | 图标 | 主标题 | 副文案 |
| --- | --- | --- | --- |
| `RESOURCE_NOT_FOUND` | `compass` | 页面走丢了 | 你访问的内容不存在、已删除，或你没有访问权限 |
| `PERM_DENIED` / `PERM_ROLE_INSUFFICIENT` | `lock` | 没有访问权限 | 联系项目管理员为你开通权限后再试 |
| `SERVER_*` | `server-crash` | 服务暂时不可用 | 请稍后重试；若持续出现，请凭追踪号反馈 |

**路由级与请求级共用**：React Router 的 `ErrorBoundary`（路由级）与业务页面的请求失败空态（请求级）渲染同一组件，仅数据来源不同。

### 3.5 429 退避提示

- 首次 429：`ErrorToast(warning)` 展示「请求过于频繁，请在 N 秒后重试」，N 取 `Retry-After` 头（优先）或 `details` 内 `RETRY_AFTER` 值。
- 拦截器内置指数退避重试（初始 1s、因子 2、抖动 ±20%、最多 3 次），**幂等方法自动重试；POST 仅携带 `Idempotency-Key` 时重试**（对齐 §7.4）。
- 退避重试期间 UI 显示局部 `Spinner`，不弹新 Toast；重试仍失败才弹 warning Toast。

### 3.6 request_id 与用户报障路径

| 步骤 | 动作 |
| --- | --- |
| 1 | 用户遇到 500 / 502 类错误，Toast 或空态页展示追踪号前 8 位 |
| 2 | 点「复制」得到完整 ULID |
| 3 | 反馈时粘贴；运维在日志系统执行 `docker compose logs api \| grep 01JBX3K9Q7…` |
| 4 | 命中 1 条 access 日志（请求元信息）+ 1 条 error 日志（完整堆栈），二者 `request_id` 同值 |

此路径是 P1 的「最小可观测性」：不引入 Sentry，也能完成一次端到端故障定位（IT-03 演练）。

### 3.7 响应式与无障碍

| 断点 | 行为 |
| --- | --- |
| ≥ 768px | Toast 右上角；空态页居中最大宽 480px |
| < 768px | Toast 顶部通栏；空态页按钮纵向堆叠 |

无障碍：

- Toast 容器 `role="status"` + `aria-live="polite"`（warning）/ `assertive`（error），屏幕阅读器自动播报。
- 字段错误文案与输入框用 `aria-describedby` 关联；错误态输入框 `aria-invalid="true"`。
- 空态页为正常文档流（非 ARIA 模式），标题用 `h1`，按钮可 Tab 到达。
- 错误信息**不依赖颜色单独传达**：红边框 + 图标 + 文字三重通道。

---

## 4. 技术架构

### 4.1 模块依赖关系

```mermaid
flowchart LR
    subgraph Framework["plane/base/（本文档新增）"]
        EC["error_codes.py<br/>注册表 75 码"]
        EX["exception.py<br/>AppException"]
        HD["handlers.py<br/>十步收敛"]
        RS["response.py<br/>success_response"]
        MW["middleware.py<br/>六件套"]
    end

    subgraph Django["Django / DRF"]
        ST["settings<br/>EXCEPTION_HANDLER=HD"]
        VI["plane/app|api|space<br/>ViewSet × N"]
    end

    subgraph Frontend["前端"]
        AX["axios 拦截器"]
        TS["ErrorCode 常量<br/>@rp/types"]
    end

    DOC["api-conventions.md §8<br/>错误码唯一事实来源"]

    DOC -->|"UT-01 断言一致"| EC
    EC --> EX
    EC --> HD
    ST --> HD
    VI -->|"只抛 AppException"| EX
    VI -->|"正常返回"| RS
    ST --> MW
    MW -->|"contextvar: request_id"| HD
    EC -->|"OpenAPI ErrorEnvelope<br/>→ gen:api-types"| TS
    TS --> AX
```

依赖方向是严格的单向树：业务包（`plane/app` 等）→ `plane/base` → DRF / Django。`plane/base` **不 import 任何业务模型**，因此可被 `plane/app` / `plane/api` / `plane/space` 三套 API 分组无差别复用（对齐 [`api-conventions.md`](../architecture/api-conventions.md) §2.1 的三组隔离要求——三组不共享 Serializer 与 Permission，但共享本框架层）。

### 4.2 错误码注册表（`error_codes.py`）

与 [`api-conventions.md`](../architecture/api-conventions.md) §8 全表一一对应（UT-01 断言）。此处全量直出，作为代码实现的唯一副本：

```python
# apps/api/plane/base/error_codes.py
"""错误码注册表 —— 与 architecture/api-conventions.md §8 一一对应。

硬性约定（BR-05 / BR-16）：
1. 新增错误码必须先在架构文档 §8 登记，再在此实现，并在同一 PR 内
   同步前端 ErrorCode 常量与 OpenAPI ErrorEnvelope（CI 三方校验）。
2. 错误码一经发布永不修改语义、永不复用；废弃码保留并标记 DEPRECATED。
3. 值为 (错误码, HTTP 状态码) 二元组；HTTP 码与 §4.3 状态码表严格一致。
"""
from rest_framework import status


class ErrorCodes:
    # ── §8.2 认证错误 AUTH_* ──────────────────────────────
    AUTH_REQUIRED            = ("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    AUTH_SESSION_EXPIRED     = ("AUTH_SESSION_EXPIRED", status.HTTP_401_UNAUTHORIZED)
    AUTH_INVALID_CREDENTIALS = ("AUTH_INVALID_CREDENTIALS", status.HTTP_401_UNAUTHORIZED)
    AUTH_INVALID_TOKEN       = ("AUTH_INVALID_TOKEN", status.HTTP_401_UNAUTHORIZED)
    AUTH_TOKEN_EXPIRED       = ("AUTH_TOKEN_EXPIRED", status.HTTP_401_UNAUTHORIZED)
    AUTH_TOKEN_REVOKED       = ("AUTH_TOKEN_REVOKED", status.HTTP_401_UNAUTHORIZED)
    AUTH_ACCOUNT_DISABLED    = ("AUTH_ACCOUNT_DISABLED", status.HTTP_401_UNAUTHORIZED)
    AUTH_EMAIL_NOT_VERIFIED  = ("AUTH_EMAIL_NOT_VERIFIED", status.HTTP_401_UNAUTHORIZED)
    AUTH_MFA_REQUIRED        = ("AUTH_MFA_REQUIRED", status.HTTP_401_UNAUTHORIZED)            # P3
    AUTH_SSO_REQUIRED        = ("AUTH_SSO_REQUIRED", status.HTTP_401_UNAUTHORIZED)            # P3
    AUTH_PASSWORD_RESET_INVALID = ("AUTH_PASSWORD_RESET_INVALID", status.HTTP_400_BAD_REQUEST)
    AUTH_PASSWORD_RESET_EXPIRED = ("AUTH_PASSWORD_RESET_EXPIRED", status.HTTP_400_BAD_REQUEST)
    AUTH_TOO_MANY_ATTEMPTS   = ("AUTH_TOO_MANY_ATTEMPTS", status.HTTP_429_TOO_MANY_REQUESTS)
    AUTH_OAUTH_INVALID_GRANT = ("AUTH_OAUTH_INVALID_GRANT", status.HTTP_400_BAD_REQUEST)      # P2
    AUTH_OAUTH_INVALID_CLIENT = ("AUTH_OAUTH_INVALID_CLIENT", status.HTTP_401_UNAUTHORIZED)   # P2
    AUTH_OAUTH_INVALID_SCOPE = ("AUTH_OAUTH_INVALID_SCOPE", status.HTTP_400_BAD_REQUEST)      # P2
    AUTH_CSRF_FAILED         = ("AUTH_CSRF_FAILED", status.HTTP_403_FORBIDDEN)

    # ── §8.3 权限错误 PERM_* ──────────────────────────────
    PERM_DENIED                = ("PERM_DENIED", status.HTTP_403_FORBIDDEN)
    PERM_ROLE_INSUFFICIENT     = ("PERM_ROLE_INSUFFICIENT", status.HTTP_403_FORBIDDEN)
    PERM_PROJECT_ADMIN_REQUIRED = ("PERM_PROJECT_ADMIN_REQUIRED", status.HTTP_403_FORBIDDEN)    # Sprint 5 注册
    PERM_WORKSPACE_ADMIN_REQUIRED = ("PERM_WORKSPACE_ADMIN_REQUIRED", status.HTTP_403_FORBIDDEN) # Sprint 5 注册
    PERM_WORKSPACE_OWNER_REQUIRED = ("PERM_WORKSPACE_OWNER_REQUIRED", status.HTTP_403_FORBIDDEN) # Sprint 5 注册
    PERM_NOT_WORKSPACE_MEMBER  = ("PERM_NOT_WORKSPACE_MEMBER", status.HTTP_403_FORBIDDEN)
    PERM_NOT_PROJECT_MEMBER    = ("PERM_NOT_PROJECT_MEMBER", status.HTTP_403_FORBIDDEN)
    PERM_PROJECT_ARCHIVED      = ("PERM_PROJECT_ARCHIVED", status.HTTP_403_FORBIDDEN)
    PERM_PROJECT_CLOSED        = ("PERM_PROJECT_CLOSED", status.HTTP_403_FORBIDDEN)             # Sprint 5 PROJ-003 注册
    PERM_WORKSPACE_ARCHIVED    = ("PERM_WORKSPACE_ARCHIVED", status.HTTP_403_FORBIDDEN)
    PERM_FIELD_READ_ONLY       = ("PERM_FIELD_READ_ONLY", status.HTTP_403_FORBIDDEN)          # P3
    PERM_FIELD_HIDDEN          = ("PERM_FIELD_HIDDEN", status.HTTP_403_FORBIDDEN)             # P3
    PERM_TRANSITION_NOT_ALLOWED = ("PERM_TRANSITION_NOT_ALLOWED", status.HTTP_403_FORBIDDEN)  # P3
    PERM_APPROVAL_NOT_ASSIGNEE  = ("PERM_APPROVAL_NOT_ASSIGNEE", status.HTTP_403_FORBIDDEN)   # P3
    PERM_LICENSE_REQUIRED      = ("PERM_LICENSE_REQUIRED", status.HTTP_403_FORBIDDEN)
    PERM_SEAT_LIMIT_EXCEEDED   = ("PERM_SEAT_LIMIT_EXCEEDED", status.HTTP_403_FORBIDDEN)
    PERM_IP_NOT_ALLOWED        = ("PERM_IP_NOT_ALLOWED", status.HTTP_403_FORBIDDEN)           # P3
    PERM_TOKEN_SCOPE_INSUFFICIENT = ("PERM_TOKEN_SCOPE_INSUFFICIENT", status.HTTP_403_FORBIDDEN)  # P2

    # ── §8.4 校验错误 VALIDATION_* ────────────────────────
    VALIDATION_ERROR               = ("VALIDATION_ERROR", status.HTTP_400_BAD_REQUEST)
    VALIDATION_INVALID_JSON        = ("VALIDATION_INVALID_JSON", status.HTTP_400_BAD_REQUEST)
    VALIDATION_INVALID_PARAM       = ("VALIDATION_INVALID_PARAM", status.HTTP_400_BAD_REQUEST)
    VALIDATION_INVALID_CURSOR      = ("VALIDATION_INVALID_CURSOR", status.HTTP_400_BAD_REQUEST)
    VALIDATION_UNSUPPORTED_MEDIA_TYPE = ("VALIDATION_UNSUPPORTED_MEDIA_TYPE", status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
    VALIDATION_PAYLOAD_TOO_LARGE   = ("VALIDATION_PAYLOAD_TOO_LARGE", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    VALIDATION_BULK_LIMIT_EXCEEDED = ("VALIDATION_BULK_LIMIT_EXCEEDED", status.HTTP_400_BAD_REQUEST)
    VALIDATION_FILE_TYPE_NOT_ALLOWED = ("VALIDATION_FILE_TYPE_NOT_ALLOWED", status.HTTP_400_BAD_REQUEST)
    VALIDATION_FILE_SIZE_EXCEEDED  = ("VALIDATION_FILE_SIZE_EXCEEDED", status.HTTP_400_BAD_REQUEST)
    VALIDATION_FILE_UPLOAD_MISMATCH = ("VALIDATION_FILE_UPLOAD_MISMATCH", status.HTTP_400_BAD_REQUEST)  # FILE-001 注册
    VALIDATION_INVALID_DATE_RANGE  = ("VALIDATION_INVALID_DATE_RANGE", status.HTTP_400_BAD_REQUEST)
    VALIDATION_CUSTOM_FIELD_INVALID = ("VALIDATION_CUSTOM_FIELD_INVALID", status.HTTP_400_BAD_REQUEST)  # P2
    VALIDATION_REQUIRED_FIELD_MISSING = ("VALIDATION_REQUIRED_FIELD_MISSING", status.HTTP_400_BAD_REQUEST)  # P3
    VALIDATION_ESTIMATE_REQUIRED   = ("VALIDATION_ESTIMATE_REQUIRED", status.HTTP_400_BAD_REQUEST)  # P2

    # ── §8.5 资源错误 RESOURCE_* ──────────────────────────
    RESOURCE_NOT_FOUND            = ("RESOURCE_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    RESOURCE_GONE                 = ("RESOURCE_GONE", status.HTTP_410_GONE)
    RESOURCE_ALREADY_EXISTS       = ("RESOURCE_ALREADY_EXISTS", status.HTTP_409_CONFLICT)
    RESOURCE_CONFLICT             = ("RESOURCE_CONFLICT", status.HTTP_409_CONFLICT)
    RESOURCE_STATE_INVALID        = ("RESOURCE_STATE_INVALID", status.HTTP_409_CONFLICT)
    RESOURCE_TRANSITION_INVALID   = ("RESOURCE_TRANSITION_INVALID", status.HTTP_409_CONFLICT)      # P3
    RESOURCE_TRANSITION_BLOCKED   = ("RESOURCE_TRANSITION_BLOCKED", status.HTTP_409_CONFLICT)      # P2
    RESOURCE_CIRCULAR_DEPENDENCY  = ("RESOURCE_CIRCULAR_DEPENDENCY", status.HTTP_409_CONFLICT)    # P2
    RESOURCE_IN_USE               = ("RESOURCE_IN_USE", status.HTTP_409_CONFLICT)
    RESOURCE_LIMIT_EXCEEDED       = ("RESOURCE_LIMIT_EXCEEDED", status.HTTP_409_CONFLICT)
    RESOURCE_LOCKED               = ("RESOURCE_LOCKED", status.HTTP_409_CONFLICT)                 # P3

    # ── §8.6 服务端错误 SERVER_* ──────────────────────────
    SERVER_ERROR                      = ("SERVER_ERROR", status.HTTP_500_INTERNAL_SERVER_ERROR)
    SERVER_DATABASE_ERROR             = ("SERVER_DATABASE_ERROR", status.HTTP_500_INTERNAL_SERVER_ERROR)
    SERVER_STORAGE_ERROR              = ("SERVER_STORAGE_ERROR", status.HTTP_500_INTERNAL_SERVER_ERROR)      # FILE-001
    SERVER_QUEUE_ERROR                = ("SERVER_QUEUE_ERROR", status.HTTP_500_INTERNAL_SERVER_ERROR)
    SERVER_EMAIL_ERROR                = ("SERVER_EMAIL_ERROR", status.HTTP_500_INTERNAL_SERVER_ERROR)
    SERVER_EXTERNAL_SERVICE_ERROR     = ("SERVER_EXTERNAL_SERVICE_ERROR", status.HTTP_502_BAD_GATEWAY)       # INTG-*
    SERVER_LIVE_SERVICE_UNAVAILABLE   = ("SERVER_LIVE_SERVICE_UNAVAILABLE", status.HTTP_503_SERVICE_UNAVAILABLE)  # COLLAB-004
    SERVER_MAINTENANCE                = ("SERVER_MAINTENANCE", status.HTTP_503_SERVICE_UNAVAILABLE)
    SERVER_TIMEOUT                    = ("SERVER_TIMEOUT", status.HTTP_504_GATEWAY_TIMEOUT)
    SERVER_NOT_IMPLEMENTED            = ("SERVER_NOT_IMPLEMENTED", status.HTTP_501_NOT_IMPLEMENTED)

    # ── §8.7 限流与配额 RATE_* / QUOTA_* ──────────────────
    RATE_LIMIT_EXCEEDED    = ("RATE_LIMIT_EXCEEDED", status.HTTP_429_TOO_MANY_REQUESTS)   # INFRA-005 填充
    QUOTA_STORAGE_EXCEEDED = ("QUOTA_STORAGE_EXCEEDED", status.HTTP_409_CONFLICT)          # P2
    QUOTA_MEMBER_EXCEEDED  = ("QUOTA_MEMBER_EXCEEDED", status.HTTP_409_CONFLICT)           # P3
    QUOTA_PROJECT_EXCEEDED = ("QUOTA_PROJECT_EXCEEDED", status.HTTP_409_CONFLICT)          # P3
    QUOTA_AI_EXCEEDED      = ("QUOTA_AI_EXCEEDED", status.HTTP_409_CONFLICT)               # P4 AI-001 注册

    @classmethod
    def all(cls) -> dict[str, int]:
        """{(code, http_status)} 全集 —— UT-01 与 CI 校验的数据源"""
        return {
            value[0]: value[1]
            for key, value in vars(cls).items()
            if isinstance(value, tuple) and len(value) == 2
        }


#: 面向用户的默认中文文案；AppException 未传 message 时使用。
#: 文案可随版本调整（客户端禁止匹配文案，只允许按 code 分支——§8.1）。
DEFAULT_MESSAGES: dict[str, str] = {
    "AUTH_REQUIRED": "请先登录",
    "AUTH_SESSION_EXPIRED": "登录已过期，请重新登录",
    "AUTH_INVALID_CREDENTIALS": "邮箱或密码错误",
    "AUTH_ACCOUNT_DISABLED": "账号已被禁用，请联系管理员",
    "AUTH_CSRF_FAILED": "安全校验失败，正在自动重试",
    "PERM_DENIED": "你没有执行此操作的权限",
    "PERM_ROLE_INSUFFICIENT": "当前角色权限不足",
    "VALIDATION_ERROR": "请求参数校验失败",
    "VALIDATION_INVALID_JSON": "请求体不是合法的 JSON",
    "VALIDATION_PAYLOAD_TOO_LARGE": "请求体过大，请使用附件直传通道",
    "RESOURCE_NOT_FOUND": "资源不存在或你没有访问权限",
    "RESOURCE_ALREADY_EXISTS": "该名称已被使用",
    "RESOURCE_CONFLICT": "该数据已被他人修改，请刷新后重试",
    "RATE_LIMIT_EXCEEDED": "请求过于频繁，请稍后重试",
    "SERVER_ERROR": "服务器开小差了，请稍后重试",
    "SERVER_DATABASE_ERROR": "数据库暂时不可用，请稍后重试",
    "SERVER_MAINTENANCE": "系统维护中，请稍后再来",
    "SERVER_TIMEOUT": "请求超时，请重试",
    # …其余码按 §8 表逐个补全（省略号仅文档示意，代码中必须全量）
}
```

**注册表规模核对**：AUTH 17 + PERM 18 + VALIDATION 14 + RESOURCE 11 + SERVER 10 + RATE/QUOTA 5 = **75 码**，与 [`api-conventions.md`](../architecture/api-conventions.md) §8 各分表行数合计一致（UT-01 逐码断言，多一个少一个都失败）。

### 4.3 业务异常基类（`exception.py`）

```python
# apps/api/plane/base/exception.py
"""业务异常 —— 业务代码唯一允许抛出的异常类型（BR-04）。"""
from rest_framework import exceptions

from plane.base.error_codes import DEFAULT_MESSAGES, ErrorCodes

MAX_MESSAGE_LENGTH = 200


class AppException(exceptions.APIException):
    """携带注册表错误码的业务异常。

    用法：
        raise AppException("RESOURCE_ALREADY_EXISTS",
                           message="项目缩写 RBT 已被使用",
                           details=[{"field": "identifier", "code": "UNIQUE",
                                     "message": "该缩写已存在"}])

    约束：
        - code 必须已注册（未注册码在此 KeyError，测试期即暴露，UT-02）
        - message ≤ 200 字符（BR-07，超长断言失败）
        - status_code 由注册表决定，调用方不可覆盖
    """

    status_code = 400  # 占位；实际由注册表覆盖

    def __init__(
        self,
        code: str,
        message: str | None = None,
        details: list[dict] | None = None,
        doc_url: str | None = None,
    ):
        registry = ErrorCodes.all()
        if code not in registry:
            raise KeyError(
                f"未注册的错误码 {code!r}：请先在 api-conventions.md §8 与 "
                f"error_codes.py 登记（同一 PR 完成前后端与 OpenAPI 同步，BR-16）"
            )
        self.error_code = code
        self.http_status = registry[code]
        self.detail_message = message or DEFAULT_MESSAGES.get(code, "请求失败")
        assert len(self.detail_message) <= MAX_MESSAGE_LENGTH, "message 超过 200 字符（BR-07）"
        self.extra_details = details or []
        self.doc_url = doc_url
        super().__init__(self.detail_message)


class BusinessError(AppException):
    """别名 —— 对齐 api-conventions.md §10.4 第 1 步的规范用名。

    规范文档称 BusinessError，代码主名 AppException（沿用 Plane 基类名，
    便于对照阅读）。二者是同一类，新代码建议统一用 AppException。
    """
```

### 4.4 全局异常处理器（`handlers.py`，十步收敛）

注册于 `REST_FRAMEWORK["EXCEPTION_HANDLER"]`。处理顺序与 [`api-conventions.md`](../architecture/api-conventions.md) §10.4 逐条对应：

```python
# apps/api/plane/base/handlers.py
"""全局异常处理器 —— 一切异常收敛为 §4.2 错误信封，无例外。"""
import logging

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied as DjangoPermissionDenied
from django.db import DatabaseError, IntegrityError, OperationalError
from django.http import Http404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework.exceptions import (
    AuthenticationFailed, MethodNotAllowed, NotAuthenticated,
    ParseError, PermissionDenied, Throttled, UnsupportedMediaType,
    ValidationError,
)

from plane.base.error_codes import DEFAULT_MESSAGES
from plane.base.request_context import current_request_id  # contextvar 读取器

logger = logging.getLogger("plane.api.errors")

#: IntegrityError 约束名 → (错误码, 冲突字段) 映射；新增约束在此登记
CONSTRAINT_MAP = {
    "uniq_workspace_slug_alive":        ("RESOURCE_ALREADY_EXISTS", "slug"),
    "uniq_project_identifier_per_workspace": ("RESOURCE_ALREADY_EXISTS", "identifier"),
    "uniq_issue_sequence_per_project":  ("RESOURCE_CONFLICT", "sequence_id"),
    "uniq_issue_assignee":              ("VALIDATION_ERROR", "assignee_ids"),
    "uniq_issue_label":                 ("VALIDATION_ERROR", "label_ids"),
    "uniq_state_name_per_project_type": ("RESOURCE_ALREADY_EXISTS", "name"),
    "chk_issue_start_before_target":    ("VALIDATION_INVALID_DATE_RANGE", "target_date"),
    "chk_issue_link_no_self":           ("VALIDATION_ERROR", "related_issue_id"),
}


def envelope_exception_handler(exc: Exception, context: dict) -> Response:
    request_id = current_request_id() or "unknown"

    # ── 第 10 步前置判定：DRF 不处理的异常（未捕获 Exception、IntegrityError、
    #    OperationalError、Django 原生异常）drf_handler 返回 None ──
    response = drf_exception_handler(exc, context)

    # ── 第 1 步：AppException（BusinessError）──
    if getattr(exc, "error_code", None):
        response = Response(status=getattr(exc, "http_status", status.HTTP_400_BAD_REQUEST))
        response.data = _error_body(
            exc.error_code, request_id,
            message=getattr(exc, "detail_message", None),
            details=getattr(exc, "extra_details", None),
            doc_url=getattr(exc, "doc_url", None),
        )

    # ── 第 2 步：DRF ValidationError → details[] 平铺（嵌套点号路径）──
    elif isinstance(exc, ValidationError):
        response = Response(status=status.HTTP_400_BAD_REQUEST)
        response.data = _error_body(
            "VALIDATION_ERROR", request_id,
            message=DEFAULT_MESSAGES["VALIDATION_ERROR"],
            details=_flatten_validation_detail(exc.detail),
        )

    elif isinstance(exc, ParseError):  # 第 2 步姊妹：JSON 解析失败
        response = Response(status=status.HTTP_400_BAD_REQUEST)
        response.data = _error_body("VALIDATION_INVALID_JSON", request_id)

    # ── 第 3-4 步：认证类 ──
    elif isinstance(exc, NotAuthenticated):
        response = Response(status=status.HTTP_401_UNAUTHORIZED)
        response.data = _error_body("AUTH_REQUIRED", request_id)
    elif isinstance(exc, AuthenticationFailed):
        # 认证层（AUTH-001）在异常上挂 error_code 指定子码
        code = getattr(exc, "error_code", "AUTH_INVALID_CREDENTIALS")
        response = Response(status=status.HTTP_401_UNAUTHORIZED)
        response.data = _error_body(code, request_id)

    # ── 第 5 步：权限类（DRF / Django 原生含 CSRF）──
    elif isinstance(exc, PermissionDenied):
        code = getattr(exc, "error_code", "PERM_DENIED")  # Permission 类可附带具体码
        response = Response(status=status.HTTP_403_FORBIDDEN)
        response.data = _error_body(code, request_id)
    elif isinstance(exc, DjangoPermissionDenied):  # CSRF 中间件抛的是 Django 原生类
        response = Response(status=status.HTTP_403_FORBIDDEN)
        response.data = _error_body("AUTH_CSRF_FAILED", request_id)

    # ── 第 6 步：404（资源不存在 / 权限不可见，二者同构）──
    elif isinstance(exc, Http404) or isinstance(exc, ObjectDoesNotExist):
        response = Response(status=status.HTTP_404_NOT_FOUND)
        response.data = _error_body("RESOURCE_NOT_FOUND", request_id)

    # ── 第 7 步：方法 / 媒体类型 ──
    elif isinstance(exc, MethodNotAllowed):
        response = Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)
        response.data = _error_body("VALIDATION_ERROR", request_id,
                                    details=[{"field": "__method__",
                                              "code": "METHOD_NOT_ALLOWED",
                                              "message": str(exc.detail)}])
    elif isinstance(exc, UnsupportedMediaType):
        response = Response(status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
        response.data = _error_body("VALIDATION_UNSUPPORTED_MEDIA_TYPE", request_id)

    # ── 第 8 步：限流 ──
    elif isinstance(exc, Throttled):
        wait = int(getattr(exc, "wait", 1) or 1)
        response = Response(status=status.HTTP_429_TOO_MANY_REQUESTS)
        response.headers["Retry-After"] = str(wait)
        response.data = _error_body(
            "RATE_LIMIT_EXCEEDED", request_id,
            message=f"请求过于频繁，请在 {wait} 秒后重试",
            details=[{"field": "retry_after", "code": "RETRY_AFTER", "message": str(wait)}],
        )

    # ── 第 9 步：数据库完整性 / 连接 ──
    elif isinstance(exc, IntegrityError):
        code, field = _parse_constraint(str(exc))
        http = status.HTTP_409_CONFLICT if code == "RESOURCE_ALREADY_EXISTS" else status.HTTP_400_BAD_REQUEST
        response = Response(status=http)
        response.data = _error_body(code, request_id,
                                    details=[{"field": field, "code": "UNIQUE" if http == 409 else "INVALID",
                                              "message": DEFAULT_MESSAGES.get(code, "数据约束冲突")}])
        logger.warning("integrity_error request_id=%s constraint=%s", request_id, _constraint_name(str(exc)))
    elif isinstance(exc, (OperationalError, DatabaseError)):
        # OperationalError 是连接级（连接池耗尽、服务不可达），DatabaseError 是其父类（含编程错误）
        logger.exception("database_error request_id=%s", request_id)
        response = Response(status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        response.data = _error_body("SERVER_DATABASE_ERROR", request_id)

    # ── 第 10 步：其余未捕获异常 → 500（堆栈只进日志，UT-04 脱敏断言）──
    elif response is None:
        logger.exception("unhandled_exception request_id=%s path=%s",
                         request_id, getattr(context.get("request"), "path", "?"))
        response = Response(status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        response.data = _error_body("SERVER_ERROR", request_id,
                                    message=DEFAULT_MESSAGES["SERVER_ERROR"])

    response.headers["X-Request-Id"] = request_id
    return response


def _error_body(code: str, request_id: str, *, message: str | None = None,
                details: list[dict] | None = None,
                doc_url: str | None = None) -> dict:
    body = {"status": "error", "error": {"code": code,
                                         "message": message or DEFAULT_MESSAGES.get(code, "请求失败"),
                                         "request_id": request_id}}
    if details:
        body["error"]["details"] = details[:20]        # BR 截断上限
    if doc_url:
        body["error"]["doc_url"] = doc_url
    return body


def _flatten_validation_detail(detail, prefix: str = "") -> list[dict]:
    """DRF ValidationError.detail → [{field, code, message}]，嵌套用点号路径。

    例：{"a": {"0": [{"b": [ErrorDetail(...REQUIRED)]}]}}
      → [{"field": "a.0.b", "code": "REQUIRED", "message": "该项为必填项"}]
    """
    from rest_framework.exceptions import ErrorDetail

    items: list[dict] = []
    if isinstance(detail, dict):
        for key, value in detail.items():
            items += _flatten_validation_detail(value, f"{prefix}{key}" if not prefix else f"{prefix}.{key}")
    elif isinstance(detail, list):
        for i, value in enumerate(detail):
            if isinstance(value, ErrorDetail):          # 非 positional 情形直接是错误串
                items += _flatten_validation_detail(value, prefix)
            else:
                items += _flatten_validation_detail(value, f"{prefix}.{i}" if prefix else str(i))
    else:  # ErrorDetail
        items.append({"field": prefix or "__all__",
                      "code": getattr(detail, "code", "INVALID"),
                      "message": str(detail)})
    return items


def _constraint_name(db_message: str) -> str | None:
    import re
    matched = re.search(r'constraint "(\w+)"', db_message) or re.search(r"ON CONSTRAINT (\w+)", db_message)
    return matched.group(1) if matched else None


def _parse_constraint(db_message: str) -> tuple[str, str]:
    name = _constraint_name(db_message)
    if name and name in CONSTRAINT_MAP:
        return CONSTRAINT_MAP[name]
    return ("VALIDATION_ERROR", "__all__") if name else ("VALIDATION_ERROR", "__all__")
```

**三个实现要点**：

| 要点 | 说明 |
| --- | --- |
| `request_id` 取自 contextvar 而非 `context["request"]` | 未捕获异常可能发生在请求对象构造早期；contextvar 由最外层中间件绑定，永远可用（§4.6 第①件） |
| `Retry-After` 头与 `details` 双通道携带等待秒数 | 头供机器（拦截器退避算法优先读头），`details` 供人（Toast 文案直接拼数字），对齐 §7.3 |
| `CONSTRAINT_MAP` 集中登记约束名 | 新增 UniqueConstraint / CheckConstraint 的模型 PR 必须同步登记，否则测试期 `IT` 用例会以 `__all__` 兜底暴露 |

### 4.5 成功响应装配（`response.py`）

```python
# apps/api/plane/base/response.py
"""成功信封唯一装配点 —— 业务 ViewSet 禁止手写 JsonResponse（BR-01/BR-04）。"""
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK, HTTP_201_CREATED


def success_response(data=None, *, meta: dict | None = None,
                     status_code: int = HTTP_200_OK, headers: dict | None = None) -> Response:
    body: dict = {"status": "success", "data": data}
    if meta is not None:
        body["meta"] = meta
    return Response(body, status=status_code, headers=headers)


def created_response(data, *, location: str, meta: dict | None = None) -> Response:
    """201 专用：必须携带 Location 头（§4.3 状态码表）。"""
    return success_response(data, meta=meta, status_code=HTTP_201_CREATED,
                            headers={"Location": location})


def paginated_response(results: list, *, paginator, request) -> Response:
    """游标分页 meta 装配 —— 九个必填字段一次到位（§6.3 表）。

    paginator 为 plane/base/paginator.py 的 CursorPagination 实例
    （INFRA-003 交付，格式 "{value}:{offset}:{is_prev}" Base64）。
    """
    return success_response(
        results,
        meta={
            "next_cursor": paginator.next_cursor,
            "prev_cursor": paginator.prev_cursor,
            "next_page_results": paginator.has_next,
            "prev_page_results": paginator.has_prev,
            "count": len(results),
            "total_count": paginator.total_count,
            "total_pages": paginator.total_pages,
            "page": paginator.page_number,
            "per_page": paginator.per_page,
        },
        headers={"X-Request-Id": current_request_id() or ""},
    )
```

### 4.6 六件套中间件（`middleware.py`）

顺序与职责对齐 [`api-conventions.md`](../architecture/api-conventions.md) §10.4 配套中间件表（自外向内 ①→⑥）；`settings/base.py` 的 `MIDDLEWARE` 列表即按此顺序书写（§4.8）：

```python
# apps/api/plane/base/middleware.py
"""六件套中间件 —— 顺序敏感，见 api-conventions.md §10.4。"""
import contextvars
import logging
import re
import time

import structlog
from django.conf import settings as dj_settings
from django.http import HttpResponse, JsonResponse
from rest_framework.status import HTTP_503_SERVICE_UNAVAILABLE
from ulid import ULID

from plane.base.error_codes import DEFAULT_MESSAGES

# ── 跨中间件共享的请求上下文（contextvar）──────────────────
_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
_actor_var: contextvars.ContextVar[dict] = contextvars.ContextVar("actor", default={})


def current_request_id() -> str | None:
    return _request_id_var.get() or None


def current_actor() -> dict:
    """AuditContextMiddleware 写入；模型 save() 与 Celery 任务读它填充审计字段"""
    return _actor_var.get()


def ulid_new() -> str:
    """生成 26 位 Crockford Base32 ULID —— RequestIDMiddleware 与 §2.6 边界表共用。"""
    return str(ULID())


def settings_debug() -> bool:
    """dev/prod 一行判断；ResponseEnvelopeMiddleware 据此切换严格模式（开发态裸 2xx 抛错）。"""
    return bool(getattr(dj_settings, "DEBUG", False))


def _error_code_of(response) -> str | None:
    """从 DRF Response.data 中提取 envelope 的 error.code（access 日志维度，§13.5）。"""
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            return err.get("code")
    return None


ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")   # Crockford Base32，排除 I/L/O/U


# ── ① RequestIDMiddleware（最外层）────────────────────────
class RequestIDMiddleware:
    """ULID 请求追踪：透传合法外部 X-Request-Id，否则生成；非法值重生成（防日志注入）。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get("X-Request-Id", "")
        request.request_id = incoming if ULID_RE.fullmatch(incoming) else ulid_new()
        token = _request_id_var.set(request.request_id)
        try:
            response = self.get_response(request)
        finally:
            _request_id_var.reset(token)      # worker 线程复用，必须清理
        response.headers["X-Request-Id"] = request.request_id   # 成功响应也带（C3）
        return response


# ── ② StructuredLoggingMiddleware ─────────────────────────
class StructuredLoggingMiddleware:
    """每请求一行结构化 access 日志；携带 §13.5 全部字段。"""

    SLOW_REQUEST_WARN_MS = 1000     # > 1s 记 WARN
    SLOW_REQUEST_ERROR_MS = 3000    # > 3s 记 ERROR
    QUERY_COUNT_WARN = 30           # 单请求查询数预警（N+1 早期信号）

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.perf_counter()
        from django.db import connection, reset_queries
        if settings_debug():
            reset_queries()
        response = self.get_response(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        log = structlog.get_logger("plane.api.access")
        level = "error" if duration_ms > self.SLOW_REQUEST_ERROR_MS else \
                "warning" if duration_ms > self.SLOW_REQUEST_WARN_MS else "info"
        # path 用路由模板而非实际 URL（避免 ID 爆炸日志基数，§13.5）
        route_template = getattr(request.resolver_match, "route", request.path)
        # workspace_id 取自 URL 路径参数（slash 前缀的 URL 解析）；命中后写入日志维度
        workspace_id = None
        if request.resolver_match is not None:
            workspace_id = request.resolver_match.kwargs.get("slug")
        # 查询数预警：DEBUG 开启连接追踪时记录实际值，超阈值则字段额外标记 WARN
        query_count = len(connection.queries) if settings_debug() else None
        if query_count is not None and query_count > self.QUERY_COUNT_WARN:
            log = log.bind(query_count_warn=True)
        log.log(getattr(logging, level.upper()), "http_request",
                method=request.method,
                path="/" + route_template,
                status=response.status_code,
                error_code=_error_code_of(response),
                duration_ms=duration_ms,
                db_query_count=query_count,
                user_id=_actor_var.get().get("user_id"),
                workspace_id=workspace_id)
        return response


# ── ③ RateLimitHeaderMiddleware（P1 空实现，INFRA-005 填充）──
class RateLimitHeaderMiddleware:
    """为所有响应注入 X-RateLimit-Limit / -Remaining / -Reset 三件套（§4.4）。

    P1 仅注入占位值（limit=-1 表示未启用）；INFRA-005 接入 Redis 计数后
    替换为真实配额。位置必须在 Logging 之内——被限流拒绝的请求也要留日志。
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.headers.setdefault("X-RateLimit-Limit", "-1")
        response.headers.setdefault("X-RateLimit-Remaining", "-1")
        response.headers.setdefault("X-RateLimit-Reset", "-1")
        return response


# ── ④ AuditContextMiddleware ──────────────────────────────
class AuditContextMiddleware:
    """user / IP / UA 写入 contextvar，供模型 save() 审计字段与（P3）审计日志消费。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        token = _actor_var.set({
            "user_id": str(user.id) if getattr(user, "is_authenticated", False) else None,
            "ip": request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
                  or request.META.get("REMOTE_ADDR"),
            "user_agent": request.META.get("HTTP_USER_AGENT", "")[:256],
        })
        try:
            return self.get_response(request)
        finally:
            _actor_var.reset(token)


# ── ⑤ ResponseEnvelopeMiddleware（兜底 + 开发态抛错）────────
class ResponseEnvelopeMiddleware:
    """捕获未经 success_response 的 2xx 响应并补齐信封（防止漏包装）。

    - 204 / 304：显式放行（C1 例外，BR-02）
    - 已是 {status:"success"} 结构：原样通过（防重复包装）
    - 流式 / 文件响应（FileResponse、StreamingHttpResponse）：放行
    - 开发态发现裸 2xx dict/list：直接抛错，尽早暴露（C1 守护）
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.status_code in (204, 304) or getattr(response, "streaming", False):
            return response
        if 200 <= response.status_code < 300:
            content_type = response.headers.get("Content-Type", "")
            if "application/json" not in content_type:      # 非 JSON（健康检查等）放行
                return response
            body = getattr(response, "data", None)
            if isinstance(body, dict) and body.get("status") == "success":
                return response                              # 已包装
            if settings_debug() and body is not None:
                raise RuntimeError(
                    f"[Envelope] {request.method} {request.path} 返回了未包装的 2xx JSON："
                    f"请使用 plane.base.response.success_response（C1）")
            if isinstance(body, (dict, list)) or body is None:
                response.data = {"status": "success", "data": body}
        return response


# ── ⑥ MaintenanceModeMiddleware（最内层，P2 启用开关）────────
class MaintenanceModeMiddleware:
    """维护模式下白名单外统一 503 SERVER_MAINTENANCE（§4.3 / §8.6）。"""

    WHITELIST = ("/api/v1/health/",)

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings as dj_settings
        if getattr(dj_settings, "MAINTENANCE_MODE", False) \
                and not request.path.startswith(self.WHITELIST):
            return JsonResponse(
                {"status": "error",
                 "error": {"code": "SERVER_MAINTENANCE",
                           "message": DEFAULT_MESSAGES["SERVER_MAINTENANCE"],
                           "request_id": _request_id_var.get() or "unknown"}},
                status=HTTP_503_SERVICE_UNAVAILABLE,
                headers={"Retry-After": "300", "X-Request-Id": _request_id_var.get() or ""},
            )
        return self.get_response(request)
```

| # | 中间件 | P1 状态 | 关键实现细节 |
| --- | --- | --- | --- |
| ① | `RequestIDMiddleware` | ✅ 全量 | ULID 正则校验外部透传值，**非法即重生成**——防止恶意注入换行符伪造日志行（边界表） |
| ② | `StructuredLoggingMiddleware` | ✅ 全量 | `path` 记**路由模板**不记实际 URL（`/workspaces/{slug}/…` 而非 `/workspaces/acme/…`），日志基数不随资源数爆炸（§13.5） |
| ③ | `RateLimitHeaderMiddleware` | 占位 | 三头注入 `-1`；`INFRA-005` 接 Redis 计数后替换 |
| ④ | `AuditContextMiddleware` | ✅ 全量 | contextvar 而非 thread-local——gunicorn gthread + async 安全 |
| ⑤ | `ResponseEnvelopeMiddleware` | ✅ 全量 | 开发态抛错把「漏包装」从线上问题变成本地问题（C1 守护的第二道闸） |
| ⑥ | `MaintenanceModeMiddleware` | 骨架 | `MAINTENANCE_MODE` 开关默认 False，P2 接入实例配置 |

### 4.7 structlog 配置与日志规范

```python
# apps/api/plane/logging.py —— 进程入口（wsgi/worker/beat 共同调用）
"""structlog：JSON 行输出到 stdout，Docker json-file driver 收集（INFRA-002 §3.4）。"""
import logging
import sys

import structlog

MAX_LOG_BYTES = 8 * 1024                 # BR-10：单条 ≤ 8KB
REDACT_KEYS = {"password", "token", "secret", "api_key", "x-api-key", "authorization"}
TRUNCATE_FIELDS = ("body", "payload", "query_params", "result")   # 超长截断字段


def _truncate(value, max_len=512):
    text = value if isinstance(value, str) else repr(value)
    return text[:max_len] + "…" if len(text) > max_len else text


def _redact(logger, method_name, event_dict):
    """脱敏 processor：REDACT_KEYS 命中键值 → '***'（BR-11，§13.5）。"""
    for key in list(event_dict):
        if key.lower() in REDACT_KEYS:
            event_dict[key] = "***"
        elif key in TRUNCATE_FIELDS:
            event_dict[key] = _truncate(event_dict[key])
    return event_dict


def configure_logging(debug: bool = False) -> None:
    shared = [
        structlog.contextvars.merge_contextvars,        # request_id / user_id 自动合入
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact,
        structlog.processors.format_exc_info,           # exception → 结构化 exc_info 字段
        structlog.processors.JSONRenderer(ensure_ascii=False),
    ]
    structlog.configure(
        processors=shared,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if debug else logging.INFO),
        cache_logger_on_first_use=True,                 # 性能：处理器链只构造一次
    )
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
```

**access 日志实际输出样例**（§13.5 字段逐一对齐）：

```json
{"event": "http_request", "request_id": "01JBX3K9Q7ZR4M8N2P5V6W7X8Y",
 "level": "info", "logger": "plane.api.access",
 "user_id": "6c7d1a2b-3e4f-4a5b-9c8d-7e6f5a4b3c2d",
 "workspace_id": "9d4a7c8b-1e2f-4a3b-8c5d-6f7e8a9b0c1d",
 "method": "PATCH", "path": "/api/v1/workspaces/{slug}/projects/{project_id}/issues/{issue_id}/",
 "status": 200, "error_code": null, "duration_ms": 34.2, "db_query_count": null,
 "timestamp": "2026-09-01T07:12:45.120Z"}
```

**error 日志样例**（未捕获异常，含堆栈）：

```json
{"event": "unhandled_exception", "request_id": "01JBX3K9Q7ZR4M8N2P5V6W7X8Z",
 "level": "error", "logger": "plane.api.errors", "path": "/api/v1/workspaces/acme/…/",
 "exc_info": "Traceback (most recent call last):\n  File \"plane/app/views/issue.py\", line 88 …",
 "timestamp": "2026-09-01T07:12:45.884Z"}
```

**日志 channel 划分**：

| channel | 用途 | 级别 | 消费方 |
| --- | --- | --- | --- |
| `plane.api.access` | 每请求一行 | info / warning（慢）/ error（超慢） | 流量画像、慢请求告警 |
| `plane.api.errors` | 未捕获异常堆栈 | error | P2 Sentry / 人工排障 |
| `plane.app.<module>` | 业务日志（`plane.app.mail` 等） | info / warning | 功能排障（如 SMTP 降级证据，IT-05） |
| `plane.celery` | 异步任务执行与重试 | info / error | 任务健康度 |

**三条可观测规则**（对齐 §13.5，全部由 ② 号中间件与 processor 自动执行，业务零参与）：

| 规则 | 阈值 | 动作 |
| --- | --- | --- |
| 慢请求 | > 1000ms | WARN + 附查询摘要；> 3000ms 升 ERROR |
| 查询数预警 | 单请求 > 30 条 | WARN（`db_query_count` 字段，仅 DEBUG 连接追踪开启时输出） |
| 敏感脱敏 | `password` / `token` / `secret` / `X-API-Key` / `authorization` | 值替换 `***`（UT-11） |

### 4.8 环境配置体系（settings 四文件）

```python
# apps/api/plane/settings/__init__.py —— 按 DJANGO_SETTINGS_MODULE 分发
import os
from .base import *          # noqa: F401,F403

_mode = os.environ.get("DJANGO_SETTINGS_MODULE", "plane.settings.dev")
assert _mode.startswith("plane.settings."), f"非法 settings 模块：{_mode}"
```

```python
# apps/api/plane/settings/base.py（公共配置，全量直出关键段落）
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

def env(key: str, default=None):
    return os.environ.get(key, default)

def env_bool(key: str, default: bool = False) -> bool:
    return str(env(key, default)).lower() in ("1", "true", "yes")

SECRET_KEY = env("SECRET_KEY", "dev-insecure-key")     # prod 强制覆盖（§ prod.py BR-13）
DEBUG = env_bool("DEBUG", False)
ALLOWED_HOSTS = [h.strip() for h in env("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")]

# ── 中间件：六件套顺序即 §4.6 编号（顺序敏感，禁止重排）────
MIDDLEWARE = [
    "plane.base.middleware.RequestIDMiddleware",             # ①
    "plane.base.middleware.StructuredLoggingMiddleware",     # ②
    "plane.base.middleware.RateLimitHeaderMiddleware",       # ③
    "plane.base.middleware.AuditContextMiddleware",          # ④
    "plane.base.middleware.ResponseEnvelopeMiddleware",      # ⑤
    "plane.base.middleware.MaintenanceModeMiddleware",       # ⑥
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# ── DRF：异常处理器 / 事务 / 默认认证 ────────────────────
REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "plane.base.handlers.envelope_exception_handler",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "plane.app.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "ATOMIC_REQUESTS": True,                       # §10.5：单资源写操作默认事务包裹
    "DEFAULT_PAGINATION_CLASS": "plane.base.paginator.CursorPagination",
    "PAGE_SIZE": 100,
    "DEFAULT_THROTTLE_CLASSES": [],               # INFRA-005 填充
}

# ── CORS：精确白名单，禁止 "*"（§13.4）────────────────────
CORS_ALLOWED_ORIGINS = [o.strip() for o in env("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
CORS_ALLOW_CREDENTIALS = True                     # Session 认证需要
CORS_ALLOW_METHODS = ["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"]
CORS_ALLOW_HEADERS = ["Content-Type", "X-CSRFToken", "X-API-Key",
                      "Authorization", "If-Match", "Idempotency-Key"]
CORS_EXPOSE_HEADERS = ["X-Request-Id", "X-RateLimit-Limit", "X-RateLimit-Remaining",
                       "X-RateLimit-Reset", "ETag", "Location", "Retry-After"]

# ── 数据层 / 队列 / 对象存储（变量名与 INFRA-002 compose 对齐）──
DATABASES = {"default": env("DATABASE_URL") and _parse_db_url(env("DATABASE_URL"))
             or {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
REDIS_URL = env("REDIS_URL", "redis://localhost:6379/0")
CELERY_BROKER_URL = env("CELERY_BROKER_URL", "amqp://guest:guest@localhost:5672//")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", f"{REDIS_URL}".replace("/0", "/1"))
AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL", "http://localhost:9000")
AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY", "")
AWS_S3_BUCKET_NAME = env("AWS_S3_BUCKET_NAME", "rp-uploads")

# ── SMTP：P1 可空 = 邮件降级为日志投递（BR-14，IT-05）──────
SMTP_HOST = env("SMTP_HOST", "")
EMAIL_FROM = env("EMAIL_FROM", "noreply@example.com")

# ── 维护模式开关（⑥ 号中间件消费）────────────────────────
MAINTENANCE_MODE = env_bool("MAINTENANCE_MODE", False)
```

```python
# apps/api/plane/settings/dev.py —— 本地容器默认值
from .base import *          # noqa: F401,F403

DEBUG = True
CORS_ALLOWED_ORIGINS = ["http://localhost:3000", "http://localhost:3001",
                        "http://localhost:3002", "http://localhost:3003"]
# 开发态：Envelope 中间件对漏包装直接抛错（⑤ 号 read：settings_debug()）
ENVELOPE_STRICT = True
```

```python
# apps/api/plane/settings/prod.py —— 安全收紧 + 9 项必填启动校验
import os
from django.core.exceptions import ImproperlyConfigured

from .base import *          # noqa: F401,F403

DEBUG = False

# ── 9 项必填：import 即校验（manage.py 任何命令快速失败，BR-13）──
# 注意：DATABASE_URL / CELERY_BROKER_URL / AWS_SECRET_ACCESS_KEY 在 base.py 设有默认值，
# 仅靠 POSTGRES_PASSWORD / RABBITMQ_DEFAULT_PASS / MINIO_ROOT_PASSWORD 校验会让
# 用户覆盖连接串时绕过 BR-13（base.py 默认值会静默接管，落到本地 sqlite / 内存 broker），
# 因此三者必须同时列入 REQUIRED_ENV。
REQUIRED_ENV = [
    "SECRET_KEY",             # 会话签名
    "POSTGRES_PASSWORD",      # 数据库密码（compose 同名变量，§2.5 of INFRA-002）
    "DATABASE_URL",           # 数据库连接串（必须显式给出，禁止默认 sqlite 回退）
    "RABBITMQ_DEFAULT_PASS",  # 消息队列密码
    "CELERY_BROKER_URL",      # Celery broker 连接串（必须显式给出，禁止默认 localhost 回退）
    "MINIO_ROOT_PASSWORD",    # 对象存储密码
    "AWS_SECRET_ACCESS_KEY",  # S3 签名密钥（必须显式给出，禁止空串回退导致上传 403）
    "CORS_ALLOWED_ORIGINS",   # 精确白名单（禁止回退 * ）
    "APP_BASE_URL",           # Cookie Domain / 绝对链接推导
]
_missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
if _missing:
    raise ImproperlyConfigured(
        f"prod 配置缺少必填环境变量：{_missing}。请对照 .env.example 补齐后重启（BR-13）。")

# ── 安全头（§13.4；proxy 层另有 HSTS 等注入，此处为应用层兜底）──
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"
```

**四文件分层规则**：

| 文件 | 职责 | 禁止事项 |
| --- | --- | --- |
| `__init__.py` | 分发 + 防呆（非法模块名断言） | 不放任何配置 |
| `base.py` | 全部公共配置 + 六件套中间件 + DRF 挂载 | 不做环境判断（`if DEBUG` 只出现在 dev/prod） |
| `dev.py` | DEBUG=True、CORS 放宽、Envelope 严格模式 | 不覆盖数据库指向 |
| `prod.py` | 9 项必填校验 + 安全头 + Cookie 加固 | 不给任何必填项写默认值（写了等于绕过 BR-13） |

### 4.9 `.env.example`（唯一模板，全量注释版）

```bash
# =====================================================================
# RabbitProjects 环境变量模板 —— 唯一事实来源（BR-15）
# 约定：代码读取的变量集合 ⊆ 本文件键集合（UT-09 扫描断言）；
#      变量名与 deploy/compose/docker-compose.yml 插值一一对应（INFRA-002 §2.5）。
# 标注 [prod 必填] 的 9 项缺失时 prod 拒绝启动（BR-13）。
# =====================================================================

# ── Django 核心 ──────────────────────────────────────────────────
# 会话签名密钥。[prod 必填] 生成：python -c "import secrets;print(secrets.token_urlsafe(60))"
SECRET_KEY=
# settings 分发：plane.settings.dev | plane.settings.prod
DJANGO_SETTINGS_MODULE=plane.settings.dev
# 逗号分隔的 Host 白名单
ALLOWED_HOSTS=localhost,127.0.0.1
# 站点对外绝对地址（Cookie Domain / 重置密码链接推导）。[prod 必填]
APP_BASE_URL=http://localhost
# CORS 精确白名单，逗号分隔，禁止 *。[prod 必填]
CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3001
# 维护模式总开关（⑥ 号中间件）
MAINTENANCE_MODE=false

# ── 数据层（compose 服务名互联；本地裸跑改为 localhost）──────────
POSTGRES_USER=plane
POSTGRES_PASSWORD=            # [prod 必填]
POSTGRES_DB=rabbit_projects
DATABASE_URL=postgresql://plane:${POSTGRES_PASSWORD}@db:5432/rabbit_projects
REDIS_URL=redis://redis:6379/0

# ── 消息队列（Celery 唯一 broker；Redis 仅 result backend）────────
RABBITMQ_DEFAULT_USER=plane
RABBITMQ_DEFAULT_PASS=       # [prod 必填]
CELERY_BROKER_URL=amqp://plane:${RABBITMQ_DEFAULT_PASS}@mq:5672//
CELERY_RESULT_BACKEND=redis://redis:6379/1

# ── 对象存储（MinIO / S3 兼容，预签名直传）──────────────────────
MINIO_ROOT_USER=rp-minio
MINIO_ROOT_PASSWORD=         # [prod 必填]
AWS_S3_ENDPOINT_URL=http://minio:9000
AWS_ACCESS_KEY_ID=${MINIO_ROOT_USER}
AWS_SECRET_ACCESS_KEY=${MINIO_ROOT_PASSWORD}
AWS_S3_BUCKET_NAME=rp-uploads

# ── SMTP（P1 可空 = 邮件功能降级为日志投递，BR-14）───────────────
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
EMAIL_FROM=noreply@example.com

# ── Web（构建期内联进 bundle，严禁放密钥 —— INFRA-001 §4.10 红线）─
VITE_API_BASE_URL=/api/v1
VITE_LIVE_BASE_URL=/live
```

**环境变量变更流程**（BR-15 的操作面）：

| 变更 | 必须同步的三处 | 校验 |
| --- | --- | --- |
| 新增 | `.env.example` + `settings/base.py` 读取 + compose 注入（若容器需要） | 同一 PR；UT-09 |
| 删除 | 保留读取代码一个迭代期 + `Deprecation` 日志 → 下迭代删除 | — |
| 密钥类 | 禁止提交真实值；compose 用 `${VAR:?}` 强制插值 | CI 密钥扫描 |

### 4.10 Nginx 网关层统一错误 JSON（`apps/proxy`）

网关在应用不可达时直接产生响应，**绕过了 Django**——因此必须在 Nginx 层复制同一信封与错误码（C1 的网关侧落地）。错误码对齐 §8.4 / §8.6：

```nginx
# apps/proxy/conf.d/api.conf（错误处理相关节选，完整路由分发见 INFRA-002 §4.7）
server {
    listen 80;
    server_name _;

    # api 请求体上限 2MB（附件走 MinIO 预签名直传不经此通道，§2.6 边界表）
    client_max_body_size 2m;

    # ── 413：请求体过大 → VALIDATION_PAYLOAD_TOO_LARGE（§8.4）──
    error_page 413 = @payload_too_large;
    location @payload_too_large {
        default_type application/json;
        return 413 '{"status":"error","error":{"code":"VALIDATION_PAYLOAD_TOO_LARGE","message":"请求体过大，请使用附件直传通道"}}';
    }

    # ── 502：上游不可达 → SERVER_EXTERNAL_SERVICE_ERROR（§8.6）──
    error_page 502 = @upstream_error;
    location @upstream_error {
        default_type application/json;
        return 502 '{"status":"error","error":{"code":"SERVER_EXTERNAL_SERVICE_ERROR","message":"服务暂不可用，请稍后重试"}}';
    }

    # ── 503：维护 / 拒绝服务 → SERVER_MAINTENANCE（§8.6）──
    error_page 503 = @maintenance;
    location @maintenance {
        default_type application/json;
        add_header Retry-After 300 always;
        return 503 '{"status":"error","error":{"code":"SERVER_MAINTENANCE","message":"系统维护中，请稍后再来"}}';
    }

    # ── 504：上游超时 → SERVER_TIMEOUT（§8.6）──
    error_page 504 = @upstream_timeout;
    location @upstream_timeout {
        default_type application/json;
        return 504 '{"status":"error","error":{"code":"SERVER_TIMEOUT","message":"请求超时，请重试"}}';
    }

    location /api/ {
        proxy_pass http://api:8000;
        proxy_read_timeout 120s;
        proxy_connect_timeout 5s;
        # 透传客户端 X-Request-Id（合法 ULID 才会被 ① 号中间件采用）
        proxy_set_header X-Request-Id $http_x_request_id;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

**网关信封与应用信封的差异说明**：网关层响应**无法携带 `request_id`**（请求尚未到达应用，① 号中间件未执行）。这是可接受的降级——网关错误本身可按 `code` 归类（上游存活 / 超时 / 维护），运维侧另有 Nginx error_log 可查。前端对 `request_id` 字段做了可选处理（§4.11 的类型定义中为 `request_id?: string`）。

### 4.11 前端消费闭环

#### 4.11.1 类型契约（`@rp/types`）

```ts
// packages/types/src/api/envelope.ts —— 与 §4.1/§4.2 逐字段对齐
export type ApiFieldError = { field: string; code: string; message: string };

export type ApiErrorBody = {
  code: string;                 // 具体值域见 error-codes.ts（gen:api-types 同源生成）
  message: string;
  details?: ApiFieldError[];
  request_id?: string;          // 可选：网关层响应无此字段
  doc_url?: string;
};

export type ApiSuccess<T> = { status: "success"; data: T; meta?: Record<string, unknown> };
export type ApiError = { status: "error"; error: ApiErrorBody };
export type ApiResponse<T> = ApiSuccess<T> | ApiError;
```

`ErrorCode` 常量由前端**手工维护**于 `packages/types/src/api/error-codes.ts`（与 [`api-conventions.md`](../architecture/api-conventions.md) §8.9 的手写示例一致）；同时 `pnpm gen:api-types` 从 OpenAPI `ErrorEnvelope` 组件的 enum 派生一个**机器可读的影子枚举** `packages/types/src/generated/error-codes.ts`（§4.12），由 `scripts/check-error-codes.mjs`（§4.11.3）三方 diff 保证「架构文档 §8 / 后端注册表 / 前端手写常量 / 影子枚举」四者集合一致——手写版本作为唯一可读的对外契约，影子枚举仅做 CI 校验用途。这是 Plane 漂移教训与 §8.1「脚本校验一致性」的折中：手写便于 IDE 跳转与代码评审，影子枚举 + 三方 diff 防止漂移（§6.1）。

#### 4.11.2 axios 响应拦截器（错误分派 + 成功解包）

```ts
// apps/web/src/lib/api/interceptors/error.ts
import axios, { AxiosError, type AxiosResponse } from "axios";
import type { ApiError, ApiSuccess } from "@rp/types";

// ── 成功拦截：解包信封，业务代码直接拿 data / meta ──
axios.interceptors.response.use((res: AxiosResponse<ApiSuccess<unknown>>) => {
  if (res.data?.status === "success") {
    (res as any).data = res.data.data;          // 解包 data
    (res as any).meta = (res as any).meta;       // meta 挂在扩展字段（分页等场景用）
  }
  return res;
}, (err: AxiosError<ApiError>) => {
  const body = err.response?.data;
  const code = body?.error?.code;
  const requestId = body?.error?.request_id;

  switch (true) {
    // ① 认证失效：清理本地态 → 跳登录（保留 next 回跳）
    case code === "AUTH_REQUIRED" || code === "AUTH_SESSION_EXPIRED":
      authStore.resetAndRedirectToLogin();
      break;

    // ② CSRF 失败：重取 token 自动重试一次（且仅一次）
    case code === "AUTH_CSRF_FAILED" && !err.config?.__csrf_retried:
      return refreshCsrfToken().then(() => {
        err.config!.__csrf_retried = true;
        return axios.request(err.config!);
      });

    // ③ 校验错误：转为 FormFieldError 交给表单（§3.3），不弹全局 Toast
    case code === "VALIDATION_ERROR" || code?.startsWith("VALIDATION_"):
      return Promise.reject(new FormFieldError(body!.error));

    // ④ 限流：Toast 展示等待秒数；幂等方法自动指数退避（§7.4）
    case code === "RATE_LIMIT_EXCEEDED":
      toast.warning(`请求过于频繁，请 ${retryAfterSeconds(err)} 秒后重试`);
      if (isIdempotent(err.config?.method) || err.config?.headers?.["Idempotency-Key"]) {
        return backoffRetry(err);               // 1s → 2s → 4s（±20% 抖动），最多 3 次
      }
      break;

    // ⑤ 服务端错误：通用 Toast + 追踪号（P2 接 Sentry 后附事件 ID）
    case code?.startsWith("SERVER_"):
      toast.error(`服务器开小差了，请稍后重试`, { traceId: requestId?.slice(0, 8) });
      break;

    // ⑥ 权限类：不弹全局 Toast，交调用方渲染局部空态（避免权限探测弹窗风暴，§8.9）
    case code?.startsWith("PERM_"):
      break;

    default:
      toast.error(body?.error?.message ?? "请求失败");
  }
  return Promise.reject(err);
});

const retryAfterSeconds = (err: AxiosError) =>
  Number(err.response?.headers?.["retry-after"]) || 3;
```

**分派表与 §8.9 逐条对齐**（P1 生效子集；OAuth / license 类码 P2+ 由同表扩展）：

| 错误码类别 | 统一动作 |
| --- | --- |
| `AUTH_*`（401） | 清理本地用户态 → 跳登录（保留回跳） |
| `AUTH_CSRF_FAILED` | 重取 CSRF token 自动重试一次 |
| `PERM_*`（403） | **不弹全局 Toast**，调用方局部空态 |
| `VALIDATION_*`（400） | `details` → `useApiFieldErrors` 落字段 |
| `RESOURCE_CONFLICT`（409） | 冲突对话框（刷新 / 覆盖） |
| `RATE_LIMIT_EXCEEDED`（429） | 指数退避 + Toast |
| `SERVER_*`（5xx） | 全局 Toast + 追踪号（P2 上报 Sentry） |

#### 4.11.3 前后端错误码一致性校验（CI）

```javascript
// scripts/check-error-codes.mjs —— 四方集合 diff，任一差异退出码 1
//   A. docs/architecture/api-conventions.md §8 表格解析出的码集合（唯一事实来源）
//   B. apps/api/plane/base/error_codes.py 的 ErrorCodes 注册表（AST 解析）
//   C. packages/types/src/api/error-codes.ts 的 ErrorCode 常量（前端手写，对外契约）
//   D. packages/types/src/generated/error-codes.ts 的 ErrorCode 常量（gen:api-types 产物，影子枚举）
import { readFileSync } from "node:fs";
import { parse as parsePy } from "python-ast-parser";       // 示意：任一 AST 解析方案

const docCodes   = parseConventionsTable("docs/architecture/api-conventions.md");
const pyCodes    = parseRegistry("apps/api/plane/base/error_codes.py");
const tsCodes    = parseConstEnum("packages/types/src/api/error-codes.ts");
const tsGenCodes = parseConstEnum("packages/types/src/generated/error-codes.ts");

const diff = (label, a, b) => {
  const only = [...a].filter((x) => !b.has(x));
  if (only.length) { console.error(`✗ 仅存在于 ${label}：${only.join(", ")}`); process.exitCode = 1; }
};
diff("架构文档",  docCodes,   pyCodes);    diff("后端注册表", pyCodes,   docCodes);
diff("后端注册表", pyCodes,   tsCodes);    diff("前端常量",   tsCodes,   pyCodes);
diff("后端注册表", pyCodes,   tsGenCodes); diff("影子枚举",   tsGenCodes, pyCodes);
if (!process.exitCode) console.log(`✓ 四方错误码一致（${docCodes.size} 码）`);
```

接入方式：`package.json` 的 `scripts.verify` 链（`pnpm verify` = oxlint + typecheck + test + `check-error-codes`），Husky pre-push 与 CI 均执行（BR-05 / C2 的机器守护）。

### 4.12 OpenAPI 契约（drf-spectacular）

```python
# apps/api/plane/base/openapi.py —— 错误信封统一注册
from drf_spectacular.utils import OpenApiErrorExtension, extend_schema

ERROR_ENVELOPE_COMPONENT = {
    "type": "object",
    "required": ["status", "error"],
    "properties": {
        "status": {"type": "string", "enum": ["error"]},
        "error": {
            "type": "object",
            "required": ["code", "message", "request_id"],
            "properties": {
                "code": {"type": "string",
                         "enum": ["<gen:api-types 从 ErrorCodes.all() 生成>"]},
                "message": {"type": "string"},
                "details": {"type": "array", "items": {"$ref": "#/components/schemas/ApiFieldError"}},
                "request_id": {"type": "string"},
                "doc_url": {"type": "string", "nullable": True},
            },
        },
    },
}
```

- 全部端点的 `@extend_schema(responses={...: ErrorEnvelope})` 引用同一组件——OpenAPI 文档中错误结构**只有一个定义点**（对齐 §10.6「补齐 responses 含错误响应示例」的检查项）。
- `pnpm gen:api-types` 从 `/api/v1/schema/` 生成前端类型，影子枚举 `packages/types/src/generated/error-codes.ts` 即由 `ErrorEnvelope` 组件的 `enum` 派生——形成「后端注册表 → OpenAPI → 影子枚举」单向生成链；前端手写常量 `packages/types/src/api/error-codes.ts` 作为对外契约的可读副本，由 `check-error-codes.mjs` 三方 diff 保证「架构文档 §8 / 后端注册表 / 前端手写常量 / 影子枚举」四者集合一致（与 §4.11.1 对齐）。

### 4.13 响应信封全示例（七类形态）

以下示例均为可复制的 curl 级真实形态（合成数据），覆盖 §2.3 决策表的全部出口：

**① 成功信封（详情）**

```json
{ "status": "success",
  "data": { "id": "8a1f9c2e-6b3d-4a7e-9f11-2c4d5e6f7a8b", "name": "支持看板卡片批量拖拽" } }
```

> 详情端点按 §4.1 表 `meta` 为「⭕」可省略；`success_response(..., meta=None)` 不输出 `meta` 键（与 `meta=null` 不同——后者是显式空值）。`ApiSuccess<T>` 类型上 `meta?: Record<string, unknown>` 也对应「键可缺失」语义。

**② 成功信封（列表，meta 必填）**

```json
{ "status": "success",
  "data": [ { "id": "…", "name": "…" } ],
  "meta": { "next_cursor": "100:1:0", "prev_cursor": "100:0:1",
            "next_page_results": true, "prev_page_results": false,
            "count": 100, "total_count": 1247, "total_pages": 13,
            "page": 1, "per_page": 100 } }
```

**③ 校验错误（400，details 平铺含嵌套点号路径）**

```json
{ "status": "error",
  "error": { "code": "VALIDATION_ERROR", "message": "请求参数校验失败",
    "details": [
      { "field": "name", "code": "REQUIRED", "message": "该字段为必填项" },
      { "field": "target_date", "code": "INVALID_DATE_RANGE", "message": "截止时间不能早于开始时间" },
      { "field": "assignee_ids.0", "code": "DOES_NOT_EXIST", "message": "所选负责人不是项目成员" }
    ],
    "request_id": "01JBX3K9Q7ZR4M8N2P5V6W7X8Y",
    "doc_url": "https://docs.example.com/api/errors#validation-error" } }
```

**④ 权限 / 资源（403 与 404 同构对照）**

```json
// 403：可见但不可操作
{ "status": "error",
  "error": { "code": "PERM_ROLE_INSUFFICIENT", "message": "当前角色权限不足",
             "request_id": "01JBX3K9Q7ZR4M8N2P5V6W7X8Z" } }
// 404：不存在或不可见（二者字节级同构，仅 request_id 不同 —— IT-02）
{ "status": "error",
  "error": { "code": "RESOURCE_NOT_FOUND", "message": "资源不存在或你没有访问权限",
             "request_id": "01JBX3K9Q7ZR4M8N2P5V6W7X9A" } }
```

**⑤ 唯一性冲突（409，IntegrityError 转换）**

```json
{ "status": "error",
  "error": { "code": "RESOURCE_ALREADY_EXISTS", "message": "该名称已被使用",
    "details": [ { "field": "identifier", "code": "UNIQUE", "message": "该缩写已存在" } ],
    "request_id": "01JBX3K9Q7ZR4M8N2P5V6W7X9B" } }
```

**⑥ 限流（429，头 + 体双通道）**

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 23
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1788230400
X-Request-Id: 01JBX3K9Q7ZR4M8N2P5V6W7X9C
```

```json
{ "status": "error",
  "error": { "code": "RATE_LIMIT_EXCEEDED", "message": "请求过于频繁，请在 23 秒后重试",
    "details": [ { "field": "retry_after", "code": "RETRY_AFTER", "message": "23" } ],
    "request_id": "01JBX3K9Q7ZR4M8N2P5V6W7X9C" } }
```

**⑦ 未捕获异常（500，脱敏 + 追踪号）**

```json
{ "status": "error",
  "error": { "code": "SERVER_ERROR", "message": "服务器开小差了，请稍后重试",
             "request_id": "01JBX3K9Q7ZR4M8N2P5V6W7X9D" } }
```

> 响应体内**没有**堆栈、SQL、文件路径；完整堆栈在 `docker compose logs api` 中按同一 `request_id` 检索（§3.6 报障路径）。

**⑧ `204`（唯一例外：体为空）**

```http
HTTP/1.1 204 No Content
X-Request-Id: 01JBX3K9Q7ZR4M8N2P5V6W7X9E
```

---

## 5. 测试用例

### 5.1 单元测试

| 用例 ID | 测试目标 | 输入 | 预期输出 | 覆盖类型 |
| --- | --- | --- | --- | --- |
| UT-01 | 注册表与架构文档 §8 集合一致 | 解析 `api-conventions.md` §8 全表 vs `ErrorCodes.all()` | 集合完全相等（75 码，多一少一均失败） | 一致性（C2） |
| UT-02 | 未注册码抛异常 | `AppException("NOT_EXIST")` | `KeyError` 且提示登记流程 | 防御 |
| UT-03 | ValidationError 嵌套平铺 | `ValidationError({"a": {"0": [{"b": [ErrorDetail(…)]}]}})` | `details=[{field:"a.0.b",…}]` | 正常 |
| UT-04 | 500 脱敏 | 视图抛 `RuntimeError("db password is xxx")` | 响应体无该字符串；error 日志含堆栈 | 安全（BR-08） |
| UT-05 | request_id 生成与三处一致 | 不带头的请求 | `X-Request-Id` 头 = `error.request_id`（错误时）= access 日志值，且为合法 26 位 ULID | 正常（C3） |
| UT-06 | prod 缺 9 项必填拒绝启动 | `DJANGO_SETTINGS_MODULE=plane.settings.prod` + 空变量 | `ImproperlyConfigured` 列出全部缺失项 | 边界（BR-13） |
| UT-07 | 信封兜底与 204 例外 | 视图返回裸 dict；另一视图返回 204 | dev 态裸 dict 抛 `RuntimeError`；204 体为空不被包装 | 正常（C1/BR-02） |
| UT-08 | 日志超长截断 | 记录 8KB+ body 字段 | 单条序列化后 ≤ 8KB，body 截为 512 字符 + `…` | 边界（BR-10） |
| UT-09 | `.env.example` 与代码读取集合一致 | AST 扫描全部 `os.environ.get` 调用 | 读取集合 ⊆ 模板键集合 | 一致性（BR-15） |
| UT-10 | IntegrityError → 409 | 触发 `uniq_workspace_slug_alive` | `RESOURCE_ALREADY_EXISTS` + `details.field="slug"` | 异常 |
| UT-11 | 日志脱敏 | 结构化记录 `{"password": "x", "token": "y", "name": "ok"}` | 输出中 password/token 为 `***`，name 保留 | 安全（BR-11） |
| UT-12 | 非法 X-Request-Id 重生成 | 携带 `X-Request-Id: abc\nDEF`（含换行注入） | 响应头为新 ULID，日志无注入字符 | 安全（边界表） |

### 5.2 集成测试

| 用例 ID | 场景 | 前置条件 | 操作步骤 | 预期结果 |
| --- | --- | --- | --- | --- |
| IT-01 | 全路由信封快照 | 全部 P0+P1 路由注册 | 对每个路由以未登录态 GET + 制造 400/404 | 响应体均为 `{status:"error"…}`，无 DRF 原生 `{"detail"}` 结构；快照存档 |
| IT-02 | 404 与 403 同构 | 用户 A、B 两账号 | A 访问 B 的私密资源；A 访问随机不存在 ID | 两者响应**除 request_id 外字节级一致**；响应时间差 < 20ms（防时序侧信道） |
| IT-03 | 500 时 request_id 可检索 | 人为注入抛 `RuntimeError` 的调试路由 | 触发后用返回的 request_id 检索 `docker compose logs api` | 恰好命中 1 条 access + 1 条 error（含堆栈）日志 |
| IT-04 | Nginx 413 统一 JSON | 3MB 请求体直发 proxy | POST `/api/v1/…` | 413 + `VALIDATION_PAYLOAD_TOO_LARGE` 信封 |
| IT-05 | SMTP 未配置降级 | `SMTP_HOST` 为空 | 触发忘记密码（`AUTH-004`） | 接口 202 正常；`plane.app.mail` channel 留降级日志；重置链接可从日志取回（开发态） |
| IT-06 | 幂等键重放 | 同 `Idempotency-Key` 二次 POST 创建 | 断言两次响应 | 响应体一致 + `Idempotency-Replayed: true`（P2 起在 Open API 创建端点强制；P1 在任意一个创建端点手工挂中间件做端到端验证即可，`TASK-001` 暂未启用此能力） |
| IT-07 | 维护模式 | `MAINTENANCE_MODE=true` 重启 | 访问任意业务端点 / `/api/v1/health/` | 前者 503 `SERVER_MAINTENANCE` + `Retry-After: 300`；后者 200 放行 |
| IT-08 | CORS 白名单 | Origin 为白名单内 / 白名单外 | 预检 OPTIONS | 前者放行且 `Access-Control-Allow-Credentials: true`；后者无 CORS 头（非 `*` 回退） |

### 5.3 E2E 测试

| 用例 ID | 用户场景 | 操作路径 | 验收标准 |
| --- | --- | --- | --- |
| E2E-01 | 用户提交非法表单 | 创建项目 → 清空名称、填非法日期提交 | 名称与日期输入框分别红字；无白屏；无全局 Toast 弹窗 |
| E2E-02 | 用户触发 500 | 调试开关注入异常 → 打开任务详情 | Toast 含追踪号前 8 位且 ≥ 10s 可读；点「复制」得完整 ULID；刷新后系统仍可用 |
| E2E-03 | 会话过期体验 | 手动删除 Session cookie 后操作 | 无报错弹窗；静默跳登录页且登录后回到原页面（`next` 回跳） |

---

## 6. 竞品深度对标

### 6.1 Plane 实现分析

基于 Plane 开源版 master 分支的具体观察：

| 维度 | Plane 的实现 | 具体表现 | 代价 |
| --- | --- | --- | --- |
| 配置 | `apps/api/plane/settings/common.py` 从 `os.environ` 直读约 40 项变量，**无启动校验** | 漏配 `WEB_URL` 之类变量在首次用到时才爆（如生成重置密码链接时） | 生产「带病启动」，故障点漂移到随机代码路径，排障成本高 |
| 响应格式 | **不统一**：多数端点直接返回资源对象或裸数组；分页端点返回带分页键对象；部分模块（如 session 认证）自定义 `{error: "..."}` 结构 | 同一个「错误」在 `/api/v1/auth/sign-in/` 与 `/api/v1/workspaces/…/issues/` 的结构不同 | 前端每个 service 函数都要写一遍「判断结构在哪」的适配逻辑 |
| 错误码 | 无系统化机器码，主要靠 HTTP 状态码 + 文案（`detail` 字段），部分端点有零散数字 `error_code` | 前端只能匹配文案分支；国际化或文案改版即静默破坏前端逻辑 | 这是「极难发现的一类缺陷」 |
| 异常收口 | `EXCEPTION_HANDLER` 有挂载，`base/exception.py` 有 `AppException` 基类，但**未全局收口** | `Http404` / `PermissionDenied` 在部分路径仍以 DRF 原生 `{"detail": "Not found."}` 漏出 | 与自定义错误结构并存，客户端需双轨解析 |
| 请求追踪 | 无统一 request_id 回传 | 社区版排障主要靠容器 stdout 肉眼扫日志 | 多人协作场景下无法把一次用户报障定位到具体请求 |
| 日志 | 默认 Django 文本日志 | 无结构化字段 | 无法直接接入 Loki/ELK 等日志系统做检索聚合 |

**Plane 值得沿用的一点**：环境变量即配置的 Docker 心智——配置项与应用代码同仓库演进，部署无需额外系统。本系统完整保留（`.env.example` 单一模板）。

### 6.2 Ones 实现分析

Ones 作为商业闭源产品，其公开材料与企业版能力清单显示的工程基座：

| 能力 | Ones 企业版 | 本系统 P1 对应 |
| --- | --- | --- |
| 配置中心 | 租户级配置下发、灰度开关、变更审批 | `.env` + settings 四文件 + 9 项启动校验（**结果体验对齐：配置可审计**；多租户下发留 P4） |
| 日志采集 | 分级采集、按租户隔离、可对接企业 SIEM | structlog JSON 行 + request_id 贯穿（**结果体验对齐：故障可定位**；SIEM 对接留 P3 `AUTH-010`） |
| 故障定位 | 工单系统与日志链路打通 | §3.6 报障路径：用户凭追踪号 → 日志检索（最小可行版） |
| 合规 | 审计级留痕、留存周期策略 | `AuditContextMiddleware` 预埋 contextvar，P3 落独立审计表 |

Ones 的这些能力依赖多租户与合规体系（P3/P4 前置），2 人团队在 P1 复刻其**实现**不现实；但以轻量方案复刻其**结果体验**（配置可审计、故障可定位）完全可行，这正是本系统的取舍。

### 6.3 本系统设计决策

1. **契约先行、代码收口**：[`api-conventions.md`](../architecture/api-conventions.md) §8 是错误码唯一事实来源，后端注册表、前端常量、OpenAPI 组件三方由 `check-error-codes.mjs` + UT-01 双重校验一致——比 Plane 多出完整的防漂移机制，错误码漂移从「线上事故」降级为「CI 失败」。
2. **快速失败**：prod 9 项必填环境变量在 `import plane.settings.prod` 时即校验（`manage.py` 任何命令快速失败），杜绝「带病启动」——直接吸取 Plane `common.py` 无校验的教训。
3. **request_id 全链路贯穿**（C3）：ULID 生成于最外层中间件，同时出现在响应头、错误体、access/error 两类日志；P1 不引入 Sentry 即可完成端到端故障定位（IT-03 演练），并为 P2 Sentry 接入预留 `error_code` 维度与 `request_id` 锚点。
4. **网关与应用同构**：Nginx 层 413/502/503/504 复制同一信封与注册表错误码——「不存在绕过信封的端点」在网关侧同样成立，这是对 Plane（网关错误为默认 HTML 页）的显式改进。
5. **差异化价值**：以 2 人团队的投入逼近 Ones 的「配置可审计、故障可定位」体验——JSON 结构化日志、环境变量单一模板、启动校验、三方码表校验，全部是标准版即内置、不额外收费的工程底座；这些能力在竞品中要么缺失（Plane），要么锁定在企业版（Ones）。

---

## 7. 里程碑与验收

### 7.1 交付物清单

| 类型 | 交付物 |
| --- | --- |
| Model / Migration | 无新增表 |
| 后端 | `plane/base/`：`error_codes.py`（75 码注册表 + 默认文案）、`exception.py`（`AppException`）、`handlers.py`（十步收敛 + `CONSTRAINT_MAP`）、`response.py`（信封装配）、`middleware.py`（六件套）、`request_context.py`（contextvar）；`plane/logging.py`（structlog 配置） |
| settings | `{__init__,base,dev,prod}.py` 四文件（六件套中间件顺序挂载、REST_FRAMEWORK、CORS、9 项必填校验、安全头） |
| 网关 | `apps/proxy/conf.d/api.conf`：413 / 502 / 503 / 504 统一 JSON + `X-Request-Id` 透传 |
| 前端 | axios 成功解包 + 错误分派拦截器、`ErrorToast`（含追踪号复制）、`useApiFieldErrors`、404/403/500 空态页、429 退避 |
| 契约 | drf-spectacular `ErrorEnvelope` 组件、`pnpm gen:api-types` 生成 `ErrorCode`、`scripts/check-error-codes.mjs` 三方校验 |
| 模板 | `.env.example` 全量注释版 |
| 测试 | UT-01~12、IT-01~08、E2E-01~03 |

### 7.2 可操作演示的验收标准

1. **信封无例外**：对系统任意端点制造 404 / 403 / 400 / 409 / 429 / 500，curl 响应体均为统一信封，`error.code` 在注册表 75 码之内；`IT-01` 全路由快照在 CI 全绿（C1）。
2. **一次报障一次定位**：用 500 响应中的 `X-Request-Id` 在 `docker compose logs api` 检索，同时命中 access 与 error（含堆栈）两条日志，`request_id` 三处同值（C3 / IT-03）。
3. **快速失败**：删除 `.env` 中 `SECRET_KEY` 后以 prod 配置启动，进程立即报 `ImproperlyConfigured` 并列出全部缺失项；补齐后正常启动（BR-13 / UT-06）。
4. **邮件降级不阻塞**：`SMTP_HOST` 为空时忘记密码流程接口 202 正常、邮件动作降级为 `plane.app.mail` 日志（BR-14 / IT-05）。
5. **网关同构**：3MB 请求体直发 proxy 得 413 `VALIDATION_PAYLOAD_TOO_LARGE` 信封；停止 api 容器后请求得 502 `SERVER_EXTERNAL_SERVICE_ERROR` 信封（IT-04）。
6. **错误码防漂移**：在注册表手工添加一个未在架构文档登记的码，`pnpm verify` 立即失败并指出差异码（C2 / UT-01）。
7. **前端零样板**：任一业务表单提交非法值，字段红字定位准确且无全局弹窗；屏幕阅读器播报错误（E2E-01 + §3.7 无障碍）。
```
