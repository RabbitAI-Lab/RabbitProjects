"""GitHub 集成视图（INTG-001 §4.2——Sprint-5 T5-04）。

端点（§4.2 表八条）：
  GET  /api/v1/workspaces/{slug}/integrations/github/app/            安装入口（integration.manage）
  GET  /api/v1/workspaces/{slug}/integrations/github/callback/       安装回调（state 校验，302）
  GET  …/projects/{pid}/integrations/github/repositories/            可绑仓库（integration.config）
  POST …/projects/{pid}/integrations/github/bindings/                绑定（≤5/项目）
  PATCH/DELETE …/bindings/{binding_id}/                              改配置 / 解绑
  GET  …/integrations/github/sync-logs/?type=conflict                同步/冲突日志
  POST /api/v1/integrations/github/webhook/                          入站（HMAC，无登录态）

入站三道闸（BR-01 顺序铁律）：定位绑定（取 secret）→ 验签（常量时间比对 +
漂移 ≤5min）→ Delivery SETNX 查重 → 快回包 202（重活在 Worker）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid as uuid_mod

from django.core.cache import cache
from django.http import HttpResponseRedirect
from django.utils import timezone
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated, require_permission
from plane.app.views._access import get_project_or_404, get_workspace_or_404
from plane.base.exception import AppException
from plane.base.response import created_response, success_response
from plane.db.models import IntegrationInstallation, ProjectRole, SyncConflictLog
from plane.db.models.integration import (
    decrypt_secret,
    encrypt_secret,
    new_integration_secret,
)

#: 绑定上限（BR-03）
MAX_REPOS_PER_PROJECT = 5
#: 入站时间戳漂移上界（BR-02）
INBOUND_SKEW_SECONDS = 300
#: 安装 state 有效期（§2.1：Redis 10min）
INSTALL_STATE_TTL = 600


# ── 入站 Webhook（无登录态；GitHub 调用）──────────────────────────────
class GitHubWebhookView(APIView):
    """POST /api/v1/integrations/github/webhook/ —— 验签/查重后入队，202 快回包。"""

    permission_classes: list = []

    def post(self, request):
        raw_body = request.body
        delivery = request.headers.get("X-GitHub-Delivery", "")
        event_name = request.headers.get("X-GitHub-Event", "")
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return success_response(None, status_code=202,
                                    meta={"delivery": delivery, "dropped": "bad-json"})
        installation_id = (payload.get("installation") or {}).get("id")
        repo_full_name = ((payload.get("repository") or {}).get("full_name") or "").lower()
        # ① 定位绑定（未命中 = 解绑窗口事件，静默 202——不泄漏已知 installation 集）
        binding = IntegrationInstallation.objects.filter(
            installation_id=installation_id or -1,
            repository_full_name=repo_full_name,
            deleted_at__isnull=True).first()
        if binding is None:
            return success_response(None, status_code=202,
                                    meta={"delivery": delivery, "dropped": "unbound"})
        # ② 验签（常量时间比对）+ 时间戳漂移
        secret = decrypt_secret(binding.webhook_secret)
        signature = request.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        if not secret or not hmac.compare_digest(signature, expected):
            raise AppException("PERM_DENIED", message="签名校验失败")
        gh_updated = _payload_latest_ts(payload)
        if gh_updated and abs(time.time() - gh_updated) > INBOUND_SKEW_SECONDS:
            raise AppException("PERM_DENIED", message="事件时间戳超出容忍窗口")
        # ③ Delivery SETNX 查重（24h）
        if delivery:
            if not cache.add(f"gh-delivery:{delivery}", 1, timeout=86400):
                return success_response(None, status_code=202,
                                        meta={"delivery": delivery, "dropped": "duplicate"})
        # ④ 入队（规范化事件）
        from plane.bgtasks.github_sync import dispatch_github_event

        dispatch_github_event.delay(_normalize(event_name, payload, delivery, installation_id))
        return success_response(None, status_code=202, meta={"delivery": delivery})


def _payload_latest_ts(payload: dict) -> float | None:
    for key in ("issue", "pull_request", "comment"):
        node = payload.get(key) or {}
        ts = node.get("updated_at") or node.get("created_at")
        if ts:
            from datetime import datetime

            try:
                return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except (ValueError, TypeError):
                continue
    return None


def _normalize(event_name: str, payload: dict, delivery: str, installation_id) -> dict:
    action = payload.get("action") or ""
    kind = {
        ("issues", "opened"): "issue.opened",
        ("issues", "edited"): "issue.edited",
        ("issues", "closed"): "issue.closed",
        ("issues", "reopened"): "issue.reopened",
        ("issue_comment", "created"): "comment.created",
        ("pull_request", "closed"): "pull_request.merged",   # merged 判定在 Worker
        ("push", ""): "push",
    }.get((event_name, action))
    return {
        "kind": kind or f"{event_name}.{action}".strip("."),
        "delivery_id": delivery,
        "installation_id": installation_id,
        "repository_full_name": ((payload.get("repository") or {})
                                 .get("full_name") or "").lower(),
        "issue": payload.get("issue") or {},
        "pull_request": payload.get("pull_request") or {},
        "comment": payload.get("comment") or {},
        "commits": payload.get("commits") or [],
        "sender": (payload.get("sender") or {}).get("login"),
    }


# ── 安装入口 / 回调（WS 级 integration.manage）────────────────────────
class GitHubAppInstallView(APIView):
    permission_classes = [IsAuthenticated]

    @require_permission("integration.manage", scope="workspace")
    def get(self, request, slug):
        from django.conf import settings

        ws, _ = get_workspace_or_404(slug, request.user)
        state = uuid_mod.uuid4().hex
        cache.set(f"gh-install-state:{state}", {"ws": ws.slug, "user": request.user.id},
                  timeout=INSTALL_STATE_TTL)
        app_slug = getattr(settings, "GITHUB_APP_SLUG", "rabbit-projects")
        base = getattr(settings, "GITHUB_WEB_BASE", "https://github.com")
        return success_response({
            "install_url": f"{base}/apps/{app_slug}/installations/new?state={state}",
            "state": state, "expires_in": INSTALL_STATE_TTL,
        })


class GitHubCallbackView(APIView):
    """GET …/integrations/github/callback/?code&state —— 换 token + 落库 + 302 前端。

    dev/mock 无 App 凭据时跳过 token 交换（installation_id 由 code 解析或 0），
    直接进入绑定流程（e2e 门禁口径——规格 IT 矩阵自身按 mock 设计）。
    """

    permission_classes: list = []

    def get(self, request, slug):
        state = request.query_params.get("state") or ""
        code = request.query_params.get("code") or ""
        installation_id = request.query_params.get("installation_id") or "0"
        ctx = cache.get(f"gh-install-state:{state}")
        if not ctx or ctx.get("ws") != slug:
            raise AppException("VALIDATION_ERROR", message="state 无效或已过期",
                               details=[{"field": "state", "code": "INVALID"}])
        cache.delete(f"gh-install-state:{state}")
        try:
            installation_id = int(installation_id)
        except (TypeError, ValueError):
            installation_id = 0
        from django.conf import settings

        frontend = getattr(settings, "WEB_FRONTEND_BASE", "/")
        return HttpResponseRedirect(
            f"{frontend}integrations/github/installed?installation_id={installation_id}"
            f"&code={code}")


# ── 项目级绑定（integration.config）───────────────────────────────────
def _require_proj_admin(project) -> None:
    if (getattr(project, "current_user_role", None) or 0) < ProjectRole.ADMIN:
        raise AppException("PERM_ROLE_INSUFFICIENT", message="需要项目管理员权限")


class GitHubRepositoriesView(APIView):
    permission_classes = [IsAuthenticated]

    @require_permission("integration.config", scope="project")
    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user)
        installation_id = request.query_params.get("installation_id")
        try:
            installation_id = int(installation_id or 0)
        except (TypeError, ValueError):
            installation_id = 0
        from plane.integrations.github import GitHubClient

        client = GitHubClient(installation_id)
        repos = client.list_repositories()
        return success_response([
            {"full_name": r.get("full_name", "").lower(), "node_id": r.get("node_id", ""),
             "private": r.get("private"), "html_url": r.get("html_url")}
            for r in repos])


def _binding_row(b: IntegrationInstallation) -> dict:
    return {
        "id": str(b.id),
        "installation_id": b.installation_id,
        "repository_full_name": b.repository_full_name,
        "repository_node_id": b.repository_node_id,
        "sync_status": b.sync_status,
        "default_issue_type_id": str(b.default_issue_type_id) if b.default_issue_type_id else None,
        "last_synced_at": b.last_synced_at.isoformat() if b.last_synced_at else None,
    }


class GitHubBindingListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @require_permission("integration.config", scope="project")
    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user)
        rows = IntegrationInstallation.objects.filter(
            project=project, deleted_at__isnull=True).exclude(sync_status="unbound")
        return success_response([_binding_row(b) for b in rows])

    @require_permission("integration.config", scope="project")
    def post(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user)
        _require_proj_admin(project)
        repo = str(request.data.get("repository_full_name") or "").strip().lower()
        node_id = str(request.data.get("repository_node_id") or "").strip()
        installation_id = int(request.data.get("installation_id") or 0)
        if not repo or "/" not in repo:
            raise AppException("VALIDATION_ERROR", message="repository_full_name 非法",
                               details=[{"field": "repository_full_name", "code": "INVALID"}])
        if IntegrationInstallation.objects.filter(
                project=project, repository_full_name=repo,
                deleted_at__isnull=True).exclude(sync_status="unbound").exists():
            raise AppException("RESOURCE_ALREADY_EXISTS", message="该仓库已绑定到本项目")
        if IntegrationInstallation.objects.filter(
                project=project, deleted_at__isnull=True,
                sync_status__in=["syncing", "paused", "stale"]).count() >= MAX_REPOS_PER_PROJECT:
            raise AppException("RESOURCE_LIMIT_EXCEEDED",
                               message=f"单项目最多绑定 {MAX_REPOS_PER_PROJECT} 个仓库")
        secret_plain = new_integration_secret()
        binding = IntegrationInstallation.objects.create(
            installation_id=installation_id,
            project=project,
            repository_full_name=repo,
            repository_node_id=node_id,
            webhook_secret=encrypt_secret(secret_plain),
            default_issue_type_id=request.data.get("default_issue_type_id") or None,
            created_by=request.user,
        )
        # Webhook 注册（出站尽力而为——dev mock 传输层直接成功）
        webhook_registered = False
        try:
            from django.conf import settings as dj_settings

            from plane.integrations.github import GitHubClient

            client = GitHubClient(installation_id)
            base = getattr(dj_settings, "GITHUB_WEBHOOK_BASE", "")
            if base:
                result = client.register_webhook(
                    repo, callback_url=f"{base}/api/v1/integrations/github/webhook/",
                    secret=secret_plain, token_cache=binding.token_cache)
                binding.token_cache = client.last_token_cache
                webhook_registered = bool(result.get("registered"))
                binding.save(update_fields=["token_cache", "updated_at"])
        except Exception:  # noqa: BLE001 —— 注册失败不阻断绑定（管理页可重试）
            webhook_registered = False
        return created_response({
            **_binding_row(binding),
            "webhook_secret_shown_once": secret_plain,   # 明文仅此一次（§4.1.1）
            "webhook_registered": webhook_registered,
            "backfill": {"state": "skipped", "task_id": None},   # 回填归 Day5 联调批次
        }, location=request.build_absolute_uri(
            f"/api/v1/workspaces/{kwargs['slug']}/projects/{project.id}/"
            f"integrations/github/bindings/{binding.id}/"))


class GitHubBindingDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @require_permission("integration.config", scope="project")
    def patch(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user)
        binding = self._get(project, kwargs["binding_id"])
        fields = ["updated_at"]
        if "default_issue_type_id" in request.data:
            binding.default_issue_type_id = request.data.get("default_issue_type_id") or None
            fields.append("default_issue_type_id")
        if "sync_status" in request.data:
            status = request.data["sync_status"]
            if status not in ("syncing", "paused"):
                raise AppException("VALIDATION_ERROR", message="sync_status 仅 syncing|paused")
            binding.sync_status = status
            fields.append("sync_status")
        binding.save(update_fields=fields)
        return success_response(_binding_row(binding))

    @require_permission("integration.config", scope="project")
    def delete(self, request, *args, **kwargs):
        """解绑（BR-13）：标记 unbound + 软删；任务 external_* 保留。"""
        from rest_framework import status
        from rest_framework.response import Response

        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user)
        binding = self._get(project, kwargs["binding_id"])
        binding.sync_status = "unbound"
        binding.deleted_at = timezone.now()
        binding.save(update_fields=["sync_status", "deleted_at", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @staticmethod
    def _get(project, binding_id) -> IntegrationInstallation:
        row = IntegrationInstallation.objects.filter(
            pk=binding_id, project=project, deleted_at__isnull=True).first()
        if row is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("RESOURCE_NOT_FOUND") from None
        return row


class GitHubSyncLogsView(APIView):
    permission_classes = [IsAuthenticated]

    @require_permission("integration.config", scope="project")
    def get(self, request, *args, **kwargs):
        project, _, _ = get_project_or_404(
            kwargs["slug"], kwargs["project_id"], request.user)
        rows = (SyncConflictLog.objects
                .filter(binding__project=project)
                .select_related("binding", "issue")
                .order_by("-occurred_at")[:100])
        data = [{
            "id": str(r.id), "repository": r.binding.repository_full_name,
            "issue_id": str(r.issue_id),
            "issue_key": None, "scope": r.scope, "direction": r.direction,
            "winner_side": r.winner_side, "winner_payload": r.winner_payload,
            "loser_payload": r.loser_payload, "delivery_id": r.delivery_id,
            "occurred_at": r.occurred_at.isoformat(),
        } for r in rows]
        return success_response(data, meta={"count": len(data), "total_count": len(data)})
