# P4 迭代已知技术债（随轮收口）

> 登记日期：2026-09-11　|　维护口径：R0 基线收口时登记的第一批 + 各轮新发现；每项注明归属轮次，收口后移入 B 区留档。

## A. 待收口（按归属轮次）

| # | 项 | 现象与依据 | 归属 |
| --- | --- | --- | --- |
| 1 | e2e 顺序依赖残留三例（S4G-9 甘特 VIEWER 只读 / S4P-7 WS 版本面板刷新 / REG-1 评论头像） | 2026-09-10 全量 e2e：166 过 / 3 失败；三条**单跑均通过**、且在 R0 改动之前的全量跑里即已失败——顺序依赖型（REG-1 表现为 fill 后提交钮未启用，疑似重复抽屉 DOM 或编辑器状态残留；S4G/S4P 为实时类抖动）。GitHub e2e 门禁（冒烟子集）不受影响 | R1（AUTH-012 e2e 编写时顺带收口；实时类若复现归 R7 INTG-003 轮） |
| 2 | 权限快照接口 `custom_codes` N+1（`/users/me/permissions/` 206 查询 @ 2511 项目） | R0 排查实测：每项目一查，项目数线性放大；清理后恢复毫秒级。大租户（P4 AUTH-012 目标场景 ≥200 项目）会复现 450ms+，加剧 fail-closed 竞态窗口（UI 已由 usePermissionSync 根治，但接口本身应批量化） | R1（AUTH-012 治理域触碰 permissions_api 时批量化 + assertNumQueries 基线上调登记） |
| 3 | web lint 余量 24 条 warning（影子变量 ×7 / 下划线命名 ×8 / exhaustive-effect-deps ×11 / iframe-missing-sandbox ×1 / no-unstable-nested-components ×1 等） | 不拦门禁（error 级已清零）。其中 **iframe-missing-sandbox 属安全向**：预览 iframe 加 sandbox 属性会改变行为（需按 FILE-003 预览能力逐 allow-* 配置），不能顺手改 | R9 FILE-006 轮（预览/水印域触碰 iframe 时一并配置）；影子变量等随触碰文件顺手清 |
| 4 | e2e 造数残留的清理纪律缺口 | R0 清理时库中堆积 2803 个残渣项目 / 55 个泄漏工作区 / 7.6 万 issue（switch-team-*、S7UI W7*、Drawer、T5 等无 afterAll 清理的旧 spec 所致）。新 spec 已按「坑 22」接清理；旧 spec 的清理补齐未做 | R1 起 e2e 编写时逐 spec 补 afterAll；存量已物理清理（2026-09-10），后续以 `docker exec rp-pg psql` 计数对比 ORM 自证 |

## B. 已收口（留档）

- （暂无）
