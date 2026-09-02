# Sprint 0 实施复盘：UI parity 三道失败与四道教训

> **来历**：用户真实使用 Sprint 0 三批发现 UI 与冻结稿不一致（项目壳缺失 / 设置页空壳 / 任务弹窗缺 6 项 / 顶栏切换器与头像菜单漏），记录根因、机制补丁与最终落点。**新功能 sprint 必读**，避免重蹈。
> 团队共享资产：个人 Claude Code memory 不入 git，本文件是**唯一权威源**；个人 memory 仅缓存路径引用，详情指向本文。

## 失败时间线

| # | 事件 | 暴露面 | 教训 |
| --- | --- | --- | --- |
| 1 | 冲刺开发占位实现未标"未完成" | 项目侧栏缺失、设置页空壳、任务弹窗缺 6 项 | 占位即视同完成 |
| 2 | 同日晚自查发现顶栏切换器/头像菜单/建团队都缺 | 清单只写了一行"切换器（logo+名称+▾）"，没展开 | 清单由我凭记忆概括=自我印证 |
| 3 | 实现 19 项后端 IssueSerializer 漏 fields 触发 500 | IssueSerializer 加 created_by 但 fields 漏该字段 | 序列化器字段元组易漏 |
| 4 | 退出登录"点了没反应" | Topbar document mousedown 监听与 React click 竞争 | dropdown 通用模板 |

## 教训详解 + 机制补丁

### 1. 占位即视同完成（ADR-0010 第 1 步）

- **根因**：冲刺中"先跑通动线再补 UI"是合理节奏，但占位实现若不标"未完成"就被当交付。
- **机制**：ADR-0010 第 1 步"完成的定义 = 清单核对 + parity 断言"，不允许"能跑"即"完成"。
- **落点**：`tests/e2e/parity.spec.ts` 用 `expect.soft` 全屏字段级扫描；C.1-C.9 附录 C 清单是验收基线。
- **预防**：新增 UI 元素前，先把字段行写进附录 C；不"凭代码看了能跑就当完成"。

### 2. 清单凭记忆概括 = 自我印证（ADR-0010 修订 1 ④ ⑤ ⑥）

- **根因**：首版附录 C 由我（实现者）凭记忆概括 → 顶栏一行只写"切换器（logo+名称+▾）"，没展开下拉/弹窗/菜单子字段 → parity 扫描照样全绿——**清单垃圾进垃圾出**。
- **机制**：
  - ④ 清单行强制溯源——每行标「来源：文档 §x.x」，无来源无效
  - ⑤ 独立覆盖审计——迭代收口前由**未参与实现的 subagent** 反向扫全部文档 §3
  - ⑥ 漏项高发区自查——条件态/下拉内容/禁用态/空态/加载态/toast 文案，逐类过
- **落点**：`test-cases.md` 附录 C.1-C.9（每行带来源§）+ `docs/adr/0010-ui-parity-discipline.md` 修订 1 章节。
- **预防**：写清单时**打开对应文档 §3 或冻结稿逐字段抄录**，禁止凭记忆概括。

### 3. 序列化器字段元组易漏（ADR-0010 修订 2 待登记）

- **根因**：Django 启动时只校验 model fields 是否存在；`SerializerMethodField` 声明但未在 `Meta.fields` 元组中，**GET 路径不触发**（get_field_names 只在 create/update/delete 时校验），**PATCH/POST 路径触发 AssertionError 500**——单跑 e2e 用 GET 列表侥幸通过。
- **机制**（修订 2 待写入 ADR-0010）：
  - 加/移 `SerializerMethodField`：**必加 fields 元组**，顺序可后于 fields 但不能漏
  - commit 前跑 `python3 tests/jmeter/sprint-0-flow.py` 触发 PATCH/POST/DELETE 路径
  - 字段错误典型信号：DRF `AssertionError: The field 'xxx' was declared on serializer Yyy, but has not been included in the 'fields' option` 出现在 500 响应 + 服务日志
- **预防**：commit 时自检"是否动了 Serializer？是否跑了 PATCH？"

### 4. dropdown 全局点击监听吞 onClick（ADR-0010 修订 2 待登记）

- **根因**：Topbar `document.addEventListener("click", close)` 在 document 阶段触发 → React 合成 click 之前先 `setMenu(null)` → onClick 里 `await session.signOut()` 后 `location.href = "/login"` 被后续重渲染吞 → 用户"点了没反应"。
- **机制**（修订 2 待写入）：
  - 用 `mousedown` 替代 `click` 阶段监听（document capture=true）
  - 判 `e.target.closest('[data-sb-scope="..."]')`，仅在 target 不在 popover/触发按钮/menuitem 内时关
  - 破坏性操作（登出、删账户、删工作空间）继续用 `location.href` 全量重载，不依赖 `nav`
- **落点**：`apps/web/app/components/Topbar.tsx` 修法（mousedown + data-sb-scope）。
- **预防**：新增任何 popover/dropdown 必须用此模板，code review 拒绝 `document.addEventListener("click", close)` 写法。

## 累计 ADR（已落库）

- **ADR-0010 UI parity 纪律**（`docs/adr/0010-ui-parity-discipline.md`）——三步纪律 → 五步纪律（修订 1）→ 序列化器+dropdown 补充（修订 2 待写）
- **CLAUDE.md** 工作流约定——同步三步→五步
- **test-cases.md 附录 C**——清单 + 每行带来源§，C.1-C.9 全屏 8 类
- **sprint-overview §10 退出条件**第 4 条"UI parity 全绿"
- **tests/e2e/parity.spec.ts** — expect.soft 全屏扫描 + 失败列全部缺失
- **tests/e2e/coverage.spec.ts** — 4 个 spec 补齐原 Nightly/占位用例

## 未来新 sprint 的复盘流程

1. 实现前 → 检查附录 C 是否有对应字段行（无则先入清单）
2. 实现中 → 占位必标 `// TODO(ai): 真实实现待 Sprint X` 注释
3. 实现后 → 自查清单 + `pnpm exec playwright test` + `python3 tests/jmeter/sprint-0-flow.py`
4. 收口前 → 派独立 subagent 反向扫文档 §3（评审对象=文档 vs 清单，审计者≠实现者）
5. 提交前 → 自问"改过 Serializer？改了 dropdown？跑了 PATCH 路径？"
6. 收尾 → 若发现新类别教训（如本 Sprint 的 3/4），追加到本文件 + ADR-0010 修订条目
