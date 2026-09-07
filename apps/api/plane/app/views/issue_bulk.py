"""任务批量操作端点（BOARD-004 §4.2）——四端点 + throttle + 幂等键。

  PATCH   /workspaces/{slug}/projects/{pid}/issues/bulk/            动作 1-4（批量更新）
  POST    /workspaces/{slug}/projects/{pid}/issues/bulk/archive/    动作 5（批量归档）
  DELETE  /workspaces/{slug}/projects/{pid}/issues/bulk/            动作 6（批量删除）
  POST    /workspaces/{slug}/projects/{pid}/issues/bulk/preview/    危险动作预检（只读）

批量层零旁路（BR-03）：逐条守卫 / 权限 / 校验全部在 BulkService 内走单条同源
服务函数；视图只做载荷结构校验（枚举 / 归属 / 上限预拦截）与信封装配。

throttle（BR-06，api-conventions §7.2）：10 次/min/用户 → 429 RATE_LIMIT_EXCEEDED
+ Retry-After（全局 handler 第 8 步）。幂等键（BR-14，§3.4）：危险动作默认携带
（UUID/批），Redis 可用时重放回首次响应 + ``Idempotency-Replayed: true``；
Redis 不可用降级直通（warning 日志——降级口径：幂等保障让位于可用性，重放
保护丢失但写路径本身幂等 / confirm_count 错配拦截兜底）。
"""

from __future__ import annotations

import json
import logging
import uuid

from django.conf import settings as dj_settings
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated
from plane.app.views._access import get_project_or_404, require_project_writable
from plane.base.exception import AppException
from plane.base.response import success_response
from plane.base.throttling import BASE_THROTTLES, BulkRateThrottle
from plane.db.models import Issue, Label, ProjectRole, State
from plane.db.services import issue_bulk as bulk_svc
from plane.db.services.issue_bulk import (
    BULK_LIMIT,
    MAX_BULK_COMMENT,
    SET_MODES,
    BulkActionError,
    BulkLimitExceeded,
    dedupe_ids,
)
from plane.utils.exceptions import AppValidationError, field_error

logger = logging.getLogger(__name__)

# Throttle（BR-06：批量端点族 10/min/用户）——INFRA-005 收编：本文件原
# ``BulkRateThrottle`` 自带实现退役，改用 ``plane.base.throttling`` 框架类
#（scope ``issue_bulk`` 并入 ``bulk``，10/min·user 语义不变）。


# ─────────────────────────────────────────────────────────────────────
# 幂等键（BR-14：键→响应摘要重放；Redis 不可用降级直通）
# ─────────────────────────────────────────────────────────────────────
IDEMPOTENCY_TTL = 86400
_IDEM_PREFIX = "bulk:idem:"


def _idem_redis():
    """Redis 客户端（含 ping 探测）；不可用返回 None。"""
    try:
        import redis

        client = redis.Redis.from_url(
            dj_settings.REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5
        )
        client.ping()
        return client
    except Exception:  # noqa: BLE001 —— 任何 Redis 故障均降级直通（BR-14 口径）
        return None


def _with_idempotency(request, handler) -> Response:
    """Idempotency-Key 命中 → 回放首次响应（仅缓存 2xx；失败响应不缓存）。"""
    key = request.headers.get("Idempotency-Key")
    if not key:
        return handler()
    client = _idem_redis()
    if client is None:  # 降级直通（记 warning；报告注明该降级口径）
        logger.warning("bulk.idempotency.redis_unavailable degraded=pass-through")
        return handler()
    cache_key = _IDEM_PREFIX + f"{getattr(request.user, 'id', '')}:{key}"
    try:
        hit = client.get(cache_key)
    except Exception:  # noqa: BLE001
        logger.warning("bulk.idempotency.redis_read_failed degraded=pass-through")
        return handler()
    if hit:
        summary = json.loads(hit)
        return Response(
            summary["body"], status=summary["status"],
            headers={"Idempotency-Replayed": "true"},
        )
    resp = handler()
    if 200 <= resp.status_code < 300:
        try:
            client.set(
                cache_key,
                json.dumps({"status": resp.status_code, "body": resp.data}),
                ex=IDEMPOTENCY_TTL,
            )
        except Exception:  # noqa: BLE001
            logger.warning("bulk.idempotency.redis_write_failed key_cached=no")
    return resp


