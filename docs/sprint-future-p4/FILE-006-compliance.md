# 文件水印 / 脱敏 / 合规留存

| 元信息项 | 内容 |
| --- | --- |
| 文档编号 | FILE-006 |
| 所属迭代 | P4：远期增强（第 13 周起，签约驱动排期） |
| 优先级 | P4（企业版增强 / 安全与合规价值线） |
| 所属模块 | M7-FILE 文件与知识 |
| 文档状态 | 待评审（Draft） |
| 最后更新日期 | 2026-09-01 |
| 上游依据 | `docs/需求文档.md` §3.4 企业版文件安全节、§8.2 P4 列（文件行） |
| 前置依赖 | `FILE-003`（预览与版本：水印注入点）、`FILE-004`（分享链接：合规策略的对外落点）、`AUTH-010`（文件审计管道）、`AUTH-012`（租户级策略分级） |
| 下游依赖 | 等保/SOC 2 审计材料（本能力是其证据源） |
| 架构基线 | [`api-conventions.md`](../architecture/api-conventions.md) §8、[`tech-stack.md`](../architecture/tech-stack.md) §4（MinIO 与衍生件管线） |
| 竞品参考 | 飞书（水印/密级/外发管控）、Box（Governance 合规模块标杆）、阿里云盘企业版（DLP 分级） |

> **范围声明**：本文档交付文件域的四件合规武器——**预览水印**（动态含访问者身份）、**外发管控**（禁转/禁下载/脱敏下载）、**留存周期**（自动清理与法律保留对冲）、**文件审计**（谁看过/下过/分享过什么）。DLP 内容识别（敏感词扫描）只做规则匹配的轻量版（§2.5），AI 内容识别归 `AI-001` 后续评估。

---

## 1. 概述

### 1.1 功能定位

文件泄露是企业客户最怕的事故形态：截图外传、离职带走、分享链接失控。合规能力的本质是**提高泄露成本 + 泄露可追溯**：

| 交付项 | 说明 |
| --- | --- |
| 动态水印 | 预览与下载件叠加「姓名 + 工号/邮箱 + 时间」水印；图片/PDF/Office 预览全覆盖；明暗两式 |
| 外发管控 | 文件/文件夹/项目三级策略：禁下载（仅预览）、禁分享链接、下载脱敏（Office 文档去元数据 + 图片降分辨率可选） |
| 留存周期 | 工作空间级保留策略（如「删除后 90 天彻底清除」「审计日志留 3 年」）；**法律保留**（Legal Hold）可冻结特定文件免清理 |
| 文件审计 | 预览/下载/分享/删除四事件全留痕（复用 `AUTH-010` 管道），租户级导出 |

### 1.2 启动条件

| 条件 | 判定 |
| --- | --- |
| 商业条件 | 签约客户含合规条款（金融/医药/法律行业刚需）；或等保测评排期确定 |
| 技术前置 | `FILE-003` 衍生件管线（LibreOffice/ffmpeg）生产稳定——水印注入复用该管线；`AUTH-010` 审计留存策略可配 |
| 合规前置 | 法务确认水印文案要素与隐私边界（员工监控告知义务）；留存周期默认值经合规评审 |

### 1.3 独立交付判定

1. 三类文件（图片/PDF/Office）预览与下载均带动态水印且不可通过常规手段去除（平铺 + 低透明 + 随机抖动）。
2. 外发管控矩阵（§2.3）四象限行为与规格一致；脱敏下载的 Office 文件元数据清空可验证。
3. 留存到期自动清理 + Legal Hold 文件豁免清理，清理动作入审计。
4. 零回归：未启用合规策略的工作空间预览/下载路径与企业版 V1.0 一致（零衍生件开销）。

### 1.4 竞品参考结论（详见第 6 章）

- **飞书**：水印（含截屏威慑的暗水印研究）+ 密级标签 + 外发审批，国内企业心智标杆。
- **Box Governance**：留存策略（Retention Policy）+ Legal Hold + 事件流外发 SIEM，合规功能的教科书。
- **本系统取舍**：水印与外发对齐飞书；留存/Legal Hold 语义对齐 Box；**不做**外发审批流（重流程，归工作流引擎 `WF-002` 客户自建）。

