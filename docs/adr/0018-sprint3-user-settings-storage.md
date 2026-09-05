# ADR-0018 · Sprint-3 实现偏差登记（一）：`users/me/settings/` 偏好存储

| 项 | 内容 |
| --- | --- |
| 状态 | 已接受（Accepted） |
| 日期 | 2026-09-06 |
| 背景 | Sprint-3 T3-03（BOARD-003 views/ CRUD）实现期发现：规格 BR-10 按「`PATCH /api/v1/users/me/settings/` 写偏好键 `board.default_view_id`、零新端点」行文，并称 `api-conventions.md` §2.5 已列该资源 `GET`；实际代码库无该端点也无偏好存储（User 无 JSON 偏好列、无 UserSetting 表）。规格的「零新端点」前提不成立，属实现偏差，立 ADR 固化 |
| 关联 | `docs/sprint-3-views-collab/BOARD-003-multi-kanban.md`（BR-10 / §4.2 注）、`apps/api/plane/db/models/user.py`、`apps/api/plane/app/views/users.py`（UserSettingsView）、`apps/api/plane/app/routes/users.py` |

## 决策

1. **存储**：`User.preferences JSONB（default dict）` 单列承载全部用户偏好——`board.default_view_id` 的值形如 `{"<project_id>": "<view_uuid>"}`（项目内至多一条；取消默认 = 删该条目）。理由：全局单点读写、无跨表查询诉求，与 IssueView「值无独立表」同哲学。
2. **端点**：新增 `GET/PATCH /api/v1/users/me/settings/`（UserSettingsView）——PATCH 为逐键合并语义（`{"<pid>": null}` 删单条目），偏好键白名单 `{"board.default_view_id"}`，未知键 400 `VALIDATION_INVALID_PARAM`；值校验 UUID 形状 + 视图存在且属于该项目（400 `DOES_NOT_EXIST`）。BR-10 的交互契约（星标设默认 / 进项目直达）不变，仅存储与端点落点为补建。
3. **迁移**：随 `0007_p2_issue_views` 落列（`ALTER TABLE users ADD COLUMN preferences jsonb NOT NULL DEFAULT '{}'`）。

## 影响

- BOARD-003 §4.2 注的「零新端点」表述登记为待回改（实际补建一个既有规划资源端点）；`api-conventions.md` §2.5 的 `users/me/settings/` 行由本 ADR 兑现。
- 后续偏好键（P3+）一律进该白名单扩展，不再加列。
