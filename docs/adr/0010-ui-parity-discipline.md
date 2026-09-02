# ADR-0010 · UI parity 纪律（实现 vs 冻结稿一致性机制）

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-09-02 |
| 状态 | 已采纳（强制） |
| 背景 | 真实使用连续发现三处实现与冻结稿不一致：项目壳缺失（任务列表/设置从 UI 不可达）、设置页空壳（不拉详情 + PATCH 空名可清库）、创建任务弹窗缺 6 项规格。需求与冻结稿本身无模糊——TASK-001 §3.2.2 八行表格齐全，原型可交互可验证 |
| 根因 | ① 冲刺开发中占位实现未标注"未完成"即被当交付；② 四道既有检查（原型评审、测试文档 5 轮评分、e2e 7/7、验收 6 条）**没有一道的评审对象是「实现 vs 冻结稿」的字段级一致性**；③ e2e 由实现者从同一心智模型编写，断言已实现子集 → 自我印证；④ 测试用例按功能文档组织（行为级），无「页面×组件×字段」清单 |
| 决策 | ① UI 表面清单（test-cases.md 附录 C）作为新交付物，新页面/弹窗**先入清单再实现**；② `tests/e2e/parity.spec.ts` 用 `expect.soft` 对全屏字段做存在/可见/可用断言，一次跑出全部缺失；③ e2e 断言由清单生成，不由实现反推；④ sprint-overview §10 退出条件追加"UI parity 全绿" |
| 后果 | 新增 UI 时多一步清单登记（约 5 分钟/屏）；换来冻结稿→实现→测试三点一线可审计。原型已知边界（如工具条装饰外壳）在清单中显式登记为"装饰外壳"，不视为缺陷 |
| 修订 1（2026-09-02 晚）| 首版清单暴露新漏洞：清单本身由实现者凭记忆概括（如顶栏一行只写"切换器（logo+名称+▾）"），未展开下拉/弹窗/菜单子字段 → 顶栏切换器、创建团队、头像菜单三件套整体漏网。**补救两条强制规则**：⑤ 清单行强制溯源——每行标「来源：文档 §x.x」，无来源的行无效，禁止凭记忆概括；⑥ 独立覆盖审计——每个迭代收口前，由未参与实现的 subagent 反向扫描全部功能文档 §3 章节，核对清单覆盖率（评审对象=文档 vs 清单），审计报告归档 docs/plan/ |

| 修订 2（2026-09-02 晚）| 实施 19 项后两起隐性 bug：① DRF `SerializerMethodField` 声明未在 `Meta.fields` 元组中 → GET 路径不触发 `get_field_names` assert，PATCH/POST 路径触发 500（单跑 e2e GET 列表侥幸通过）；② Topbar `document.addEventListener("click", close)` 在 document 阶段先触发 `setMenu(null)`，React 合成 click 之前被吞 → onClick 内 `await signOut()` 后 `location.href = "/login"` 被重渲染覆盖 → 用户"点了没反应"。**补救两条强制规则**：⑦ 序列化器字段元组——加/移 `SerializerMethodField` **必加 fields 元组**，commit 前跑 PATCH 路径；⑧ dropdown 通用模板——禁止 `document.addEventListener("click", close)` 写法，改 `mousedown` 阶段 + `target.closest('[data-sb-scope="..."]')` 判范围，破坏性操作（登出/删账户/删工作空间）继续用 `location.href` 全量重载。详细事件链：docs/sprint-0-poc/lessons-learned.md |
