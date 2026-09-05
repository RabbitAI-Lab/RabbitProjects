#!/usr/bin/env python3
"""Sprint 2 接口端到端验证 —— 与 sprint-0/1-flow.py 并列的 CI gate。

用法：python3 tests/jmeter/sprint-2-flow.py [http://localhost:8000]
前置：API 已启动并连接真实 PG；TASK-008/010 段另需 Redis + RabbitMQ + activity 队列
worker（本地：docker 起 rp-redis/rp-mq 后 `uv run --project apps/api celery -A plane
worker -Q activity,celery`；或 compose 全套）。

契约常量全部来自 tests/jmeter/_contract.py（CLAUDE.md 测试脚本规范 ①）。
Sprint-2 顶层错误码零新增（75 码注册表已含全部所需；DEPTH/CYCLE/LIMIT/STATE/
BLOCKED_BY 为 error.details[].sub_code 字段级子码，按 api-conventions §8.8 登记）。

本文件当前为**骨架**（阶段 0 产出）：环境准备段已可跑通，七个功能域分段随
TASK-004~010 逐段落地填充。每段标注归属文档 §，关键异常/并发用例与
docs/sprint-2-task-full/test-cases.md 的 IT 清单对应。
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _contract import CODES, HTTP, Client, error_code, q

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
PASS = 0
FAIL = 0
FAILURES: list[str] = []


def ck(cid: str, desc: str, cond: bool, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {cid} {desc}")
    else:
        FAIL += 1
        FAILURES.append(f"{cid} {desc} {extra}")
        print(f"  ✗ {cid} {desc} {extra}")


def section(title: str):
    print(f"\n═══ {title} ═══")


def signup(c: Client, tag: str):
    ts = int(time.time() * 1000) % 100000000
    email = f"{tag}{ts}@rabbit.dev"
    code, body = c.req(
        "POST", "/api/v1/auth/sign-up/",
        {"email": email, "password": "Rabbit123!", "display_name": "S2 Flow"},
        {"X-CSRFToken": c.csrf()},
    )
    if code != HTTP["CREATED"]:
        print(f"  ✗ 前置失败：sign-up {code} {body}")
        raise SystemExit(1)
    return email, body["data"]["default_workspace_slug"]


def make_project(c: Client, ws: str, identifier: str) -> str:
    code, body = c.req(
        "POST", f"/api/v1/workspaces/{q(ws)}/projects/",
        {"name": f"S2 {identifier}", "identifier": identifier},
        {"X-CSRFToken": c.csrf()},
    )
    if code != HTTP["CREATED"]:
        print(f"  ✗ 前置失败：create project {code} {body}")
        raise SystemExit(1)
    return body["data"]["id"]


def make_issue(c: Client, ws: str, proj: str, name: str, **fields) -> dict:
    code, body = c.req(
        "POST", f"/api/v1/workspaces/{q(ws)}/projects/{proj}/issues/",
        {"name": name, **fields},
        {"X-CSRFToken": c.csrf()},
    )
    ck(f"mk:{name}", f"建任务 {name} → 201", code == HTTP["CREATED"], f"got {code} {body}")
    return body["data"]


# ═══ 0. 环境准备（唯一已实现段） ═══

admin = Client(BASE)
email, ws = signup(admin, "s2flow-")
proj = make_project(admin, ws, "S2F")

# 第二账号：越权负向（AUTH-003 隔离基线，Sprint-2 全部新端点沿用同款断言）
outsider = Client(BASE)
_out_email, _out_ws = signup(outsider, "s2out-")

# ═══ 1. TASK-004 多层级子任务（随阶段 1 落地） ═══
# 计划锚点（docs/sprint-2-task-full/test-cases.md IT-004 段）：
#   - sub-issues/ 挂载到第 5 层 → 409 RESOURCE_LIMIT_EXCEEDED（details sub_code=DEPTH）
#   - PATCH parent_id 移动子树成环 → 409 RESOURCE_CIRCULAR_DEPENDENCY（details 含环路径）
#   - 移动子树高度越限（4 层子树挂到第 3 层）→ 409 DEPTH
#   - GET subtree/ 整树 stats（含根口径）+ ?parent_id= 懒加载
#   - DELETE 级联软删 → 200 + {deleted_count, descendant_ids}（Sprint-0 的 204 契约被本迭代变更）
#   - 跨项目 parent → 400；越权 outsider 访问 → 404

# ═══ 2. TASK-005 任务依赖（随阶段 2 落地） ═══
# 计划锚点：
#   - POST relations/ 成对写入 + Location；重复/镜像 → 409 RESOURCE_ALREADY_EXISTS
#   - 间接环（A→B→C 后 C→A）→ 409 CYCLE（details 依赖链）；120 层合法深链放行
#   - PATCH 迁移 completed 被未完成前置拦截 → 409 RESOURCE_TRANSITION_BLOCKED
#     （details[].issue_key）；force=true + comment（≥5 字）→ 200；cancelled 前置不阻塞
#   - GET relations/ 契约冻结（GANTT-001 数据源，内联 related_issue）
#   - GET ?blocked=true 筛选

# ═══ 3. TASK-006 工时（随阶段 3 落地） ═══
# 计划锚点：
#   - PATCH estimate_minutes（≤525600；超限 400）
#   - POST worklogs/（1~1440 分钟；未来日期/超 30 天窗口 → 400）
#   - PATCH worklogs/{id}/ 编辑对他人的 → 403；编辑重校验窗口（29 天前记录改 31 天前 → 400）
#   - GET worklogs/ meta.sum_minutes + actor/worked_on/mine 筛选
#   - subtree/ stats 扩展 subtree_spent_minutes / subtree_estimate_minutes

# ═══ 4. TASK-007 多执行人（随阶段 4 落地） ═══
# 计划锚点：
#   - PUT assignees/ 全量替换（changes.added/removed）；>10 人 → 409 LIMIT；
#     非成员/COMMENTER/VIEWER → 400 DOES_NOT_EXIST；归档任务 → 409 STATE
#   - POST assignees/claim/ 空集合才可；重复认领 → 409 STATE
#   - DELETE assignees/{user_id}/ 仅自退；删他人 → 403
#   - GET ?assignee_ids=me,null（null 糖值 = 未指派；组合丢弃 null + meta.warning）

# ═══ 5. TASK-008 自定义字段（随阶段 5 落地） ═══
# 计划锚点：
#   - POST issue-properties/（12 类型抽样：select 选项/number/currency/date/member/
#     checkbox/url/email/phone）；field_key 冲突 → 409；改 field_type → 400 READ_ONLY
#   - GET field-schema/（builtin+custom、filterable/sortable 推导、ETag/304）
#   - PATCH issues/ custom_fields 合并语义（null 显式清空；未知 key → 400 INVALID；
#     select 越界值 → 400 NOT_A_CHOICE；必填缺失 → 400 REQUIRED）
#   - GET ?property.<id>= 等值/包含/null + order_by=±cf_x（数字字段 9<10<100）
#   - DELETE issue-properties/{id}/ → 202（task_id/status_url）
#   - 50 字段上限 → 409 LIMIT（BR-10）

# ═══ 6. TASK-009 复制/归档（随阶段 6 落地） ═══
# 计划锚点：
#   - POST duplicate/ 五选项（副本后缀 (副本)/(副本 2)；>500 节点 → 409 LIMIT；
#     归档源 → 409 STATE；已归档子任务复制为活跃）
#   - POST archive/ 整树幂等（重复 POST 200 count=0）；DELETE archive/ 恢复对称
#   - 归档写保护：PATCH 归档任务 → 409 STATE；归档项目 → 403 PERM_PROJECT_ARCHIVED
#   - GET ?archived=true 归档视图

# ═══ 7. TASK-010 审计日志（随阶段 7 落地） ═══
# 计划锚点：
#   - GET activities/ epoch 预聚合（items[]）+ 游标分页（30 组/页；跨页边界组完整）
#   - ?field=state&actor_id= 过滤
#   - 写操作产生 Activity（改 state/优先级/标签/执行人/自定义字段逐键）
#   - 死信：GET activity-dead-letters/（非 admin → 403）/ 单条重放 / 批量 / 丢弃
#   - 幂等（event_key 去重）与 DLQ 需要 worker 运行；无 worker 环境标 skip

# ═══ 汇总 ═══

print(f"\n{'═' * 40}\nSprint 2 接口流程：{PASS} 通过 / {FAIL} 失败（骨架：七个功能域待逐段填充）")
if FAILURES:
    print("\n".join("  ✗ " + f for f in FAILURES))
    sys.exit(1)
print("全部通过 ✓")
