# WF-006 审批留痕与合规溯源

| 元信息项 | 内容 |
| --- | --- |
| 文档编号 | WF-006 |
| 所属迭代 | Sprint 7 — 企业工作流核心（第 10 周 D3-4 支线） |
| 优先级 | P3（企业版核心级 · 合规面） |
| 覆盖模块 | M5-WF 工作流与审批（留痕切面） |
| 工作量估算 | 3 人日（后端 2 + 前端 0.5 + QA 0.5） |
| 文档状态 | 待评审（Draft） |
| 最后更新日期 | 2026-09-01 |
| 上游依赖 | `WF-002`（`ApprovalInstance/ApprovalRecord` 执行模型）、`TASK-010`（Activity 管道）、`FILE-002`（导出文件落文件库范式） |
| 下游消费 | Sprint 8 `AUTH-010`（全站审计并入统一检索）、Sprint 9（审批时效报表数据源）、P4 合规认证 |

---

## 1. 概述

### 1.1 功能定位

WF-006 把 WF-002 的审批执行数据升级为**合规级留痕体系**，回答审计三问——**谁在什么时间对什么流转做了什么决定、依据是什么、记录是否可信**：

1. **不可变存储**：审批票（`ApprovalRecord`）DB 触发器级只增不改；实例终态不可回写。
2. **全量事件链**：立案/各级动作/驳回/撤回/终止/超时/转交，每个事件落三类出口——审批时间线（业务）、任务动态（TASK-010）、审计日志（本文档）。
3. **检索与导出**：多维度检索（人/任务/流/动作/时间窗）、CSV 导出（导出动作本身入审计）、留存策略（3 年）。

### 1.2 留痕三层出口

```mermaid
flowchart LR
    EVT["审批域事件<br/>（WF-002 唯一入口产生）"] --> TL["业务时间线<br/>approval_records<br/>（不可变票据）"]
    EVT --> ACT["任务动态<br/>issue_activities（TASK-010）<br/>verb=approval_*"]
    EVT --> AUD["审计日志<br/>approval_audit_events<br/>（合规检索/导出）"]
    AUD --> EXP["CSV 导出<br/>（导出动作自身入审计）"]
    TL -.只增不改.-> DBT["DB 触发器<br/>拒绝 UPDATE/DELETE"]
```

> **为什么审计单独建表而非复用 `approval_records`**：records 是业务票据（审批人视角的票），审计事件还覆盖**非票动作**（立案、终止、超时提醒、定义变更、导出、访问）且需要独立留存策略与检索索引——两表同源事件、各司其职；Sprint 8 `AUTH-010` 建全站 `audit_logs` 时本表事件**双写**并入，检索面统一。

### 1.3 范围边界

| 范围 | 本文档交付 | 明确不做 |
| --- | --- | --- |
| 不可变 | records 触发器、实例终态保护、篡改检测（哈希链） | 法律级电子签名/时间戳公证（P4 合规认证时评估） |
| 审计事件 | 审批域全事件采集、双写预留（AUTH-010） | 全站操作审计（Sprint 8） |
| 检索导出 | 项目/工作空间两级检索、CSV 导出、导出审计 | BI 对接（P4 `RPT-005`） |
| 留存 | 票据与审计 3 年、超期归档（冷存 MinIO） | 按租户差异化留存（P4 合规包） |

### 1.4 前置依赖

| 依赖 | 内容 | 阻塞原因 |
| --- | --- | --- |
| `WF-002` §4.3 | ApprovalInstance/ApprovalRecord 表与唯一入口 | 留痕数据源 |
| `TASK-010` | Activity 幂等管道（event_key 范式） | 动态落账 |
| `INFRA-002` | MinIO 桶与 Celery | 导出与归档 |
| `INFRA-004` | 请求日志中间件（request_id 贯穿） | 溯源关联 |

### 1.5 竞品参考

| 竞品 | 参考点 | 处置 |
| --- | --- | --- |
| Ones | 审批记录查询与导出（企业版合规卖点） | 功能面对齐；**哈希链篡改检测**是其未做的加固 |
| Jira | Audit log（管理操作）+ 审批记录 | 审计事件模型对齐其 `auditing` 插件结构 |
| 钉钉审批 | 审批单留档 + 导出 | 留存语义对齐（钉钉 3 年） |
| SOX/等保实践 | WORM（Write Once Read Many）存储要求 | 触发器 + 哈希链 = 应用层 WORM |

