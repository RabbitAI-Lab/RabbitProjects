# Slack / Zoom 全量集成

| 元信息项 | 内容 |
| --- | --- |
| 文档编号 | INTG-003 |
| 所属迭代 | P4：远期增强（第 13 周起，签约驱动排期） |
| 优先级 | P4（企业版增强 / 生态与开放价值线） |
| 所属模块 | M9-INTG 集成开放 |
| 文档状态 | 待评审（Draft） |
| 最后更新日期 | 2026-09-01 |
| 上游依据 | `docs/需求文档.md` §3.5 集成节、§8.2 P4 列（集成行） |
| 前置依赖 | `INTG-001`（GitHub 集成框架：OAuth App 范式、事件管道）、`INTG-002`（Webhook 出站：签名与投递管道）、`COLLAB-001`（通知通道）、`TASK-006`（WorkLog，会议纪要回挂载体） |
| 下游依赖 | `INTG-004`（Open API 复用本文档的 OAuth 2.0 应用管理面） |
| 架构基线 | [`api-conventions.md`](../architecture/api-conventions.md) §7.2 / §8 / §13 |
| 竞品参考 | Jira（Slack for Jira 官方应用）、Linear（Slack 双向同步标杆）、Zoom Team Chat 应用范式 |

> **范围声明**：本文档交付两个 SaaS 生态集成——**Slack 全量**（频道联动通知 + 斜杠命令 + 消息转任务 + 线程同步）与 **Zoom**（会议关联任务 + 纪要/录制回挂）。Teams/钉钉/飞书复用同一框架但不在本文档（各自单独立项）。入站消息中的文件附件下载转存不在本期（标注 P4+）。

---

## 1. 概述

### 1.1 功能定位

客户团队的日常沟通在 Slack，会议在 Zoom——任务系统若不能「贴着沟通流工作」，就会退化为「另一个要打开的网页」。两个集成的价值锚点：

| 集成 | 价值 | 核心场景 |
| --- | --- | --- |
| Slack | 任务动态进频道、讨论不离开 Slack 即可建任务 | 频道订阅项目动态；`/rp create` 斜杠命令；消息右键「转为任务」；评论线程双向同步 |
| Zoom | 会议与任务互相关联，纪要自动归位 | 任务详情一键发起/关联 Zoom 会议；会后录制链接与 AI 纪要自动回挂任务评论 |

### 1.2 启动条件

| 条件 | 判定 |
| --- | --- |
| 商业条件 | ≥ 3 家使用 Slack 的客户签约（国内私有化客户通常走钉钉/飞书另案）；Zoom 集成依赖 Zoom Marketplace 上架审批通过 |
| 技术前置 | `INTG-001/002` 集成框架生产稳定（OAuth 应用管理、事件管道、签名投递）；实例具备公网 HTTPS 回调地址（SaaS 天然满足，私有化需客户开放） |
| 选型前置 | Slack App 上架 Slack Marketplace 的材料（隐私政策/安全问卷）经法务评审；Zoom 侧同理 |

### 1.3 独立交付判定

1. Slack 沙盒 workspace 完成：安装 → 频道订阅 → 任务事件推送 → 斜杠建任务 → 消息转任务 → 线程双向同步，全链路可演示。
2. Zoom 沙盒完成：任务关联会议 → 会后 webhook 接收录制完成事件 → 录制链接 + 纪要回挂评论。
3. 断链降级：Slack/Zoom API 故障时主系统功能零影响（集成队列积压告警，不阻塞业务写路径）。
4. 零回归：未安装集成的工作空间行为与企业版 V1.0 一致。

### 1.4 竞品参考结论（详见第 6 章）

- **Linear ↔ Slack**：业界标杆——双向同步（Slack 线程 ↔ Linear 评论）、消息转任务带上下文卡片、通知粒度可配到事件类型。
- **Jira Slack 应用**：功能全但配置复杂（JQL 订阅），通知噪音被诟病。
- **Zoom 范式**：Meeting SDK 创建会议 + `recording.completed` webhook + AI Companion 纪要 API。
- **本系统取舍**：对齐 Linear 的双向体验；订阅配置**只给事件类型勾选**不做查询语言（汲取 Jira 噪音教训，BR-04）；Zoom 侧只做「关联 + 回挂」不做内嵌会议 UI。

