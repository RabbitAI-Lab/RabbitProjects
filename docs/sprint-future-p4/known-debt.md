# P4 迭代已知技术债（随轮收口）

> 登记日期：2026-09-11　|　维护口径：R0 基线收口时登记的第一批 + 各轮新发现；每项注明归属轮次，收口后移入 B 区留档。

## A. 待收口（按归属轮次）

| # | 项 | 现象与依据 | 归属 |
| --- | --- | --- | --- |
| 3 | web lint 余量 24 条 warning（影子变量 ×7 / 下划线命名 ×8 / exhaustive-effect-deps ×11 / iframe-missing-sandbox ×1 / no-unstable-nested-components ×1 等） | 不拦门禁（error 级已清零）。其中 **iframe-missing-sandbox 属安全向**：预览 iframe 加 sandbox 属性会改变行为（需按 FILE-003 预览能力逐 allow-* 配置），不能顺手改 | R9 FILE-006 轮（预览/水印域触碰 iframe 时一并配置）；影子变量等随触碰文件顺手清 |
| 4 | e2e 造数残留的清理纪律缺口 | R0 清理时库中堆积 2803 个残渣项目 / 55 个泄漏工作区 / 7.6 万 issue（switch-team-*、S7UI W7*、Drawer、T5 等无 afterAll 清理的旧 spec 所致）。新 spec 已按「坑 22」接清理；旧 spec 的清理补齐未做 | R1 起 e2e 编写时逐 spec 补 afterAll；存量已物理清理（2026-09-10），后续以 `docker exec rp-pg psql` 计数对比 ORM 自证。**2026-09-11 加固**：`_cleanup_s4.py` 修 NOT NULL FK 表置空炸点（workspace_login_daily 改 DELETE）+ DEMO_KEEP 补 sso-demo |
| 5 | 治理域通知通道占位 | AUTH-012 处置/申诉/L2 的「通知平台运营与客户」当前为日志（execute_action alert 档 logger.info）；COLLAB-005 统一消息推送策略落地时统一接 | R15 COLLAB-005 |
| 6 | 边界报告产物为 JSON 非 PDF | BR-10 数据契约（隔离机制清单/90 天计数/审计样本）齐备，PDF 渲染依赖待选型（reportlab/weasyprint） | R1 尾部（R3 演示视频前） |

## B. 已收口（留档）

- **#1 e2e 三残留（2026-09-11 收口，根因修正）**：非「顺序依赖」——真根因是**演示工作区成员数顶满 MAX_WORKSPACE_MEMBERS=100**（s3r/s3s 族残留成员 97 个 + `_cleanup_s4.py` 因 NOT NULL FK 表置空炸点而失效无法自治）→ 新注册用户（带 pending 邀请）注册钩子自动接受邀请撞软限 409 → 注册事务回滚 → S4G-9/S4P-7 等待 `/projects` 超时。REG-1 另有独立根因：评论区受控挂载竞态（tab 点击后立刻 fill 被 draft="" 首渲染重置）。修复：软移除 97 残留成员（余 3 演示号）、清理脚本修 DELETE 分支 + DEMO_KEEP 补 sso-demo、REG-1 加 `0/5000` 计数器就绪等待。三 spec 连跑全绿（20 passed / 1 skipped）。
- **#2 permissions N+1（2026-09-11 收口）**：`custom_codes_bulk` 一次 IN 预取替代逐项目查询（206 → 常数 5），`test_permissions_api.py` 以 `django_assert_num_queries(5)` 锁基线 + 批量/单点语义一致性双断言。
