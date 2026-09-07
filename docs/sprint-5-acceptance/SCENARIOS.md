# Sprint-5 验收视频 · 场景契约（Phase 6 / T5-14）

> 状态：契约定稿（2026-09-07，sprint-5 收口前）
> 执行时点：Sprint-5 全部门禁绿（TC-AUTH6-001/002 + sprint-5-flow + test_intg001/002 + test_rpt002 + test_proj003 + test_team003）之后的验收录制
> 录制纪律：**全部从真实用户路径出发**——登录页 →（一键演示账号 / zhangsan 表单登录 → 顶栏切工作空间）→ 项目卡片 → 侧栏导航（统计/集成/Webhook/项目设置）→ 操作；禁止直接 goto 深链、禁止 API 直调充当场面（数据准备与断言取证除外）。与 CLAUDE.md「用户入口铁律」同源。

## 基建（沿用 sprint-2/3/4 模式）

| 物 | 说明 |
| --- | --- |
| `scripts/seed_acceptance_s5.py` | 幂等数据准备（走 API + 幂等 SQL 清场，不属于被验收场面）：「S5 验收演示」项目——基线 + 三 Webhook 端点（含 mock 5xx 接收方）+ 统计样本任务 + 生命周期四态项目 + 治理四区块场景 + 演示账号（zhangsan@rabbit.dev ADMIN + 李四/王五 CONTRIBUTOR/GUEST + 第二 WS 的 OUTSIDER）|
| `scripts/acceptance_video_s5.mjs` | Playwright 逐幕录制：每幕独立 browser context（`recordVideo` 1440×900）、关键交互 `waitForResponse` 校验 2xx（录到的必须是成功画面）、失败画面留 1.2s、`ONLY=` 过滤 |
| `scripts/mock_github_receiver.py` | GitHub 侧 mock（8090 端口）：验证签 + 触发 issues.opened/edited/closed + pull_request.closed merged=true + push events |
| `scripts/mock_500_receiver.py` | 5xx 接收方 mock（8091 端口）：用于演示 INTG-002 死信链路（7 次 5xx → dead） |
| `docs/sprint-5-acceptance/videos/` | `scene-XX[-a|b]-<名称>.webm` 逐幕产物（webm 不入 git） |
| `docs/sprint-5-acceptance/README.md` | 幕表（场景/验收条款出处/时长/结果）+ 环境复跑 + 差异 + 已知事项 |
| `.gitignore` | 补 `docs/sprint-5-acceptance/videos/` 目录规则 |

## 场景清单（13 幕 → sprint-overview §6 验收条款全覆盖）

§6 七条中：§6-1（文档结构）/ §6-8（压测基线）/ §6-9（越权矩阵）/ §6-10（覆盖率）/ §6-11（文档同步）由门禁与文档覆盖，本录屏不设幕；其余七条逐条 ≥1 幕。

### GitHub 集成域（4 幕）

| # | 幕 | 用户路径叙述 | 验收条款 |
| --- | --- | --- | --- |
| 01 | 安装入口与绑仓 | 顶栏「集成」→「安装应用」获取 install_url → 跳 mock 域（无 App 凭据走 dev token 直通）→ 回项目「集成」页绑定 `acme/s5-demo` 仓库（≤5 上限）→ 一次性 secret 展示 | INTG-001 §4.2.1+§4.2.2 / 概览 §6-2 |
| 02 | Issue 双向同步三向（a/b） | a 视口：张三 web 修改某 GitHub 来源任务的标题/状态/评论；b 视口：mock 接收方相应回调入站 → a 视口**无刷新**自动同步（看板列颜色/标题/评论） | INTG-001 §2.2 / 概览 §6-2 |
| 03 | PR 合并 → 任务自动完成 | 在 a 视口把任务迁完成组的依赖前置就绪 → mock 域 PR closed merged=true → a 视口**无旁路**走 TASK-005 守卫进入完成组 + Activity ⚙ GitHub | INTG-001 §2.3 / 概览 §6-2 |
| 04 | Commit 挂载（`RBT-123` 引用）| mock 域 push commit message `RBT-1: 联调通过` → a 视口任务详情页 github_context.commits 自动追加（sha×任务 去重，重复 push 同 sha 不增） | INTG-001 §2.2 / 概览 §6-2 |