---

## 2. 业务逻辑

### 2.1 审计事件清单

| 事件 type | 触发点 | 关键 payload |
| --- | --- | --- |
| `instance.created` | 立案 | 发起人/任务/边/流快照版本/守卫通过清单 |
| `record.acted` | 审批动作 | level/审批人/动作/意见哈希/原状态→新状态 |
| `record.skipped` | 自动跳票 | 原因（self/offboarded） |
| `instance.level_advanced` | 逐级推进 | from_level→to_level |
| `instance.completed` | 终审通过 | 迁移前状态→目标状态/终审守卫重跑结果 |
| `instance.rejected` | 驳回 | 驳回人/意见哈希 |
| `instance.withdrawn` | 撤回 | 发起人 |
| `instance.terminated` | 终止 | 终止人/原因/comment 哈希 |
| `instance.timeout_reminded` | 超时提醒 | 提醒对象/渠道 |
| `instance.escalated` | 离职转交 | 原审批人→补位人 |
| `flow.definition_changed` | 审批流定义变更 | 变更 diff（谁改的、改了什么） |
| `audit.exported` | 导出动作 | 导出者/过滤条件/行数/文件指针 |
| `audit.accessed` | 敏感访问（非成员读实例详情由成员可读，故仅记「导出文件下载」） | 访问者/对象 |

> **意见哈希而非明文**：审计事件存 `comment_sha256`（意见原文在 records 业务表），导出时合并——审计表即使泄露也不暴露意见内容，同时可验证原文未被篡改。

### 2.2 不可变与篡改检测

```mermaid
sequenceDiagram
    autonumber
    participant S as ApprovalService（唯一写入口）
    participant PG as PostgreSQL
    participant H as 哈希链

    S->>PG: INSERT approval_audit_events（含 prev_hash, event_hash）
    PG->>H: event_hash = sha256(prev_hash ∥ canonical(payload))
    Note over PG: 触发器 1：UPDATE/DELETE → EXCEPTION<br/>触发器 2：event_hash 校验链式连续
    S->>PG: UPDATE approval_records SET action=…（pending→approve 唯一合法迁移）
    Note over PG: 触发器 3：仅允许 action acted_at comment 三字段、<br/>且 pending→终态单向；其余 UPDATE/DELETE → EXCEPTION
```

| 机制 | 实现 |
| --- | --- |
| 哈希链 | 每条审计事件 `prev_hash` 指向前条（项目维度链）；`event_hash = sha256(prev_hash ‖ canonical_json(payload))`——删改任何历史行都会断链，校验任务可检测 |
| 链校验 | beat 每日抽查（每项目末 1000 条重算比对）；年度合规审计可全量重放 |
| records 有限状态机 | `pending→approve/reject/skipped` 单向；触发器列级白名单（仅 `action/comment/acted_at` 可写且仅一次） |
| 实例终态保护 | `status ∈ {approved,rejected,withdrawn,terminated}` 后任何 UPDATE 拒绝（completed_at 一次性写入除外，同事务） |

### 2.3 检索与导出

| 能力 | 规则 |
| --- | --- |
| 检索维度 | 项目内：任务/发起人/审批人/流/动作/状态/时间窗（组合 AND）；工作空间级（WS_ADMIN）：跨项目同人/同流检索 |
| 权限 | 项目成员可查本项目审批业务记录；**审计检索页**与导出需 `approval.audit`（PROJ_ADMIN+）；跨项目需 WS_ADMIN |
| 导出 | CSV（UTF-8 BOM，Excel 兼容）；异步生成（>1 万行走 Celery → 落 MinIO 临时预签 24h）；**导出动作与下载动作均入审计** |
| 导出字段 | 时间/项目/任务编号/任务标题/流转/发起人/级别/审批人/动作/意见/耗时/request_id |
| 留存 | records 与审计事件在线 3 年；超期月度归档任务导出 Parquet 至 MinIO 冷存（再存 4 年），在线表删除归档段（归档本身入审计） |

### 2.4 业务规则汇总

