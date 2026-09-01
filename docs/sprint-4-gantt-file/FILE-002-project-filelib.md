# 项目文件库与多层级目录

| 元信息项 | 内容 |
| --- | --- |
| 文档编号 | FILE-002 |
| 所属迭代 | Sprint 4 — 甘特图 + 文件管理（第 6 周） |
| 优先级 | P2（标准版完整级 · **文件体系的骨架**） |
| 所属模块 | M7-FILE｜文件资源管理 |
| 文档状态 | 待评审（Draft） |
| 最后更新日期 | 2026-09-01 |
| 上游依赖 | `FILE-001`（预签名直传三步、`FileAsset` 模型、类型白名单、孤儿清理）、`INFRA-002`（MinIO 对象存储与桶策略）、`PROJ-002`（成员与角色）、`AUTH-005`（权限门控） |
| 下游消费 | **`FILE-003`（分片续传 / 预览 / 多版本——直接扩展本骨架）**、`FILE-004`（分享与权限）、`FILE-005`（P3 Wiki）、`INTG-001`（GitHub 附件挂接）、`COLLAB-001`（评论图片引用） |
| 上游依据 | `docs/需求文档.md` §3.7（项目文件夹创建、多层级目录管理、文件下载重命名移动删除、文件权限管控）、§8.2 文件管理 P2 列 |
| 关联架构文档 | [`api-conventions.md`](../architecture/api-conventions.md)（**§13.2 预签名直传规范**、§8 错误码、§10.5 事务纪律）、[`monorepo-structure.md`](../architecture/monorepo-structure.md)（MinIO 服务）、[`rbac-permission-model.md`](../architecture/rbac-permission-model.md)（`file.*` 权限码） |
| 对标基线 | Plane（**无项目级文件库——仅 Issue 附件**，本系统差异化） · Ones 项目文件模块（目录/权限/版本） |
| 工作量估算 | 后端 3 人日 / 前端 3 人日 / 联调与测试 1 人日，合计 **7 人日** |

---

## 1. 概述

### 1.1 功能定位

P1 的文件能力止步于「任务附件」：文件挂在单个任务下，没有目录、没有项目级沉淀。本迭代把文件升级为**项目资产**：

- **多层级目录**（`FileFolder` 树）——按「需求文档 / 设计稿 / 会议纪要 / 合同」组织，深度 ≤ 5（复用层级治理经验）；
- **项目文件库页**——树形导航 + 列表/网格双视图 + 全部文件操作（上传 / 下载 / 重命名 / 移动 / 删除 / 恢复）；
- **三态可见性权限**——全员可见 / 仅项目管理员 / 指定成员，在 UI、API、预签名三层一致校验；
- **工作空间存储配额**——默认 10GB，超配额拒绝上传（可配置）。

工程主线延续 `FILE-001` 的铁律：**Django 永不接触文件字节流**——一切上传下载走 MinIO 预签名，Django 只管理元数据与权限。

### 1.2 关键约定：`FileAsset` 的双重身份

> ⚠️ P1 的 `FileAsset`（任务附件）与 P2 的文件库**共用一张表**，靠两个字段区分身份：

| 身份 | 判定 | 上传入口 | 归属 |
| --- | --- | --- | --- |
| 任务附件 | `issue` 非空 / `folder` 为空 | 任务详情、评论插图 | `FILE-001` 契约不变 |
| 文件库文件 | `folder` 非空 | 文件库页拖拽上传 | 本文档 |
| 双挂 | `issue` 与 `folder` 均非空 | 文件库文件「附加到任务」 | 合法（一份对象两个视图入口，元数据单行） |

「双挂」是刻意能力：把文件库的设计稿挂到某个需求任务下，对象存储只有一份，删除任务附件只是解除 `issue` 关联而非删对象——**删除语义按「引用计数」执行**（§2.4 BR-06）。

### 1.3 交付内容

| # | 能力 | 说明 |
| --- | --- | --- |
| 1 | 目录树 | `FileFolder`（项目级、自引用、深度 ≤5、同名同层拒绝）；新建/改名/移动/删除/恢复 |
| 2 | 文件操作 | 上传（复用预签名三步 + 落库到目录）、下载（预签名 GET，5 分钟有效）、重命名、移动（改 folder）、删除（软删进回收站）、恢复 |
| 3 | 文件库页 | 左树右表双视图（列表/网格切换）、面包屑、拖拽上传、拖拽移动、右键/⋯菜单 |
| 4 | 三态可见性 | `visibility ∈ {all, admins, members}` + `allowed_members` 列表；三层校验 |
| 5 | 存储配额 | 工作空间级 `storage_quota_bytes`（默认 10GB）；上传前校验余量 |
| 6 | 检索 | 目录内按名称过滤（trgm 模糊）+ 类型/上传人/时间筛选；全库搜索归 P3 |

### 1.4 范围边界