---

## 2. 业务逻辑

### 2.1 集成架构

```mermaid
flowchart LR
    subgraph RP["RabbitProjects"]
        EVT["任务事件流<br/>（TASK-010 管道）"] --> FAN["SlackFanout<br/>订阅匹配"]
        FAN -->|on_commit| DELIV["slack_deliver<br/>chat.postMessage"]
        CMD["斜杠命令/交互<br/>SlackActions View"] --> SVC["IssueService<br/>（既有服务层）"]
        ZOOMWH["Zoom Webhook<br/>recording.completed"] --> ZSVC["ZoomRecapService<br/>回挂评论"]
    end
    SLACK["Slack API"] <-->|"HTTPS 双向"| RP
    ZOOM["Zoom API / Webhook"] --> RP
    RP -->|"meetings.create"| ZOOM
```

| 维度 | Slack | Zoom |
| --- | --- | --- |
| 授权 | OAuth 2.0（Bot Token，workspace 粒度） | OAuth 2.0（Account-level App，实例粒度） |
| 入站 | 斜杠命令 + Interactive Components + Events API | Webhook（录制/会议状态） |
| 出站 | `chat.postMessage` / `chat.update` | `POST /users/{id}/meetings` |
| 凭证 | `xoxb-` token 密保库存储 | client credentials 密保库 + 账号级 token 刷新 |

### 2.2 业务规则（BR）

| 编号 | 规则 | 说明 |
| --- | --- | --- |
| BR-01 | 安装即绑定 | Slack App 安装到客户 Slack workspace 时与本系统 Workspace 一对一绑定（`team_id ↔ workspace`）；解绑双向可发起，数据保留 30 天 |
| BR-02 | 身份映射 | Slack 用户 ↔ 系统用户按邮箱自动映射；映射失败的动作者显示 Slack 昵称并以「集成主体」落 Activity（不伪造用户） |
| BR-03 | 频道订阅 | 频道可订阅项目/任务类型的指定事件类型；每个频道 ≤ 10 条订阅；私频道需 Bot 被邀请方可订阅 |
| BR-04 | 反噪音 | 订阅仅事件类型勾选（创建/状态变更/指派/评论/截止提醒），无自定义查询；同任务 5 min 内多事件合并为一条消息（线程追加） |
| BR-05 | 斜杠命令确认 | `/rp create` 弹模态框（标题/项目/负责人/截止），创建成功回 ephemeral 消息带任务链接 |
| BR-06 | 消息转任务 | Message Action「转为任务」：消息正文进描述（Slack mrkdwn → 系统富文本 JSON 转换），原消息链接自动回挂 |
| BR-07 | 线程同步 | 订阅频道中任务卡片下的 Slack 线程回复 → 任务评论；任务评论 → 卡片线程回复（双向）；**同步开关按频道可关**；机器人消息不回环（BR-08） |
| BR-08 | 防回环 | 系统产生的 Slack 消息带 `metadata.event_payload.origin=rp`；入站事件含此标记直接丢弃；评论同步带 `[Slack]` 前缀作者标记 |
| BR-09 | Zoom 关联 | 任务可关联 0..N 个会议（`meeting_id + join_url`）；创建会议走任务上下文模板（标题=任务名，议程=任务链接） |
| BR-10 | 纪要回挂 | `recording.completed` webhook 到达 → 录制链接 + AI 纪要摘要（Zoom AI Companion API，可选）回挂为任务评论（作者=「Zoom 集成」），参会人含系统映射用户时 @ 提及 |
| BR-11 | 失败降级 | 出站投递失败指数退避 5 次后计入集成健康面板；入站处理失败返回 200（防 Slack 重试风暴）+ 死信队列人工重放 |
| BR-12 | 权限与审计 | 安装/解绑/订阅管理 `integration.manage`（WS_ADMIN+）；斜杠建任务以映射用户身份走既有 `issue.create` 权限校验；全链路审计 |

