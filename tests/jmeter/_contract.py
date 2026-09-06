#!/usr/bin/env python3
"""接口契约真相源（sprint-1 起唯一定义点）。

CLAUDE.md「测试脚本规范 ①」要求后端契约只有一份事实来源，且
`tests/e2e/no-console-errors.ts` 的 API_TRUTH 镜像同一份（跨语言双源同步）。

sprint-0 把状态码表写在 `sprint-0-flow.py` 顶部，但该脚本没有 `__main__` 守卫——
一旦 import 就会跑完整条 10 步流程，导致「唯一真相源」实际无法被其它脚本复用，
新脚本只能各自硬编码（正是规范要防的漂移）。故本模块只放**可 import 的常量与
HTTP 辅助**，不含任何自动执行的流程。

变更任一常量时必须同步 `tests/e2e/no-console-errors.ts` 的 HTTP / CODES。
"""
from __future__ import annotations

import http.cookiejar
import json
import urllib.error
import urllib.parse
import urllib.request

#: HTTP 状态码表（与 tests/e2e/no-console-errors.ts 的 HTTP 镜像）
HTTP = {
    "OK": 200,            # GET 资源正常
    "CREATED": 201,       # POST 建资源
    "ACCEPTED": 202,      # 已受理异步处理（TASK-008 字段删除异步清理值）
    "NO_CONTENT": 204,    # DELETE / sign-out 无 body
    "BAD_REQUEST": 400,   # 参数 / 校验失败
    "UNAUTHORIZED": 401,  # DRF 未认证（CSRF / 未登录）
    "FORBIDDEN": 403,     # 越权（角色不足）
    "NOT_FOUND": 404,     # 越权 404（AUTH-003 防 ID 枚举）
    "CONFLICT": 409,      # 唯一性冲突 / 上限
    "GONE": 410,          # 公开分享读时四查统一失效（FILE-004 §4.3.1 防枚举区分）
    "TOO_MANY": 429,      # 限流
    "SRV_ERR": 500,       # 期望失败用
    "SRV_UNAVAILABLE": 503,  # 依赖服务不可用（COLLAB-004 live 探测降级 SERVER_LIVE_SERVICE_UNAVAILABLE）
}

#: 错误码常量（须存在于 apps/api/plane/base/error_codes.py 的 75 码注册表）
#: Sprint-2 起顶层码零新增（规格与架构文档 §8 已对齐；DEPTH/CYCLE 等为 details 子码）
CODES = {
    "invalidCreds": "AUTH_INVALID_CREDENTIALS",
    "disabled": "AUTH_ACCOUNT_DISABLED",
    "csrf": "AUTH_CSRF_FAILED",
    "resetInvalid": "AUTH_PASSWORD_RESET_INVALID",
    "resetExpired": "AUTH_PASSWORD_RESET_EXPIRED",
    "roleInsufficient": "PERM_ROLE_INSUFFICIENT",
    "projectAdmin": "PERM_PROJECT_ADMIN_REQUIRED",
    "workspaceAdmin": "PERM_WORKSPACE_ADMIN_REQUIRED",
    "workspaceOwner": "PERM_WORKSPACE_OWNER_REQUIRED",
    "validation": "VALIDATION_ERROR",
    "invalidParam": "VALIDATION_INVALID_PARAM",
    "notFound": "RESOURCE_NOT_FOUND",
    "alreadyExists": "RESOURCE_ALREADY_EXISTS",
    "conflict": "RESOURCE_CONFLICT",
    "limitExceeded": "RESOURCE_LIMIT_EXCEEDED",
    # ── Sprint-2（TASK-004~010，均为既有注册码的别名） ──
    "stateInvalid": "RESOURCE_STATE_INVALID",            # 归档任务/已认领等状态拒绝（T007/T009）
    "circular": "RESOURCE_CIRCULAR_DEPENDENCY",          # 父子/依赖成环（T004/T005）
    "transitionBlocked": "RESOURCE_TRANSITION_BLOCKED",  # 未完成前置拦截完成（T005）
    "cfInvalid": "VALIDATION_CUSTOM_FIELD_INVALID",      # 自定义字段值校验（T008）
    "queueError": "SERVER_QUEUE_ERROR",                  # 死信堆积告警（T010）
    # ── Sprint-3（BOARD-003/004、TASK-011、COLLAB-002/003/004，均为既有注册码） ──
    "permDenied": "PERM_DENIED",                         # 内置视图锁定 / 换票 issue 不可见拒整票（B003/C004）
    "projectArchived": "PERM_PROJECT_ARCHIVED",          # 归档项目只读（C002 评论/表情、B004 整批）
    "invalidCursor": "VALIDATION_INVALID_CURSOR",        # 动态流组边界游标损坏（C003）
    "bulkLimit": "VALIDATION_BULK_LIMIT_EXCEEDED",       # 批量 100 条上限（B004 BR-01）
    "rateLimited": "RATE_LIMIT_EXCEEDED",                # 批量 throttle 10/min（B004 BR-06，429+Retry-After）
    "fileSize": "VALIDATION_FILE_SIZE_EXCEEDED",         # 评论图 5MB 收紧（C002 BR-08）
    "fileType": "VALIDATION_FILE_TYPE_NOT_ALLOWED",      # 评论图 png/jpg/gif/webp 白名单（C002 BR-08）
    "tokenExpired": "AUTH_TOKEN_EXPIRED",                # 实时票据过期/无效续签拒绝（C004 BR-02）
    "liveUnavailable": "SERVER_LIVE_SERVICE_UNAVAILABLE",  # live 探测降级（C004 §2.5，503）
    # ── Sprint-4（FILE-002 项目文件库，均为既有注册码） ──
    "quotaStorage": "QUOTA_STORAGE_EXCEEDED",            # 工作空间配额耗尽（F002 §2.5，409；details 子码 QUOTA）
    "uploadMismatch": "VALIDATION_FILE_UPLOAD_MISMATCH",  # complete HEAD 大小不匹配（F002 IT-02）
    # ── Sprint-4（FILE-003/004、GANTT，均为既有注册码） ──
    "authRequired": "AUTH_REQUIRED",                     # 公开分享未解锁读 content（F004 §4.3.3，401）
    "gone": "RESOURCE_GONE",                             # 读时四查统一失效（F004 §4.3.1，410 同码同文案）
    "storageError": "SERVER_STORAGE_ERROR",              # MinIO 不可用降级（F002/F003/F004，500）
}