| 能力 | 本文档（P2） | 归属 |
| --- | --- | --- |
| 目录树 / 文件 CRUD / 回收站 | ✅ | — |
| 三态可见性 + 指定成员 | ✅ | — |
| 存储配额 | ✅ | — |
| 分片续传（>50MB） | ❌（`upload_session` 位预留） | `FILE-003` |
| 在线预览 / 缩略图 | ❌ | `FILE-003` |
| 多版本 / 回滚 / 版本对比 | ❌（`current_version` 列预建） | `FILE-003` |
| 外部分享链接（密码/有效期） | ❌ | `FILE-004` |
| 全库全文搜索 | ❌ | P3 |
| 文件级评论 / @ | ❌（讨论用任务评论承载） | — |
| 水印 / 禁转 / 合规留存 | ❌ | P4 `FILE-006` |
| Wiki | ❌ | P3 `FILE-005` |

### 1.5 前置依赖

| 依赖 | 内容 | 阻塞原因 |
| --- | --- | --- |
| `FILE-001` | `FileAsset` 模型、presign/complete 端点、类型白名单、孤儿清理 beat | 上传链路复用；本迭代扩展模型 |
| `INFRA-002` | MinIO 桶策略（`projects/{id}/` 前缀）、生命周期规则 | 对象键位规划 |
| `PROJ-002` | 成员与角色（admins/members 候选） | 可见性指定成员校验 |
| `TASK-010` | Activity 管道 | 文件操作留痕（项目动态素材，`COLLAB-003` 消费） |

### 1.6 竞品参考

| 竞品 | 参考点 | 处置 |
| --- | --- | --- |
| Plane | **无项目文件库**——Issue 附件即全部 | 本系统文件库是相对 Plane 开源版的差异化能力 |
| Plane | 附件元数据内联 `attributes` JSONB | `FileAsset.attributes` 延续（name/size/type） |
| Ones | 项目文件模块：目录、权限、版本、预览 | P2 目录+权限；版本/预览 `FILE-003` |
| Confluence | 树形空间 + 权限继承 | 目录可见性**不继承**（P2 每目录独立声明）；P3 视需要加继承 |

---

## 2. 业务逻辑

### 2.1 上传落库流程（复用 + 扩展）

```mermaid
sequenceDiagram
    autonumber
    participant U as 用户
    participant FE as 文件库页
    participant API as Django API
    participant S3 as MinIO
    participant CW as Celery

    U->>FE: 拖拽 3 个文件到「设计稿」目录
    FE->>API: POST …/folders/{id}/files/presign/ ×3（并行）
    API->>API: 校验：类型白名单 / 单文件 ≤50MB（直传上限）/<br/>配额余量（含在途预留）/ 目录写权限
    API-->>FE: 3 × {upload_url, fields, asset_id, expires_at}
    FE->>S3: PUT 对象 ×3（直传，Django 不过字节流）
    FE->>API: POST …/files/{asset_id}/complete/ ×3
    API->>S3: HEAD 校验对象存在且大小匹配
    API->>API: FileAsset 状态 active；on_commit → 项目动态事件
    API-->>FE: 201 文件元数据；列表乐观插入
    Note over CW: 30 分钟未 complete 的 presign 记录<br/>由 beat 回收（FILE-001 既有任务）
```

### 2.2 移动 / 重命名 / 删除 / 恢复

```mermaid
flowchart TD
    A["文件/目录 ⋯ 菜单"] --> B{"操作"}
    B -->|重命名| C["PATCH name（目录同层同名拒绝 409）"]
    B -->|移动| D["目标选择器（目录树，禁选自身及后代）"]
    D --> E{"目标合法？<br/>深度 ≤5 / 非自身后代 / 目录无同名"}
    E -->|否| E1["400/409 对应错误码"]
    E -->|是| F["PATCH folder_id / parent_id<br/>（纯元数据操作，对象键不动）"]
    B -->|删除| G["回收站确认弹层<br/>（目录删除提示将删除 N 个文件）"]
    G --> H["软删：deleted_at 置位<br/>（目录连带整棵子树软删）"]
    H --> I["对象存储不动（引用计数语义）"]
    B -->|恢复| J["回收站 → 还原到原位<br/>（原位被占则进根目录并加 (恢复) 后缀）"]
```

### 2.3 可见性判定（三层一致）

```mermaid
flowchart LR
    A["请求访问文件/目录"] --> B{"API 层：ViewSet<br/>visibility 判定（can_view_file）"}
    B -->|all| C["项目成员可见"]
    B -->|admins| D["仅 PROJ_ADMIN / WS_ADMIN+"]
    B -->|members| E["admin + allowed_members 列表内"]
    C --> F["预签名层：GET/PUT 签发前<br/>二次执行同一判定函数"]
    D --> F
    E --> F
    F --> G["UI 层：不可见文件不出现在列表<br/>（列表查询即过滤）"]
```

> 预签名 URL 有效期仅 5 分钟，且**签发时**必须完成与列表同源的权限判定——「先拿链接再被移出权限」的窗口被 5 分钟时效 + 签发时校验双重收窄（BR-09）。

### 2.4 业务规则汇总