---

## 2. 业务逻辑

### 2.1 策略模型

```mermaid
flowchart TB
    WS["工作空间策略<br/>（默认）"] --> PROJ["项目策略<br/>（覆盖）"] --> FOLDER["文件夹策略<br/>（覆盖）"] --> FILE["文件策略<br/>（最细，覆盖）"]
    FILE --> EFF["有效策略 = 最细层非空值"]
    EFF --> WM["水印注入<br/>预览/下载"]
    EFF --> CTRL["外发控制<br/>禁下载/禁分享/脱敏"]
    EFF --> RET["留存周期<br/>清理 vs Legal Hold"]
    AUD["四事件审计<br/>AUTH-010 管道"]
```

| 策略字段 | 取值 | 说明 |
| --- | --- | --- |
| `watermark` | `off / preview_only / preview_and_download` | 水印作用面 |
| `download` | `allow / deny / desensitized` | 下载控制 |
| `share_link` | `allow / deny / password_required` | 分享链接控制（叠加 `FILE-004` 既有能力） |
| `retention_days` | int / null（永久） | 软删后彻底清除天数 |
| `legal_hold` | bool（仅文件级，由合规角色操作） | 冻结免清理 |

### 2.2 业务规则（BR）

| 编号 | 规则 | 说明 |
| --- | --- | --- |
| BR-01 | 策略继承与覆盖 | 有效策略沿 空间→项目→文件夹→文件 链取最细层非空值；文件级设置需 `file.compliance.manage`（WS_ADMIN+） |
| BR-02 | 水印身份 | 水印内容 = 当前访问者显示名 + 邮箱 + 访问时间（精确到分）；匿名分享访问用水印「外部分享 + 链接尾号 + 时间」 |
| BR-03 | 禁下载即禁原文件 | `download=deny` 时：预签名下载 URL 拒发（`PERM_DENIED`）；预览衍生件仍可用但强制水印 |
| BR-04 | 脱敏下载 | `download=desensitized`：Office 去作者/公司/修订记录元数据（重新导出）；图片可选降分辨率（最长边 1920）；PDF 去表单可编辑性（扁平化） |
| BR-05 | 分享拦截 | `share_link=deny` 时创建分享链接返回 `RESOURCE_STATE_INVALID`；存量链接在该策略生效时自动失效（410 `RESOURCE_GONE`） |
| BR-06 | 留存清理 | 每日 Celery 任务扫描：`deleted_at + retention_days < now` 且无 Legal Hold → 对象存储物理删除 + 行标记 `purged_at`；清理清单入审计 |
| BR-07 | Legal Hold 优先 | Hold 中的文件：留存清理豁免、删除被拒（`RESOURCE_LOCKED`）、版本链不可裁剪；Hold 操作（挂/解）双人确认入审计 |
| BR-08 | 审计四事件 | `file.preview / file.download / file.share.create / file.purge` 全部入 `AUTH-010`，含文件 ID、策略快照、IP |
| BR-09 | 预览水印性能 | 水印衍生件按（文件版本 × 用户）缓存 24h；未命中实时合成 P95 < 3s（异步生成 + 轮询） |
| BR-10 | 暗水印（图片） | 图片预览叠加 DCT 域盲水印（用户 ID 编码），截图后可提取溯源——仅企业版旗舰档开启（计算开销高） |
| BR-11 | 不可规避声明 | 水印为威慑与溯源手段，规格明确不承诺对抗专业图像处理去除；客户协议中文案由法务审核 |
| BR-12 | 零回归 | 策略全默认（off/allow）时：预览/下载路径与 V1.0 一致，无额外衍生件任务，响应头无新增字段 |

### 2.3 外发管控矩阵

| 场景 | `download=deny` | `download=desensitized` | `share_link=deny` |
| --- | --- | --- | --- |
| 在线预览 | ✅ 强制水印 | ✅ 强制水印 | ✅ 正常 |
| 下载原文件 | ❌ `PERM_DENIED` | ✅ 脱敏件（BR-04） | ✅ 正常 |
| 创建分享链接 | ✅（链接内同样禁下载） | ✅（链接下载亦为脱敏件） | ❌ `RESOURCE_STATE_INVALID` |
| 存量分享链接 | 链接预览强制水印 | 链接下载切脱敏 | 自动失效（410） |

