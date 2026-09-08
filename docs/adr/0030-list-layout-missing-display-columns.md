# ADR-0030：列表布局漏接 display_props.columns（BOARD-003 §3.3 / TASK-011 BR-13 违反）

> 登记日期：2026-09-08　|　迭代：Sprint 7（体验优化批）　|　状态：已修复

## 背景与排查

用户反馈：「列表和表格，我在显示里面，修改列的隐藏显示后，列表和表格展示内容不同，列表好像没正确按照配置展示，表格是正确的。」

排查（API 直连 + 浏览器全链路双重复现）：
- **表格布局**（`apps/web/app/routes/table.tsx:80-126`）正确消费 `vp.effDisplay.columns`：表头按 `visibleCols.map` 渲染，单元走内联 `cell()` 函数（含 key/title/state/assignees/due/priority/labels 7 列，'-' 前缀 = 隐藏）。
- **列表布局**（`apps/web/app/routes/issues-list.tsx`）对 `columns`/`effDisplay` **零消费**：grep 全部命中为零。表头是字面量数组 `["编号", "标题", "状态", "负责人", "截止时间"]`（line 1242–1253 旧版），单元格是逐列硬编码。
- 文档侧三处承诺列表布局应受显示配置控制：(1) BOARD-003 §3.3 表格「列（列表布局）」+ 原型 O3；(2) TASK-011 BR-13「四布局共用 `display_props.columns`」；(3) §4.1.1 数据模型。
- 根因：**Sprint-3 视图体系接线时只接了新建的表格页，遗漏了 Sprint-2 起的列表页**。

## 修复要点

1. **共用渲染器**（`apps/web/app/components/views/TableCell.tsx`，新建）：把表格页内联 `cell()` 抽出为纯函数 `renderTableCell(it, col, { nameOf, today, labels })`，依赖作为参数注入（两布局的 `nameOf`/`labels` 数据源不同）。
2. **表格页改造**（`apps/web/app/routes/table.tsx`）：删除内联 cell()，改 `import { renderTableCell } from "../components/views/TableCell"`；`renderCell(it, c) = renderTableCell(it, c, { nameOf, today, labels: vp.labels })`。
3. **列表页改造**（`apps/web/app/routes/issues-list.tsx`）：
   - 删除本地 `members` useState + 它的 useEffect（仍保留 QuickAssignPop 必需的带 role 成员数据用于角色过滤，但 nameOf 走 vp.members）；
   - 新增 `visibleCols = useMemo(() => (vp.effDisplay.columns ?? Object.keys(TABLE_COL_NAMES)).filter(c => !c.startsWith("-")), [vp.effDisplay.columns])`，与表格完全一致；
   - 表头按 `visibleCols.map(c => TABLE_COL_NAMES[c] ?? c)` 渲染，每 `<th>` 加 `data-col={c}` 属性便于断言；
   - 主体改为 `visibleCols.map`：标题列特化（保留树形专属元素：缩进/折叠/依赖图标/归档/快速加子/拖拽把手/行菜单/加载失败重试），其他 6 列（key/state/assignees/due/priority/labels）走共用渲染器；
   - **空态也渲染表头**（`filtered.length === 0` 旧版只渲染空态，不渲染 `<table>`）—— 修复路径上的副 bug，让用户在无任务时也能调显示配置。
   - 工时列（wlColumn）/ 自定义列（cfCols）/ 子任务列仍为列表布局扩展列（不在 display_props.columns 7 键域内），拼在基础列之后。
   - colSpan 计算改为 `1 + visibleCols.length + (wlColumn ? 1 : 0) + cfCols.length + 1`（行首复选列 + 核心列 + 扩展列 + 子任务列）。
4. **「截止」列**统一为 MM-DD（`.slice(5)`）+ 逾期加粗红色（与表格同构）；「标签」列改为色块胶囊（与表格同构）。

## 实现偏差登记（与本次修复一并发现）

**BOARD-003 §4.1.1 文档与代码列 key 不一致**：
- 文档写：`columns: ["issue_key", "name", "state_id", "assignee_ids", "target_date"]`
- 代码用：`TABLE_COLUMN_KEYS = ["key", "title", "state", "assignees", "due", "priority", "labels"]`
- 裁决：**以代码为准**（已上线、被 BR-09 持久化、`view-dsl.ts:107-110` 的 `TABLE_COL_NAMES` 同源）。文档待回改列入 known-debt（本批不展开）。

## 不变量

- 「全部」裸态设计与 `_sanitize_card_fields` schema 缓存净化逻辑（BR-09）均不变。
- 表格页列渲染**与列表页完全同构**——任何后续修改列默认/新增列类型只需改 `TableCell.tsx` 一处。
- 列表布局的扩展列（工时/cf_*/子任务）独立于视图配置，符合「核心 7 列由视图控制、列表专属列由列表自己的 localStorage 偏好控制」的产品分界。

## 测试

- e2e S3V-10b 已新增：登录 → 创项目 → 造任务 → 切需求池视图 → 进入列表布局 → 断言 7 列 `<th data-col>` 可见 → 显示配置切隐藏优先级列 → PATCH 200 → 断言列表 `<th data-col="priority">` 消失 + `<th data-col="title">` 仍可见 → 切表格布局 → 同一份 columns 同步生效 → 表格 `<th data-col="priority">` 也隐藏。
- 突变自检：列表页 `<thead>` 临时还原硬编码列头（`visibleCols` → 字面量数组）→ S3V-10b 红 → 恢复 → 绿（闭环）。
- 浏览器手动复验：详见工作区对话。
- 全量回归：views 13 条 + interactions 20 条 + S3V-10b 共 34 条应全绿（PARITY-0030 收口后）。

## 未展开（列为后续 known-debt）

- DisplayDrawer 列 chip 拖拽排序（文档承诺「拖拽排序 · 点击显隐」当前仅点击显隐）。
- BOARD-003 §4.1.1 文档列 key 偏差本身的回改（本 ADR 仅登记）。
- 列表布局嵌套行（quickSubOf）所在行的 colSpan 已被显式改为基于 visibleCols.length，但浏览器老版本未审到的极端 colSpan 边界（如 visibleCols 空数组）尚未单独 e2e 覆盖。
