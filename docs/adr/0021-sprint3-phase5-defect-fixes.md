# ADR-0021 · Sprint-3 Phase 5 验收发现的缺陷修复：视图应用面越权与时区版本比较

| 项 | 内容 |
| --- | --- |
| 状态 | 已接受（Accepted） |
| 日期 | 2026-09-06 |
| 背景 | Phase 5 十四幕验收视频录制（`scripts/acceptance_video_s3.mjs`）实测发现两处缺陷：幕 13 越权场景暴露 P2、幕 11 深挖暴露 P1。本 ADR 固化修复口径 |
| 关联 | `docs/sprint-3-views-collab/BOARD-003-multi-kanban.md`（§4.2-6 / BR-11 / §6-9）、`docs/sprint-3-views-collab/COLLAB-004-websocket-sync.md`（BR-07）、`docs/sprint-3-acceptance/`（SCENARIOS.md 幕 11/13）、`apps/api/plane/app/views/issues.py`、`apps/web/app/realtime/useBoardLiveSync.ts` |

## 决策 1（P2）：`?view_id=` 应用面存在性隐藏收严

- **现象**：`GET …/issues/?view_id=<他人个人视图>` 对「存在但非本人」的视图返回 200——T3-04 实现将 `views/{id}/` 详情面的 board.manage 审计通道（CONTRIBUTOR+ 可读他人视图）误带入列表/分组消费面，违反 §6-9「任何越权访问均 404」与 BR-11 可见性口径（随机不存在 id 才 404，存在的他人视图反而可应用）。
- **修复**：应用面（`IssueListCreateView.list` 的 view_id 展开）仅 `is_system or owner==user` 可用，其余一律 404 存在性隐藏；board.manage 审计通道**只**保留在 `views/{view_id}/` CRUD 面（GET 详情/PATCH/DELETE，BOARD-003 §4.2 表第 3 行原文语义）。
- **验证**：`test_issue_grouping.py::test_other_personal_view_access_matrix` 断言更新（本人 200 / 隐式 ADMIN 404 / VIEWER 404）；幕 13 重录后 API 404 实录。

## 决策 2（P1）：实时事件 version 的时区统一比较

- **现象**：Worker 事件载荷 `version = issue.updated_at.isoformat()` 为 **UTC（+00:00）**，REST 序列化 `updated_at` 在 `TIME_ZONE="Asia/Shanghai"` + `USE_TZ=True` 下为**本地（+08:00）**；前端 BR-07 采用字符串比较，跨时区偏移恒判 stale → `issue.state.changed` 定向更新被静默丢弃（纯状态变更不同步对端看板；看板拖卡因 `board.moved` 无 version 门而被掩盖，故 e2e/视频拖卡路径未暴露）。
- **修复**：前端 `isStale` 改为 `Date.parse` 数值比较（对 UTC/本地两种表示均正确；解析失败退回字符串比较兜底）。事件侧维持 UTC ISO（带偏移的合法 ISO 8601，数值比较天然处理）。
- **验证**：数值比较对 `+00:00`/`+08:00`/`Z` 三形态等价（解析级验证）；既有 S3R-1 乱序免疫用例（远旧 version 不得触发收敛）复跑保持绿。

## 影响

- 两处均为行为修复而非契约变更：应用面 404 收严与 §6-9 验收一致；version 比较是纯前端内部逻辑。
- BOARD-003 §4.2-6 注无需勘误（规格本意即存在性隐藏，属实现偏差回改，收口进本 ADR）。
