#!/usr/bin/env python3
"""5xx 接收方 mock（sprint-5 验收录屏 · INTG-002 死信链路演示）。

恒 500 的出站投递接收方（SCENARIOS 幕 06）：Webhook 端点指向本服务 →
7 次 5xx → 退避表走完 → 死信。收到的每次投递（含 HMAC 签名头）记录到
/__log，录制器据此断言「6 次退避节奏 + 死信 + 重放」全链。

可控模式：PUT /__mode {"fail": false} 可切换 200（重放幕用——死信重放后
先切 200 再点重放，演示重放成功路径）。

用法：python3 scripts/mock_500_receiver.py [port=8091]
"""
from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8091
FAIL = {"on": True}
LOG: list[dict] = []
STATE_FILE = "/tmp/s5-mock-500-state.json"


def _persist() -> None:
    with open(STATE_FILE, "w") as f:
        json.dump({"fail": FAIL["on"], "log": LOG[-100:]}, f, ensure_ascii=False)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(ln)
        LOG.append({"t": round(time.time(), 3), "sig": self.headers.get("X-RP-Signature", "")[:20],
                    "ts": self.headers.get("X-RP-Timestamp", ""), "body": body.decode()[:200]})
        _persist()
        if FAIL["on"]:
            return self._json(500, {"err": "mock 5xx"})
        return self._json(200, {"received": len(LOG)})

    def do_PUT(self):  # /__mode {"fail": false}
        ln = int(self.headers.get("Content-Length", 0))
        FAIL["on"] = bool(json.loads(self.rfile.read(ln) or b"{}").get("fail", True))
        _persist()
        self._json(200, {"fail": FAIL["on"]})

    def do_GET(self):
        if self.path == "/__log":
            return self._json(200, LOG[-30:])
        if self.path == "/__health":
            return self._json(200, {"ok": True})
        return self._json(404, {})


if __name__ == "__main__":
    print(f"mock 5xx receiver on :{PORT} (恒 500；PUT /__mode 切换)")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
