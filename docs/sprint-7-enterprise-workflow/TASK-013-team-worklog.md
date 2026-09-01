# TASK-013 团队工时统计与工时管控

| 元信息项 | 内容 |
| --- | --- |
| 文档编号 | TASK-013 |
| 所属迭代 | Sprint 7 — 企业工作流核心（第 9-10 周） |
| 模块 | M4-TASK 任务核心（工时体系） |
| 优先级 | P3（企业版核心） |
| 工作量估算 | 后端 3.5 人日（审批模型 1.5 + 台账聚合 1 + 预警 1）｜前端 2.5 人日（台账视图 1 + 审批界面 1 + 预警入口 0.5）｜测试 1.5 人日 |
| 关联架构文档 | [`unified-issue-model.md`](../architecture/unified-issue-model.md)、[`api-conventions.md`](../architecture/api-conventions.md)、[`rbac-permission-model.md`](../architecture/rbac-permission-model.md) |
| 上游依赖 | `TASK-006`（WorkLog 模型与 `idx_worklog_actor_day` 人员×日期索引——**为本文档预留**）；`TASK-010`（Activity 幂等管道）；`COLLAB-001`（预警通知通道）；`WF-002`（审批语义对齐——工时审批为轻量单级审批，不复用 ApprovalFlow 引擎） |
| 下游消费 | `RPT-004`（团队负载热力图 × 四维评分卡的工时不偏差维度直接消费台账快照）；P4 计费/成本 |
| 文档状态 | 待评审（Draft） |
| 最后更新日期 | 2026-09-01 |

---

## 1. 概述

### 1.1 背景

`TASK-006` 交付了个人工时填报：一分钟粒度记录、`estimate_minutes` 预估、子树上卷汇总。企业场景缺的是**管控闭环**——团队负责人要回答三个问题：「大家的工时填了吗（填报规范）、填得对吗（审批）、项目工时超了吗（预警）」。

TASK-013 补齐四块：

1. **工时审批**：成员按「周 × 项目」打包提交工时，负责人通过/驳回（附意见），驳回后修改再提交，通过后锁定——这是工时数据进入考核/结算前的质量门禁。
2. **填报规范**：每日上限、最小粒度、回填窗口（承 TASK-006 30 天）、周五未交提醒——规范前置到录入与提醒，而非事后追责。
3. **超额预警**：任务 `spent/estimate` 越过 80%/100% 阈值即预警到负责人收件箱；人日 >8h 标红。
4. **团队台账**：按「人 × 周」预聚合快照表，台账页与 `RPT-004` 负载热力图共用同一数据源——报表不扫明细表。

### 1.2 目标

- `WorkLogApproval` 审批批次模型：成员 × 项目 × 自然周 一个批次，四态流转（draft → submitted → approved / rejected → submitted…）。
- 审批通过的工时被锁定（不可改不可删），驳回批次内工时可修改后整批重交。
- `WorkLogSummary` 快照表：`(project, actor, week_start)` 增量刷新、审批通过冻结，台账/报表查询 P95 < 200ms。
- 预警三通道：任务超额（阈值可配）、人日超标、周五未交——全部走 `COLLAB-001` 收件箱，幂等不轰炸。

### 1.3 范围与边界

| 范围 | 本文档交付 | 明确不做（归属） |
| --- | --- | --- |
| 工时审批 | 单级审批（提交/驳回/通过/重交）、通过后锁定 | 多级审批链（如有需要走 `WF-002` 引擎另配）、审批委派（P4） |
| 填报规范 | 日上限/粒度/回填窗口校验、未交提醒 | 强制打卡/考勤（非目标） |
| 预警 | 任务超额阈值（项目级可配）、人日 >8h、周五未交提醒 | 成本/计费预警（P4） |
| 台账 | 人×周快照表 + 台账查询/导出 API | 跨项目资源调度（P4 `PROJ-004` 之上） |

### 1.4 术语表

| 术语 | 定义 |
| --- | --- |
| 审批批次（Batch） | 某成员在某项目某自然周（周一~周日）全部工时记录的集合，`WorkLogApproval` 承载 |
| 锁定 | 批次 `approved` 后，覆盖的 WorkLog 不可修改/删除（`409 RESOURCE_LOCKED`） |
| 台账 | 按人×周的工时汇总视图，数据源为 `WorkLogSummary` 快照 |
| 快照冻结 | 批次 approved 后对应摘要行 `is_frozen=true`，后续明细变更（仅允许驳回重交流程内）须先解冻 |

### 1.5 前置依赖

| 依赖 | 内容 | 阻塞原因 |
| --- | --- | --- |
| `TASK-006` §4 | `WorkLog`（actor/worked_on/minutes 1-1440/note）、`Issue.estimate_minutes`、`idx_worklog_actor_day` 索引 | 审批覆盖的对象与台账直查索引均为既有 |
| `TASK-010` | Activity 幂等管道 + 事件扇出 | 审批动作留痕 |
| `COLLAB-001` | 收件箱通知 | 预警与审批提醒通道 |
| `rbac-permission-model.md` | `issue.worklog.review` 权限码（PROJ_ADMIN+，可经 Sprint 8 自定义角色授予） | 审批人判定 |

