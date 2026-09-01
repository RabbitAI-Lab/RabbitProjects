# 团队归档与全局模板配置

| 元信息项 | 内容 |
| --- | --- |
| 文档编号 | TEAM-003 |
| 所属迭代 | Sprint 5 — 集成 + 标准版收尾（第 7 周） |
| 优先级 | P2（标准版完整级） |
| 所属模块 | M2-TEAM｜团队管理 |
| 文档状态 | 待评审（Draft） |
| 最后更新日期 | 2026-09-01 |
| 上游依赖 | `TEAM-002`（成员管理与角色）、`PROJ-002`（归档写保护守卫范式）、`PROJ-003`（项目模板机制——全局模板是其工作空间级延伸）、`TASK-008`（自定义字段定义模型） |
| 下游消费 | Sprint 8 组织治理（部门/角色依托工作空间治理面）、`AUTH-012`（P4 多租户——工作空间隔离与归档语义是其基线）、`PROJ-004`（P3 项目集消费全局标签） |
| 上游依据 | `docs/需求文档.md` §3.1（团队归档 / 全局模板配置 / 成员活跃度）、§8.2 团队管理 P2 列 |
| 关联架构文档 | [`api-conventions.md`](../architecture/api-conventions.md)（错误码 / 幂等动作）、[`rbac-permission-model.md`](../architecture/rbac-permission-model.md)（WS_OWNER/WS_ADMIN 权限码） |
| 对标基线 | Plane Workspace（无归档、无全局配置） · Ones 团队管理（归档 + 全局字段/状态模板下发） |
| 工作量估算 | 后端 2.5 人日 / 前端 1.5 人日 / 联调与测试 1 人日，合计 **5 人日** |

---

## 1. 概述

### 1.1 功能定位

工作空间（团队）此前只有「存在」一种状态，`TEAM-003` 补齐治理面三件事：

1. **团队归档**：整个工作空间一键只读冻结（全部项目联动只读），可恢复——客户暂停合作、季度封板、试点收尾的标准动作；
2. **全局模板配置**：工作空间级**全局标签**（下发到全部项目、项目可覆盖同名）与**基础状态模板**（新项目默认状态集来源），把 `PROJ-003` 模板机制上移到空间层；
3. **成员活跃度统计**：WS_ADMIN 可见的空间级聚合视图（活跃成员数 / 任务变更数 / 登录天数分布）——**只显聚合，不显个人明细**（隐私红线 BR-09）。

### 1.2 关键约定

| 约定 | 内容 |
| --- | --- |
| 归档 = 空间级只读 | 复用 `PROJ-002` 写保护范式上移一层：`PERM_WORKSPACE_ARCHIVED` 拦截一切写请求；登录/只读/导出不受限 |
| 可逆 | 归档 ↔ 恢复自由往返（WS_OWNER 专属）；无「关闭」终态（空间级删除归既有危险操作区，本文档不涉及） |
| 全局标签 = 下发 + 覆盖 | 全局标签以 `origin=global` 出现在每个项目；项目可创建同名本地标签**覆盖**显示（颜色/描述），不阻断全局更新其余属性 |
| 活跃度只聚合 | 任何接口不返回「某成员某日做了什么」级明细；最小聚合粒度 = 周；明细需求归 P3 `AUTH-010` 审计体系（合规语境另议） |
| 状态模板只管新建 | 全局状态模板变更不影响既有项目（快照语义，`PROJ-003` 同纪律） |

### 1.3 交付内容

| # | 能力 | 说明 |
| --- | --- | --- |
| 1 | 空间归档/恢复 | `Workspace.archived_at` 加列 + 幂等动作端点 + 全站写保护扩展 |
| 2 | 全局标签 | `WorkspaceLabel` 新表 + 下发合并逻辑 + 项目覆盖机制 |
| 3 | 基础状态模板 | 空间级默认状态集（新建项目/模板实例化时的兜底来源） |
| 4 | 活跃度统计 | `GET …/workspace/activity-stats/`：三聚合指标 + 周粒度分布 |
| 5 | 治理设置页 | 空间设置新增「归档」「全局标签」「状态模板」「活跃度」四区块 |

