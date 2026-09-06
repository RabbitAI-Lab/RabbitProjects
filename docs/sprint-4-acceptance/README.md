# Sprint-4 验收录屏（13 幕，全部从用户实际入口出发）

> 概览 §6 六条验收条款的幕映射：§6-2 甘特（幕 1~6）｜§6-3 文件（幕 7~10、12）｜§6-4 权限（幕 11）｜
> §6-6 无障碍键盘（幕 5 含行选择/T/1/2/3/Shift 全套）｜§6-1（五规格文档结构）与 §6-5（孤儿清理/配额）
> 为后台行为非用户路径，不设幕——分别由门禁（五规格评审 + sprint-4-flow 213 断言含 IT-09 三段）覆盖。

| 幕 | 场景 | 验收条款 | 时长 | 结果 |
| --- | --- | --- | --- | --- |
| 01 | 万级滚动与粒度切换 | GANTT-001 IT-01/IT-02 / 概览 §6-2 | 20s | ✓ |
| 02 | 拖拽改期持久化 | GANTT-002 §7.2 / 概览 §6-2 | 7s | ✓ |
| 03 | 依赖连线与冲突确认 | GANTT-002 §3.1 / TASK-005 / 概览 §6-2 | 12s | ✓ |
| 04 | 延期概览 | GANTT-002 §3.2（C.109）/ 概览 §6-2 | 8s | ✓ |
| 05 | 未排期入轨与键盘改期 | GANTT-002 C.108/C.111 / 概览 §6-6 | 11s | ✓ |
| 06 | PNG导出 | GANTT-002 §4.3.3（C.110）/ 概览 §6-2 | 8s | ✓ |
| 07 | 百兆分片续传 | FILE-003 §7.2（C.119）/ 概览 §6-3 | 13s | ✓ |
| 08 | 五通道预览 | FILE-003 §2.3（C.120）/ 概览 §6-3 | 15s | ✓ |
| 09 | 多版本回滚 | FILE-003 §3.4（C.121）/ 概览 §6-3 | 10s | ✓ |
| 10 | 分享全链路（a/b 成对） | FILE-004 §7.2（C.123/C.125）/ 概览 §6-3 | 13s | ✓ |
| 11 | 三态权限越权（a/b 成对） | FILE-002 §4（C.113/C.116）/ 概览 §6-4 | 15s | ✓ |
| 12 | 回收站往返 | FILE-002 §3.4（C.117）/ 概览 §6-3 | 12s | ✓ |
| 13 | 双端实时（a/b 成对） | GANTT-002 §4.4 + FILE-003 §4.4 / COLLAB-004 / 概览 §6-2/§6-3 | 15s | ✓ |

## 双视口成对视频（a/b 并排观看）

- 幕 10：`10a` 张三视口（web 3001，创建分享弹层三件套）；`10b` 匿名收件人视口（space 3003——密码门/解锁/预览/下载/仅预览）。
- 幕 11：`11a` 张三（ADMIN）UI 现场改三态；`11b` 李四（CONTRIBUTOR）改前对照 → 改后列表剪枝 + 直连 404。
- 幕 13：`13a` 张三（拖改期 + 同名上传 v2）；`13b` 李四（甘特条 2s 内同步 + 版本面板无刷新自更新）。

## 环境与复跑

前置：API 8000（**全量 env 含 AWS_S3_***，CLAUDE.md 坑 #14）+ web 3001 + live 3000（`pnpm dev` 带起，坑 #17 密钥三件套）+ Celery worker（activity 队列单代，`celery inspect registered` 应含 `event_publisher`）+ MinIO 9000 + **space 3003**（`pnpm dev:space`；幕 10/13）+ PG（rp-pg）+ 演示账号 bootstrap（zhangsan@rabbit.dev 一键进入）。

```bash
python3 scripts/seed_acceptance_s4.py          # 数据准备（幂等；李四/王五账号自动创建，密码固定）
node scripts/acceptance_video_s4.mjs           # 全量 13 幕（脚本会先自动重跑一次 seed）
ONLY=分享全链路 node scripts/acceptance_video_s4.mjs   # 单幕重录（幕名子串匹配，逗号分隔多个）
```

产物：`videos/scene-XX[-a|b]-<名称>.webm`（1440×900，每幕独立 context；webm 不入 git）。

### 万级数据集说明（幕 1）

幕 1 的 10,100 任务 / 5 年跨度 / 1000 连线数据集由 `seed_acceptance_s4.py --gantt10k` 现场 SQL 构造（与 `tests/jmeter/sprint-4-bench-gantt.py` 同款——`generate_series` 直灌 + 确定性 UUID），**录完即清**（`--gantt10k-clean` 复查零残留），不进常驻演示库。首屏 <1.5s 为 bench G1 门禁口径（rows + relations/bulk 合并 P95，实测 P95 99.5ms）；本次录屏单次实测：**UI 到首条 342ms，rows 合计 66ms + relations 合计 30ms = 96ms**（含 Playwright 事件开销的墙钟；门禁以 bench P95 为准，录屏演示流畅性）。

### 已知事项

1. **Office 预览排队态（幕 8 ⑤）**：本机无 `soffice`（LibreOffice），Office 转码按 ADR-0022 D-1 如实展示 202 排队态（「正在转码预览… 预计约 N 秒」+ LibreOffice 异步转 PDF 说明 + 先下载兜底）；compose 工具链分层已就位，装 soffice 后该通道转 ready 态。其余四通道（图片/PDF/文本/视频）为 ready 实操。
2. **视频通道样本为 webm**（`产品演示录屏.webm`，取 sprint-3 验收录屏真实产物）：本机无 ffmpeg 无法生成 mp4 样本；webm 与 mp4 同属官方流式白名单（FILE-003 BR-12），通道行为一致。且视频扩展名仅分片会话白名单可达（直传 presign 白名单零回改——FILE-003 §1.4 #5 已知口径），故该样本经分片通道入库。
3. **PNG 导出水印（幕 6）**：水印三行（项目名 / 时间戳 / 导出人）在导出渲染期间临时挂载、不驻留 UI——录屏画面不可见；导出产物已存 `/tmp/s4-gantt-export.png` 并经视觉核验（右下角三行：`S4 验收演示 / 2026-09-07 06:24 / 张三`，2× 分辨率 1800×1318）。
4. 演示项目（S4 验收演示 / S4AC）录制后保留供回看；幕 1 万级数据集录完即清（见上）。

## 与 sprint-3 验收目录的差异

- 新增 `scripts/seed_acceptance_s4.py`（S4 数据准备：幂等 SQL 清场 + API 造数 + `--gantt10k` 万级数据集建/清）与 `scripts/acceptance_video_s4.mjs`（13 幕录制器）；沿用 s2/s3 的 seed + 录制器模式未改动它们。
- 双视口成对视频从 s3 的 3 幕扩到 4 幕（新增匿名收件人视口——space 3003 域）；s3 的断线补偿幕（停 live 模拟）无对应 S4 条款，不在本批。
- 视频 webm 同 s2/s3 不入 git（.gitignore 补 `docs/sprint-4-acceptance/videos/` 目录规则）；本 README 与 SCENARIOS.md 入库。

观看建议：慢放 0.75× 可看清 Toast/徽标/连线细节；每幕开头的登录/导航即「用户实际入口」演示。

