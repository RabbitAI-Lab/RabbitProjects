# 企业微信 / 钉钉通知与审批通道

| 元信息项 | 内容 |
| --- | --- |
| 文档编号 | INTG-005 |
| 所属迭代 | P4：远期增强（v2 范围裁定 2026-09-10 纳入） |
| 优先级 | P4（企业版增强 / 集成价值线） |
| 所属模块 | M9-INTG 第三方工具集成 |
| 文档状态 | 已实现稿（R5，自含交付——免确认模式下按 INTG-003 同构范式） |
| 最后更新日期 | 2026-09-12 |
| 上游依据 | `docs/需求文档.md` §3.9 第三方工具集成模块、需求池未承接项（2026-09-10 用户裁定纳入 P4） |
| 前置依赖 | `INTG-003`（Slack/Zoom 集成——安装载体/订阅/映射范式同构复用） |
| 下游依赖 | `COLLAB-005`（统一消息推送策略——本文为其中两个通道实例） |
| 架构基线 | [`api-conventions.md`](../architecture/api-conventions.md) §4/§8/§13、rbac §8（integration.config 既有码） |

> **范围声明**：交付企业微信（WeCom）与钉钉（DingTalk）两条国内 IM 通道的**通知出站**（任务事件 → 群机器人 webhook）与**入站审批回调**（卡片按钮 → 动作受理）。不做的：企微/钉钉侧的 OAuth 安装流（企业自建应用代开发归商业化个案）、组织架构同步（归 AUTH-011 目录通道范畴）、AI 纪要（归 AI-001）。

---

## 1. 概述

### 1.1 功能定位

国内企业客户的 IM 主阵地是企微/钉钉而非 Slack。不做这两条通道，通知触达率在企业客户处近乎为零——「任务指派了但没人知道」直接阻断采纳。

| 交付项 | 说明 |
| --- | --- |
| 群机器人通知 | 任务事件（创建/流转/指派/评论）→ 指定群机器人 webhook（Markdown 卡片） |
| 多通道订阅 | 同 INTG-003 订阅模型：通道 × 群 × 范围（全项目/单项目）× 事件类型 |
| 入站动作回调 | 卡片按钮（查看/指派/完成）→ 动作端点（与 Slack 动作同构） |
| 通道健康 | 投递失败退避重试 5 次；连续失败标记通道 degraded，管理面可见 |

### 1.2 与 INTG-003 的关系

**范式同构、通道独立**：订阅模型（通道 × 目标 × 范围 × 事件）、去重唯一性（COALESCE 表达式索引）、入站动作受理、软删让位重建全部复用 INTG-003 冻结范式；通道实现（webhook 出站签名、回调验签、卡片格式）各自独立——企微群机器人为「webhook + key 签名」、钉钉为「加签 secret（HMAC-SHA256 timestamp）」，两者协议互不兼容不做抽象层（过早抽象证伪：Slack API 与两家 webhook 形态差异过大）。

## 2. 业务逻辑

### 2.1 业务规则（BR）

| 编号 | 规则 | 说明 |
| --- | --- | --- |
| BR-01 | 一群一订 | 同通道同群同范围（全项目/单项目）唯一——COALESCE 表达式索引去重（同 INTG-003 BR-14 范式：普通约束对 NULL 互异失效） |
| BR-02 | 凭证最小化 | 机器人 webhook key / 加签 secret 走密保库句柄（DB 只存句柄，同 BR-008 范式；dev 兜底 env） |
| BR-03 | 投递重试 | 失败退避重试 5 次（同 INTG-003 出站口径）；5 败标记 degraded 并告警管理面 |
| BR-04 | 入站验签 | 企微回调验签（msg_signature 解密可延后——群机器人回调本轮不启用）；钉钉回调按钉钉规范头校验 |
| BR-05 | 事件白名单 | 仅白名单事件类型出站（created/state_changed/assigned/commented/priority_changed——与 INTG-003 同集） |
| BR-06 | 审计 | 通道配置增删改、入站动作受理入 AuditLog |