| 编号 | 规则 | 判定位置 | 违反后果 |
| --- | --- | --- | --- |
| BR-01 | 目录深度 ≤ 5（根=1）；目录名 1~64 字符，同层同名拒绝（偏条件唯一约束） | Serializer + DB | `409 RESOURCE_ALREADY_EXISTS` |
| BR-02 | 文件展示名 1~255 字符；同目录同名**允许共存**（对象键含 UUID，不冲突）——重名由展示区分（`FILE-003` 上线同名新版本策略后收紧） | Serializer | — |
| BR-03 | 上传校验链：类型白名单（沿用 `FILE-001`）→ 单文件 ≤ 50MB（直传上限；更大走 `FILE-003` 分片）→ 配额余量（含在途 presign 预留） | Service | `400 VALIDATION_FILE_*` / `409 QUOTA_STORAGE_EXCEEDED` |
| BR-04 | 目录移动禁选自身与后代（环防护，复用 `TASK-004` CTE 判定范式）；移动不改对象键 | Service | `409 RESOURCE_CIRCULAR_DEPENDENCY` |
| BR-05 | 目录删除 = 整棵子树软删（含文件）；需空目录或二次确认显示文件数 | Service | — |
| BR-06 | **删除按引用计数**：软删时对象不删；仅当「无任何存活引用（issue 挂接 + 文件库行 + 版本历史）」时，由 beat 在 30 天回收站期满后删除对象 | Celery | — |
| BR-07 | 恢复：原位存在同名目录时进根目录并追加 `(恢复)` 后缀；目录整树恢复 | Service | — |
| BR-08 | 可见性三态在 列表查询 / 端点详情 / 预签名签发 三处执行**同一判定函数** `can_view_file(user, obj)`（单入口） | Service | 评审拒绝 |
| BR-09 | 预签名 GET 有效期 5 分钟、PUT 30 分钟（FILE-001 既有）；签发时实时权限校验 | Service | — |
| BR-10 | 下载计数异步累加（Redis 计数 → beat 批量落库，避免热点行） | Celery | — |
| BR-11 | 配额：`storage_quota_bytes` 存 WS 设置默认 10GB；已用 = Σ active 对象 size；达到 95% 告警通知 WS Admin | Service + beat | — |
| BR-12 | 文件操作留痕：上传/重命名/移动/删除/恢复产生项目动态事件（`COLLAB-003` 管道；不入 `IssueActivity`——非任务域） | on_commit | — |
| BR-13 | 权限：读=`file.read`（VIEWER+，受可见性过滤）；写=`file.upload`（CONTRIBUTOR+）；删除=`file.delete`（ADMIN 或上传者本人）；可见性配置=`file.permission.manage`（ADMIN） | Permission | `403` |
| BR-14 | 项目归档后文件库只读（浏览下载可，写操作 403） | Permission | `403 PERM_PROJECT_ARCHIVED` |

### 2.5 异常处理

| 场景 | HTTP | 错误码 | details 子码 | 前端表现 |
| --- | --- | --- | --- | --- |
| 目录同层同名 | 409 | `RESOURCE_ALREADY_EXISTS` | `UNIQUE` | 输入框行内红 |
| 目录深度超限 | 409 | `RESOURCE_LIMIT_EXCEEDED` | `LIMIT` | 「目录最多 5 层」 |
| 目录移动成环 | 409 | `RESOURCE_CIRCULAR_DEPENDENCY` | `CYCLE` | 目标树禁选自身后代 |
| 文件类型不允许 | 400 | `VALIDATION_FILE_TYPE_NOT_ALLOWED` | — | 拖拽拒绝 + 列出白名单 |
| 单文件超 50MB | 400 | `VALIDATION_FILE_SIZE_EXCEEDED` | — | 「超大文件请等待分片上传支持」（P2 文案） |
| 配额耗尽 | 409 | `QUOTA_STORAGE_EXCEEDED` | — | 弹层显示用量/配额 + 清理建议 |
| 预签名过期使用 | 403 | `PERM_DENIED`（MinIO 侧） | — | 自动重申 presign 重试一次 |
| 可见性不足 | 404 | `RESOURCE_NOT_FOUND` | — | 列表本就不可见；直连 404（存在性隐藏） |
| 恢复冲突 | 200 | —（自动改名） | — | 落根 + `(恢复)` 后缀提示 |
| complete 大小不匹配 | 400 | `VALIDATION_ERROR` | `INVALID` | 上传失败重试 |

### 2.6 边界条件

| 边界场景 | 限制值 | 超出处理 |
| --- | --- | --- |
| 单文件（直传） | 50MB | 400（分片归 FILE-003） |
| 单目录文件数 | 无硬限（分页 50） | — |
| 目录深度 | 5 | 409 |
| 目录名 / 文件名 | 64 / 255 | 400 |
| 配额 | 10GB/WS（可配） | 409 + 95% 预警 |
| 回收站保留 | 30 天 | 期满对象清理 |
| 预签名在途预留 | 计入配额判定 | 防并发穿透 |
| 批量上传 | 一次 ≤ 20 文件 | 前端分批 |

---

## 3. UI/UX 设计