| 编号 | 规则 | 判定位置 | 违反后果 |
| --- | --- | --- | --- |
| BR-01 | 审计事件只增：UPDATE/DELETE 被触发器拒绝（含 superuser 连接——应用与运维同约束） | DB | EXCEPTION + 告警 |
| BR-02 | 哈希链断裂 = 安全事件：校验任务发现即 P0 告警并冻结导出 | 校验任务 | 告警 + 冻结 |
| BR-03 | 审计事件由 WF-002 唯一入口产生；任何绕过直写 records 的路径评审拒绝 | 架构约束 | 评审拒绝 |
| BR-04 | 意见在审计表仅存哈希；导出时从业务表合并原文 | 服务 | — |
| BR-05 | 导出需 `approval.audit`；导出与下载动作自身入审计（谁导了什么条件多少行） | Service | `403 PERM_DENIED` |
| BR-06 | 在线留存 3 年；归档冷存 4 年；归档段删除前必须完成冷存校验 | 归档任务 | 校验失败中止删除 |
| BR-07 | 审计检索结果不含意见原文（仅哈希）——意见只在业务时间线与导出中可见 | Serializer | — |
| BR-08 | 双写预留：事件写入同时发 `audit_event` 消息（Sprint 8 AUTH-010 消费建全站表）；消费方缺席不影响本表 | on_commit | — |
| BR-09 | request_id 贯穿：每条审计事件记录产生它的请求 request_id（INFRA-004 中间件注入） | 服务 | — |
| BR-10 | 篡改检测抽查每日执行；全量重放工具随运维手册交付 | beat | — |
| BR-11 | 实例/票据删除仅一种合法路径：项目删除级联（级联前先归档导出） | 级联服务 | — |
| BR-12 | 审计页访问频率限制（INFO 级即可，但导出端点限流 10 次/时/人） | Throttle | `429 RATE_LIMIT_EXCEEDED` |

### 2.5 异常处理

| 场景 | HTTP | 错误码 | details 子码 | 前端表现 |
| --- | --- | --- | --- | --- |
| 无 `approval.audit` 访问审计页 | 403 | `PERM_DENIED` | — | 入口隐藏 |
| 导出超频率 | 429 | `RATE_LIMIT_EXCEEDED` | — | Retry-After 倒计时 |
| 导出时间窗超 1 年 | 400 | `VALIDATION_ERROR` | `RANGE_TOO_LARGE` | 缩短窗口提示 |
| 哈希链断裂后尝试导出 | 409 | `RESOURCE_CONFLICT` | `AUDIT_FROZEN` | 「审计完整性校验中，导出暂不可用」 |
| 归档任务冷存校验失败 | —（异步） | 告警 | — | 运维告警，删除中止 |
| 检索参数非法（时间倒置等） | 400 | `VALIDATION_ERROR` | `INVALID` | 行内提示 |

---

## 3. UI/UX 设计

### 3.1 审计检索页（项目设置 · 审批审计）

```
┌──────────────────────────────────────────────────────────────────────┐
│ 项目设置 / 审批审计                  [导出 CSV]（限 10 次/时）        │
│ 筛选：[审批人 ▾] [动作 ▾] [流转 ▾] [时间 2026-08-01 ~ 2026-09-01]    │
├──────────────────────────────────────────────────────────────────────┤
│ 时间        任务       流转        审批人   动作    耗时   意见       │
│ 09-07 09:15 PROJ-128  提交评审     赵六     ✓通过  2.1h  [查看]      │
│ 09-06 15:02 PROJ-128  提交评审     韩梅     ✓通过  4.7h  [查看]      │
│ 09-06 10:22 PROJ-128  提交评审     —        ◉立案   —    —           │
│ 09-05 17:40 PROJ-117  上线审批     系统     ⊘跳票   —    自审跳过     │
│ …（游标加载更多）                                                   │
│ ──────────────────────────────────────────────────────────────────── │
│ 完整性：✓ 哈希链校验通过（今日 04:00 抽查 1000 条）                   │
└──────────────────────────────────────────────────────────────────────┘
```

| 元素 | 行为 |
| --- | --- |
| 意见「查看」 | 审计页内仅显示哈希与「存在意见」标记；点击跳业务时间线（BR-07 权限继承业务侧） |
| 导出 | 弹窗确认当前筛选条件与预估行数；>1 万行提示异步「生成后通知下载」 |
| 完整性徽标 | 展示最近一次链校验结果与时间；断裂时红色横幅 + 导出禁用 |

### 3.2 工作空间级检索（WS_ADMIN）