### 2.3 Slack 通知卡片与合并

```mermaid
sequenceDiagram
    participant U as 用户(改状态)
    participant RP as 系统事件流
    participant F as SlackFanout
    participant S as Slack API
    participant C as 订阅频道

    U->>RP: 状态 待办→进行中
    RP->>F: issue.state_changed (on_commit)
    F->>F: 匹配订阅(项目×事件类型)
    F->>S: chat.postMessage(卡片)
    S->>C: 任务卡片消息
    Note over U,C: 5 min 内同任务再事件
    U->>RP: 指派给张三
    RP->>F: issue.assigned
    F->>F: 查近 5min 同任务消息 ts
    F->>S: chat.update(原卡片追加) + 线程回复
    S->>C: 原卡片更新（BR-04 合并）
```

| 卡片元素 | 内容 |
| --- | --- |
| Header | 任务编号 + 标题（链接）+ 项目徽标 |
| 变更区 | 事件描述（「陈项目 将状态改为 进行中」） |
| 上下文 | 状态/负责人/优先级/截止 四联字段 |
| 动作 | `查看任务` `指派给我` `完成`（Interactive Button，走入站动作端点） |

### 2.4 线程双向同步状态机

| 方向 | 触发 | 规则 |
| --- | --- | --- |
| Slack → 任务 | 卡片线程新回复 | 忽略 bot 消息与 `origin=rp` 标记（BR-08）；作者按邮箱映射，失败以「Slack 用户 {昵称}」署名；内容转富文本（@提及 → 系统 mention，#频道 → 文本） |
| 任务 → Slack | 任务新评论 | 仅同步到「该任务卡片所在且开启同步」的频道（BR-07）；长评论截断 2,800 字符附「查看全文」链接 |
| 编辑/删除 | 任一侧编辑/删除 | 同步编辑标记「(已编辑)」；删除不同步物理删除，同步「(已撤回)」占位（审计完整性优先） |

### 2.5 Zoom 回挂流程

```mermaid
sequenceDiagram
    participant U as 用户
    participant RP as 系统
    participant Z as Zoom API
    participant W as Zoom Webhook

    U->>RP: 任务详情「发起 Zoom 会议」
    RP->>Z: POST /users/{id}/meetings（模板 BR-09）
    Z-->>RP: meeting_id + join_url
    RP->>RP: IssueMeeting 关联落库
    Note over Z,W: 会议进行…结束，录制上传
    W->>RP: recording.completed (webhook)
    RP->>Z: GET 录制与 AI 纪要（可选）
    RP->>RP: 回挂评论（BR-10）+ @参会映射用户
```

| 边界 | 处理 |
| --- | --- |
| 非关联会议 | webhook 的 `meeting_id` 无 `IssueMeeting` 匹配 → 忽略（不报错） |
| 多任务关联同会议 | 全部关联任务都回挂（一次会议评审多个任务） |
| 录制删除 | Zoom 侧删除不回撤系统评论（历史事实），链接失效由 Zoom 页提示 |
| 无 AI 纪要订阅 | 仅回挂录制链接与时长 |

---

## 3. UI/UX 设计

### 3.1 页面清单

| 页面 | 位置 | 核心任务 |
| --- | --- | --- |
| 集成中心-Slack | 工作空间设置 → 集成 → Slack | 安装/解绑、身份映射状态、健康度（近 24h 投递成功率） |
| 频道订阅管理 | 同上（子页） | 订阅列表（频道×项目×事件类型）、新增/编辑/停用 |
| 集成中心-Zoom | 工作空间设置 → 集成 → Zoom | 账号连接状态、回挂开关、默认会议模板 |
| 任务详情-会议区 | 任务详情侧栏 | 关联会议列表、发起会议、加入按钮 |

### 3.2 Slack 订阅管理线框