### 3.1 文件库页（项目导航「文件」入口）

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ 📁 文件     [🔍 名称过滤] [类型▾] [上传人▾] [时间▾]  [⟳列表|▦网格]  [＋上传] │
├──────────────────┬───────────────────────────────────────────────────────────┤
│ 📂 项目文件       │  📂 项目文件 / 📂 设计稿 / 📂 2026Q3            12 个文件 │
│ ├ 📂 需求文档    │ ┌────┬──────────────────┬──────┬──────┬────────┬────────┐│
│ ├ 📂 设计稿 ▌   │ │类型│ 名称              │大小  │上传人│修改时间 │ 操作    ││
│ │  ├ 2026Q3     │ ├────┼──────────────────┼──────┼──────┼────────┼────────┤│
│ │  └ 归档       │ │ 🖼 │ 首页改版-v3.fig  │ 8.2MB│ 张三 │ 09-01  │  ⋯      ││
│ ├ 📂 会议纪要    │ │ 📄 │ 需求评审纪要.docx│ 340KB│ 李四 │ 08-30  │  ⋯      ││
│ ├ 📂 合同       │ │ 🎬 │ 方案演示.mp4     │ 46MB │ 张三 │ 08-28  │  ⋯      ││
│ └ 📂 归档       │ │ 📦 │ 资产打包.zip     │ 12MB │ 王五 │ 08-25  │  ⋯      ││
│                  │ └────┴──────────────────┴──────┴──────┴────────┴────────┘│
│ [＋新建目录]      │            ⤓ 拖拽文件到此处上传到「2026Q3」                │
│ [🗑 回收站 (3)]  │                                                           │
└──────────────────┴───────────────────────────────────────────────────────────┘
```

| 区域 | 组件 | 规格 |
| --- | --- | --- |
| 左树 | 目录树（缩进 16px/层，当前高亮 `▌`），悬浮 ⋯（改名/移动/删除/可见性） | 深度 ≤5 |
| 面包屑 | 路径可点击逐级返回 | — |
| 工具条 | 名称过滤（防抖 300ms）+ 类型/上传人/时间筛选 + 视图切换 + 上传 | — |
| 列表视图 | 表列：类型图标 / 名称 / 大小（人性化）/ 上传人 / 修改时间 / ⋯ | 行 hover 浅底 |
| 网格视图 | 卡片 120×140：类型大图标（缩略位 `FILE-003`）+ 名称两行 + 大小 | — |
| 拖拽上传 | 右区整体 dropzone；拖入蓝色虚线框 + 「松开上传到 {目录}」 | 多文件并行 |
| 拖拽移动 | 文件/目录拖到左树目标（高亮落点） | 环/深度前端预判 |
| 回收站 | 左树底部入口：已删列表 + 还原 / 彻底删除（ADMIN） | 计数徽标 |

### 3.2 上传进度与状态

| 状态 | 表现 |
| --- | --- |
| 在途 | 列表底部浮层：每文件进度条 + 速度 + 取消；完成即插入列表 |
| 失败 | 浮层行红 + 重试（自动重申 presign） |
| 取消 | 移除浮层行；已传对象成孤儿（30 分钟回收） |
| 配额将满 | 上传前预检弹层：用量/配额 + 「仍要上传」/「取消」 |

### 3.3 ⋯ 菜单与操作弹层

| 操作 | 形态 | 要点 |
| --- | --- | --- |
| 下载 | 预签名 GET 触发另存 | 多选打包下载 P4 |
| 重命名 | 行内编辑 | 同层同名即时校验 |
| 移动 | 目录选择弹层（树 + 新建快捷） | 禁选自身后代；显示目标路径 |
| 附加到任务 | 任务搜索弹层 | 建立 `issue` 双挂（§1.2） |
| 可见性 | 三态单选 + 成员多选（members 态） | 仅 ADMIN 可见此项 |
| 删除 | 确认弹层（目录显示 N 文件） | 回收站 30 天提示 |
| 还原（回收站） | 原位/根目录冲突说明 | — |

### 3.4 空状态 / 加载 / 失败

| 场景 | 处置 |
| --- | --- |
| 空目录 | 「拖拽文件到此处，或 [＋上传]」插画 |
| 空文件库 | 首次引导卡：三个示例目录模板（需求文档/设计稿/会议纪要）一键创建 |
| 过滤无结果 | 「未找到匹配文件」+ 清除筛选 |
| 树加载 | 3 级骨架 |
| 下载失败 | 自动重申一次预签名，再失败 Toast |

### 3.5 响应式与无障碍

| 断点 | 布局 |
| --- | --- |
| ≥ 1280px | 左树 260px + 右列表 |
| 768~1279px | 左树收起为目录下拉 |
| < 768px | 单列列表 + 底部上传按钮；拖拽禁用改点选 |

无障碍：树 `role="tree"`；列表语义 `<table>`；⋯ 菜单键盘可达；上传浮层 `role="status" aria-live="polite"` 播报进度里程碑（25/50/75/100%）；删除确认 `role="alertdialog"`；大小与类型图标冗余文本。

---

## 4. 技术架构

### 4.1 数据模型

#### 4.1.1 `FileFolder`（新表）与 `FileAsset`（扩展）

```python
# apps/api/plane/db/models/file.py
class FileFolder(BaseModel):
    """项目文件目录 —— 自引用树，深度 ≤ 5，可见性独立声明（不继承）"""

    class Visibility(models.TextChoices):
        ALL = "all", "全员可见"
        ADMINS = "admins", "仅项目管理员"
        MEMBERS = "members", "指定成员"

    project = models.ForeignKey("db.Project", on_delete=models.CASCADE,
                                related_name="file_folders", verbose_name="所属项目")
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True,
                               related_name="children", verbose_name="父目录")
    name = models.CharField(max_length=64, verbose_name="目录名")
    visibility = models.CharField(max_length=16, choices=Visibility.choices,
                                  default=Visibility.ALL, verbose_name="可见性")
    allowed_members = models.JSONField(default=list, blank=True,
                                       verbose_name="指定可见成员（UUID 列表，members 态生效）")

    class Meta(BaseModel.Meta):
        db_table = "file_folders"
        constraints = [
            models.UniqueConstraint(
                fields=["parent", "name"],
                condition=models.Q(deleted_at__isnull=True),
                name="uniq_folder_name_per_parent"),
        ]
        indexes = [
            models.Index(fields=["project", "parent"], name="idx_folder_project_parent"),
        ]


