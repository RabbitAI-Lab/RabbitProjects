# Sprint-9 已知技术债（Sprint-10+ 消费）

> 登记日期：2026-09-10　|　状态：企业版 V1.0 交付收口　|　依据：
> 本文件 + ADR-0031（R2 实现偏差）+ r0-baseline.md（债务认领表）

## A. 顺延项（非阻塞收口）

| # | 项 | 顺延理由与归属 |
| --- | --- | --- |
| 1 | ELK.js 分层布局依赖登记（PROJ-004 §4.7 前端要点：tech-stack 新增依赖流程补登） | 依赖图首版用 CSS 分列布局承载（连线动态实测）；ELK 引擎化随首个大规模图需求接入 |
| 2 | 燃尽/速率/CFD PNG 导出（html-to-image 2x 复用 GANTT-002 管线） | CSV 导出已交付（workload 同步流式 + burndown CSV）；PNG 前端渲染随 S10 打磨轮 |
| 3 | Wiki 协同编辑实时冲突感知（Yjs P4 评估前的「他人编辑中」轻提示） | 发布乐观锁 409 已兜底正确性；实时感知属体验增强 |
| 4 | Wiki 回收站 30 天 beat 硬删任务（`purge_deleted_wiki_pages`） | BR-09 恢复/过滤已全；期满硬删随 S10 beat 轮（当前回收站行留存无 TTL 风险） |
| 5 | SAML 真容器断言联调（S8 A#2 续）：Keycloak SAML IdP 浏览器流 | OIDC 全链已过；SAML SP 元数据/无效断言拒绝已测，真断言流随 S10 验收轮 |
| 6 | SSO e2e Keycloak fixture 化（S8 A#8） | OIDC 手工联调自证；CI fixture 化属基建投资 |
| 7 | CPM 关键链依赖边加粗红渲染（甘特连线样式分级） | 条级红描边 + 降淡已交付；边级样式随 S10 甘特打磨 |
| 8 | 跨项目环检测 CTE 工作空间级扩展验证（BR-09 大空间压测） | 服务级已测（跨项目环 409）；万级工作空间 CTE 压测随 S10 容量轮 |
| 9 | report_measure 度量切换后「当日之后快照口径重算」批处理 | 配置 PATCH 已落；切换重算历史快照的批量任务随真实切换场景接入 |

## B. 本迭代收口清单（已闭环，留档）

- PROJ-004 项目集：组合树/里程碑/贡献项/汇总三卡/依赖图 + 跨项目依赖放开（BR-08）+
  守卫项目过滤（BR-07 软策略）+ beat 里程碑预警
- RPT-003 敏捷报表：Cycle 时间盒/整批换绑（cycles 事件埋点）/快照管道（口径单源 +
  幂等补跑 + is_final 终版锚）/燃尽/速率/累积流/度量配置/CSV 导出
- RPT-004 健康度：四维评分（RPT-002 基座复用）/样本不足重归一/快照冻结/下钻实时口径/
  负载热力（容量唯一源）/窗口校验
- FILE-005 Wiki：空间/页面树/草稿/发布乐观锁/版本台账回滚/回收站整树恢复/trgm+ILIKE 检索
- GANTT-003 关键路径：Kahn CPM（环防御/分档/负浮动）+ 指纹缓存 + debounce +
  每日维护 + 甘特高亮/浮动卡/预警配置
- 双债收口：S7 A#1 + S8 A#1（ExportTask 异步两段式 + 审计导出接入）｜S7 A#3（自动化
  事件总线接线）｜S7 A#4（模板快照保存校验）｜S8 A#5（权限码并集分支）
- 前端 13 新面 + 甘特增强（冻结原型基准，FROZEN 2026-09-10）

## C. 门禁终态（2026-09-10）

| 类别 | 数据 |
| --- | --- |
| pytest | 973 全绿（项目集 21 + 迭代 19 + Wiki 14 + CPM 11 + 健康度 12 + 既有回归） |
| e2e | 160 过 0 挂（parity-sprint9 5/5：项目集/迭代/健康负载/Wiki/关键路径） |
| vitest | 58 过；覆盖率 functions 28.68%（≥25% 门槛，TC-COVER-004 收口） |
| 静态 | ruff + mypy（288 文件）+ tsc + oxlint 全绿 |
| 四门禁压测 | summary 32ms(<300) ｜ dep-graph 25ms(<200) ｜ wiki 检索 18ms(<300) ｜ CPM 1 万节点 38ms(<300) |
| 验收视频 | 六幕 6/6（docs/sprint-9-enterprise-portfolio/videos/，gitignore 永不在库） |
| 迁移 | 0031~0036 六批（手工灌 + fake） |

## D. Sprint-10 入口

- 本表 A 组逐项排期；与 S8 A 组未消费项（A#4 泳道拖拽/A#7 org 拖拽/A#9 S5~7 存量原型债）合并
- 交接：`docs/sprint-9-enterprise-portfolio/r0-baseline.md`（R0 债务认领表）+ 本文件
- dev 栈注意：worker 队列含 `reports`（-Q activity,celery,workflow,notifications,audit,reports）；
  MinIO 凭证 rpminio/rpminio123（AWS_ACCESS_KEY_ID 等 runserver+worker 双进程都要）
