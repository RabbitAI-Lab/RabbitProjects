# ADR-0012 · Sprint 1 实现偏差登记

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-09-04 |
| 状态 | 进行中（Sprint 1 实现期间持续追加，收口时定稿） |
| 背景 | Sprint 1 的 11 份规格文档全部通过 5 维度评审（≥9.5）后进入实现。实现过程中出现三类偏差：① 文档显式要求由本迭代发起 ADR 的命名收口；② 文档与 sprint-0 既有实现冲突、需破坏性变更；③ 文档描述在当前依赖版本下不可直接落地。本文逐条登记，供后续 Sprint 回改架构文档 |
| 相关 | [ADR-0001](./0001-sprint-0-impl-deviations.md)（Sprint 0 偏差）、[ADR-0010](./0010-ui-parity-discipline.md)（UI parity 纪律）、[ADR-0011](./0011-sprint-1-cross-doc-ui-adjudication.md)（跨文档 UI 裁决） |

> 登记口径同 ADR-0001：**实现与文档不一致时先登记再继续**，不得以临时实现替代架构决策。标注「架构文档待回改」的条目需在后续 Sprint 回写 `docs/architecture/`。

## A 类 · 文档显式要求的收口

| # | 偏差 | 文档依据 | 决策 | 影响面 |
| --- | --- | --- | --- | --- |
| A1 | settings 模块命名收口：`common.py`→`base.py`、`local.py`→`dev.py`、新增 `prod.py`、`test.py` 改为从 `dev` 继承 | INFRA-004 §1.3 落位说明 2：「该命名收口在 INFRA-003 验收通过后由本文档统一发起 ADR」 | 按 INFRA-004 §4.8 执行重命名 | `manage.py` / `wsgi.py` / `asgi.py` / `celery.py` / `Dockerfile` / `deploy/compose/docker-compose.yml`（4 处）/ `pyproject.toml` / `tests/run-ci-checks.sh` 的 TC-INF3-002 路径 |
| A2 | 框架层落位 `plane/base/`（error_codes / exception / handlers / response / middleware / request_context） | INFRA-004 §1.3 落位说明 1 | 采纳，作为与 `plane/app/` 平行的第六个包 | **架构文档待回改**：`api-conventions.md` §10.4 的 `plane/utils/exception_handler.py`、`monorepo-structure.md` §2 的 `utils/`/`middleware/` 命名 |

## B 类 · 破坏性变更（与 sprint-0 既有实现冲突）

| # | 偏差 | 决策 | 影响面 |
| --- | --- | --- | --- |
| B1 | **响应信封形态变更**：sprint-0 为 `{"status": true/false, "data", "meta"}`（布尔）；INFRA-004 C1 要求 2xx `{"status":"success","data","meta"?}`、4xx/5xx `{"status":"error","error":{code,message,details?,request_id,doc_url?}}` | 按 C1 全量迁移，无兼容期。`ResponseEnvelopeMiddleware` 在 dev 下对未包装的 2xx JSON 直接抛错，防止漏改 | 全部既有 views/serializers；前端 `apps/web/app/services/axios.ts` 两个拦截器；`tests/jmeter/*.py`、`tests/e2e/no-console-errors.ts`、`tests/e2e/coverage.spec.ts` |
| B2 | **`/api/v1/health/` 纳入统一信封**：由 `{"status":"ok","checks":{"db":"ok"}}` 改为 `{"status":"success","data":{"checks":{"db":"ok"}}}` | C1 明确「不存在任何第三种结构」，健康端点不豁免（§4.6 的白名单只针对**维护模式**，与信封无关）。compose 探针用 `curl -f` 仅判状态码，不受影响 | **架构文档已对齐**：INFRA-002 §4.10 §1323 已写 `{"status":"success","data":{"status":"ok","checks":{…}}}` 形态；§1327 已登记健康端点的 503 映射例外（与本实现完全一致，**D5 修正：无需再回写**）。`tests/e2e/PG_README.md` 已同步 |
| B3 | 错误码收敛到 75 码注册表：sprint-0 自造的 `AUTH_EMAIL_EXISTS` / `PROJECT_IDENTIFIER_EXISTS` 均未注册，统一改为 `RESOURCE_ALREADY_EXISTS` + `details[{field,code:"UNIQUE"}]`；`PERM_PROJECT_CONTRIBUTOR_REQUIRED` / `PERM_WORKSPACE_MEMBER_REQUIRED` 收敛为 `PERM_ROLE_INSUFFICIENT` | 注册表是唯一事实源，未注册码在 `AppException` 构造期 KeyError | 前端 `projects-list.tsx` 的 identifier 冲突分支：`e.meta.suggestion` → `e.details[0].suggestion`，匹配码改 `RESOURCE_ALREADY_EXISTS` |