class FileAsset(BaseModel):                                # FILE-001 既有，本迭代扩展
    """文件资产 —— 对象存储元数据行（任务附件 / 文件库文件双身份）"""

    project = models.ForeignKey("db.Project", on_delete=models.CASCADE,
                                related_name="file_assets", verbose_name="所属项目")
    issue = models.ForeignKey("db.Issue", on_delete=models.SET_NULL, null=True,
                              blank=True, related_name="attachments",
                              verbose_name="挂接任务（附件身份，可空）")
    folder = models.ForeignKey(FileFolder, on_delete=models.SET_NULL, null=True,
                               blank=True, related_name="files",
                               verbose_name="所属目录（文件库身份，可空）")
    # FILE-001 既有：asset(S3 key) / attributes(JSONB) / uploaded_by /
    #               status(pending|active|orphaned)
    # 本迭代新增：
    visibility = models.CharField(max_length=16, choices=FileFolder.Visibility.choices,
                                  default=FileFolder.Visibility.ALL,
                                  verbose_name="可见性（默认随目录，可独立收紧）")
    allowed_members = models.JSONField(default=list, blank=True)
    download_count = models.PositiveIntegerField(default=0, verbose_name="下载次数")
    # FILE-003 预留（本次迁移一并建列，后续零 DDL）：
    #   current_version = FK("db.FileVersion", null=True)
    #   upload_session  = OneToOneField("db.UploadSession", null=True)

    class Meta(BaseModel.Meta):
        db_table = "file_assets"
        indexes = [
            models.Index(fields=["project", "folder"], name="idx_asset_project_folder"),
            models.Index(fields=["issue"], name="idx_asset_issue"),
            models.Index(fields=["project", "status"], name="idx_asset_project_status"),
        ]
