# LDAP / SCIM 账号同步

| 元信息项 | 内容 |
| --- | --- |
| 文档编号 | AUTH-011 |
| 所属迭代 | P4：远期增强（第 13 周起，签约驱动排期） |
| 优先级 | P4（企业版增强 / 安全与合规价值线） |
| 所属模块 | M1-AUTH 账号与权限 |
| 文档状态 | 待评审（Draft） |
| 最后更新日期 | 2026-09-01 |
| 上游依据 | `docs/需求文档.md` §3.1 企业版专属节、§8.2 P4 列（账号与权限行） |
| 前置依赖 | `AUTH-009`（SSO：SAML/OIDC 与 JIT 开通，身份面同窗）、`AUTH-007`（部门树，目录同步落点）、`AUTH-008`（自定义角色，映射目标）、`AUTH-010`（审计管道，同步留痕） |
| 下游依赖 | `AUTH-012`（多租户身份隔离复用同步管道）、P4 合规报表 |
| 架构基线 | [`api-conventions.md`](../architecture/api-conventions.md) §2.5 / §7.2 / §8、[`rbac-permission-model.md`](../architecture/rbac-permission-model.md) §2 / §9 |
| 竞品参考 | Ones（LDAP/AD On-Premises 同步 + SCIM）、Plane（无 LDAP，SSO 仅 OIDC 且企业版）、GitLab（LDAP group sync）、Okta SCIM 范式 |

> **范围声明**：本文档交付两条企业身份源通道——**LDAP/AD 拉取式目录同步**（我方周期性拉取）与 **SCIM 2.0 推送式开通**（IdP 主动推送）。两者共用同一张映射表与同一套冲突裁决规则。SAML/OIDC 登录协议面归 `AUTH-009`，本文档只消费其 `SSOAccount` 身份绑定结果，不重复实现。

---

## 1. 概述

### 1.1 功能定位

企业客户的真实账号源在 **AD/LDAP 目录** 或 **云 IdP（Okta / Entra ID / 飞连 / 竹云）** 中。没有目录同步时，管理员需要手工在 RabbitProjects 逐个建号、逐个禁用——500 人规模的组织每季度入离职变动约 30-60 人次，手工维护必然漏禁（离职账号残留 = 安全事故），这是企业采购的硬性阻断项。

AUTH-011 交付一个可独立售卖的「身份源打通」能力包：

| 交付项 | 说明 |
| --- | --- |
| LDAP 目录同步 | 周期拉取 AD/LDAP 用户与组，自动开通 / 变更 / 禁用账号，按组映射部门与角色 |
| SCIM 2.0 服务端 | 接受 IdP 推送的 User/Group 开通、更新、禁用事件，幂等落库 |
| 冲突裁决 | 邮箱为主键的统一身份归并规则（本地账号 × SSO 账号 × 目录账号三方归并） |
| 同步留痕 | 每次同步产生 `DirectorySyncRun` 台账（新增/变更/禁用/跳过/失败五类计数 + 逐条明细） |
| 干跑（Dry Run） | 同步策略变更后先干跑输出影响清单，确认后才允许真实执行 |

### 1.2 启动条件（签约驱动）

| 条件 | 判定 |
| --- | --- |
| 商业条件 | 客户签约企业版且合同含「身份源集成」条款；许可 Seats ≥ 50（小客户手工维护成本可接受，不推销） |
| 技术前置 | 企业版 V1.0（Sprint 9）已发布；`AUTH-009` SSO 上线且至少一个客户生产可用 |
| 选型前置 | 收集客户目录类型（AD / OpenLDAP / 云 IdP）、是否允许出域连接（私有化部署无障碍；SaaS 需客户开放 LDAPS 或改用 SCIM） |
| 安全前置 | 安全评审通过：bind 凭证入密保库存储、LDAPS/StartTLS 强制、同步操作全量入审计 |

### 1.3 独立交付判定

本能力包**不依赖其他 P4 文档**，满足以下全部条件即判定独立交付完成：

1. AD 环境（客户提供测试域或本团队 `docs/testing/ad-fixture` 容器）完成全量 + 增量同步各一轮，台账计数与目录实况一致。
2. Okta 或 Entra ID 沙盒完成 SCIM 开通 → 变更 → 禁用全链路，幂等重放不产生重复账号。
3. 既有客户零回归：未启用目录同步的工作空间行为与企业版 V1.0 完全一致（API v1 契约不变）。
4. 安全评审表（§4.8）签字归档。

### 1.4 目标用户

| 用户 | 场景 | 关注点 |
| --- | --- | --- |
| 客户 IT 管理员 | 入职 50 人批量开通；离职当天禁用 | 目录里改一次，系统 15 分钟内生效；禁用不可遗漏 |
| 安全合规官 | 季度权限审计 | 任何账号的开通/禁用都有来源记录（谁同步、何时、依据哪条目录记录） |
| 实施工程师 | 私有化交付时对接客户 AD | 连接可测试、映射可配置、失败有明确报错，不需要改代码 |

### 1.5 前置依赖说明

| 依赖文档 | 依赖内容 | 缺失后果 |
| --- | --- | --- |
| `AUTH-009` | `IdentityProvider` / `SSOAccount` 模型、`external_id` 绑定范式、JIT 开通代码路径 | 目录同步与 SSO 各建一份身份绑定，同一用户两个账号 |
| `AUTH-007` | `Department` 树与成员归属 API | 同步来的部门归属无处落 |
| `AUTH-008` | `CustomRole` 权限码集合与角色挂接 API | 组→角色映射无目标 |
| `AUTH-010` | `AuditLog` 异步写入管道与幂等 `event_key` | 同步留痕需自建管道，重复造轮子 |