### 1.4 范围边界

| 能力 | 本文档（P2） | 归属 |
| --- | --- | --- |
| 空间归档/恢复 + 写保护 | ✅ | — |
| 全局标签（下发+覆盖）/ 状态模板（快照） | ✅ | — |
| 活跃度聚合统计 | ✅ | — |
| 空间删除 / 转让 | ❌（既有危险操作区） | `TEAM-002` |
| 多工作空间层级治理 | ❌ | P3/P4 |
| 活跃度个人明细 / 考勤化报表 | ❌（隐私红线） | P3 `AUTH-010` 合规审计另议 |
| 全局自定义字段下发 | ❌（字段归项目/模板层） | P3 评估 |
| 合规留存策略 | ❌ | P4 `FILE-006` |

### 1.5 前置依赖

| 依赖 | 内容 | 阻塞原因 |
| --- | --- | --- |
| `PROJ-002` | 写保护中间件（`PERM_PROJECT_ARCHIVED` 范式） | 空间级扩展的模板 |
| `PROJ-003` | `workspace_active` 守卫（项目恢复依赖空间未归档） | 联动语义 |
| `TEAM-002` | `WorkspaceMember.role` 与成员列表页 | 设置页挂载 |
| Sprint 0 | `Label` / `State` 模型 | 全局下发目标 |

### 1.6 竞品参考

| 竞品 | 参考点 | 处置 |
| --- | --- | --- |
| Plane | Workspace 无归档态、无全局标签 | 本系统原创增量（Ones 对位） |
| Ones | 团队归档 + 全局状态/标签下发 + 成员活跃度报表 | 归档与下发语义对齐；Ones 活跃度含个人明细——本系统红线只聚合（§1.2） |
| Jira | 全局状态/工作流共享方案（scheme） | 下发思想对齐；Jira scheme 是引用式（改全局影响全部）——本系统快照+覆盖语义更安全 |

---

## 2. 业务逻辑

### 2.1 空间归档与恢复

```mermaid
stateDiagram-v2
    [*] --> active
    active --> archived: 归档（WS_OWNER，二次确认）
    archived --> active: 恢复（WS_OWNER）
    active --> [*]: 删除（既有危险操作，本文档不涉及）
```

| 面 | archived 态行为 |
| --- | --- |
| 写请求 | 一律 `403 PERM_WORKSPACE_ARCHIVED`（含项目/任务/评论/文件/设置全族） |
| 只读 | 登录、浏览、搜索、统计、导出正常 |
| 项目联动 | 各项目逻辑上等同 archived（不逐行改写项目状态——**状态派生**而非状态复制，恢复时零回写成本） |
| 成员 | 不可邀请/移出/改角色；既有成员只读保留 |
| 集成 | Webhook 停扇出（`INTG-002` BR-12 上移）；GitHub 同步暂停；API Key 只读端点可用、写端点 403 |
| 项目恢复守卫 | `PROJ-003` 的 `workspace_active` guard：空间归档中项目不可 archived→active |
| 通知 | 归档/恢复时全员通知（`COLLAB-001`）+ Activity（workspace 域） |

### 2.2 全局标签：下发与覆盖

```mermaid
flowchart TB
    G["WorkspaceLabel（全局）<br/>bug 红 / feature 紫 / urgent 橙"] -->|下发| P1["项目 A 标签集"]
    G -->|下发| P2["项目 B 标签集"]
    P1 --> L1["项目 A 本地覆盖：<br/>同名 bug → 改色深红 + 本地描述"]
    G2["全局更新 bug 描述"] -->|覆盖存在：仅同步未被覆盖的属性| L1
    G3["全局新增 security 标签"] -->|自动出现| P1
    G3 -->|自动出现| P2
```

| 规则 | 内容 |
| --- | --- |
| 下发 | 项目标签列表 = 全局标签（`origin=global`）∪ 项目本地标签；任务打标签可引用任一来历 |
| 覆盖 | 项目可建同名本地标签：显示属性（color/description）以本地为准，**不阻断**全局其他属性更新（边界 #3 逐项合并表） |
| 删除全局 | 已引用该标签的任务**保留文字快照**（`label_name_snapshot`），标签实体软删；新项目不再下发 |
| 冲突判定 | 同名 = `lower(trim(name))` 归一化后相等 |

