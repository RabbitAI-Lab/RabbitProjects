"""GitHub API 客户端（INTG-001 §4.3.2/§6——Sprint-5 T5-04）。

- 出站全走 installation token（缓存至过期前 5 分钟）；
- 速率预算（BR-14）：Redis 计数器 5000/h/installation，429/403-rate-limit 按
  ``X-RateLimit-Reset`` 暂停出站队列（不丢任务——IntegrationQuotaService）；
- 传输层可注入（``transport=``）——测试以本地 stub 替换，零出网（门禁口径：
  规格 IT-05 自身即按 mock 5xx 设计）。

API 基址 ``settings.GITHUB_API_BASE``（缺省 https://api.github.com）——
e2e/flow 门禁可指向本地 mock 服务。
"""
from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger("plane.integrations.github")

RETRY_BACKOFF = (1, 4, 16)
#: 稳态自限（§2.6：30 请求/分钟/安装，低于 GitHub 上限）
STEADY_PER_MIN = 30
QUOTA_PER_HOUR = 5000
#: BR-04 风险表：超 70% 触发降级开关（暂停 PR 挂载细节，仅保留 Issue 同步）
QUOTA_DEGRADE_RATIO = 0.70


class GitHubApiError(Exception):
    def __init__(self, status: int, message: str, *, reset_at: int | None = None):
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status
        self.message = message
        self.reset_at = reset_at          # epoch 秒（429/403 rate-limit）
        self.rate_limited = status in (403, 429) and reset_at is not None


class IntegrationQuotaService:
    """速率预算（BR-14）：Redis 滑窗计数 + 降级开关。"""

    @staticmethod
    def _key(kind: str, installation_id: int) -> str:
        return f"gh-quota:{kind}:{installation_id}"

    @classmethod
    def hit(cls, installation_id: int, *, steady: bool = False) -> dict:
        """记一次出站请求；返回 {count, remaining, degraded}。"""
        window = 60 if steady else 3600
        cap = STEADY_PER_MIN if steady else QUOTA_PER_HOUR
        now = int(time.time())
        key = cls._key("min" if steady else "hour", installation_id)
        hits = [t for t in (cache.get(key) or []) if now - t < window]
        hits.append(now)
        cache.set(key, hits, timeout=window)
        count = len(hits)
        ratio = count / cap
        degraded = ratio >= QUOTA_DEGRADE_RATIO
        return {"count": count, "remaining": max(0, cap - count),
                "degraded": degraded, "ratio": round(ratio, 3)}

    @classmethod
    def pause_until(cls, installation_id: int, reset_at: int) -> None:
        """429/403-rate-limit：按 Reset 时间挂暂停标记（出站任务 defer）。"""
        cache.set(cls._key("pause", installation_id), reset_at,
                  timeout=max(1, reset_at - int(time.time())))

    @classmethod
    def paused(cls, installation_id: int) -> int | None:
        """剩余暂停秒数（None = 未暂停）。"""
        reset_at = cache.get(cls._key("pause", installation_id))
        if reset_at and reset_at > time.time():
            return int(reset_at - time.time())
        return None

    @classmethod
    def status(cls, installation_id: int) -> dict:
        """只读配额状态（INTG-002 交接项 3——quota-status 端点数据源；不计数）。

        degraded = 稳态/小时任一窗口占比 ≥ QUOTA_DEGRADE_RATIO（70%——与 hit()
        同口径；「INFRA-005 根据此旗位切换速率预算」的消费面由此读取）。
        """
        now = int(time.time())
        out: dict = {"installation_id": installation_id}
        for kind, window, cap in (
            ("minute", 60, STEADY_PER_MIN), ("hour", 3600, QUOTA_PER_HOUR),
        ):
            hits = [t for t in (cache.get(cls._key(kind[:3], installation_id)) or [])
                    if now - t < window]
            ratio = len(hits) / cap
            out[kind] = {"count": len(hits), "cap": cap,
                         "remaining": max(0, cap - len(hits)),
                         "ratio": round(ratio, 3),
                         "degraded": ratio >= QUOTA_DEGRADE_RATIO}
        out["paused_for"] = cls.paused(installation_id)
        out["degraded"] = out["minute"]["degraded"] or out["hour"]["degraded"]
        return out