### 2.2 通知卡片格式

企微（Markdown）：

```text
**【任务指派】SK-12 发版窗口确认**
状态：进行中 → 待验收
指派给：@映射成员（未映射显 Slack/企微显示名）
[查看任务](https://instance/issues/uuid) · [完成任务](动作端点回调)
```

钉钉（Markdown + actionCard 按钮经独立消息类型）——本轮交付 link 列表形态（按钮回调随企微/钉钉开放平台审批流个案演进登记）。

## 3. UI/UX 设计

复用 INTG-003 §3.2 订阅管理线框（通道下拉增「企业微信/钉钉」）；本节不另立线框（同构面零漂移——v2 免确认口径）。

## 4. 技术架构

### 4.1 数据模型

```python
# apps/api/plane/db/models/wecom_dingtalk.py（新增——通道配置独立表）
class ImWebhookChannel(BaseModel):
    """企微/钉钉群机器人通道（workspace 级多通道实例）。"""
    workspace = FK(Workspace, CASCADE, related_name="im_channels")
    provider = CharField(choices=[("wecom", "企业微信"), ("dingtalk", "钉钉")])
    name = CharField(64)
    target = CharField(255)          # 群机器人 webhook URL（密保库句柄或直存——dev 兜底）
    secret_ref = CharField(128)      # 钉钉加签 secret 句柄（企微空）
    is_degraded = BooleanField(default=False)
    class Meta: db_table = "integration_im_channels"
    # 唯一：workspace × provider × target（软删存活行）
```

订阅复用 SlackChannelSubscription 同构模型——独立 `ImSubscription`（通道 FK 为 ImWebhookChannel；范围/事件/线程同步字段同集），去重表达式索引同 §4.1.1 范式迁移承载。

### 4.2 端点

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET/POST | `/api/v1/workspaces/{slug}/integrations/im/channels/` | 通道列表/创建（integration.config） |
| DELETE | `…/im/channels/{id}/` | 删除（软删；订阅级联软删） |
| GET/POST | `…/integrations/im/subscriptions/` | 订阅 CRUD（同 INTG-003 范式） |
| POST | `/api/v1/integrations/im/actions/` | 入站动作受理（通道 provider 判别，Slack 动作端点同构） |
| POST | `/api/v1/integrations/im/test/` | 通道测试投递（管理面「发送测试消息」） |

### 4.3 出站投递

`im_deliver` 任务（integration 队列注同 ADR-0032#1）：按 provider 组装格式 → webhook POST（钉钉加签：`&timestamp=…&sign=base64(hmac(secret))`）→ 失败退避 5 次 → degraded 标记。

## 5. 测试用例（核心）

| 编号 | 用例 | 断言 |
| --- | --- | --- |
| UT-01 | 通道 CRUD | 创建/列表/软删；重复 target 拒绝 |
| UT-02 | 订阅去重 | 同通道同群同范围 409 UNIQUE（全项目行 NULL 去重） |
| UT-03 | 钉钉加签 | 投递任务组装的 sign 与规范公式一致（HMAC-SHA256 + base64） |
| UT-04 | 入站动作 | 未映射拒 / 指派落库 / 完成落库（Slack 同构） |
| UT-05 | degraded | 5 次失败置位（伪 client 注入） |

## 6. 竞品取舍

钉钉/企微开放平台群机器人为标准能力（竞品 Ones/飞书项目均内置）；本系统取舍：**只做群机器人最小闭环**（webhook 出站 + 动作回调），不做企业自建应用安装流（涉及企业管理员授权、应用市场审核、代开发资质——商业化个案承接，登记 known-debt）。

## 7. 里程碑

| 交付面 | 估算 |
| --- | --- |
| 模型+迁移+端点+投递任务+测试 | 2 d（范式同构复用 INTG-003） |