### 1.6 竞品参考

| 竞品 | 参考点 | 处置 |
| --- | --- | --- |
| Jira (Tempo) | Timesheet 按周期提交/审批、锁定、团队台账 | 周期提交/审批/锁定范式采纳；**Tempo 的独立账户体系不学**（审批挂在项目角色上） |
| Ones | 工时审批 + 填报规范（日上限/回填限制）+ 负载统计 | 规范项对齐；台账「人×周」粒度对齐 |
| Plane | 无工时体系（2026-09） | 工时域整体为差异化能力 |

---

## 2. 业务逻辑

### 2.1 总体流程

```mermaid
flowchart TB
    subgraph DAILY["日常填报（TASK-006 既有）"]
        A1["成员填报工时<br/>（规范校验：日上限/粒度/回填窗）"]
    end
    subgraph WEEKLY["周审批闭环"]
        B1["周五汇总本周批次<br/>draft"] --> B2["成员提交 submitted"]
        B2 --> B3{"负责人审批"}
        B3 -->|通过| B4["approved<br/>工时锁定 + 快照冻结"]
        B3 -->|驳回（附意见）| B5["rejected<br/>成员修改明细"]
        B5 --> B2
    end
    subgraph ALERT["预警"]
        C1["任务 spent/estimate ≥ 阈值"] --> C4["负责人收件箱"]
        C2["人日 > 8h"] --> C4
        C3["周五 17:00 未提交"] --> C5["成员收件箱提醒"]
    end
    subgraph LEDGER["台账"]
        B4 -.冻结.-> D1["WorkLogSummary<br/>人×周快照"]
        A1 -.增量刷新.-> D1
        D1 --> D2["台账页 / RPT-004 负载热力"]
    end
    A1 --> B1
```

### 2.2 业务规则（BR）

| 编号 | 规则 | 强制层 | 违约响应 |
| --- | --- | --- | --- |
| BR-01 | 批次维度 = `(actor, project, week_start)`（周一 00:00 本地时区）；同维度至多一个非删除批次 | DB 唯一约束 | `409 RESOURCE_ALREADY_EXISTS` |
| BR-02 | 批次四态：`draft → submitted → approved`；`submitted → rejected → submitted`（可循环）；`approved` 终态 | 状态机（Service） | `409 RESOURCE_STATE_INVALID` |
| BR-03 | 提交时批次须非空（覆盖周内有 ≥1 条工时） | Service | `400 VALIDATION_ERROR` |
| BR-04 | 审批人 = 持 `issue.worklog.review`（默认 PROJ_ADMIN+）；不可审批自己的批次 | Permission + Service | `403 PERM_DENIED` / `400 VALIDATION_ERROR` |
| BR-05 | 驳回必填意见（≤500 字） | Serializer | `400 VALIDATION_ERROR` + `REQUIRED` |
| BR-06 | `approved` 批次覆盖的 WorkLog 锁定：改/删 → `409 RESOURCE_LOCKED`；新增**该周**工时须入新周期或解冻（BR-07） | WorkLog Service 钩子 | 409 |
| BR-07 | 解锁唯一路径：负责人撤销审批（`approved → rejected`，必填意见）——审计保留完整轨迹 | Service | — |
| BR-08 | 填报规范：单日单人累计 ≤1440 分钟（DB 既有约束外的软上限 480 分钟可配，超出仅警告不拦截）；单笔粒度 ∈ {15, 30, 60} 分钟倍数可配（默认 15） | Service + 项目配置 | 警告入响应 `meta.warnings[]` |
| BR-09 | 回填窗口承 TASK-006（30 天）；审批通过后回填窗口对该周失效（锁定优先） | Service | `400 VALIDATION_ERROR` |
| BR-10 | 任务超额预警：`spent/estimate ≥ warn_ratio`（默认 0.8，项目可配 0.5-1.5）触发一次；≥1.0 再触发一次；`estimate` 变更重置已触发标记 | Celery on_commit + Redis SETNX 幂等 | — |
| BR-11 | 人日 >8h（480 分钟）在台账行标红，不产生通知（噪音控制） | 台账渲染 | — |
| BR-12 | 周五 17:00（项目时区）未提交批次 → 提醒成员；下周一 10:00 仍未交 → 提醒负责人；每周至多各一条（幂等键含周期） | Celery beat + SETNX | — |
| BR-13 | 快照表只增改不删：明细变更（驳回重交流程内）触发对应 `(project, actor, week)` 行重算；`is_frozen` 行拒绝重算（须先 BR-07 解锁） | 聚合任务 | — |
| BR-14 | 台账可见性：成员只见自己；负责人（`issue.worklog.review`）见项目全员；导出（CSV）需同一权限且入审计（Sprint 8 `AUTH-010` 挂接点预留） | Permission | `403 PERM_DENIED` |
| BR-15 | 项目归档/成员移出：批次与快照保留（历史可审计），新项目周期不可再提交 | Service | `403 PERM_PROJECT_ARCHIVED` |