# ─────────────────────────────────────────────────────────────────────
# 载荷解析（结构性错误 400 整请求拒绝；业务失败由服务层项级收集）
# ─────────────────────────────────────────────────────────────────────
def _parse_issue_ids(payload: dict) -> list[uuid.UUID]:
    raw = payload.get("issue_ids")
    if raw is None:
        raise AppValidationError([field_error("issue_ids", "REQUIRED", "issue_ids 为必填")])
    if not isinstance(raw, list) or not raw:
        raise AppValidationError([field_error("issue_ids", "REQUIRED", "issue_ids 必须为非空列表")])
    ids: list[uuid.UUID] = []
    for x in raw:
        try:
            ids.append(uuid.UUID(str(x)))
        except (ValueError, AttributeError, TypeError):
            raise AppValidationError(
                [field_error("issue_ids", "INVALID_UUID", f"UUID 格式非法：{x}")]
            ) from None
    if len(dedupe_ids(ids)) > BULK_LIMIT:  # BR-01 预拦截（服务端双保险）
        raise BulkLimitExceeded(len(dedupe_ids(ids)))
    return ids


def _parse_comment(payload: dict) -> str:
    comment = str(payload.get("comment") or "").strip()
    if len(comment) > MAX_BULK_COMMENT:  # BR-12
        raise AppValidationError(
            [field_error("comment", "TOO_LONG", f"批量备注最长 {MAX_BULK_COMMENT} 字")]
        )
    return comment


def _parse_set_spec(payload: dict, key: str) -> tuple[dict, list[uuid.UUID]]:
    """assignees / labels 集合运算三模式（§3.2 浮层契约：replace|add|remove）。"""
    spec = payload.get(key)
    if not isinstance(spec, dict):
        raise AppValidationError([field_error(key, "REQUIRED", f"{key} 为必填对象")])
    ids_field = "assignee_ids" if key == "assignees" else "label_ids"
    errors: list[dict] = []
    mode = spec.get("mode")
    if mode not in SET_MODES:
        errors.append(field_error(f"{key}.mode", "NOT_A_CHOICE", f"mode 必须为 {'/'.join(SET_MODES)}"))
    raw_ids = spec.get(ids_field)
    if not isinstance(raw_ids, list):
        errors.append(field_error(f"{key}.{ids_field}", "REQUIRED", "必须为列表"))
        raw_ids = []
    ids: list[uuid.UUID] = []
    for x in raw_ids:
        try:
            ids.append(uuid.UUID(str(x)))
        except (ValueError, AttributeError, TypeError):
            errors.append(field_error(f"{key}.{ids_field}", "INVALID_UUID", f"UUID 格式非法：{x}"))
    if errors:
        raise AppValidationError(errors)
    return {"mode": mode, ids_field: dedupe_ids(ids)}, ids


def _gate_bulk_write(project) -> None:
    """批级写门槛：issue.bulk.update（CONTRIBUTOR+；VIEWER/COMMENTER 全拒，BE-14）。"""
    if project.current_user_role < ProjectRole.CONTRIBUTOR:
        raise AppException("PERM_ROLE_INSUFFICIENT")
    require_project_writable(project)  # archived/closed 整批 403（PROJ-003）


def _translate_bulk_error(exc: BulkActionError) -> AppException:
    """项级失败清单 → 400 VALIDATION_ERROR（BR-02 三键逐项，message 汇总口径）。"""
    return AppException(
        "VALIDATION_ERROR",
        message=f"{exc.batch_size} 项中 {len(exc.failures)} 项未通过校验，未执行任何修改",
        details=exc.failures,
    )