项目设置同构页面放大到 `/admin/approvals/audit/`：增加「项目」筛选列与跨项目同人聚合视图（「王一 近 90 天审批 47 次 · 平均耗时 3.2h」——Sprint 9 报表复用此聚合查询）。

---

## 4. 技术架构

### 4.1 审计事件模型

```python
class ApprovalAuditEvent(models.Model):
    """审批域审计事件（BR-01 只增；哈希链篡改检测）"""

    id = models.BigAutoField(primary_key=True)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="+")
    type = models.CharField(max_length=32)                       # §2.1 清单
    instance = models.ForeignKey("ApprovalInstance", null=True, on_delete=models.SET_NULL,
                                 related_name="+")
    actor = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+",
                              help_text="NULL=系统（超时/跳票/归档）")
    payload = models.JSONField(default=dict)                     # 含 comment_sha256（BR-04）
    request_id = models.CharField(max_length=32, blank=True, default="")   # BR-09
    prev_hash = models.CharField(max_length=64)
    event_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "approval_audit_events"
        indexes = [
            models.Index(fields=["project", "created_at"], name="idx_aae_project_time"),
            models.Index(fields=["project", "actor", "created_at"], name="idx_aae_actor"),
            models.Index(fields=["instance"], name="idx_aae_instance"),
            models.Index(fields=["created_at"], name="idx_aae_retention"),
        ]
```

### 4.2 不可变触发器（迁移内 DDL）

```sql
-- records：有限状态机列级白名单
CREATE OR REPLACE FUNCTION trg_approval_records_guard() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'approval_records is append-only';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    IF OLD.action <> 'pending'
       OR NEW.action NOT IN ('approve','reject','skipped')
       OR NEW.level <> OLD.level OR NEW.approver_id <> OLD.approver_id
       OR NEW.instance_id <> OLD.instance_id THEN
      RAISE EXCEPTION 'illegal mutation on approval_records';
    END IF;
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER approval_records_guard BEFORE UPDATE OR DELETE
  ON approval_records FOR EACH ROW EXECUTE FUNCTION trg_approval_records_guard();

-- audit events：绝对只增 + 链式连续
CREATE OR REPLACE FUNCTION trg_aae_guard() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'approval_audit_events is append-only';
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER aae_no_mutation BEFORE UPDATE OR DELETE
  ON approval_audit_events FOR EACH ROW EXECUTE FUNCTION trg_aae_guard();
```

> 归档删除走**专用归档角色**（`rp_archiver`，持有 `BYPASSRLS` 且触发器对该角色 `DISABLE` 后由归档函数以 `SECURITY DEFINER` 执行）——BR-06 冷存校验通过才可删，删除窗口即审计事件（`audit.archived`）。

### 4.3 事件写入服务

```python
class ApprovalAuditor:
    """WF-002 各动作点的留痕挂接（BR-03 唯一入口；与业务写入同事务）"""

    @staticmethod
    def emit(*, project, type: str, actor, instance=None, payload: dict,
             request) -> None:
        prev = (ApprovalAuditEvent.objects.filter(project=project)
                .order_by("-id").values_list("event_hash", flat=True).first() or GENESIS_HASH)
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        event_hash = hashlib.sha256(f"{prev}{canonical}".encode()).hexdigest()
        ApprovalAuditEvent.objects.create(
            project=project, type=type, actor=actor, instance=instance,
            payload=with_comment_hash(payload), request_id=getattr(request, "id", ""),
            prev_hash=prev, event_hash=event_hash)
        transaction.on_commit(lambda: emit_audit_bus.delay(          # BR-08 双写预留
            topic="approval.audit", project_id=str(project.id), type=type))
```

> 链头读取与插入同事务（`select_for_update` 项目级链尾行或项目 advisory lock），并发立案时串行化——审批是低频写（峰值 < 10/s/项目），串行成本可忽略。

### 4.4 导出与归档任务

```python
@app.task(queue="workflow")
def export_approval_audit(project_id: str, filters: dict, exporter_id: str):
    rows = ApprovalAuditEvent.objects.filter(project_id=project_id, **compile(filters))
    if rows.count() > 100_000:
        raise ValidationError("RANGE_TOO_LARGE")
    path = f"audit-exports/{project_id}/{uuid7()}.csv"
    with minio_put_stream(path) as w:                            # 流式写，不落磁盘
        write_csv(rows.iterator(chunk_size=2000), w, merge_comments=True)  # BR-04 合并原文
    ApprovalAuditor.emit(type="audit.exported", actor_id=exporter_id,
                         payload={"filters": filters, "rows": rows.count(), "file": path})
    notify(exporter_id, "audit_export_ready", {"url": presign(path, expires=86400)})

@app.task(queue="workflow")
def verify_hash_chains():
    """beat 每日 04:00：每项目末 1000 条重算（BR-10）"""
    for project_id in active_project_ids():
        if not verify_chain_tail(project_id, depth=1000):
            freeze_exports(project_id)
            alert_p0("approval audit hash chain broken", project_id)
```