### 2.3 审批时序

```mermaid
sequenceDiagram
    participant M as 成员
    participant API as WorkLogApprovalView
    participant SVC as ApprovalService
    participant DB as PostgreSQL
    participant R as 负责人
    M->>API: POST …/worklog-approvals/submit/ {project, week_start}
    API->>SVC: submit(actor, project, week)
    SVC->>DB: INSERT WorkLogApproval(submitted)<br/>（唯一约束兜底并发重复提交）
    SVC->>DB: on_commit → 通知负责人（COLLAB-001）
    API-->>M: 200 + batch
    R->>API: POST …/worklog-approvals/{id}/approve/ 或 /reject/ {note}
    API->>SVC: 状态机流转 + 审批人校验（BR-04）
    alt approve
        SVC->>DB: 批次 approved；UPDATE work_logs SET locked<br/>UPDATE summary SET is_frozen=true
    else reject
        SVC->>DB: 批次 rejected + note；工时解锁可改
    end
    SVC->>DB: Activity 留痕（TASK-010 管道）
    API-->>R: 200
```

---

## 3. UI/UX 设计

### 3.1 我的工时周视图（成员侧）

```
┌────────────────────────────────────────────────────────────────────────┐
│ 我的工时 · 2026 第 36 周（08-31 ~ 09-06）        批次状态: ●已提交       │
├────────────────────────────────────────────────────────────────────────┤
│ 任务                周一   周二   周三   周四   周五   周六   周日   合计 │
│ ────────────────────────────────────────────────────────────────────  │
│ RBT-128 审批模型     2.0h   3.5h   4.0h   3.0h   2.0h    —     —   14.5h│
│ RBT-131 台账聚合      —     2.0h   2.5h   4.0h   3.5h    —     —   12.0h│
│ RBT-140 预警联调      —      —      —     1.0h   2.5h    —     —    3.5h│
│ ────────────────────────────────────────────────────────────────────  │
│ 日合计              2.0h   5.5h   6.5h   8.0h†  8.0h†   —     —   30.0h│
│ † 达日上限 8h（软上限，仅提示）                                          │
├────────────────────────────────────────────────────────────────────────┤
│ 审批记录: 09-04 17:23 提交 → 等待 张妍 审批                              │
│                                          [撤回提交]      [查看驳回意见]  │
└────────────────────────────────────────────────────────────────────────┘
```

### 3.2 负责人审批队列

```
┌────────────────────────────────────────────────────────────────────────┐
│ 工时审批 · 电商重构项目 · 2026 第 36 周                 待审 3 / 共 12     │
├────────────────────────────────────────────────────────────────────────┤
│ 成员      提交时间        工时   任务数   日分布            操作          │
│ ────────────────────────────────────────────────────────────────────  │
│ 李骁      09-04 17:23    30.0h   3      ▂▅▇█†█†··      [通过] [驳回]    │
│ 王思远    09-04 16:40    26.5h   5      ▄▅▅▆▇··       [通过] [驳回]    │
│ 陈默      09-03 09:12    22.0h   2      ▃▄▄▄▅··       ✓ 已通过 09-04   │
│ ────────────────────────────────────────────────────────────────────  │
│ ▼ 展开李骁明细：逐任务逐日工时 + 备注（驳回须填意见）                       │
│   驳回意见: [_________________________________]  [取消] [确认驳回]       │
└────────────────────────────────────────────────────────────────────────┘
```

### 3.3 团队台账（人 × 周矩阵）

```
┌────────────────────────────────────────────────────────────────────────┐
│ 团队台账 · 电商重构项目      [按周▾]  2026-08 ~ 2026-09    [导出 CSV]    │
├────────────────────────────────────────────────────────────────────────┤
│ 成员     W33    W34    W35    W36    合计    审批率   负载°              │
│ ────────────────────────────────────────────────────────────────────  │
│ 李骁     38.0   40.0   36.5   30.0   144.5   100%    ▓▓▓▓▓▓▓▓░░ 75%    │
│ 王思远   40.0   42.5†  39.0   26.5   148.0    75%    ▓▓▓▓▓▓▓▓▓░ 82%    │
│ 陈默     32.0   35.0   33.0   22.0   122.0   100%    ▓▓▓▓▓▓░░░░ 61%    │
│ ────────────────────────────────────────────────────────────────────  │
│ † 含 >8h 人日（悬停查看明细）  ° 负载 = 周工时/40h（RPT-004 热力图同源）   │
└────────────────────────────────────────────────────────────────────────┘
```

### 3.4 交互规则