```

```mermaid
erDiagram
    Project ||--o{ FileFolder : "file_folders（树，深度≤5）"
    FileFolder ||--o{ FileFolder : "parent/children"
    FileFolder ||--o{ FileAsset : "files（文件库身份）"
    Issue ||--o{ FileAsset : "attachments（附件身份，双挂合法）"
    Project ||--o{ FileAsset : "file_assets"
    FileFolder {
        uuid project_id FK
        uuid parent_id FK "self CASCADE"
        string name "64 uk(parent,name)"
        string visibility "all|admins|members"
        jsonb allowed_members
    }
    FileAsset {
        uuid project_id FK
        uuid issue_id FK "nullable"
        uuid folder_id FK "nullable"
        string asset "S3 key（含 UUID，移动不变）"
        jsonb attributes "name/size/content_type"
        string status "pending|active|orphaned"
        string visibility "可独立于目录收紧"
        int download_count "异步累加"
        uuid current_version "FILE-003 预留"
    }
```

#### 4.1.2 对象键位规划（MinIO）

```
文件库：projects/{project_id}/folders/{folder_id}/{asset_uuid}/{filename}
任务附件（FILE-001 既有）：projects/{project_id}/issues/{issue_id}/{asset_uuid}/{filename}
说明：目录 id 入键仅助排障；移动目录不改键（寻址走元数据，键含 UUID 保证唯一）
回收站：不移动键位（软删仅元数据）；30 天期满清理按引用计数删对象
```

#### 4.1.3 迁移

```python
# 00XX_p2_file_library.py
operations = [
    migrations.CreateModel(...),                          # FileFolder
    migrations.AddField(model_name="fileasset", name="folder", ...),
    migrations.AddField(model_name="fileasset", name="visibility", ...),
    migrations.AddField(model_name="fileasset", name="allowed_members", ...),
    migrations.AddField(model_name="fileasset", name="download_count", ...),
    # current_version / upload_session 预留列一并建齐（FILE-003 零 DDL）
]
```

### 4.2 API 定义

| # | 方法 | 路径 | 描述 | 权限 | 成功码 |
| --- | --- | --- | --- | --- | --- |
| 1 | `GET` | `…/projects/{project_id}/folders/` | 目录树（全量） | `file.read` | `200` |
| 2 | `POST` | `…/projects/{project_id}/folders/` | 新建目录 | `file.upload` | `201` |
| 3 | `PATCH` | `…/folders/{folder_id}/` | 改名/移动/可见性 | 写=CONTRIBUTOR+；可见性=`file.permission.manage` | `200` |
| 4 | `DELETE` | `…/folders/{folder_id}/` | 删除（整树软删，回传文件数） | `file.delete` | `200` |
| 5 | `GET` | `…/folders/{folder_id}/files/` | 目录文件列表（游标+筛选） | `file.read`（可见性过滤） | `200` |
| 6 | `POST` | `…/folders/{folder_id}/files/presign/` | 上传预签名（三步复用） | `file.upload` | `201` |
| 7 | `GET` | `…/files/{asset_id}/download-url/` | 下载预签名（5 分钟） | `file.read`（实时校验） | `200` |
| 8 | `PATCH` | `…/files/{asset_id}/` | 重命名/移动/可见性/附加任务 | `file.upload` | `200` |
| 9 | `DELETE` | `…/files/{asset_id}/` | 删除（软删） | `file.delete`（ADMIN 或上传者） | `204` |
| 10 | `POST` | `…/files/{asset_id}/restore/` | 回收站还原 | `file.delete` | `200` |
| 11 | `GET` | `…/projects/{project_id}/files/trash/` | 回收站列表 | `file.read`（ADMIN+） | `200` |
| 12 | `GET` | `…/projects/{project_id}/files/storage/` | 配额用量 | `file.read` | `200` |

#### 4.2.1 `GET …/folders/{folder_id}/files/` — 目录文件列表

**请求**

```http
GET /api/v1/workspaces/acme/projects/7b3e9c1a-…/folders/9a1b2c3d-…/files/?name=设计&order_by=-created_at&per_page=50 HTTP/1.1
```

**成功响应 `200`**

```json
{
  "status": "success",
  "data": [
    {
      "id": "c1d2e3f4-5a6b-4c7d-8e9f-0a1b2c3d4e5f",
      "name": "首页改版-v3.fig",
      "size_bytes": 8388608, "content_type": "application/octet-stream",
      "type_category": "image",
      "visibility": "all",
      "folder_id": "9a1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d",
      "issue_id": null,
      "uploaded_by": { "id": "6c7d1a2b-3e4f-4a5b-9c8d-7e6f5a4b3c2d",
                       "display_name": "张三" },
      "download_count": 12,
      "created_at": "2026-09-01T02:14:33.001Z",
      "updated_at": "2026-09-01T02:14:33.001Z"
    }
  ],
  "meta": { "next_cursor": "50:1:0", "next_page_results": false, "count": 1,
            "total_count": 1, "total_size_bytes": 8388608,
            "page": 1, "per_page": 50 }
}
```

> `meta.total_size_bytes` 支撑目录容量展示；`type_category` 由 content_type 映射（image/document/video/archive/other），图标与筛选复用。

**失败响应 `404`（可见性不足，存在性隐藏）**

```json
{
  "status": "error",
  "error": {
    "code": "RESOURCE_NOT_FOUND",
    "message": "目录不存在或你没有访问权限",
    "request_id": "01JCBC2B5ZD4W7X3D1E5F6G8H9"
  }
}
```

#### 4.2.2 `POST …/folders/{folder_id}/files/presign/` — 上传预签名

**请求**

```json
{ "file_name": "首页改版-v3.fig", "file_size": 8388608,
  "content_type": "application/octet-stream" }
```

**失败响应 `409`（配额）**

```json
{
  "status": "error",
  "error": {
    "code": "QUOTA_STORAGE_EXCEEDED",
    "message": "工作空间存储空间不足",
    "details": [{ "field": "file_size", "code": "QUOTA",
                  "message": "已用 9.8GB / 10GB，本次需 8MB" }],
    "request_id": "01JCBC2B5ZD4W7X3D1E5F6G8H0"
  }
}
```

**成功响应 `201`**：同 `FILE-001` presign 结构（`upload_url/fields/asset_id/expires_at`），`asset_id` 已绑定 `folder`。

#### 4.2.3 `GET …/files/storage/` — 配额用量

```json
{
  "status": "success",
  "data": { "quota_bytes": 10737418240, "used_bytes": 10522669875,
            "pending_bytes": 20971520, "usage_ratio": 0.98 }
}
```

### 4.3 核心逻辑

#### 4.3.1 可见性单入口（三层一致的本体）

```python
# apps/api/plane/db/services/file_permission.py
def can_view_file(user, asset_or_folder) -> bool:
    """BR-08：列表 / 详情 / 预签名 三处共用的唯一判定。"""
    v = asset_or_folder.visibility
    if v == "all":
        return has_project_role(user, asset_or_folder.project_id, min_role=5)
    if v == "admins":
        return (has_project_role(user, asset_or_folder.project_id, min_role=20)
                or has_ws_role(user, ws_id_of(asset_or_folder), min_role=15))  # WS_ADMIN 隐式
    return (has_project_role(user, asset_or_folder.project_id, min_role=20)
            or str(user.id) in set(asset_or_folder.allowed_members))


def presign_download(user, asset: FileAsset) -> str:
    if not can_view_file(user, asset):                       # 签发时实时校验（BR-09）
        raise NotFound()                                     # 存在性隐藏 → 404
    _incr_download_count_async(asset.id)                     # BR-10
    return s3_client.generate_presigned_url(
        "get_object", Params={"Bucket": BUCKET, "Key": asset.asset},
        ExpiresIn=300)                                       # 5 分钟
```

#### 4.3.2 目录环与深度校验（复用 `TASK-004` 范式）

```python
@transaction.atomic
def move_folder(*, folder_id: uuid.UUID, new_parent_id: uuid.UUID | None) -> FileFolder:
    """移动目录：环防护 + 深度预算（整棵子树高度），纯元数据操作。"""
    folder = FileFolder.objects.select_for_update().get(
        id=folder_id, deleted_at__isnull=True)
    if new_parent_id:
        parent = FileFolder.objects.get(id=new_parent_id, deleted_at__isnull=True)
        if parent.project_id != folder.project_id:
            raise ValidationError({"parent_id": "跨项目移动不允许"})
        if _folder_is_descendant(parent, folder):            # BR-04 CTE 上行
            raise CircularDependencyError()
        if _folder_depth(parent) + 1 + _subtree_height(folder) > 5:
            raise LimitExceeded(limit=5)
    folder.parent_id = new_parent_id
    folder.save(update_fields=["parent", "updated_at"])
    return folder                                            # 对象键不动
```

#### 4.3.3 引用计数删除（对象生命周期）

```python
# apps/api/plane/bgtasks/file_gc.py
@shared_task
def sweep_expired_trash() -> int:
    """回收站 30 天期满：元数据硬删 + 无引用对象删除（BR-06）。

    对象删除条件（同一键可能被多行引用——双挂/版本历史）：
      NOT EXISTS(存活 file_assets) AND NOT EXISTS(存活 file_versions 指向)
    """
    expired = FileAsset.all_objects.filter(
        deleted_at__lt=timezone.now() - timedelta(days=30))
    for asset in expired.iterator():
        if not _has_live_references(asset.asset):            # 键级引用计数
            s3_client.delete_object(Bucket=BUCKET, Key=asset.asset)
        asset.delete()                                       # 元数据硬删
    ...
```

#### 4.3.4 配额判定（含在途预留）

```python
def assert_quota(*, workspace_id: uuid.UUID, incoming: int) -> None:
    quota = get_workspace_quota(workspace_id)                # 默认 10GB
    used = FileAsset.objects.filter(
        project__workspace_id=workspace_id, status="active",
        deleted_at__isnull=True).aggregate(s=Sum("attributes__size"))["s"] or 0
    pending = FileAsset.objects.filter(
        project__workspace_id=workspace_id, status="pending"
    ).aggregate(s=Sum("attributes__size"))["s"] or 0
    if used + pending + incoming > quota:                    # 在途计入（BR-03）
        raise QuotaExceeded(used=used, quota=quota, incoming=incoming)
```

### 4.4 前端实现

- `FileLibraryStore`（`packages/shared-state`）：`folderTree`（SWR `project:{id}:folders`）、`filesByFolder:{cursor}`、`trash`、`storage`；上传队列（并行 3；断点续传 `FILE-003`）。
- 组件：`FolderTree`（复用 `TASK-004` 树交互）、`FileTable`/`FileGrid` 双视图、`UploadDropzone`（整页 dropzone + 进度浮层）、`MoveTargetPicker`（环预判）、`VisibilityEditor`（三态 + 成员多选）。
- 下载：点击 → `GET download-url` → `window.open`；403 过期自动重申一次。
- 配额条：侧栏底部进度条（≥95% 红）。

---

## 5. 测试用例

### 5.1 单元测试

| 用例 ID | 测试目标 | 输入 | 预期输出 | 覆盖类型 |
| --- | --- | --- | --- | --- |
| UT-01 | 目录同名 | 同层建同名 | 409 UNIQUE | 异常 |
| UT-02 | 深度上限 | 第 6 层 | 409 LIMIT | 边界 |
| UT-03 | 移动成环 | 父移到子下 | 409 CYCLE | 异常 |
| UT-04 | 跨项目移动 | 目标他项目 | 400 | 安全 |
| UT-05 | 同名文件共存 | 同目录两名 | 均成功（键含 UUID） | 边界 |
| UT-06 | 可见性 all | VIEWER 浏览 | 可见 | 正常 |
| UT-07 | 可见性 admins | CONTRIBUTOR | 列表不可见；直连 404 | 安全 |
| UT-08 | 可见性 members | 指定本人 | 可见；他人 404 | 安全 |
| UT-09 | 预签名实时校验 | 拿链接后被移出 | 新签发 404 | 安全 |
| UT-10 | 配额在途预留 | 临界 + 并发两笔 | 恰一笔成功 | 并发 |
| UT-11 | 双挂删除语义 | 删任务附件关系 | 文件库行与对象存活 | 正常 |
| UT-12 | 回收站期满 | 30 天后 beat | 无引用对象删除；有引用保留 | 正常 |
| UT-13 | 恢复冲突 | 原位同名 | 落根 + `(恢复)` | 边界 |
| UT-14 | 目录级联软删 | 删含 12 文件目录 | 整树软删，回传 12 | 正常 |
| UT-15 | 50MB 上限 | 51MB | 400 SIZE_EXCEEDED | 边界 |
| UT-16 | 项目归档只读 | 归档后上传 | 403 | 安全 |

### 5.2 集成测试

| 用例 ID | 场景 | 前置条件 | 操作步骤 | 预期结果 |
| --- | --- | --- | --- | --- |
| IT-01 | 上传全链路 | MinIO 就绪 | presign→PUT→complete | active；对象落位；无 Django 字节流日志 |
| IT-02 | complete 校验 | — | PUT 半量后 complete | 400（HEAD 大小不匹配） |
| IT-03 | 孤儿回收 | presign 不 complete | 加速时钟 | 对象删除，行 orphaned |
| IT-04 | 移动零对象操作 | 含 1GB 文件目录 | 计时 + S3 列表 | 毫秒级；键不变 |
| IT-05 | 可见性三层一致 | admins 态 | 列表/详情/预签名三路 | 全部不可见/404 |
| IT-06 | 配额 95% 预警 | 灌至 95% | beat 扫描 | WS Admin 收通知 |
| IT-07 | 万文件浏览 | 万级单目录 | 翻页 | P95 < 300ms |
| IT-08 | 动态留痕 | 上传/移动/删除 | 项目动态 | 三类事件齐全 |

### 5.3 E2E 测试

| 用例 ID | 用户场景 | 操作路径 | 验收标准 |
| --- | --- | --- | --- |
| E2E-01 | 首次建库 | 模板三目录 → 拖 3 文件 | 树/列表/进度浮层正确；刷新保持 |
| E2E-02 | 目录管理 | 建 4 层 → 改名 → 拖动移动 | 面包屑与树同步；环被禁选 |
| E2E-03 | 权限体验 | admins 文件以 CONTRIBUTOR 看 | 不可见；ADMIN 可见可配置 |
| E2E-04 | 回收站往返 | 删目录 → 回收站还原 | 计数正确；冲突落根带后缀 |
| E2E-05 | 下载与过期 | 下载 → 6 分钟后复用旧链接 | 403 → 自动重签成功 |

---

## 6. 竞品深度对标

### 6.1 Plane 实现分析

- 开源版 Plane **没有项目文件库**——`IssueAttachment`/FileAsset 体系仅服务任务附件，无目录、无项目级聚合、无可见性控制、无配额。其 `apps/api/plane/app/views/file.py`（新版资产体系）核心是「对象存储抽象 + 预签名」，**不含库语义**。
- 本系统的分层借鉴其资产层（`FileAsset` + attributes 内联 + 预签名三步在 `FILE-001` 已落地），在其上补齐「目录树 + 可见性 + 配额 + 回收站」的库语义——相对 Plane 开源版的净增量。

### 6.2 Ones 实现分析

- Ones 项目文件模块提供目录、权限（角色/成员粒度读写）、版本、预览、配额——面向企业文档治理。本系统 P2 交付目录+可见性+配额+回收站；版本与预览紧跟（`FILE-003`），水印合规 P4。
- Ones 的权限粒度（按成员/角色分别授权读写）比三态更细——P2 三态（all/admins/members）覆盖 90% 场景且认知成本低；P4 合规场景再评估细粒度 ACL。

### 6.3 本系统设计决策

1. **一表双身份 + 引用计数删除**：附件与库文件共用 `FileAsset`，一份对象多视图入口；删除按「键级引用计数」——避免「删任务附件把库里同一份设计稿删了」的经典事故，也省一倍存储。
2. **可见性单入口函数**：`can_view_file` 在列表/详情/预签名三处强制复用（BR-08 评审红线）——权限 bug 最常见来源就是三处各自实现。
3. **移动是纯元数据操作**：对象键含 UUID 不含路径语义，移动目录毫秒级零对象操作——「路径语义在元数据层」是对象存储的正确用法。
4. **在途预留的配额判定**：pending presign 计入余量，堵住并发上传穿透配额的窗口（UT-10）。
5. **回收站 30 天 + 引用计数期满清理**：用户安全的删除缓冲与企业级的存储治理合一。

---

## 7. 里程碑与验收

### 7.1 交付物清单

| 类型 | 交付物 |
| --- | --- |
| Model / Migration | `FileFolder` 新表；`FileAsset` 扩展（folder/visibility/allowed_members/download_count + FILE-003 预留列）；索引 |
| 后端 | 目录 CRUD+移动环校验、文件 CRUD/下载预签名/恢复/回收站、`can_view_file` 单入口、配额判定（在途预留）、`sweep_expired_trash` 与下载计数 beat |
| 前端 | 文件库页（树/面包屑/双视图/dropzone/进度浮层/配额条）、移动选择器、可见性编辑器、回收站页 |
| 测试 | UT-01~16、IT-01~08、E2E-01~05 |

### 7.2 可操作演示的验收标准

1. 模板一键建库 → 拖拽上传 3 个不同类型文件：进度浮层逐文件推进，完成即入列表；MinIO 对象落位、Django 无字节流日志。
2. 建至 4 层目录并拖动移动：面包屑/树即时同步；父目录拖到自己后代被禁选；移动含 1GB 文件的目录耗时毫秒级（对象零操作）。
3. 三态可见性：设为「仅管理员」的文件，CONTRIBUTOR 列表不可见且直连 404；ADMIN 可见并可改回全员。
4. 配额：灌至 9.8GB 后上传 8MB 被拒并显示用量明细；WS Admin 收到 95% 预警通知。
5. 回收站往返：删除含 12 文件的目录 → 回收站计数 → 还原（构造同名冲突时落根带 `(恢复)`）；30 天期满无引用对象被清理（加速时钟演示）。
6. 任务双挂：把库内设计稿「附加到任务」，任务附件区可见；解除挂接后文件库与对象均无损。