### 1.6 竞品参考结论（详见第 6 章）

- **Ones**：私有化版 LDAP/AD 同步是标准能力——周期全量拉取、组映射部门、离职自动禁用；SCIM 在其国际化版提供。同步粒度为「全量覆盖式」，无干跑。
- **GitLab**：LDAP group sync 以 `cn` 匹配组名，提供 `ldap:check` rake 干跑任务；其「blocked on LDAP disable」语义与本系统一致。
- **Okta SCIM 范式**：`POST /Users` 幂等靠 `externalId` + `userName` 唯一约束；`active=false` 即禁用而非删除；Group push 独立开关。
- **本系统取舍**：采纳 GitLab 干跑 + Okta 幂等语义；**不采纳** Ones 的「全量覆盖式禁用」（网络分区时会误禁全员），改为「连续两次同步缺席才禁用」的双确认机制（§2.4）。

---

## 2. 业务逻辑

### 2.1 两条通道的统一模型

```mermaid
flowchart LR
    subgraph PULL["拉取通道（LDAP）"]
        AD["AD / LDAP 目录"] -->|LDAPS 周期拉取| LSYNC["ldap_sync_worker<br/>Celery beat 每 15min"]
    end
    subgraph PUSH["推送通道（SCIM）"]
        IDP["Okta / Entra ID"] -->|SCIM 2.0 HTTPS 推送| SCIM["ScimUsersView<br/>ScimGroupsView"]
    end
    LSYNC --> CORE["DirectorySyncService<br/>统一归并与裁决"]
    SCIM --> CORE
    CORE --> USER["User / WorkspaceMember<br/>DepartmentMember / CustomRole"]
    CORE --> RUN["DirectorySyncRun 台账"]
    CORE --> AUDIT["AuditLog（AUTH-010 管道）"]
```

| 维度 | LDAP 通道 | SCIM 通道 |
| --- | --- | --- |
| 方向 | 我方拉取（outbound 连接客户目录） | IdP 推送（inbound 到我方端点） |
| 典型部署 | 私有化（目录在内网可达） | SaaS / 云 IdP 客户 |
| 时效 | 周期 15 分钟（可配 5-1440） | 准实时（秒级） |
| 幂等键 | 目录侧 `objectGUID` / `entryUUID` | `externalId`（IdP 侧主键） |
| 组语义 | LDAP group → 部门 + 角色 | SCIM Group → 部门 + 角色 |
| 互斥性 | 同一工作空间**只允许启用一条通道**（BR-01） | 同左 |

### 2.2 业务规则（BR）

| 编号 | 规则 | 说明 |
| --- | --- | --- |
| BR-01 | 单通道互斥 | 一个 Workspace 同时只能启用 `ldap` 或 `scim` 一条通道；切换前必须停用旧通道并完成一次「归属移交」干跑 |
| BR-02 | 邮箱主键 | 身份归并的唯一键是小写规范化邮箱（`lower(trim())`）；目录记录无邮箱者进「跳过」桶并给出原因 |
| BR-03 | 不开通无席位者 | 许可席位满时，新目录成员进入「待开通」队列而非直接开通，WS_ADMIN 收到通知；禁止静默失败 |
| BR-04 | 双确认禁用 | LDAP 通道下，某账号连续 **2 次**全量同步均缺席（且非过滤条件变化所致）才执行禁用；SCIM 通道以 IdP `active=false` 为准，单次即生效 |
| BR-05 | 禁用不删除 | 任何通道都只做 `is_active=False` 软禁用，历史数据（任务/评论/审批）全部保留；重新出现即复活并恢复原部门与角色 |
| BR-06 | 保护本地管理员 | `WS_OWNER` 与标记 `is_sync_protected=True` 的账号永不被同步禁用或改角色（防目录配置错误锁死组织） |
| BR-07 | 映射变更需干跑 | 修改属性映射 / 组映射 / 过滤条件后，必须先 Dry Run 输出影响清单，由管理员显式确认才生效 |
| BR-08 | 凭证最小化 | 系统不存储任何用户目录密码；bind 凭证仅入密保库（KMS/Vault），DB 只存引用句柄 |
| BR-09 | 审计全覆盖 | 每次同步（含干跑）产生台账；每个账号的开通/变更/禁用/复活各产生一条 `AuditLog`，`actor` 为 `directory_sync` 系统主体 |
| BR-10 | 冲突人工裁决 | 同邮箱出现「本地密码账号」与「目录账号」时，默认归并为同一 `User` 并标记 `identity_source`；归并动作不可逆，进台账高亮 |
| BR-11 | 组映射幂等 | 组→部门/角色映射按「增量计算 + 差异应用」，重复执行不产生重复归属记录 |
| BR-12 | 失败降级 | 单次同步失败（连接超时/凭证失效）只告警不动作；**绝不**因同步失败批量禁用账号 |

### 2.3 LDAP 同步流程

```mermaid
sequenceDiagram
    participant Beat as Celery Beat
    participant W as ldap_sync_worker
    participant AD as 客户 AD/LDAP
    participant SVC as DirectorySyncService
    participant DB as PostgreSQL
    participant AU as AuditLog 管道

    Beat->>W: 每 15min 派发 sync_directory.delay(config_id)
    W->>AD: LDAPS bind（密保库取凭证）+ paged search
    AD-->>W: 条目批次（每页 500，含 uSNChanged）
    W->>SVC: normalize(entries) → 与 DirectoryUserMapping 对账
    SVC->>SVC: 分桶：新增 / 变更 / 缺席 / 跳过（无邮箱）
    Note over SVC: 缺席桶查 absence_count，≥2 才进禁用桶（BR-04）
    SVC->>DB: 单事务批量应用（开通/变更/禁用/复活）
    SVC->>DB: 写 DirectorySyncRun（五类计数 + 明细 JSONB）
    SVC->>AU: on_commit → 逐条 AuditLog（幂等 event_key）
    W-->>Beat: 失败时指数退避重试 3 次，仍败则告警 WS_ADMIN
```