### 2.4 留存与 Legal Hold 流程

```mermaid
sequenceDiagram
    participant B as Celery Beat(每日 04:20)
    participant R as retention_sweeper
    participant S as MinIO
    participant A as AuditLog

    B->>R: purge_expired_files.delay()
    R->>R: 查 deleted_at + retention < now
    R->>R: 剔除 legal_hold=True（BR-07）
    loop 每批 200
        R->>S: 删除原文件+全部版本+衍生件
        S-->>R: OK
        R->>A: file.purge（批量清单，BR-06/08）
    end
    Note over R: Legal Hold 挂载时
    R->>R: 删除/Hold 文件请求 → RESOURCE_LOCKED
```

| 状态 | 说明 |
| --- | --- |
| `purged_at` | 物理清除时间戳；清除后行保留（审计锚点），下载/预览返回 `RESOURCE_GONE` |
| 版本链 | 留存按文件整体计（最新删除时间），历史版本随主体一同清理 |
| Hold 台账 | `LegalHold` 表记录挂载人/事由/案件号（自由文本）/双人确认，供合规导出 |

### 2.5 轻量内容识别（DLP-Lite）

| 能力 | 说明 |
| --- | --- |
| 规则库 | 内置 6 类正则：身份证号、银行卡号、手机号、邮箱、营业执照号、密钥模式（`AKIA…`/`-----BEGIN PRIVATE KEY-----`）；工作空间可自定义 ≤ 20 条 |
| 扫描时机 | 上传完成后异步扫文本层（Office/PDF 提取文本，图片不扫——OCR 归 `AI-001` 评估） |
| 命中处置 | 仅标记与告警（`FileAsset.dlp_hits` JSONB + 通知 WS_ADMIN），**不阻断上传**（误报成本高于漏报，处置权归人） |
| 审计 | 命中事件入审计，含规则 ID 与命中计数（不含命中内容本身——二次敏感） |

---

## 3. UI/UX 设计

### 3.1 页面清单

| 页面 | 位置 | 核心任务 |
| --- | --- | --- |
| 合规策略设置 | 工作空间设置 → 文件合规 | 默认策略四项 + DLP 规则管理 |
| 项目/文件夹策略 | 项目设置 / 文件夹右键菜单 | 层叠覆盖设置与「继承自」提示 |
| Legal Hold 台账 | 工作空间设置 → 文件合规 → Hold | 挂载/解除（双人）、事由记录、导出 |
| 文件徽标体系 | 文件库列表/详情/预览 | 水印/禁下载/Hold/DLP 命中四徽标 |

### 3.2 合规策略设置线框

```
┌──────────────────────────────────────────────────────────────────┐
│ 设置 / 文件合规                                                   │
├──────────────────────────────────────────────────────────────────┤
│ ── 默认策略（可被项目/文件夹/文件覆盖，BR-01）───────────────     │
│ 水印:       ( ) 关闭  (•) 仅预览  ( ) 预览+下载                   │
│ 下载:       (•) 允许  ( ) 禁止    ( ) 脱敏后允许                  │
│ 分享链接:   (•) 允许  ( ) 禁止    ( ) 必须密码                    │
│ 留存:       软删除后 [90] 天彻底清除（0 = 永久保留）               │
│                                                                  │
│ ── DLP 内容识别（轻量版）─────────────────────────────────        │
│ ☑ 身份证号  ☑ 银行卡号  ☑ 手机号  ☐ 邮箱  ☑ 密钥模式              │
│ 自定义规则: [+ 添加正则]  (已用 3/20)                             │
│ 命中处置: 仅标记并通知管理员（不阻断上传）              [?]        │
│                                                                  │
│ ── Legal Hold ───────────────────────────────────────────        │
│ 当前生效 2 项 [管理台账 →]                                        │
│                                              [保存] 已自动审计    │
└──────────────────────────────────────────────────────────────────┘
```