```
┌──────────────────────────────────────────────────────────────────┐
│ 设置 / 集成 / Slack · 频道订阅                        [+ 新增订阅] │
├──────────────────────────────────────────────────────────────────┤
│ Slack 团队: acme-corp.slack.com  ●已连接   Bot: @RabbitProjects    │
│ 身份映射: 182/195 已映射 · 13 未映射 [查看]   24h 投递成功率 99.2% │
│ ┌────────────────────┬────────────┬─────────────────┬─────────┐  │
│ │ 频道               │ 项目       │ 事件类型        │ 线程同步│  │
│ ├────────────────────┼────────────┼─────────────────┼─────────┤  │
│ │ #proj-ecommerce    │ 电商平台   │ 全部 5 类       │ 开 ●    │  │
│ │ #team-qa           │ 电商平台   │ 状态变更/截止   │ 关 ○    │  │
│ │ #general           │ — (全项目) │ 仅截止提醒      │ 关 ○    │  │
│ └────────────────────┴────────────┴─────────────────┴─────────┘  │
│ ⚠ 反噪音保护: 同任务 5 分钟内事件自动合并（BR-04）                 │
└──────────────────────────────────────────────────────────────────┘
```

### 3.3 Slack 侧卡片与模态框（线框）

```
#proj-ecommerce 频道
┌─ RabbitProjects ─────────────────────────────────────────┐
│ 🟦 ECOM-231  下单链路重构                    [电商平台]   │
│ 陈项目 将状态改为「进行中」 · 14:32                       │
│ ──────────────────────────────────────────────           │
│ 状态: 进行中    负责人: 张三点                            │
│ 优先级: 高      截止: 9/16 (🔴 基线+6d)                   │
│ [查看任务]  [指派给我]  [✓完成]                           │
│ 💬 线程 (2) — 任务评论同步开启                            │
│   ├ 李四维(系统): 接口联调排到周四，依赖库存服务           │
│   └ slack_ken: 周四可以，我先出 mock        [Slack]       │
└──────────────────────────────────────────────────────────┘

/rp create 模态框
┌─ 创建任务 ────────────────────────────────┐
│ 项目:   [电商平台 ▾]                       │
│ 标题:   [___________________________]      │
│ 负责人: [选择成员 ▾]   截止: [日期选择]    │
│ 描述:   [_____________________]            │
│         (自动附带当前频道上下文链接)        │
│                    [取消]  [创建]          │
└────────────────────────────────────────────┘
```

### 3.4 交互规则

| 场景 | 交互 |
| --- | --- |
| 安装向导 | 三步：Slack 授权 → 选择绑定 Workspace → 默认订阅推荐（#general 截止提醒）→ 完成页给 `/rp` 命令速查 |
| 未映射用户处理 | 映射面板列出未映射 Slack 用户，支持手工指定或发送邀请；斜杠命令未映射用户收到 ephemeral「请先绑定账号」+ 绑定链接 |
| 会议发起 | 任务详情「发起 Zoom 会议」→ 二次确认（模板预览）→ 创建成功 join_url 复制 + 自动发评论通知负责人 |
| 健康告警 | 投递成功率 < 95%（24h）时集成页顶部黄色横幅 + 通知 WS_ADMIN |
| 权限 | 集成菜单 WS_ADMIN 可见；任务详情会议区项目成员可见，发起会议需 `issue.update` |

---

## 4. 技术架构

### 4.1 数据模型

