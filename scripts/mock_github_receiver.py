#!/usr/bin/env python3
"""GitHub 侧 mock 接收方（sprint-5 验收录屏 · SCENARIOS 基建）。

扮演 github.com 的两个角色（被验收的 RabbitProjects 集成代码全真实执行，
本服务只提供可控的"对方"）：

  1. Webhook 接收端点 /github/webhook/rp —— 演示「RabbitProjects 出站回写」
     落点（标题前缀 [RBT-n] PATCH、关闭 Issue、评论——INTG-001 §4.3 出站）。
     收到的请求记录到 /__log（录制器取证断言用）。
  2. 可编程触发器 POST /__trigger/<event> —— 录制器以此模拟 GitHub 向
     RabbitProjects 的入站 webhook（issues.opened/edited/closed、
     pull_request.closed merged、push commits）——签名按绑定 secret 现算
     X-Hub-Signature-256（走 RabbitProjects 同一验签路径，非旁路）。

用法：python3 scripts/mock_github_receiver.py [port=8090]
录制器侧：curl -X POST localhost:8090/__trigger/issue-opened -d '{"binding_id":…}'
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
API = "http://localhost:8000/api/v1/integrations/github/webhook/"

#: binding_id → secret（seed 写入后由录制器经 __register 注入；出站回写验签同源）
SECRETS: dict[str, str] = {}
INBOUND_LOG: list[dict] = []
OUTBOUND_LOG: list[dict] = []

STATE_FILE = "/tmp/s5-mock-github-state.json"


def _persist() -> None:
    with open(STATE_FILE, "w") as f:
        json.dump({"secrets": SECRETS, "inbound": INBOUND_LOG[-50:], "outbound": OUTBOUND_LOG[-80:]}, f, ensure_ascii=False)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静音默认访问日志（录制终端干净）
        pass

    def _json(self, code: int, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    # ── GitHub 扮演：接收 RabbitProjects 出站回写 ──
    def do_PATCH(self):
        ln = int(self.headers.get("Content-Length", 0))
        OUTBOUND_LOG.append({"t": time.time(), "method": "PATCH", "path": self.path,
                             "body": self.rfile.read(ln).decode()[:400]})
        _persist()
        self._json(200, {"ok": True})

    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(ln)
        if self.path.startswith("/github/"):  # RabbitProjects 出站（评论/关闭等）
            OUTBOUND_LOG.append({"t": time.time(), "method": "POST", "path": self.path,
                                 "body": raw.decode()[:400]})
            _persist()
            return self._json(201, {"id": int(time.time() * 1000) % 10**8})
        if self.path == "/__register":
            body = json.loads(raw)
            SECRETS[str(body["binding_id"])] = str(body["secret"])
            _persist()
            return self._json(200, {"registered": True})
        if self.path.startswith("/__trigger/"):
            return self._trigger(self.path.rsplit("/", 1)[-1], json.loads(raw))
        return self._json(404, {"error": "not found"})

    def do_GET(self):
        if self.path == "/__log":
            return self._json(200, {"inbound": INBOUND_LOG[-20:], "outbound": OUTBOUND_LOG[-30:]})
        if self.path == "/__health":
            return self._json(200, {"ok": True})
        return self._json(404, {})

    # ── 触发器：以 GitHub 身份向 RabbitProjects 发签名 webhook ──
    def _trigger(self, event: str, spec: dict) -> None:
        delivery = str(uuid.uuid4())
        payload = _build_payload(event, spec)
        body = json.dumps(payload, ensure_ascii=False).encode()
        secret = SECRETS.get(str(spec.get("binding_id", "")), "")
        # GitHub 真实头是事件族名（issues/issue_comment/pull_request/push）——
        # 触发路径名（issue-edited）必须映射回族名，否则 RabbitProjects 的
        # _normalize 映射落空 → kind=issue-edited.edited → unhandled
        family = {"issue": "issues", "pr": "pull_request", "push": "push"}.get(
            event.split("-")[0], event.split(".")[0])
        req = urllib.request.Request(API, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": family,
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(),
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        except Exception as e:  # noqa: BLE001
            INBOUND_LOG.append({"event": event, "delivery": delivery, "error": str(e)})
            _persist()
            return self._json(502, {"error": str(e)})
        INBOUND_LOG.append({"event": event, "delivery": delivery, "status": code,
                            "issue_node": (payload.get("issue") or {}).get("node_id")})
        _persist()
        self._json(200, {"delivered": code, "delivery": delivery})


def _build_payload(event: str, s: dict) -> dict:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    p: dict = {"installation": {"id": s.get("installation_id", 9001)},
               "repository": {"full_name": s.get("repo", "acme/rabbit-web"),
                              "node_id": s.get("repo_node", "R_MOCK")},
               "sender": {"login": "gh-mock"}}
    if event in ("issue-opened", "issue-edited", "issue-closed", "issue-reopened"):
        p["action"] = event.split("-", 1)[1].replace("opened", "opened").replace("reopened", "reopened")
        p["issue"] = {"node_id": s["node_id"], "number": s.get("number", 1),
                      "title": s.get("title", ""), "body": s.get("body", ""),
                      "state": "closed" if event == "issue-closed" else "open",
                      "updated_at": now, "created_at": now,
                      "html_url": f"https://github.com/{p['repository']['full_name']}/issues/{s.get('number', 1)}"}
    elif event == "pr-merged":
        p["action"] = "closed"
        p["pull_request"] = {"number": s.get("number", 42), "merged": True,
                             "title": s.get("title", ""), "body": s.get("body", ""),
                             "html_url": "https://github.com/p/1", "merged_at": now}
    elif event == "push":
        p["commits"] = [{"id": s.get("sha", "a" * 40), "message": s.get("message", ""),
                         "url": "https://github.com/c", "author": {"name": "gh-mock"}}]
    return p


if __name__ == "__main__":
    print(f"mock github receiver on :{PORT} (trigger/log/health)")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
