# Sprint-5 验收录屏（13 幕 mock 全过 + 第 14 幕真 GitHub 联调，2026-09-07 实录；sprint-3/4 同模式）

> 概览 §6 七条验收条款的幕映射：§6-2 GitHub 集成（幕 1~4）｜§6-3 Webhook 出站（幕 5~8）｜§6-4 项目统计（幕 9~10）｜§6-5 生命周期 + 模板（幕 11）｜§6-6 团队治理（幕 12）｜§6-7 行级隔离（幕 13 a/b）。
>
> §6-1（六规格文档结构）为后台行为非用户路径——由 sprint-5-flow.jMeter（in CI）覆盖；本录屏不设幕。
> §6-8 压测基线（10w 任务 P95<200ms）由 sprint-5-bench.py（in CI）覆盖；本录屏不设幕。
> §6-9 越权矩阵由 test_auth006（in CI）覆盖；本录屏仅以幕 13 演示"可见性端到端正确"。
> §6-10 覆盖率（80%/70%）由 run-ci-checks 新增 TC-COVER-001/002 覆盖；本录屏不设幕。
> §6-11 文档同步（6 规格状态「已实现」+ ADR-0023~26）由 docs/ 目录 git 状态覆盖；本录屏不设幕。

| 幕 | 场景 | 验收条款 | 时长 | 结果 |
| --- | --- | --- | --- | --- |
| 01 | 安装入口与绑仓 | INTG-001 §4.2.1+§4.2.2 / 概览 §6-2 | 5s | ✓ |
| 02 | Issue 双向同步三向（标题/状态/重开）| INTG-001 §2.2 / 概览 §6-2 | 33s | ✓ |
| 03 | PR 合并 → 任务自动完成 | INTG-001 §2.3 / 概览 §6-2 | 8s | ✓ |
| 04 | Commit 挂载（`S5AC-7` 引用）| INTG-001 §2.2 / 概览 §6-2 | 23s | ✓ |
| 05 | Webhook 创建与 secret 一次性展示 | INTG-002 §3.2 §4.2.1 / 概览 §6-3 | 5s | ✓ |
| 06 | 投递日志 + 重放（5xx 故障注入演练）| INTG-002 §3.3 §4.2.7 / 概览 §6-3 | 10s | ✓ |
| 07 | 50 连败自动停用 + 通知 | INTG-002 §2.4 §4.2.4 / 概览 §6-3 | 25s | ✓ |
| 08 | 接收方 HMAC 验签（参考实现）| INTG-002 §4.3.1 / 概览 §6-3 | 7s | ✓ |
| 09 | 项目进度（days 切换 + 趋势 + 成员任务量）| RPT-002 §3.1/§3.2 / 概览 §6-4 | 9s | ✓ |
| 10 | 统计限流（10/min·user 429）| RPT-002 §4.3.3 / 概览 §6-4 | 5s | ✓ |
| 11 | 生命周期全链（active→archived→active + 模板实化）| PROJ-003 §3.2/§3.3/§2.5 / 概览 §6-5 | 8s | ✓ |
| 12 | 团队治理四区块（归档/全局标签/状态模板/活跃度）| TEAM-003 §3.1 / 概览 §6-6 | 6s | ✓ |
| 13 | 矩阵可见性端到端（a/b）| AUTH-006 §3.3 / 概览 §6-7 | 4s | ✓ |
| 14 | 真 GitHub 联调全链（a/b，github.com 实站）| INTG-001 §2.1/§2.2/§2.3 / 概览 §6-2 | 99s | ✓ |

## 双视口成对视频（a/b 并排观看）

- 幕 02：`02a` 张三视口（web 3001 改标题/状态/评论）；`02b` GitHub mock 视口（dev mock server `localhost:8090`，演示入站 webhook 副作用回流到 a 视口）。
- 幕 06：`06a` 张三（投递日志页 + 死信红点 + 重放）；`06b` 接收方服务（mock `httpbin.org/status/500` 模拟 5xx 实流）。
- 幕 13：`13a` 张三（MEMBER 视口可见项目列表）；`13b` 同一 ws 下的 OUTSIDER 视口（直连 404 + workspace 归档后 403 PERM_WORKSPACE_ARCHIVED）。

## 环境与复跑

前置（实录已验）：API 8000（全量 env）+ web 3001 + worker（`-Q activity,celery`，含 dispatch_github_event/deliver_webhook/project_activity；**broker 必须 amqp://guest:guest@**——celery.py 默认 rp:rp 会被 settings 覆盖为 env 值，env 缺失时 publish 静默丢失）+ 双 mock（`python3 scripts/mock_github_receiver.py 8090` + `python3 scripts/mock_500_receiver.py 8091`）。seed 会把绑定 secret 实注册进 mock 触发器（验签同源）。

