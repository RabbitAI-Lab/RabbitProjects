# Sprint-7 已知技术债（Sprint-8+ 消费）

> 登记日期：2026-09-08　|　状态：企业工作流核心交付收口　|　依据：ADR-0028 /
> 迭代六轮实录（R0 基线 / R1 引擎 / R2 守卫字段 / R3 规则工时 / R4 前端 / R5 模板留痕）

## A. 顺延项（非阻塞收口）

| # | 项 | 顺延理由与归属 |
| --- | --- | --- |
| 1 | WF-006 异步导出（task_id/status_url 两段式） | 当前 CSV 流式代理端点已承载（approval-audit/export/ 直下）；MinIO 异步范式随首个大容量导出需求接入（GANTT-002 §4.5 范式）——Sprint-9 RPT 域 |
| 2 | 审批超时提醒的管理员加报面（BR-12 §2.4 24h 档） | 当前仅加报发起人；admin 通知走 R5 admin 台深化——与 Sprint-6 known-debt #10 同批 |
| 3 | automation_match 事件总线接线（TASK-010 管道 hook） | 引擎/worker/dry-run 全就绪（35 断言 flow 过）；生产事件源挂接（state_changed on_commit 投递）随首个自动化生产场景接入——防循环三闸已测 |
| 4 | WorkflowTemplate 图快照深度校验（BR-02 保存即校验） | 当前实例化侧防御（缺键跳过）；保存侧 jsonschema 随 WF-005 前端配置器联调补 |
| 5 | 前端审批中心 count_only 徽标轮询 + WS 增量 | 端点已就绪（approvals/pending/?count_only=1）；前端轮询接入随 R4 后续打磨 |
| 6 | e2e spec（approval-center/canvas/worklog） | 附录 C.140~C.143 清单已入库 + 组件 data-sb-scope 全埋；spec 随验收视频轮（用户参与）补 |
| 7 | 覆盖率新面（plane/workflow 域） | pytest 118 项覆盖主路径；80% 门槛推进随 TC-COVER 复测（承 Sprint-6 #4/#5 软目标） |
| 8 | sprint-5-flow 32/8 历史在案（R0 补登记） | Sprint-5 收口 39/47 同源：W6 段 mock 只 patch 同步 `_post`、重试经 apply_async 投 worker 无 mock + 真退避 → 终态不可达；A1-01/P3-03/P3-04/I5-06/I5-09 待查。r1-inflight R0 段曾承诺「R5 收口时入本表」但实际漏登，Sprint-8 R0 补上；修复随首个 flow 复跑轮 triage |

## B. 本迭代收口清单（已闭环，留档）

- WF-001 引擎（三模型/兜底链/发布校验/两行版本/终审回填三文档闭环）
- WF-002 审批（四态机/会签或签逐级/BR-10 显式 409/禁自审转交）
- WF-003 自动化（四触发器×五动作/防循环三闸/Dry Run/熔断/90 天清理）
- WF-004 守卫矩阵（四执行器/主码分流/guard_payload 单请求闭环/字段锁定/PATCH state 收口）
- WF-005 模板库（预设四套/两步下发/BR-05 状态映射/解锁申请）
- WF-006 留痕（哈希链/触发器只增/CSV 导出/链校验端点）
- TASK-012 高级字段（四类型/权限四态/四处剔除/FilterCompiler hidden 拒绝）
- TASK-013 工时（批次四态/锁定冻结/台账/软上限 warnings）
- 测试终态：pytest 771 过 + sprint-7-flow 35/35 + 前端 tsc/oxlint 新文件零错

## C. 门禁终态（2026-09-08）

| 类别 | 数据 |
| --- | --- |
| pytest | 771 全绿（含工作流 29 + 守卫 20 + 字段权限 16 + R3 22 + R5 9） |
| 静态 | ruff + mypy（234 文件）全绿 |
| sprint-7-flow | 35/35（六段契约矩阵） |
| e2e | R0 基线 162/0；R4/R5 新面 spec 待验收视频轮（A#6） |

## D. Sprint-8 入口

- A 组 1~7 逐项排期；本表与 sprint-6 版 A 组（#1~#10 未消费项）合并消费
- 协议字段冻结清单（guards/side_effects/approval）已随 ADR-0028 §6 交付——AUTH-008 自定义角色消费节点权限模型（sprint-overview §10.3）
- 交接文档：`docs/sprint-7-enterprise-workflow/r1-inflight.md`（六轮全实录）
