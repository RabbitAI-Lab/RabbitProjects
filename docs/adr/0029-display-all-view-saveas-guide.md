# ADR-0029：显示配置「全部」裸态另存为引导（体验优化，产品拍板）

> 登记日期：2026-09-08　|　迭代：Sprint 7（体验优化批）　|　状态：已实现

## 背景

用户反馈「字段管理新增字段 → 任务列表显示配置启用该字段 → 无法保存」。排查结论（2026-09-08，API 直连 + 浏览器全链路双重复现）：**非缺陷**——保存链路（后端 `validate_view_payload` / `_sanitize_card_fields` 的 schema 缓存失效、视图 PATCH 持久化）实测正常；根因是用户处于「全部」裸态（BOARD-003 §3.6：前端固定入口、不入库），显示配置抽屉的「保存到视图」按钮 `disabled={!currentView}` 灰置，仅靠悬停 tooltip 说明出路，且裸态下 dirty 黄条只有「另存为/放弃」无「保存」——引导不足致用户无处落子。

用户裁决：按提议优化体验（本 ADR 即该拍板的登记）。

## 改动清单

| # | 位置 | 改动 |
| --- | --- | --- |
| 1 | `apps/web/app/components/views/DisplayDrawer.tsx` | 「全部」裸态下底部按钮由灰置「保存到视图」改为可点「另存为视图」（`data-sb-scope="disp-saveas"`），点击 `onClose()` + 派发 `rp:open-save-view` 事件打开 SaveViewModal（接线与 FilterPanelDrawer「另存为」同款）；有视图时行为不变（`disp-save` 原 PATH 保留） |
| 2 | `apps/web/app/components/views/useViewPage.ts` | `patchDisplay` 在裸态下首次修改时 toast「『全部』不入库：修改仅本次生效，可点『另存为视图』保存」（`allViewHintedRef` 每页面会话一次，防逐开关刷屏） |

### 不变量（未动）

- 「全部」不入库的语义（§3.6）不变——裸态下显示修改仍为会话级临时层；
- 有视图（内置/个人）时「保存到视图」就地 PATCH display_props 的路径与文案不变（S3V-7 既有断言不受影响）；
- SaveViewModal `createView` 在裸态的 displayProps 合成（DEFAULT ⊕ `__all__` override）为既有行为，未改。

## 文档回改（同批完成）

- `docs/sprint-3-views-collab/BOARD-003-multi-kanban.md` §3.3「保存到视图」行补裸态分支；
- `docs/sprint-0-poc/test-cases.md` 附录 C.68「底部动作」行补【条件态·「全部」裸态】。

## 测试

- `tests/e2e/parity-sprint3-views.spec.ts` 新增 **S3V-7b**：裸态断言 `disp-saveas` 可点且 `disp-save` 不存在 → 首改 toast + 二次不刷屏 → 黄条无「保存」→ 另存为弹层（默认名「未命名视图」）→ POST /views/ 201 → `?view_id=` 选中 + 黄条收起（行为断言三件套）。
- 突变自检（2026-09-08 实测）：临时还原旧灰按钮（MUTATION-1）→ S3V-7b 红（`expect(disp-saveas).toBeVisible()` 失败，证明断言咬人）→ 恢复 → S3V-7/7b + 全量 33 条（views + interactions）绿。