```python
# apps/api/rp_integrations/models_slack.py
class SlackInstallation(BaseModel):
    workspace = models.OneToOneField("rp_workspaces.Workspace",
                                     on_delete=models.CASCADE,
                                     related_name="slack_installation")
    team_id = models.CharField(max_length=32, unique=True)     # Slack T…
    team_name = models.CharField(max_length=128)
    bot_token_ref = models.CharField(max_length=128)           # 密保库句柄
    bot_user_id = models.CharField(max_length=32)
    installed_by = models.ForeignKey("rp_users.User",
                                     on_delete=models.PROTECT)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "intg_slack_installation"


class SlackChannelSubscription(BaseModel):
    installation = models.ForeignKey(SlackInstallation,
                                     on_delete=models.CASCADE,
                                     related_name="subscriptions")
    channel_id = models.CharField(max_length=32)               # C…
    channel_name = models.CharField(max_length=128)
    project = models.ForeignKey("rp_projects.Project", null=True,
                                on_delete=models.CASCADE)      # null=全项目
    event_types = models.JSONField(default=list)               # ["state","assign",...]
    thread_sync = models.BooleanField(default=False)           # BR-07
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "intg_slack_subscription"
        constraints = [
            models.UniqueConstraint(
                fields=["installation", "channel_id", "project"],
                name="uq_slack_sub_channel_project"),
        ]


class SlackUserMap(BaseModel):
    installation = models.ForeignKey(SlackInstallation,
                                     on_delete=models.CASCADE)
    slack_user_id = models.CharField(max_length=32)
    user = models.ForeignKey("rp_users.User", null=True,
                             on_delete=models.SET_NULL)        # null=未映射
    slack_email = models.EmailField()
    slack_display_name = models.CharField(max_length=128)

    class Meta:
        db_table = "intg_slack_user_map"
        constraints = [
            models.UniqueConstraint(fields=["installation", "slack_user_id"],
                                    name="uq_slack_user_map"),
        ]


class IssueMeeting(BaseModel):                                  # Zoom 关联
    issue = models.ForeignKey("rp_issues.Issue", on_delete=models.CASCADE,
                              related_name="meetings")
    meeting_id = models.CharField(max_length=32, db_index=True)
    join_url = models.URLField(max_length=512)
    topic = models.CharField(max_length=255)
    start_time = models.DateTimeField(null=True)
    created_by = models.ForeignKey("rp_users.User",
                                   on_delete=models.PROTECT)

    class Meta:
        db_table = "intg_issue_meeting"
        indexes = [models.Index(fields=["meeting_id"],
                                name="idx_issue_meeting_mid")]
```

| 补充 | 说明 |
| --- | --- |
| 消息锚点 | `SlackMessageAnchor(issue, channel_id, message_ts)` 表记录任务卡片位置，支撑 `chat.update` 合并（BR-04）与线程同步定位 |
| Zoom 配置 | `ZoomConnector`（实例级单例表）：account_id、client 凭证句柄、AI 纪要开关、webhook secret 句柄 |

### 4.2 出站投递与合并

```python
# apps/api/rp_integrations/tasks_slack.py
from celery import shared_task


@shared_task(queue="integration", bind=True,
             autoretry_for=(SlackApiError,), retry_backoff=True,
             retry_kwargs={"max_retries": 5})
def slack_deliver(self, subscription_id: str, event: dict) -> None:
    sub = SlackChannelSubscription.objects.select_related(
        "installation").get(id=subscription_id, is_active=True)
    client = SlackClient(sub.installation)          # 密保库取 token
    anchor = SlackMessageAnchor.objects.filter(
        issue_id=event["issue_id"], channel_id=sub.channel_id,
        created_at__gte=timezone.now() - timedelta(minutes=5)).first()
    blocks = build_issue_card(event, compact=anchor is not None)
    if anchor:                                       # BR-04 合并
        client.chat_update(channel=sub.channel_id, ts=anchor.message_ts,
                           blocks=blocks)
        client.chat_post_reply(channel=sub.channel_id, ts=anchor.message_ts,
                               text=event_summary_line(event))
    else:
        resp = client.chat_post_message(channel=sub.channel_id, blocks=blocks)
        SlackMessageAnchor.objects.create(
            issue_id=event["issue_id"], channel_id=sub.channel_id,
            message_ts=resp["ts"])
```

| 要点 | 说明 |
| --- | --- |
| 事件源 | `TASK-010` 事件管道新增 `slack` 订阅者（`on_commit` 扇出），与 webhook 订阅者并列 |
| 限流 | Slack `chat.postMessage` 每频道 1 msg/s 限制——客户端内置令牌桶，429 时读 `Retry-After` 延迟重试 |
| 健康度 | 每次投递结果落 Redis 计数器（`intg:slack:{ws}:ok/fail`，24h 滑窗），健康面板直读 |