```bash
python3 scripts/seed_acceptance_s5.py          # 数据准备（幂等；李四/王五账号自动创建，密码固定）
node scripts/acceptance_video_s5.mjs           # 全量 13 幕（脚本会先自动重跑一次 seed）
ONLY="双向同步三向" node scripts/acceptance_video_s5.mjs   # 单幕重录（幕名子串匹配，逗号分隔多个）
```

产物：`videos/scene-XX[-a|b]-<名称>.webm`（1440×900，每幕独立 context；webm 不入 git）。

## 与 sprint-4 acceptance 的差异

- **新增** `scripts/seed_acceptance_s5.py`（S5 数据准备：基线项目含 GitHub mock 绑定 + 三 Webhook 端点 + 统计样本 + 生命周期四态 + 治理四区块；李四/王五 + 第二 WS 的 OUTSIDER 视口素材）
- **新增** `scripts/acceptance_video_s5.mjs`（13 幕录制器，沿用 s4 同款 Playwright 模式）
- **新增** mock 服务两个（GitHub 接收方 8090 / 5xx 接收方 8091）—— 替代真实外网，零出网
- **新增** `docs/sprint-5-acceptance/SCENARIOS.md`（13 幕场景契约）
- 视频 webm 同 s2/s3/s4 不入 git（.gitignore 补 `docs/sprint-5-acceptance/videos/` 目录规则）；本 README 与 SCENARIOS 入库

## 已知事项

1. **GitHub mock（已实跑全链，2026-09-07）**：`scripts/mock_github_receiver.py`(8090) 扮演 github.com——入站事件（opened/edited/closed/reopened/merged/push）以真实 GitHub 事件族名发 `X-GitHub-Event` 头、按绑定 secret 现算 `X-Hub-Signature-256` 签名投到本站 webhook 端点，**RabbitProjects 的验签/幂等/查重/worker 路由全链真实执行**（幕 02 三向/幕 03 合并流转/幕 04 挂载均为 HTTP→broker→worker→DB 实弹，worker 日志 `mounted:1`/`updated:name` 可复核）。
1a. **真 GitHub 联调（第 14 幕已实拍，2026-09-07）**：GitHub App `rabbit-projects`（App ID 4859835）已安装于 RabbitAI-Lab 组织（installation 159744359）；因 App 未含「Repository webhooks」写权限，repo webhook 注册改走 **App 级 webhook**（`PATCH /app/hook/config` 指向公网穿透域名，签名 secret 与绑定行一致）；入站七链全实弹（建任务/改名/关闭流转/重开/评论镜像/PR 合并自动完成/双 Commit 挂载），出站三链全实弹（标题 `[S5AC-n]` 前缀回写、评论推送、installation token 全 API）；联调暴露并修复两缺陷：issue key 正则漏含数字前缀（已修）、**BR-06 评论双向同步整条缺失（worker 路由 + 出站任务均零实现，已补齐）**。目标仓库 `RabbitAI-Lab/RabbitTest`（公开，b 视口匿名可视为证）。复跑前置：GITHUB_APP_ID/GITHUB_APP_PRIVATE_KEY/GITHUB_WEBHOOK_BASE 三 env 注入 API+worker + 穿透指向 8000 + `node scripts/acceptance_video_s14_real_github.mjs`（独立录制器，不跑 seed、不依赖 mock）。
2. **5xx 故障注入（已实跑）**：`scripts/mock_500_receiver.py`(8091) 恒 500 + `PUT /__mode` 可切 200——幕 06 演示真实退避（投递→500→退避→死信红点→切 200→重放成功），7 次完整退避表（含 6h 尾档）由 sprint-5-flow/test_intg002 全链断言（`apply_async(countdown=)` 序列覆盖 1s/10s/60s/600/3600/21600 全表）。
3. **WebSocket 主动踢出未实现**（A-1#9' 偏差）：禁用账号的 401 收口在 API 面（DRF SessionAuthentication 拒 inactive）已即时，WS 主动推送待 sprint-6 INFRA-005 增强——本录屏幕 13 仅演示 API 端 401。
4. **公开项目 WS_ONLY 通道未实现**（A-1#1）：本录屏不演示"WS_ONLY 访问公开项目"——架构回改后由 sprint-6 收尾补录。
5. **跨工作空间成员越权直连**（A-1#1 配套）：本录屏幕 13 演示"非成员直连 404"，未演示"WS_ONLY 公开项目只读可见"——同上。

观看建议：慢放 0.75× 可看清 Toast/徽标/连线细节；每幕开头的登录/导航即「用户实际入口」演示。