| 步骤 | 关键决策 |
| --- | --- |
| 全量 vs 增量 | 默认每 15 分钟增量（`uSNChanged`/`modifyTimestamp` 水位线），每日 03:30 一次全量校准；水位线存 `LdapDirectoryConfig.sync_cursor` |
| 分页 | 强制 paged results（page=500），拒绝不分页拉取（防大目录 OOM） |
| 过滤 | 管理员可配 `user_filter`（如 `(&(objectClass=user)(!(userAccountControl:1.2.840.113556.1.4.803:=2)))`），保存时服务端语法校验 |
| 属性映射 | `mail→email`、`displayName→display_name`、`department→部门路径`、`title→job_title`；可自定义 JSON 映射表 |

### 2.4 缺席双确认状态机

```mermaid
stateDiagram-v2
    [*] --> Active: 目录出现 / SCIM active=true
    Active --> Absent1: 全量同步缺席（LDAP）
    Absent1 --> Active: 重新出现（复活，恢复原归属）
    Absent1 --> Disabled: 连续第 2 次缺席
    Active --> Disabled: SCIM active=false（单次生效）
    Disabled --> Active: 重新出现 / active=true（复活）
    Disabled --> Disabled: 持续缺席（幂等，不重复动作）
```

| 字段 | 语义 |
| --- | --- |
| `absence_count` | 连续缺席次数，重新出现即清零；禁用动作只在 `absence_count` 从 1→2 的跃迁点执行一次 |
| `disabled_at_source` | `ldap_absent` / `scim_inactive` / `manual`——手工禁用优先级最高，同步永不自动复活手工禁用账号（BR-06 延伸） |

### 2.5 SCIM 服务端语义

| SCIM 操作 | 端点 | 系统动作 | 幂等处理 |
| --- | --- | --- | --- |
| 开通 | `POST /scim/v2/Users` | 按 `userName`（=邮箱）查重：存在则 409 转 200 返回既有（Okta 容忍）；不存在则创建待激活账号 | `externalId` 唯一约束兜底 |
| 全量更新 | `PUT /scim/v2/Users/{id}` | 覆盖式更新映射属性 | `meta.version` ETag 不匹配返回 409 |
| 补丁 | `PATCH /scim/v2/Users/{id}` | 仅支持 `replace active` / `replace name` / `replace emails` 三类 op | 操作日志按 `externalId+op 哈希` 去重 |
| 禁用 | `PATCH … active=false` | 单次生效软禁用（BR-04） | 重复禁用幂等 200 |
| 组推送 | `POST /scim/v2/Groups` 等 | 组 → 部门/角色映射（同 LDAP 组映射引擎） | 成员增删按差集应用（BR-11） |
| 删除 | `DELETE /scim/v2/Users/{id}` | **拒绝物理删除**：按禁用处理，返回 204 | 符合 BR-05 |

SCIM 认证：每工作空间一枚 `ScimToken`（`scim_` 前缀，SHA-256 落库仅存哈希），走 `Authorization: Bearer`；token 泄露可一键吊销重签，吊销动作入审计。

### 2.6 身份归并与冲突裁决

同邮箱三方归并的裁决矩阵（BR-02 / BR-10）：

| 既有身份 | 目录记录到达时 | 裁决 |
| --- | --- | --- |
| 无账号 | — | 创建 `User`（`identity_source=directory`），随机口令置不可用，强制走 SSO/邀请设密 |
| 本地密码账号 | 同邮箱 | 归并：绑定 `DirectoryUserMapping`，`identity_source` 改为 `merged`；台账高亮，通知本人 |
| SSO 账号（AUTH-009） | 同邮箱 | 归并：SSO 绑定保留，目录映射叠加；登录仍走 SSO |
| 已禁用目录账号 | 重新出现 | 复活：恢复 `is_active` + 原部门 + 原角色（`DirectoryUserMapping.snapshot` 回放） |
| 目录邮箱变更 | 老邮箱缺席 + 新邮箱出现 | **不自动归并**（防冒名），进「人工裁决」队列，WS_ADMIN 确认后合并 |

---

## 3. UI/UX 设计

### 3.1 页面清单与信息架构

| 页面 | 路由 | 入口 | 核心任务 |
| --- | --- | --- | --- |
| 身份源总览 | `/{ws}/settings/directory` | 工作空间设置 → 身份源 | 查看通道状态、最近同步、待办（待开通/人工裁决） |
| LDAP 配置 | `/{ws}/settings/directory/ldap` | 总览 → 配置 | 连接参数、属性映射、组映射、过滤、周期 |
| SCIM 配置 | `/{ws}/settings/directory/scim` | 总览 → 配置 | 端点 URL、Token 生成/吊销、属性映射 |
| 同步台账 | `/{ws}/settings/directory/runs` | 总览 → 台账 | 每次同步五类计数、明细下钻、失败原因 |
| 干跑确认 | `/{ws}/settings/directory/dry-run/{runId}` | 映射变更后强制跳转 | 影响清单审阅 → 确认执行 / 放弃 |

### 3.2 身份源总览线框