| 场景 | 行为 |
| --- | --- |
| 单元格填报 | 点击单元格弹输入（数字 + 备注），粒度校验即时提示；锁定周的单元格只读 + 🔒 |
| 撤回提交 | `submitted` 且未审 → 可撤回为 draft（BR-02 状态机允许 `submitted → draft` 仅撤回路径） |
| 驳回修改 | 驳回批次行标红 + 意见气泡；修改明细后「重新提交」 |
| 超额预警 | 任务详情预估旁显示进度环（spent/estimate），≥80% 橙、≥100% 红；收件箱预警可点击直达任务 |
| 台账导出 | CSV（UTF-8 BOM），列 = 成员×周矩阵；导出动作入审计（Sprint 8 挂接） |

---

## 4. 技术架构

### 4.1 实体关系

```mermaid
erDiagram
    PROJECT ||--o{ WORKLOG_APPROVAL : has
    USER ||--o{ WORKLOG_APPROVAL : "actor（被审人）"
    USER ||--o{ WORKLOG_APPROVAL : "reviewer（审批人）"
    WORKLOG_APPROVAL ||--o{ WORKLOG : "covers（同 actor+project+周）"
    PROJECT ||--o{ WORKLOG_SUMMARY : has
    USER ||--o{ WORKLOG_SUMMARY : actor
    WORKLOG_APPROVAL {
        uuid id PK
        uuid project_id FK
        uuid actor_id FK
        date week_start "周一"
        string status "draft/submitted/approved/rejected"
        text review_note "驳回/撤销必填"
        uuid reviewer_id FK
        datetime submitted_at
        datetime reviewed_at
    }
    WORKLOG_SUMMARY {
        uuid id PK
        uuid project_id FK
        uuid actor_id FK
        date week_start
        int total_minutes
        int task_count
        int approved_minutes "已审批部分"
        bool is_frozen
    }
```

### 4.2 模型定义

```python
class WorkLogApproval(BaseModel):
    """工时审批批次 —— 成员 × 项目 × 自然周（BR-01）

    轻量单级审批：刻意不复用 WF-002 ApprovalFlow 引擎（会签/逐级对周工时属过度设计），
    但状态机语义与审批留痕规范与 WF-002/WF-006 对齐，便于未来迁移。
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "待提交"
        SUBMITTED = "submitted", "待审批"
        APPROVED = "approved", "已通过"
        REJECTED = "rejected", "已驳回"

    # 合法流转（BR-02）：draft→submitted；submitted→approved/rejected/draft(撤回)；rejected→submitted
    ALLOWED_TRANSITIONS = {
        Status.DRAFT: {Status.SUBMITTED},
        Status.SUBMITTED: {Status.APPROVED, Status.REJECTED, Status.DRAFT},
        Status.REJECTED: {Status.SUBMITTED},
        Status.APPROVED: {Status.REJECTED},  # BR-07 撤销审批（仅负责人，必填意见）
    }

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="worklog_approvals", verbose_name="所属项目"
    )
    actor = models.ForeignKey(
        "db.User", on_delete=models.CASCADE, related_name="worklog_approvals", verbose_name="被审人"
    )
    week_start = models.DateField(verbose_name="周起始日（周一）")
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True, verbose_name="状态"
    )
    review_note = models.CharField(max_length=500, blank=True, verbose_name="审批意见（驳回/撤销必填）")
    reviewer = models.ForeignKey(
        "db.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reviewed_worklog_approvals", verbose_name="审批人"
    )
    submitted_at = models.DateTimeField(null=True, blank=True, verbose_name="最近提交时间")
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name="最近审批时间")

    class Meta(BaseModel.Meta):
        db_table = "worklog_approvals"
        constraints = [
            models.UniqueConstraint(
                fields=["actor", "project", "week_start"],
                condition=models.Q(deleted_at__isnull=True),
                name="uniq_worklog_approval_batch",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "week_start", "status"], name="idx_wla_project_week"),
        ]


class WorkLogSummary(BaseModel):
    """人×周工时快照（BR-13 只增改不删）——台账与 RPT-004 负载的唯一数据源"""

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="worklog_summaries", verbose_name="所属项目"
    )
    actor = models.ForeignKey(
        "db.User", on_delete=models.CASCADE, related_name="worklog_summaries", verbose_name="成员"
    )
    week_start = models.DateField(verbose_name="周起始日")
    total_minutes = models.PositiveIntegerField(default=0, verbose_name="总工时（分钟）")
    task_count = models.PositiveIntegerField(default=0, verbose_name="涉及任务数")
    approved_minutes = models.PositiveIntegerField(default=0, verbose_name="已审批工时")
    is_frozen = models.BooleanField(default=False, verbose_name="是否冻结（批次通过）")

    class Meta(BaseModel.Meta):
        db_table = "worklog_summaries"
        constraints = [
            models.UniqueConstraint(fields=["project", "actor", "week_start"],
                                    name="uniq_worklog_summary_cell"),
        ]
        indexes = [
            models.Index(fields=["project", "week_start"], name="idx_wls_project_week"),
            models.Index(fields=["actor", "week_start"], name="idx_wls_actor_week"),
        ]
```

