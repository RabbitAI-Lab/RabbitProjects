# ADR-0027：Sprint-6 实现偏差登记（INFRA-005 限流 / 备份 / 部署加固）

> 状态：已采纳　|　日期：2026-09-08　|　迭代：Sprint-6（T6-01/02/03/07）
> 依据：sprint-overview §10.3（架构偏离先登记再继续）；消费面 known-tech-debt（Sprint-7+）

## 背景

Sprint-6 为功能冻结的发布迭代，INFRA-005（限流/备份/部署）与 QA-001（质量门禁）
落地过程中出现以下与规格文本不一致、但经论证更优或受环境约束的实现决策，逐条登记。

## 偏差清单

### 1. L2 全局限流配置位置与门控（T6-01）

- **规格**：`DEFAULT_THROTTLE_CLASSES` 写入 settings/production.py 增量。
- **实现**：写入 base.py 全量 + `RATE_LIMIT_ENABLED` 运行时开关门控
  （dev/test 缺省关、prod 置 True、验收演示 env 可开）。
- **理由**：settings 级 prod-only 与规格自身的 ViewSet 端展开模式
  `[*BASE_THROTTLES, X]` 矛盾——显式列表在 dev 也会带起 L2（jMeter flow 六套
  与 574 项 pytest 会被全局 60/min 打爆），或不展开则 prod 下 L3 视图静默失去
  L2。运行时开关两头都解。
- **影响面**：`plane/base/throttling.py`（模块 docstring 同源说明）；收编类
  （Report/Bulk/ShareUnlock）不受门控，保持 sprint-4/5 全环境生效面。

### 2. SearchRateThrottle 只建类不挂载（T6-01）

V1.0 无独立全局搜索端点（sprint-1 搜索为列表 `?q=` 过滤）；feature freeze
不新增端点。类与配额已就位，Sprint-7+ 搜索端点落地时 `[*BASE_THROTTLES,
SearchRateThrottle]` 挂载。

### 3. DRF 配额语法扩展 "N/<mult><unit>"（T6-01）

规格配额 "5/10m" 在 DRF 原生 `parse_rate` 下抛 `KeyError`（period 首字符
必须是单位字母）。`RedisRateThrottle.parse_rate` 覆写支持
`[数字前缀]<单位>[后缀]`（"10m"=600s、"min"=60s 同表），语义与 §7.2 冻结表
一致。

### 4. 分享解锁窗口从 TTL 锚定改固定窗口量化（T6-01）

FILE-004 原实现 EXPIRE 锚定首次尝试；框架统一为 `now // 600` 量化窗口——
规格 §2.1 总表自口径「固定窗口 600s」。边界语义差 ≤1 窗口（同窗内行为
一致），UT-13 与 test_file_shares（UT-05/IT-08）双覆盖。

### 5. 备份任务走默认 celery 队列（T6-03）

规格时序图示 "backup 队列"。未另建队列：避免 rabbitmq 同名队列参数不可变
（坑 15）带来的拓扑变更；beat 投递与队列归属无关，时序零影响。

### 6. SSE-AES256 降级（T6-03）

MinIO 未配 KMS 时 SSE-S3 请求被拒（`NotImplemented: KMS is not configured`）
——`upload_fileobj` 降级明文上传 + warn，仅 KMS/加密类错误降级（凭据/网络
错误直抛）。known-debt：生产启用 KMS 后回收该降级并回归加密路径。

### 7. read_only 根文件系统先行 8 服务（T6-02）

api/worker/live 三个应用服务暂不置只读根：需在真实 prod 栈/演练中确认
运行时写路径（gunicorn 临时文件、LibreOffice $HOME 等），防止「未验证资产」。
known-debt 登记，恢复演练收口轮（compose 模式）实测后补齐。

### 8. compose v5 网络重置语义（T6-02）

`!reset` 在 Docker Compose v5.4 不吃同标签替换值（实测网络归 default）；
网络成员整体替换必须用 `!override`（`!reset []` 剥端口正常）。规格中
`!reset` 写法按此修正。

### 9. 顺手修复的既有缺陷（非本迭代规格项，冒烟/矩阵暴露）

- `FileAsset.workspace` NOT NULL 与 AvatarService 头像域写 `None` 的漂移
  （AUTH-004 期，avatar presign 必 IntegrityError→400）——迁移 0020 收口
  nullable。
- sprint-5-flow docstring 承诺的 S5A 域清理从未实现（残留 S5A3C 副本 t1
  打爆 test_rpt002 全局 `get`）——补 FK 全序前后置清理；C7-2 INSERT 缺列
  （F 表 victim 残留真身）修复。
- coverage/pytest-cov 未入锁文件（冻结时靠环境残留过 TC-COVER-001/002，
  `uv sync` 即剪掉）——正式入 dev 依赖组。

## 后续动作

| # | 动作 | 归属 |
| --- | --- | --- |
| 1 | api-conventions §7.1 补注 L1 auth/public 预拦区（数值不变） | T6-12 文档收口 |
| 2 | INFRA-002 §4.4 P0 zone 两行标注已收编 | T6-12 |
| 3 | K8s 骨架 + compose 演练模式运行时验证（read_only 收口一并） | 收口轮 |
| 4 | 生产 MinIO KMS 启用后回收 SSE 降级 | INFRA-006（P4）/运维手册 |
