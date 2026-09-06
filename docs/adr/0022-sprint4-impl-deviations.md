# ADR-0022 · Sprint-4 实现偏差汇总收口（甘特 + 文件管理）

| 项 | 内容 |
| --- | --- |
| 状态 | 已接受（Accepted） |
| 日期 | 2026-09-07 |
| 背景 | Sprint-4（GANTT-001/002、FILE-002/003/004）全量实现收口。各实施批次（T4-03~T4-14）上报的偏差按 ADR-0020 模式汇总登记；本文为唯一汇总口径，各批次代码注释不再重复展开 |
| 关联 | `docs/sprint-4-gantt-file/`（五规格 + 概览）、ADR-0021（前置缺陷修复）、ADR-0017（epoch 签名）、ADR-0010（parity 五步） |

## A. 实现偏差（按规格）

### A-1 FILE-002（T4-03，11 条）

| # | 规格 | 实现 | 理由 |
| --- | --- | --- | --- |
| 1 | §4.1.1 FileAsset 落 `models/file.py` | 留在 `asset.py`，仅 FileFolder 新建 file.py | 迁移历史锚定；搬移引发 Delete/Create 误判 |
| 2 | §4.1.1 FILE-003 预留列为 FK | UUID 列落库（列名=目标 FK 列名） | 目标模型属 FILE-003 未存在；0010 迁移 AlterField 升级真 FK |
| 3 | BR-11 配额存 WS 设置 | `settings.WS_STORAGE_QUOTA_BYTES`（env，默认 10GiB） | 无 WS 设置模型，DDL 清单亦无 WS 列 |
| 4 | §1.7 30m→60m | `/uploads/` 独立 `UPLOADS_MAX_BODY_SIZE`（缺省 60m），全局不动 | 现状实为参数化 100M 非字面 30m；独立变量保留调节能力 |
| 5 | BR-12 文件操作动态留痕 | 未实现 | §7.1 未列；COLLAB-003 为 issue 域合流，管道扩展归后续（见 D-2） |
| 6 | §4.2 #7 download 形态未定 | 200 JSON `{download_url, expires_in}` | §4.4 前端 window.open 语义；与端点表成功码一致 |
| 7 | presign expires 字段两处矛盾 | 双字段 `expires_at`+`expires_in` | 规格自相矛盾，双源兼容 |
| 8 | IT-01/06/07/08 形态 | pytest 覆盖确定性子集；真 MinIO/通知/P95/留痕分别归 flow/bench/未交付件 | §7.1 划界 |
| 9 | BR-07 目录恢复端点 | service 层 `restore_folder`，由文件 restore 自动触发 | 端点表未列目录级端点（逻辑已实现，UT-13 锚定） |
| 10 | 下载计数 beat 注册 | 任务+include 注册，无 PeriodicTask 行 | django_celery_beat 不在 INSTALLED_APPS（FILE-001 同状态） |
| 11 | — | 清理任务增 `restrict_workspace_id` 测试安全参数 | 坑 18 共享库纪律；生产不传=原行为 |

### A-2 FILE-003（T4-04，8 条）

| # | 规格 | 实现 | 理由 |
| --- | --- | --- | --- |
| 1 | 会话白名单=FILE-001 | `LIBRARY_CHUNK_EXTS`（+视频容器/svg 等） | §1.4 #5 视频预览以 mp4 上传为前提；直传白名单零回改 |
| 2 | IT-02 会话数上限「排队提示」 | 409 `RESOURCE_LIMIT_EXCEEDED`（用户×WS） | 规格无端点语义 |
| 3 | BR-13 版本事件入项目动态 | 仅 WebSocket 半边 | COLLAB-003 按 issue_id 键取数，文件域无落点（见 D-2） |
| 4 | 版本 attributes 键形 | 双键形（name/size/content_type/md5 + mime/ext） | FILE-002 行级镜像与 type_category 依赖 |
| 5 | upload_session 预留列 | RemoveField 撤销 + `upload_sessions.asset` SET_NULL | 规格 §4.1.2 注 2 原文口径；资产硬删后会话存续为账本 |
| 6 | BR-05 过期即释放配额 | expired 转记直传在途侧，abandoned 才全额释放 | 防孤儿占额漏洞（更保守） |
| 7 | 衍生物登记 | 挂 `FileVersion.attributes.derivatives` 零新表 | DDL 清单锁死的必然推论 |
| 8 | 版本对比端点 | 未单设；#9 内容双端点支撑前端 diff | 端点表为权威（§1.3 diff 在前端） |

### A-3 FILE-004（T4-05，8 条）