### 4.5 API 定义

| 方法 | 路径 | 说明 | 权限 |
| --- | --- | --- | --- |
| GET | `/api/v1/ws/{slug}/projects/{id}/approval-audit/` | 审计检索（组合筛选 + cursor） | `approval.audit` |
| POST | `…/approval-audit/export/` | 触发导出（同步小量 / 异步大量） | `approval.audit`（10 次/时） |
| GET | `/api/v1/ws/{slug}/admin/approvals/audit/` | 工作空间级检索 | WS_ADMIN |
| GET | `…/approval-audit/integrity/` | 链校验状态（最近结果） | `approval.audit` |

**检索响应片段**：

```json
{
  "status": 0,
  "data": {
    "events": [
      {"id": 90211, "type": "record.acted", "created_at": "2026-09-07T09:15:22Z",
       "actor": {"id": "01J7Z…", "name": "赵六"}, "level": 2, "action": "approve",
       "comment_present": true, "comment_sha256": "9f2c…", "request_id": "01J9AM…",
       "issue": {"seq": "PROJ-128", "title": "支付网关联调"}}
    ],
    "integrity": {"status": "ok", "checked_at": "2026-09-07T04:00:11Z"}
  },
  "meta": {"cursor": {"next": "100:1:01J9AM…", "has_more": true}}
}
```

**导出触发响应（异步）**：

```json
{
  "status": 0,
  "data": {"accepted": true, "estimated_rows": 18422,
           "delivery": "notification", "expires_in": 86400}
}
```

### 4.7 检索筛选参数与 CSV 列定义

**检索参数**（`GET …/approval-audit/`）：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `actor` | uuid | 动作人（含系统事件用 `actor=system`） |
| `type` | 逗串 | §2.1 事件类型多选 |
| `action` | 枚举 | approve/reject/skipped/withdrawn/terminated（映射到对应事件组） |
| `transition` | uuid | 按流转边过滤 |
| `issue` | 编号或 uuid | `PROJ-128` 自动解析 |
| `from` / `to` | ISO 日期 | 闭区间；窗口 > 1 年仅导出场景拒绝，检索允许但提示 |
| `cursor` | 游标 | `"100:1:<last_id>"` 规范 |
| `order_by` | `-created_at`（默认）/`created_at` | 白名单两项 |

**CSV 列定义**（导出，19 列固定序）：

| 列 | 来源 | 备注 |
| --- | --- | --- |
| event_time | `created_at`（ISO8601 带时区） | — |
| project | 项目名 + key | — |
| issue_seq / issue_title | 任务编号/标题 | 标题截断 120 字符 |
| transition_name | 边名 | 立案/终止事件必填 |
| from_state / to_state | 状态名 | 迁移事件 |
| flow_name / flow_version | 流快照 | — |
| level | 级别 | 动作事件 |
| actor_name / actor_email | 动作人 | 系统事件为 `system` |
| action | 动作 | 中文枚举值 |
| comment | 意见原文（BR-04 合并自 records） | 含换行转义 |
| duration_hours | 级别耗时（动作时间 − 级别开启时间） | 一位小数 |
| skip_reason / terminal_reason | 跳票/终止原因 | — |
| guard_rerun_result | 终审重跑结果 | completed 事件 |
| request_id | 请求关联 | — |
| event_hash | 链哈希 | 审计自校验用途 |

### 4.8 归档函数与冷存格式