### 2.3 基础状态模板

| 规则 | 内容 |
| --- | --- |
| 结构 | 空间级 `default_states` JSON 快照（[{name, group, color, sequence}]，五组各 ≥1 项，校验同 `PROJ-003` 模板） |
| 生效点 | 新建项目（空白模板时）与项目模板未含状态集时的兜底来源 |
| 快照语义 | 修改全局状态模板**不影响**既有项目与既有项目模板（与 §1.2 约定五一致） |

### 2.4 成员活跃度统计

| 指标 | 口径 | 粒度 |
| --- | --- | --- |
| `active_members_7d / 30d` | 期内有任意写操作（任务/评论/工时/文件）的去重成员数 | 空间 |
| `contribution_distribution` | 按周分桶：0 / 1-5 / 6-20 / >20 次操作的成员数分布 | 周 × 桶（无个人行） |
| `login_days_histogram` | 期内登录天数 1/2-3/4-5/6+ 的成员数分布 | 月 |
| `top_actions` | 操作类型构成（任务变更/评论/文件/工时占比） | 空间 |

> **隐私红线（BR-09）**：以上四指标无任何「个人 × 明细」可还原性（无 per-user 行、无 per-day 行）；接口实现层禁止暴露 `user_id` 维度——代码评审红线 + UT 断言响应 Schema 无 `user_id` 键。

### 2.5 业务规则汇总

| 编号 | 规则 | 说明 / 验收点 |
| --- | --- | --- |
| BR-01 | 归档/恢复幂等：重复请求 200 + 当前态，不产生重复 Activity/通知 | IT 守护 |
| BR-02 | 归档写保护单入口：中间件查 `workspace.archived_at`，全资源族生效 | 中间件测试 |
| BR-03 | 仅 WS_OWNER 可归档/恢复（WS_ADMIN 不可——空间级生死归所有者） | 权限矩阵 |
| BR-04 | 归档不逐行改项目状态（状态派生） | 恢复零回写验证 |
| BR-05 | 全局标签下发并集展示；同名本地覆盖仅覆盖显示属性 | 合并逻辑 UT |
| BR-06 | 全局标签删除：任务保留名称快照，实体软删 | 数据测试 |
| BR-07 | 状态模板快照语义：改全局不动既有 | 同 PROJ-003 纪律 |
| BR-08 | 归档空间内禁止：项目恢复 / 模板实例化 / 新成员邀请 / 集成同步 | 守卫矩阵 |
| BR-09 | 活跃度接口 Schema 无 `user_id` 维度；最小粒度周 | Schema UT 断言 |
| BR-10 | 活跃度仅 WS_ADMIN+ 可见 | 权限码 `team.stats.read` |
| BR-11 | 全局标签上限 100 / 空间；名称 ≤ 50 字符 | 400 `VALIDATION_ERROR` |
| BR-12 | 归档空间不计入「新建项目」入口（按钮禁用 + API 403） | 双端验证 |

### 2.6 异常处理

| 场景 | 处理 |
| --- | --- |
| 非 OWNER 归档 | `403 PERM_WORKSPACE_OWNER_REQUIRED` |
| 归档中写请求 | `403 PERM_WORKSPACE_ARCHIVED` + 响应体附 `archived_at` 与恢复提示 |
| 全局标签超限/重名 | `400 VALIDATION_ERROR` |
| 活跃度窗口非法 | `400 VALIDATION_ERROR`（`days` 白名单 7/30/90） |
| 覆盖合并冲突（全局本地同时改同一属性） | 本地优先（BR-05），无错误——语义定义即答案 |

### 2.7 边界条件