| # | 规格 | 实现 | 理由 |
| --- | --- | --- | --- |
| 1 | share_url 绝对地址示例 | `request.build_absolute_uri` | 无 SPACE_BASE_URL 配置；宿主域随部署 |
| 2 | beat 载体 | Celery 原生 `app.conf.beat_schedule`（crontab 每小时） | 仓库首个代码内 beat；compose DatabaseScheduler 接通归 INFRA-005 |
| 3 | BR-13 入项目动态 | WebSocket 三事件（file.share.*） | 同 A-2#3（见 D-2） |
| 4 | BR-07 计数口径 | 请求进入时计数；解锁成功清零 | 规格未言明清零；防成功访客被余量卡住 |
| 5 | 内部写操作归档只读 | 沿用 FILE-002 `_require_not_archived` | 规格未言明，与文件库写纪律一致 |
| 6 | Redis 不可达限流 | 降级放行 | file_stats/event_publisher 同款纪律；INFRA-005 收编复核 |
| 7 | types LiveEventName | 当时未同步五事件 | 已由 T4-08 偿还（同步落地） |
| 8 | IT-02/03 S3 时效 | 预签名窗口参数断言承载 | 真实过期链路归 HTTP 侧脚本 |

### A-4 GANTT-001（T4-06，9 条）

| # | 规格 | 实现 | 理由 |
| --- | --- | --- | --- |
| 1 | §4.3.1 子树聚合 SQL annotate | Python `_subtree_ranges`（≤5 小查询+id__in 支路） | 多跳谓词在 PG 计划灾难（实测 P95>2s）；输出契约不变 |
| 2 | 视窗谓词单 OR 表达式 | 无 OR 三支互斥分解（真值表等价） | OR-NULL 形态致估算漂移翻 Seq Scan，IT-07 不稳定 |
| 3 | display_props.collapsed | 入 BOARD-003 键域+校验 | BR-10 持久化依赖保存管线 |
| 4 | rows 白名单参数 | 仅 view_id+?filters DSL | §4.2.1 要点 4 字面 |
| 5 | unscheduled 筛选 | 支持 view_id/?filters | IT-06 带筛选时 list==count 一致 |
| 6 | relation_count 物理行数 | 逻辑关系数 | 成对存储下 issue 侧行恰等（UT-21 锚定） |
| 7 | bulk 连线项目过滤 | 双端过滤 | TASK-005 已拦创建，脏数据防御纵深 |
| 8 | 首页 prev_cursor 示例非空 | null | 规格示例自相矛盾（prev_page_results=false）；按 api-conventions §6.3 |
| 9 | 列宽 32/8/2（§1.4） | 冻结原型 O3 36/13/9 | 原型为唯一验收基准且冻结更晚（勘误 E-1） |

### A-5 GANTT-002（T4-07，3 条）

| # | 规格 | 实现 | 理由 |
| --- | --- | --- | --- |
| 1 | §4.3.1 SQL 日期减法 | Min(target_date) 推最大天数 + Python 派生 | Django 5.1 duration 语义在 PG 报 interval/integer 不匹配（实测 500）；零语义差且走索引 |
| 2 | 限流键 | `gantt-agg:{user_id}`（匿名兜底 IP 不可达） | §4.2.1 要点 4 原文 |
| 3 | by_assignee 并列序 | 追加 assignee_id 稳定次键 | 规格未指定并列序 |

### A-6 live/实时（T4-08，3 条）

| # | 规格 | 实现 | 理由 |
| --- | --- | --- | --- |
| 1 | 票据上限 | issue+file 合并 ≤8（单字段先行校验保留） | 总数 10 不变 |
| 2 | COLLAB-004 §2.3「6 类核心」标题 | file 五事件独立补登块 | 冻结原文不动纪律 |
| 3 | （附带）rollback 缺 @transaction.atomic | 补齐 | 存量 bug，e2e 发现（pytest 事务掩盖） |

### A-7 前端（T4-09/10/11，合并 15 条）

| # | 规格 | 实现 | 理由 |
| --- | --- | --- | --- |
| 1 | GanttStore 落 shared-state | `apps/web/app/stores/gantt.ts` | 该包纪律不发起 HTTP + 坑 19；BoardStore 先例 |
| 2 | 时间轴域 | 固定 today−365d~+730d | 规格未定义无界轴锚点 |
| 3 | 键盘改期冲突弹层 | 不弹（红点提示保留） | §3.4 未要求 |
| 4 | 空态判定 | meta.total_count+unscheduled_count 组合 | 视窗外有任务窗内空的口径 |
| 5 | resize 差分提交 | 仅变更字段 | 「仅写两字段」语义内 |
| 6 | 折叠持久化 | 选中视图时 PATCH | 「全部」裸态本地记忆（BOARD-003 同口径） |
| 7 | 概览条实时刷新 | 载入/切视图/本人改期后刷新 | 10/min 限流保护 |
| 8 | e2e 测试钩子 | `window.__rpGanttBus`（DEV 门控） | 合成事件注入 |
| 9 | 根层文件列表 | 渲染目录引导（无根层端点） | §4.2 #5 仅 folders/{id}/files/（契约缺口，见 E-6） |
| 10 | 时间筛选 | 客户端过滤 | 后端列表无时间参数（契约缺口） |
| 11 | 回收站删除人列 | 渲染 uploaded_by | 契约无 deleted_by 字段 |
| 12 | 拖拽移动到左树 | ⋯ 菜单移动弹层承载 | 交互增强归后续 |
| 13 | FileLibraryStore（shared-state） | 页面内 React state | 无跨页共享需求（table.tsx 范式） |
| 14 | space dev 代理 | vite /api、/uploads 反代自带头 | 生产由网关承载 |
| 15 | 分享/预览菜单占位 | data-todo="t4-11"（T4-11 已接通） | 批次边界 |