```
┌──────────────────────────────────────────────────────────────────┐
│ 设置 / 身份源                                    [? 帮助文档]      │
├──────────────────────────────────────────────────────────────────┤
│ ┌─ 当前通道 ──────────────────────────────────────────────────┐  │
│ │  ● LDAP/AD 同步   已启用 · 每 15 分钟                        │  │
│ │  目录: ldaps://ad.corp.example:636  (base: OU=Staff,DC=…)   │  │
│ │  最近同步: 2026-09-01 14:30 · 成功 · +3 变更 12 缺席 0       │  │
│ │  [立即同步]  [干跑一次]  [修改配置]  [停用通道…]              │  │
│ └─────────────────────────────────────────────────────────────┘  │
│ ┌─ 待办 ──────────────────────────────────────────────────────┐  │
│ │  ⚠ 待开通 (5)   席位不足，请扩容或选择不开通        [处理]    │  │
│ │  ⚠ 人工裁决 (2) 邮箱变更疑似同人，需确认            [裁决]    │  │
│ │  ⚠ 跳过 (1)     目录记录缺邮箱 (CN=svc-printer)     [详情]    │  │
│ └─────────────────────────────────────────────────────────────┘  │
│ 最近 7 天同步趋势  ▁▃▅▃▆▅▃  成功 96/96                            │
└──────────────────────────────────────────────────────────────────┘
```

### 3.3 干跑确认页线框

```
┌──────────────────────────────────────────────────────────────────┐
│ 干跑结果 · 组映射变更                                2026-09-01    │
├──────────────────────────────────────────────────────────────────┤
│ 变更摘要: 组「CN=QA-Team」映射从 部门:质量部 改为 部门:测试中心   │
│                                                                  │
│ 将影响 23 个账号:                                                 │
│ ┌──────────────────────┬────────────┬─────────────────────────┐  │
│ │ 账号                 │ 动作       │ 明细                    │  │
│ ├──────────────────────┼────────────┼─────────────────────────┤  │
│ │ wang.fang@corp.ex…   │ 部门变更   │ 质量部 → 测试中心       │  │
│ │ li.wei@corp.ex…      │ 部门变更   │ 质量部 → 测试中心       │  │
│ │ …(展开全部 23 条)                                             │  │
│ └──────────────────────┴────────────┴─────────────────────────┘  │
│ 新增 0 · 变更 23 · 禁用 0 · 跳过 0                               │
│                                                                  │
│            [放弃变更]                    [确认并应用 →]           │
└──────────────────────────────────────────────────────────────────┘
```

### 3.4 交互规则

| 场景 | 交互 |
| --- | --- |
| 保存映射配置 | 不直接生效；自动触发干跑并跳转确认页；干跑 24h 未确认则过期需重新发起 |
| 停用通道 | 二次确认弹窗说明「已有绑定保留但不再同步；账号不会被禁用」；停用动作入审计 |
| 连接测试 | 配置页「测试连接」按钮即时验证 bind + base DN + 过滤语法，返回样本前 5 条（脱敏） |
| 令牌展示 | `ScimToken` 仅创建时完整展示一次，之后只显示前后各 4 位 |
| 权限 | 仅 `WS_ADMIN` 及以上可见身份源菜单；`WS_MEMBER` 无入口（菜单服务端下发剔除） |

---

## 4. 技术架构

### 4.1 数据模型

```python
# apps/api/rp_directory/models.py
import uuid
from django.db import models
from rp_core.models import BaseModel


class DirectoryChannel(models.TextChoices):
    LDAP = "ldap", "LDAP/AD"
    SCIM = "scim", "SCIM 2.0"


class LdapDirectoryConfig(BaseModel):
    """LDAP 拉取通道配置；每工作空间至多一条 enabled（BR-01）。"""

    workspace = models.ForeignKey(
        "rp_workspaces.Workspace", on_delete=models.CASCADE,
        related_name="ldap_configs",
    )
    name = models.CharField(max_length=64)
    server_uri = models.CharField(max_length=255)          # ldaps://ad.corp:636
    bind_dn = models.CharField(max_length=255)
    bind_secret_ref = models.CharField(max_length=128)     # 密保库句柄，非密文本身
    base_dn = models.CharField(max_length=255)
    user_filter = models.CharField(
        max_length=512,
        default="(&(objectClass=user)(mail=*))",
    )
    attribute_map = models.JSONField(default=dict)         # {"mail": "email", ...}
    group_map = models.JSONField(default=list)             # [{"dn":..., "department":..., "role":...}]
    sync_interval_minutes = models.PositiveSmallIntegerField(default=15)
    use_starttls = models.BooleanField(default=False)
    sync_cursor = models.CharField(max_length=64, blank=True)  # uSNChanged 水位线
    is_enabled = models.BooleanField(default=False)

    class Meta:
        db_table = "directory_ldap_config"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace"],
                condition=models.Q(is_enabled=True),
                name="uq_ldap_enabled_per_workspace",
            ),
        ]


class ScimConnector(BaseModel):
    workspace = models.ForeignKey(
        "rp_workspaces.Workspace", on_delete=models.CASCADE,
        related_name="scim_connectors",
    )
    name = models.CharField(max_length=64)
    token_hash = models.CharField(max_length=64, unique=True)   # SHA-256
    token_prefix = models.CharField(max_length=8)               # 展示用 scim_Ab3x
    attribute_map = models.JSONField(default=dict)
    group_map = models.JSONField(default=list)
    is_enabled = models.BooleanField(default=False)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "directory_scim_connector"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace"],
                condition=models.Q(is_enabled=True),
                name="uq_scim_enabled_per_workspace",
            ),
        ]
```

### 4.2 映射与台账模型