| # | 边界 | 行为 |
| --- | --- | --- |
| 1 | 归档中到期任务 | 不触发逾期通知（写保护含通知面） |
| 2 | 归档中有排程 Webhook 重试 | 继续至天然终态；不产生新事件 |
| 3 | 全局改色 + 本地改描述 | 合并：本地描述 + 全局新色（逐项合并） |
| 4 | 项目删除本地覆盖 | 回落到全局显示属性 |
| 5 | 新成员加入归档空间 | 不可加入（BR-08）；恢复后可 |
| 6 | 活跃度 0 成员空间 | 全 0 + 空态插画 |
| 7 | 禁用成员活跃度 | 不计入（禁用后无操作）；历史周桶数据不回溯 |
| 8 | 归档空间 API Key | 只读端点可用；写端点 403 同中间件 |
| 9 | 恢复后项目状态 | 各项目回到归档前自身状态（active/archived 原样，因派生语义零回写） |
| 10 | 全局标签超项目上限 | 项目标签总数 = 全局 ∪ 本地，上限仅约束本地创建（BR-11 分层） |

---

## 3. UI/UX 设计

### 3.1 工作空间设置（治理四区块）

```
┌──────────────────────────────────────────────────────────────────┐
│ 工作空间设置 · Acme                                                │
│ [常规] [成员] [全局标签] [状态模板] [活跃度] [归档]                  │
├──────────────────────────────────────────────────────────────────┤
│ ▍全局标签                                            [+ 新建标签]  │
│  下发到全部项目；项目可用同名本地标签覆盖显示。                     │
│  ● bug        #E5484D   缺陷类问题        [编辑] [删除]            │
│  ● feature    #8E4EC6   新功能            [编辑] [删除]            │
│  ● urgent     #F76B15   需要立即处理      [编辑] [删除]            │
│  ⓘ 12/100                                                       │
├──────────────────────────────────────────────────────────────────┤
│ ▍基础状态模板                                                     │
│  新建项目的默认状态集（快照语义，不影响既有项目）。                  │
│  Backlog → Todo → In Progress → In Review → Done → Cancelled     │
│  [编辑状态集]                                                     │
├──────────────────────────────────────────────────────────────────┤
│ ▍归档                                                             │
│  归档后整个工作空间只读：成员可登录浏览，不可做任何修改。           │
│  仅所有者可操作，可随时恢复。                        [归档工作空间] │
└──────────────────────────────────────────────────────────────────┘
```

### 3.2 归档确认与归档态横幅

```
┌─ 归档工作空间 Acme？ ──────────────────────────────┐
│ · 全部 6 个项目将变为只读                            │
│ · 成员可继续登录浏览与导出，但不能修改任何内容        │
│ · 集成同步与 Webhook 将暂停                          │
│ · 仅你（所有者）可以恢复                             │
│ 输入工作空间名称「Acme」确认: [________]             │
│                        [取消]  [确认归档]（禁用态）   │
└─────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────┐
│ 🔒 工作空间已归档 · 只读模式   [联系所有者恢复]         │
└──────────────────────────────────────────────────────┘  ← 全站顶置横幅（非 OWNER）
┌──────────────────────────────────────────────────────┐
│ 🔒 工作空间已归档 · 只读模式   [恢复工作空间]           │
└──────────────────────────────────────────────────────┘  ← OWNER 视角
```

### 3.3 活跃度页

```
┌─ 成员活跃度（WS_ADMIN 可见）────────────────────────────┐
│ 窗口: [近 7 天▾]                                        │
│ ┌─────────────┐ ┌─────────────┐ ┌─────────────────────┐ │
│ │ 活跃成员 7d │ │ 活跃成员 30d│ │ 操作构成            │ │
│ │   18 / 24   │ │   21 / 24   │ │ 任务52% 评论28%     │ │
│ │             │ │             │ │ 文件12% 工时8%      │ │
│ └─────────────┘ └─────────────┘ └─────────────────────┘ │
│ 周贡献分布（成员数）          登录天数分布（成员数）        │
│  0次    ▓▓ 4                  1天   ▓ 2                  │
│  1-5次  ▓▓▓▓▓ 9              2-3天 ▓▓▓▓ 8               │
│  6-20次 ▓▓▓ 6                4-5天 ▓▓▓▓▓▓ 11            │
│  >20次  ▓ 1                   6+天  ▓▓▓ 5                │
│ ⓘ 仅聚合统计，不提供个人明细（隐私保护）                   │
└──────────────────────────────────────────────────────────┘
```