## C 类 · 文档不可直接落地 / 实现期修正

| # | 偏差 | 决策 | 备注 |
| --- | --- | --- | --- |
| C1 | INFRA-004 §4.8 指定 `DEFAULT_PAGINATION_CLASS = plane.base.paginator.CursorPagination`，该类尚不存在 | **不设置全局分页类**（sprint-0 亦无，各 view 手工分页）。引入 DRF 默认分页器会静默改变既有列表响应结构 | 待 `CursorPagination` 交付后再切回 |
| C2 | INFRA-004 §4.7 structlog 配置未指定 `logger_factory`，而 processors 用了 `structlog.stdlib.add_logger_name` | 显式设 `logger_factory=structlog.stdlib.LoggerFactory()` | 默认 `PrintLoggerFactory` 产出的 `PrintLogger` 无 `.name`，与 stdlib processor 混用会在**每个请求**上 `AttributeError`（实测已触发） |
| C3 | `request_context._actor_var` 的 ContextVar 默认值取 `None`（ruff B039 禁止可变对象作 default） | 统一走 `current_actor()` 访问器（未绑定时返回 `{}`），禁止直读 `_actor_var.get()` | 中间件里曾直读导致 `None.get()` 崩溃（实测已触发） |
| C4 | `FileAsset` 扩展名白名单 CheckConstraint | 延后到后续迁移用 `RunSQL` 落地 | FILE-001 §4.1.1 要求 PG 原生 `~*`（大小写不敏感），Django ORM `__regex` 只桥接大小写敏感的 `~`。应用层 presign 校验先行覆盖 |
| C5 | TASK-002 §4.1.4 的 `issue_type` 存量回填 `RunPython` 数据迁移 | 未随 `0003_sprint1` 落地 | 需在功能实现时补独立数据迁移 |
| C6 | 模型命名：概览 §4 称 `WorkspaceInvitation`，TEAM-002 §4.1.1 称 `WorkspaceMemberInvite`（含完整字段/索引配方） | 以 **TEAM-002** 为准（§4 是权威模型规格，概览为转述） | **文档待回改**：sprint-1 `sprint-overview.md` §4 的模型名 |
| C7 | `django-environ` 从依赖中移除，settings 改为直读 `os.environ` | 采纳 | `.env` 不再自动加载；本地启动需显式传 `DATABASE_URL` / `SECRET_KEY`（与 CLAUDE.md 既有说明一致） |

## D 类 · 实现期发现并修复的既有缺陷