```python
class DirectoryUserMapping(BaseModel):
    """目录身份 ↔ 本地账号 的唯一映射与复活快照。"""

    workspace = models.ForeignKey("rp_workspaces.Workspace", on_delete=models.CASCADE)
    user = models.ForeignKey(
        "rp_users.User", on_delete=models.CASCADE,
        related_name="directory_mappings",
    )
    channel = models.CharField(max_length=8, choices=DirectoryChannel.choices)
    external_id = models.CharField(max_length=128)         # objectGUID / SCIM externalId
    email_snapshot = models.EmailField()                   # 归并时邮箱（变更检测用）
    absence_count = models.PositiveSmallIntegerField(default=0)
    disabled_at_source = models.CharField(
        max_length=16, blank=True,
    )  # ldap_absent / scim_inactive / manual / ""
    restore_snapshot = models.JSONField(default=dict)      # {"departments":[...], "roles":[...]}

    class Meta:
        db_table = "directory_user_mapping"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "channel", "external_id"],
                name="uq_directory_identity",
            ),
        ]
        indexes = [
            models.Index(fields=["workspace", "email_snapshot"],
                         name="idx_dir_mapping_email"),
        ]


class DirectorySyncRun(BaseModel):
    """每次同步（含干跑）的台账头；明细存 detail JSONB。"""

    class Status(models.TextChoices):
        RUNNING = "running", "执行中"
        SUCCESS = "success", "成功"
        FAILED = "failed", "失败"
        DRY_RUN = "dry_run", "干跑"
        CONFIRMED = "confirmed", "干跑已确认"

    workspace = models.ForeignKey("rp_workspaces.Workspace", on_delete=models.CASCADE)
    channel = models.CharField(max_length=8, choices=DirectoryChannel.choices)
    status = models.CharField(max_length=12, choices=Status.choices,
                              default=Status.RUNNING)
    is_dry_run = models.BooleanField(default=False)
    triggered_by = models.CharField(max_length=16, default="beat")  # beat/manual/dry_run
    counts = models.JSONField(default=dict)   # {"created":3,"updated":12,"disabled":0,"skipped":1,"failed":0}
    detail = models.JSONField(default=list)   # 逐条 {email, action, reason}
    error = models.TextField(blank=True)
    confirmed_by = models.ForeignKey(
        "rp_users.User", null=True, blank=True, on_delete=models.SET_NULL,
    )
    expires_at = models.DateTimeField(null=True, blank=True)  # 干跑 24h 过期

    class Meta:
        db_table = "directory_sync_run"
        indexes = [
            models.Index(fields=["workspace", "-created_at"],
                         name="idx_dir_run_ws_created"),
        ]
```

迁移要点：`directory_user_mapping` 建表与唯一约束同迁移；`uq_*_enabled_per_workspace` 部分唯一索引依赖 PostgreSQL，`CREATE INDEX CONCURRENTLY` 不适用新表（直接建）；大字段 `detail` 上限 10 万字符，超出截断留 `truncated=true` 标记。

### 4.3 归并服务（DirectorySyncService）

```python
# apps/api/rp_directory/services.py
from dataclasses import dataclass, field
from django.db import transaction
from rp_users.models import User
from rp_audit.services import write_audit_log  # AUTH-010 幂等管道


@dataclass
class SyncBuckets:
    created: list = field(default_factory=list)
    updated: list = field(default_factory=list)
    disabled: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    failed: list = field(default_factory=list)

    def counts(self) -> dict:
        return {k: len(getattr(self, k)) for k in
                ("created", "updated", "disabled", "skipped", "failed")}


class DirectorySyncService:
    """LDAP 与 SCIM 共用的归并裁决核心；所有动作幂等、可干跑。"""

    ABSENCE_THRESHOLD = 2  # BR-04 双确认

    def __init__(self, workspace, channel: str, dry_run: bool = False):
        self.workspace = workspace
        self.channel = channel
        self.dry_run = dry_run
        self.buckets = SyncBuckets()

    def reconcile(self, entries: list[dict]) -> SyncBuckets:
        """entries: 规范化后的目录记录 [{external_id, email, attrs...}]"""
        seen_ids = {e["external_id"] for e in entries}
        with transaction.atomic():
            for entry in entries:
                self._reconcile_one(entry)
            self._reconcile_absent(seen_ids)
            if self.dry_run:
                transaction.set_rollback(True)  # 干跑：全部回滚
        return self.buckets

    def _reconcile_one(self, entry: dict) -> None:
        email = (entry.get("email") or "").strip().lower()
        if not email:  # BR-02
            self.buckets.skipped.append(
                {"external_id": entry["external_id"], "reason": "missing_email"})
            return
        mapping = DirectoryUserMapping.objects.filter(
            workspace=self.workspace, channel=self.channel,
            external_id=entry["external_id"],
        ).select_related("user").first()
        if mapping is None:
            self._create_or_merge(entry, email)
            return
        if not mapping.user.is_active:
            self._reactivate(mapping)          # 复活 + 快照回放（BR-05）
            return
        if self._attrs_changed(mapping, entry):
            self._apply_update(mapping, entry)  # 变更：显示名/部门/角色

    def _reconcile_absent(self, seen_ids: set) -> None:
        if self.channel == DirectoryChannel.SCIM:
            return  # SCIM 以 active=false 推送为准，无缺席概念
        qs = DirectoryUserMapping.objects.filter(
            workspace=self.workspace, channel=self.channel,
            user__is_active=True,
        ).exclude(external_id__in=seen_ids).exclude(
            disabled_at_source="manual")
        for mapping in qs.select_related("user"):
            mapping.absence_count += 1
            if mapping.absence_count >= self.ABSENCE_THRESHOLD:
                self._disable(mapping, source="ldap_absent")
            else:
                mapping.save(update_fields=["absence_count", "updated_at"])

    def _disable(self, mapping, *, source: str) -> None:
        user = mapping.user
        if self._is_protected(user):           # BR-06
            self.buckets.skipped.append(
                {"email": user.email, "reason": "sync_protected"})
            return
        mapping.restore_snapshot = self._snapshot_membership(user)
        mapping.disabled_at_source = source
        user.is_active = False
        if not self.dry_run:
            user.save(update_fields=["is_active", "updated_at"])
            mapping.save()
            write_audit_log.delay_on_commit(  # 幂等 event_key
                actor="directory_sync", action="user.disable",
                target_id=str(user.id),
                event_key=f"dirsync:{self.channel}:{mapping.external_id}:disable",
            )
        self.buckets.disabled.append({"email": user.email, "source": source})
```