#: Sprint-4 起端点路径模板（FILE-002/003/004 + GANTT-001/002 + 实时第四类房间）。
#: flow / coverage / bench 共用，防端点路径各自硬编码漂移；全部强制尾斜杠。
_S4_BASE = "/api/v1/workspaces/{ws}/projects/{proj}"
ENDPOINTS = {
    # FILE-002 §4.2 #1~#14
    "folders": _S4_BASE + "/folders/",
    "folder_detail": _S4_BASE + "/folders/{folder}/",
    "folder_files": _S4_BASE + "/folders/{folder}/files/",
    "folder_presign": _S4_BASE + "/folders/{folder}/files/presign/",
    "file_detail": _S4_BASE + "/files/{asset}/",
    "file_complete": _S4_BASE + "/files/{asset}/complete/",
    "file_download": _S4_BASE + "/files/{asset}/download-url/",
    "file_restore": _S4_BASE + "/files/{asset}/restore/",
    "file_purge": _S4_BASE + "/files/{asset}/purge/",
    "trash": _S4_BASE + "/files/trash/",
    "storage": _S4_BASE + "/files/storage/",
    # FILE-003 §4.2 #1~#11
    "upload_sessions": _S4_BASE + "/upload-sessions/",
    "upload_session_detail": _S4_BASE + "/upload-sessions/{session}/",
    "upload_session_chunk": _S4_BASE + "/upload-sessions/{session}/chunks/{n}/",
    "upload_session_complete": _S4_BASE + "/upload-sessions/{session}/complete/",
    "file_versions": _S4_BASE + "/files/{asset}/versions/",
    "file_version_rollback": _S4_BASE + "/files/{asset}/versions/{version}/rollback/",
    "file_version_content": _S4_BASE + "/files/{asset}/versions/{version}/content/",
    "file_preview": _S4_BASE + "/files/{asset}/preview/",
    "file_derivative": _S4_BASE + "/files/{asset}/derivatives/{kind}/",
    # FILE-004 §4.2 内部 #1~#4 + 公开 #5~#7
    "share_links": _S4_BASE + "/files/{asset}/share-links/",
    "share_link_detail": _S4_BASE + "/share-links/{link}/",
    "share_link_extend": _S4_BASE + "/share-links/{link}/extend/",
    "public_share": "/api/v1/public/shares/{slug}/",
    "public_unlock": "/api/v1/public/shares/{slug}/unlock/",
    "public_content": "/api/v1/public/shares/{slug}/content/",
    # GANTT-001 §4.2 + GANTT-002 §4.2.1
    "gantt_rows": _S4_BASE + "/gantt/",
    "gantt_relations": _S4_BASE + "/gantt/relations/bulk/",
    "gantt_unscheduled": _S4_BASE + "/gantt/unscheduled/",
    "gantt_overdue": _S4_BASE + "/gantt/overdue-summary/",
    # COLLAB-004（file_rooms 走同端点载荷扩展）
    "realtime_token": _S4_BASE + "/realtime-token/",
}