| # | 缺陷 | 根因 | 修复 |
| --- | --- | --- | --- |
| D1 | **`sort_order` 恒为 65535**，BR-8「末任务追加 = 列尾」完全失效 | `IssueListCreateView.create` 算出了列尾值 `max_order` 却从未传给 service；`create_issue` 调用 `calculate_sort_order(prev_order=None, next_order=payload.pop("next_sort_order"))` 两参皆 None → 恒返回常量 65535。视图里那个局部变量是死代码（ruff F841 暴露） | service 增加 `prev_sort_order` 入参，视图传 `max_order`。实测三次创建得 65535 → 131070 → 196605（严格递增、步长 65535）。该语义是 BOARD-002 筛选后拖拽排序的前提（sprint-overview §9 风险 8） |
| D2 | `ruff check .` 在 main 上即为红（`api-ci.yml` 门禁失效）：5×B904、1×F841、2×EXE002 | 历史遗留 | 全部修绿。B904 用 `raise ... from None`——越权统一 404 是**刻意**屏蔽原异常（AUTH-003 防 ID 枚举），`from None` 正表达该意图 |
| D3 | 429 提示文案叠字「请稍后再再试」 | 历史遗留 | 按 INFRA-004 §4.2 `DEFAULT_MESSAGES` 的 `RATE_LIMIT_EXCEEDED` 文案校正为「请求过于频繁，请稍后重试」 |
| D4 | 前端 `exactOptionalPropertyTypes: true` 下 `ApiError` 可选属性赋 `undefined` 触发 TS2412 | 信封迁移引入 | 属性类型显式并入 `| undefined` |
| D5 | **越权 404 未走统一信封**，返回 DRF 默认的 `{"detail": "RESOURCE_NOT_FOUND"}`，违反 C1 | `handlers.py` 第 6 步只判 `Http404` / `ObjectDoesNotExist`，而代码里抛的是 DRF 的 `rest_framework.exceptions.NotFound`——它是 `APIException` 子类，两个判定都不命中，于是保留 `drf_exception_handler` 的原始输出。`_access.py` 的 6 处越权 404 全走这条路，是全 API 命中率最高的错误出口 | 第 6 步改为 `isinstance(exc, (NotFound, Http404, ObjectDoesNotExist))`。由 `sprint-1-flow.py` ENV-03/04/05 守护 |
| D6 | 附件 presign 端点必 500（`AttributeError: module 'ulid' has no attribute 'new'`） | 仓库锁的依赖是 **python-ulid**（暴露 `ULID` 类），而 `asset.py` 调的是 **ulid-py** 的 `ulid.new().str`——两个同名不同包的 API。`middleware.py` 里已有正确写法 `ulid_new()` | `asset.py` 改为复用 `plane.base.middleware.ulid_new()`，避免第二份实现。由 FILE-03 守护 |
| D7 | 附件 `complete` 返回的 `attachment_count` 比真实值**多 1**（落库值正确，返回值说谎） | 写法是「先 `_current_count()` 再 `+1`」，但那次 count 已经在状态翻转成 `UPLOADED` **之后**执行，本条已被计入，再 `+1` 即重复 | 去掉 `+1`，统一在翻转后统计。由 FILE-06「计数三处一致」守护 |
| D8 | 看板分组各组 `total_results` 之和远小于真实总数（4 条任务只统计出 1） | `base_qs` 已经过 `apply_order()`，`values_list("state_id").annotate(Count("id"))` 会把**排序列一并塞进 GROUP BY**，于是每行自成一组、n 恒为 1，`dict()` 再把同 state 的组覆盖成最后一条。Django 聚合的经典坑 | 聚合前 `.order_by()` 清空排序，并用 `Count("id", distinct=True)` 与上游 `.distinct()` 对齐。由 BRD-04 守护 |
| D9 | 前端 `noUncheckedIndexedAccess` 下角色查表 `Record<number, …>[v]` 为 `T \| undefined` | 角色值来自 API，运行时可能落在矩阵之外 | 不收窄键类型（那与运行时现实不符），改为「宽松查表 + 兜底」的全函数 `wsRole(v)`；`maskEmail` 同理改 `slice` 取子串避开下标 |

## E 类 · 为并行开发所做的结构调整（非文档要求）