```sql
-- 月度归档：在线段 → 冷存（由 rp_archiver 角色执行，BR-06/11）
CREATE OR REPLACE FUNCTION archive_approval_audit(cutoff timestamptz)
RETURNS TABLE(project_id uuid, archived_count bigint) AS $$
DECLARE
  proj uuid; cnt bigint;
BEGIN
  FOR proj IN SELECT DISTINCT aae.project_id FROM approval_audit_events aae
              WHERE aae.created_at < cutoff LOOP
    -- 1. 冷存校验（Parquet 已在应用层上传并回读校验 MD5）
    PERFORM assert_cold_storage_verified(proj, cutoff);
    -- 2. 删除在线段（触发器对 rp_archiver DISABLE，本函数 SECURITY DEFINER）
    DELETE FROM approval_audit_events
      WHERE project_id = proj AND created_at < cutoff;
    GET DIAGNOSTICS cnt = ROW_COUNT;
    -- 3. 归档本身入审计（归档事件永不归档——created_at 为当前）
    INSERT INTO approval_audit_events(project_id, type, payload, prev_hash, event_hash, created_at)
      VALUES (proj, 'audit.archived',
              jsonb_build_object('cutoff', cutoff, 'rows', cnt),
              chain_head(proj), sha256_of(proj, cutoff, cnt), now());
    RETURN QUERY SELECT proj, cnt;
  END LOOP;
END; $$ LANGUAGE plpgsql SECURITY DEFINER;
```

**冷存格式**：`minio://rp-audit-archive/{project_id}/{yyyy-mm}/approval-audit.parquet`（Snappy 压缩；含 event_hash 列，重放校验工具 `tools/audit-replay.py` 可离线重建链验证）+ 同目录 `manifest.json`（行数/MD5/链头尾哈希）。records 业务票同步归档为 `approval-records.parquet`（含意见原文——冷存即最终明文归宿）。

### 4.9 典型事件 payload 示例

```json
// record.acted
{"level": 2, "action": "approve", "comment_sha256": "9f2c4a…",
 "pass_mode": "all", "level_progress": "2/3", "duration_minutes": 126}

// instance.completed
{"from_state": "开发中", "to_state": "评审中", "guard_rerun": "passed",
 "engine_duration_ms": 41}

// instance.terminated
{"reason": "guard_failed_at_complete", "guard_failures": ["required_fields:assignees"],
 "terminated_by": "system"}

// audit.exported
{"filters": {"from": "2026-06-01", "to": "2026-09-01", "action": "approve"},
 "rows": 18422, "file": "audit-exports/01J8P…/01J9Q….csv"}
```

### 4.6 性能预算

| 路径 | 预算 | 手段 |
| --- | --- | --- |
| 审计检索 | P95 < 200ms（百万行项目） | 组合索引 + cursor |
| 事件写入 | +3ms/动作 | 同事务单 INSERT + 链头缓存 |
| 链校验（1000 条） | < 2s/项目 | 顺序扫描重算 |
| 导出 10 万行 | < 60s | 流式 CSV + iterator |

---

## 5. 测试用例

### 5.1 单元测试

| 编号 | 用例 | 断言 |
| --- | --- | --- |
| UT-01 | records 合法迁移（pending→approve） | 成功 |
| UT-02 | records 非法 UPDATE（改 level/二次动作/改审批人） | 触发器异常 |
| UT-03 | records DELETE 拒绝 | 触发器异常 |
| UT-04 | audit events UPDATE/DELETE 拒绝 | 触发器异常 |
| UT-05 | 哈希链计算确定性 | 同 payload 同 prev 得同 hash |
| UT-06 | 断链检测：删中间行后校验失败 | verify 返回 false + 冻结 |
| UT-07 | 意见哈希化 | payload 无原文，sha256 与原文比对一致 |
| UT-08 | 并发立案链串行 | 100 并发事件链无分叉 |
| UT-09 | 导出限流 10 次/时 | 第 11 次 429 |
| UT-10 | 导出时间窗 >1 年拒绝 | `RANGE_TOO_LARGE` |
| UT-11 | 归档前冷存校验失败中止删除 | 在线段保留 + 告警 |
| UT-12 | 终态实例 UPDATE 拒绝 | 触发器异常 |

### 5.2 集成测试

| 编号 | 用例 | 断言 |
| --- | --- | --- |
| IT-01 | 审批全链路事件完整性（五场景） | §2.1 事件序列与 WF-002 动作一一对应 |
| IT-02 | 导出全链路：触发 → 文件生成 → 通知 → 下载 | 导出与下载各入一条审计 |
| IT-03 | 双写总线 | `approval.audit` 消息发出且可被 AUTH-010 消费端接收 |
| IT-04 | 每日链校验 beat | 抽查通过记录 integrity；注入断链后 P0 告警 + 导出冻结 |
| IT-05 | 归档月度任务 | 3 年前数据导出冷存 + 校验 + 在线删除 + `audit.archived` 事件 |
| IT-06 | request_id 贯穿 | 审计事件 request_id 与请求日志中间件一致 |
| IT-07 | 项目删除级联 | 先归档导出再删除，留归档指针 |
| IT-08 | 权限矩阵 | 成员/PROJ_ADMIN/WS_ADMIN 三视角可见性 |