**WorkLog 增列**（本迭代唯一 DDL）：`locked = BooleanField(default=False, db_index=True)`——审批通过置真，TASK-006 的改/删 Service 前置检查（BR-06）。

### 4.3 审批服务（状态机 + 锁定）

```python
class WorkLogApprovalService:
    @transaction.atomic
    def submit(self, *, actor, project, week_start) -> WorkLogApproval:
        logs = WorkLog.objects.filter(
            issue__project=project, actor=actor,
            worked_on__range=(week_start, week_start + timedelta(days=6)), locked=False)
        if not logs.exists():                                   # BR-03
            raise ApiError("VALIDATION_ERROR", 400, details={"week": [{"code": "REQUIRED",
                           "message": "本周无可提交工时"}]})
        batch, _ = WorkLogApproval.objects.get_or_create(
            actor=actor, project=project, week_start=week_start,
            defaults={"status": WorkLogApproval.Status.DRAFT})
        self._move(batch, WorkLogApproval.Status.SUBMITTED, by=actor)
        transaction.on_commit(lambda: notify_reviewers.delay(str(batch.id)))   # COLLAB-001
        return batch

    @transaction.atomic
    def review(self, *, batch_id, reviewer, action, note="") -> WorkLogApproval:
        batch = WorkLogApproval.objects.select_for_update().get(pk=batch_id)
        if batch.actor_id == reviewer.id:                       # BR-04 不可自审
            raise ApiError("VALIDATION_ERROR", 400, details={"reviewer": [{"code": "INVALID",
                           "message": "不可审批自己的批次"}]})
        target = {"approve": WorkLogApproval.Status.APPROVED,
                  "reject": WorkLogApproval.Status.REJECTED,
                  "revoke": WorkLogApproval.Status.REJECTED}[action]
        if action in ("reject", "revoke") and not note.strip():  # BR-05/07
            raise ApiError("VALIDATION_ERROR", 400, details={"note": [{"code": "REQUIRED",
                           "message": "驳回/撤销必填意见"}]})
        self._move(batch, target, by=reviewer, note=note)
        if target == WorkLogApproval.Status.APPROVED:
            self._lock_logs(batch, locked=True)
        elif action == "revoke":
            self._lock_logs(batch, locked=False)
        transaction.on_commit(lambda: refresh_summary.delay(      # §4.4 快照重算
            str(batch.project_id), str(batch.actor_id), batch.week_start.isoformat()))
        return batch

    def _move(self, batch, target, *, by, note=""):
        if target not in batch.ALLOWED_TRANSITIONS[batch.status]:
            raise ApiError("RESOURCE_STATE_INVALID", 409, details={
                "current": batch.status, "target": target})
        batch.status, batch.reviewer, batch.review_note = target, by, note
        batch.reviewed_at = timezone.now()
        batch.save(update_fields=["status", "reviewer", "review_note", "reviewed_at", "updated_at"])

    def _lock_logs(self, batch, *, locked: bool):
        WorkLog.objects.filter(
            issue__project=batch.project, actor=batch.actor,
            worked_on__range=(batch.week_start, batch.week_start + timedelta(days=6))
        ).update(locked=locked)
```

### 4.4 快照聚合任务（增量 + 冻结）