| # | 调整 | 理由 |
| --- | --- | --- |
| E1 | 抽取 `plane/app/views/_access.py`，作为 `get_workspace_or_404` / `get_project_or_404` 的唯一定义点；原 `views/workspaces.py` / `views/projects.py` 保留同名再导出以兼容既有 import | sprint-0 把两个 helper 分放在两个 view 模块、彼此跨文件 import 私有函数；sprint-1 新增 9 个功能域 view 模块全部依赖它们 |
| E2 | 路由按功能域拆分到 `plane/app/routes/<域>.py`，各自导出 `urlpatterns`，由 `plane/app/urls.py` 的 `FEATURE_MODULES` 汇总 | sprint-0 全部 path() 平铺在单一 `urls.py`；sprint-1 一次性新增 9 个域的端点，平铺会让并行实现在同一文件互相踩踏 |
| E3 | 权限矩阵落位 `plane/constants/permissions.py`（25 个权限点 + 中文标签 + `threshold_of()`） | AUTH-005 §4.4 指定为「全仓库唯一手写权限点清单」，前后端与 CI 一致性检查共同消费 |
| E4 | 接口契约常量抽到 `tests/jmeter/_contract.py`（HTTP / CODES / ENVELOPE / `Client` / 断言辅助），`tests/jmeter/sprint-1-flow.py` 消费之 | CLAUDE.md 测试脚本规范 ① 要求「API 真相源唯一」，但 `sprint-0-flow.py` 没有 `__main__` 守卫——**一 import 就跑完整条 10 步流程**，导致所谓的唯一真相源实际无法被复用，新脚本只能各自硬编码（正是规范要防的漂移）。新模块只含可 import 的常量与辅助，无自动执行 |
| E5 | 权限矩阵在 AUTH-005 §4.4 的 22 点基础上增补 3 点：`project.member.read` / `project.favorite` / `project.archive`（PROJ-002 §4.2 消费） | AUTH-005 R1 评审发现 ⑤ 已记「project.favorite/archive 未入 P1 矩阵（PROJ-002 前置）」。**架构文档已回写**：AUTH-005 §2.4.2 表 + §4.4 PERMISSION_MATRIX/PERMISSION_LABELS 三处已补行；sprint-overview.md §4 模型名 `WorkspaceInvitation`→`WorkspaceMemberInvite`（C6）已修正；INFRA-002 §4.10 健康端点形态本就是 sprint-0 提前对齐的（B2 标"已对齐"） |
| E6 | 403 路由页 `/403` 移出 `route-groups/permissions.ts`，挂到 `public-extra.ts`（公共 layout）；移除页内 Topbar/Sidebar 引用；`root.tsx` 的 `allowedPublic` 白名单加 `/403` 与 `/invite/:token` | 受 Guard 保护的工作空间 layout 会把未登录直达 `/403` 的用户跳到 `/login`，导致「权限不足」提示页反而要先登录才看得到——**违反 C.14「直达无权 URL 重定向 /403、不白屏」原意**。`/403` 本就是无 workspaceSlug 的裸提示页，挂公共 layout 语义正确 | 由 `parity-sprint1.spec.ts` C.14 守护 |
| E7 | 同步把 `architecture/api-conventions.md` §10.4 / `architecture/monorepo-structure.md` §2 的 `plane/utils/exception_handler.py` `utils/` `middleware/` 几处描述更新为 `plane/base/` | INFRA-004 §1.3 落位说明 1 明确指出 `plane/base/` 是为避免与既有 `utils/`/`middleware/` 命名冲突而新增的第六个框架层，但这两份架构文档早于此变更。本回写让架构文档与实际目录命名一致 |
| E8 | axios 401 → /login 重定向需与 root.tsx `allowedPublic` 对齐；只排除 `/login` 会把未登录访问 `/403`、`/labels-admin`、`/invite/:token` 误拖到登录 | 受中断 agent 添加 `/labels-admin` 公共调试路由后被触发：未登录访问该路由 → 触发 LabelAPI.list → 401 → axios 拦截器跳 /login，模态还没渲染就被踢出。修复后 isPublic 列表与 root.tsx 共享白名单 | 由 `parity-sprint1.spec.ts` C.14 守护（可达即断言路由可解析）|
| E9 | 路由守卫的「登录态」判定只查内存 store（`isBootstrapped && isLoggedIn`），未与 cookie 中的 `sessionid` 一致，导致 e2e 跨 spec 复用 store 时旧 store 显示已登录但后端 401 → 整页跳 login | CLAUDE.md 规范 ③ 实测根因（不同 test 间复用 store 状态）：清除 cookies 后内存仍标「已登录」，直接放行；后续 API 全 401，axios 拦截器 `location.href = "/login?next=..."` 覆盖当前页（约 50ms 内页变 about:blank）→ 后续 button click 找不到元素。修复：Guard 进入 useEffect 时检 `document.cookie.match(/(?:^|;\s*)sessionid=/)`，无则视为会话失效、清空 store 重走 bootstrap | 由 `parity-sprint1.spec.ts` C.14 + `interactions.spec.ts` 全量 14/14 守护 |
| E10 | `apps/web/app/components/NewTaskModal.tsx` 提交按钮缺 `data-testid="create-task-submit"`（`interactions.spec.ts` INT-F1/F2/F3 与未来 parity 锚定） | C.22 改造时「按回车提交」逻辑走 form onKeyDown，但 spec 找的是提交按钮 testid，agent 改造时未补；生产无影响（点击 / 回车都不走 testid），但 parity 断言需要稳定锚点 | 由 `interactions.spec.ts` INT-F1/F2/F3 守护 |
| E11 | `apps/web/app/routes/issues-list.tsx` 复制 issue_key 静默失败（headless 浏览器无剪贴板权限，`navigator.clipboard.writeText` reject 后无反馈） | 原型设计有 `copyText` 复选 + Toast，sprint-1 改用 `navigator.clipboard` 简化后丢掉 .then/.catch，导致失败时既无 Toast 也无降级方案。修复：成功 → Toast「已复制 XXX」；失败 → Toast「复制失败」（error 级） | 由 `interactions.spec.ts` INT-G1 守护（断言 toast「已复制 XXX-数字」） |