### 3.4 空状态 / 响应式 / 无障碍

- 全局标签空态：「还没有全局标签」+ 一键导入内置三组（bug/feature/urgent）；
- 活跃度加载骨架；0 成员操作空间空态插画（边界 #6）；
- 移动端设置区块纵向堆叠；活跃度直方图横向滚屏 + 表格视图切换；
- 归档横幅 `role="alert"`；直方图附 `aria-label` 数值朗读；确认输入框受控禁用提交。

---

## 4. 技术架构

### 4.1 数据模型

```python
class Workspace(BaseModel):                               # 既有模型加列
    # … 既有字段 …
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey("db.User", on_delete=models.SET_NULL,
                                    null=True, related_name="+")
    default_states = models.JSONField(default=list)       # §2.3 快照

class WorkspaceLabel(BaseModel):
    """全局标签：下发到全部项目（BR-05 并集语义）。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE,
                                  related_name="global_labels")
    name = models.CharField(max_length=50)
    color = models.CharField(max_length=7)                # #RRGGBB
    description = models.CharField(max_length=255, blank=True, default="")
    deleted_at = models.DateTimeField(null=True, blank=True)   # BR-06 软删

    class Meta:
        db_table = "workspace_labels"
        constraints = [
            models.UniqueConstraint(fields=["workspace", "name"], name="uniq_wslabel_ws_name",
                                    condition=Q(deleted_at__isnull=True)),
        ]

class Label(BaseModel):                                   # 既有模型加列
    # … 既有字段 …
    origin = models.CharField(max_length=8, default="local")   # global|local
    overrides_global_id = models.UUIDField(null=True, blank=True)  # 覆盖指向
    name_snapshot = models.CharField(max_length=50, blank=True, default="")  # BR-06
```

**迁移要点**：① `workspaces` 加 3 列（在线 DDL）；② `workspace_labels` 新表；③ `labels` 加 3 列 + 存量回填 `origin='local'`；④ 写保护中间件挂载点：`WorkspaceMiddleware` 在 `workspace_slug` 解析后查 `archived_at` 命中即短路写方法（GET/HEAD/OPTIONS 放行）。

### 4.2 API 定义

#### 4.2.1 归档/恢复 `POST /api/v1/workspaces/{slug}/archive/` `POST …/restore/`

成功 `200`：

```json
{
  "status": "success",
  "data": {"slug": "acme", "archived_at": "2026-09-07T10:02:41.556Z", "affected_projects": 6},
  "meta": {"request_id": "01J9Y08AB2C3D4E5F6G7H8J9K0"}
}
```

非 OWNER `403 PERM_WORKSPACE_OWNER_REQUIRED`；幂等重复归档 `200`（`archived_at` 原值，BR-01）。

#### 4.2.2 归档中写请求（任意写端点）`403`

```json
{
  "status": "error",
  "error": {
    "code": "PERM_WORKSPACE_ARCHIVED",
    "message": "Workspace is archived",
    "detail": {"archived_at": "2026-09-07T10:02:41.556Z", "restore_hint": "Contact the workspace owner to restore"}
  },
  "meta": {"request_id": "01J9Y09CD3E4F5G6H7J8K9L0M1"}
}
```

#### 4.2.3 全局标签 `GET/POST …/workspaces/{slug}/labels/`、`PATCH/DELETE …/{id}/`

列表 `data[]`（≤100，无分页）；删除软删（BR-06），响应含 `affected_issues: 37`（引用计数，前端二次确认文案）。

#### 4.2.4 状态模板 `GET/PUT …/workspaces/{slug}/default-states/`

PUT 全量替换（数组校验：五组各 ≥1、sequence 连续、name 非空 ≤50）；响应回显新快照。

#### 4.2.5 活跃度 `GET …/workspaces/{slug}/activity-stats/?days=30`

