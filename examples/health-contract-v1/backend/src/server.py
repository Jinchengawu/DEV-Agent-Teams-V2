"""健康状态 HTTP 服务；仅监听 loopback，不读取外部配置或其他仓库。"""

from __future__ import annotations

import argparse
import json
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

CONTRACT_ID = "health-contract-v1"
HEALTH_STATUSES = frozenset({"ok", "degraded", "unavailable"})


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        request = urlsplit(self.path)
        if request.path != "/health":
            self.respond(404, {"error": "NOT_FOUND"})
            return
        values = parse_qs(request.query, keep_blank_values=True).get("status", ["ok"])
        if len(values) != 1 or values[0] not in HEALTH_STATUSES:
            self.respond(400, {"error": "HEALTH_STATUS_INVALID"})
            return
        self.respond(200, {"status": values[0], "version": CONTRACT_ID})

    def respond(self, status: int, payload: dict[str, str]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        # 测试输入不写入应用日志。
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    with (
        ThreadingHTTPServer(("127.0.0.1", args.port), HealthHandler) as server,
        suppress(KeyboardInterrupt),
    ):
        server.serve_forever()


if __name__ == "__main__":
    main()