#: 统一信封字段路径（INFRA-004 C1）
ENVELOPE = {
    "successStatus": "success",
    "errorStatus": "error",
    "dataKey": "data",
    "metaKey": "meta",
    "errorKey": "error",
    "errorCode": "code",
    "errorMessage": "message",
    "errorDetails": "details",
    "errorRequestId": "request_id",
}


def _decode(raw: bytes, content_type: str):
    """尽力解析响应体；非 JSON 时保留原文摘要而不是抛异常。"""
    if not raw:
        return None
    text = raw.decode(errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return {"_raw": text[:300], "_content_type": content_type}


class Client:
    """带独立 cookie jar 的 HTTP 客户端 —— 多用户场景每人一个实例。"""

    def __init__(self, base: str = "http://localhost:8000"):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def req(self, method: str, path: str, data=None, headers=None, want_headers=False):
        """返回 (status_code, parsed_body)；4xx/5xx 不抛异常，交由调用方断言。

        非 JSON 响应（如 DEBUG 下未捕获异常返回的 HTML 错误页）不得让测试工具自身崩溃——
        那会把「被测端点坏了」误报成「测试脚本坏了」。此时返回
        {"_raw": "<前 300 字>", "_content_type": ...} 供调用方判定。

        want_headers=True 时返回 (status, body, headers_dict)——断言协议头
        （429 Retry-After / Idempotency-Replayed 等）用；headers 键名原样保留
        大小写规范形式（HTTPResponse.headers / HTTPError.headers 同源）。
        """
        body = json.dumps(data).encode() if data is not None else None
        h = {"Accept": "application/json", "Content-Type": "application/json", "Referer": self.base + "/"}
        if headers:
            h.update(headers)
        # 路径里的非 ASCII（中文搜索词等）必须百分号编码，否则 http.client 在
        # request.encode('ascii') 处直接 UnicodeEncodeError——报错点离真正的原因很远。
        # 已编码的 %XX 不会被二次转义（safe 含 %）。
        r = urllib.request.Request(self.base + urllib.parse.quote(path, safe="/?&=:%+,"), data=body, method=method, headers=h)
        try:
            with self.opener.open(r, timeout=15) as resp:
                pair = (resp.status, _decode(resp.read(), resp.headers.get("Content-Type", "")))
                hdrs = dict(resp.headers.items())
        except urllib.error.HTTPError as e:
            pair = (e.code, _decode(e.read(), e.headers.get("Content-Type", "")))
            hdrs = dict(e.headers.items())
        return (pair[0], pair[1], hdrs) if want_headers else pair

    def csrf(self) -> str:
        """登录态变化（Django login() 会 rotate CSRF）后必须重新拉（CLAUDE.md 坑 #2）。"""
        return self.req("GET", "/api/v1/auth/csrf-token/")[1]["data"]["csrf_token"]

    def post(self, path, data=None):
        return self.req("POST", path, data or {}, {"X-CSRFToken": self.csrf()})

    def patch(self, path, data=None):
        return self.req("PATCH", path, data or {}, {"X-CSRFToken": self.csrf()})

    def put(self, path, data=None):
        return self.req("PUT", path, data or {}, {"X-CSRFToken": self.csrf()})

    def delete(self, path):
        return self.req("DELETE", path, None, {"X-CSRFToken": self.csrf()})

    def get(self, path):
        return self.req("GET", path)

    def get_no_redirect(self, path):
        """GET 且不跟随 302 —— 断言「换发下载链接」类端点用（FILE-001 §4.3.4）。

        默认 opener 会自动跟随重定向，而 download 端点换发的是网关相对路径
        `/uploads/...`，直连 API（无 nginx）时跟随会得到 404，无法区分
        「端点坏了」和「本地没网关」。这里只取 (status, Location)。
        """
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect)
        r = urllib.request.Request(
            self.base + urllib.parse.quote(path, safe="/?&=:%+,"),
            headers={"Accept": "application/json", "Referer": self.base + "/"},
            method="GET",
        )
        try:
            with opener.open(r, timeout=15) as resp:
                return resp.status, resp.headers.get("Location", "")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Location", "")


def q(value: str) -> str:
    """查询串取值需百分号编码——中文直接拼进 URL 会 UnicodeEncodeError。"""
    return urllib.parse.quote(str(value))


def error_code(body) -> str | None:
    """从错误信封取 error.code（C1：错误码在 error 节点内，不在 meta）。"""
    if not isinstance(body, dict):
        return None
    return (body.get("error") or {}).get("code")


def detail_of(body, field: str):
    """从错误信封的 details[] 中按 field 取条目。"""
    if not isinstance(body, dict):
        return None
    for item in (body.get("error") or {}).get("details") or []:
        if item.get("field") == field:
            return item
    return None
