# ADR-0019 · Sprint-3 实现偏差登记（二）：COLLAB-002 评论协作

| 项 | 内容 |
| --- | --- |
| 状态 | 已接受（Accepted） |
| 日期 | 2026-09-06 |
| 背景 | Sprint-3 T3-05（COLLAB-002 楼中楼/表情/图片评论）实现期发现 5 处规格未定义或与既有 COLLAB-001 语义存在空隙的决策点，立 ADR 固化 |
| 关联 | `docs/sprint-3-views-collab/COLLAB-002-thread-reply.md`、`docs/sprint-1-mvp/COLLAB-001-comments-notifications.md`、`apps/api/plane/db/services/comment.py`、`apps/api/plane/app/views/comments.py`、`apps/api/plane/db/services/notify.py` |

## 决策

1. **`reply_to_actor` 的 GET 侧来源（§4.2.1/§4.2.2 空隙）**：归并挂载后原始回复目标无法从 `parent_id` 反推（parent 已归并为顶层）。实现为 POST 时在 `accessory.reply_to={comment_id, actor_id}` 存档、GET 读档回显（`accessory` 为规格化 `images` 键之外的扩展键）。
2. **父删子留的占位条件（BR-06/BR-13 收窄）**：软删顶层**仅当仍有存活回复**时以 `is_deleted: true` 占位出现（引导线延续、replies 保留）；无回复的软删评论维持 COLLAB-001 的消失语义。占位行的正文/图片/mention 不再回传（软删内容不可再见的既有语义延续）。
3. **纯图片评论仍受 1~5000 文本长度约束**：COLLAB-001 stripped 长度规则不放宽（BR 未要求）；前端图片评论场景始终有引导文字。
4. **归档只读闸门补位（BR-01/IT-08）**：COLLAB-001 原视图无归档闸门，本次按规格 BR-01 补上——POST 评论 / reactions toggle 在 `project.status == "archived"` 时 403 `PERM_PROJECT_ARCHIVED`（错误码既有注册）。
5. **并发与通知的测试形态**：并发同点（UT-10）以 `all_objects` 直插断言 `IntegrityError`（pytest 事务内真并发不可靠，唯一约束是最终防线）；通知三互斥走 `fanout_comment` 服务层直测（分派唯一实现点；端到端投递链路由 sprint-3-flow.py 覆盖）。

## 影响

- 偏差 1/2 属响应契约的补充定义，随本 ADR 回写规格勘误候选（规格 §4.2.2 的 replies[] 字段表补 `reply_to` 来源说明）；其余为测试策略说明，无契约影响。
- Pillow 依赖登记见 tech-stack.md v1.2（11.x→12.x 修订）。