### Webhook 出站域（4 幕）

| # | 幕 | 用户路径叙述 | 验收条款 |
| --- | --- | --- | --- |
| 05 | 创建端点与 secret 一次性展示 | 项目设置 → Webhook → 新建端点（url 指 mock 5xx 服务便于后续演练）→ secret 一次性展示 → 复制保存 | INTG-002 §3.2 §4.2.1 / 概览 §6-3 |
| 06 | 投递日志 + 重放（a/b）| a 视口：web 触发 issue.updated（点按钮 mock）→ 端点日志页 7 次 attempt 状态序列 1s/10s/1m/10m/1h/6h（演示模式压缩为各 1s 实跑）→ dead 红点 + 重放按钮；b 视点：5xx mock 服务实时显示 7 次 5xx 接收记录 | INTG-002 §3.3 §4.2.7 / 概览 §6-3 |
| 07 | 50 连败自动停用 + 通知 | 直 SQL 注入 `consecutive_failures=49` + 一次投递 → `auto_disabled` 徽章 + 通知中心出现 webhook.auto_disabled 通知 + 手动 enable 连败清零 | INTG-002 §2.4 §4.2.4 / 概览 §6-3 |
| 08 | 接收方 HMAC 验签（参考实现）| 终端跑 `python3 scripts/intg002_receiver_reference.py https://api.github.com/webhook --secret <secret>`——回显验签通过/失败分支与时间窗漂移拒绝；文档引用 INTG-002 §4.3.1 | INTG-002 §4.3.1 / 概览 §6-3 |

### 项目统计域（2 幕）

| # | 幕 | 用户路径叙述 | 验收条款 |
| --- | --- | --- | --- |
| 09 | 进度 + 趋势 + 成员任务量 | 侧栏统计 → 五组分布条 + 完成率（剔已取消）+ 工期四数 + 趋势双折线（近 30 天）→ 切换 days 7/14/30/90 → 成员任务量表（含未指派 + 合计行 + 已禁用灰标）| RPT-002 §3.1/§3.2 / 概览 §6-4 |
| 10 | 统计限流（429 + 头） | 11 次连刷 stats → 第 11 次起 429 RATE_LIMIT_EXCEEDED + 响应头 X-RateLimit-Limit/Remaining | RPT-002 §4.3.3 / 概览 §6-4 |

### 生命周期域（1 幕）

| # | 幕 | 用户路径叙述 | 验收条款 |
| --- | --- | --- | --- |
| 11 | 四态全链 + 模板实化 + 副本重开 | 新建项目 → 选内置敏捷模板 → 检查状态四件套就位 → draft→active→archived→active→closed → closed 副本重开得 draft -C 标识（描述首行附"本项目为 XXX 的副本"） + close 向导（开放任务时弹"先去处理/强制关闭"二分按钮 + 输入项目名确认 + force=true 批量迁已取消） | PROJ-003 §3.2/§3.3/§2.5 / 概览 §6-5 |

### 团队治理域（1 幕）

| # | 幕 | 用户路径叙述 | 验收条款 |
| --- | --- | --- | --- |
| 12 | 治理四区块 | 工作空间设置 → 治理 → 归档/全局标签/状态模板/活跃度四 Tab 逐个走：归档后写操作 403 横幅 + 恢复；全局标签建/改/删（受 0 任务影响）+ 项目列表并集显示；状态模板五组编辑保存 version 自增；活跃度三数 + 周分桶 + 登录直方图（无 per-user 行，键路径无 user_id）| TEAM-003 §3.1 / 概览 §6-6 |

### 行级隔离域（1 幕，a/b）

