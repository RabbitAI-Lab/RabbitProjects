# ADR-0017 · Sprint-3 上游服务签名扩展：共享 epoch 与抑制内建投递

| 项 | 内容 |
| --- | --- |
| 状态 | 已接受（Accepted） |
| 日期 | 2026-09-05 |
| 背景 | Sprint-3 开发前置批量登记（T3-01）：`BOARD-004`（批量操作）评审 PASS，其 BR-05「单次投递 + 共享 epoch」的兑现路径依赖两个 Sprint-2 已交付服务的签名扩展，属**上游待回改登记**的架构决策，立 ADR 固化 |
| 关联 | `docs/sprint-3-views-collab/BOARD-004-batch-operations.md`（BR-05 / §4.1 / §4.3.4）、`docs/sprint-2-task-full/TASK-004-subtask-hierarchy.md`、`docs/sprint-2-task-full/TASK-009-task-copy-archive.md`、`docs/sprint-2-task-full/TASK-010-full-audit-log.md`（BR-07 / BR-12）、`docs/sprint-3-views-collab/COLLAB-003-activity-stream.md`、`apps/api/plane/db/services/issue_hierarchy.py`、`apps/api/plane/db/services/issue_archive.py` |

## 决策：两个级联服务补 `epoch=None` / `suppress_activity=False` 参数

```python
# apps/api/plane/db/services/issue_hierarchy.py（现签名 delete_subtree(issue_id, actor_id)）
def delete_subtree(issue_id: uuid.UUID, actor_id: uuid.UUID, *,
                   epoch: float | None = None,
                   suppress_activity: bool = False) -> dict: ...

# apps/api/plane/db/services/issue_archive.py（现签名 archive_subtree(*, issue_id, actor_id)）
def archive_subtree(*, issue_id: uuid.UUID, actor_id: uuid.UUID,
                    epoch: float | None = None,
                    suppress_activity: bool = False) -> dict: ...
```

- `epoch=None`（缺省）：服务在入口自行生成 epoch——**单条调用行为不变**；
- `epoch=<共享值>`：跳过自生成、直接采用调用方传入的批量共享 epoch（与
  `batch_id` 同值，BOARD-004 BR-05）；
- `suppress_activity=False`（缺省）：保留内建 `on_commit(record_delete)` /
  `on_commit(record_archive)` 单条投递——**单条端点零改动**；`True` 时抑制内建
  投递，Activity 落库与投递职责上移给调用方。

## 动因：不改则「双份 Activity + epoch 分裂」

两服务现签名各自在入口生成 epoch 并内建单条 `on_commit` 投递。`BOARD-004`
批量删除 / 批量归档若照原样逐条调用：

1. **每条任务落两份 Activity**——服务内建投递一份、批量出口 batch 载荷一份；
2. **epoch 分裂**——两份各持不同 epoch，`TASK-010` BR-07 幂等键
   `sha256(verb + issue_id + actor_id + epoch)` 因 epoch 不同而**跨份无法去重**；
3. **破坏 `COLLAB-003` 同 epoch 折叠**——动态流的「同一批操作聚合展示」失去
   聚合键（`comment` 前缀 `batch:` + epoch 同值即聚合依据）。

## 兑现路径（BOARD-004 BR-05，批量删除与批量归档同一形态）

```
批量入口生成共享 epoch（同批全部条目同值，batch_id = epoch）
  → 逐条调用 delete_subtree / archive_subtree 时传入 epoch 并 suppress_activity=True
  → 批量出口统一 on_commit 单次投递 batch 载荷
    （{"batch_delete"|"batch_archive": [...], actor_id, epoch, comment: "batch: …"}）
```

批量归档 `POST …/issues/bulk/archive/` 与批量删除 `DELETE …/issues/bulk/`
走同一兑现路径（BOARD-004 §4.2.2 / BR-09）。

## 兼容性与守护

- 缺省参数语义完全向后兼容：Sprint-2 的单条删除 / 归档端点与既有测试零改动；
- 批量侧传入 `epoch` 而未置 `suppress_activity=True` 视为编程错误（开发期
  assert / 单测覆盖——防「共享 epoch 但双份投递」的半吊子组合）；
- 锚定用例以 `BOARD-004` §5 交付为准（批量留痕聚合 + 幂等键跨份去重断言）。