# ─────────────────────────────────────────────────────────────────────
# #1 PATCH …/issues/bulk/（动作 1-4）+ #3 DELETE …/issues/bulk/（动作 6）
# ─────────────────────────────────────────────────────────────────────
class IssueBulkView(APIView):
    """PATCH …/issues/bulk/ —— 状态 / 优先级 / 指派 / 标签（§4.2.1）。

    请求恰含一个动作字段（``patch`` / ``assignees`` / ``labels``）+ ``comment?``；
    响应 ``data={updated, epoch, action, comment}`` + ``meta={batch_size}``。
    同路径的 DELETE（动作 6 批量删除，§4.2.3）见 :meth:`delete`。
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [*BASE_THROTTLES, BulkRateThrottle]

    def patch(self, request, *args, **kwargs):
        return _with_idempotency(request, lambda: self._handle_update(request, **kwargs))

    def _handle_update(self, request, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        _gate_bulk_write(project)
        payload = request.data or {}
        ids = _parse_issue_ids(payload)
        comment = _parse_comment(payload)

        actions = [k for k in ("patch", "assignees", "labels") if k in payload]
        if len(actions) != 1:
            raise AppValidationError([
                field_error("__all__", "INVALID",
                            "必须且仅提供一个动作字段（patch / assignees / labels）")
            ])
        action = actions[0]
        patch = assignees = labels = None
        label_names: dict[uuid.UUID, str] = {}
        if action == "patch":
            patch = self._parse_patch(payload["patch"], project)
        elif action == "assignees":
            spec, _ = _parse_set_spec(payload, "assignees")
            assignees = {"mode": spec["mode"], "assignee_ids": spec["assignee_ids"]}
        else:
            spec, label_ids = _parse_set_spec(payload, "labels")
            # 标签归属本项目（payload 级，单条 PUT 同口径：project + active + 未删）
            valid = {
                lid: name
                for lid, name in Label.objects.filter(
                    pk__in=label_ids, project=project, is_active=True, deleted_at__isnull=True
                ).values_list("id", "name")
            }
            invalid = [str(x) for x in label_ids if x not in valid]
            if invalid:
                raise AppValidationError([
                    field_error("labels.label_ids", "DOES_NOT_EXIST",
                                "包含不属于当前项目或已停用的标签")
                ])
            label_names = valid
            labels = {"mode": spec["mode"], "label_ids": spec["label_ids"]}

        try:
            result = bulk_svc.bulk_update(
                project=project, actor=request.user, issue_ids=ids,
                patch=patch, assignees=assignees, labels=labels,
                label_names=label_names, comment=comment,
                is_admin=project.current_user_role >= ProjectRole.ADMIN,
            )
        except BulkActionError as exc:
            raise _translate_bulk_error(exc) from None
        return success_response(result["data"], meta=result["meta"])

    @staticmethod
    def _parse_patch(raw: dict, project) -> dict:
        if not isinstance(raw, dict) or not raw:
            raise AppValidationError([field_error("patch", "REQUIRED", "patch 必须为非空对象")])
        unknown = set(raw) - {"state_id", "priority"}
        if unknown:
            raise AppValidationError([
                field_error("patch", "NOT_A_CHOICE", f"仅支持 state_id / priority，收到 {sorted(unknown)}")
            ])
        patch: dict = {}
        if "state_id" in raw:
            state = State.objects.filter(
                pk=raw["state_id"], project=project, deleted_at__isnull=True
            ).first()
            if state is None:  # BR-11：目标态必属本项目（载荷级）
                raise AppValidationError([
                    field_error("patch.state_id", "DOES_NOT_EXIST", "状态不属于当前项目")
                ])
            patch.update({"state_id": raw["state_id"], "state": state})
        if "priority" in raw:
            valid = {c for c, _ in Issue.Priority.choices}
            if raw["priority"] not in valid:
                raise AppValidationError([
                    field_error("patch.priority", "NOT_A_CHOICE", "优先级取值非法")
                ])
            patch["priority"] = raw["priority"]
        return patch

    def delete(self, request, *args, **kwargs):
        """动作 6（§4.2.3）：与 PATCH 同路径，方法分派。"""
        return _with_idempotency(request, lambda: _bulk_delete(request, **kwargs))


# ─────────────────────────────────────────────────────────────────────
# #2 POST …/issues/bulk/archive/ —— 动作 5
# ─────────────────────────────────────────────────────────────────────
class IssueBulkArchiveView(APIView):
    """POST …/issues/bulk/archive/ —— 整树级联归档（§4.2.2，同步 200 豁免声明）。

    ``data={archived_count, affected_total, epoch}`` + ``meta={batch_size, cascade}``。
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [*BASE_THROTTLES, BulkRateThrottle]

    def post(self, request, *args, **kwargs):
        return _with_idempotency(request, lambda: self._handle(request, **kwargs))

    def _handle(self, request, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        _gate_bulk_write(project)
        payload = request.data or {}
        ids = _parse_issue_ids(payload)
        comment = _parse_comment(payload)
        try:
            result = bulk_svc.bulk_archive(
                project=project, actor=request.user, issue_ids=ids,
                comment=comment, role=project.current_user_role,
            )
        except BulkActionError as exc:
            raise _translate_bulk_error(exc) from None
        return success_response(result["data"], meta=result["meta"])


# ─────────────────────────────────────────────────────────────────────
# #3 DELETE …/issues/bulk/ —— 动作 6（与 #1 同路径，IssueBulkView 分派）
# ─────────────────────────────────────────────────────────────────────
def _bulk_delete(request, **kwargs):
    """DELETE …/issues/bulk/ —— 软删 + 级联（§4.2.3）。

    载荷 ``{issue_ids, confirm_count}``（二次确认输入数量，BR-10）；错配 400。
    ``data={deleted, affected_total, epoch}`` + ``meta={batch_size, cascade}``。
    """
    project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
    _gate_bulk_write(project)
    payload = request.data or {}
    ids = _parse_issue_ids(payload)
    try:
        confirm_count = int(payload.get("confirm_count"))
    except (TypeError, ValueError):
        raise AppValidationError([
            field_error("confirm_count", "REQUIRED", "confirm_count 必须为整数")
        ]) from None
    comment = _parse_comment(payload)
    try:
        result = bulk_svc.bulk_delete(
            project=project, actor=request.user, issue_ids=ids,
            confirm_count=confirm_count, comment=comment,
            role=project.current_user_role,
        )
    except BulkActionError as exc:
        raise _translate_bulk_error(exc) from None
    return success_response(result["data"], meta=result["meta"])


# ─────────────────────────────────────────────────────────────────────
# #4 POST …/issues/bulk/preview/ —— 危险动作预检（只读，issue.read）
# ─────────────────────────────────────────────────────────────────────
class IssueBulkPreviewView(APIView):
    """POST …/issues/bulk/preview/ —— 级联统计 + 权限失败前移（§4.2.4）。

    ``{issue_ids, action: delete|archive}``；VIEWER 可读（只读预检不在 BE-14 拒绝列）。
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [*BASE_THROTTLES, BulkRateThrottle]

    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(kwargs["slug"], kwargs["project_id"], request.user)
        payload = request.data or {}
        ids = _parse_issue_ids(payload)
        action = payload.get("action")
        if action not in ("delete", "archive"):
            raise AppValidationError([
                field_error("action", "NOT_A_CHOICE", "action 必须为 delete 或 archive")
            ])
        return success_response(
            bulk_svc.bulk_preview(
                project=project, actor=request.user, issue_ids=ids,
                action=action, role=project.current_user_role,
            )
        )