```json
{
  "status": "success",
  "data": {
    "active_members_7d": 18,
    "active_members_30d": 21,
    "total_members": 24,
    "contribution_distribution": [
      {"week": "2026-W36", "buckets": {"0": 4, "1-5": 9, "6-20": 6, ">20": 1}}
    ],
    "login_days_histogram": {"1": 2, "2-3": 8, "4-5": 11, "6+": 5},
    "top_actions": {"issue": 0.52, "comment": 0.28, "file": 0.12, "worklog": 0.08}
  },
  "meta": {"request_id": "01J9Y0AEF4G5H6J7K8L9M0N1P2"}
}
```

非 WS_ADMIN `403 PERM_WORKSPACE_ADMIN_REQUIRED`；**Schema 红线**：任何键路径不含 `user_id`（BR-09，UT-05 断言）。

### 4.3 核心逻辑

#### 4.3.1 空间写保护中间件

```python
class WorkspaceArchiveMiddleware:
    """slug 解析后短路一切写方法（BR-02 单入口）。"""
    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
    EXEMPT_PATHS = ("/restore/", "/archive/")              # 恢复通道自身放行

    def __call__(self, request):
        ws = getattr(request, "workspace", None)
        if (ws and ws.archived_at and request.method not in self.SAFE_METHODS
                and not any(p in request.path for p in self.EXEMPT_PATHS)):
            raise WorkspaceArchived(ws)                      # → 403 PERM_WORKSPACE_ARCHIVED
        return self.get_response(request)
```

#### 4.3.2 全局标签合并（项目标签列表序列化点）

```python
def merged_labels(project) -> list[dict]:
    """全局 ∪ 本地；同名本地覆盖显示属性，其余属性随全局更新（BR-05/边界 #3）。"""
    globals_ = {norm(l.name): l for l in project.workspace.global_labels.filter(deleted_at__isnull=True)}
    locals_ = Label.objects.filter(project=project, deleted_at__isnull=True)
    out, covered = [], set()
    for l in locals_:
        g = globals_.get(norm(l.name))
        if g and l.overrides_global_id:                      # 本地覆盖行
            out.append({**serialize(g), "color": l.color or g.color,
                        "description": l.description or g.description, "origin": "override"})
            covered.add(norm(l.name))
        else:
            out.append({**serialize(l), "origin": "local"})
    for key, g in globals_.items():
        if key not in covered and not any(norm(l.name) == key for l in locals_):
            out.append({**serialize(g), "origin": "global"})
    return sorted(out, key=lambda x: x["name"])
```

#### 4.3.3 活跃度聚合（只聚合实现）

```python
def activity_stats(workspace, *, days: int) -> dict:
    since = timezone.now() - timedelta(days=days)
    acts = IssueActivity.objects.filter(workspace=workspace, created_at__gte=since)
    wk = acts.annotate(week=TruncWeek("created_at")).values("week") \
             .annotate(users=Count("actor", distinct=True))   # 周 × 去重成员
    buckets = bucketize(wk, by_week_user_counts(acts))        # 服务器侧分桶，无个人行
    return {"active_members_7d": count_active(workspace, 7),
            "active_members_30d": count_active(workspace, 30),
            "contribution_distribution": buckets,
            "login_days_histogram": login_histogram(workspace, days),   # Session 表日去重
            "top_actions": action_mix(acts)}                  # verb 族占比
```

> 红线落点：`bucketize`/`by_week_user_counts` 在内存聚合后立即丢弃 per-user 中间表；响应构造器类型上无 `user_id` 字段（`TypedDict` 静态守护）。

### 4.4 前端实现

```typescript
// stores/workspace-admin.store.ts
export class WorkspaceAdminStore {
  async archive(nameConfirm: string) {
    if (nameConfirm !== this.root.workspace.name) throw new Error("name mismatch");
    const res = await workspaceService.archive(this.root.workspaceSlug);
    this.root.workspace.setArchived(res.archived_at);   // 全局横幅 + 写操作入口全禁用
    return res;
  }
  get readOnly() { return !!this.root.workspace.archivedAt; }   // 所有编辑器读取此旗标
}
```

