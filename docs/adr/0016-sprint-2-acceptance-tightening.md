# ADR-0016 · Sprint-2 验收标准收紧（用户裁决）

| 项 | 内容 |
| --- | --- |
| 状态 | 已接受（Accepted） |
| 日期 | 2026-09-05 |
| 背景 | 迭代交付评审时用户质询三项放宽项，裁决「不应该放宽标准，需要严格验收」，三项全部收紧 |
| 关联 | ADR-0014 A-2（测试基建）、TASK-010 §4.2.2/§4.3.2、CLAUDE.md 测试段 |

## T-1 死信端点权限：去掉「表空放行」开发口径

- **原状**：`SystemAdmin` 表为空时任何登录用户可访问 `/api/v1/activity-dead-letters/`（开发便利）。
- **收紧**：仅 `SystemAdmin` 表 active 成员；非成员一律 `403 PERM_DENIED`。测试授予走显式
  INSERT（flow T10-12 前置）；前端死信页新增 403 分支（权限空态「需要系统审计权限」，
  非误导性「没有死信」空态，e2e T010-3 锚定）。
- **锚定**：flow T10-12（授权后可达）/ T10-19（非 SystemAdmin 403）；spec T010-2（403 负向）+ T010-3（页面分支）。

## T-2 Activity 主写路径全量异步化

- **原状**：主更新路径（PATCH 字段 diff / custom_fields 逐键 / 标签 / 创建 / 子任务挂载）
  在 `on_commit` 后**同步 INSERT**（不阻塞事务但占用请求线程）——与 TASK-010 §1.4
  「活动时间线不阻塞主请求」的架构承诺部分不符。
- **收紧**：`_record_activity` 统一改为投递 `record_activity_row` Worker（行级幂等：
  `(issue, actor, verb, epoch, field, old_id, new_id)` 全键去重；重试 1s/4s/16s；DLQ 兜底）。
  五个同步落库点经此入口零改动全异步化；应用层不再有 `IssueActivity.objects.create` 直写
  （Worker 内部除外）。broker 不可用时 enqueue 内部降级同步直写（保「业务成功可追溯」底线）。
- **验证**：本地常驻 worker（activity+celery 队列）下 flow 168 断言全绿（含全部时间线断言）；
  pytest 86 无损（单元测试不依赖投递路径）。

## T-3 T8-42 恢复硬断言 + worker 前置纪律

- **原状**：「删除字段异步清理」断言在无 worker 环境降级 SKIP。
- **收紧**：恢复为硬断言；`sprint-2-flow.py` 运行前置 = 常驻 worker（CLAUDE.md 测试段已登记
  启动命令）。附带修复：celery include 遗漏 `plane.account.tasks`（worker 侧 unregistered）。
- **说明**：三条 flow 的 CI job 尚未建立（仓库既有状态：flow 为本地 gate，api-ci 只含
  ruff/mypy/pytest）——如需 flow 进 CI 需 pg/redis/mq services + flow 的 docker-exec
  依赖参数化，作为独立工作项另行排期（非本次放宽的回补）。