### 3.3 文件详情徽标与预览水印线框

```
文件详情侧栏
┌────────────────────────────────────────┐
│ 📄 合同终版-v3.pdf                      │
│ 🛡 预览水印  ⬇ 禁下载  📌 Legal Hold    │
│ ⚠ DLP: 命中「身份证号」×2 (9/1 扫描)    │
├────────────────────────────────────────┤
│ 有效策略: 文件夹「合同库」覆盖空间默认  │
│ 留存: 删除后 365 天清除 · Hold 豁免中   │
└────────────────────────────────────────┘

预览区（水印平铺示意）
┌──────────────────────────────────────────────┐
│      王小明 wang@acme.com 14:32              │
│  合同条款……        王小明 wang@acme.com      │
│         14:32                                │
│    王小明 wang@acme.com 14:32                │
│ ……第 3 条           王小明 wang@acme…        │
│                                              │
│  （45° 平铺 · 8% 透明度 · 位置随机抖动）      │
└──────────────────────────────────────────────┘
```

### 3.4 交互规则

| 场景 | 交互 |
| --- | --- |
| 禁下载态 | 下载按钮隐藏；右键/快捷键说明文案「该文件策略禁止下载」；拖拽下载同样拦截（无下载 URL 可拖） |
| Hold 挂载 | 双人确认流：挂载人填事由 → 第二管理员确认 → 生效；文件徽标即时出现 |
| 脱敏提示 | 下载脱敏件时 Toast「已按策略脱敏：元数据已清除」 |
| DLP 命中 | 命中徽标仅 WS_ADMIN 与文件所有者可⻅；点击看规则 ID 与计数（无内容，§2.5） |
| 权限 | 策略管理 `file.compliance.manage`；Hold 操作 WS_ADMIN × 2；普通成员仅见徽标与受限行为 |

---

## 4. 技术架构

### 4.1 数据模型

```python
# apps/api/rp_files/compliance_models.py
class CompliancePolicy(BaseModel):
    """单表多态承载四级策略；scope 四选一非空（CHECK 约束）。"""

    workspace = models.ForeignKey("rp_workspaces.Workspace", null=True,
                                  on_delete=models.CASCADE)
    project = models.ForeignKey("rp_projects.Project", null=True,
                                on_delete=models.CASCADE)
    folder = models.ForeignKey("rp_files.FileFolder", null=True,
                               on_delete=models.CASCADE)
    asset = models.ForeignKey("rp_files.FileAsset", null=True,
                              on_delete=models.CASCADE)
    watermark = models.CharField(max_length=24, null=True)     # off/preview_only/preview_and_download
    download = models.CharField(max_length=16, null=True)      # allow/deny/desensitized
    share_link = models.CharField(max_length=20, null=True)    # allow/deny/password_required
    retention_days = models.PositiveIntegerField(null=True)

    class Meta:
        db_table = "file_compliance_policy"
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(workspace__isnull=False, project__isnull=True,
                             folder__isnull=True, asset__isnull=True)
                    | models.Q(project__isnull=False, folder__isnull=True,
                               asset__isnull=True)
                    | models.Q(folder__isnull=False, asset__isnull=True)
                    | models.Q(asset__isnull=False)
                ),
                name="ck_policy_exactly_one_scope"),
            models.UniqueConstraint(fields=["asset"],
                                    condition=models.Q(asset__isnull=False),
                                    name="uq_policy_asset"),
        ]


class LegalHold(BaseModel):
    asset = models.ForeignKey("rp_files.FileAsset",
                              on_delete=models.CASCADE,
                              related_name="legal_holds")
    reason = models.CharField(max_length=255)
    case_ref = models.CharField(max_length=64, blank=True)     # 案件号
    placed_by = models.ForeignKey("rp_users.User",
                                  related_name="+", on_delete=models.PROTECT)
    confirmed_by = models.ForeignKey("rp_users.User",          # 双人 BR-07
                                     related_name="+", on_delete=models.PROTECT)
    released_at = models.DateTimeField(null=True)
    released_by = models.ForeignKey("rp_users.User", null=True,
                                    related_name="+", on_delete=models.PROTECT)

    class Meta:
        db_table = "file_legal_hold"
        indexes = [models.Index(fields=["asset", "released_at"],
                                name="idx_hold_active")]
```