| 设计点 | 说明 |
| --- | --- |
| 单事务 + 干跑回滚 | `reconcile` 整批一个事务；干跑置 `set_rollback(True)` 保证零副作用但桶计数真实 |
| 幂等 | 所有动作按当前状态前置判断（已禁用不重复禁用）；审计 `event_key` 唯一兜底 |
| 保护名单 | `_is_protected` = `WS_OWNER` 或 `is_sync_protected`（BR-06） |

### 4.4 Celery 任务与 LDAP 拉取

```python
# apps/api/rp_directory/tasks.py
from celery import shared_task
from django.db import transaction


@shared_task(bind=True, queue="directory",
             autoretry_for=(LdapConnectionError,),
             retry_backoff=True, retry_kwargs={"max_retries": 3})
def ldap_sync(self, config_id: str, *, dry_run: bool = False,
              triggered_by: str = "beat") -> str:
    config = LdapDirectoryConfig.objects.select_related("workspace").get(
        id=config_id, is_enabled=True)
    run = DirectorySyncRun.objects.create(
        workspace=config.workspace, channel="ldap",
        is_dry_run=dry_run, triggered_by=triggered_by)
    try:
        entries = LdapClient(config).paged_search(page_size=500)
        svc = DirectorySyncService(config.workspace, "ldap", dry_run=dry_run)
        buckets = svc.reconcile(entries)
        run.status = "dry_run" if dry_run else "success"
        run.counts = buckets.counts()
        run.detail = _serialize_buckets(buckets)
        if not dry_run:
            config.sync_cursor = entries[-1]["usn_changed"] if entries else config.sync_cursor
            config.save(update_fields=["sync_cursor", "updated_at"])
    except Exception as exc:                      # noqa: BLE001 — 台账必须落失败原因
        run.status, run.error = "failed", str(exc)[:2000]
        notify_workspace_admins.delay(            # BR-12：只告警不动作
            config.workspace_id, "directory_sync_failed",
            {"run_id": str(run.id), "error": run.error[:200]})
        run.save()
        raise
    run.save()
    return str(run.id)
```

| 要点 | 说明 |
| --- | --- |
| 独立队列 | `directory` 队列与 `webhook`/`activity` 隔离，避免大目录同步阻塞通知投递 |
| 派发时机 | API 触发（立即同步/干跑）一律 `transaction.on_commit`；beat 触发无需 |
| 重叠防护 | beat 每 15min 派发前查同 config 有无 `running` 状态 run，有则跳过本轮（Redis 锁 `dirsync:{config_id}`，TTL=interval） |
| 席位检查 | `_create_or_merge` 内查许可席位，满则入「待开通」队列并通知（BR-03），不抛异常中断整批 |

### 4.5 API 端点

| 方法 | 路径 | 说明 | 权限 |
| --- | --- | --- | --- |
| GET | `/api/v1/workspaces/{slug}/directory/` | 通道总览（启用状态 + 最近 run + 待办计数） | WS_ADMIN |
| PUT | `/api/v1/workspaces/{slug}/directory/ldap/` | 创建或整体替换 LDAP 配置（集合替换语义，PUT 白名单） | WS_ADMIN |
| PATCH | `/api/v1/workspaces/{slug}/directory/ldap/` | 修改周期/过滤/映射（触发干跑强制流程） | WS_ADMIN |
| POST | `/api/v1/workspaces/{slug}/directory/ldap/test/` | 连接测试（bind + base + filter 样本 5 条脱敏） | WS_ADMIN |
| POST | `/api/v1/workspaces/{slug}/directory/sync/` | 立即同步（`{"dry_run": false}`） | WS_ADMIN |
| GET | `/api/v1/workspaces/{slug}/directory/runs/` | 台账列表（cursor 分页） | WS_ADMIN |
| GET | `/api/v1/workspaces/{slug}/directory/runs/{id}/` | 台账明细 | WS_ADMIN |
| POST | `/api/v1/workspaces/{slug}/directory/runs/{id}/confirm/` | 确认干跑结果并应用 | WS_ADMIN |
| PUT | `/api/v1/workspaces/{slug}/directory/scim/` | 启用 SCIM 并签发 Token（仅本次返回明文） | WS_ADMIN |
| DELETE | `/api/v1/workspaces/{slug}/directory/scim/token/` | 吊销 Token | WS_ADMIN |
| * | `/scim/v2/Users`、`/scim/v2/Groups` | SCIM 协议端点（Bearer Token 认证，不走 Session） | ScimToken |

**成功示例** — `POST …/directory/sync/`：

```json
{
  "status": "success",
  "data": {
    "run_id": "01J6ZQK4M2N8PXRVTBWY3HD5EA",
    "status": "running",
    "is_dry_run": false,
    "message": "同步任务已派发，请稍后在台账查看结果"
  },
  "meta": {"request_id": "01J6ZQK5G8QF3N2T4H7V9XW1YB"}
}
```

**错误示例** — 另一通道已启用（BR-01）：

```json
{
  "status": "error",
  "error": {
    "code": "RESOURCE_STATE_INVALID",
    "message": "已启用 SCIM 通道，须先停用并完成归属移交干跑后才能启用 LDAP",
    "details": [{"field": "channel", "code": "INVALID",
                 "message": "当前启用通道: scim"}]
  },
  "meta": {"request_id": "01J6ZQK6H2RM8P5W3N7T1VY4ZC"}
}
```