| 组件 | 要点 |
| --- | --- |
| `ArchiveBanner` | 全站顶置；OWNER 带恢复按钮、成员带提示文案（§3.2 双视角） |
| `GlobalLabelManager` | 100 上限计数；删除前 `affected_issues` 二次确认 |
| `DefaultStatesEditor` | 五组行编辑（增删/排序/改色），校验同 PROJ-003 模板编辑器复用 |
| `ActivityStatsPage` | 四聚合卡 + 双直方图；页脚隐私说明；窗口切换 7/30/90 |

---

## 5. 测试用例

### 5.1 单元测试

| 编号 | 用例 | 断言 |
| --- | --- | --- |
| UT-01 | 归档写保护 | 归档后 POST/PATCH/PUT/DELETE 全 403 `PERM_WORKSPACE_ARCHIVED`；GET 200 |
| UT-02 | 恢复通道豁免 | 归档中 `restore/` 可调用（EXEMPT_PATHS） |
| UT-03 | 幂等归档/恢复 | 重复请求 200 无重复 Activity/通知（BR-01） |
| UT-04 | 权限分层 | WS_ADMIN 归档 403；OWNER 成功（BR-03） |
| UT-05 | 活跃度 Schema 红线 | 响应 JSON 递归遍历无 `user_id` 键（BR-09） |
| UT-06 | 全局标签并集 | 项目标签列表 = 全局 ∪ 本地，排序稳定 |
| UT-07 | 同名覆盖合并 | 本地改色 + 全局改描述 → 合并输出（边界 #3） |
| UT-08 | 删除覆盖回落 | 删本地覆盖后显示回全局属性（边界 #4） |
| UT-09 | 全局删除快照 | 任务保留 `name_snapshot`；新项目不下发（BR-06） |
| UT-10 | 状态模板校验 | 五组缺一组 PUT 400 `VALIDATION_ERROR`；sequence 断裂 400 `VALIDATION_ERROR` |
| UT-11 | 模板快照语义 | 改全局状态模板后既有项目 State 集不变（BR-07） |
| UT-12 | 标签上限 | 第 101 个全局标签 400 `VALIDATION_ERROR`（BR-11） |
| UT-13 | 归档禁新建项目 | API 403 + 前端按钮禁用（BR-12） |
| UT-14 | 项目恢复守卫联动 | 空间归档中项目 archived→active 被 `workspace_active` 拦截（BR-08） |
| UT-15 | 活跃度窗口 | `days=14` 400 `VALIDATION_INVALID_PARAM`；7/30/90 正常 |
| UT-16 | 周桶聚合 | 构造跨 2 周操作，分布桶计数与手工对账一致 |

### 5.2 集成测试

| 编号 | 用例 | 断言 |
| --- | --- | --- |
| IT-01 | 归档全联动 | 6 项目写全 403、只读全 200、Webhook 停扇出、GitHub 同步暂停、通知全员 |
| IT-02 | 恢复零回写 | 恢复后各项目状态与归档前逐一致（派生语义，BR-04） |
| IT-03 | 全局标签下发 | 新建全局标签自动出现在 3 个项目列表；项目覆盖后其余属性随全局更新 |
| IT-04 | 归档中排程任务 | 在途 Webhook 重试至天然终态；逾期通知不产生（边界 #1/2） |
| IT-05 | 活跃度对账 | 手工 SQL 重算四指标与接口一致；禁用成员不计入（边界 #7） |
| IT-06 | 状态模板生效点 | 空白模板新项目状态集 = 全局快照；带模板项目用模板状态集 |
| IT-07 | API Key 分层 | 归档空间只读端点 200、写端点 403（边界 #8） |
| IT-08 | 新成员限制 | 归档中邀请 403；恢复后邀请成功（边界 #5） |

### 5.3 E2E 测试

| 编号 | 场景 |
| --- | --- |
| E2E-01 | 归档全流程：输入名称确认 → 横幅出现（OWNER/成员双视角）→ 编辑按钮全禁 → 恢复 → 复原 |
| E2E-02 | 全局标签：新建 `security` → 项目 A/B 标签选择器出现 → 项目 A 覆盖改色 → 全局改描述后 A 合并正确 |
| E2E-03 | 状态模板：编辑模板 → 新建空白项目状态集为新模板 → 既有项目看板列不变 |
| E2E-04 | 活跃度页：四卡 + 双直方图渲染；窗口切换数据变化；页脚隐私说明可见 |
| E2E-05 | 归档横幅无障碍：读屏播报 `role="alert"`；键盘可触达恢复按钮 |