### 4.3 入站端点（Slack Actions / Zoom Webhook）

| 端点 | 验签 | 处理 |
| --- | --- | --- |
| `POST /integrations/slack/commands/` | `X-Slack-Signature`（HMAC-SHA256，签名密钥密保库）+ 5 min 时间戳窗 | `/rp create` → 3s 内回 200 + 模态框 `trigger_id`（Slack 3 秒规则），建任务走异步视图响应 |
| `POST /integrations/slack/actions/` | 同上 | 按钮动作（完成/指派）→ 映射用户鉴权 → 既有服务层 → `response_url` 回执 |
| `POST /integrations/slack/events/` | 同上 | 线程回复 → URL verification 握手 + 事件去重（`event_id` Redis SETNX 24h）→ 评论落库 |
| `POST /integrations/zoom/webhook/` | Zoom `x-zm-signature` HMAC + CRC 校验（`endpoint.url_validation`） | `recording.completed` → `zoom_recap.delay`；其余事件类型忽略 |

| 纪律 | 说明 |
| --- | --- |
| 快速确认 | 全部入站端点 200 先行：验签通过即异步入队（BR-11），处理失败进 `integration.dlq` 死信可重放 |
| 权限穿透 | 入站动作以映射用户身份构造 `request.user`，走既有 Permission 层——集成无特权通道（BR-12） |
| 回环阻断 | 事件处理首查 `origin=rp` 标记与 bot_user_id（BR-08） |

### 4.4 API 端点（管理面）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/workspaces/{slug}/integrations/slack/` | 安装状态 + 映射统计 + 健康度 |
| POST | `/api/v1/workspaces/{slug}/integrations/slack/install/` | 返回 Slack OAuth 跳转 URL |
| GET | `/api/v1/workspaces/{slug}/integrations/slack/callback/` | OAuth 回调（state 校验） |
| DELETE | `/api/v1/workspaces/{slug}/integrations/slack/` | 解绑（30 天数据保留） |
| GET/POST | `…/slack/subscriptions/` | 订阅列表 / 新增 |
| PATCH/DELETE | `…/slack/subscriptions/{id}/` | 编辑事件类型/开关线程同步 / 停用 |
| GET | `…/slack/user-maps/` | 映射列表（未映射筛选） |
| POST | `/api/v1/projects/{pid}/issues/{id}/meetings/` | 发起 Zoom 会议并关联 |
| GET | `/api/v1/projects/{pid}/issues/{id}/meetings/` | 关联会议列表 |
| DELETE | `/api/v1/projects/{pid}/issues/{id}/meetings/{mid}/` | 解除关联（不取消 Zoom 会议） |

**成功示例** — `POST …/subscriptions/`：

```json
{
  "status": "success",
  "data": {
    "id": "01J6ZWX8K2NQ4PVTBRYH5MD3EA",
    "channel_id": "C08ABC123",
    "channel_name": "proj-ecommerce",
    "project": {"id": "01J6XU…", "name": "电商平台"},
    "event_types": ["created", "state_changed", "assigned", "commented", "due_reminder"],
    "thread_sync": true,
    "is_active": true
  },
  "meta": {"request_id": "01J6ZWY9L3OR5QWUC_SZJ6NE4FB"}
}
```

**错误示例** — 私频道未邀请 Bot（BR-03）：

```json
{
  "status": "error",
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "无法订阅该频道",
    "details": [{"field": "channel_id", "code": "INVALID",
                 "message": "私有频道需先邀请 @RabbitProjects 加入"}]
  },
  "meta": {"request_id": "01J6ZWZ0M4PS6RXVD0AK7PF5GC"}
}
```

**错误示例** — 未安装即建会议：

