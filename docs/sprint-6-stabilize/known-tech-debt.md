# Sprint-6 已知技术债（Sprint-7+ 消费）

> 登记日期：2026-09-08　|　状态：标准版 V1.0 发布收口　|　依据：ADR-0027 /
> v1-freeze-list 复审 / 迭代四轮实录　|　门禁终态见文末

## A. 顺延项（原计划评估后登记，非阻塞 V1.0）

| # | 项 | 顺延理由与归属 |
| --- | --- | --- |
| 1 | 公开项目 WS_ONLY 只读通道（A-1#1：rbac §6.2 `_scoped_for` 公开分支） | 架构回改涉及矩阵单源+IT-02 解锁面；V1.0 公开项目走 404 收口兜底（ADR-0026 既定）——Sprint-7 AUTH 面 |
| 2 | WS 主动踢出通道（A-1#3：live 票据 kick） | 依赖 COLLAB-004 webhookserver 扩展；当前 120s 票据时效为安全上界——Sprint-7 协同面 |
| 3 | APIKey 基建（A-1#9，P3 明确归属） | feature freeze 不新增端点；throttle 类占位已就位，Sprint-7+ AUTH-009 落地即接 |
| 4 | 前端覆盖率 70%（36.4%→70%：realtime/session/permission store 未测） | 补量性质；35% 门槛不后退已守住——Sprint-7 前端轮 |
| 5 | 后端覆盖率 80% 门槛（现状 73%，plane/app+db） | 同上；TC-COVER-002 已按 70% 守门 |
| 6 | K8s 镜像级探针就绪冒烟 | 需生产镜像可拉取（registry）；对象级冒烟（apply+结构断言）已过——INFRA-006（P4）或首个生产部署 |
| 7 | api/worker/live 只读根文件系统 | 需真实 prod 栈运行时写路径实测（防未验证资产）；8 服务已先行——恢复演练 compose 模式轮 |
| 8 | MinIO KMS 启用后回收 SSE 降级 | 运维侧 KMS 配置；代码降级路径+告警已就位——INFRA-006 |
| 9 | SearchRateThrottle 挂载 | V1.0 无全局搜索端点（类+配额已就位）——Sprint-7 搜索端点 |
| 10 | admin 运维面 drill 触发/产物下载预签名两端口 | restore-drill.sh 与 mc 已承载操作面——Sprint-7 admin 控制台深化 |
| 11 | ~~GitHub App per-repo webhook 回归（G-1）~~ **已收口（2026-09-08）** | 用户补权限+组织重批后回归全过：绑仓端点 `webhook_registered:true`、仓上 hook active（675944498，四事件族）、探针清理防双投递、ping 全链 202（隧道+验签闭环）；探针=`scripts/g1-webhook-regress.sh`（幂等）。附注：App 权限变更不自动作用于已存在 installation——需组织 owner 重批（本次排查结论） |
| 12 | ~~C 表三件：api-conventions §13.3 限流章节 / INTG-002 §9 风险表补登 / §4.1 口径对齐~~ **已收口（2026-09-08，Sprint-7 R0 文档批次）** | §13.3 重试/自动禁用两行按 INTG-002 BR-06/08 精确化（死信单表 + 无时间窗终态计数器）；sprint-5 概览 §1 补事件挂点边、§9 风险 #2/#3 五处冲突对齐、§3 权限码改 `integration.config`；70% 配额降级核实已实现（`QUOTA_DEGRADE_RATIO=0.70`）无需回改；INTG-002 §1.2 登记段与 sprint-5 known-tech-debt C 表 #3/#5/#6 同步标 ✓。另：dg 错位（WF-002~006/AUTH-009,010/WF-006 缺行/倒挂边）经 R0 核验确认已由 2026-09-06 架构文档专项回改批次完成，sprint-7 overview §3 注一/注二已同步消解 |
| 13 | E2E 浏览器兼容矩阵（Chrome/Edge/Firefox/Safari） | 需多浏览器 runner 基建——Sprint-7 QA 面；Chromium 全绿 |

## B. 本迭代收口清单（已闭环，留档）

- 三层限流（L1 三区/L2 十类/L3 端点级）+ 头/退避闭环；限流矩阵 8/8 实测
- 备份全链（三校验/快照随行/双保险清理/连败告警）+ 恢复演练 PASS（RTO 2s、冒烟 18/18）
- compose prod 覆盖层 + K8s 骨架 + kind 对象级冒烟 + 四脚本（backup-now/preflight/restore-drill/smoke/release）
- 发布门禁六端点 + admin 运维台四页（C.134~136，parity 3/3）
- 安全门禁 CI 四源 + BR-06 判定 + 豁免单机制；越权矩阵 64 格；gates.py 门禁表即代码（UT-07 双源防漂移）
- 顺手修掉三项既有缺陷（FileAsset workspace 漂移 / flow 清理缺失 / coverage 未入锁）

## C. 门禁终态（2026-09-08）

| 类别 | 数据 |
| --- | --- |
| pytest | 618 项全绿（含 IT-SEC 64 格 / 发布门禁 6 / 备份 10 / verdict 6 / gates 3） |
| 静态 | ruff + mypy 全绿；run-ci-checks 56/56 |
| e2e | admin-ops parity 3/3；既有 web 套件复跑见验收记录 |
| 限流矩阵 | 8/8（用户桶 61→429 信封+头/auth/报表/批量/presign） |
| 演练 | preflight ALL-OK；restore-drill local PASS（RTO 2s + 冒烟 18/18） |
| kind | 9 对象 apply + 六项结构断言全对 |

## D. Sprint-7 入口

- 本表 A 组逐项排期；QA-001 §7.2 发布验收报告三方签署归档 `release/v1.0.0/`（模板已就位）
- 交接上游：`sprint-5-integration-standard/known-tech-debt.md` F/G 表消费进度——F2✓ D2✓ G1✓（2026-09-08 收口，#11）；A 表 1/2/3/9 见本表 A 组