`FileAsset` 增列（AddField 迁移）：`dlp_hits JSONB default '[]'`、`purged_at DateTimeField null`。有效策略解析走「四级 LEFT JOIN 取最细非空」单 SQL + Redis 缓存（键 `fpol:{asset_id}`，策略变更时精确失效，TTL 10min）。

### 4.2 水印衍生件管线

```python
# apps/api/rp_files/watermark.py
from celery import shared_task


@shared_task(queue="derivative", rate_limit="30/m")
def render_watermarked(asset_id: str, version_id: str, user_ctx: dict) -> str:
    """生成带水印预览件；返回 MinIO 键。缓存 24h（BR-09）。"""
    asset = FileAsset.objects.select_related("folder__project").get(id=asset_id)
    policy = resolve_policy(asset)                       # 四级链
    if policy.watermark == "off":
        return get_plain_derivative_key(asset, version_id)   # BR-12 直通
    src = fetch_derivative(asset, version_id)            # 复用 FILE-003 产物
    text = watermark_text(user_ctx, asset)               # BR-02
    if asset.is_image:
        out = tile_text_watermark(src, text, opacity=0.08, rotate=45, jitter=True)
        if policy.dark_watermark_enabled:                # BR-10 旗舰档
            out = embed_dct_watermark(out, user_ctx["user_id"])
    elif asset.is_pdf:
        out = pdf_overlay_watermark(src, text)           # pypdf 图层叠加
    else:                                                # Office → PDF 预览件
        out = pdf_overlay_watermark(src, text)
    key = put_derivative(asset, version_id, user_ctx["user_id"], out)
    cache.set(f"wm:{asset_id}:{version_id}:{user_ctx['user_id']}", key, 86400)
    return key
```

| 要点 | 说明 |
| --- | --- |
| 复用管线 | 水印叠加在 `FILE-003` 既有衍生件之上（PDF 预览件/图片缩略件），不重做格式转换 |
| 匿名访问 | 分享链接访问以 `share:{slug尾号}` 为身份键缓存（BR-02） |
| 降级 | 水印合成失败 → 预览放行纯衍生件 + 告警（可用性优先，失败入审计 `watermark_failed`） |

### 4.3 留存清理任务

```python
# apps/api/rp_files/tasks_compliance.py
@shared_task(queue="maintenance")
def purge_expired_files() -> None:
    """每日 04:20 beat；分批 200；全程审计（BR-06）。"""
    candidates = (
        FileAsset.objects
        .filter(deleted_at__isnull=False, purged_at__isnull=True)
        .exclude(legal_holds__released_at__isnull=True)      # BR-07 豁免
        .select_related("folder__project__workspace"))
    for asset in candidates.iterator(chunk_size=200):
        days = resolve_policy(asset).retention_days
        if days is None or days == 0:
            continue                                          # 永久保留
        if asset.deleted_at + timedelta(days=days) > timezone.now():
            continue
        keys = list_storage_keys(asset)                       # 原文件+版本+衍生件
        storage_delete(keys)                                  # MinIO 批量删
        asset.purged_at = timezone.now()
        asset.save(update_fields=["purged_at", "updated_at"])
        write_audit_log.delay_on_commit(
            actor="retention_sweeper", action="file.purge",
            target_id=str(asset.id),
            event_key=f"purge:{asset.id}",
            extra={"keys_removed": len(keys)})
```