## B. 缺陷修复登记（门禁/e2e 实弹驱动，非偏差）

| # | 缺陷 | 发现处 | 修复提交 |
| --- | --- | --- | --- |
| 1 | `folder_tree` 计数裸取键 → 父有文件子无文件 500 | sprint-4-flow | 8f58cd3 |
| 2 | 分片 complete 响应 version 恒 `{}`（旧实例读指针） | sprint-4-flow | 8f58cd3 |
| 3 | 万级列表全量水合 P95 313ms 失守 | sprint-4-bench FB | 8f58cd3（SQL 下推 40ms） |
| 4 | `wire_direct_version` 缺事务包裹（直传 complete 恒 500） | T4-10 e2e | 77294cc |
| 5 | 204 带 body 三处（DELETE/abort/revoke）keep-alive 流错位 | T4-10/11 e2e | 77294cc / 4da07e1 |
| 6 | `register_chunk` 并发丢片（无行锁） | T4-11 ChunkUploader | 4da07e1 |
| 7 | 目录 ⋯ 弹层冒泡触发树行漂移 | T4-10 e2e | 77294cc |
| 8 | upload_session.rollback 缺事务 | T4-08 e2e | a4d6dd1 |
| 9 | 原型甘特表头错位（flex-shrink 压列）/连线起点悬空/tooltip 裁切 | 用户走查 | 785845a / dc4d72d / debe4ee |

## C. 门禁脚本偏差（T4-12/13，5 条）

| # | 项 | 处置 |
| --- | --- | --- |
| 1 | 非流式视频负例 | 走分片通道达成（直传白名单差异为既定） |
| 2 | gantt/ meta 不回显 view_id | 按规格只断行集筛选 |
| 3 | 对称边 violation 恒 false | 统一序列化口径 |
| 4 | 分享/预览轻门禁 | 只报数不判定（规格无数值） |
| 5 | office 无 soffice | 恒 202 排队口径断言（环境性 failed 兜底，无假成功） |

## D. 流程与工具链登记

| # | 项 | 说明 |
| --- | --- | --- |
| 1 | 转码工具链 | 本机无 soffice/ffmpeg：任务完整实现、失败语义如实（无静默假成功）；compose worker `INSTALL_TRANSCODE_TOOLS` 分层已就位未本机构建验证 |
| 2 | 文件域动态流缺口 | COLLAB-003 按 issue_id 键取数，file.* 动态无法入流——WebSocket 半边全交付，管道扩域归 COLLAB-003 回改（三规格共同前置） |
| 3 | e2e 造数清理 | 三份 sprint-4 spec 接 afterAll 自动清理（`_cleanup_s4.py`，S4[FGP] 全域幂等硬删+演示工作区名额治理） |
| 4 | 突变残留事故 | T4-11 被用量上限截断，MUTATION-1 残留被主线复验收网——教训入 CLAUDE.md 坑 23 |

## E. 规格勘误待回改（收口后回写，不阻塞）

| # | 规格处 | 现状 | 待回改 |
| --- | --- | --- | --- |
| 1 | GANTT-001 §1.4 列宽 32/8/2 | 实现按冻结原型 O3 36/13/9 | §1.4 改 36/13/9 |
| 2 | GANTT-001 §1.4 周列头「周一日期+周数」 | 实现起–止区间（原型 O3） | §1.4 对齐 |
| 3 | 甘特 ⋯ 全屏/重置缩放 | 原型/实现有、规格无条款 | §3.3 补登 |
| 4 | FILE-002/004 🔗N 分享数角标 | 原型/实现有、规格无 | §3.1 补登 |
| 5 | FILE-002 download-url 200 形态 / presign 双字段 | 实现定稿 | §4.2 示例对齐 |
| 6 | FILE-002 根层列表端点 / 时间筛选参数 / trash deleted_by | 契约缺口 | §4.2 补登或声明排除 |
| 7 | C.123/124 自拟文案标注 | 已在清单标注 | 规格可选补文案 |

## 结论

Sprint-4 全部偏差按上述口径收口；退出条件三项核验见概览 §10 收口注记（功能验收/工程质量/文档同步）。后续回改项以本文 E 表为清单，随下一迭代文档批次执行。
