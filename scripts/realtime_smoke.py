#!/usr/bin/env python3
"""COLLAB-004 联调冒烟脚本（IT-01 最小版）——api + live + Redis 全起后手动跑。

用法（依赖 apps/api 的 venv —— 复用 redis / PyJWT 包）：

    uv run --project apps/api python scripts/realtime_smoke.py \
        [--api http://localhost:8000] [--live ws://localhost:3000] \
        [--redis redis://localhost:6379/0]

链路（§2.1 时序图的最小闭环）：
  ① sign-up → 建项目/任务 → POST …/realtime-token/（换票，服务端装配 rooms）
  ② WS /live/connect?token=…（stdlib 最小客户端）→ 断言 connected 帧
  ③ A 真实链路（可选）：API PATCH 任务状态 → Worker 扇出 → 1s 内 issue.state.changed
       （需要 celery worker 在跑：-Q activity,celery；无 worker 时降级提示）
    B 直灌链路（保底断言）：Redis PUBLISH rp:events 合成事件 → 1s 内收到
  ④ 断言 seq / room / payload（不回显由 live 侧 vitest 覆盖）
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import socket
import struct
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests", "jmeter"))
from _contract import Client  # noqa: E402 —— 契约客户端唯一定义点（ADR-0012 E4）

HTTP_OK = 200


# ────────────────────────────────────────────────────────────────
# 最小 WebSocket 客户端（stdlib；本脚本只做短连读帧，不做重连/心跳）
# ────────────────────────────────────────────────────────────────
class MiniWs:
    def __init__(self, url: str, timeout: float = 6.0):
        parsed = urllib.parse.urlparse(url)
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 80
        self.path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        self.sock = socket.create_connection((self.host, self.port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._handshake()

    def _handshake(self) -> None:
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        req = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("upgrade 握手中断")
            raw += chunk
        head, _, rest = raw.partition(b"\r\n\r\n")
        status_line = head.split(b"\r\n")[0].decode()
        if " 101 " not in status_line:
            raise ConnectionError(f"upgrade 失败：{status_line}")
        self._buffer = rest

    def _read_exact(self, n: int) -> bytes:
        while len(self._buffer) < n:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("连接中断")
            self._buffer += chunk
        out, self._buffer = self._buffer[:n], self._buffer[n:]
        return out

    def recv_text(self) -> str:
        """读下一帧（text/close/ping——ping 自动回 pong），返回 text 载荷。"""
        while True:
            b1, b2 = self._read_exact(2)
            opcode = b1 & 0x0F
            masked = b2 & 0x80
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(length)
            if mask:
                payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
            if opcode == 0x1:                       # text
                return payload.decode()
            if opcode == 0x8:                       # close
                code = struct.unpack(">H", payload[:2])[0] if len(payload) >= 2 else 0
                raise ConnectionError(f"server close: {code}")
            if opcode == 0x9:                       # ping → pong（masked client 帧）
                pong = bytearray()
                pong.append(0x8A)
                pong.append(0x80 | len(payload))
                m = secrets.token_bytes(4)
                pong += m
                pong += bytes(c ^ m[i % 4] for i, c in enumerate(payload))
                self.sock.sendall(bytes(pong))
                continue

    def wait_event(self, event: str, timeout: float = 1.0) -> dict:
        deadline = time.time() + timeout
        seen: list[str] = []
        while time.time() < deadline:
            self.sock.settimeout(max(deadline - time.time(), 0.05))
            try:
                frame = json.loads(self.recv_text())
            except (socket.timeout, TimeoutError):
                break
            seen.append(frame.get("event", "?"))
            if frame.get("event") == event:
                return frame
        raise TimeoutError(f"等待 {event} 超时（收到：{seen}）")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


# ────────────────────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────────────────────
def ok(step: str) -> None:
    print(f"  ✓ {step}")


def fail(step: str, extra: str = "") -> None:
    print(f"  ✗ {step} {extra}")
    raise SystemExit(1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--live", default="ws://localhost:3000")
    ap.add_argument("--redis", default=os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    ap.add_argument("--live-http-health", default=None,
                    help="live /health 探测地址（默认由 --live 推导 http://host:port）")
    args = ap.parse_args()

    print("═══ COLLAB-004 realtime smoke（IT-01 最小版）═══")

    # ① 账号 / 项目 / 任务
    c = Client(args.api)
    ts = int(time.time() * 1000) % 100_000_000
    code, body = c.req("POST", "/api/v1/auth/sign-up/",
                       {"email": f"c004-smoke-{ts}@rabbit.dev",
                        "password": "Rabbit123!", "display_name": "Smoke"},
                       {"X-CSRFToken": c.csrf()})
    if code != 201:
        fail("sign-up", f"{code} {body}")
    ws_slug = body["data"]["default_workspace_slug"]
    ok(f"sign-up（ws={ws_slug}）")

    code, body = c.req("POST", f"/api/v1/workspaces/{ws_slug}/projects/",
                       {"name": "C004 Smoke", "identifier": "SMK"},
                       {"X-CSRFToken": c.csrf()})
    if code != 201:
        fail("create project", f"{code} {body}")
    proj = body["data"]["id"]
    code, body = c.req("POST",
                       f"/api/v1/workspaces/{ws_slug}/projects/{proj}/issues/",
                       {"name": "smoke-task"}, {"X-CSRFToken": c.csrf()})
    if code != 201:
        fail("create issue", f"{code} {body}")
    issue = body["data"]
    ok(f"project + issue 就绪（issue={issue['id']}）")

    # 换票
    code, body = c.req(
        "POST", f"/api/v1/workspaces/{ws_slug}/projects/{proj}/realtime-token/",
        {"client_tab_id": f"smoke-tab-{ts}", "issue_rooms": [issue["id"]]},
        {"X-CSRFToken": c.csrf()})
    if code != HTTP_OK:
        fail("realtime-token", f"{code} {body}")
    ticket = body["data"]
    assert f"project:{proj}" in ticket["rooms"] and f"issue:{issue['id']}" in ticket["rooms"]
    ok(f"换票成功（rooms={ticket['rooms']} renew_after={ticket['renew_after']}）")

    # ② WS connect
    ws_url = f"{args.live}/live/connect?token={urllib.parse.quote(ticket['token'])}"
    try:
        conn = MiniWs(ws_url)
    except ConnectionError as exc:
        fail("ws connect", str(exc))
        return 1
    try:
        connected = conn.wait_event("connected", timeout=3)
        assert connected["payload"]["rooms"] == ticket["rooms"]
        ok(f"connected 帧（heartbeat={connected['payload']['heartbeat']}s）")

        # ③A 真实链路（可选，需 celery worker -Q activity,celery 在跑）
        code, states = c.req("GET",
                             f"/api/v1/workspaces/{ws_slug}/projects/{proj}/states/")
        target_state = next(
            (s for s in states["data"] if s["group"] != "unstarted"), states["data"][0])
        patch_code, _ = c.req(
            "PATCH", f"/api/v1/workspaces/{ws_slug}/projects/{proj}/issues/{issue['id']}/",
            {"state": target_state["id"]}, {"X-CSRFToken": c.csrf()})
        ok(f"API PATCH state → {patch_code}（真实链路事件取决于 worker 是否在跑）")
        real_event = None
        try:
            real_event = conn.wait_event("issue.state.changed", timeout=4)
        except TimeoutError:
            print("  ⚠ 真实链路 4s 未达（worker/broker 未起属预期）——降级直灌链路断言")

        # ③B 直灌链路（保底）：Redis publish 合成事件（worker 缺席时的确定性断言）
        if real_event is None:
            import redis as redis_mod

            r = redis_mod.Redis.from_url(args.redis, decode_responses=True)
            message = json.dumps({
                "event": "issue.state.changed",
                "rooms": [f"project:{proj}", f"issue:{issue['id']}"],
                "payload": {
                    "issue_id": issue["id"], "actor_id": None,
                    "from_group": "unstarted", "to_group": target_state["group"],
                    "version": issue.get("updated_at") or "",
                },
                "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z",
            }, ensure_ascii=False)
            r.publish("rp:events", message)
            ok("Redis PUBLISH rp:events（合成 issue.state.changed）")

        t0 = time.time()
        try:
            evt = conn.wait_event("issue.state.changed", timeout=1.0)
        except TimeoutError:
            fail("issue.state.changed 1s 内送达")
            return 1
        latency_ms = (time.time() - t0) * 1000
        assert evt["room"] in (f"project:{proj}", f"issue:{issue['id']}")
        assert evt["seq"] >= 1
        assert evt["payload"]["to_group"] == target_state["group"]
        ok(f"issue.state.changed <1s 送达（latency={latency_ms:.0f}ms seq={evt['seq']} "
           f"room={evt['room']}）")

        # ④ live /health 指标
        health_url = args.live_http_health or args.live.replace("ws://", "http://")
        with urllib.request.urlopen(f"{health_url}/health", timeout=3) as hresp:
            health = json.loads(hresp.read())
        assert health["status"] == "ok" and health["connections"] >= 1
        ok(f"/health 指标（connections={health['connections']} rooms={health['rooms']} "
           f"degraded={health['degraded']}）")
    finally:
        conn.close()

    print("\n═══ SMOKE PASS ═══")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