### 4.4 API 端点

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET/PUT | `/api/v1/workspaces/{slug}/file-compliance/` | 空间默认策略（PUT 整体替换） |
| PUT | `/api/v1/projects/{pid}/file-compliance/` | 项目策略覆盖 |
| PUT | `/api/v1/workspaces/{slug}/folders/{id}/compliance/` | 文件夹策略 |
| PUT | `/api/v1/workspaces/{slug}/files/{id}/compliance/` | 文件级策略（最细） |
| GET | `/api/v1/workspaces/{slug}/files/{id}/compliance/effective/` | 有效策略解析（含继承来源标注） |
| GET/POST | `/api/v1/workspaces/{slug}/legal-holds/` | Hold 台账 / 挂载（双人字段） |
| POST | `/api/v1/workspaces/{slug}/legal-holds/{id}/release/` | 解除（双人） |
| GET/POST/DELETE | `/api/v1/workspaces/{slug}/dlp-rules/` | 自定义 DLP 规则管理 |

**成功示例** — `GET …/compliance/effective/`：

```json
{
  "status": "success",
  "data": {
    "watermark": {"value": "preview_and_download", "inherited_from": "folder:合同库"},
    "download": {"value": "desensitized", "inherited_from": "file"},
    "share_link": {"value": "allow", "inherited_from": "workspace"},
    "retention_days": {"value": 365, "inherited_from": "folder:合同库"},
    "legal_hold": true
  },
  "meta": {"request_id": "01J70AK2M8NQ4PXRBTVH5WD3EA"}
}
```

**错误示例** — 禁下载（BR-03）：

```json
{
  "status": "error",
  "error": {
    "code": "PERM_DENIED",
    "message": "该文件策略禁止下载，仅可在线预览",
    "details": [{"field": "download", "code": "INVALID",
                 "message": "策略来源: 文件夹「合同库」"}]
  },
  "meta": {"request_id": "01J70AL3N9OR5QYSCUW6XE4FB"}
}
```

**错误示例** — 删除 Hold 文件（BR-07）：

```json
{
  "status": "error",
  "error": {
    "code": "RESOURCE_LOCKED",
    "message": "文件处于法律保留（Legal Hold）中，不可删除",
    "details": [{"field": "asset", "code": "INVALID",
                 "message": "Hold 事由: 劳动仲裁案 A-2026-081，挂载人: 合规-林"}]
  },
  "meta": {"request_id": "01J70AM4O0PS6RZTDVX7YF5GC"}
}
```

### 4.5 性能与规模

| 指标 | 预算 | 手段 |
| --- | --- | --- |
| 有效策略解析 | < 1ms（缓存命中） | Redis `fpol:{asset}`；策略变更精确失效（四级分别失效相关子树键） |
| 水印预览首击 | P95 < 3s | 异步生成 + 前端轮询；二次访问缓存直达（24h） |
| 留存扫描 | 10 万删除文件 < 10min | `iterator` 分批 + MinIO 批量删（1000 keys/请求） |
| DLP 扫描 | 上传后 < 60s | 文本提取复用 `FILE-003` 管线，正则集预编译 |

### 4.6 明水印渲染参数与脱敏细则

| 参数 | 取值 | 说明 |
| --- | --- | --- |
| 文本内容 | `{display_name} {email} {yyyy-MM-dd HH:mm}` | 匿名分享：`外部分享 {slug尾4位} {时间}`（BR-02） |
| 平铺 | 45° 旋转，间距 260×140px，位置随机抖动 ±20px | 防单点裁剪去除 |
| 透明度 | 8%（深色底 12%） | 可读性与干扰平衡，用户可用性测试标定 |
| 字体 | Noto Sans 14px（中英等宽回退） | 私有化离线内置（`INFRA-006` BR-07 联动） |
| PDF 叠加 | pypdf 每页独立图层（非全页图） | 文字仍可选中复制（不破坏正常办公） |
| 图片降采样 | 预览最长边 2048 | 水印件体积 P95 < 1.5MB |

| 脱敏细则（BR-04） | 实现 |
| --- | --- |
| Office 元数据 | `docProps/core.xml` 重写（author/company/lastModifiedBy 清空）+ 修订记录（`w:ins/w:del`）接受全部 + 批注剥离（python-docx/openpyxl 重写导出） |
| PDF 扁平化 | 表单字段渲染为静态内容、注释/附件剥离、JavaScript 动作清除 |
| 图片 | EXIF 全剥离 + 可选降分辨率（策略 `desensitized` 时默认开启最长边 1920） |

### 4.7 DLP 规则格式与示例