**错误示例** — 未确认干跑直接改映射：

```json
{
  "status": "error",
  "error": {
    "code": "RESOURCE_STATE_INVALID",
    "message": "映射变更需先完成干跑确认",
    "details": [{"field": "group_map", "code": "REQUIRED",
                 "message": "存在未确认的干跑 01J6ZP…，请先确认或放弃"}]
  },
  "meta": {"request_id": "01J6ZQK7J9SN2Q6X4M8W0VZ3AD"}
}
```

### 4.6 前端 Store（MobX）

```typescript
// apps/web/src/modules/directory/directory.store.ts
import { makeAutoObservable, runInAction } from "mobx";

interface IDirectoryOverview {
  channel: "ldap" | "scim" | null;
  enabled: boolean;
  lastRun: { id: string; status: string; counts: Record<string, number> } | null;
  todos: { pendingProvision: number; manualReview: number; skipped: number };
}

export class DirectoryStore {
  overview: IDirectoryOverview | null = null;
  dryRunDetail: ISyncRunDetail | null = null;
  isSyncing = false;

  constructor(private workspaceSlug: string) {
    makeAutoObservable(this);
  }

  get hasBlockingTodos(): boolean {
    const t = this.overview?.todos;
    return !!t && (t.pendingProvision + t.manualReview) > 0;
  }

  async triggerSync(dryRun: boolean) {
    this.isSyncing = true;
    try {
      const res = await directoryService.sync(this.workspaceSlug, dryRun);
      if (dryRun) await this.pollRunUntilDone(res.data.run_id);
      return res.data.run_id;
    } finally {
      runInAction(() => { this.isSyncing = false; });
    }
  }

  async confirmDryRun(runId: string) {
    await directoryService.confirmRun(this.workspaceSlug, runId);
    await this.fetchOverview();   // 应用后刷新待办与通道状态
  }
}
```

| 前端规则 | 说明 |
| --- | --- |
| 轮询 | 干跑/立即同步后按 2s×5 → 5s×N 退避轮询 run 状态，`success/failed` 即止 |
| SWR 缓存键 | `DIRECTORY_OVERVIEW(ws)` / `DIRECTORY_RUNS(ws, cursor)`，确认干跑后 mutate 两个键 |
| 错误呈现 | `RESOURCE_STATE_INVALID`（BR-01/BR-07）弹引导对话框而非 Toast（需用户决策） |

### 4.7 安全评审清单（签字归档项）

| # | 检查项 | 标准 |
| --- | --- | --- |
| 1 | bind 凭证 | 仅密保库句柄落 DB；日志/台账/审计任何位置不出现密文；连接测试不回显 |
| 2 | 传输加密 | `ldap://` 明文连接拒绝保存（强制 `ldaps://` 或 StartTLS）；证书校验不可关闭（私有化自签 CA 走受信根导入，非 skip-verify） |
| 3 | SCIM Token | SHA-256 落库、仅创建时明文展示一次、可吊销；SCIM 端点独立限流 60/min（`AUTH_INVALID_TOKEN` 连续 10 次封 IP 15min） |
| 4 | 日志脱敏 | 目录条目进台账前剥离 `userPassword`/`unicodePwd` 等敏感属性（黑名单过滤） |
| 5 | 越权面 | SCIM 端点绑定 `workspace`（Token 解析即定位），跨空间 ID 一律 `RESOURCE_NOT_FOUND` |
| 6 | 注入面 | `user_filter` 保存时做 LDAP 过滤器语法白名单校验 |

### 4.8 性能与规模

| 指标 | 预算 | 手段 |
| --- | --- | --- |
| 1 万账号全量同步 | < 10 min | paged 500/页 + 批量 `bulk_create`/`bulk_update`（每批 500）；部门/角色差集应用 |
| 同步期 DB 压力 | 分批事务 < 30s/批 | 每 1000 条一个事务（干跑除外——干跑单事务保证回滚原子性，限定干跑条目 ≤ 5000） |
| SCIM 开通延迟 | P95 < 500ms | 同步路径无 Celery 跳转，直接写库；审计异步 |
| 缺席扫描 | 1 万映射 < 5s | `exclude(external_id__in=…)` 走 `uq_directory_identity` 索引 |

---

## 5. 测试用例

### 5.1 单元测试（UT）

| 编号 | 用例 | 断言 |
| --- | --- | --- |
| UT-01 | 无邮箱目录记录 | 进 skipped 桶，reason=`missing_email`，不落库 |
| UT-02 | 邮箱大小写/空格归一 | `  Wang.Fang@Corp.com ` 与 `wang.fang@corp.com` 归并为同一人 |
| UT-03 | 首次同步开通 | 创建 `User`（不可用口令）+ `DirectoryUserMapping`，`identity_source=directory` |
| UT-04 | 本地账号归并 | 同邮箱本地账号被绑定，`identity_source=merged`，台账高亮 |
| UT-05 | 缺席一次 | `absence_count=1`，账号仍 active |
| UT-06 | 连续缺席两次 | 第二次后 `is_active=False`，`disabled_at_source=ldap_absent`，快照已存 |
| UT-07 | 复活 | 重新出现后 `is_active=True` 且部门/角色按快照回放 |
| UT-08 | 手工禁用不复活 | `disabled_at_source=manual` 的账号重新出现也不复活 |
| UT-09 | 保护名单 | `WS_OWNER`/`is_sync_protected` 缺席两次仍 active，进 skipped |
| UT-10 | 干跑回滚 | dry_run 后数据库零变化，桶计数正确 |
| UT-11 | SCIM 重复开通 | 同 `externalId` 第二次 POST 返回 200 既有账号，不重复创建 |
| UT-12 | SCIM DELETE | 按禁用处理，返回 204，历史任务保留 |
| UT-13 | 席位满 | 新成员进待开通队列，WS_ADMIN 收到通知，批次继续 |
| UT-14 | 单通道互斥 | 启用 LDAP 时启用 SCIM 返回 `RESOURCE_STATE_INVALID` |
| UT-15 | 邮箱变更不归并 | 老缺席+新出现进入人工裁决队列，不自动合并 |