---

## 6. 竞品深度对标

### 6.1 Plane 实现分析

| 面 | Plane 现状 | 本系统增量 |
| --- | --- | --- |
| 空间生命周期 | 无归档态（只能留着或删除） | 归档/恢复双向门——客户暂停场景的真实需求（Ones 对位能力） |
| 全局配置 | 无全局标签/状态模板 | `WorkspaceLabel` + `default_states` 原创增量 |
| 活跃度 | 无 | 聚合四指标（且立下隐私红线） |

### 6.2 Ones / Jira 实现分析

| 竞品 | 机制 | 本系统决策 |
| --- | --- | --- |
| Ones 团队归档 | 归档只读 + 恢复 | 语义对齐；补「状态派生而非逐行复制」工程优化（恢复零回写） |
| Ones 活跃度报表 | 含个人操作明细排行 | **刻意不跟**——考勤化报表与协作工具定位冲突；BR-09 红线下仅聚合 |
| Jira scheme | 全局状态/工作流引用式共享，改全局全量生效 | 快照 + 覆盖语义：全局变更不冲击既有项目（可教性与安全兼优） |

### 6.3 本系统设计决策

| 决策 | 理由 |
| --- | --- |
| 状态派生而非复制 | 归档时逐行改 6×N 个项目状态 = 恢复时必须精确回滚（故障面）；派生语义归档=加一行、恢复=删一行 |
| 逐项合并的覆盖语义 | 「全有或全无」覆盖会让本地覆盖后错过全局修正；逐项合并让两层治理各管各的属性 |
| 活跃度无 per-user 维度 | 一旦被用作考勤，成员会用垃圾操作刷指标——指标腐败且信任崩塌；红线写进 Schema 层而非口头约定 |
| OWNER 专属归档 | 空间级生死影响全体——比项目归档（PROJ_ADMIN 即可）提一级到所有者 |
| 快照式状态模板 | 与 `PROJ-003` 模板纪律一致：全局变更是「未来的默认」，不是「对既有的修改」 |

---

## 7. 里程碑与验收

### 7.1 交付物清单

| 类别 | 交付物 |
| --- | --- |
| Model / Migration | `workspaces` +3 列、`workspace_labels` 新表、`labels` +3 列回填 |
| 后端 | 归档/恢复动作 + 写保护中间件、全局标签 CRUD + 合并序列化、状态模板 GET/PUT、活跃度聚合端点 |
| 前端 | 设置四区块页、`ArchiveBanner`（双视角）、`GlobalLabelManager`、`DefaultStatesEditor`、`ActivityStatsPage`、全站只读旗标联动 |
| 测试 | UT-01~16、IT-01~08、E2E-01~05 |
| 错误码 | `PERM_WORKSPACE_ARCHIVED`、`PERM_WORKSPACE_OWNER_REQUIRED` 注册入 `api-conventions.md` §8 |

### 7.2 可操作演示的验收标准

1. 归档：OWNER 输入名称确认 → 全站横幅 → 6 项目写全 403（响应含 `archived_at` 与恢复提示）→ 只读/导出正常 → 恢复后逐项目状态复原（零回写验证）。
2. 权限：WS_ADMIN 归档 403；非成员 404。
3. 全局标签：新建下发全项目；同名覆盖仅覆盖显示属性、其余属性随全局更新；删除全局后任务保留名称快照。
4. 状态模板：编辑后新建项目生效、既有项目不变（快照验证）。
5. 活跃度：四指标与手工 SQL 对账一致；响应递归遍历无 `user_id`；非 WS_ADMIN 403。
6. 回归：`PROJ-002/003` 守卫链（含 `workspace_active`）、`INTG-001/002` 暂停语义、`TEAM-002` 成员管理全部无回归；标准版 V1.0 功能冻结清单全绿。

---