```json
{
  "status": "success",
  "data": {
    "builtin": [
      {"id": "cn_id_card", "name": "身份证号", "pattern": "\\b\\d{17}[\\dXx]\\b", "checksum": "gb11643"},
      {"id": "cn_bank_card", "name": "银行卡号", "pattern": "\\b\\d{16,19}\\b", "checksum": "luhn"},
      {"id": "cn_mobile", "name": "手机号", "pattern": "\\b1[3-9]\\d{9}\\b"},
      {"id": "email_addr", "name": "邮箱", "pattern": "[\\w.+-]+@[\\w-]+\\.[\\w.]+"},
      {"id": "biz_license", "name": "统一社会信用代码", "pattern": "\\b[0-9A-HJ-NPQRTUWXY]{18}\\b"},
      {"id": "secret_key", "name": "密钥模式", "pattern": "(AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"}
    ],
    "custom": [
      {"id": "c_01", "name": "内部项目代号", "pattern": "Project-(?:Titan|Aurora)", "enabled": true}
    ],
    "custom_quota": {"used": 3, "limit": 20}
  },
  "meta": {"request_id": "01J70CK8M2NQ4PXRBTVH5WD3EA"}
}
```

| 校验 | 说明 |
| --- | --- |
| 校验和 | `cn_id_card`/`cn_bank_card` 命中后过 GB11643/Luhn 校验，误报率降至 < 2%（正则裸命中约 15%） |
| 自定义规则审查 | 保存时编译验证 + 灾难性回溯检测（`safe-regex` 等价检查），超限/非法 `VALIDATION_ERROR` |
| 扫描窗口 | 单文件文本层 ≤ 2MB（超出截断扫描并标记 `partial=true`） |

### 4.8 策略变更的传播与一致性

| 场景 | 行为 |
| --- | --- |
| 文件夹策略收紧 | 子树 `fpol` 缓存批量失效（Redis 按 `folder_path` 前缀扫键删除）；存量下载链接下次请求即按新策略（无宽限——收紧即时生效是合规语义） |
| 文件夹策略放宽 | 同样即时；放宽动作审计高亮（`severity=high`） |
| 文件移入受管文件夹 | 移动完成即按新继承链生效；`FILE-002` 移动接口已走事务，策略解析在移动提交后自然读取新链 |
| 策略删除（恢复继承） | 删除该层策略行 → 有效值回落上一层；缓存同步失效 |
| 并发冲突 | 策略行带 `version` 乐观锁；同层并发 PATCH 返回 `RESOURCE_CONFLICT` |

---

## 5. 测试用例

### 5.1 单元测试（UT）

| 编号 | 用例 | 断言 |
| --- | --- | --- |
| UT-01 | 策略继承 | 四级链各组合的有效值与继承来源标注正确（16 组合参数化） |
| UT-02 | 禁下载 | `download=deny` 预签名拒发 `PERM_DENIED`；预览放行 |
| UT-03 | 脱敏下载 | Office 脱敏件元数据（作者/公司/修订）为空；图片降分辨率生效 |
| UT-04 | 分享拦截 | `share_link=deny` 创建链接 `RESOURCE_STATE_INVALID`；存量链接 410 |
| UT-05 | 水印文本 | 成员访问含姓名+邮箱+时间；匿名分享含链接尾号（BR-02） |
| UT-06 | 水印缓存 | 同（版本，用户）二次请求命中缓存不重渲染 |
| UT-07 | 留存到期 | `deleted_at + days < now` 进清理集；未到期与永久保留不清理 |
| UT-08 | Hold 豁免 | Hold 文件不在清理集；删除请求 `RESOURCE_LOCKED` |
| UT-09 | Hold 双人 | 单人挂载不落库；确认人=挂载人被拒 |
| UT-10 | DLP 命中 | 含身份证号的 docx 上传后 `dlp_hits` 计数正确；命中不含内容串 |
| UT-11 | DLP 自定义上限 | 第 21 条自定义规则 `RESOURCE_LIMIT_EXCEEDED` |
| UT-12 | 零回归 | 默认策略下预览/下载路径无水印任务派发（mock 断言零调用） |