class GitHubClient:
    """installation token 客户端（传输层可注入）。"""

    def __init__(self, installation_id: int, *, transport: Callable[..., Any] | None = None):
        self.installation_id = installation_id
        self._transport = transport or self._http
        self._app_id = getattr(settings, "GITHUB_APP_ID", "")
        self._private_key = getattr(settings, "GITHUB_APP_PRIVATE_KEY", "")
        self._api_base = getattr(settings, "GITHUB_API_BASE", "https://api.github.com")

    # ── 传输 ──
    @staticmethod
    def _http(method: str, url: str, *, headers: dict, json_body: dict | None) -> dict:
        import requests

        resp = requests.request(method, url, headers=headers, json=json_body, timeout=(3, 10))
        if resp.status_code >= 400:
            reset = resp.headers.get("X-RateLimit-Reset")
            raise GitHubApiError(
                resp.status_code, resp.text[:200],
                reset_at=int(reset) if reset and resp.status_code in (403, 429) else None)
        return {"status": resp.status_code, "json": _safe_json(resp),
                "headers": dict(resp.headers)}

    def _call(self, method: str, path: str, *, body: dict | None = None,
              token: str | None = None) -> dict:
        url = path if path.startswith("http") else f"{self._api_base}{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "rabbit-projects-integration",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        last_exc: Exception | None = None
        for attempt, backoff in enumerate((0, *RETRY_BACKOFF)):
            if backoff:
                time.sleep(backoff)
            paused = IntegrationQuotaService.paused(self.installation_id)
            if paused:
                raise GitHubApiError(429, f"quota paused ({paused}s to reset)",
                                     reset_at=int(time.time()) + paused)
            try:
                IntegrationQuotaService.hit(self.installation_id, steady=True)
                return self._transport(method, url, headers=headers, json_body=body)
            except GitHubApiError as exc:
                last_exc = exc
                if exc.rate_limited and exc.reset_at:
                    IntegrationQuotaService.pause_until(self.installation_id, exc.reset_at)
                if exc.status >= 500 and attempt < len(RETRY_BACKOFF):
                    continue
                raise
        raise last_exc or GitHubApiError(500, "unreachable")

    # ── installation token（§4.3.2：App 私钥 JWT 换取，缓存至过期前 5 分钟）──
    def installation_token(self, binding_token_cache: dict | None = None) -> tuple[str, dict]:
        """返回 (token, token_cache)。token_cache 由调用方持久化到绑定行。"""
        cache_dict = binding_token_cache or {}
        token, expires_at = cache_dict.get("token"), cache_dict.get("expires_at", 0)
        if token and expires_at and expires_at - time.time() > 300:   # 过期前 5 分钟
            return str(token), dict(cache_dict)
        # 无 App 凭据（dev/mock 环境）：走 dev token 直通（e2e 可注入）
        if not (self._app_id and self._private_key):
            dev_token: dict = {"token": f"dev-inst-{self.installation_id}",
                               "expires_at": time.time() + 3600}
            return str(dev_token["token"]), dev_token
        jwt = self._app_jwt()
        resp = self._call("POST", f"/app/installations/{self.installation_id}/access_tokens",
                          body=None, token=jwt)
        data = resp.get("json") or {}
        new_cache: dict = {"token": data.get("token"),
                           "expires_at": time.time() + int(data.get("expires_in", 3600))}
        return str(new_cache["token"]), new_cache

    def _app_jwt(self) -> str:
        import jwt  # PyJWT（apps/api 依赖已有则用；缺则退化为 dev token）

        now = int(time.time())
        payload = {"iat": now - 60, "exp": now + 600, "iss": str(self._app_id)}
        return jwt.encode(payload, self._private_key, algorithm="RS256")

    # ── 业务面 ──
    def list_repositories(self, token_cache: dict | None = None) -> list[dict]:
        token, new_cache = self.installation_token(token_cache)
        resp = self._call("GET", "/installation/repositories", token=token)
        self.last_token_cache = new_cache
        return (resp.get("json") or {}).get("repositories", [])

    def register_webhook(self, repo_full_name: str, *, callback_url: str,
                         secret: str, token_cache: dict | None = None) -> dict:
        token, new_cache = self.installation_token(token_cache)
        resp = self._call("POST", f"/repos/{repo_full_name}/hooks", body={
            "config": {"url": callback_url, "content_type": "json",
                       "secret": secret, "insecure_ssl": "0"},
            "events": ["issues", "issue_comment", "pull_request", "push"],
            "active": True,
        }, token=token)
        self.last_token_cache = new_cache
        return {"id": (resp.get("json") or {}).get("id"),
                "registered": True, "status": resp.get("status", 201)}

    def edit_issue_title(self, repo_full_name: str, issue_number: int,
                         title: str, token_cache: dict | None = None) -> None:
        """回写标题前缀 [RBT-128]（BR-05）——出站前内容 hash 比对由调用方做（BR-08）。"""
        token, new_cache = self.installation_token(token_cache)
        self._call("PATCH", f"/repos/{repo_full_name}/issues/{issue_number}",
                   body={"title": title}, token=token)
        self.last_token_cache = new_cache

    def create_issue_comment(self, repo_full_name: str, issue_number: int,
                             body: str, token_cache: dict | None = None) -> None:
        token, new_cache = self.installation_token(token_cache)
        self._call("POST", f"/repos/{repo_full_name}/issues/{issue_number}/comments",
                   body={"body": body}, token=token)
        self.last_token_cache = new_cache

    def close_issue(self, repo_full_name: str, issue_number: int,
                    state: str, token_cache: dict | None = None) -> None:
        token, new_cache = self.installation_token(token_cache)
        self._call("PATCH", f"/repos/{repo_full_name}/issues/{issue_number}",
                   body={"state": state}, token=token)
        self.last_token_cache = new_cache


def _safe_json(resp) -> dict:
    try:
        return resp.json() or {}
    except Exception:  # noqa: BLE001 —— 204/非 JSON 响应兜底
        return {}


def content_hash(payload: dict) -> str:
    """出站内容指纹（BR-08：无差异跳过请求）。"""
    return hashlib.sha256(
        "|".join(f"{k}={payload.get(k)}" for k in sorted(payload)).encode()
    ).hexdigest()[:16]