### 5.3 E2E 测试

| 编号 | 场景 | 断言 |
| --- | --- | --- |
| E2E-01 | 审计页检索组合筛选 + 游标翻页 | 结果与后端一致 |
| E2E-02 | 导出 CSV：筛选 → 导出 → 下载 → 内容抽检 | 行数与筛选一致；审计新增两条 |
| E2E-03 | 完整性徽标正常/断裂两态 | 断裂时导出禁用横幅 |
| E2E-04 | 无权限用户无入口、直连 403 | 权限面 |
| E2E-05 | 业务时间线与审计页同事件双视角一致 | 票据与审计对账 |
| E2E-06 | 工作空间级同人聚合检索 | 跨项目数据正确 |

---

## 6. 竞品深度对标

### 6.1 Ones / 钉钉 留痕分析

| 观察点 | Ones | 钉钉审批 | 本系统 |
| --- | --- | --- | --- |
| 记录不可变 | 应用层约定（DBA 可改） | 云端黑盒 | **DB 触发器 + 哈希链**，篡改可检测 |
| 导出审计 | 导出本身留痕不明 | 有下载记录 | 导出与下载双留痕（BR-05） |
| 留存 | 未公开 | 3 年 | 在线 3 年 + 冷存 4 年（对齐并超出） |
| 意见保护 | 明文随记录 | 明文 | 审计层哈希隔离，导出才合并（BR-04） |

### 6.2 Jira Auditing 分析

| 观察点 | Jira 做法 | 本系统决策 |
| --- | --- | --- |
| 审计范围 | 管理配置操作为主，业务审批记录分离 | 审批域专表 + Sprint 8 全站表双写统一检索 |
| 留存分级 | Data Center 可配 retention | 在线/冷存两级对齐 |
| 完整性 | 无篡改检测 | 哈希链差异化（合规审计可直接演示） |

### 6.3 本系统设计决策汇总

1. **应用层 WORM**：触发器 + 哈希链达到 WORM 存储的审计效果，无需专用硬件/对象锁——私有化部署合规可演示。
2. **业务票据与审计事件分离**：records 面向业务（审批中心/时间线），audit 面向合规（检索/导出/留存）——索引与权限各自优化，互不拖累。
3. **导出即审计对象**：导出是数据离域动作，其本身必须留痕——多数竞品只做「记录查询」不做「出口管控」。

---

## 7. 里程碑与验收

### 7.1 交付物清单

| 类别 | 产物 |
| --- | --- |
| Model / Migration | `approval_audit_events` 表、records/audit 触发器 DDL、归档角色与函数 |
| 后端 | `ApprovalAuditor`（链式写入）、导出 worker（流式 CSV）、链校验 beat、月度归档任务、双写总线预留 |
| API | 项目检索/导出/完整性 + 工作空间检索共 4 端点 |
| 前端 | 项目审批审计页、完整性徽标、导出确认流、工作空间级检索页 |
| 测试 | UT-01~12、IT-01~08、E2E-01~06 |

### 7.2 可操作演示的验收标准

1. 五审批场景（WF-002 验收）执行后，审计页事件序列与时间线逐条对账一致，request_id 可关联请求日志。
2. 篡改演示：DBA 直连删除一条历史审计事件 → 次日链校验（或手动触发）报警 P0、导出冻结、完整性徽标红色。
3. records 篡改演示：直连 UPDATE 已动作票据被触发器拒绝。
4. 导出：1.8 万行异步导出 → 通知下载 → CSV 抽检一致；审计新增 `audit.exported` 与下载记录。
5. 意见保护：审计检索响应不含意见原文（仅哈希），导出含原文。
6. 归档：构造 3 年前数据，月度任务导出冷存、校验、在线删除、`audit.archived` 留痕；冷存校验失败时删除中止。
7. 性能：百万行项目检索 P95 < 200ms；链校验 1000 条 < 2s。