```python
@shared_task(queue="reports", max_retries=3, retry_backoff=True)
def refresh_summary(project_id: str, actor_id: str, week_start: str):
    """WorkLog 增删改 / 审批流转后经 on_commit 触发（BR-13）。幂等：全量重算该行。"""
    row = WorkLogSummary.objects.filter(
        project_id=project_id, actor_id=actor_id, week_start=week_start).first()
    if row and row.is_frozen:
        return                                                # 冻结行拒绝重算（须先 BR-07 解锁）
    week_end = date.fromisoformat(week_start) + timedelta(days=6)
    agg = WorkLog.objects.filter(
        issue__project_id=project_id, actor_id=actor_id,
        worked_on__range=(week_start, week_end)
    ).aggregate(total=Coalesce(Sum("minutes"), 0),
                tasks=Count("issue", distinct=True),
                approved=Coalesce(Sum("minutes", filter=Q(locked=True)), 0))
    WorkLogSummary.objects.update_or_create(
        project_id=project_id, actor_id=actor_id, week_start=week_start,
        defaults={"total_minutes": agg["total"], "task_count": agg["tasks"],
                  "approved_minutes": agg["approved"]})


@shared_task(queue="reports")
def check_estimate_alerts(issue_id: str):
    """任务超额预警（BR-10）：WorkLog 变更后 on_commit 触发；SETNX 幂等防轰炸。"""
    issue = Issue.objects.filter(pk=issue_id).values("id", "project_id", "sequence_id",
                                                     "title", "estimate_minutes", "spent_minutes").first()
    if not issue or not issue["estimate_minutes"]:
        return
    ratio = issue["spent_minutes"] / issue["estimate_minutes"]
    cfg = ProjectConfig.objects.get(project_id=issue["project_id"]).worklog_alert_ratio  # 默认 0.8
    for threshold in (cfg, 1.0):
        if ratio >= threshold:
            key = f"worklog:alert:{issue['id']}:{threshold}"
            if cache.set(key, "1", timeout=7 * 86400, nx=True):   # 7 天内同阈值只发一次
                notify_estimate_exceeded.delay(issue_id, threshold)  # 负责人收件箱


@shared_task(queue="reports")
def weekly_submit_reminder():
    """Celery beat：周五 17:00（各项目时区）未交提醒成员；周一 10:00 未交提醒负责人（BR-12）。"""
    week = current_week_start()
    for project in Project.objects.filter(status="active", worklog_approval_enabled=True):
        submitted = WorkLogApproval.objects.filter(
            project=project, week_start=week,
            status__in=["submitted", "approved"]).values_list("actor_id", flat=True)
        for member in project.members_active.exclude(id__in=submitted):
            key = f"worklog:remind:{project.id}:{member.id}:{week}"
            if cache.set(key, "1", timeout=7 * 86400, nx=True):
                notify_submit_reminder.delay(str(member.id), str(project.id), str(week))
```

**台账查询**直接命中 `idx_wls_project_week`：50 人 × 12 周 = 600 行内一次索引扫描，P95 < 200ms（验收标准 3）。RPT-004 负载热力图复用本表（`total_minutes / 2400` = 周负载率），**不另建聚合**——单一数据源承诺。

### 4.5 API 端点

| 方法 | 路径 | 说明 | 权限 |
| --- | --- | --- | --- |
| GET | `…/projects/{id}/worklog-approvals/?week_start=&status=` | 批次列表（负责人看全员；成员仅自己，BR-14） | 项目成员 |
| POST | `…/worklog-approvals/submit/` | 提交 `{project_id, week_start}`（幂等：重复提交返回既有批次） | 成员本人 |
| POST | `…/worklog-approvals/{batch_id}/withdraw/` | 撤回（`submitted→draft`） | 成员本人 |
| POST | `…/worklog-approvals/{batch_id}/approve/` | 通过 | `issue.worklog.review` |
| POST | `…/worklog-approvals/{batch_id}/reject/` | 驳回 `{note}` | 同上 |
| POST | `…/worklog-approvals/{batch_id}/revoke/` | 撤销审批 `{note}`（BR-07） | 同上 |
| GET | `…/projects/{id}/worklog-summary/?from=&to=&actor_ids=` | 台账（人×周矩阵，快照表） | BR-14 |
| GET | `…/projects/{id}/worklog-summary/export/` | CSV 导出（Sprint 8 审计挂接点） | `issue.worklog.review` |

**① 提交响应（200）**：

```json
{
  "status": 0,
  "data": {
    "id": "01J9XQK7M3N4P5R6S7T8V9W0T1",
    "actor": { "id": "01J9XQK7M3N4P5R6S7T8V9W0U2", "display_name": "李骁" },
    "week_start": "2026-08-31",
    "status": "submitted",
    "log_count": 9,
    "total_minutes": 1800,
    "submitted_at": "2026-09-04T09:23:11.482Z"
  },
  "meta": { "request_id": "01J9XQK7M3N4P5R6S7T8V9W0V3" }
}
```

**② 台账响应（200）**：

```json
{
  "status": 0,
  "data": {
    "weeks": ["2026-08-03", "2026-08-10", "2026-08-17", "2026-08-24"],
    "rows": [
      {
        "actor": { "id": "01J9XQK7M3N4P5R6S7T8V9W0U2", "display_name": "李骁", "avatar": "https://cdn.example.com/av/lx.png" },
        "cells": [
          { "week_start": "2026-08-03", "total_minutes": 2280, "task_count": 4, "approved_minutes": 2280, "is_frozen": true, "over_8h_days": 1 },
          { "week_start": "2026-08-10", "total_minutes": 2400, "task_count": 5, "approved_minutes": 2400, "is_frozen": true, "over_8h_days": 2 }
        ]
      }
    ]
  },
  "meta": { "request_id": "01J9XQK7M3N4P5R6S7T8V9W0W4" }
}
```

**③ 错误响应矩阵**：