### 5.2 集成测试（IT）

| 编号 | 用例 | 断言 |
| --- | --- | --- |
| IT-01 | 三格式水印 | PNG/PDF/DOCX 预览件水印可见（图像 diff 检测）；下载件按策略带/不带 |
| IT-02 | 暗水印提取 | 旗舰档图片预览截图（模拟）后 DCT 提取还原用户 ID |
| IT-03 | 留存清理全链 | 构造过期文件 → 任务执行 → MinIO 键全删 → `purged_at` 落 → 审计清单完整 |
| IT-04 | Hold 全链 | 挂载（双人）→ 清理豁免 → 删除被拒 → 解除（双人）→ 可正常留存清理 |
| IT-05 | 策略失效传播 | 文件夹策略变更后下属文件 `fpol` 缓存失效，新行为即时生效 |
| IT-06 | 外发矩阵 | §2.3 矩阵 12 格行为自动化全绿 |

### 5.3 E2E 测试

| 编号 | 场景 | 验收 |
| --- | --- | --- |
| E2E-01 | 管控演示 | 配置合同库策略 → 成员预览见水印、无下载按钮 → 分享链接被拦 → 管理员改策略后即时生效 |
| E2E-02 | 合规审计 | 导出某文件全量审计（预览/下载/分享/清理）供检查 |
| E2E-03 | DLP 告警 | 上传含敏感信息文件 → WS_ADMIN 收通知 → 徽标可见 |

---

## 6. 竞品深度对标

| 维度 | 飞书 | Box Governance | 阿里云盘企业版 | 本系统 |
| --- | --- | --- | --- | --- |
| 水印 | 明水印 + 截屏威慑研究 | 水印（预览/下载） | 明水印 | 明水印（平铺抖动）+ DCT 盲水印（旗舰） |
| 外发管控 | 密级 + 外发审批 | 分类驱动策略 | 禁转/有效期 | 四级策略继承（空间→文件） |
| 留存 | 管理后台保留期 | Retention + Legal Hold（教科书） | 回收站期 | 留存周期 + Legal Hold 双人（对齐 Box） |
| DLP | 内容识别（企业旗舰） | 无原生（生态集成） | 敏感词 | 轻量正则 6+20，标记不阻断 |
| 审计 | 管理审计 | 事件流外发 SIEM | 操作日志 | 四事件入 `AUTH-010` + 策略快照 |

**结论**：Box 证明了留存/Hold 是合规的骨架，飞书证明了水印与外发是客户感知最强的面子；本系统两者兼备且以「策略四级继承 + 审计含策略快照」形成差异化——任何一次下载都能回答「当时生效的是哪条策略」，这是事后定责的关键。DLP 刻意轻量：正则误报率高，阻断式 DLP 会激怒用户，标记 + 人审是投入产出最优解；内容理解型识别等 `AI-001` 能力成熟后再升级。

---

## 7. 里程碑与验收

### 7.1 工作量估算

| 交付面 | 内容 | 估算 |
| --- | --- | --- |
| Model / Migration | 策略/Hold 2 表 + `FileAsset` 增列 + CHECK 约束 | 1.5 d |
| 后端 | 策略解析、水印管线（含 DCT 可选）、留存任务、DLP 扫描、8 组端点 | 6 d |
| 前端 | 合规设置页、徽标体系、Hold 台账、有效策略展示 | 3.5 d |
| 测试 | UT-01~12、IT-01~06、E2E-01~03 | 3 d |
| **合计** | | **14 d（2 人并行约 1.5-2 周）** |

### 7.2 可操作演示的验收标准

1. 外发矩阵（§2.3）12 格现场演示全绿；脱敏件元数据清空可验。
2. 三格式水印可见且截图威慑演示（暗水印提取还原用户 ID，旗舰档）。
3. 留存闭环：过期文件自动物理清除 + 审计清单；Hold 文件豁免与解除后恢复清理资格。
4. DLP：内置 6 类与自定义规则命中告警，误报不阻断上传。
5. 性能：§4.5 四项指标达标；零回归断言（UT-12）通过。