```json
{
  "status": "error",
  "error": {
    "code": "RESOURCE_STATE_INVALID",
    "message": "Zoom 集成未连接，请管理员先在集成中心完成连接",
    "details": []
  },
### 4.5 前端 Store

```typescript
// apps/web/src/modules/integrations/slack.store.ts
export class SlackIntegrationStore {
  installation: ISlackInstallation | null = null;
  subscriptions: ISlackSubscription[] = [];
  health: { successRate24h: number } | null = null;
  unmappedUsers: ISlackUserMap[] = [];

  constructor(private workspaceSlug: string) { makeAutoObservable(this); }

  get isInstalled() { return !!this.installation?.isActive; }
  get healthWarn() { return (this.health?.successRate24h ?? 100) < 95; }

  async install() {
    const res = await integrationService.slackInstallUrl(this.workspaceSlug);
    window.location.href = res.data.authorize_url;   // OAuth 跳转
  }

  async toggleThreadSync(subId: string, enabled: boolean) {
    await integrationService.patchSubscription(
      this.workspaceSlug, subId, { thread_sync: enabled });
    await this.fetchSubscriptions();
  }
}
```

| 前端规则 | 说明 |
| --- | --- |
| OAuth 回跳 | callback 页读取后端写入的安装结果 → 成功跳订阅推荐向导；失败显示 Slack 错误码与重试入口 |
| SWR 键 | `SLACK_INSTALL(ws)` / `SLACK_SUBS(ws)` / `ISSUE_MEETINGS(issueId)`；会议创建后 mutate 任务评论键（回挂评论） |

### 4.6 性能与安全

| 指标 | 预算 | 手段 |
| --- | --- | --- |
| 事件→频道延迟 | P95 < 5s | 管道直达 + 独立 `integration` 队列 |
| 入站确认 | < 500ms（Slack 3s 规则余量 6 倍） | 验签 + 入队即 200 |
| 投递吞吐 | 100  msg/min/实例 | Slack 客户端令牌桶 + 每频道 1/s 限速 |
| 凭证安全 | 零明文落库 | 全部密保库句柄；日志黑名单 `xoxb-`/`x-zm-` 前缀脱敏 |

---

## 5. 测试用例

### 5.1 单元测试（UT）

| 编号 | 用例 | 断言 |
| --- | --- | --- |
| UT-01 | 邮箱映射 | 同邮箱自动绑定；无匹配进未映射列表 |
| UT-02 | 未映射斜杠 | `/rp create` 未映射用户收到绑定引导 ephemeral |
| UT-03 | 事件合并 | 5 min 内两事件 → `chat.update` 被调而非第二次 post；锚点正确 |
| UT-04 | 防回环 | `origin=rp` 事件与 bot 消息入站直接丢弃 |
| UT-05 | 线程→评论 | Slack 回复落评论且作者署名 `[Slack] 昵称`（未映射）或映射用户 |
| UT-06 | 评论→线程 | 任务评论出现在卡片线程；长评论截断 2,800 字符带链接 |
| UT-07 | mrkdwn 转换 | Slack `*粗体* <url|text> @U123` 正确转系统富文本 JSON |
| UT-08 | 验签 | 错误签名/超 5 min 时间戳 → 401；正确签名通过 |
| UT-09 | 订阅上限 | 频道第 11 条订阅返回 `RESOURCE_LIMIT_EXCEEDED` |
| UT-10 | 会议关联 | 创建会议落 `IssueMeeting` 且模板含任务链接 |
| UT-11 | 无关联 webhook | 未匹配 `meeting_id` 的 recording 事件忽略不报错 |
| UT-12 | 多任务回挂 | 同会议关联 3 任务，3 条回挂评论均生成且 @ 正确 |

### 5.2 集成测试（IT）

| 编号 | 用例 | 断言 |
| --- | --- | --- |
| IT-01 | Slack 沙盒全链路 | 安装→订阅→事件推送→斜杠建任务→消息转任务→线程双向，各步沙盒侧可见 |
| IT-02 | 合并窗口 | 脚本 3 min 内触发 5 事件，频道仅 1 卡片 + 4 线程回复 |
| IT-03 | 失败降级 | Slack API 500 注入：业务写路径 P95 无变化；5 次重试后死信；恢复后重放成功 |
| IT-04 | Zoom 沙盒回挂 | 模拟 `recording.completed` → 评论含录制链接与时长；AI 纪要开关开时含摘要 |
| IT-05 | 令牌桶 | 突发 50 消息/频道投递速率 ≤ 1/s 且无 429 失败 |
| IT-06 | 解绑 | 解绑后事件不再投递；30 天内重装恢复订阅配置 |

### 5.3 E2E 测试

| 编号 | 场景 | 验收 |
| --- | --- | --- |
| E2E-01 | 频道联动 | 真实 Slack 沙盒演示 BR-03~08 全部场景 |
| E2E-02 | 会议闭环 | 任务发起会议 → 真实开会 1 min 录制 → 录制完成评论自动出现 |
| E2E-03 | 权限 | MEMBER 尝试管理订阅被拒；斜杠建任务在无权限项目被拒并回执 |

---

## 6. 竞品深度对标

| 维度 | Linear Slack | Jira Slack 应用 | Asana/Zoom | 本系统 |
| --- | --- | --- | --- | --- |
| 通知配置 | 事件类型勾选 | JQL 订阅（强但噪音大） | 固定模板 | 事件类型勾选 + 合并窗口（BR-04） |
| 双向同步 | ✅ 线程↔评论（标杆） | 单向为主 | ❌ | ✅ 双向 + 防回环 + 编辑标记 |
| 消息转任务 | ✅ 带上下文卡片 | ✅ | ❌ | ✅ + 原消息回链 |
| 身份映射 | 邮箱自动 | Atlassian ID 强制 | 邮箱 | 邮箱自动 + 未映射降级署名 |
| Zoom 回挂 | 无（Linear 无 Zoom） | 无 | 录制链接回挂 | 录制 + AI 纪要 + 参会人 @ |
| 失败处理 | 未公开 | 未公开 | 未公开 | 死信队列 + 健康面板 + 重放 |

**结论**：Linear 证明了「双向同步 + 合并窗口」是 Slack 集成的体验分水岭，Jira 证明了「查询语言订阅」必然噪音失控——本系统全盘采纳前者、明确拒绝后者。Zoom 侧做「关联 + 回挂」而非内嵌会议 UI，因为客户已有 Zoom 客户端习惯，系统只需成为会议产物的归集处。

---

## 7. 里程碑与验收

### 7.1 工作量估算

| 交付面 | 内容 | 估算 |
| --- | --- | --- |
| Model / Migration | 5 表 + 锚点表 + Zoom 单例 | 1.5 d |
| 后端 | OAuth 双流程、投递与合并、入站四端点、线程同步、Zoom 回挂、管理面 10 端点 | 7 d |
| 前端 | 集成中心 3 页、任务会议区、安装向导 | 3.5 d |
| 上架材料 | Slack/Zoom Marketplace 材料与安全问卷 | 2 d |
| 测试 | UT-01~12、IT-01~06、E2E-01~03 | 3 d |
| **合计** | | **17 d（2-3 人并行约 2 周）** |

### 7.2 可操作演示的验收标准

1. Slack 沙盒全链路（E2E-01）一次演示通过：安装 → 订阅 → 五类事件推送（合并生效）→ 斜杠建任务 → 消息转任务 → 线程双向（含未映射署名）。
2. 回环测试：系统评论同步到 Slack 后，该 Slack 消息不再回流成重复评论（无限循环防护验证）。
3. Zoom 闭环（E2E-02）：真实会议录制完成事件驱动回挂评论，含链接/时长/纪要。
4. 降级：Slack API 故障演练中业务写路径 P95 漂移 < 5%，死信可重放。
5. 安全：日志/审计全文无 `xoxb-`/Zoom 凭证明文；验签负例全拒。
6. 零回归：未安装工作空间契约快照与企业版 V1.0 一致。