| 场景 | HTTP | code | details |
| --- | --- | --- | --- |
| 空批次提交 | 400 | `VALIDATION_ERROR` | 子码 `REQUIRED` |
| 重复提交（并发） | 200 | —（幂等返回既有批次） | — |
| 非法状态流转 | 409 | `RESOURCE_STATE_INVALID` | `current`/`target` |
| 审批自己批次 | 400 | `VALIDATION_ERROR` | 子码 `INVALID` |
| 驳回/撤销缺意见 | 400 | `VALIDATION_ERROR` | 子码 `REQUIRED` |
| 修改已锁定工时 | 409 | `RESOURCE_LOCKED` | `batch_id` + `week_start` |
| 无审批权限 | 403 | `PERM_DENIED` | 所需权限码 `issue.worklog.review` |
| 查看他人台账 | 403 | `PERM_DENIED` | BR-14 |
| 归档项目提交 | 403 | `PERM_PROJECT_ARCHIVED` | — |

```json
// 409 RESOURCE_LOCKED 示例
{
  "status": 1,
  "error": {
    "code": "RESOURCE_LOCKED",
    "message": "该工时已随 2026 第 36 周批次通过审批，如需修改请联系负责人撤销审批",
    "details": { "batch_id": "01J9XQK7M3N4P5R6S7T8V9W0T1", "week_start": "2026-08-31", "reviewer": "张妍" }
  },
  "meta": { "request_id": "01J9XQK7M3N4P5R6S7T8V9W0X5" }
}
```

### 4.6 前端实现

```typescript
class TeamWorklogStore {
  @observable weeks: string[] = [];
  @observable rows = observable.map<string, SummaryRow>();   // actor_id → 行
  @observable pendingBatches: WorklogBatch[] = [];

  async fetchSummary(projectId: string, from: string, to: string) {
    // SWR：key `worklog-summary:{project}:{from}:{to}`；审批动作后 mutate
    const res = await api.get(`…/projects/${projectId}/worklog-summary/`, { params: { from, to } });
    runInAction(() => {
      this.weeks = res.data.data.weeks;
      res.data.data.rows.forEach((r: SummaryRow) => this.rows.set(r.actor.id, r));
    });
  }

  @computed loadRateOf(actorId: string, week: string): number {
    const cell = this.rows.get(actorId)?.cells.find(c => c.week_start === week);
    return cell ? cell.total_minutes / 2400 : 0;               // 周负载率（§3.3，RPT-004 同源）
  }

  async review(batchId: string, action: "approve" | "reject" | "revoke", note?: string) {
    await api.post(`…/worklog-approvals/${batchId}/${action}/`, { note });
    runInAction(() => {
      this.pendingBatches = this.pendingBatches.filter(b => b.id !== batchId);
    });
  }
}
```

| 前端要点 | 方案 |
| --- | --- |
| 周视图单元格 | 复用 TASK-006 `WorkLogStore` 填报组件，锁定周置灰 + 🔒（`locked` 标志随 Schema/详情返回） |
| 审批队列 | 行展开明细懒加载（点击才拉批次内 WorkLog 明细）；驳回意见必填前端先行校验 |
| 台账热力 | 负载率 0-60% 绿 / 60-90% 蓝 / 90-100% 橙 / >100% 红（与 RPT-004 热力图同色系规范） |
| 预警入口 | 任务详情预估进度环 + 收件箱跳转锚点 |

---

## 5. 测试用例

### 5.1 单元测试（UT）

| 编号 | 用例 | 断言 |
| --- | --- | --- |
| UT-01 | 状态机全合法路径（draft→submitted→approved；submitted→draft；rejected→submitted；approved→rejected） | 逐条通过 |
| UT-02 | 非法流转（draft→approved、approved→submitted 等 8 组） | 全部 409 `RESOURCE_STATE_INVALID` |
| UT-03 | 空批次提交 | 400 `REQUIRED` |
| UT-04 | 审批自己批次 | 400 `INVALID` |
| UT-05 | 驳回/撤销缺意见 | 400 `REQUIRED` |
| UT-06 | `_lock_logs` 周区间边界（周一 00:00 / 周日 24:00） | 恰覆盖 7 天，邻周不受影响 |
| UT-07 | `refresh_summary` 幂等：同一周重复触发结果一致 | total/tasks/approved 稳定 |
| UT-08 | 冻结行拒绝重算 | 早退，行不变 |
| UT-09 | 超额预警阈值穿越：0.79→0.81 触发一次；0.81→0.85 不重复；≥1.0 二次触发 | SETNX 语义正确 |
| UT-10 | estimate 变更重置预警标记 | 标记清除后可再触发 |
| UT-11 | 周提醒幂等：同周同人只一条 | key 含周期 |
| UT-12 | 批次唯一约束并发提交 | 恰一行，另一请求获既有批次 |
| UT-13 | 台账权限：成员查他人行 | 403 `PERM_DENIED` |

### 5.2 集成测试（IT）

