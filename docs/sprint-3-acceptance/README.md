# Sprint-3 验收录屏（14 幕，全部从用户实际入口出发）

| 幕 | 场景 | 验收条款 | 时长 | 结果 |
| --- | --- | --- | --- | --- |
| 01 | 视图保存与还原 | BOARD-003 §7.2-1 / 概览 §6-2 | 17s | ✓ |
| 02 | 四布局切换 | BOARD-003 §7.2-2 / TASK-011 §7.2-3 | 11s | ✓ |
| 03 | 多维分组与拖拽 | BOARD-003 §7.2-3/4 / BR-14/15 | 16s | ✓ |
| 04 | 组合筛选器（a/b 成对） | TASK-011 §7.2-1/2 / 概览 §6-8 | 16s | ✓ |
| 05 | 批量操作全链路 | BOARD-004 §7.2-2 / 概览 §6-4 | 13s | ✓ |
| 06 | 批量删除确认 | BOARD-004 §7.2-4 | 26s | ✓ |
| 07 | 楼中楼与表情 | COLLAB-002 §7.2-1/2 | 16s | ✓ |
| 08 | 图片评论与灯箱 | COLLAB-002 §7.2-3 | 12s | ✓ |
| 09 | 父删子留 | COLLAB-002 §7.2-4 | 7s | ✓ |
| 10 | 项目动态流 | COLLAB-003 §7.2-1/3 / 概览 §6-6 | 41s | ✓ |
| 11 | 双端实时同步（a/b 成对） | COLLAB-004 §7.2-1/3 / 概览 §6-7 | 18s | ✓ |
| 12 | 断线补偿与降级 | COLLAB-004 §7.2-4/5 | 38s | ✓ |
| 13 | 越权404 | 概览 §6-9 | 15s | ✓ |
| 14 | 内置视图与默认（a/b 成对） | BOARD-003 §7.2-5 / BR-10 | 17s | ✓ |

## 双视口成对视频（a/b 并排观看）

- 幕 4：`04a` 构建三层嵌套筛选树并应用；`04b` 第二浏览器粘贴分享 URL 还原整树。
- 幕 11：`11a` 张三视口（看板远端迁移 / 动态页新行划入 / 铃铛 +1）；`11b` 李四视口（拖卡 / 改优先级 / 回复评论）——两视口同一时间轴编排。
- 幕 14：`14a` 张三（五内置在场 🔒 → 右键设默认 → 重进项目直达）；`14b` 李四（同一「我的待办」@me 各自生效）。

## 环境与复跑

前置（与 sprint-2 同栈 + 实时/对象存储）：API(8000，含票据密钥与 INTERNAL_KEY) + Web(3001) + Celery worker（activity 队列，`celery inspect registered` 应含 `plane.bgtasks.event_publisher.publish_event`）+ live(3000，Redis 连通) + MinIO(9000) + 演示账号 bootstrap（zhangsan@rabbit.dev 一键进入）。

```bash
python3 scripts/seed_acceptance_s3.py     # 数据准备（幂等；李四/王五账号自动创建，密码固定）
node scripts/acceptance_video_s3.mjs      # 全量 14 幕（脚本会先自动重跑一次 seed）
ONLY=组合筛选器 node scripts/acceptance_video_s3.mjs   # 单幕重录（幕名子串匹配，逗号分隔多个）
```

产物：`videos/scene-XX[-a|b]-<名称>.webm`（1440×900，每幕独立 context；webm 不入 git）。

## 录制手段备注

- 全部幕走真实用户入口：登录页 →（一键演示账号 / 李四表单登录 → 顶栏切工作空间）→ 项目卡片 → 侧栏导航。深链 goto 仅两处且均为契约明文：幕 1/4 的「URL ?view_id= / ?filters= 分享直达」（被验收功能本身）与幕 13 的越权错误分支（先登录后直达他人视图 / 被移出成员动态页）。
- 幕 11 拖卡远端可见耗时实测 <10ms（PATCH 响应后 DOM 已迁移；独立探针全链路 worker→Redis→live→对端约 123ms，满足 IT-01 <1s）。铃铛 +1 走真实链路：李四回复张三顶层评论 → COMMENT_REPLIED 通知 → user 房间。
- 幕 12 断网模拟：`context.setOffline(true)` 封死新连接/REST + 页内 WebSocket 注册表主动断开既有 `/live/connect`（Chromium 网络模拟对已建 WS 的处置不稳定，双保险；live/api 服务全程未动）。降级横幅由真实状态机在连接失败累计 30s 后触发，恢复后 [立即重连] 触发补偿拉取（group_by 全列收敛）。

## 与 sprint-2 验收目录的差异

- 本目录新增 `scripts/seed_acceptance_s3.py`（S3 数据准备，走 API、幂等）与 `scripts/acceptance_video_s3.mjs`（14 幕录制器）；沿用 sprint-2 的 `scripts/seed_acceptance.py` / `scripts/acceptance_video.mjs` 模式但未改动它们。
- 幕 4/11/14 为双视口成对视频（sprint-2 全部单视口）；幕 12 因 live 属用户 dev 树不可停，改为浏览器断网模拟（sprint-2 无此约束）。
- 视频 webm 同 sprint-2 一样不入 git；本 README 与 SCENARIOS.md 入库。

## 已知缺陷（录制期间发现，未在本次修复）

1. **P2 · 列表端点对他人个人视图未校验归属**（幕 13 触发）：`GET …/issues/?view_id=` 对「存在但他人个人视图」返回 200（仅校验存在性）；随机不存在 id 才 404。前端侧 ViewStore 不可见 → 黄条回退「全部」正常。规格口径：BOARD-003 §4.2-6 / BR-11 要求不可见视图 404 存在性隐藏。最小复现：李四会话 `GET /api/v1/workspaces/workspace/projects/{pid}/issues/?view_id={张三个人视图id}` → 200。
2. **P1（潜在）· 实时事件 version 时区混用**（幕 11 排查期间发现）：worker 载荷 `version=updated_at.isoformat()` 为 UTC（+00:00），REST 列表/分组端点序列化为本地时区（+08:00），前端 BR-07 按字符串比较恒判「旧于本地」→ `issue.state.changed` 对已加载卡片的定向更新被静默丢弃。看板拖卡因伴随 `sort_order` 变更走 `board.moved`（无 version 门）而掩盖；纯状态变更（列表页/批量单卡）不会实时迁移对端看板。最小复现：A 端看板已含某卡，B 端 `PATCH {state_id}`（不含 sort_order）→ A 收到 `issue.state.changed` 帧但卡片不迁移。

观看建议：慢放 0.75× 可看清 Toast 与动画细节；每幕开头的登录/导航即「用户实际入口」演示。