### 5.2 集成测试（IT）

| 编号 | 用例 | 断言 |
| --- | --- | --- |
| IT-01 | AD fixture 容器全量同步 | 1,000 条目录记录 90s 内完成，五类计数与 fixture 清单一致 |
| IT-02 | 增量水位线 | 修改 3 条后增量同步只处理 3 条，`sync_cursor` 前移 |
| IT-03 | 同步失败告警 | 关闭 AD 端口后同步 failed，无任何账号被禁用，管理员收到通知 |
| IT-04 | Okta 沙盒 SCIM 全链路 | 开通→改部门→禁用→重激活四步，每步幂等重放结果一致 |
| IT-05 | 重叠防护 | 连续触发两次立即同步，第二个任务检测到 running 锁跳过 |
| IT-06 | 审计完整性 | 同步产生的每条动作均有对应 `AuditLog` 且 `event_key` 唯一 |
| IT-07 | 映射变更强制干跑 | PATCH `group_map` 后未确认干跑直接保存被拒（409） |

### 5.3 E2E 测试

| 编号 | 场景 | 验收 |
| --- | --- | --- |
| E2E-01 | 管理员配置 LDAP → 测试连接 → 干跑 → 确认 → 立即同步 | 总览页计数与台账一致，待办清零 |
| E2E-02 | 离职禁用 | AD 中删除用户 → 两次同步后该账号登录返回 `AUTH_ACCOUNT_DISABLED` |
| E2E-03 | 入职开通 | AD 新增用户 → 15 分钟内可经 SSO 登录（`AUTH-009` 链路），部门归属正确 |
| E2E-04 | SCIM Token 吊销 | 吊销后旧 Token 请求返回 `AUTH_TOKEN_REVOKED` |

---

## 6. 竞品深度对标

| 维度 | Ones（私有化） | GitLab | Okta 范式 | 本系统 |
| --- | --- | --- | --- | --- |
| 同步模式 | 全量覆盖式周期拉取 | 全量 + group sync | 推送式 | 增量水位线 + 每日全量校准 |
| 禁用语义 | 目录缺席即禁用 | `ldap:check` 后 block | `active=false` | **双确认缺席禁用**（防网络分区误伤） |
| 干跑 | ❌ | `rake ldap:check`（只读输出） | 事件可回放 | 事务回滚式干跑 + 强制确认流（BR-07） |
| 身份归并 | 邮箱归并（自动） | extern_uid + email | `externalId` | 三方归并矩阵 + 邮箱变更人工裁决（§2.6） |
| 删除语义 | 软禁用 | block | `active=false` | 软禁用 + 快照回放复活（BR-05） |
| 代码路径 | `ldap_sync_service.rb`（GitLab CE `lib/gitlab/ldap/`） | 同左 | SCIM RFC 7643/7644 | `rp_directory/services.py` 单裁决核心，双通道复用 |

**结论**：Ones 强在私有化 AD 对接成熟度但禁用语义激进；GitLab 的干跑思想值得采纳但仅限只读输出；本系统的差异化是「事务回滚干跑 + 双确认禁用 + 快照回放复活」三件套，直接针对企业客户最怕的两类事故——误批量禁用与离职漏禁。

---

## 7. 里程碑与验收

### 7.1 工作量估算

| 交付面 | 内容 | 估算 |
| --- | --- | --- |
| Model / Migration | 4 表 + 唯一约束 + 索引 | 1.5 d |
| 后端 | `DirectorySyncService` 裁决核心、LdapClient、SCIM 协议视图（Users/Groups 六端点）、Celery 任务与 beat 注册、审计挂接 | 6 d |
| 前端 | 总览/配置/台账/干跑确认四页 + Store + 轮询 | 4 d |
| 安全评审 | §4.7 六项检查 + 整改 | 1.5 d |
| 测试 | UT-01~15、IT-01~07、E2E-01~04 | 3 d |
| **合计** | | **16 d（约 3.2 人周，2 人并行 2 周）** |

### 7.2 可操作演示的验收标准

1. AD fixture（1,000 用户 12 组）全量同步一轮：台账计数与 fixture 清单逐条一致；修改 5 条后增量同步只处理 5 条。
2. 双确认演示：删除 AD 用户 → 第一次同步后仍 active → 第二次后禁用 → 登录返回 `AUTH_ACCOUNT_DISABLED` → AD 恢复用户 → 下一次同步复活且部门/角色与删除前一致。
3. Okta 沙盒 SCIM：开通 → 变更 → 禁用全链路；同请求重放三次不产生重复账号或重复审计。
4. 干跑强制流：改组映射 → 自动干跑 → 确认页影响清单正确 → 应用生效；绕过干跑直接 PATCH 被 409 拒绝。
5. 席位满：新目录成员进待开通队列且管理员收到通知，批次其余成员正常开通。
6. 零回归：未启用通道的工作空间全量 API 契约测试通过（与企业版 V1.0 快照比对无差异）。
7. 安全评审六项全过，扫描无凭证泄露（日志/台账/审计全文 grep `unicodePwd`/`userPassword`/bind 密文 = 0 命中）。