| 编号 | 用例 | 断言 |
| --- | --- | --- |
| IT-01 | 全链路：填报→提交→驳回（意见）→修改→重交→通过→锁定 | 各状态/字段/通知落库正确；快照随每次流转刷新 |
| IT-02 | 锁定后改/删工时 | 409 `RESOURCE_LOCKED` 且 details 含批次信息 |
| IT-03 | 撤销审批→解锁→修改→重交 | 锁定标志翻转正确；快照解冻重算 |
| IT-04 | 台账 API 与明细 SUM 对账（随机 3 项目 × 4 周） | 逐 cell 相等 |
| IT-05 | 预警链路：工时越过 80% → 负责人收件箱一条；再越 100% → 第二条 | 数量与去重正确 |
| IT-06 | 周五 17:00 beat：未交成员收提醒；周一负责人收催办 | 幂等键生效（重复跑 beat 不重复发） |
| IT-07 | 导出 CSV：列/编码（BOM）/权限；导出入审计挂接点（mock AUTH-010） | 内容对账 + 403 路径 |

### 5.3 E2E

| 编号 | 场景 |
| --- | --- |
| E2E-01 | 成员周视图填报 5 天 → 提交 → 负责人队列出现 → 通过 → 单元格锁定 |
| E2E-02 | 驳回→意见气泡→成员修改→重交→通过 全闭环 |
| E2E-03 | 台账页人×周矩阵渲染、>8h 标红、负载条、CSV 导出与页面一致 |
| E2E-04 | 任务 spent 越 80%：进度环变橙 + 负责人收件箱预警点击直达任务 |
| E2E-05 | 审批率统计：4 周 3 周通过 → 台账「审批率 75%」正确 |

---

## 6. 竞品深度对标

| 维度 | Jira + Tempo | Ones | Plane | **本方案** |
| --- | --- | --- | --- | --- |
| 审批粒度 | 独立 timesheet 账户体系，周期提交 | 周批次提交 | 无工时 | 周批次，挂项目角色零新账户体系 |
| 审批后锁定 | 锁定周期（管理员可解锁） | 锁定 | — | 锁定 + **撤销审批唯一解锁路径**（BR-07，审计轨迹完整） |
| 台账数据源 | 明细实时聚合（大团队慢，Tempo 需独立报表模块） | 预聚合 | — | `WorkLogSummary` 增量快照 + 冻结语义，台账/负载同源 |
| 超额预警 | Tempo 插件可配 | 项目级阈值 | — | 项目级阈值 + 双阈值（80%/100%）+ SETNX 防轰炸 |
| 填报规范 | 周期窗口 | 日上限/回填窗 | — | 软上限警告（不阻断真实加班场景）+ 粒度可配 + 回填窗 |

---

## 7. 里程碑与验收

### 7.1 交付清单

| 类别 | 交付物 |
| --- | --- |
| Model / Migration | `worklog_approvals`、`worklog_summaries` 两表 + 2 唯一约束 + 4 索引；`work_logs.locked` 增列（本迭代唯一 DDL） |
| 后端 | `WorkLogApprovalService` 状态机、锁定钩子（TASK-006 改/删前置）、`refresh_summary` / `check_estimate_alerts` / `weekly_submit_reminder` 三 Celery 任务（全部 on_commit + SETNX 幂等）、8 个端点 |
| 前端 | 周视图批次状态与锁定、审批队列、台账矩阵（热力 + 导出）、预估进度环 |
| 测试 | UT-01~13、IT-01~07、E2E-01~05 |

### 7.2 可操作演示的验收标准

1. 全链路演示（Sprint 7 验收清单第 7 条）：提交 → 驳回（附意见）→ 修改 → 通过 → 台账统计，状态机与通知逐步正确。
2. 锁定演示：通过后的工时改/删返回 409 `RESOURCE_LOCKED`；负责人撤销后解锁可改，Activity 留痕完整。
3. 台账性能：50 人 × 12 周查询 P95 < 200ms（快照表索引扫描，EXPLAIN 佐证）；台账与明细 SUM 对账一致（IT-04）。
4. 预警演示：80%/100% 双阈值各触发一次不重复；estimate 变更重置；周五未交提醒/周一催办幂等。
5. 权限演示：成员查他人台账 403；非负责人审批 403；审批自己批次 400。
6. RPT-004 联调：负载热力图数据与台账 API 逐 cell 一致（同源验证）。
7. 全部端点通过 `api-conventions.md` §14 检查清单；错误码零新增。

---

## 8. 相关文档

- 迭代概览：[`docs/sprint-7-enterprise-workflow/sprint-overview.md`](sprint-overview.md)
- 工时基座：[`docs/sprint-2-task-full/TASK-006-worklog.md`](../sprint-2-task-full/TASK-006-worklog.md)
- 通知通道：[`docs/sprint-1-mvp/COLLAB-001-comment-notify.md`](../sprint-1-mvp/COLLAB-001-comment-notify.md)
- 审批语义对齐：[`docs/sprint-7-enterprise-workflow/WF-002-approval-flow.md`](WF-002-approval-flow.md)
- 负载消费方：[`docs/sprint-9-enterprise-portfolio/RPT-004-project-health.md`](../sprint-9-enterprise-portfolio/RPT-004-project-health.md)