| # | 幕 | 用户路径叙述 | 验收条款 |
| --- | --- | --- | --- |
| 13 | 矩阵可见性端到端（a/b）| a 视口：MEMBER 看见项目并能写评论；b 视口：同 WS 下 OUTSIDER 直连项目 404 → 归档后 a 视口写操作 403 PERM_WORKSPACE_ARCHIVED | AUTH-006 §3.3 / 概览 §6-7 |
| 14 | 真 GitHub 联调全链（a/b·补录）| a 视口：集成页真实绑定行（rabbitai-lab/rabbittest）→ GitHub 实时建 issue 落任务列表 → S5AC-14 抽屉 GitHub 区块（真 PR #4 + 双 commit sha）→ S5AC-13 评论输入；b 视口：github.com 公开仓库匿名页——issue #2 双向评论（a 视口评论 reload 即现）、issues 列表 `[S5AC-*]` 前缀、PR #4 已合并。App 级 webhook 经公网穿透入站，出站走 installation token | INTG-001 §2.1/§2.2/§2.3 / 概览 §6-2 |

## 与 sprint-4 acceptance 的差异

- 新增 `scripts/seed_acceptance_s5.py`（S5 数据准备：基线项目含 GitHub mock 绑定 + 三 Webhook 端点 + 统计样本 + 生命周期四态 + 治理四区块 + 演示账号 + OUTSIDER 视口素材）
- 新增 `scripts/acceptance_video_s5.mjs`（13 幕录制器，沿用 s4 同款 Playwright 模式）
- 新增 mock 服务两个（`scripts/mock_github_receiver.py:8090` 替代真实 GitHub 域 / `scripts/mock_500_receiver.py:8091` 触发 INTG-002 死信）
- 新增接收方参考实现（`scripts/intg002_receiver_reference.py` ——Python HMAC-SHA256 验签参考实现 + 时间窗检查）
- 视频 webm 同 s2/s3/s4 不入 git（.gitignore 补 `docs/sprint-5-acceptance/videos/` 目录规则）；本 README 与 SCENARIOS 入库
- 跨工作空间成员越权直连：13 演示"非成员直连 404"，未演示"WS_ONLY 公开项目只读可见"——架构回改后由 sprint-6 收尾补录

## 数据准备清单（seed_acceptance_s5.py 自动建）

| 物 | 数量 | 用途 |
| --- | --- | --- |
| 项目「S5 验收演示」（identifier S5AC）| 1 | 基线 |
| 项目状态 | active | 幕 11 起始 |
| 任务 | 12（5 态分布 + 1 阻塞前置）| 幕 09 统计 / 幕 11 关闭向导 |
| 工作标签 | 3 | 幕 12 全局标签并集 |
| Webhook 端点 | 3（issue.updated / issue.state.changed / project.*）| 幕 05/06/07 |
| GitHub 绑定 | 1（acme/s5-demo） | 幕 01-04 |
| 演示账号 | zhangsan@rabbit.dev（ADMIN）+ 李四（CONTRIBUTOR）+ 王五（GUEST）+ 第二 WS 的 OUTSIDER | 幕 02/06/11/13 视口角色 |
| 通知 | workspace.member_disabled / webhook.auto_disabled | 幕 07/12 |

## 与 sprint-5-flow / bench 的分工

- `sprint-5-flow.jMeter`（CI gate）：端到端断言 + 异常路径（坏签名/未绑定/重复 delivery/非法边/50 连败）+ 通知投递断言——**功能正确性**
- `sprint-5-bench.py`（CI gate）：10w 任务数据集 + 限流 30/min/项目 + 多线程压测——**性能基线**
- `tests/e2e/parity-sprint5-*.spec.ts`（CI gate）：4 新页字段级断言 + 行为断言——**UI parity**
- `tests/test_*.py`（CI gate）：业务功能 + 矩阵 + 越权单元测试——**回归保险**
- 本录屏（QA-001 实跑）**演示全部门禁覆盖到位**——验收签字
